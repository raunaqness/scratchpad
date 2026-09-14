"""Synchronous Postgres access for the scraper subprocess.

``spider_runner.py`` runs Scrapy's blocking, Twisted-reactor-based
``CrawlerProcess`` — that can't share a thread with an asyncio event loop, so
it can't use ``backend/ingest/runs_store.py`` (asyncpg). This is the same
``scratchpad.ingest_runs`` / ``scratchpad.ingest_items`` tables, accessed with
a plain synchronous driver instead.
"""

from __future__ import annotations

import psycopg2

from backend.config import settings


def _connect():
    return psycopg2.connect(settings.database_url)


def selected_items(run_id: int) -> list[dict]:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT url, title, published_at FROM scratchpad.ingest_items "
            "WHERE run_id = %s AND selected = true ORDER BY rank",
            (run_id,),
        )
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_run_user_id(run_id: int) -> str | None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT user_id FROM scratchpad.ingest_runs WHERE id = %s", (run_id,))
        row = cur.fetchone()
        return row[0] if row else None


def set_item_episode_uuid(run_id: int, url: str, episode_uuid: str) -> None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE scratchpad.ingest_items SET episode_uuid = %s, updated_at = now() "
            "WHERE run_id = %s AND url = %s",
            (episode_uuid, run_id, url),
        )


def set_run_status(run_id: int, status: str) -> None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE scratchpad.ingest_runs SET status = %s, updated_at = now() WHERE id = %s",
            (status, run_id),
        )


def update_item_stage(run_id: int, url: str, stage: str, error: str | None = None) -> None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE scratchpad.ingest_items "
            "SET stage = %s, error = %s, updated_at = now() "
            "WHERE run_id = %s AND url = %s",
            (stage, error, run_id, url),
        )
