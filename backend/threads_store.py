"""Per-user registry of conversation threads.

The LangGraph checkpointer already stores each thread's state keyed by
``thread_id``; this table is the thin index on top: which threads belong to a
user, their title (first user message), and when they were last touched. It lets
the client restore a user's thread list across devices and gives Langfuse
sessions a human-readable name.

Stored in the same SQLite file as the checkpointer and the version lists, in its
own table. Writes are best-effort — a failure here never fails a turn.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import aiosqlite

from backend.config import settings

_TABLE = "conversation_threads"
_CREATE = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    thread_id  TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    title      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""
_INDEX = f"CREATE INDEX IF NOT EXISTS idx_{_TABLE}_user ON {_TABLE} (user_id, updated_at)"

_TITLE_MAX = 80


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _title(text: str | None) -> str:
    clean = " ".join((text or "").split())
    return clean[:_TITLE_MAX]


async def _connect() -> aiosqlite.Connection:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(str(settings.db_path))
    await conn.execute("PRAGMA busy_timeout=5000")
    await conn.execute(_CREATE)
    await conn.execute(_INDEX)
    return conn


async def list_threads(user_id: str, limit: int | None = None) -> list[dict[str, Any]]:
    """A user's threads, most recently touched first."""

    query = (
        f"SELECT thread_id, title, created_at, updated_at FROM {_TABLE} "
        f"WHERE user_id = ? ORDER BY updated_at DESC"
    )
    params: tuple[Any, ...] = (user_id,)
    if limit is not None:
        query += " LIMIT ?"
        params += (limit,)

    conn = await _connect()
    try:
        async with conn.execute(query, params) as cursor:
            rows = await cursor.fetchall()
    finally:
        await conn.close()
    return [
        {
            "thread_id": r[0],
            "title": r[1],
            "created_at": r[2],
            "updated_at": r[3],
        }
        for r in rows
    ]


async def get_thread(thread_id: str) -> dict[str, Any] | None:
    """One thread's registry row, for an ownership check before returning its
    message history to a caller."""

    conn = await _connect()
    try:
        async with conn.execute(
            f"SELECT thread_id, user_id, title, created_at, updated_at FROM {_TABLE} "
            f"WHERE thread_id = ?",
            (thread_id,),
        ) as cursor:
            row = await cursor.fetchone()
    finally:
        await conn.close()
    if row is None:
        return None
    return {
        "thread_id": row[0],
        "user_id": row[1],
        "title": row[2],
        "created_at": row[3],
        "updated_at": row[4],
    }


async def create_thread(
    user_id: str, thread_id: str, title: str | None = None
) -> dict[str, Any]:
    """Register a thread. Idempotent — re-registering only bumps ``updated_at``."""

    now = _now()
    conn = await _connect()
    try:
        await conn.execute(
            f"INSERT INTO {_TABLE} (thread_id, user_id, title, created_at, updated_at) "
            f"VALUES (?, ?, ?, ?, ?) "
            f"ON CONFLICT(thread_id) DO UPDATE SET updated_at = excluded.updated_at",
            (thread_id, user_id, _title(title), now, now),
        )
        await conn.commit()
    finally:
        await conn.close()
    return {
        "thread_id": thread_id,
        "title": _title(title),
        "created_at": now,
        "updated_at": now,
    }


async def touch_thread(
    thread_id: str, *, user_id: str, title: str | None = None
) -> None:
    """Upsert a thread's ``updated_at``; fill the title only if still blank."""

    now = _now()
    conn = await _connect()
    try:
        await conn.execute(
            f"INSERT INTO {_TABLE} (thread_id, user_id, title, created_at, updated_at) "
            f"VALUES (?, ?, ?, ?, ?) "
            f"ON CONFLICT(thread_id) DO UPDATE SET "
            f"updated_at = excluded.updated_at, "
            f"title = CASE WHEN {_TABLE}.title = '' THEN excluded.title "
            f"ELSE {_TABLE}.title END",
            (thread_id, user_id, _title(title), now, now),
        )
        await conn.commit()
    finally:
        await conn.close()
