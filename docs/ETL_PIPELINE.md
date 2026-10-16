# Stage 3: incremental local Parquet lake

## Scope and source contract

Local PostgreSQL remains authoritative. Stage 3 never inserts, updates, deletes,
truncates, or alters a `source` table and never disables triggers. Migration
`007_extract_checkpoints.sql` creates only three ETL metadata tables and an index.
Run it explicitly with `python -m retailpulse migrate-etl`. It is transactional,
versioned (v1), and repeatable. Existing schema and loader version numbers stay
unchanged. No dashboard, scheduler, cloud storage, or paid service is introduced.

All 37 operational tables are copied, keeping original column names and keys:

| Required subject | Lake source tables |
| --- | --- |
| Orders / items | sales_orders, order_lines, order_line_discounts |
| Order changes / cancellations | order_status_events, order_line_cancellations |
| Payments / refunds | payment_transactions (CAPTURE and REFUND), payment_status_events |
| Returns / credit | return_requests, return_items, credit_notes, credit_note_lines |
| Customers | customers |
| Products | categories, product_styles, skus, price_versions |
| Stores | locations, states, gst_registrations |
| Inventory | inventory_snapshots, inventory_movements, stock_lots, inventory_reservations, inventory_policies, cost allocations |
| Footfall / traffic | store_footfall_daily, digital_traffic_daily |
| Supporting lineage | ingestion_batches, source_events, promotions, tax_rules, fulfillments, fulfillment_items, sales_invoices, invoice_lines, demand_rejections |

The business model is a settled synthetic historical dataset. Existing 30 SQL
business checks (including golden order, tax, financial and stock reconciliation)
remain publication gates. Arbitrary in-progress live orders that fail those checks
are not certified; the prior lake remains readable until the source reconciles.
Stage 3 does not manufacture mutable order status columns: orders are immutable;
changes belong to their related event/cancellation/payment/return tables.

## Incremental strategy and correctness

The schema has no universal `updated_at`, and event time is business time, not commit
time. An ID or timestamp watermark would lose backdated events and mutable-table
updates. Therefore v1 deliberately performs **full source scans with incremental
partition publication**. This is not database log CDC and does not offer an
incremental SQL read volume guarantee.

One REPEATABLE READ, READ ONLY transaction establishes a consistent snapshot of
all tables. Reads use server-side cursors in 5,000-row batches; Pandas preserves
Python objects (particularly Decimal and large integers), and explicit Arrow
schemas preserve money, UTC timestamps, dates, booleans and nullable values.
The database snapshot identifier is recorded for audit, not used as a resumable
PostgreSQL snapshot token after disconnection.

Each table has 16 deterministic buckets. A numeric primary-key component modulo
16 is used where possible; otherwise the first MD5 byte of the first key's text
is used. Composite keys are preserved, and rows are sorted by the entire primary
key. Buckets rather than event dates ensure changing a timestamp cannot strand
an old row in a previous date partition. Primary-key edits and deletes appear
as removal from the old bucket and addition to the new bucket in one publication.

A deterministic Parquet checksum is the persisted partition change checkpoint.
Identical partitions reuse their immutable objects. Changed partitions replace
whole buckets in the new manifest, not append records to the current dataset.
Late records with low IDs or old timestamps are included on the next scan.
Intermediate updates between snapshots are not an audit history; operational
event tables retain their own event history. Schema drift fails explicitly and
requires a reviewed format migration rather than silently casting new columns.

## Files and publication

Default layout (all beneath ignored `data/`):

- `data/raw/batch_001/`: existing synthetic generator output, untouched.
- `data/raw/extract/<table>/bucket=00/<sha256>.parquet`: immutable typed source partitions.
- `data/processed/extract/<table>/bucket=00/<sha256>.parquet`: validated normalized partitions.
- `data/.retailpulse-etl.lock`: local advisory process lock.

Raw and processed have identical normalized content in this stage, so processed
files are hardlinks to raw files, not a second physical copy. Both must reside on
one hardlink-capable local filesystem (APFS on macOS works). Never edit these
objects in place: modifying either hardlink modifies both. No dimensional model,
aggregated metrics, or dashboard is implied by the processed layer.

Each partition is streamed to `.writing.parquet`, closed, fsynced, hashed, then
renamed atomically to its content-addressed path. Files and directory entries
are fsynced before publication. The writer verifies counts, nullability and
strict primary-key ordering, and reconciles each table with `COUNT(*)` from the
same source snapshot. Every referenced file's checksum, schema and count is
validated again before publication. Validated PostgreSQL constraints plus the
exact source copies preserve foreign-key relationships across the snapshot.

`etl.extract_runs` records run UUID, start/end, status, snapshot identifier,
rows scanned, changed partitions, safe failure class/context, and final manifest.
`etl.extract_heads` maps an absolute lake root to the current completed run.
One PostgreSQL transaction marks the run completed and moves the head together.
This database pointer is the only publication authority. There is no filesystem
`CURRENT` pointer that could diverge from a database commit. Files are durable
before the transaction, and `synchronous_commit` is enabled on metadata writes.

**Do not glob all Parquet files in either directory.** Old versions coexist and
would duplicate records. Readers pin the current manifest once and read exactly
its files; they can continue using that manifest during the next publication.

