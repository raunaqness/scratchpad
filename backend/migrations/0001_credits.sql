-- Credit system: accounts, an append-only ledger, and redeem codes.
-- Everything lives in its own `scratchpad` schema so it's easy to see in
-- Supabase Studio and trivial to pg_dump onto hosted Supabase later.

CREATE SCHEMA IF NOT EXISTS scratchpad;

-- One row per signed-in user, keyed by the Google `sub`.
CREATE TABLE IF NOT EXISTS scratchpad.accounts (
    user_id         TEXT PRIMARY KEY,
    email           TEXT NOT NULL DEFAULT '',
    name            TEXT NOT NULL DEFAULT '',
    credits_balance INTEGER NOT NULL DEFAULT 0 CHECK (credits_balance >= 0),
    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'blocked')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Append-only history of every balance change. `accounts.credits_balance` is a
-- cache of SUM(delta) over this table for the user.
CREATE TABLE IF NOT EXISTS scratchpad.credit_ledger (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id       TEXT NOT NULL REFERENCES scratchpad.accounts (user_id),
    delta         INTEGER NOT NULL,
    reason        TEXT NOT NULL,          -- signup | message | grant | redeem | admin_adjust | refund
    balance_after INTEGER NOT NULL,
    ref           TEXT,                   -- thread_id, redeem code, or a note
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ledger_user_time
    ON scratchpad.credit_ledger (user_id, created_at DESC);

-- Pre-minted codes worth N credits ("credit keys").
CREATE TABLE IF NOT EXISTS scratchpad.redeem_codes (
    code            TEXT PRIMARY KEY,
    credits         INTEGER NOT NULL CHECK (credits > 0),
    note            TEXT NOT NULL DEFAULT '',
    max_redemptions INTEGER NOT NULL DEFAULT 1 CHECK (max_redemptions > 0),
    times_redeemed  INTEGER NOT NULL DEFAULT 0,
    expires_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS scratchpad.redemptions (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code       TEXT NOT NULL REFERENCES scratchpad.redeem_codes (code),
    user_id    TEXT NOT NULL REFERENCES scratchpad.accounts (user_id),
    credits    INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (code, user_id)              -- a user can redeem a given code once
);

-- --------------------------------------------------------------------------
-- Helper: apply a credit delta and write the ledger row in one transaction.
-- Positive delta = grant, negative = spend. Returns the new balance, or NULL
-- if the account is missing / blocked / would go negative.
-- Callable straight from the Studio SQL editor:
--   SELECT scratchpad.apply_credits('google-sub-123', 500, 'admin_adjust', 'paid via UPI');
-- --------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION scratchpad.apply_credits(
    p_user_id TEXT,
    p_delta   INTEGER,
    p_reason  TEXT,
    p_ref     TEXT DEFAULT NULL
) RETURNS INTEGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_balance INTEGER;
BEGIN
    UPDATE scratchpad.accounts
       SET credits_balance = credits_balance + p_delta,
           updated_at = now()
     WHERE user_id = p_user_id
       AND status = 'active'
       AND credits_balance + p_delta >= 0
    RETURNING credits_balance INTO v_balance;

    IF NOT FOUND THEN
        RETURN NULL;
    END IF;

    INSERT INTO scratchpad.credit_ledger (user_id, delta, reason, balance_after, ref)
    VALUES (p_user_id, p_delta, p_reason, v_balance, p_ref);

    RETURN v_balance;
END;
$$;

-- Convenience for the operator: grant by email instead of user_id.
CREATE OR REPLACE FUNCTION scratchpad.grant_credits_by_email(
    p_email  TEXT,
    p_amount INTEGER,
    p_note   TEXT DEFAULT 'admin_adjust'
) RETURNS INTEGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_user_id TEXT;
BEGIN
    SELECT user_id INTO v_user_id
      FROM scratchpad.accounts
     WHERE lower(email) = lower(p_email)
     ORDER BY updated_at DESC
     LIMIT 1;

    IF v_user_id IS NULL THEN
        RAISE EXCEPTION 'no account with email %', p_email;
    END IF;

    RETURN scratchpad.apply_credits(v_user_id, p_amount, 'admin_adjust', p_note);
END;
$$;
