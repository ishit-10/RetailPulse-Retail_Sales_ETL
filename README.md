# RetailPulse — Retail Data Engineering & Analytics Platform

A reproducible Python/PostgreSQL project for **Vastra Collective**, a fictional Indian omnichannel apparel retailer. No real customers, GST registrations, sales evidence, or market benchmarks are used. Faker names are explicitly marked synthetic; no contact details are generated.

**Default:** 100,000 orders confirmed from **1 January 2025 through 30 June 2026**, 20,000 fictional customers, 12 stores, 2 fulfillment centers, 1 logical transit location, 40 styles and 240 SKUs. Returns and deliveries settle through 31 July 2026. All merchandise money uses `Decimal` and PostgreSQL `NUMERIC`; dates report in Asia/Kolkata.

**No payment, credit card, billing account, Docker Desktop, AWS or Supabase is needed.** PostgreSQL, generation, validation and Parquet files run locally. GitHub Actions is optional. Stage 5 adds a local Streamlit dashboard and a compact offline demo; no application is deployed.

## Stage 5 — Streamlit retail intelligence

Five pages cover executive KPIs, stores/channels, products/inventory,
customers/orders and pipeline health. Shared dependent filters persist across
pages. Local mode reads the existing published analytics warehouse in read-only
transactions; default demo mode reads a compact, consistent 500-order synthetic
fixture with no database or credentials. Region, brand and detailed workflow
statuses are explicitly unavailable where the source does not support them.

```bash
cd "$HOME/Developer/retailpulse"
source .venv/bin/activate
python -m pip install -e .
python -m pytest -m 'not integration' -q
RETAILPULSE_DASHBOARD_MODE=demo python -m streamlit run streamlit_app.py
# Stop with Control-C, then use your existing private PostgreSQL configuration:
RETAILPULSE_DASHBOARD_MODE=local python -m streamlit run streamlit_app.py
```

Open `http://localhost:8501`. No new database migration, seed or ETL run is needed
if Stage 4 is already published. Do not rerun bulk ingestion just to launch the UI.
See [DASHBOARD.md](docs/DASHBOARD.md) for filter semantics, approved KPI reuse,
optional read-only role, native macOS tests and demo differences.
[Dashboard test results](docs/DASHBOARD_TEST_RESULTS.md): **125 passed, 31 skipped**
in the original implementation workspace. The owner subsequently reported
**156 tests passed** on macOS and reviewed the local dashboard; Stage 5 was merged
through PR #3. Public deployment verification is tracked separately.

## Stage 4 analytics warehouse

Stage 2 and Stage 3 are merged and locally verified. Stage 4 is implemented and locally verified on
the merged Stage 4 implementation: 117 native-suite tests passed, the 100,000-order
warehouse loaded and verified, and an identical rerun returned `unchanged`.
These Mac results were supplied by the project owner on 17 October 2026.
Stage 4 was merged through PR #2 (`7b97067`). It consumes the latest published Parquet snapshot,
builds dimensions/facts privately, reconciles the approved retail calculations,
and atomically publishes warehouse reader views in `analytics`. Existing
source-backed analytics views, seed loader, source records and lake remain intact.

Read [the warehouse guide](docs/ANALYTICS_WAREHOUSE.md) for grains, KPI rules,
unsupported dimensions, disk safety, recovery, and the isolated macOS test
commands. [Actual validation results](docs/WAREHOUSE_TEST_RESULTS.md) distinguish
executed checks from tests that require your local PostgreSQL instance.

After native tests pass and a fresh lake snapshot is available:

```bash
python -m retailpulse migrate-warehouse
python -m retailpulse warehouse-load --lake-root "$HOME/Developer/retailpulse/data" --pg-data-dir "/opt/homebrew/var/postgresql@17" --preflight-only
# Continue only if preflight passes:
caffeinate -i python -m retailpulse warehouse-load --lake-root "$HOME/Developer/retailpulse/data" --pg-data-dir "/opt/homebrew/var/postgresql@17"
python -m retailpulse verify-warehouse
python -m retailpulse warehouse-kpi sales --start 2025-04-01 --end 2026-09-30
```

The warehouse needs additional PostgreSQL storage beyond the small Parquet lake.
Its 3 GiB reserve plus build estimate must pass; do not bypass it on a nearly
full Mac. The dashboard does not require another warehouse load or migration.

## Continue the existing Stage 2 project (PostgreSQL 17)

