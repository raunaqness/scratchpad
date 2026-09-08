"""Per-thread linear version history for the live artifact.

Every user turn that changes the artifact appends one snapshot. The list is a
plain line (no branches): navigating back to an older version is a read-only
preview on the client; sending a turn from a past version *truncates* everything
after it and continues from there. At most :data:`MAX_VERSIONS` are kept — the
oldest are dropped.

Stored as a single JSON blob per thread in the same SQLite file the LangGraph
checkpointer uses (a separate table). Version I/O never overlaps a checkpointer
connection — callers read before opening the graph and write after it closes.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from backend.config import settings

MAX_VERSIONS = 50

_TABLE = "artifact_version_lists"
_CREATE = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    thread_id  TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

# Fields that make two scratchpad snapshots "the same version".
_COMPARE_KEYS = (
    "title",
    "topic",
    "body",
    "angles",
    "outline",
    "tags",
    "open_questions",
)

# Fields the client needs to render a version (current or previewed).
_ITEM_KEYS = ("title", "topic", "body", "angles", "outline", "tags", "open_questions")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path() -> str:
    return str(settings.db_path)


async def _connect() -> aiosqlite.Connection:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(_db_path())
    await conn.execute("PRAGMA busy_timeout=5000")
    await conn.execute(_CREATE)
    return conn


async def load_versions(thread_id: str) -> list[dict[str, Any]]:
    """Return the stored version list (oldest first), or ``[]``."""

    conn = await _connect()
    try:
        async with conn.execute(
            f"SELECT data FROM {_TABLE} WHERE thread_id = ?", (thread_id,)
        ) as cursor:
            row = await cursor.fetchone()
    finally:
        await conn.close()
    if not row:
        return []
    try:
        data = json.loads(row[0])
    except (TypeError, ValueError):
        return []
    return data if isinstance(data, list) else []


async def replace_versions(thread_id: str, versions: list[dict[str, Any]]) -> None:
    """Overwrite the stored version list for a thread."""

    conn = await _connect()
    try:
        await conn.execute(
            f"INSERT INTO {_TABLE} (thread_id, data, updated_at) VALUES (?, ?, ?) "
            f"ON CONFLICT(thread_id) DO UPDATE SET data = excluded.data, "
            f"updated_at = excluded.updated_at",
            (thread_id, json.dumps(versions, ensure_ascii=False), _now()),
        )
        await conn.commit()
    finally:
        await conn.close()


def artifact_changed(before: dict[str, Any] | None, after: dict[str, Any] | None) -> bool:
    """True when ``after`` differs from ``before`` on any content field."""

    before = before or {}
    after = after or {}
    return any(before.get(key) != after.get(key) for key in _COMPARE_KEYS)


def append_version(
    versions: list[dict[str, Any]],
    artifact: dict[str, Any],
    user_message: str,
    *,
    base_seq: int | None = None,
) -> list[dict[str, Any]]:
    """Return a new list with ``artifact`` appended.

    ``base_seq`` (1-based) is the version the turn continued from. When it points
    at anything earlier than the current tip, every later version is discarded
    first — the line is rewritten from that point. The list is then capped to the
    most recent :data:`MAX_VERSIONS`.
    """

    if base_seq is not None and 1 <= base_seq < len(versions):
        versions = versions[:base_seq]
    else:
        versions = list(versions)

    versions.append(
        {
            "artifact": artifact,
            "user_message": user_message,
            "created_at": _now(),
        }
    )
    if len(versions) > MAX_VERSIONS:
        versions = versions[-MAX_VERSIONS:]
    return versions


def version_items(versions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compact, renderable form of the list for the client (1-based ``seq``)."""

    items: list[dict[str, Any]] = []
    for index, entry in enumerate(versions):
        artifact = entry.get("artifact", {}) if isinstance(entry, dict) else {}
        item = {"seq": index + 1, "created_at": entry.get("created_at", "")}
        for key in _ITEM_KEYS:
            item[key] = artifact.get(key, "" if key in {"title", "topic", "body"} else [])
        items.append(item)
    return items