```python
from retailpulse import db, etl
engine = db.engine()
try:
    manifest = etl.current_manifest(engine, 'data')
    orders = etl.read_table(engine, 'sales_orders', 'data', manifest).to_pandas()
    payments = etl.read_table(engine, 'payment_transactions', 'data', manifest).to_pandas()
    refunds = payments.loc[payments['type'].eq('REFUND')]
finally:
    engine.dispose()
```

`read_table` materializes the selected table for convenience; extraction itself
is streamed. For large downstream reads, pin the manifest and iterate the listed
files with `pyarrow.parquet.ParquetFile(...).iter_batches()` instead.

## Interruption, recovery and concurrency

Stage 3 holds the same session advisory lock as the Stage 2 loader/reset/migration
and a filesystem lock for its lake root. Concurrent ETL writers, loader runs and
resets are refused or serialized by their existing lock contract. Ordinary source
DML can continue: changes committed after the snapshot appear on the next run.
A long snapshot can delay PostgreSQL vacuum cleanup; run during quiet periods.

On an exception, the run becomes failed when the lock connection is still usable.
On hard termination or server failure it may remain running. The next successful
lock owner marks that abandoned run failed (`ProcessInterrupted`) and starts a
new attempt. It rescans a fresh consistent source snapshot, reusing byte-identical
partition objects already written. **Resume is file reuse, not resuming a lost
transaction or skipping an old scan offset.** Partial table offsets would mix
snapshots and lose changes, so they are never trusted.

The previous head remains readable after any pre-publication failure. On an
ambiguous database commit outcome, inspect `etl-status`/`verify-lake` and rerun;
the failure handler never changes an already completed run. Files written before
a crash but not published are harmless unreferenced objects. No automatic garbage
collection is implemented: historical manifests remain readable, and a growing
change history consumes storage. Retention/garbage collection needs a separate
reviewed reader-pinning policy; do not manually delete referenced files.

Before each partition and batch, the writer requires a 3 GiB free-space reserve
plus twice the current decoded batch size. Only one temporary partition is being
written at a time; a no-op run temporarily rewrites partitions to establish their
checksums, then discards those temporary copies. This is a guard, not a total-size
prediction or disk quota. Other applications, PostgreSQL verification spill and
vacuum can use additional space. A disk failure leaves the old checkpoint intact;
free unrelated storage and rerun the same command. Never delete `pg_wal`.

Corrupt/missing published files fail validation before the next checkpoint moves.
Restore the lake files from a matching backup, or choose a new empty `--lake-root`
to rebuild from the intact source. A lake path is part of checkpoint identity;
use the same absolute path on every normal run. Database backups and filesystem
backups must be coordinated to preserve referenced lake versions.

## Exact Mac commands

From the existing feature branch at commit `42a3ec6`, apply the supplied Stage 3
patch on a new branch. `git apply --check` must succeed before application:

```bash
cd "$HOME/Developer/retailpulse"
source .venv/bin/activate
git status --short
git switch -c feature/incremental-parquet-etl
git apply --check "$HOME/Downloads/retailpulse-stage3.patch"
git apply "$HOME/Downloads/retailpulse-stage3.patch"
python -m pytest -m 'not integration' -q
```

Run integration tests in a new **disposable** cluster, never on `retailpulse`.
This subshell stops on any setup error and stops its own server on exit. Port
55433 must be unused. The test fixture refuses existing source rows. The cluster
is left on disk for inspection; its printed path identifies it for later cleanup.

```bash
(
  set -e
  export PATH="$(brew --prefix postgresql@17)/bin:$PATH"
  ETL_TEST_PGDATA="$(mktemp -d /tmp/retailpulse-stage3.XXXXXX)"
  printf 'Disposable test cluster: %s\n' "$ETL_TEST_PGDATA"
  initdb -D "$ETL_TEST_PGDATA" -U retailpulse_test_runner --auth=trust
  pg_ctl -D "$ETL_TEST_PGDATA" -l "$ETL_TEST_PGDATA/server.log" \
    -o "-h 127.0.0.1 -p 55433 -k $ETL_TEST_PGDATA" start
  trap 'pg_ctl -D "$ETL_TEST_PGDATA" -m fast stop' EXIT
  createdb -h 127.0.0.1 -p 55433 -U retailpulse_test_runner retailpulse_test
  export RETAILPULSE_TEST_DATABASE_URL='postgresql+psycopg://retailpulse_test_runner@127.0.0.1:55433/retailpulse_test'
  python -m pytest -q
)
```

After native tests pass, use the existing `.env` pointing to local `retailpulse`.
The commands below read source rows and write ETL metadata/local files only; they
do not regenerate, reset or seed your operational data:

```bash
python -m retailpulse doctor
python -m retailpulse migrate-etl
df -h "$HOME/Developer/retailpulse/data"
caffeinate -i python -m retailpulse etl-run --lake-root "$HOME/Developer/retailpulse/data"
python -m retailpulse etl-status --lake-root "$HOME/Developer/retailpulse/data"
python -m retailpulse verify-lake --lake-root "$HOME/Developer/retailpulse/data"
```

Repeat `etl-run` with the same root to resume or refresh. With unchanged source,
`partitions_changed` should be zero. `verify-lake` validates the published snapshot,
not whether the source has changed since extraction; run ETL again to synchronize.
For the verified full dataset, expect 100,000 sales_orders, 209,999 order_lines,
119,641 payment_transactions and 1,612,566 source_events in `verify-lake`.
No full-data Stage 3 result is claimed until you execute these on your Mac.

All commands also work through the installed `retailpulse` console entry point.
No dependency additions are required. No push or merge is performed.
