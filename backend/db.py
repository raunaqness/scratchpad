"""Postgres connection pool + a tiny forward-only migration runner.

Only the credit system uses Postgres today; the LangGraph checkpointer, thread
registry and version lists stay on SQLite. When ``SIGNAL_DATABASE_URL`` is unset
the pool is simply unavailable and credit enforcement is skipped.

The migration runner applies ``backend/migrations/*.sql`` in filename order and
records each in ``scratchpad.schema_migrations``. Files must be idempotent
(``CREATE ... IF NOT EXISTS``, ``CREATE OR REPLACE``) — they may re-run after a
partial failure.
"""

from __future__ import annotations

import logging
from pathlib import Path

import asyncpg

from backend.config import settings

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

_pool: asyncpg.Pool | None = None


def database_configured() -> bool:
    return bool(settings.database_url)


async def get_pool() -> asyncpg.Pool:
    """Process-wide connection pool. Raises if no ``SIGNAL_DATABASE_URL``."""

    global _pool
    if not settings.database_url:
        raise RuntimeError("SIGNAL_DATABASE_URL is not set")
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=settings.database_url,
            min_size=1,
            max_size=settings.db_pool_size,
            command_timeout=30,
            # asyncpg can't prepare statements through a transaction-mode pooler
            # (hosted Supabase :6543). Harmless on a direct connection.
            statement_cache_size=0,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def run_migrations() -> list[str]:
    """Apply any pending ``migrations/*.sql``. Returns the names applied."""

    if not settings.database_url:
        logger.info("SIGNAL_DATABASE_URL unset; skipping migrations")
        return []

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "CREATE SCHEMA IF NOT EXISTS scratchpad;"
            "CREATE TABLE IF NOT EXISTS scratchpad.schema_migrations ("
            "  name TEXT PRIMARY KEY,"
            "  applied_at TIMESTAMPTZ NOT NULL DEFAULT now()"
            ")"
        )
        done = {
            r["name"]
            for r in await conn.fetch("SELECT name FROM scratchpad.schema_migrations")
        }
        applied: list[str] = []
        for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            if path.name in done:
                continue
            logger.info("applying migration %s", path.name)
            async with conn.transaction():
                await conn.execute(path.read_text())
                await conn.execute(
                    "INSERT INTO scratchpad.schema_migrations (name) VALUES ($1)",
                    path.name,
                )
            applied.append(path.name)
    return applied
