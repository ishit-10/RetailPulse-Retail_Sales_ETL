-- Optional administrator setup, not run automatically by the application.
-- Run in retailpulse. No password or role membership is stored in Git.
-- After this script, use psql's interactive: \password retailpulse_dashboard
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='retailpulse_dashboard') THEN
  CREATE ROLE retailpulse_dashboard LOGIN;
 END IF;
END $$;
ALTER ROLE retailpulse_dashboard SET default_transaction_read_only=on;
GRANT CONNECT ON DATABASE retailpulse TO retailpulse_dashboard;
GRANT USAGE ON SCHEMA analytics,etl TO retailpulse_dashboard;
-- Public warehouse views retain their identities through CREATE OR REPLACE VIEW.
GRANT SELECT ON analytics.dim_date,analytics.dim_product,analytics.dim_store,
 analytics.dim_state,analytics.dim_channel,analytics.fact_order_items,
 analytics.fact_sales,analytics.fact_returns,analytics.fact_refunds,
 analytics.fact_inventory_daily,analytics.fact_inventory_movements,
 analytics.fact_footfall_daily,analytics.warehouse_sales_events TO retailpulse_dashboard;
GRANT SELECT (singleton,run_id) ON etl.warehouse_head TO retailpulse_dashboard;
GRANT SELECT (run_id,snapshot_id,started_at,completed_at,status,counts,totals,error_class,error_context,lake_root)
 ON etl.warehouse_runs TO retailpulse_dashboard;
GRANT SELECT (run_id,started_at,completed_at,status,rows_scanned,partitions_changed,error_class,error_context,lake_root)
 ON etl.extract_runs TO retailpulse_dashboard;
GRANT SELECT ON etl.extract_heads TO retailpulse_dashboard;
-- No INSERT/UPDATE/DELETE, source-table SELECT, migration or private-build grants.