Use these commands for the existing `~/Developer/retailpulse` checkout and
`data/raw/batch_001`. **Do not regenerate that dataset or reset either existing
database.** The original setup instructions below are for new installations.
The current batch spans April 2025–September 2026; the manifest governs its dates.

After applying the reviewed patch on `feature/resumable-seed-loader`:

```bash
cd "$HOME/Developer/retailpulse"
source .venv/bin/activate
python -m pip install -e .
python -m pytest -m 'not integration' -q
python -m retailpulse doctor
python -m retailpulse migrate-loader
python -m retailpulse seed-status
```

The migration adds ETL checkpoint tables; it does not reload or change source
records. Preserve `backups/retailpulse_before_loader_change.dump` locally.
The console command `retailpulse` is equivalent to `python -m retailpulse` after
editable installation. `init` alone does not apply the new loader migration.

Check the **connected server's** data directory as your existing Homebrew
administrator (the macOS login that owns the cluster), then use that value:

```bash
export PATH="$(brew --prefix postgresql@17)/bin:$PATH"
psql -h 127.0.0.1 -p 5432 -U "$USER" -d postgres -c 'SHOW data_directory;'
psql -h 127.0.0.1 -p 5432 -U "$USER" -d postgres -c 'SHOW max_wal_size;' -c 'SHOW wal_keep_size;'
```

For a standard Apple Silicon Homebrew installation the directory is often
`/opt/homebrew/var/postgresql@17`; **use the verified output**, not that assumption.
The app role may not be allowed to read `data_directory`, so the explicit option
below is supported. If it can read the setting, the two paths must agree.

```bash
# Replace this placeholder with the verified directory from SHOW data_directory.
export RETAILPULSE_PGDATA='/verified/path/to/postgresql@17'
python -m retailpulse seed --data "$HOME/Developer/retailpulse/data/raw/batch_001" \
  --pg-data-dir "$RETAILPULSE_PGDATA" --preflight-only
```

Preflight hashes files, verifies existing checkpoints and measures free space on
PostgreSQL's data/WAL/tablespace volumes without inserting any source rows. The
last reported **8.3 GiB is below the 10 GiB reserve**, even before estimated
remaining data/index/WAL growth. Free space first; the command prints the actual
required amount. Do not delete PostgreSQL WAL or backups to bypass this guard.

Once preflight passes and the isolated tests below pass:

```bash
python -m retailpulse seed --data "$HOME/Developer/retailpulse/data/raw/batch_001" \
  --pg-data-dir "$RETAILPULSE_PGDATA" --chunk-rows 5000
python -m retailpulse seed-status
python -m retailpulse verify-db
psql -h 127.0.0.1 -U retailpulse_app -d retailpulse -W \
  -c 'SELECT batch_id,status,loaded_at FROM source.ingestion_batches;' \
  -c 'SELECT batch_id,state,error_class FROM etl.seed_runs;' \
  -c 'SELECT count(*) FROM source.sales_orders;'
```

After interruption, recover service/free space as needed and rerun the **same
seed command**. It resumes committed chunks. Changing `--chunk-rows` is supported.
Only successful final reconciliation sets `LOADED`/`completed`. Expect 100,000
orders if that is the manifest count; this repository change does not itself
load your Mac. See [transaction and recovery design](docs/RESUMABLE_LOADER.md) and
[current test evidence](docs/LOADER_TEST_RESULTS.md).

## Optional guarded low-disk mode

The default 10 GiB reserve is deliberately conservative. After reviewing server
settings, `--low-disk` offers a 3 GiB reserve plus the same remaining-data allowance,
with chunks capped at 1,000 rows. It rejects WAL configurations outside its supported
limits and retains every integrity check. It does not guarantee that the full import
will fit; other apps, swap, WAL and temporary files may consume additional space.
See the [low-disk design and limitations](docs/RESUMABLE_LOADER.md).

For the verified Homebrew path in this project, first run **preflight only**:

```bash
python -m retailpulse seed --data data/raw/batch_001 \
  --pg-data-dir /opt/homebrew/var/postgresql@17 --low-disk --preflight-only
```

Run the isolated native integration tests described below before importing with
the new mode. If the tests and preflight pass, the corresponding import is:

```bash
python -m retailpulse seed --data data/raw/batch_001 \
  --pg-data-dir /opt/homebrew/var/postgresql@17 --low-disk
python -m retailpulse seed-status
python -m retailpulse verify-db
```

