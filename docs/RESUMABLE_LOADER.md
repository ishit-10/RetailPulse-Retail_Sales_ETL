# Stage 2 resumable source loader

## Scope and prerequisites

This change loads the existing generated Parquet dataset into the approved `source`
schema. It is not the Stage 3 extraction/transformation pipeline. PostgreSQL stays
local and authoritative; no cloud resource or subscription is involved. The
existing generator and business date range are not changed. The manifest, rather
than generator defaults in the README, determines the imported dates and row counts.

Use the source checkout with `python -m pip install -e .`. Keep the same dataset
files unchanged for the entire run, including retries. The source schema must be
v1. Run `python -m retailpulse migrate-loader` once before seeding; repeating it is
safe. `sql/005_seed_checkpoints.sql` adds only ETL metadata, with its own version.
`etl.schema_version` and the operational DDL/guards are unchanged. The application
role that created/owns `etl` and `source` can run this migration. No superuser grant
or integrity bypass is required.

## Transaction boundaries and trigger analysis

The session holds advisory lock 847210 from admission through completion. It is
shared with initialization/reset and prevents simultaneous cooperating loaders.
The physical connection is discarded at exit so a pooled session cannot retain
this lock. `synchronous_commit=on` ensures acknowledged commits wait for local WAL
flush. Disk admission also requires `fsync=on` and `full_page_writes=on`. PostgreSQL must retain these durability settings. This loader
never changes them, checkpoint settings, indexes, triggers, or foreign keys.

1. Hash the manifest identity and each Parquet file; check footer row counts.
2. Inspect persisted progress and local disk space before any source write.
3. Commit the run (`pending`), table checkpoints, and batch 1 (`GENERATED`, NULL
   `loaded_at`) together. The generator uses batch 1; this is intentionally a
   single-dataset bootstrap loader, not a multi-batch incremental ingestion service.
4. Mark the run `loading`. Load `source_events` first, then the existing dependency
   order in `db.LOAD_ORDER` (masters, orders, fulfillment, invoices, returns,
   credits, payments, inventory and traffic). Preserve Parquet row order.
5. For each chunk, COPY rows, update the exact row offset, and commit **one
   transaction**. Default: 5,000 rows; supported: 1–50,000. Arrow batches bound
   memory without loading all tables into Pandas or making a second staging copy.
   A single large Parquet value/row group can still require extra decoder memory.
6. Rehash all input files. Lock source tables against writes, reconcile, and update
   both the run (`completed`) and operational batch (`LOADED`, timestamp) in the
   same final transaction. `verify-db` repeats reconciliation under source locks.

Immediate FKs require all parents first. Categories and payment transactions have
self-references: the current generator emits parents/captures before children/
refunds. A differently reordered file that violates this contract fails safely;
we do not defer or disable its FKs. Quantity/discount/credit/refund guards enforce
upper limits and ownership against previously committed rows plus the current
chunk. They do not require future tables at intermediate commits. Immutable
financial triggers are never invoked by replay because committed rows are skipped,
not updated/upserted. Whole-order equality, inventory snapshots and the golden
transaction are deliberately checked only at the final barrier.

During loading, some parent-only or incompletely fulfilled orders are visible.
They are **not** certified business data. Consumers must require operational
`LOADED` and ETL `completed`; the existing analytics views themselves are unchanged
and do not filter partial batches. Stop other source writers during ingestion.
The advisory lock is cooperative, not an authorization boundary. Arbitrary
external updates/deletes, edited checkpoint metadata, or disabled constraints by
an administrator are outside the resumability guarantee. Resume checks detect
row-count drift; they are not a cryptographic comparison of every database value.

## Identity and idempotency

`dataset_id` must equal SHA-256 of the manifest without `dataset_id`, using the
existing generator's `json.dumps(..., sort_keys=True)` format. Every listed file
must match its SHA-256 and row count, unknown table names are refused, and order
count must match the manifest configuration. The full manifest is persisted and
must match on resume. Optional tables omitted by the generator have expected zero
rows and still receive checkpoints.

