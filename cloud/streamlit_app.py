"""Community Cloud entrypoint; local users keep the root streamlit_app.py."""
from importlib.util import find_spec
from pathlib import Path
import os
import sys

import streamlit as st

# Streamlit executes from the repo root but script-import paths may differ.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    # Match the Python version used by the fresh cloud-environment app checks.
    if sys.version_info[:2] != (3, 12):
        st.error(f'Cloud runtime is Python {sys.version_info[0]}.{sys.version_info[1]}. This deployment requires Python 3.12.')
        st.info('Recreate this cloud app, select Advanced settings → Python 3.12 → Save, then deploy cloud/streamlit_app.py.')
        st.stop()
    required = {
        'dotenv': 'python-dotenv', 'sqlalchemy': 'SQLAlchemy', 'psycopg': 'psycopg[binary]',
        'pyarrow': 'pyarrow', 'duckdb': 'duckdb', 'plotly': 'plotly',
        'pandas': 'pandas', 'numpy': 'numpy',
    }
    missing = [package for module, package in required.items() if find_spec(module) is None]
    if missing:
        st.error('Cloud dependencies are missing: ' + ', '.join(missing))
        st.info('Check deployment entrypoint cloud/streamlit_app.py and installation of cloud/requirements.txt. Reboot after correcting the deployment. Do not install packages at application runtime.')
        st.stop()
    # Public cloud deployment always uses the synthetic fixture, irrespective of
    # accidental account environment settings. Root entrypoint retains local mode.
    os.environ['RETAILPULSE_DASHBOARD_MODE'] = 'demo'
    from retailpulse.dashboard.app import main as dashboard
    dashboard()


main()
