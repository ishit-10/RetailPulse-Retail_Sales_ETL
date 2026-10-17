# Stage 5 — RetailPulse decision workspace

The Streamlit application reads the published Stage 4 warehouse. It never seeds,
extracts, loads, migrates, cleans up or modifies data. There is no deployment in
this stage. Local PostgreSQL remains authoritative; demo mode is explicitly a
small frozen synthetic fixture. No paid service, Docker Desktop or billing is
required.

## Launch on macOS

From the existing checkout after applying the Stage 5 feature patch:

```bash
cd "$HOME/Developer/retailpulse"
source .venv/bin/activate
python -m pip install -e .
python -m pytest -m 'not integration' -q
RETAILPULSE_DASHBOARD_MODE=demo python -m streamlit run streamlit_app.py
```

Open `http://localhost:8501`. Stop with Control-C. Demo is the default if the mode
variable is absent. It makes no database connection and needs no `.env`.

To read your existing complete warehouse:

```bash
cd "$HOME/Developer/retailpulse"
source .venv/bin/activate
python -m retailpulse warehouse-status
python -m retailpulse verify-warehouse
RETAILPULSE_DASHBOARD_MODE=local python -m streamlit run streamlit_app.py
```

The existing `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` configuration
and private `.env` are reused. The host must be loopback, as enforced by the
existing database connector. Do not regenerate or reload your 100,000-order
warehouse to launch this dashboard. Do not put database passwords in commands,
Git, screenshots or Streamlit source. Restart Streamlit after changing database
credentials/configuration because its engine is cached.

Optional read-only role, run as the local PostgreSQL administrator after Stage 4
has published its views:

```bash
psql -h 127.0.0.1 -p 5432 -U "$USER" -d retailpulse \
  -v ON_ERROR_STOP=1 -f scripts/dashboard_readonly.sql
psql -h 127.0.0.1 -p 5432 -U "$USER" -d retailpulse \
  -c '\password retailpulse_dashboard'
```

For a dedicated dashboard terminal, securely prompt for this role's password
without changing the pipeline `.env` (zsh):

```zsh
export PGUSER=retailpulse_dashboard
read -s 'PGPASSWORD?Dashboard database password: '
printf '\n'
export PGPASSWORD
RETAILPULSE_DASHBOARD_MODE=local python -m streamlit run streamlit_app.py
unset PGUSER PGPASSWORD
```

The optional script grants only SELECT on the named public warehouse views and
audit columns, not the operational source or private build schemas. If a role
with this name already exists, review its other grants/memberships; this additive
script does not revoke pre-existing access. Independently of role choice, every
application transaction uses `READ ONLY`, `REPEATABLE READ`, UTC audit timestamps
and a 30-second statement timeout. Business dates were already materialized in
Asia/Kolkata by Stage 4.

## Pages and interaction

| Page | Contents and drilldown |
|---|---|
| Executive overview | Net sales, booked GMV, OPD, booked AOV, profit, margin; equal-length previous-period comparisons; daily/weekly/monthly revenue, channels, categories and booking volumes |
| Store & channel | Revenue/OPD/AOV/margin ranking, state/city labels, online versus physical channels, selected-store trend, footfall conversion and low-revenue investigation list |
| Product & inventory | SKU/category revenue and margin, net units, product trend, latest observed inventory in the selected period, approved sell-through and positive-stock/zero-sales investigation list |
| Customer & orders | Recognized purchasing customers, booking frequency, first-observed versus returning customer segments, active-customer cohorts, derived quantity outcomes, cancellations, matured returns and cash refunds |
| Pipeline health | Actual ETL/warehouse audit history, publication IDs, extraction age, counts, durations, failures and last-publication reconciliation status |

Shared entrypoint widgets retain their selections across navigation. State narrows
cities/stores; city narrows stores; category narrows SKUs. Invalid child choices
reset to All. Selecting WEB/APP clears and disables originating city/store.
Reset filters restores the initial booking window, all dimensions, monthly grain
and the warehouse's return-observation cutoff. Filters are single-select plus All.
The date window is inclusive; the default uses available booking dates, not a
hardcoded 100,000-order calendar. Outcome-tail dates remain selectable.

Region and brand are absent from the approved source and shown disabled. State
is supported; it is not renamed region. Online orders have a state but no
originating store/city. A SKU filter narrows merchandise and counts distinct
orders containing that SKU, not all merchandise in those orders.

Charts are Plotly, tables are downloadable as filtered UTF-8 CSV with exact
Decimal monetary strings and spreadsheet-formula escaping for text. Tables show
up to 1,000 rows; exports include all rows returned by the filtered aggregate
query. Store/product ranking controls sort numeric values before display.
Currency cards use Indian lakh/crore notation; monetary table cells use Indian
grouping. Undefined ratios show an em dash, never a fabricated zero.

## Business semantics and filter applicability

