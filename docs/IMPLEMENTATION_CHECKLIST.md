# Implementation checklist

## Included in this package

- [x] Retain Stage 1 normalized source entities and business-event separation.
- [x] Replace AWS/S3 dependencies with local PostgreSQL and Parquet.
- [x] Supply explicit macOS Homebrew commands without Docker Desktop.
- [x] Include all requested source entities and synthetic generation.
- [x] Exact 100,000-order default covering 18 months.
- [x] Decimal discounts/GST, immutable invoices/credits and FIFO cost lineage.
- [x] Separate failed/successful refunds and original capture references.
- [x] Generation manifest, checksums, reproducible seeds and bulk-data exclusions.
- [x] Transactional init/seed, idempotent same-batch replay and guarded reset.
- [x] Python reconciliation and pytest suite.
- [x] Opt-in PostgreSQL tests and public-repository standard-runner CI.
- [x] Test evidence that distinguishes executed tests from skipped ones.

## Complete on your Mac

- [ ] Install/start Homebrew PostgreSQL and create the app role/database.
- [ ] Create `.env` and run `python -m retailpulse doctor` successfully.
- [ ] Run `init`, generate the full dataset, then `verify-files`.
- [ ] Run `seed` and `verify-db` successfully.
- [ ] Run the explicit integration suite against an empty `retailpulse_test`.
- [ ] Inspect the golden transaction and save a local backup.
- [ ] Inspect staged files before committing; do not commit bulk data or secrets.

## Later stages; not claimed complete

- [ ] Confirm alignment against the separately approved architecture document.
- [ ] Implement reliable incremental extraction and durable checkpoints.
- [ ] Build full analytics marts and all Stage 1 KPI/date/filter combinations.
- [ ] Add Streamlit dashboard and a reviewed synthetic public demo export.
- [ ] Measure dashboard P95 performance at full volume.
- [ ] Deploy only if requested; local operation must remain independent of hosting.
