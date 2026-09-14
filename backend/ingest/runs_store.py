"""Ingestion run/item persistence on Postgres (Supabase), async (asyncpg).

Source-agnostic: nothing here knows about blogs, scraping, or Scrapy. This is
the job/queue state for any ingestion source — a run moves through
``discovering -> awaiting_confirmation -> scraping -> ingesting -> done``
(or ``failed`` / ``cancelled``), and each item moves through its own per-row
``stage``. Used by ``backend/ingest/api.py`` and ``backend/ingest/cli.py``.

The 15-article cap and the knowledge graph are both **account-wide**, not
per-run (see docs/plan-house-voice-reader.md) — a run is just "one blog-URL
scrape attempt"; ``library_items``/``ingested_count`` look across every run
for a user to answer "what's actually in their graph right now."

``backend/ingest/blog/spider_runner.py`` runs in a separate subprocess and
does NOT use this module — it updates ``ingest_items.stage`` through a
synchronous client instead (see that file for why).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.db import get_pool
from backend.ingest.models import Candidate, IngestItem, IngestRun

ACTIVE_STATUSES = ("discovering", "awaiting_confirmation", "scraping", "ingesting")


class SelectionTooLarge(ValueError):
    """Raised when a caller tries to select more items than the run allows."""


async def create_run(
    source_type: str, source_ref: str, user_id: str | None, max_items: int
) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "INSERT INTO scratchpad.ingest_runs (source_type, source_ref, user_id, max_items) "
            "VALUES ($1, $2, $3, $4) RETURNING id",
            source_type, source_ref, user_id, max_items,
        )


async def save_candidates(
    run_id: int, candidates: list[Candidate], discovery_method: str | None
) -> None:
    """Persist the discovered pool and flip the run to ``awaiting_confirmation``.

    Always lands on ``awaiting_confirmation`` — there is no code path that
    skips straight to ``scraping``, regardless of how many candidates there
    are (see docs/plan-house-voice-reader.md §5.2).
    """

    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        for rank, c in enumerate(candidates):
            await conn.execute(
                "INSERT INTO scratchpad.ingest_items "
                "(run_id, url, title, published_at, selected, rank) "
                "VALUES ($1, $2, $3, $4, $5, $6) "
                "ON CONFLICT (run_id, url) DO UPDATE SET "
                "  title = EXCLUDED.title, published_at = EXCLUDED.published_at, "
                "  selected = EXCLUDED.selected, rank = EXCLUDED.rank",
                run_id, c.url, c.title, c.published_at, c.selected, rank,
            )
        await conn.execute(
            "UPDATE scratchpad.ingest_runs "
            "SET discovery_method = $2, status = 'awaiting_confirmation', updated_at = now() "
            "WHERE id = $1",
            run_id, discovery_method,
        )


async def update_selection(run_id: int, selected_urls: list[str], max_items: int) -> None:
    if len(selected_urls) > max_items:
        raise SelectionTooLarge(
            f"at most {max_items} items may be selected here, got {len(selected_urls)}"
        )
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "UPDATE scratchpad.ingest_items SET selected = false, updated_at = now() "
            "WHERE run_id = $1",
            run_id,
        )
        if selected_urls:
            await conn.execute(
                "UPDATE scratchpad.ingest_items SET selected = true, updated_at = now() "
                "WHERE run_id = $1 AND url = ANY($2::text[])",
                run_id, selected_urls,
            )


async def set_run_status(run_id: int, status: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE scratchpad.ingest_runs SET status = $2, updated_at = now() WHERE id = $1",
            run_id, status,
        )


async def update_item_stage(
    run_id: int, url: str, stage: str, error: str | None = None
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE scratchpad.ingest_items "
            "SET stage = $3, error = $4, updated_at = now() "
            "WHERE run_id = $1 AND url = $2",
            run_id, url, stage, error,
        )


async def set_item_episode_uuid(run_id: int, url: str, episode_uuid: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE scratchpad.ingest_items SET episode_uuid = $3, updated_at = now() "
            "WHERE run_id = $1 AND url = $2",
            run_id, url, episode_uuid,
        )


async def get_run(run_id: int) -> IngestRun | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        run_row = await conn.fetchrow(
            "SELECT id, user_id, source_type, source_ref, discovery_method, status, max_items "
            "FROM scratchpad.ingest_runs WHERE id = $1",
            run_id,
        )
        if run_row is None:
            return None
        item_rows = await conn.fetch(
            "SELECT id, run_id, url, title, published_at, selected, stage, error, rank "
            "FROM scratchpad.ingest_items WHERE run_id = $1 ORDER BY rank",
            run_id,
        )
    return IngestRun(
        **dict(run_row),
        items=[IngestItem(**dict(r)) for r in item_rows],
    )


async def active_run_id(user_id: str) -> int | None:
    """A run for this account still in progress (discovering through
    ingesting), if any — the library view shows this instead of/alongside
    the article list so an in-flight scrape is never invisible."""

    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT id FROM scratchpad.ingest_runs "
            "WHERE user_id = $1 AND status = ANY($2::text[]) "
            "ORDER BY id DESC LIMIT 1",
            user_id, list(ACTIVE_STATUSES),
        )


async def ingested_count(user_id: str) -> int:
    """How many articles this account currently has in its knowledge graph
    (across every run, minus anything removed) — the number the 15-cap is
    checked against."""

    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM scratchpad.ingest_items i "
            "JOIN scratchpad.ingest_runs r ON r.id = i.run_id "
            "WHERE r.user_id = $1 AND i.stage = 'ingested' AND i.removed_at IS NULL",
            user_id,
        )


async def library_items(user_id: str) -> list[dict[str, Any]]:
    """Every currently-ingested article for this account, newest first —
    what the library view lists, and what "remove an article" operates on."""

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT i.id, i.url, i.title, i.published_at, i.episode_uuid, "
            "       i.run_id, r.source_ref, i.created_at "
            "FROM scratchpad.ingest_items i "
            "JOIN scratchpad.ingest_runs r ON r.id = i.run_id "
            "WHERE r.user_id = $1 AND i.stage = 'ingested' AND i.removed_at IS NULL "
            "ORDER BY i.id DESC",
            user_id,
        )
    return [dict(r) for r in rows]


async def get_item_for_user(item_id: int, user_id: str) -> dict[str, Any] | None:
    """Ownership-checked single-item lookup — used before removal so one
    account can't delete another's article by guessing an id."""

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT i.id, i.run_id, i.url, i.episode_uuid "
            "FROM scratchpad.ingest_items i "
            "JOIN scratchpad.ingest_runs r ON r.id = i.run_id "
            "WHERE i.id = $1 AND r.user_id = $2 AND i.removed_at IS NULL",
            item_id, user_id,
        )
    return dict(row) if row else None


async def mark_item_removed(item_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE scratchpad.ingest_items SET removed_at = now(), updated_at = now() "
            "WHERE id = $1",
            item_id,
        )


async def selected_items(run_id: int) -> list[dict[str, Any]]:
    """Selected rows for a run, for the scraper subprocess to seed itself from."""

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT url, title, published_at FROM scratchpad.ingest_items "
            "WHERE run_id = $1 AND selected = true ORDER BY rank",
            run_id,
        )
    return [dict(r) for r in rows]
