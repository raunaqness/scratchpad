"""Graphiti knowledge graph: ingest stored pages, then query them.

Scoped **per account**, not per run — every article a user ingests, across
every blog they've ever run through discover/confirm/scrape, lands in the
same FalkorDB database. A "run" is just one blog-URL scrape attempt; the
graph (and the 15-article cap) belongs to the account.

Graph store: FalkorDB — a Redis-protocol server (``docker-compose.test.yml``
runs one as ``ingest-falkordb``; see docs/plan-house-voice-reader.md §5.5 for
the history here — Kuzu was tried first as a zero-infra embedded option but
its graphiti-core driver turned out not to actually create the full-text
index its own search queries need, confirmed by hitting that failure live).
LLM + embedder: OpenRouter's OpenAI-compatible endpoints, reusing
``settings.openrouter_*`` — no new provider/credential to manage. This uses
``graphiti_core``'s own OpenAI client wrapper directly (not the LangChain
``ChatOpenAI`` in ``backend/llm.py``, which Graphiti's client interface doesn't
accept).
"""

from __future__ import annotations

import re

from graphiti_core import Graphiti
from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
from graphiti_core.driver.falkordb_driver import FalkorDriver
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.openai_client import OpenAIClient
from graphiti_core.nodes import EpisodeType, EpisodicNode

from backend.config import settings
from backend.ingest.models import Page

_ACCOUNT_KEY_RE = re.compile(r"[^a-zA-Z0-9_]+")


def _account_database(user_id: str) -> str:
    """FalkorDB database name for this account's graph. ``user_id`` is a
    Google `sub` (numeric string) or the local dev user — sanitized anyway
    since it becomes a Redis key, not just for defensiveness."""

    return f"ingest_account_{_ACCOUNT_KEY_RE.sub('_', user_id)}"


def _client(user_id: str) -> Graphiti:
    llm_config = LLMConfig(
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        model=settings.openrouter_model,
    )
    embedder_config = OpenAIEmbedderConfig(
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        embedding_model=settings.openrouter_embedding_model,
    )
    return Graphiti(
        graph_driver=FalkorDriver(
            host=settings.ingest_falkordb_host,
            port=settings.ingest_falkordb_port,
            database=_account_database(user_id),
        ),
        llm_client=OpenAIClient(config=llm_config),
        embedder=OpenAIEmbedder(config=embedder_config),
        # Graphiti defaults this to a plain OpenAI client reading OPENAI_API_KEY
        # if not given explicitly — point it at OpenRouter too.
        cross_encoder=OpenAIRerankerClient(config=llm_config),
    )


async def ingest(user_id: str, pages: list[Page]) -> dict[str, str]:
    """One Graphiti episode per page, added to this account's shared graph.

    Returns ``{page.url: episode_uuid}`` so the caller can persist which
    episode each stored item became — needed later to remove that one
    article without touching anything else in the shared graph.
    """

    graphiti = _client(user_id)
    episode_uuids: dict[str, str] = {}
    try:
        await graphiti.build_indices_and_constraints()
        for page in pages:
            reference_time = _parse_time(page.published_at) or _parse_time(page.fetched_at)
            result = await graphiti.add_episode(
                name=page.title or page.url,
                episode_body=f"{page.title or ''}\n\n{page.text}".strip(),
                source_description=page.url,
                reference_time=reference_time,
                source=EpisodeType.text,
            )
            episode_uuids[page.url] = result.episode.uuid
    finally:
        await graphiti.close()
    return episode_uuids


async def remove(user_id: str, episode_uuid: str) -> None:
    """Delete one article from this account's graph. Graphiti's
    ``remove_episode`` only deletes edges/entities exclusive to that
    episode — anything shared with other kept articles is untouched."""

    graphiti = _client(user_id)
    try:
        await graphiti.remove_episode(episode_uuid)
    finally:
        await graphiti.close()


async def query(user_id: str, question: str, num_results: int = 10) -> list[dict]:
    """Each fact's ``sources`` resolves the episode(s) it came from back to
    the article (title + URL) — article-level citation. Graphiti stores one
    episode per whole page, not per paragraph, so this is as granular as it
    gets without custom paragraph-level chunking at ingest time."""

    graphiti = _client(user_id)
    try:
        edges = await graphiti.search(question, num_results=num_results)
        episode_uuids = sorted({uuid for edge in edges for uuid in edge.episodes})
        sources_by_uuid: dict[str, dict] = {}
        if episode_uuids:
            episodes = await EpisodicNode.get_by_uuids(graphiti.driver, episode_uuids)
            sources_by_uuid = {
                ep.uuid: {"title": ep.name, "url": ep.source_description}
                for ep in episodes
            }
    finally:
        await graphiti.close()
    return [
        {
            "fact": edge.fact,
            "valid_at": edge.valid_at.isoformat() if edge.valid_at else None,
            "sources": [
                sources_by_uuid[uuid] for uuid in edge.episodes if uuid in sources_by_uuid
            ],
        }
        for edge in edges
    ]


def _parse_time(value: str | None):
    from datetime import datetime, timezone

    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
