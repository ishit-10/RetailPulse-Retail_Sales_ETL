# Executed test evidence

Run date: 16 October 2026 (Asia/Kolkata). Execution environment: Linux, Python 3.12.14. This is not the user's Mac.

## Results

| Check | Actual result |
|---|---|
| Latest `python -m pytest -q` | **26 passed, 4 skipped**, 2.67 seconds |
| Full default generation | **100,000 orders**, 2025-01-01 through 2026-06-30 |
| Full file validation | **64 named checks passed** |
| SQL parser checks | All four SQL files parsed: 84 statements |
| PL/pgSQL parser checks | All 10 guard/DO bodies parsed |
| Python compile check | Passed for application, wrappers and tests |
| PostgreSQL initialization/load/constraints | **Not executed here** |
| User's local PostgreSQL connection | **Not established or verified** |
| GitHub Actions execution | Workflow supplied; **not run or published** |
| Mac/Homebrew commands | Documented using official Homebrew guidance; **not executed on macOS** |
| Streamlit/dashboard performance | Outside this source-data stage; **not tested** |

PostgreSQL binaries were absent. Installing PostgreSQL through the environment's package manager failed because OS user/group operations were not permitted. SQL/PLpgSQL syntax parsing does not establish successful schema installation, valid runtime joins, COPY loading, trigger behavior or database performance. Run the supplied integration suite before treating the SQL path as verified.

## Generated volume

| Entity | Rows |
|---|---:|
| Customers | 20,000 |
| Orders | 100,000 |
| Order lines | 208,606 |
| Fulfillments | 103,367 |
| Invoice lines | 203,970 |
| Payment attempts, captures and refunds combined | 119,333 |
| Accepted return items | 15,119 |
| Inventory movements | 498,218 |
| Inventory snapshots | 68,400 |
| Store/day traffic rows | 6,552 |
| Web/App day traffic rows | 1,092 |
| All generated source rows | 4,829,001 |

Generation took 57.6 seconds in this environment, with measured peak RSS approximately 2.25 GiB. The generated Parquet files total 43,673,705 bytes (approximately 41.7 MiB). These are measurements here, not promises for a Mac. Database size and load time are unmeasured. Validation can use more memory because it builds several relational lookups.

## What the Python suite exercised

- Historical tax boundaries and the September 2025 transition.
- Exact paise allocation, invalid discounts and odd-paise CGST/SGST splits.
- Undefined ratios when denominators are zero or negative.
- Same seed/config produces identical Parquet checksums and manifests.
- Corrupted Parquet checksum detection and refusal to overwrite an existing batch.
- Complete golden transaction, failed refund attempts and damaged-return treatment.
- Split fulfillment, partial cancellation, zero-footfall days and FIFO allocation.
- Delivered inventory timing and captured booking-value reconciliation.
- Deliberately corrupted inventory snapshot rejected by verification.

The full 100,000-order dataset independently passed `verify-files`. See `VALIDATION_RESULTS.json` for the 64 check names and exact golden values; `GENERATION_MANIFEST.json` records actual table counts, hashes, seed and package versions. Bulk Parquet files are deliberately excluded from the source ZIP; regenerate locally using the README.

## Remaining validation

The four skipped tests require an explicitly configured, empty `retailpulse_test` PostgreSQL database. They cover load/replay and SQL reconciliation, immutable invoices, overfulfillment rejection, and negative-price rejection. They must pass on PostgreSQL before using this as a verified database implementation. No claim is made that all 20 Stage 1 product acceptance criteria, dashboard filters or P95 performance targets have been satisfied.
