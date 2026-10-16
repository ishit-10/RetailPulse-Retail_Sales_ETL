-- Stage 3 v1: additive ETL metadata only; no operational DDL or triggers.
CREATE TABLE IF NOT EXISTS etl.extract_version (version integer PRIMARY KEY CHECK(version=1));
INSERT INTO etl.extract_version VALUES (1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS etl.extract_runs (
 run_id uuid PRIMARY KEY,
 lake_root text NOT NULL,
 started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 completed_at timestamptz,
 status text NOT NULL CHECK(status IN ('running','failed','completed')),
 source_snapshot text,
 rows_scanned bigint NOT NULL DEFAULT 0,
 partitions_changed integer NOT NULL DEFAULT 0,
 error_class text,
 error_context jsonb,
 manifest jsonb
);
CREATE TABLE IF NOT EXISTS etl.extract_heads (
 lake_root text PRIMARY KEY,
 run_id uuid NOT NULL REFERENCES etl.extract_runs(run_id)
);
CREATE INDEX IF NOT EXISTS extract_runs_root ON etl.extract_runs(lake_root,started_at);
