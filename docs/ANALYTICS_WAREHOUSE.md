# Stage 4 — PostgreSQL analytics warehouse

Implementation branch: `feature/analytics-layer`, based on merged Stage 3
`b9b4e47`. Local PostgreSQL remains authoritative. There is no cloud service,
new paid dependency, Docker requirement, scheduler, or dashboard.

## Contract and boundaries

The model follows the approved Stage 1 specification and the existing
`sql/003_analytics.sql`, `004_verify.sql`, `006_seed_verify.sql`, and
`docs/MODEL_DECISIONS.md`. The actual source schema and Stage 3 manifest govern
field names and coverage. The loader does **not** query source business rows:
it reads source catalog definitions, copies the published Parquet files into
private replicas, then transforms and reconciles those replicas. It never
inserts, updates, deletes, truncates, or disables constraints/triggers in source.
Existing `analytics.bookings`, `recognized_sales`, `return_credits`,
`sales_events`, `order_financials`, and `store_conversion` remain unchanged;
they still serve Stage 2 verification and are not warehouse reader endpoints.

The source has no brand or region field. State is available; state is not
silently renamed region. Customers and product attributes represent the
published snapshot, not invented historical SCD versions. Online orders have
no originating store or city. Fulfillment centers are not attributed as online
selling stores. Store rankings therefore include a NULL-store online group;
use channel rankings for online performance.

## Publication architecture and grain

`analytics` exposes stable views named below. Their physical dimension and fact
tables live in an isolated, logged `wh_<run UUID without hyphens>` schema. This
indirection allows all tables and lineage to switch atomically, without
TRUNCATE, partial refreshes, or a long transaction holding reader locks during
extraction. Do not query private schemas or union multiple builds.

| Public object | Grain and key | Join rules |
|---|---|---|
| `dim_date` | Indian business date, `date_key` | Calendar days, ISO Monday week, calendar month, April fiscal start year |
| `dim_product` | SKU, `sku_id` | Style/category attributes flattened; original line prices stay in facts |
| `dim_state` | State, `state_id` | Place-of-supply labels for online and POS orders |
| `dim_store` | Operational location, `location_id` | Includes stores, FCs and transit; inspect `type` |
| `dim_customer` | Customer, `customer_id` | Guest customer references remain NULL |
| `dim_channel` | POS/WEB/APP, `channel` | No synthetic channel allocation for stock |
| `dim_order` | Confirmed order, `order_id` | Order-level linkage for refund transactions |
| `fact_order_items` | Original order line, `order_line_id` | Booking measures; discounts and cancellations aggregated before joins |
| `fact_sales` | Recognized invoice line, `invoice_line_id` | FK to order line; FIFO costs aggregated per fulfillment item |
| `fact_returns` | Accepted return item, `return_item_id`; unique credit line | FK to original invoice line; separate accepted and credit dates |
| `fact_refunds` | Refund payment transaction, `payment_txn_id` | FK to order; includes statuses; sum only SUCCEEDED for customer refund value |
| `fact_inventory_daily` | Observed snapshot date/SKU/location | **Sparse observations**, not a complete daily series |
| `fact_inventory_movements` | Movement date/SKU/location/type/stock state | Signed daily movement aggregate; preserves negative adjustments |
| `fact_footfall_daily` | Store/business date | Distinct completed POS orders aggregated before joining footfall |

Source inventory snapshots are month-end plus the final observation date.
Missing observations return no row, not zero stock. Safety stock absent from
policy gives NULL ATP and NULL availability. Negative ATP is retained. The
movement fact supports period opening balances and sell-through without
fabricating daily snapshots.

Do not directly join order-line, sales, return and refund facts on `order_id`
and then sum. They have different grains. `warehouse_sales_events` uses UNION
ALL of recognized sales and negative credit events. `warehouse_sales_detail`
adds many-to-one dimensional attributes. Refund reports use EXISTS against the
order scope, preventing multiplication by the number of order lines. All
warehouse physical primary/foreign keys are validated. The full source FK set
is also validated on private replicas before transformation.

## KPI rules

Amounts are INR. Merchandise excludes GST; tax is separate. Monetary facts use
NUMERIC(20,2); source calculations retain Decimal precision. Dates use
`Asia/Kolkata`, independent of session timezone. No GST or historical prices
are recalculated using current rules.

