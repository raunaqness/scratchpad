-- The ingest subsystem moves from "one graph per run" to "one graph per
-- account" — every ingested article across every run for a user lands in
-- the same FalkorDB graph, so the 15-article cap and the Ask box are both
-- account-wide, not scoped to a single blog/run.
--
-- episode_uuid: the Graphiti episode this item became, once ingested —
-- needed to surgically remove it later (Graphiti's remove_episode only
-- deletes what was exclusively from that episode, leaving anything shared
-- with other kept articles untouched).
-- removed_at: soft-delete marker so removed items drop out of the library
-- and the running count, without losing the audit trail.

ALTER TABLE scratchpad.ingest_items
    ADD COLUMN IF NOT EXISTS episode_uuid TEXT,
    ADD COLUMN IF NOT EXISTS removed_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_ingest_items_removed
    ON scratchpad.ingest_items (run_id) WHERE removed_at IS NULL;
