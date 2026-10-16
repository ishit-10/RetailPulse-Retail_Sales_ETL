# Resumable loader review and execution evidence

Date: 2026-10-16. Base: `main` at `68a3cf3`. Local feature branch:
`feature/resumable-seed-loader`. No GitHub push/merge or Mac database operation
was performed. The earlier CI pass is not evidence for this new implementation.

## Executed here

| Check | Actual result |
| --- | --- |
| Python 3.12, repository-pinned dependencies | Installed in an isolated temporary virtual environment |
| `python -m pytest -q` | **43 passed, 13 skipped** |
| Native PostgreSQL integration tests | All 13 skipped: no test connection configured; native server startup unavailable because this environment cannot switch to a non-root OS user |
| `python -m compileall -q retailpulse tests` | Passed |
| Editable installation and `retailpulse seed --help` | Passed; console entry point and new options work |
| Embedded PostgreSQL SQL smoke test | Passed using PGlite 0.5.10 / PostgreSQL 18.6 WASM, not native PostgreSQL 17 |
| SQL smoke fixture | Separately generated 500 synthetic orders, 2 styles, 50 customers; generator-default dates; no user data |
| Chunked SQL COPY smoke | All 36 data tables loaded in dependency order, using 1,000-row transactions with guards/FKs active |
| Migration SQL | Applied twice successfully in the embedded engine |
| Final SQL reconciliation | All **30** named checks returned zero failures |
| Immutable financial guards | Invoice update and inventory delete rejected |
| Snapshot verification equivalence | Original and optimized queries both identified exactly one deliberately corrupted snapshot |
| Failed SQL transaction | Duplicate-key failure rolled back the whole test transaction |
| Diff whitespace and excluded-file audit | Passed; no `.env`, generated bulk files or database backups in the patch |

The embedded smoke test used temporary Node/PGlite tooling outside the repository.
It validates SQL, COPY dependency order, immediate guards and the rewritten queries.
It **does not validate** the Python/psycopg loader end-to-end, multi-session advisory
locks, native WAL/disk-full recovery, operating-system crash recovery or PostgreSQL
17 compatibility. PGlite/pgserver/Node were not added to project dependencies.

The 43 passing Python tests include 26 pre-existing unit tests plus 17 loader tests
for checksum identity, altered files/manifests/counts, table allowlisting, chunk
bounds, disk reserve/expansion, server path discovery/fallback, mismatched paths,
archiving/slot rejection and durability settings. The 13 opt-in database tests are
implemented but still require execution on the isolated native cluster described
in the README. They cover load/replay, interruption and resume, disconnect,
checkpoint drift, unmanaged partial rows, rollback, trigger/FK enforcement,
reconciliation status, disk rejection, read-only preflight, exclusive loading,
wrong dataset identity and abrupt process exit.

## Files changed

| File | Purpose |
| --- | --- |
| `retailpulse/loader.py` | Streaming manifests/COPY, disk admission, session lock, checkpoints, resume and reconciliation |
| `retailpulse/db.py` | Delegate seed/verify to loader; explicit reset clears matching metadata |
| `retailpulse/__main__.py` | `migrate-loader`, `seed-status`, chunk size, data-directory and preflight options |
| `pyproject.toml` | `retailpulse` console command |
| `sql/005_seed_checkpoints.sql` | Additive versioned ETL run/table checkpoints |
| `sql/006_seed_verify.sql` | Additional final business checks formerly covered by in-memory validation |
| `sql/004_verify.sql` | Disk-efficient snapshot reconciliation; reject missing delivery timestamps on returns |
| `tests/test_loader.py` | Unit coverage for identity and disk policy |
| `tests/test_postgres.py` | Guarded disposable-database integration suite |
| `.github/workflows/tests.yml` | PostgreSQL 17 test service; test timeout allowance |
| `README.md` | Existing-Mac migration/preflight/resume and isolated test-cluster commands |
| `docs/RESUMABLE_LOADER.md` | Transaction, trigger, identity, disk and recovery decisions |
| `docs/LOADER_TEST_RESULTS.md` | This evidence and limitations |

`sql/001_schema.sql`, `sql/002_guards.sql`, the generator and dependency pins remain
unchanged. No integrity trigger, foreign key, source index or source table was
removed or weakened. No Stage 3 pipeline was added.

## Remaining native verification

1. Review and apply the patch on the feature branch, then install with
   `python -m pip install -e .`.
2. Run `python -m pytest -m 'not integration' -q`.
3. Use the README's new temporary PostgreSQL 17 cluster on port 55432 and run
   `python -m pytest -q`. Do not point tests at the existing populated test database.
4. Against the real application connection, run `doctor`, `migrate-loader`, and
   `seed --data data/raw/batch_001 --pg-data-dir VERIFIED_PATH --preflight-only`.
5. Free the reported required space. Run `seed`, inspect `seed-status`, then run
   `verify-db`. Verify the manifest's expected 100,000 orders and `LOADED` status.

The full 100,000-order Mac ingestion, native disk probe and actual disk-full/server
restart behavior remain unverified. No claim of production-readiness or successful
full-data ingestion is made before those checks pass.

## Native Mac follow-up (user-provided output, 2026-10-16)

The user ran the full suite against the isolated native test cluster: **55 passed,
1 failed in 21.90 seconds**. `test_lost_connection_resume` expected the injected
Python `ConnectionError`, but transaction cleanup tried to roll back the closed
psycopg connection and correctly surfaced SQLAlchemy `OperationalError` instead.
The test therefore stopped before its checkpoint/resume assertions.

The follow-up corrects the expected exception and asserts connection invalidation,
1,000 committed rows/checkpoint, intermediate `loading`/`GENERATED` states, and
`LOADED` after verified resume. The corrected native test still needs a rerun on
the Mac. Local verification of this follow-up: **43 passed, 13 skipped**. Loader
implementation and production database contents are unchanged by this correction.

## Opt-in low-disk follow-up (2026-10-16)

User measurements: production database 170 MB, pg_wal 256 MB, max_wal_size 1GB,
min_wal_size 80MB, wal_keep_size 0, archive_mode off; recent free space about 4.8 GiB.
These support evaluating a smaller reserve but do not prove peak full-load usage.
The new explicit `--low-disk` mode is documented in RESUMABLE_LOADER.md and keeps
all integrity/durability checks, with additional WAL admission rules and smaller
chunks. Default-mode disk policy remains unchanged. No server settings were altered.

Executed after the change: **50 unit tests passed, 14 native integration tests
skipped**; CLI help and diff whitespace checks passed. Seven added unit cases cover
the unchanged default, supported/unsupported WAL settings, estimated low-disk
admission, falling-space rejection and the final 3 GiB reserve. One new native
integration test covers low-disk preflight, effective chunk size and completed load;
it has not yet run here. The full native suite should now collect **64 tests**.
No new source schema or ETL migration is required. No full import is claimed.