Stage 4 `kpis.build_query()` is the single SQL builder used by both the CLI
`kpis.report()` and the dashboard. Extracting this builder did not change the
existing SQL formulas or CLI result structure. No monetary KPI is recomputed in
Python. Python only formats results, selects display columns and sorts rankings.
New scorecards/customer analyses are parameterized SQL in `dashboard/queries.py`.

- GMV is original booked quantity times price. Booked AOV is initial discounted
  merchandise divided by distinct confirmed orders. Later cancellations/returns
  do not retroactively reduce these booking metrics.
- Net sales are recognized merchandise less issued merchandise credits, excluding
  GST, on recognition/credit dates. Net COGS reverse original FIFO cost only for
  restockable returns. Cash refunds include GST and are displayed separately;
  they are not deducted a second time from net sales.
- OPD includes every selected calendar day. ASP and gross margin follow Stage 4
  denominators; nonpositive denominators yield NULL.
- Comparisons use the immediately preceding equally long calendar window with
  the same filters. Insufficient warehouse date coverage or a nonpositive prior
  denominator suppresses the comparison. Margin differences are percentage
  points. The revenue series retains Stage 4 period-boundary comparison rules.
- Cancellation rate is cancelled units / original ordered units for the selected
  booking cohort, not cancelled orders / orders.
- Return rate uses Stage 4's 30-day matured fulfillment cohort and an explicit
  observation cutoff bounded by warehouse date coverage. Refund transactions
  cannot be allocated to a selected SKU/category, so that section is unavailable
  under product filters. No unapproved generic cash-refund percentage is added.
- Footfall conversion uses distinct completed POS orders and valid entries at the
  same store/date grain. Product and online-channel filters make that panel
  unavailable. A POS filter is redundant for this strictly POS measure.
- Inventory shows the latest **observed** snapshot within the selected range,
  including its date; sparse snapshots are not interpolated to daily stock.
  ATP is sellable minus reserved minus safety stock. Unknown policies remain
  NULL; negative ATP is retained. Inventory has no channel attribution, so channel
  filters disable its panels instead of being silently ignored.
- Sell-through reuses Stage 4's opening stock/purchase/sellable-restock pool and
  single-store inward-transfer rule; company scope excludes internal transfers.
  City/state pools spanning multiple locations are unsupported without paired
  transfers. Select one store or clear those filters.
- Low-revenue stores and positive-stock/zero-sales SKUs are investigation lists,
  not assertions about targets, service levels or a new approved slow-stock KPI.
- Pipeline monitoring is global for the published lake/warehouse and does not
  respond to business-dimension filters; the page explicitly says so.

Customer analysis is an explicitly labelled descriptive extension, not a change
to approved retail KPIs. “Purchasing customers” counts distinct non-null customers
with recognized sales in the selected recognition window. Frequency, segment AOV,
customer trends and cohorts use confirmed booking orders. Guests are excluded
from identified-customer denominators and shown separately where applicable.
First observed purchase is calculated over all warehouse booking history before
applying the selected reporting scope. New-in-period versus returning describes
this first observed date, not a lifetime acquisition claim. Cohorts show active
customer counts by first observed month/activity month, not a fabricated
retention percentage. Historical left-censoring is disclosed. No names, contact
fields or individual customer tables are rendered/exported.

Detailed order-status events were not included in Stage 4. The order panel
therefore labels whole-order **quantity outcomes**, not workflow statuses. Product
filters select matching orders; classification then uses the entire order to
avoid calling a partially fulfilled order complete because one selected SKU was
delivered. No source-schema query or migration is introduced to fill this gap.

## Architecture, consistency and failure handling

- `streamlit_app.py`: small repository-root entrypoint.
- `retailpulse/dashboard/app.py`: navigation, cache, mode and error boundaries.
- `data.py`: read-only adapters, pinned head, bundle validation and page payloads.
- `queries.py`: SQL comparisons, scorecards, customer aggregates and audit reads.
- `filters.py`, `components.py`, `charts.py`, `formatting.py`: shared interface.
- `pages/`: five independently testable renderers.
- `.streamlit/config.toml`: light theme and disabled usage telemetry.

Local metadata reads the completed `etl.warehouse_head` publication. Each page
payload pins all its SQL in one repeatable-read transaction. The current head is
read on every rerun; cached payloads are keyed by mode, warehouse run, dates,
filters, page, grain and cutoff (60-second TTL, 96 entries). A publication change
between metadata and payload raises a reload message. A concurrent publication
after the transaction begins can serve the consistent prior snapshot with its
own lineage, never a mixture. Pipeline audit history may lag by at most the cache
TTL until a rerun. No background scheduler or automatic reload is installed.

SQL parameters are bound; identifiers/grain choices use internal allowlists.
Aggregate/filter work happens in SQL. The local app does not load the complete
operational dataset into memory. Small dimension catalogs support filters; latest
inventory returns bounded-by-snapshot SKU/location rows rather than all history.
Database failures show a sanitized retry message and log only error class/mode;
SQL, connection URLs, raw error text and credentials are not exposed. Empty
results produce explicit no-observation messages. There is no silent demo fallback.

