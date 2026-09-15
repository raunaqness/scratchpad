"""Unit tests for `_ground()`'s two retrieval sources (backend/app.py).

Corpus-meta questions ("summarize my last 3 blogs") need the article list
(title + date) from `runs_store.library_items` — there is no fact edge in
the graph meaning "this is one of your 3 most recent posts", so semantic
search alone (`blog_graph.query`) structurally can't answer that shape of
question. `interpret` sets `plan.asks_about_knowledge_base` to say whether
the article list should be layered on top. The two sources are additive,
not either/or: `blog_graph.query` always runs (it's still the right tool
for content questions), and `library_items` is added only when the flag is
set — so a misclassified flag never costs a turn its real fact-search
answer. See docs/tasklist.md §1.1.

These patch the DB-layer functions directly (`backend.db.database_configured`,
`backend.ingest.runs_store.ingested_count` / `.library_items`,
`backend.ingest.blog.graph.query`) rather than exercising a real Postgres/
FalkorDB — `_ground` imports them lazily inside the function body, so
patching the source modules' attributes is enough; no need to patch
`backend.app`'s namespace.
"""

from __future__ import annotations

from backend.app import run_conversation
from backend.signal_models import TurnPlan
from tests.conftest import FakeModelScript


def _patch_ingest(monkeypatch, *, count: int, library_items=None, query_result=None):
    import backend.db as db
    import backend.ingest.runs_store as runs_store
    import backend.ingest.blog.graph as blog_graph

    monkeypatch.setattr(db, "database_configured", lambda: True)

    async def _ingested_count(user_id: str) -> int:
        return count

    async def _library_items(user_id: str):
        return library_items or []

    async def _query(user_id: str, question: str, num_results: int = 5):
        return query_result if query_result is not None else []

    monkeypatch.setattr(runs_store, "ingested_count", _ingested_count)
    monkeypatch.setattr(runs_store, "library_items", _library_items)
    monkeypatch.setattr(blog_graph, "query", _query)
    return runs_store, blog_graph


def test_meta_question_layers_library_items_onto_the_grounding(
    isolated_state, install_models, monkeypatch
):
    """`asks_about_knowledge_base: true` adds the article list on top of
    (not instead of) the semantic search — this is what actually fixes
    the live bug: `blog_graph.query` alone came back with nothing relevant
    for "summarize my last 3 blogs" (no fact edge means "one of the 3 most
    recent posts"), but the article list has titles/dates directly."""

    _patch_ingest(
        monkeypatch,
        count=3,
        library_items=[
            {"title": "China's AI Investment Wave", "url": "https://x/1", "published_at": "2026-01-01"},
            {"title": "Chips and Sovereignty", "url": "https://x/2", "published_at": "2026-02-01"},
            {"title": "The Next Model Race", "url": "https://x/3", "published_at": "2026-03-01"},
        ],
        query_result=[],  # reproduces the live bug: semantic search finds nothing
    )

    import backend.ingest.runs_store as runs_store
    import backend.ingest.blog.graph as blog_graph

    calls = {"query": 0, "library": 0}
    real_library_items = runs_store.library_items
    real_query = blog_graph.query

    async def _counting_library_items(user_id: str):
        calls["library"] += 1
        return await real_library_items(user_id)

    async def _counting_query(user_id: str, question: str, num_results: int = 5):
        calls["query"] += 1
        return await real_query(user_id, question, num_results=num_results)

    monkeypatch.setattr(runs_store, "library_items", _counting_library_items)
    monkeypatch.setattr(blog_graph, "query", _counting_query)

    from backend.app import _ground

    async def _run_ground():
        return await _ground(
            {
                "user_id": "u-meta",
                "user_message": "give me a summary of the last three blogs I've written",
                "plan": {"asks_about_knowledge_base": True},
                "trajectory": [],
            }
        )

    import asyncio

    state = asyncio.run(_run_ground())

    assert calls["library"] == 1
    assert calls["query"] == 1  # still runs — additive, not either/or
    grounding = state["grounding"] or []
    assert any("China's AI Investment Wave" in f["fact"] for f in grounding)
    assert len(grounding) == 3  # the 3 library items; the empty query added nothing


def test_content_question_still_uses_semantic_search(
    isolated_state, install_models, monkeypatch
):
    """`asks_about_knowledge_base: false` (the default) keeps the existing
    per-fact semantic search path unchanged and never adds the article
    list (which would be irrelevant noise for a content question)."""

    _patch_ingest(
        monkeypatch,
        count=2,
        query_result=[
            {
                "fact": "China invested heavily in AI chips in 2025",
                "valid_at": None,
                "sources": [{"title": "China's AI Investment Wave", "url": "https://x/1"}],
            }
        ],
    )

    import backend.ingest.runs_store as runs_store
    import backend.ingest.blog.graph as blog_graph

    calls = {"query": 0, "library": 0}
    real_query = blog_graph.query
    real_library_items = runs_store.library_items

    async def _counting_query(user_id: str, question: str, num_results: int = 5):
        calls["query"] += 1
        return await real_query(user_id, question, num_results=num_results)

    async def _counting_library_items(user_id: str):
        calls["library"] += 1
        return await real_library_items(user_id)

    monkeypatch.setattr(blog_graph, "query", _counting_query)
    monkeypatch.setattr(runs_store, "library_items", _counting_library_items)

    install_models(
        FakeModelScript(
            plan=TurnPlan(mode="chat", asks_about_knowledge_base=False),
            reply="China invested heavily in AI chips.",
        )
    )

    result = run_conversation(
        user_id="u-content",
        conversation_id="c-content",
        user_message="what did I say about China's AI investment?",
    )

    assert calls["query"] == 1
    assert calls["library"] == 0
    assert result["assistant_message"]


def test_zero_ingested_articles_skips_both_paths(isolated_state, install_models, monkeypatch):
    """Unchanged fail-open behavior: with nothing ingested, neither
    retrieval path runs at all."""

    _patch_ingest(monkeypatch, count=0)
    import backend.ingest.runs_store as runs_store
    import backend.ingest.blog.graph as blog_graph

    calls = {"query": 0, "library": 0}

    async def _query(user_id: str, question: str, num_results: int = 5):
        calls["query"] += 1
        return []

    async def _library_items(user_id: str):
        calls["library"] += 1
        return []

    monkeypatch.setattr(blog_graph, "query", _query)
    monkeypatch.setattr(runs_store, "library_items", _library_items)

    install_models(
        FakeModelScript(plan=TurnPlan(mode="chat", asks_about_knowledge_base=True))
    )
    result = run_conversation(
        user_id="u-empty",
        conversation_id="c-empty",
        user_message="summarize my last 3 blogs",
    )

    assert calls["query"] == 0
    assert calls["library"] == 0
    assert result["assistant_message"]
