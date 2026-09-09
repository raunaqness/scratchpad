"""Credit accounting on Postgres (Supabase).

One credit == one chat turn. The HTTP layer (``backend/agent.py``) calls
``ensure_account`` then ``try_debit`` before running a turn; everything else here
is for the admin CLI (``python -m backend.credits ...``).

All balance changes go through the ``scratchpad.apply_credits`` SQL function so
the ledger row and the balance move together, atomically.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.config import settings
from backend.db import close_pool, get_pool, run_migrations

logger = logging.getLogger(__name__)

_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no look-alikes


def _new_code() -> str:
    body = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(12))
    return f"SCRP-{body[:4]}-{body[4:8]}-{body[8:]}"


# --- account lifecycle -----------------------------------------------------

async def ensure_account(user_id: str, email: str = "", name: str = "") -> dict[str, Any]:
    """Upsert an account. First sight seeds ``signup_credits`` + a ledger row."""

    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            """
            INSERT INTO scratchpad.accounts (user_id, email, name)
            VALUES ($1, $2, $3)
            ON CONFLICT (user_id) DO UPDATE SET
                email = COALESCE(NULLIF(EXCLUDED.email, ''), scratchpad.accounts.email),
                name  = COALESCE(NULLIF(EXCLUDED.name, ''),  scratchpad.accounts.name),
                updated_at = now()
            RETURNING (xmax = 0) AS inserted, credits_balance, status, email, name
            """,
            user_id, email or "", name or "",
        )
        balance = row["credits_balance"]
        if row["inserted"] and settings.signup_credits > 0:
            await conn.execute(
                "UPDATE scratchpad.accounts SET credits_balance = $2 WHERE user_id = $1",
                user_id, settings.signup_credits,
            )
            await conn.execute(
                """
                INSERT INTO scratchpad.credit_ledger
                    (user_id, delta, reason, balance_after, ref)
                VALUES ($1, $2, 'signup', $2, NULL)
                """,
                user_id, settings.signup_credits,
            )
            balance = settings.signup_credits
    return {
        "user_id": user_id,
        "email": row["email"],
        "name": row["name"],
        "credits_balance": balance,
        "status": row["status"],
    }


async def get_account(user_id: str) -> dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT user_id, email, name, credits_balance, status, "
            "       created_at, updated_at "
            "FROM scratchpad.accounts WHERE user_id = $1",
            user_id,
        )
    return dict(row) if row else None


async def get_balance(user_id: str) -> int | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT credits_balance FROM scratchpad.accounts WHERE user_id = $1",
            user_id,
        )


# --- the hot path --------------------------------------------------------

async def try_debit(
    user_id: str, amount: int, *, reason: str = "message", ref: str | None = None
) -> int | None:
    """Atomically spend ``amount``. Returns the new balance, or ``None`` when the
    account is missing / blocked / lacks the credits (nothing is written)."""

    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT scratchpad.apply_credits($1, $2, $3, $4)",
            user_id, -abs(amount), reason, ref,
        )


async def grant(
    user_id: str, amount: int, *, reason: str = "grant", ref: str | None = None
) -> int | None:
    """Add (or remove, if ``amount`` < 0) credits. Returns the new balance."""

    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT scratchpad.apply_credits($1, $2, $3, $4)",
            user_id, amount, reason, ref,
        )


async def set_status(user_id: str, status: str) -> bool:
    if status not in ("active", "blocked"):
        raise ValueError("status must be 'active' or 'blocked'")
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE scratchpad.accounts SET status = $2, updated_at = now() "
            "WHERE user_id = $1",
            user_id, status,
        )
    return result.endswith("1")


async def history(user_id: str, limit: int = 50) -> list[dict[str, Any]]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT created_at, delta, reason, balance_after, ref "
            "FROM scratchpad.credit_ledger WHERE user_id = $1 "
            "ORDER BY created_at DESC LIMIT $2",
            user_id, limit,
        )
    return [dict(r) for r in rows]


async def list_accounts(limit: int = 50, order: str = "recent") -> list[dict[str, Any]]:
    order_sql = {
        "recent": "updated_at DESC",
        "balance": "credits_balance ASC",
        "created": "created_at DESC",
    }.get(order, "updated_at DESC")
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT user_id, email, name, credits_balance, status, updated_at "
            f"FROM scratchpad.accounts ORDER BY {order_sql} LIMIT $1",
            limit,
        )
    return [dict(r) for r in rows]


async def resolve_user_id(*, user_id: str | None, email: str | None) -> str:
    if user_id:
        return user_id
    if not email:
        raise SystemExit("pass --user-id or --email")
    pool = await get_pool()
    async with pool.acquire() as conn:
        found = await conn.fetchval(
            "SELECT user_id FROM scratchpad.accounts WHERE lower(email) = lower($1) "
            "ORDER BY updated_at DESC LIMIT 1",
            email,
        )
    if not found:
        raise SystemExit(f"no account with email {email!r}")
    return found


# --- redeem codes ("credit keys") --------------------------------------

async def mint_codes(
    credits: int,
    count: int = 1,
    *,
    note: str = "",
    max_redemptions: int = 1,
    expires_at: datetime | None = None,
) -> list[str]:
    pool = await get_pool()
    codes: list[str] = []
    async with pool.acquire() as conn:
        for _ in range(count):
            code = _new_code()
            await conn.execute(
                "INSERT INTO scratchpad.redeem_codes "
                "(code, credits, note, max_redemptions, expires_at) "
                "VALUES ($1, $2, $3, $4, $5)",
                code, credits, note, max_redemptions, expires_at,
            )
            codes.append(code)
    return codes


async def redeem(user_id: str, code: str) -> dict[str, Any]:
    """Redeem a code into an account. Idempotent-safe: a second attempt by the
    same user returns ``already_redeemed`` without changing the balance."""

    code = code.strip().upper()
    pool = await get_pool()
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "SELECT credits, max_redemptions, times_redeemed, expires_at "
            "FROM scratchpad.redeem_codes WHERE code = $1 FOR UPDATE",
            code,
        )
        if row is None:
            return {"ok": False, "error": "invalid_code"}
        if await conn.fetchval(
            "SELECT 1 FROM scratchpad.redemptions WHERE code = $1 AND user_id = $2",
            code, user_id,
        ):
            return {"ok": False, "error": "already_redeemed"}
        if row["expires_at"] and row["expires_at"] < datetime.now(timezone.utc):
            return {"ok": False, "error": "expired"}
        if row["times_redeemed"] >= row["max_redemptions"]:
            return {"ok": False, "error": "exhausted"}

        await conn.execute(
            "UPDATE scratchpad.redeem_codes SET times_redeemed = times_redeemed + 1 "
            "WHERE code = $1",
            code,
        )
        await conn.execute(
            "INSERT INTO scratchpad.redemptions (code, user_id, credits) "
            "VALUES ($1, $2, $3)",
            code, user_id, row["credits"],
        )
        new_balance = await conn.fetchval(
            "SELECT scratchpad.apply_credits($1, $2, 'redeem', $3)",
            user_id, row["credits"], code,
        )
    return {"ok": True, "credits_added": row["credits"], "credits_balance": new_balance}


# --- CLI -----------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m backend.credits",
        description="Admin the credit system (reads SIGNAL_DATABASE_URL).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def who(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--user-id")
        sp.add_argument("--email")

    sp = sub.add_parser("migrate", help="apply pending SQL migrations")

    sp = sub.add_parser("grant", help="add credits (negative amount to remove)")
    who(sp)
    sp.add_argument("--amount", type=int, required=True)
    sp.add_argument("--note", default="admin_adjust")

    sp = sub.add_parser("balance", help="show an account's balance")
    who(sp)

    sp = sub.add_parser("history", help="show an account's ledger")
    who(sp)
    sp.add_argument("--limit", type=int, default=20)

    sp = sub.add_parser("block", help="disable an account")
    who(sp)
    sp = sub.add_parser("unblock", help="re-enable an account")
    who(sp)

    sp = sub.add_parser("accounts", help="list accounts")
    sp.add_argument("--limit", type=int, default=50)
    sp.add_argument("--order", choices=["recent", "balance", "created"], default="recent")

    sp = sub.add_parser("mint-code", help="create redeem code(s)")
    sp.add_argument("--credits", type=int, required=True)
    sp.add_argument("--count", type=int, default=1)
    sp.add_argument("--note", default="")
    sp.add_argument("--max-redemptions", type=int, default=1)
    sp.add_argument("--expires-days", type=int, default=0)

    sp = sub.add_parser("redeem", help="redeem a code into an account")
    who(sp)
    sp.add_argument("code")

    return p


async def _run(args: argparse.Namespace) -> int:
    if not settings.database_url:
        print("SIGNAL_DATABASE_URL is not set")
        return 2

    if args.cmd == "migrate":
        applied = await run_migrations()
        print("applied:", ", ".join(applied) if applied else "(nothing pending)")
        return 0

    if args.cmd == "accounts":
        for a in await list_accounts(args.limit, args.order):
            print(
                f"{a['credits_balance']:>7}  {a['status']:<7}  "
                f"{(a['email'] or a['user_id']):<40}  {a['user_id']}"
            )
        return 0

    if args.cmd == "mint-code":
        expires = (
            datetime.now(timezone.utc) + timedelta(days=args.expires_days)
            if args.expires_days
            else None
        )
        for code in await mint_codes(
            args.credits, args.count, note=args.note,
            max_redemptions=args.max_redemptions, expires_at=expires,
        ):
            print(f"{code}   ({args.credits} credits)")
        return 0

    uid = await resolve_user_id(user_id=args.user_id, email=args.email)

    if args.cmd == "grant":
        new = await grant(uid, args.amount, reason="admin_adjust", ref=args.note)
        if new is None:
            print(f"refused (missing/blocked/would go negative): {uid}")
            return 1
        print(f"{uid}  ->  {new} credits")
        return 0

    if args.cmd == "balance":
        acct = await get_account(uid)
        print(acct)
        return 0

    if args.cmd == "history":
        for h in await history(uid, args.limit):
            print(
                f"{h['created_at']:%Y-%m-%d %H:%M}  {h['delta']:>+5}  "
                f"{h['reason']:<13}  bal={h['balance_after']:<6}  {h['ref'] or ''}"
            )
        return 0

    if args.cmd in ("block", "unblock"):
        ok = await set_status(uid, "blocked" if args.cmd == "block" else "active")
        print("ok" if ok else "no such account")
        return 0 if ok else 1

    if args.cmd == "redeem":
        print(await redeem(uid, args.code))
        return 0

    return 2


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _build_parser().parse_args()
    try:
        raise SystemExit(asyncio.run(_run(args)))
    finally:
        try:
            asyncio.run(close_pool())
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