A checkpoint row and its COPY rows have exactly the same commit outcome. After
an uncertain commit acknowledgement, reconnect and read the checkpoint rather
than retrying the last in-memory chunk. Resume scans past committed rows and can
slice a batch at the old offset, so changing `--chunk-rows` is safe. This uses
sequential reads, not a temporary staging database or duplicated Parquet files.
A completed replay verifies identity and row counts, then returns without inserting
anything. Run `verify-db` to repeat the full financial checks.

Existing source rows with no loader metadata are **not adopted automatically**.
Matching counts alone cannot establish provenance or equality. The loader refuses
with no source changes. This also applies to data loaded by the old atomic loader.
Use a separate database for inspection/comparison or recover a known consistent
backup; do not edit checkpoints to force a resume. For the reported failed import,
all 37 source tables must be empty, not just the four previously checked tables.
The loader checks every source table it manages and `ingestion_batches`.

## Disk safety

The supported deployment is native PostgreSQL on the same machine as Python, not
a tunnel, remote DB or container. Read `data_directory` from `pg_settings`; if the
app role cannot see it, supply `--pg-data-dir` only after checking the connected
server as the local administrator. This explicit fallback is an operator assertion
of locality. If the setting is visible, a mismatching argument is rejected.

We measure available space on the PostgreSQL data, resolved WAL and all tablespace
paths, not merely the dataset directory. These paths must be locally accessible.
Archiving and replication slots are refused because they can retain WAL without
bound. This guard does not alter their configuration or remove any WAL.

For **each** relevant filesystem, required free bytes are:

```
10 GiB reserve + 8 × estimated remaining uncompressed Parquet bytes
```

The size basis is the greater of file size and summed uncompressed row-group sizes.
The factor covers row storage, indexes, WAL and overhead; it is conservative
planning, **not a guaranteed upper bound**. Remaining bytes are estimated from
committed row fractions. A shared filesystem is not charged multiple times, but
each checked volume must meet the same conservative threshold. Space is checked
before starting, before every COPY chunk, and before final reconciliation.
The default 10 GiB reserve remains even with all rows committed (see the explicit low-disk option below). The last reported 8.3 GiB
is therefore insufficient. There is deliberately no force/skip-disk-check CLI flag.

A commit does not itself recycle WAL. Recycling depends on PostgreSQL checkpoints
and retention configuration. Do not infer bounded total WAL from bounded
transactions. Custom large `max_wal_size`/`wal_keep_size`, concurrent writers,
other disk consumers, APFS snapshots/quotas, and abnormal row sizes can exceed
estimates. Inspect these locally and leave additional space when necessary.
No forced CHECKPOINT, VACUUM FULL, WAL deletion or durability reduction is used.
The snapshot reconciliation query now uses daily aggregation and a timeline
window rather than a movement-to-every-later-snapshot range join, reducing large
intermediate results. Sort/hash memory is set to 16 MB per operation; verification
can still spill to disk.

## Failure and recovery

| Situation | Durable result | Recovery |
| --- | --- | --- |
| Python exception/Ctrl-C during COPY | Current chunk rolls back; earlier chunks remain | Fix cause; rerun the same seed command |
| Hard process exit or server disconnect | Transaction either committed both rows/checkpoint or neither; state may remain `loading` | Restore service, free space, rerun; lock reacquisition determines availability |
| Disk space preflight refusal | No new source rows | Free space and repeat preflight |
| Disk fills during a chunk | PostgreSQL recovery determines commit outcome; failure status may be unwritable | Free unrelated files, start/recover PostgreSQL normally, inspect logs, repeat preflight and seed |
| Final reconciliation fails | All copied rows may exist, batch remains `GENERATED`, run `failed` | Inspect the named failed checks; do not mark LOADED manually; retry after resolving the cause |
| Changed dataset/checksum | Resume refused | Restore exact original files; do not regenerate in place |
| Rows without checkpoints / count drift | Resume refused; no guessing or deletes | Preserve backup and investigate in a separate database |