| Measure | Definition and reporting basis |
|---|---|
| Booked GMV | Original confirmed quantity × original unit price, booking date; later cancellations/returns do not reduce it |
| Booked merchandise / AOV | Initial GMV minus initial discounts / distinct confirmed orders; booking cohort |
| OPD | Distinct confirmed orders / all calendar days in inclusive date range, including zero-order days |
| Discount impact | Initial discount amount and discount / booked GMV |
| Cancellation rate | Cancelled **units** / original confirmed units in booking cohort, as known in the snapshot |
| Gross sales | Recognized invoice taxable value after discount, on delivered/handover date |
| Net sales | Gross sales minus merchandise credits on credit issue date |
| Net units / ASP | Recognized units minus credited units on the same sales-event basis; net sales / positive net units |
| Net COGS | Historical FIFO sale costs minus original cost of RESTOCK returns; no reversal for QUARANTINE/damaged returns |
| Gross profit / margin | Net sales minus net COGS; profit / positive net sales ×100 |
| Refund impact | Merchandise credits are the revenue reduction; SUCCEEDED payment refunds including GST are reported separately and never subtracted twice |
| Return rate | Accepted returned units through explicit as-of cutoff / units in selected fulfillment cohort matured for 30 days |
| Store conversion | Distinct completed POS orders / valid entries at matching completion date/store; no online orders returned in-store |
| Stock availability | Per observed SKU/location: sellable − reserved − safety stock >0; missing policy yields NULL |
| Sell-through | Period net fulfilled units / available stock pool; opening sellable + purchases + sellable restocks; single-store pool also includes inward transfers |

Opening-balance movements on the first selected date enter the available pool.
Opening balances before that date include signed adjustments. Adjustments
within the period remain visible in the movement fact, but are not relabeled
as purchase receipts. Company sell-through excludes internal transfers from
inflows. Cross-store returns are attributed to the original fulfillment
location in the numerator and their receiving location in the restock pool.
City/state sell-through across several locations is rejected because transfer
pairs cannot reliably identify that region's boundary flows; select one store
or company scope.

Every ratio with denominator <=0 returns NULL. Profit can be negative. Zero
orders give OPD=0 and AOV=NULL. Zero valid footfall gives conversion=NULL.
A missing store/day is not synthesized as zero traffic; reports expose covered
store-days. A product filter narrows booked AOV to that product's merchandise
per order containing it; it is not the whole-basket AOV for those customers.

Stage 1 does not specify a distinct payment-refund **rate** denominator. No
refund-rate percentage is invented. `booked_units_per_order` is explicitly
named original units / confirmed orders; it is a derived booking measure, not
an asserted approved realized UPT definition. Refund transactions cannot be
allocated reliably to products, so product filters on refund reports reject;
use product merchandise-credit impact instead. Refund dates are the source
payment transaction `event_at`, not an inferred bank settlement date.

The `daily`, `weekly`, and `monthly` reports respect exact inclusive date
filters, including partial boundary periods. Growth is NULL for partial
periods or nonpositive prior amounts. Complete-period comparisons use full
prior periods, even when those precede the selected date range. Calendar
months are filled to avoid comparing nonadjacent months. Monthly YoY compares
the same calendar month one year earlier. Weekly calendar-year date offsets
usually do not align to Mondays and return NULL; no unsupported ISO-week YoY
convention is implied. `warehouse_monthly_revenue` is an unfiltered calendar
series using LAG(1)/LAG(12); use the parameterized monthly report for filters.
Reports outside source coverage can return zero sales; they do not assert
that future outcomes have been observed. Return as-of must be supplied and
cannot exceed the greatest date in the published warehouse.

## Snapshot admission and transaction boundaries

1. Acquire the existing Stage 2/3 session advisory lock and the lake file lock.
   This serializes cooperating seed, ETL, migration and warehouse processes.
2. Resolve `etl.extract_heads` for the exact absolute lake root. Require a
   completed run and completion time, matching manifest run ID, format v1,
   all 37 tables, all 16 partitions per table, hashes for raw and processed
   files, exact Arrow schema tokens and row counts. Compare input schemas and
   primary keys against actual PostgreSQL catalog definitions, including DOMAIN
   precision/scale from information_schema.
3. Reject snapshots older than 24 hours from extraction **start**, by default;
   reject future timestamps and rollback to an extraction older than the
   current warehouse. `--max-snapshot-age-hours` explicitly changes the age
   policy (positive finite value, maximum one year). Run `etl-run` to refresh.
   Freshness is a bounded extraction-age policy, not CDC proof that no source
   row changed after extraction. Stage 3's repeatable-read snapshot is the
   consistency boundary.
