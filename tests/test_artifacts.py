"""Artifact-store tests. Skipped unless a reachable Postgres URL is available.

    SIGNAL_TEST_DATABASE_URL=postgresql://postgres:pw@127.0.0.1:5432/postgres \
        python -m pytest tests/test_artifacts.py -q
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

_URL = os.environ.get("SIGNAL_TEST_DATABASE_URL") or os.environ.get("SIGNAL_DATABASE_URL")
if not _URL or "@db:" in _URL:
    pytest.skip("no reachable SIGNAL_TEST_DATABASE_URL", allow_module_level=True)

import backend.db as _db  # noqa: E402
from backend import artifacts_store  # noqa: E402
from backend.config import settings  # noqa: E402
from backend.db import close_pool, get_pool, run_migrations  # noqa: E402


def _run(coro):
    async def _wrapped():
        try:
            return await coro
        finally:
            await close_pool()

    return asyncio.run(_wrapped())


@pytest.fixture(scope="module", autouse=True)
def _migrated():
    prev = settings.database_url
    settings.database_url = _URL
    _db._pool = None
    _run(run_migrations())
    yield
    _db._pool = None
    settings.database_url = prev


@pytest.fixture
def thread_id():
    tid = f"test-{uuid.uuid4()}"
    yield tid

    async def _cleanup():
        pool = await get_pool()
        async with pool.acquire() as c:
            await c.execute("DELETE FROM scratchpad.artifacts WHERE thread_id=$1", tid)

    _run(_cleanup())


def test_version_increments_per_thread_and_skill(thread_id):
    async def body():
        a1 = await artifacts_store.save_artifact(thread_id, "blog_outline", "# v1")
        a2 = await artifacts_store.save_artifact(thread_id, "blog_outline", "# v2")
        b1 = await artifacts_store.save_artifact(thread_id, "social_post", "post v1")
        assert (a1["version"], a2["version"]) == (1, 2)
        assert b1["version"] == 1  # independent per skill
        rows = await artifacts_store.list_artifacts(thread_id)
        assert len(rows) == 3
        assert rows[0]["skill_id"] == "blog_outline" and rows[0]["version"] == 2

    _run(body())


def test_get_artifact_roundtrip(thread_id):
    async def body():
        saved = await artifacts_store.save_artifact(thread_id, "blog_outline", "# body")
        got = await artifacts_store.get_artifact(saved["id"])
        assert got is not None and got["body"] == "# body"
        assert await artifacts_store.get_artifact(-1) is None

    _run(body())