Do not edit the reserve constant or disable disk checks. A space refusal is resumable;
use the same dataset after adequate space becomes available. This option changes no
server settings, operational data definitions, or data volume.

## What is included

- Normalized SQL source schema preserving all 31 Stage 1 logical tables, plus explicit FIFO cost allocation, refund history, safety-stock, snapshot and rejected-demand tables.
- Reproducible Faker/NumPy generation, Pandas/Parquet files and SHA-256 manifest.
- SQLAlchemy connection management and psycopg `COPY` loading.
- Initialization, generation, seeding, guarded reset, file verification and database verification commands.
- Financial constraints, append-only financial snapshots and reconciliation SQL.
- Small analytics views for validating bookings, recognized sales, credits, refunds and store conversion.
- Python tests and opt-in PostgreSQL integration tests; public-repository CI configuration.
- [Model decisions](docs/MODEL_DECISIONS.md), [testing evidence](docs/TEST_RESULTS.md), and [implementation checklist](docs/IMPLEMENTATION_CHECKLIST.md).

The complete Stage 1 text was retrieved as `Pasted text.txt`. A separately approved zero-cost architecture document was not available to this build. The implementation follows the local PostgreSQL/Python/Parquet requirements in the request. The larger 24-month Stage 1 volume is explicitly replaced by this request's 100,000 orders over 18 months.

## Exact macOS setup

Run commands in **Terminal** or the **VS Code integrated terminal**. Homebrew is the only system package manager used. If `brew --version` fails, install Homebrew using the instructions at <https://brew.sh>, then reopen Terminal. Do not reinstall an existing PostgreSQL service or overwrite its data directory.

### 1. Unzip and open the project

Download `RetailPulse.zip` and run:

```bash
mkdir -p "$HOME/Projects"
unzip "$HOME/Downloads/RetailPulse.zip" -d "$HOME/Projects"
cd "$HOME/Projects/retailpulse"
```

If that folder already exists, choose a new destination. In VS Code, use **File → Open Folder** and select it.

### 2. Install Python and PostgreSQL

```bash
brew install python@3.12 postgresql@17
export PATH="$(brew --prefix postgresql@17)/bin:$(brew --prefix python@3.12)/bin:$PATH"
brew services start postgresql@17
pg_isready -h 127.0.0.1 -p 5432
psql -d postgres -c 'SELECT version(), current_user;'
```

`postgresql@17` is keg-only, so the PATH command matters on both Apple Silicon and Intel Macs. Add that same `export PATH=...` line to `~/.zshrc` if you want it in future terminals. `pg_isready` only checks service readiness; it does **not** prove application credentials work.

If port 5432 is already occupied, inspect `brew services list` and your existing database service. Do not delete `postmaster.pid` or existing database directories to force this setup.

### 3. Create a dedicated non-superuser and database (once)

```bash
createuser --pwprompt --no-superuser --no-createdb --no-createrole retailpulse_app
createdb --owner=retailpulse_app retailpulse
```

Choose a local password when prompted. If the role/database already exists, inspect it with `psql -d postgres -c '\du'` or `psql -d postgres -c '\l'`; do not rerun creation blindly. Homebrew normally creates an administrator matching your macOS account, which these commands use.

### 4. Create the Python environment

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
chmod 600 .env
open -e .env
```

Replace `PGPASSWORD=replace_with_your_local_password` with the password you selected. Keep `PGHOST=127.0.0.1`. Environment fields avoid URL-escaping issues. For passwords containing spaces or `#`, quote the value in `.env`. Never commit `.env`.

### 5. Verify the connection and initialize

```bash
python -m retailpulse doctor
python -m retailpulse init
```

`doctor` must display the actual database, username and server version before you regard the database as connected. This project has **not connected to your Mac**. `init` is transactional and repeatable; it refuses to overwrite unknown schemas.

### 6. Generate and validate 100,000 orders

```bash
python -m retailpulse generate --orders 100000 --seed 42 --out data/raw/seed-42
python -m retailpulse verify-files --data data/raw/seed-42
```

These commands do not need a running PostgreSQL server. Generation prints a manifest after completion. It refuses to overwrite an existing output directory; choose another directory for another run. Financial records are stored in compressed local Parquet files, with one file per nonempty table.

For a faster first run:

