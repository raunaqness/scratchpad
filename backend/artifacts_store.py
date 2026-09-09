"""Generated-artifact persistence on Postgres (Supabase).

A skill run produces one artifact; we keep every run as a new ``version`` for
that ``(thread_id, skill_id)`` pair so the user can flip between takes. The
scratchpad, not this table, is the source of truth — rows here are disposable.
"""

from __future__ import annotations

from typing import Any

from backend.db import get_pool


async def save_artifact(thread_id: str, skill_id: str, body: str) -> dict[str, Any]:
    """Append a new version for this thread + skill. Returns the stored row."""

    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        version = await conn.fetchval(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM scratchpad.artifacts "
            "WHERE thread_id = $1 AND skill_id = $2",
            thread_id, skill_id,
        )
        row = await conn.fetchrow(
            "INSERT INTO scratchpad.artifacts (thread_id, skill_id, version, body) "
            "VALUES ($1, $2, $3, $4) "
            "RETURNING id, thread_id, skill_id, version, body, created_at",
            thread_id, skill_id, version, body,
        )
    return _row(row)


async def list_artifacts(thread_id: str) -> list[dict[str, Any]]:
    """Every artifact for a thread, newest first."""

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, thread_id, skill_id, version, body, created_at "
            "FROM scratchpad.artifacts WHERE thread_id = $1 "
            "ORDER BY skill_id, version DESC",
            thread_id,
        )
    return [_row(r) for r in rows]


async def get_artifact(artifact_id: int) -> dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, thread_id, skill_id, version, body, created_at "
            "FROM scratchpad.artifacts WHERE id = $1",
            artifact_id,
        )
    return _row(row) if row else None


def _row(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "thread_id": row["thread_id"],
        "skill_id": row["skill_id"],
        "version": row["version"],
        "body": row["body"],
        "created_at": row["created_at"].isoformat(),
    }
