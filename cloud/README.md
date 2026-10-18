# Community Cloud deployment

Use `cloud/streamlit_app.py` on branch `main`, Python **3.12**, with no secrets.
The root entrypoint remains available for local/demo use on your Mac.

Streamlit searches an entrypoint's directory for dependency files before the
repository root. This directory supplies one explicit `requirements.txt`, with
only dashboard runtime dependencies; it retains the tested versions. Root
requirements and pyproject.toml continue to serve the full data-engineering
project and are not removed. No business query or KPI formula changes.

The entrypoint checks the actual Python runtime and required import names before
importing the database module, and displays actionable errors if the environment
is incomplete. These checks diagnose installation problems; they do not install
packages or fix Cloud's package manager. Public deployment forces demo mode and
requires no PostgreSQL access. No credentials should be entered in cloud secrets.

## Exact deployment settings

1. Merge this fix into the deployed branch after CI passes.
2. Delete only the failed cloud app, not the GitHub repository or local project.
3. Create app → repository `ishit-10/RetailPulse-Retail_Sales_ETL`, branch `main`.
4. Main file path: **`cloud/streamlit_app.py`**.
5. Advanced settings → Python **3.12** → Save. Leave secrets empty.
6. Deploy. Installation logs should identify **`cloud/requirements.txt`** and
   **Python 3.12**. If they identify the root file or Python 3.14, the deployment
   does not have these settings. Restarting an existing app does not change Python.
7. Check all five pages, filters and downloads at the public URL. A successful
   package install alone does not establish that the dashboard works.

Existing failure evidence: Cloud logged Python 3.14.8, selected the correct root
requirements file, and failed at `from dotenv import load_dotenv`. GitHub's root
file was directly verified to contain python-dotenv. The supplied log omits a
package installation listing, so the exact reason for that missing module remains
unproven. The presence of root pyproject.toml is not evidence that Poetry won:
those logs explicitly say uv selected requirements.txt.

Local reproduction of the deployment environment:

```bash
python3.12 -m venv /tmp/retailpulse-cloud-check
source /tmp/retailpulse-cloud-check/bin/activate
python -m pip install -r cloud/requirements.txt
python -m streamlit run cloud/streamlit_app.py
```

The cloud app can run from the repository without `pip install -e .`: its
entrypoint adds the repository root to the import path, and all runtime packages
are declared explicitly. Do not export your full database for this deployment.

## Executed verification for this fix

A new Python 3.12.14 virtual environment installed only `cloud/requirements.txt`.
All five pages then passed Streamlit AppTest checks in that environment, including
the dedicated entrypoint overriding an accidental local-mode setting. No Faker,
pytest or editable project installation was required for this app startup check.
After adding Faker and pytest as test-only dependencies, `python -m pytest -q`
reported **128 passed, 31 skipped in 11.20 seconds**. The skips require native
PostgreSQL, which is unavailable here. New tests cover cloud startup, the missing
dotenv diagnostic and wrong-Python rejection. The owner's prior 156-test native
pass predates this fix. This is fresh-environment evidence, not a successful
Streamlit Community Cloud deployment claim; the live URL still needs checking.
