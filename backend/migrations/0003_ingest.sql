-- Ingestion subsystem: one run per "bring material in" attempt (blog scrape
-- today; file upload / paste-text share this same shape later), with one row
-- per candidate/selected item so progress is observable per item, not just
-- per run. The scratchpad and the knowledge graph are the durable outputs;
-- these rows are the job/queue state for getting there.

CREATE TABLE IF NOT EXISTS scratchpad.ingest_runs (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id           TEXT,
    source_type       TEXT NOT NULL DEFAULT 'blog',
    source_ref        TEXT NOT NULL,
    discovery_method  TEXT,
    status            TEXT NOT NULL DEFAULT 'discovering',
    max_items         INTEGER NOT NULL DEFAULT 15,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS scratchpad.ingest_items (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id       BIGINT NOT NULL REFERENCES scratchpad.ingest_runs(id) ON DELETE CASCADE,
    url          TEXT,
    title        TEXT,
    published_at TIMESTAMPTZ,
    selected     BOOLEAN NOT NULL DEFAULT false,
    stage        TEXT NOT NULL DEFAULT 'discovered',
    error        TEXT,
    rank         INTEGER NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, url)
);

CREATE INDEX IF NOT EXISTS idx_ingest_items_run
    ON scratchpad.ingest_items (run_id, rank);
