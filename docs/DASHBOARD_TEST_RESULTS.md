# Stage 5 verification report

Implementation base: merged Stage 4 `7b97067`; feature branch
`feature/streamlit-dashboard`. No push, merge, deployment or Mac database change
was performed by this implementation session.

## Executed in the cloud workspace

- Python 3.12.14, Streamlit 1.55.0, Plotly 6.6.0, DuckDB 1.5.0.
- `python -m pytest -q`: **125 passed, 31 skipped in 7.67 seconds**.
  All 31 skips require an explicitly configured native PostgreSQL test database.
  This is not a 156-test native pass claim.
- Streamlit AppTest rendered all five page functions, with no exceptions/errors;
  additional tests exercised dependent/reset filters and sanitized connection
  failures. Navigation is supplied by `st.Page`/`st.navigation`; AppTest invokes
  each renderer through the shared application entrypoint because it does not
  simulate browser navigation between these pages.
- Started the actual Streamlit server; `/_stcore/health` returned **HTTP 200, ok**.
- Supplemental PGlite 0.5.10 SQL execution built the existing 500-order fixture
  through Stage 4 SQL and passed 30 source business checks. Twenty new dashboard
  query executions (ten kinds, unfiltered and SKU-filtered) succeeded: comparison,
  order trend, store/channel scorecards, customer summary/segments/trend/cohorts,
  quantity outcomes and latest inventory. This is PostgreSQL-compatible embedded
  SQL evidence, not native psycopg permissions/concurrency/durability verification.
- Demo fixture hashes/counts and all five payloads passed tests. Full fixture
  totals: 500 orders, GMV ₹2,153,636.00, net sales ₹1,769,721.56, net COGS
  ₹864,926.33, gross profit ₹904,795.23.
- Filtered contributions, date boundaries, empty scopes, unsupported denominator
  filters, null inventory policies, customer cohorts, Indian currency rounding,
  percent formatting, CSV escaping, bound SQL, corruption and changed-head
  rejection are covered by the executed unit tests.

One initial assertion incorrectly assumed every inventory location had a safety
policy. The fixture includes transit stock with no policy; the assertion was
corrected to require NULL availability in that case. Business data/formulas were
not changed to satisfy that test.

## Not verified here

Native PostgreSQL is unavailable in this workspace. Three added native tests
are included but skipped here: local-versus-Stage-4 KPI parity/all page payloads
and read-only transactions; native-versus-demo monetary/customer/inventory parity;
and small synthetic export round-trip/safety. Existing native tests were also
skipped. Run the isolated macOS commands in DASHBOARD.md before considering the
new database adapter verified against PostgreSQL 17.

The user's prior Stage 4 **117-test pass** and 100,000-order warehouse results
remain historical user-supplied evidence, not execution results for this dashboard.
No full-size dashboard latency/memory benchmark was performed or invented.

A Playwright Chromium installation was attempted for actual screenshots, but the
browser download repeatedly failed with a truncated/invalid archive. No rendered
browser screenshot or mobile/navigation visual verification is claimed. Streamlit
AppTest and HTTP startup checks passed; manual browser QA remains necessary.

## Release review

Only twelve small synthetic Parquet fixtures are deliberately Git-allowlisted.
Production `data/`, generated bulk Parquet, `.env`, `.streamlit/secrets.toml`,
backups, logs and virtual environments remain excluded. The optional reader-role
script contains no password. No schema migration is needed for the dashboard.
The Stage 4 query-builder refactor preserves its SQL and public CLI interface.

## Owner verification and cloud startup follow-up — 17 October 2026

The owner reported **156 passed in 162.68 seconds** on the isolated macOS
PostgreSQL cluster; it stopped successfully afterward. The supplied local-mode
Executive Overview screenshot showed the expected full-batch KPI values at
displayed precision. Stage 5 was committed as 562ed27 and merged through PR #3.
These are user-supplied Mac results, not cloud execution.

Community Cloud subsequently failed importing dotenv despite the correct root
requirements file. Its supplied log identifies Python 3.14.8 and confirms uv
selected requirements.txt. The exact missing-package cause is not established.
The `cloud/` entrypoint/dependency bundle makes the intended installation location
explicit, forces the public synthetic demo, and diagnoses wrong runtime/missing
imports before importing the database module. Fresh Python 3.12 cloud-only
installation and all five page checks passed. With test-only dependencies added,
the workspace suite passed **128 tests, 31 skipped in 11.20 seconds**. The three
additional tests cover cloud startup/forced demo, missing imports and runtime
version rejection. Native database tests were not repeated in this workspace.
See [cloud deployment instructions](../cloud/README.md). No live deployment
success is claimed until the public URL is checked.

### Public deployment check and filter reset correction

The public demo at https://retailpulsee.streamlit.app/ was inspected in a
browser. All five pages loaded. Website-channel filtering persisted across
pages and disabled unsupported city/store filters. A reset defect was observed:
totals briefly reset while the browser retained Website, which became active
again on navigation.

The reset callback now assigns default widget values explicitly instead of
deleting session-state keys. Added an AppTest covering channel/category/date,
trend and observation-cutoff reset followed by page changes.

Validation of this correction in a fresh Python 3.12 environment:
- Non-integration suite: **129 passed, 31 deselected in 10.06 seconds**.
- `git diff --check`: passed.
- PostgreSQL integration tests were not run for this UI-only change.
- The correction has not been pushed or deployed. A post-deployment browser
  check of Reset filters remains required.
