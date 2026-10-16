# Stage 3 final verification report — 2026-10-17

## Verified outcome

Stage 3 extraction, local Parquet publication, verification, unchanged-source
idempotency, and the complete automated suite passed on the user's Mac.
The local results below were supplied by the user; no remote connection to the
Mac's PostgreSQL server was made.

| Check | Actual result |
| --- | --- |
| Full pytest suite, disposable PostgreSQL 17 cluster | **87 passed in 91.53 seconds**, no failures or skips reported |
| Test composition | 65 non-integration tests and 22 PostgreSQL integration tests |
| Full source extraction | **5,068,139 rows across 37 tables** |
| Initial publication | **592 partitions**, completed |
| Published lake validation | `lake_checks: passed`, expected counts across all 37 tables |
| Unchanged-source rerun | Completed, **5,068,139 rows scanned, zero partitions changed** |
| Disposable test server | Stopped successfully by the shell EXIT trap |

Full-data publication run: `3cb82cfd-bad9-405c-a54a-06fecc2461fc`.
Unchanged-source publication run: `84e86961-d876-41eb-b4e9-f163ed1bf8ef`.

Verified lake counts include 100,000 sales orders, 209,999 order lines,
119,641 payment transactions, 610,857 order status events, 539,147 inventory
movements, and 1,612,566 source events. The extractor's source business validation
and lake reconciliation gates passed before each successful publication.

The tests used the disposable `retailpulse_test` database on port 55433,
cluster `/tmp/retailpulse-stage3.sJifpV`, role `retailpulse_test_runner`.
The existing fixture refuses pre-existing source data before authorizing cleanup.
The production `retailpulse` database was not reset for these tests.

## Automated coverage

The 65 non-integration tests include existing generator/loader coverage and
15 ETL type/filesystem cases covering exact money and large integer round trips,
Unicode, dates, timestamps, nulls, domain numeric modifiers, duplicate key rejection,
immutable partition reuse, changed/deleted rows, empty partitions, interrupted
writes, hardlinks, disk rejection, process locking, corruption, and manifest
schema/count/partition reconciliation.

The 22 native integration tests include 14 existing Stage 2 loader cases and
8 Stage 3 cases covering:

1. Full extraction, no-op replay, backdated events, mutable updates, deletes,
   unique primary keys, and old pinned-manifest reads.
2. Interrupted publication retaining the old head and duplicate-free retry.
3. Initial filesystem failure publishing nothing and successful retry.
4. Partial source ingestion and concurrent Stage 2 loader refusal.
5. Corrupt published files preventing checkpoint advancement.
6. Concurrent source changes appearing only in the next consistent snapshot.
7. Abrupt subprocess exit, abandoned-run detection, and recovery.
8. Cancellation/refund changes and backdated payment events, with failed inventory
   reconciliation preserving the previous publication.

Integration tests use a test-only 64 MiB free-space reserve for the small
500-order fixture, including its subprocess. They still inspect actual free space.
The production ETL reserve remains 3 GiB; unit tests independently cover rejection.
Full-size actual disk exhaustion or a PostgreSQL server restart during Stage 3
was not deliberately induced.

## Cloud execution and fixes

After both fixes, cloud execution returned **65 passed, 22 skipped in 11.50 seconds**;
no native PostgreSQL test URL was configured. Native PostgreSQL cannot be started
in that environment because unprivileged-user switching is unavailable. The later
Mac execution above supplies the native results; skipped cloud tests are not
counted as cloud successes. Compilation, CLI command registration and Git diff
whitespace checks also succeeded during implementation.

The first Mac extraction exposed domain reflection omitting numeric precision and
scale. The mapper now reads these from PostgreSQL `information_schema.columns`
and refuses unknown numeric modifiers instead of defaulting to scale zero.
An exact fractional-decimal regression test covers this case; no source monetary
values were rounded or changed.

The first native integration attempt hit the production disk reserve before seven
scenarios reached their target behavior. The test-only reserve correction enabled
the subsequent complete 87-test pass without lowering the real CLI's disk guard.

## Implementation and practical limits

Changed files: `retailpulse/etl.py`, `retailpulse/__main__.py`,
`sql/007_extract_checkpoints.sql`, `tests/test_etl.py`, `tests/test_etl_postgres.py`,
`README.md`, `docs/ETL_PIPELINE.md`, and this report.

Stage 2 loader, operational DDL, constraints, business validation SQL, generator,
dependency pins and CI workflow remain unchanged. Existing CI collects the new
tests, but no Stage 3 GitHub Actions result is claimed. Nothing was pushed or merged
by the agent. Generated data, lake files, credentials and backups are excluded
from the source patches.

The extractor scans all source rows per attempt and publishes changed partitions;
it is not transaction-log CDC. Historical objects consume disk space; automatic
garbage collection is not implemented. Disk checks are guards, not quotas or
completion guarantees. Source schema changes require a reviewed lake-format
migration. Read manifests rather than globbing historical files.

See `ETL_PIPELINE.md` for exact migration, Mac test/execution commands, transaction
boundaries, publication semantics, validation, recovery and reader API.