```bash
python -m retailpulse generate --orders 1000 --styles 2 --customers 100 --out data/smoke
python -m retailpulse verify-files --data data/smoke
```

The generator holds records in memory. Close memory-heavy apps for the default run; reserve several GB of free RAM. Loading requires the measured disk allowance described above, in addition to space for generation and backups. Actual measured generation figures are in the test report. The smoke profile is suitable for initial checks on constrained machines.

### 7. Load and verify

```bash
python -m retailpulse migrate-loader
python -m retailpulse seed --data data/raw/seed-42 --preflight-only
python -m retailpulse seed --data data/raw/seed-42
python -m retailpulse verify-db
python -m pytest -q
```

The loader hashes input files, loads in foreign-key order using bounded COPY transactions and durable checkpoints, and runs final SQL reconciliation. A failure rolls back only its current chunk. Earlier chunks remain resumable; partial data is not certified. Repeating the same completed manifest inserts no duplicates. A different dataset is refused. This is Stage 2 source loading, not Stage 3 incremental ETL. If disk discovery needs an explicit path, use the verified `--pg-data-dir` option above.

Verification requires all named checks to pass. `pytest` skips database integration tests unless you explicitly configure the separate test database below.

### 8. Inspect the golden transaction

```bash
psql -h 127.0.0.1 -U retailpulse_app -d retailpulse -W \
  -c "SELECT * FROM analytics.order_financials WHERE external_reference='SYNTH-GOLDEN-001';"
```

Expected: GMV ₹6,000; discount ₹1,200; booked merchandise ₹4,800; net sales ₹2,400; net COGS ₹1,200; gross profit ₹1,200; gross margin 0.50; successful refund ₹2,520; net units 1. Original invoice GST totals ₹240. Two unit-level invoice lines reference one two-unit order line.

## Reset (explicitly destructive; not a recovery step)

`python -m retailpulse reset --confirm-database NAME` clears synthetic source rows
and associated loader checkpoints only when NAME matches `retailpulse` or
`retailpulse_test`. It uses the same advisory lock and retains schema/files.
Do not use it on your current production dataset or existing 500-order test data
as part of this change. Recovery normally means rerunning `seed`.

## Run PostgreSQL integration tests on your Mac

Your existing `retailpulse_test` contains 500 orders. Preserve it. Run the tests in
an isolated temporary PostgreSQL 17 cluster on **port 55432**, with a new database
also named `retailpulse_test`. No Docker is needed. These commands create synthetic
test data only and use loopback trust authentication for this disposable cluster.
Do not change your production cluster's authentication.

```bash
cd "$HOME/Developer/retailpulse"
source .venv/bin/activate
export PATH="$(brew --prefix postgresql@17)/bin:$PATH"
export RETAILPULSE_TEST_PGDATA="$(mktemp -d /tmp/retailpulse-pg17.XXXXXX)"
initdb -D "$RETAILPULSE_TEST_PGDATA" -U retailpulse_test_runner --auth=trust
pg_ctl -D "$RETAILPULSE_TEST_PGDATA" -l "$RETAILPULSE_TEST_PGDATA/server.log" \
  -o "-h 127.0.0.1 -p 55432 -k $RETAILPULSE_TEST_PGDATA" start
createdb -h 127.0.0.1 -p 55432 -U retailpulse_test_runner retailpulse_test
export RETAILPULSE_TEST_DATABASE_URL='postgresql+psycopg://retailpulse_test_runner@127.0.0.1:55432/retailpulse_test'
python -m pytest -q
pg_ctl -D "$RETAILPULSE_TEST_PGDATA" -m fast stop
unset RETAILPULSE_TEST_DATABASE_URL
```

If port 55432 is occupied, stop here and select another unused test port in each
command and the URL; do not stop your existing service. If startup fails, do not continue
with database creation or tests. The temporary directory is retained for logs;
after testing you can remove that **specific disposable directory** manually.
The suite refuses pre-existing rows and only cleans up its own initially empty
test database. It never changes the `.env` production connection.

Integration tests cover real SQL/COPY, resume after exceptions, abrupt process
exit and lost connections, duplicate prevention, trigger/FK protection, row counts,
final reconciliation and status transitions. The database tests substitute disk
probes for CI container compatibility; the disk policy has independent unit tests.
Run production `seed --preflight-only` above to check native macOS volume discovery.
CI uses a PostgreSQL 17 service on GitHub's standard runner; the existing CI pass
predates this change and does not prove these new integration tests have passed.

