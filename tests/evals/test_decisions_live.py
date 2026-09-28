"""Decision evals: does the real model make the right call on each turn?

Only ``interpret`` hits the live model. Every other LLM call is the offline
fake from ``tests/conftest.py``, and the knowledge-base layer is replaced with
spies. So each case costs one model call, and every assertion is exact: the
``TurnPlan`` fields, the node the graph routed to, and whether the knowledge
graph was searched (and with what query). No LLM judge anywhere in this file.

Cases live in ``decision_cases.json``. ``expect`` keys:

  mode                     exact, or a list of acceptable modes
  route                    node that ran after ``ground`` (note / expand /
                           tighten / brainstorm / critique / build / respond)
  skill_id, safety_flag, asks_about_knowledge_base, subject_changed
                           exact match on the plan field
  plan_contains            {field: substring}, case-insensitive
  plan_present             [field, ...] must be non-empty
  confirmed_facts_mention  [substring, ...] each in some confirmed fact
  kb_search                was ``blog_graph.query`` called
  kb_search_query_contains [substring, ...] in the query it was called with
  kb_article_list          was ``runs_store.library_items`` called
  scratchpad_changed       body differs from the seed
  derived_created          a skill artifact was produced

A case with ``"xfail": "reason"`` is a known gap: it is reported, not failed.

Run: ``scripts/evals.sh live`` (needs OPENROUTER_API_KEY + OPENROUTER_MODEL).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import backend.llm as llm
from backend.app import run_conversation
from backend.config import settings
from backend.signal_models import TurnPlan
from tests.conftest import FakeModelScript

pytestmark = pytest.mark.live

_DATA = json.loads((Path(__file__).parent / "decision_cases.json").read_text())
_SEEDS: dict[str, dict[str, Any]] = _DATA["seeds"]
_CASES: list[dict[str, Any]] = _DATA["cases"]

# Captured before any test monkeypatches it.
_REAL_GET_CHAT_MODEL = llm.get_chat_model

_NON_ROUTE_STEPS = {"interpret", "ground", "follow_up"}


class LiveInterpretScript(FakeModelScript):
    """The real model for ``signal:interpret``; the offline fakes for the rest."""

    def __call__(self, *, streaming=False, temperature=None, tags=None):
        if (tags or ["?"])[0] == "signal:interpret":
            return _REAL_GET_CHAT_MODEL(
                streaming=streaming, temperature=temperature, tags=tags
            )
        return super().__call__(streaming=streaming, temperature=temperature, tags=tags)


def _spy_knowledge_base(monkeypatch, ingested: int) -> dict[str, list]:
    import backend.db as db
    import backend.ingest.blog.graph as blog_graph
    import backend.ingest.runs_store as runs_store

    calls: dict[str, list] = {"query": [], "library": []}

    async def _ingested_count(user_id: str) -> int:
        return ingested

    async def _library_items(user_id: str):
        calls["library"].append(user_id)
        return [
            {"title": "China's AI Investment Wave", "url": "https://x/1", "published_at": "2026-01-01"},
            {"title": "Why We Price Per Seat", "url": "https://x/2", "published_at": "2026-02-01"},
        ]

    async def _query(user_id: str, question: str, num_results: int = 5):
        calls["query"].append({"question": question, "num_results": num_results})
        return []

    monkeypatch.setattr(db, "database_configured", lambda: True)
    monkeypatch.setattr(runs_store, "ingested_count", _ingested_count)
    monkeypatch.setattr(runs_store, "library_items", _library_items)
    monkeypatch.setattr(blog_graph, "query", _query)
    return calls


def _route(trajectory: list[dict[str, Any]]) -> str | None:
    for step in trajectory:
        if step.get("step") not in _NON_ROUTE_STEPS:
            return step.get("step")
    return None


def _check(case: dict[str, Any], result: dict[str, Any], calls: dict[str, list]) -> list[str]:
    """Every mismatch between ``expect`` and what happened, as readable lines."""

    expect = case["expect"]
    plan = result["plan"] or {}
    seed_body = (_SEEDS.get(case.get("scratchpad") or "", {}) or {}).get("body", "")
    body = (result["scratchpad"] or {}).get("body", "")
    errors: list[str] = []

    def mismatch(key: str, want: Any, got: Any) -> None:
        errors.append(f"{key}: expected {want!r}, got {got!r}")

    if "mode" in expect:
        allowed = expect["mode"] if isinstance(expect["mode"], list) else [expect["mode"]]
        if plan.get("mode") not in allowed:
            mismatch("mode", expect["mode"], plan.get("mode"))
    if "route" in expect and _route(result["trajectory"]) != expect["route"]:
        mismatch("route", expect["route"], _route(result["trajectory"]))
    for key in ("skill_id", "safety_flag", "asks_about_knowledge_base", "subject_changed"):
        if key in expect and plan.get(key) != expect[key]:
            mismatch(key, expect[key], plan.get(key))
    for key, needle in expect.get("plan_contains", {}).items():
        if needle.lower() not in str(plan.get(key) or "").lower():
            mismatch(f"plan.{key} contains", needle, plan.get(key))
    for key in expect.get("plan_present", []):
        if not plan.get(key):
            mismatch(f"plan.{key} present", True, plan.get(key))
    facts = [f.lower() for f in plan.get("confirmed_facts") or []]
    for needle in expect.get("confirmed_facts_mention", []):
        if not any(needle.lower() in f for f in facts):
            mismatch("confirmed_facts mention", needle, plan.get("confirmed_facts"))
    if "kb_search" in expect and bool(calls["query"]) != expect["kb_search"]:
        mismatch("kb_search called", expect["kb_search"], bool(calls["query"]))
    for needle in expect.get("kb_search_query_contains", []):
        queries = [c["question"] for c in calls["query"]]
        if not any(needle.lower() in q.lower() for q in queries):
            mismatch("kb_search query contains", needle, queries)
    if "kb_article_list" in expect and bool(calls["library"]) != expect["kb_article_list"]:
        mismatch("kb_article_list called", expect["kb_article_list"], bool(calls["library"]))
    if "scratchpad_changed" in expect and (body != seed_body) != expect["scratchpad_changed"]:
        mismatch("scratchpad_changed", expect["scratchpad_changed"], body != seed_body)
    if "derived_created" in expect and bool(result["derived"]) != expect["derived_created"]:
        mismatch("derived_created", expect["derived_created"], bool(result["derived"]))
    return errors


@pytest.fixture(autouse=True)
def _require_live_model():
    if not settings.openrouter_api_key or not settings.openrouter_model:
        pytest.skip("Set OPENROUTER_API_KEY and OPENROUTER_MODEL to run decision evals.")


@pytest.mark.parametrize("case", _CASES, ids=lambda c: f"{c['category']}/{c['id']}")
def test_decision(case, isolated_state, install_models, monkeypatch, request):
    request.node.user_properties.append(("category", case["category"]))
    if case.get("xfail"):
        request.applymarker(pytest.mark.xfail(reason=case["xfail"], strict=False))

    install_models(LiveInterpretScript(plan=TurnPlan(mode="chat")))
    calls = _spy_knowledge_base(monkeypatch, case.get("kb_ingested", 0))
    seed = _SEEDS.get(case.get("scratchpad") or "")

    result = run_conversation(
        user_id=f"eval-{case['id']}",
        conversation_id=f"eval-{case['id']}",
        user_message=case["message"],
        client_artifact=dict(seed) if seed else None,
    )

    errors = _check(case, result, calls)
    plan = result["plan"] or {}
    assert not errors, (
        f"{case['id']}: {case['message']!r}\n  "
        + "\n  ".join(errors)
        + f"\n  plan: mode={plan.get('mode')} skill_id={plan.get('skill_id')} "
        f"safety={plan.get('safety_flag')} kb_meta={plan.get('asks_about_knowledge_base')}"
    )