`seed-status` reports persisted state, committed/expected rows and safe error class.
`loading` is not proof a process is alive. Failures log class/table/timing, not SQL
values, credentials or entire exceptions. A process killed before its failure
handler cannot record `failed`. Only a verified final transaction sets `LOADED`.
Never delete files from `pg_wal`, use `pg_resetwal`, reset the production database,
or manually advance checkpoints as a recovery shortcut. Aborted inserts can leave
dead space that PostgreSQL may reuse; this implementation does not automatically
compact relations or promise that freed logical rows return filesystem space.

## Reconciliation and tests

Final checks include exact counts for all 36 data tables, explicit anti-joins for
every FK (including the batch reference), the existing 17 SQL checks and 13 extra
checks: invoice discount allocation, historical prices, MRP ceiling,
refund route, credit refund limit, return quantity, credit lineage, reserved
snapshots, channel coverage, exact cart coupons, exact synthetic tax schedule/split,
digital traffic and confirmed-date store traffic. These preserve the old Python
validator’s business checks without materializing the entire dataset in memory. Existing SQL covers taxes, credit ceilings, inventory running balances,
snapshot equality, cost allocation, traffic and the golden transaction. Both
financial status updates roll back if any check fails.

Tests use 500 generated orders. They cover successful/repeated loading, changed
chunk size, failed COPY rollback, process exit, lost connection, wrong identity,
unmanaged partial data, checkpoint drift, FK and immutable financial guards,
reconciliation failure, preflight refusal and mutual exclusion. Integration tests
require an explicitly configured **empty** `retailpulse_test` database and refuse
pre-existing source data. Cleanup is limited to rows created in that initially
empty disposable database. CI container SQL tests substitute the filesystem probe;
disk policy is independently unit-tested. Native macOS disk discovery must also
be exercised with `--preflight-only`.

## Optional low-disk mode (2026-10-16 follow-up)

`seed --low-disk` explicitly selects a **3 GiB reserve**, retaining the 8x remaining
uncompressed-data estimate. The default is still 10 GiB. There is no option to
skip the disk check or choose a reserve below 3 GiB. This is a reduced-headroom
planning policy, not evidence that the full load has been measured to fit.

The existing native-path, fsync/full_page_writes, archive-off and no-replication-slot
checks still apply. Additional admission checks require `max_wal_size <= 1GB`,
`wal_keep_size=0`, and WAL summarization off. Nothing changes PostgreSQL settings.
Chunks are capped at 1,000 rows even when a larger size is requested; decoded Arrow
chunks exceeding 8 MiB are refused before COPY (choose fewer rows if that occurs).
The smaller chunk bound limits typical work between space checks, not total WAL.
Disk checks still run before every COPY and final verification; stopped loads
retain committed checkpoints and never prematurely mark the source batch LOADED.

For the user's measured planning allowance of 1.24 GiB, total required free space
becomes approximately **4.24 GiB**. Existing database/WAL bytes are already reflected
in measured free space. A 170 MB empty/partially allocated database and 256 MB WAL
directory do not measure the eventual full dataset or peak WAL usage. PostgreSQL
explicitly documents max_wal_size as a soft limit, not a storage quota:
https://www.postgresql.org/docs/17/wal-configuration.html

The same limitations about other applications, swap growth, WAL overshoot,
verification spill and disk errors apply with **less margin** than the default.
Completion on a Mac with 4.8 GiB free is not guaranteed. Close unused heavy workloads
and keep backups. If space decreases, allow the loader to stop and resume later;
do not lower the reserve again or delete PostgreSQL-managed files.

No operational schema, financial validation, dataset size or dataset definitions
change. The mode can be switched between attempts without invalidating checkpoints.
Use the explicit flag for both preflight and the later import; changing only
preflight does not change the subsequent default-mode policy.
