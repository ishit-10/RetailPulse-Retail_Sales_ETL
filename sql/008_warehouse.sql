-- Additive warehouse control plane. Existing source-backed analytics views survive.
CREATE TABLE IF NOT EXISTS etl.warehouse_version(version integer PRIMARY KEY CHECK(version=1));
INSERT INTO etl.warehouse_version VALUES(1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS etl.warehouse_runs (
 run_id uuid PRIMARY KEY,
 snapshot_id uuid NOT NULL REFERENCES etl.extract_runs(run_id),
 lake_root text NOT NULL,
 build_schema text NOT NULL UNIQUE,
 manifest_hash text NOT NULL,
 started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 completed_at timestamptz,
 storage_removed_at timestamptz,
 status text NOT NULL CHECK(status IN ('loading','failed','completed')),
 counts jsonb,
 totals jsonb,
 error_class text,
 error_context jsonb
);
CREATE TABLE IF NOT EXISTS etl.warehouse_head (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 run_id uuid NOT NULL REFERENCES etl.warehouse_runs(run_id)
);