4. Preflight local PostgreSQL data/WAL/tablespace filesystems. Estimate 3 GiB
   reserve plus eight times uncompressed Parquet row-group bytes for replicas,
   indexes, facts, WAL and spill. Old retained builds already consume reported
   free space. Refuse unsafe loads. No PostgreSQL settings are reduced.
5. Audit a `loading` run and create its private schema. COPY Parquet batches of
   at most 5,000 rows into **unlogged disposable** replicas with copied PK,
   unique and check constraints. Each batch commits privately. Recheck free
   space before each batch. No source triggers are bypassed: these are separate
   warehouse replicas, not operational writes.
6. In a private build transaction, add/validate all replica foreign keys, run
   all 30 approved SQL checks against replicas, build logged dimension/fact
   tables and indexes, and independently reconcile measures with the original
   analytics reference queries. Drop replicas/reference views after success.
   Logged facts are never published from unlogged storage.
7. Rehash files and recheck freshness. Confirm the lock connection is alive.
   In one short transaction recheck the ETL head, replace all public warehouse
   view bindings, mark audit completed with counts/totals, and update the
   singleton `etl.warehouse_head`. This is the only reader publication point.

The private transformation transaction can generate substantial WAL and spill;
preflight is conservative, not a guaranteed disk quota. A competing application
can consume space between probes. Do not lower safety thresholds or delete
`pg_wal`. A full 100,000-order warehouse needs more headroom than the small
Parquet lake. With the previously reported ~3 GiB free, expect refusal and
free additional space before attempting the production build.

Single-statement readers see one publication. Multi-query readers must use a
REPEATABLE READ transaction and read lineage in that transaction, as the Python
report API does. Publication briefly waits for readers' relation locks.
Future incompatible warehouse column changes require a new versioned migration;
CREATE OR REPLACE VIEW deliberately rejects incompatible replacement schemas.

## Recovery, idempotency and retention

An identical validated head returns `unchanged` without copying data. A new ETL
run ID rebuilds even if its content is unchanged; it still exposes only one
warehouse, with exact new snapshot lineage. Previous logged builds are retained
until explicit cleanup. ETL audit records and Parquet objects are not deleted.

On a failed chunk, only that chunk rolls back. No private partial data becomes
visible. On interruption/restart/connection loss, the public head survives.
Unlogged replicas may be cleared by PostgreSQL crash recovery; they are never
resumed by row offset. Retry builds a fresh candidate from the current validated
snapshot. This intentionally avoids combining extraction snapshots. Audit
rows may remain `loading` after abrupt death; the next locked load/cleanup
marks them failed. Ambiguous publication commits are not overwritten as failed
if their completion was committed.

```bash
python -m retailpulse warehouse-status
# Reclaims audited failed/interrupted private builds only, after obtaining lock:
python -m retailpulse warehouse-cleanup
# Then retry the same warehouse-load command.
```

After verifying a successful refresh, optionally reclaim **all** prior
unpublished warehouse builds:

```bash
python -m retailpulse warehouse-cleanup --include-previous
```

Cleanup verifies each schema name against its recorded UUID, excludes the
current published build, and rejects external view/FK dependencies. Never add
custom dependencies on private `wh_*` objects. Audit history remains, with
`storage_removed_at`; source, lake, backups and current warehouse remain intact.

`verify-warehouse` compares published row counts and monetary/operational totals
with the committed audit and checks constraint validation. It does not assert
freshness against today's live source or re-run historical replica checks after
those replicas have been removed. Those checks ran before publication.

## Exact macOS commands

First review/apply the Stage 4 patch on the feature branch. No push or merge is
part of this procedure. Keep production and disposable-test URLs separate.

```bash
cd "$HOME/Developer/retailpulse"
git switch feature/analytics-layer
source .venv/bin/activate
python -m pip install -e .
python -m pytest -m 'not integration' -q
```

Native integration tests use an entirely new empty cluster and refuse the
production database name. Existing tests refuse pre-existing source data.
No production reset is needed. Check available disk first; stop if space is low.
Port 55434 must be unused; a failed start stops this command block.