## Back up locally

```bash
mkdir -p backups
pg_dump -h 127.0.0.1 -U retailpulse_app -d retailpulse -W -Fc -f backups/retailpulse.dump
```

Retain the source code, dependency pins and raw batch manifest to reproduce the dataset. Backups and data are ignored by Git. Restore to a separate empty database with `pg_restore --no-owner`; do not overwrite your only working copy. Keep PostgreSQL listening on loopback; no public network rules, router forwarding or cloud credentials are needed.

## Repository hygiene

`.gitignore` excludes `.env`, `data/`, Parquet/CSV bulk files, dumps, logs, caches and virtual environments. The source ZIP excludes all generated bulk data. Before your first commit:

```bash
git init
git add .
git status --short
git diff --cached --stat
git check-ignore .env data/raw/seed-42/sales_orders.parquet
```

Do not use `git add -f` on data or secrets. No GitHub repository is created or published by these scripts.

## Troubleshooting

- **Connection refused:** check `brew services list`, `pg_isready`, port and `.env`. Run `doctor` again.
- **Authentication failure:** test `psql -h 127.0.0.1 -U retailpulse_app -d retailpulse -W`. Do not loosen PostgreSQL authentication for public access.
- **Missing dependency:** activate `.venv`, then rerun `python -m pip install -r requirements.txt`.
- **Output directory exists:** use another `--out`; output is intentionally immutable.
- **Different dataset already loaded:** back up, then use the explicit reset command.
- **Schema validation fails:** transaction is rolled back. Check SQL and integration tests in the dedicated database. Do not disable constraints to get the load through.
- **Insufficient memory:** begin with the smoke profile. The provided generator is in-memory rather than a streaming million-order engine.

## Reference documentation

- Homebrew PostgreSQL 17: <https://formulae.brew.sh/formula/postgresql@17>
- psycopg COPY: <https://www.psycopg.org/psycopg3/docs/basic/copy.html>
- PostgreSQL constraints: <https://www.postgresql.org/docs/16/ddl-constraints.html>

GST rules here are the approved project's synthetic historical contract, not tax-compliance advice. No empirical seasonality or behavior claims are made.

- GitHub standard public runners: <https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job>

## Stage 3 — incremental local Parquet lake

Stage 3 preserves the operational schema and resumable loader. It uses a consistent
PostgreSQL snapshot and full-scan partition change detection because the source
has no reliable universal update timestamp. Only changed Parquet partitions are
published. Backdated records, mutable updates, cancellations/refunds and deletions
are captured without timestamp watermarks or source triggers.

```bash
cd "$HOME/Developer/retailpulse"
source .venv/bin/activate
python -m retailpulse migrate-etl
caffeinate -i python -m retailpulse etl-run --lake-root "$HOME/Developer/retailpulse/data"
python -m retailpulse etl-status --lake-root "$HOME/Developer/retailpulse/data"
python -m retailpulse verify-lake --lake-root "$HOME/Developer/retailpulse/data"
```

Run the isolated integration tests documented in [ETL_PIPELINE.md](docs/ETL_PIPELINE.md)
before the first full extraction. That guide includes the exact Mac test commands,
additive migration, table mappings, snapshot/checkpoint semantics, manifest-based
read API, disk guard, and interruption recovery. Repeat the same `etl-run` command
to resume or refresh. An unchanged source should report zero changed partitions.

Generated files live under `data/raw/extract/` and `data/processed/extract/`; existing
`data/raw/batch_001/` stays untouched. Raw/processed files share disk blocks using
hardlinks. Read the published manifest, **never glob all historical Parquet files**.
The database head switches atomically after validation, so failed runs retain the
previous complete lake. Historical objects consume space; automatic retention is
not part of v1. A 3 GiB free-space reserve is checked during writes.

See [Stage 3 test results](docs/ETL_TEST_RESULTS.md) for executed checks and remaining
native PostgreSQL verification. Stage 3 itself introduces no dashboard or cloud service.

## Public demo deployment

Use [`cloud/streamlit_app.py`](cloud/streamlit_app.py) as the Community Cloud
entrypoint with **Python 3.12** and no secrets. Its adjacent runtime dependency
file avoids ambiguity with root project metadata. The cloud entrypoint checks
installed dependencies and forces synthetic demo mode. See
[cloud/README.md](cloud/README.md) for exact setup and troubleshooting.
