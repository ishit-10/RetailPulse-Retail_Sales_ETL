-- Additive Stage 2 loader metadata; operational schema v1 is unchanged.
CREATE TABLE IF NOT EXISTS etl.seed_loader_version (
 version integer PRIMARY KEY CHECK (version = 1)
);
INSERT INTO etl.seed_loader_version VALUES (1) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS etl.seed_runs (
 batch_id bigint PRIMARY KEY CHECK (batch_id = 1),
 dataset_id text NOT NULL,
 manifest jsonb NOT NULL,
 state text NOT NULL CHECK (state IN ('pending','loading','failed','completed')),
 started_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 error_class text,
 completed_at timestamptz
);
CREATE TABLE IF NOT EXISTS etl.seed_tables (
 batch_id bigint NOT NULL REFERENCES etl.seed_runs,
 table_name text NOT NULL,
 expected_rows bigint NOT NULL CHECK (expected_rows >= 0),
 committed_rows bigint NOT NULL DEFAULT 0 CHECK (committed_rows >= 0 AND committed_rows <= expected_rows),
 state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','loading','failed','completed')),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(batch_id, table_name)
);
