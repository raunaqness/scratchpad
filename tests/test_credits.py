"""Credit-system tests. Skipped unless a reachable Postgres URL is available.

    SIGNAL_TEST_DATABASE_URL=postgresql://postgres:pw@127.0.0.1:5432/postgres \
        python -m pytest tests/test_credits.py -q

Plain ``asyncio.run`` per test — no pytest-asyncio dependency.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

_URL = os.environ.get("SIGNAL_TEST_DATABASE_URL") or os.environ.get("SIGNAL_DATABASE_URL")
if not _URL or "@db:" in _URL:  # '@db:' is the in-container DSN, unreachable here
    pytest.skip("no reachable SIGNAL_TEST_DATABASE_URL", allow_module_level=True)

os.environ.setdefault("SIGNAL_CREDITS_ENABLED", "true")
os.environ.setdefault("SIGNAL_SIGNUP_CREDITS", "100")

import backend.db as _db  # noqa: E402
from backend import credits  # noqa: E402
from backend.config import settings  # noqa: E402
from backend.db import close_pool, get_pool, run_migrations  # noqa: E402


def _run(coro):
    # Fresh event loop + fresh pool per call: the module-level asyncpg pool
    # can't survive across the loops that asyncio.run() creates and tears down.
    async def _wrapped():
        try:
            return await coro
        finally:
            await close_pool()

    return asyncio.run(_wrapped())


@pytest.fixture(scope="module", autouse=True)
def _migrated():
    prev_url, prev_enabled = settings.database_url, settings.credits_enabled
    settings.database_url = _URL
    settings.credits_enabled = True
    settings.signup_credits = 100
    _db._pool = None
    _run(run_migrations())
    yield
    _db._pool = None
    settings.database_url, settings.credits_enabled = prev_url, prev_enabled


@pytest.fixture
def user():
    uid = f"test-{uuid.uuid4()}"
    yield uid

    async def _cleanup():
        pool = await get_pool()
        async with pool.acquire() as c:
            await c.execute("DELETE FROM scratchpad.redemptions WHERE user_id=$1", uid)
            await c.execute("DELETE FROM scratchpad.credit_ledger WHERE user_id=$1", uid)
            await c.execute("DELETE FROM scratchpad.accounts WHERE user_id=$1", uid)

    _run(_cleanup())


def test_signup_grant_is_once(user):
    async def body():
        a = await credits.ensure_account(user, "e@x.com", "E")
        assert a["credits_balance"] == 100
        a2 = await credits.ensure_account(user, "", "")
        assert a2["credits_balance"] == 100  # no second signup grant
        hist = await credits.history(user)
        assert [h["reason"] for h in hist].count("signup") == 1

    _run(body())


def test_debit_decrements_and_floors_at_zero(user):
    async def body():
        await credits.ensure_account(user)
        assert await credits.try_debit(user, 1, ref="t1") == 99
        assert await credits.try_debit(user, 99, ref="t1") == 0
        assert await credits.try_debit(user, 1, ref="t1") is None

    _run(body())


def test_debit_is_atomic_under_concurrency(user):
    async def body():
        await credits.ensure_account(user)
        await credits.grant(user, -90, reason="admin_adjust")  # balance now 10
        results = await asyncio.gather(
            *(credits.try_debit(user, 1, ref="race") for _ in range(25))
        )
        assert len([r for r in results if r is not None]) == 10  # never 11
        assert await credits.get_balance(user) == 0

    _run(body())


def test_blocked_account_cannot_spend_or_be_granted(user):
    async def body():
        await credits.ensure_account(user)
        await credits.set_status(user, "blocked")
        assert await credits.try_debit(user, 1) is None
        assert await credits.grant(user, 50) is None
        await credits.set_status(user, "active")
        assert await credits.grant(user, 50) == 150

    _run(body())


def test_redeem_code_adds_once_per_user(user):
    async def body():
        await credits.ensure_account(user)
        await credits.grant(user, -100, reason="admin_adjust")
        (code,) = await credits.mint_codes(40, 1, note="test")
        assert (await credits.redeem(user, code))["credits_balance"] == 40
        assert (await credits.redeem(user, code))["error"] == "already_redeemed"

    _run(body())
