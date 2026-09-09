-- Generated artifacts: one row per skill run, thread-scoped, versioned per type.
-- The scratchpad is the source of truth; these are disposable outputs the user
-- can regenerate at will.

CREATE TABLE IF NOT EXISTS scratchpad.artifacts (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    thread_id  TEXT NOT NULL,
    skill_id   TEXT NOT NULL,
    version    INTEGER NOT NULL,
    body       TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (thread_id, skill_id, version)
);

CREATE INDEX IF NOT EXISTS idx_artifacts_thread_skill
    ON scratchpad.artifacts (thread_id, skill_id, version DESC);