Health reads actual `extract_runs`, `extract_heads`, `warehouse_runs` and
`warehouse_head`. Successful publication means reconciliation passed at load
time, not that the dashboard just reran full verification. `verify-warehouse`
remains the explicit full check. Extraction age is measured from extraction start;
newer lake publication and extraction age over 24 hours produce warnings.

## Compact offline demo

`demo/warehouse/` contains twelve reviewed Parquet files plus a versioned manifest,
about 82 KiB total. They hold the existing generator's complete **500-order,
seed-42, two-style, 50-customer synthetic fixture**, transformed through the actual
Stage 4 SQL. Small fact grains are preserved to support correct distinct-order,
customer, refund and inventory filters; independent disconnected aggregates would
not compose correctly. Customer/contact dimensions and live audit histories are
excluded. The manifest records provenance, row counts, SHA-256 hashes and expected
financial totals. Only these exact files are allowlisted in Git; generated bulk
`data/`, `.env`, backups and Streamlit secrets remain ignored.

Demo uses embedded DuckDB to execute the shared SQL over Arrow tables. The adapter
changes parameter syntax, generate_series aliases and unconstrained NUMERIC casts
for dialect compatibility; it does not implement separate Python KPI formulas.
Money remains decimal. Ratios may have insignificant backend precision differences.
All hashes/counts are validated before queries, external DuckDB access is disabled,
and its memory limit is 256 MiB. Corruption fails closed. Demo dates and totals
are deliberately different from the complete local batch and clearly labelled.
Pipeline health shows fixture provenance/counts, not invented job success history.

For a future demo refresh, use a **complete small synthetic test warehouse** and
`scripts/export_dashboard_demo.py --output <new-directory> --acknowledge-synthetic`
with that database's private environment configuration. It uses read-only SQL,
the bundled column/schema allowlist, a 5,000-order limit and a 100,000-row-per-table
limit, and never truncates or overwrites a bundle. Never point this at the full
production batch; it will reject its size. Synthetic acknowledgement is a human
assertion, not an automatic privacy classifier. Review the resulting bundle and
run parity tests before deliberately replacing tracked demo files.

For later free Streamlit Community Cloud deployment, the repository entrypoint
is `streamlit_app.py`, requirements are pinned, and default demo mode needs no
secrets or database. No application has been deployed by this change. Local mode
is not a public multi-user service: it has no authentication/authorization layer.

## Native integration tests on macOS

These use a new disposable cluster on port 55435, never the production cluster.
Check free disk first; the fixture includes seed/lake/warehouse integration tests.
Do not lower the production loader's disk reserve. If startup fails because the
port is occupied, stop and inspect it instead of connecting to an unknown server.

```bash
cd "$HOME/Developer/retailpulse"
source .venv/bin/activate
(
  set -e
  export PATH="$(brew --prefix postgresql@17)/bin:$PATH"
  DASHBOARD_TEST_PGDATA="$(mktemp -d /tmp/retailpulse-stage5.XXXXXX)"
  printf 'Disposable test directory: %s\n' "$DASHBOARD_TEST_PGDATA"
  initdb -D "$DASHBOARD_TEST_PGDATA" -U retailpulse_test_runner --auth=trust
  pg_ctl -D "$DASHBOARD_TEST_PGDATA" -l "$DASHBOARD_TEST_PGDATA/server.log" \
    -o "-h 127.0.0.1 -p 55435 -k $DASHBOARD_TEST_PGDATA" start
  trap 'pg_ctl -D "$DASHBOARD_TEST_PGDATA" -m fast stop' EXIT
  createdb -h 127.0.0.1 -p 55435 -U retailpulse_test_runner retailpulse_test
  export RETAILPULSE_TEST_DATABASE_URL='postgresql+psycopg://retailpulse_test_runner@127.0.0.1:55435/retailpulse_test'
  python -m pytest -q
)
```

The existing fixture guards require the explicitly configured empty disposable
`retailpulse_test` database. Tests create/drop fixture schemas there. Keep the
printed directory for diagnosis; only remove a positively identified stopped
throwaway cluster later. No test cleanup targets the production data directory.

For manual full-warehouse parity, select All dimensions and 1 April 2025 through
30 September 2026 in local mode, then compare the overview to:

```bash
python -m retailpulse warehouse-kpi sales --start 2025-04-01 --end 2026-09-30
```

Expected values from the owner's prior Stage 4 output, not a new dashboard run:
net sales ₹329,076,096.08; GMV ₹397,978,845.00; 100,000 orders; gross profit
₹165,939,021.53. Check filters/dates and warehouse lineage if they differ.
Also switch pages with a store/SKU filter, reset, inspect an empty date window,
and verify footfall/inventory applicability notices. See
[DASHBOARD_TEST_RESULTS.md](DASHBOARD_TEST_RESULTS.md) for actual execution evidence.
