# Stage 4 verification report

Base: merged Stage 3 `b9b4e47`. Work branch: `feature/analytics-layer`.
This report separates cloud checks executed by the implementation agent from
Mac results supplied by the project owner on 17 October 2026. The Mac native
suite and full-data acceptance checks passed. No Stage 4 push, merge or new
CI execution is claimed.

## Executed Python checks

Environment: isolated Python 3.12.14 virtual environment, project-pinned
requirements. No new production dependency was added.

```text
python -m pytest -q
89 passed, 28 skipped
```

The skips are integration tests requiring
`RETAILPULSE_TEST_DATABASE_URL`: 22 existing Stage 2/3 integration tests and six
new warehouse integration tests. They are **not passes**. The 89 passing tests
include the 65 existing nonintegration tests and 24 new warehouse tests.

Also executed successfully:

- `python -m compileall -q retailpulse`
- `python -m retailpulse warehouse-load --help`
- `git diff --check`

New executed unit coverage: freshness/lineage admission, invalid age policies, deterministic manifest fingerprints,
unsupported/inapplicable filter rejection, bound parameters, inclusive calendar
ranges, typed IDs and strings, mandatory return-cohort cutoff, and disk
preflight rejection. Native COPY/DDL transactions are not mocked and claimed
as tested by these unit tests.

## Supplemental executed SQL checks

Because native PostgreSQL binaries/server are unavailable in the cloud
workspace, an isolated **PGlite 0.5.10** embedded SQL smoke test was run outside
the repository. This is supplementary PostgreSQL SQL execution; it is not a
replacement for PostgreSQL 17/psycopg integration, durability, WAL, filesystem,
concurrent-reader, or process-restart tests. PGlite is not a project dependency.

The smoke procedure generated a separate 500-order fixture using the existing
generator (`styles=2`, `customers=50`), created the source definitions, copied
fixture rows into private replicas, and executed the warehouse SQL model.
Actual checks/results:

- All **70 source foreign keys** added to private replicas and validated.
- All **30 existing business checks** in `004_verify.sql` and
  `006_seed_verify.sql` passed against those replicas.
- Warehouse dimension/fact tables, primary/foreign keys, indexes and views
  were created successfully.
- All **13 report types** executed, including the matured return cohort query.
- Booking, recognized-sales and return-credit aggregates reconciled with the
  existing reference SQL queries.
- Golden transaction: net sales **2400.00**, net COGS **1200.00**; corresponding
  gross profit **1200.00**, margin **50%**.
- Partial daily, weekly and monthly report totals reconciled to the exact
  selected date interval.
- Zero-order report returned OPD=0, AOV=NULL and gross margin=NULL.
- CREATE OR REPLACE VIEW inside a rolled-back publication transaction restored
  the prior reader binding.

For this generated fixture (not the user's 100,000-order dataset), the sales
report returned booked GMV 2,153,636.00, net sales 1,769,721.56, net COGS
864,926.33, gross profit 904,795.23 and 500 confirmed orders. These are smoke
fixture reconciliation outputs, not business benchmarks or performance claims.

## Native acceptance tests — passed on the Mac

`tests/test_warehouse_postgres.py` reuses the guarded disposable 500-order
fixture from Stage 2 and refuses any database name other than
`retailpulse_test`. Tests cover:

1. Full load, identical rerun, key/count checks, golden transaction, approved
   source reconciliation, all report types, date boundaries and zero orders.
2. Corrupt, incomplete or stale snapshots cannot replace a valid publication.
3. Keyboard interruption after committed private COPY, cleanup and successful
   retry, with the previous publication retained.
4. Publication DDL rollback preserves the previous head and reader data.
5. Zero footfall, missing inventory/policy, and negative stock adjustments.
6. Lost COPY connection leaves the prior publication usable and can be retried.

The native tests override disk probing only for small disposable fixtures.
Production disk thresholds remain enabled. None of these tests resets or
modifies the Mac's production `retailpulse` database.

## Mac acceptance evidence — 17 October 2026

The project owner supplied these terminal results; the implementation agent did
not remotely connect to or modify the Mac database.

- Nonintegration suite: **89 passed, 28 deselected in 4.18s**.
- Full native suite: **117 passed in 138.48s (2:18)**, with no skips reported.
  The isolated test server on port 55434 stopped successfully afterward.
- Production metadata migration: warehouse metadata v1 applied; source and
  lake unchanged.
- Disk preflight initially refused insufficient space. After local cleanup it
  passed with 5,625,987,072 bytes free versus 4,874,193,376 bytes required.
- Full warehouse run **cf998e61-0a9f-4ec6-872b-69a7d7f21b89** completed from
  published snapshot **84e86961-d876-41eb-b4e9-f163ed1bf8ef**.
- `verify-warehouse`: **passed**. `verify-db`: **passed**, including 100,000
  source orders. The source database remained valid after warehouse loading.
- Identical warehouse rerun returned **unchanged**, retaining the same run
  and snapshot IDs.
- Final reported filesystem availability: approximately **5.3 GiB**. This is
  a point-in-time observation, not a guarantee for future refreshes.

### Published warehouse row counts

| Object | Rows |
|---|---:|
| dim_date | 579 |
| dim_product | 600 |
| dim_store | 15 |
| dim_customer | 25,000 |
| dim_channel | 3 |
| dim_order | 100,000 |
| dim_state | 5 |
| fact_order_items | 209,999 |
| fact_sales | 204,865 |
| fact_returns | 15,347 |
| fact_refunds | 19,641 |
| fact_inventory_daily | 170,999 |
| fact_inventory_movements | 513,964 |
| fact_footfall_daily | 6,576 |

Refund rows include different transaction statuses; 19,641 is not the count of
successful refunds. The movement fact aggregates the 539,147 source movement
rows by date/SKU/location/type/stock state, so its lower row count is expected.

### Executed sales report

Inclusive period: **2025-04-01 through 2026-09-30**, 548 calendar days. This is
synthetic project data, not real business evidence. Booking measures use booking
dates; sales and credits use their separate recognition/issue dates. These
period totals do not imply that later outcomes are included.

| Measure | Observed value |
|---|---:|
| Confirmed orders | 100,000 |
| Original ordered units | 210,250 |
| Cancelled units | 5,385 |
| GMV, INR | 397,978,845.00 |
| Discounts, INR | 32,388,598.80 |
| Booked merchandise, INR | 365,590,246.20 |
| Gross sales, INR | 355,419,306.88 |
| Merchandise credits, INR | 26,343,210.80 |
| Net sales, INR | 329,076,096.08 |
| Net COGS, INR | 163,137,074.55 |
| Gross profit, INR | 165,939,021.53 |
| Net tax, INR | 32,062,311.46 |
| Net units | 189,337 |
| OPD, rounded | 182.48 |
| Booked AOV, INR, rounded | 3,655.90 |
| Gross margin, rounded | 50.43% |
| ASP, INR, rounded | 1,738.04 |

The reported net sales equal gross sales minus merchandise credits; gross
profit equals net sales minus net COGS. Refund payments are not deducted again.

## Remaining scope

Local acceptance is complete for the supplied full snapshot and tested recovery
scenarios. No hardware power-loss experiment, independent production restart
experiment, concurrency benchmark, or general performance guarantee is claimed.
Review the code/documentation and staged paths before committing. Push and merge
require the project owner's confirmation. Future refreshes must still pass
snapshot freshness, validation and disk preflight.
