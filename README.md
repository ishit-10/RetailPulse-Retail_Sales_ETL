# RetailPulse — local synthetic retail source system

A reproducible Python/PostgreSQL project for **Vastra Collective**, a fictional Indian omnichannel apparel retailer. No real customers, GST registrations, sales evidence, or market benchmarks are used. Faker names are explicitly marked synthetic; no contact details are generated.

**Default:** 100,000 orders confirmed from **1 January 2025 through 30 June 2026**, 20,000 fictional customers, 12 stores, 2 fulfillment centers, 1 logical transit location, 40 styles and 240 SKUs. Returns and deliveries settle through 31 July 2026. All merchandise money uses `Decimal` and PostgreSQL `NUMERIC`; dates report in Asia/Kolkata.

**No payment, credit card, billing account, Docker Desktop, AWS or Supabase is needed.** PostgreSQL, generation, validation and Parquet files run locally. GitHub Actions is optional. Streamlit dashboards are a later stage; this package does not claim to implement or deploy them.

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
brew install python@3.12 postgresql@16
export PATH="$(brew --prefix postgresql@16)/bin:$(brew --prefix python@3.12)/bin:$PATH"
brew services start postgresql@16
pg_isready -h 127.0.0.1 -p 5432
psql -d postgres -c 'SELECT version(), current_user;'
```

`postgresql@16` is keg-only, so the PATH command matters on both Apple Silicon and Intel Macs. Add that same `export PATH=...` line to `~/.zshrc` if you want it in future terminals. `pg_isready` only checks service readiness; it does **not** prove application credentials work.

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

The generator holds records in memory. Close memory-heavy apps for the default run; reserve several GB of free RAM and at least 2 GB free disk for generation, PostgreSQL and backups. Actual measured generation figures are in the test report. The smoke profile is suitable for initial checks on constrained machines.

### 7. Load and verify

```bash
python -m retailpulse seed --data data/raw/seed-42
python -m retailpulse verify-db
python -m pytest -q
```

The loader validates checksums and business rules, loads in foreign-key order, runs SQL checks, then commits. Any failure rolls back the entire load. Repeating the same manifest is a no-op. A different dataset requires an explicitly cleared database. The current seed command is a complete initial load, **not** an incremental ETL implementation.

Verification requires all named checks to pass. `pytest` skips database integration tests unless you explicitly configure the separate test database below.

### 8. Inspect the golden transaction

```bash
psql -h 127.0.0.1 -U retailpulse_app -d retailpulse -W \
  -c "SELECT * FROM analytics.order_financials WHERE external_reference='SYNTH-GOLDEN-001';"
```

Expected: GMV ₹6,000; discount ₹1,200; booked merchandise ₹4,800; net sales ₹2,400; net COGS ₹1,200; gross profit ₹1,200; gross margin 0.50; successful refund ₹2,520; net units 1. Original invoice GST totals ₹240. Two unit-level invoice lines reference one two-unit order line.

## Reset and regenerate

**This clears all synthetic rows in the named database.** It leaves the schema and generated files intact and refuses unknown database names or remote hosts.

```bash
python -m retailpulse reset --confirm-database retailpulse
python -m retailpulse seed --data data/raw/seed-42
```

No `DROP DATABASE`, no cascading drops, and no automatic overwrite of existing raw files. A schema migration beyond v1 must be explicitly designed; `init` is not a general migration engine.

## Run PostgreSQL integration tests on your Mac

Create a separate disposable database using your Homebrew administrator:

```bash
createdb --owner=retailpulse_app retailpulse_test
```

Keep credentials out of shell history. Set the test URL from the existing `.env` values:

```bash
export RETAILPULSE_TEST_DATABASE_URL="$(python - <<'PY'
from retailpulse.db import engine
print(engine().url.set(database='retailpulse_test').render_as_string(hide_password=False))
PY
)"
python -m pytest -m integration -q
unset RETAILPULSE_TEST_DATABASE_URL
```

Tests require an empty `retailpulse_test` and intentionally leave their data available for inspection. Before rerunning, clear only the test dataset explicitly:

```bash
PGDATABASE=retailpulse_test python -m retailpulse reset --confirm-database retailpulse_test
```

Integration tests check SQL initialization, actual `COPY` load, repeated-batch idempotency, SQL reconciliation and rejected invalid writes. They are not replaced by SQLite mocks. This project's optional CI uses a PostgreSQL service container **on GitHub's runner**; no local Docker is required. Use standard runners in a public repository; do not enable paid larger runners. Provider policies may change, and the full local workflow never depends on CI.

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

- Homebrew PostgreSQL 16: <https://formulae.brew.sh/formula/postgresql@16>
- psycopg COPY: <https://www.psycopg.org/psycopg3/docs/basic/copy.html>
- PostgreSQL constraints: <https://www.postgresql.org/docs/16/ddl-constraints.html>

GST rules here are the approved project's synthetic historical contract, not tax-compliance advice. No empirical seasonality or behavior claims are made.

- GitHub standard public runners: <https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/choose-the-runner-for-a-job>