```bash
df -h "$HOME/Developer/retailpulse/data"
(
  set -e
  export PATH="$(brew --prefix postgresql@17)/bin:$PATH"
  WAREHOUSE_TEST_PGDATA="$(mktemp -d /tmp/retailpulse-stage4.XXXXXX)"
  printf 'Disposable cluster: %s\n' "$WAREHOUSE_TEST_PGDATA"
  initdb -D "$WAREHOUSE_TEST_PGDATA" -U retailpulse_test_runner --auth=trust
  pg_ctl -D "$WAREHOUSE_TEST_PGDATA" -l "$WAREHOUSE_TEST_PGDATA/server.log" \
    -o "-h 127.0.0.1 -p 55434 -k $WAREHOUSE_TEST_PGDATA" start
  trap 'pg_ctl -D "$WAREHOUSE_TEST_PGDATA" -m fast stop' EXIT
  createdb -h 127.0.0.1 -p 55434 -U retailpulse_test_runner retailpulse_test
  export RETAILPULSE_TEST_DATABASE_URL='postgresql+psycopg://retailpulse_test_runner@127.0.0.1:55434/retailpulse_test'
  python -m pytest -q
)
```

Trust authentication is limited here to an ephemeral loopback test cluster;
production authentication is unchanged. The stopped cluster is retained for
inspection; the script does not remove directories automatically.

Only after native tests pass, use the usual production `.env` connection:

```bash
python -m retailpulse doctor
python -m retailpulse migrate-warehouse
# Refresh extraction age; does not regenerate or seed source data.
caffeinate -i python -m retailpulse etl-run --lake-root "$HOME/Developer/retailpulse/data"
python -m retailpulse verify-lake --lake-root "$HOME/Developer/retailpulse/data"
python -m retailpulse warehouse-load --lake-root "$HOME/Developer/retailpulse/data" --pg-data-dir "/opt/homebrew/var/postgresql@17" --preflight-only
```

The PostgreSQL path above is the Homebrew path you previously verified. If your server moved, confirm `SHOW data_directory` with your administrator and use the returned path; the loader rejects a mismatch.

Stop if preflight fails. Once it passes:

```bash
caffeinate -i python -m retailpulse warehouse-load --lake-root "$HOME/Developer/retailpulse/data" --pg-data-dir "/opt/homebrew/var/postgresql@17"
python -m retailpulse verify-warehouse
python -m retailpulse warehouse-status
# Exact same snapshot: must report unchanged.
python -m retailpulse warehouse-load --lake-root "$HOME/Developer/retailpulse/data" --pg-data-dir "/opt/homebrew/var/postgresql@17"
python -m retailpulse verify-db
```

Examples (choose dates covered by your actual batch):

```bash
python -m retailpulse warehouse-kpi sales --start 2025-04-01 --end 2026-09-30
python -m retailpulse warehouse-kpi monthly --start 2025-04-01 --end 2026-09-30 --filters '{"channel":"WEB"}'
python -m retailpulse warehouse-kpi products --start 2025-04-01 --end 2026-09-30 --filters '{"category_id":1}'
python -m retailpulse warehouse-kpi footfall --start 2025-04-01 --end 2026-09-30 --filters '{"store_id":1}'
python -m retailpulse warehouse-kpi inventory --start 2026-09-01 --end 2026-09-30
python -m retailpulse warehouse-kpi sell-through --start 2025-04-01 --end 2026-09-30
python -m retailpulse warehouse-kpi refunds --start 2025-04-01 --end 2026-09-30
# As-of is an explicit known observation cutoff; change if your lake ends earlier.
python -m retailpulse warehouse-kpi returns --start 2025-04-01 --end 2026-08-31 --as-of 2026-09-30
```

Supported reports: sales, daily, weekly, monthly, products, categories, stores,
channels, returns, refunds, footfall, inventory, sell-through. JSON filters are
bound parameters; field names are allowlisted. IDs must be integers. Supported
sales filters: city, state_id, store_id, channel, category_id, style_id, sku_id,
customer_id. Inapplicable denominator filters reject instead of silently
ignoring them. Direct SQL clients should use the documented warehouse views
and supply parameters rather than interpolate user input.

## Tests and release gate

See [WAREHOUSE_TEST_RESULTS.md](WAREHOUSE_TEST_RESULTS.md) for actual cloud
results and what remains unverified. Tests cover the existing 500-order golden
fixture, financial reconciliation, partial-period totals, zero denominators,
missing inventory/policy, negative stock adjustments, identical reruns,
incomplete/corrupt/stale snapshot rejection, private-copy interruption,
disconnection, publication rollback and retention cleanup. The project owner supplied successful Mac results on 17 October 2026:
117 tests passed, the 100,000-order warehouse completed, warehouse and source
verification passed, and an identical rerun returned `unchanged`. These are
local acceptance results, not a claim of a new GitHub Actions run. See the
verification report for exact lineage and reporting-period totals.
