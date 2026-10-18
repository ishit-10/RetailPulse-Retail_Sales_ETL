"""Cloud startup must fail actionably before loading unavailable DB imports."""
from pathlib import Path
import sys
import pytest
from streamlit.testing.v1 import AppTest

ENTRY = Path(__file__).resolve().parents[1] / 'cloud' / 'streamlit_app.py'


@pytest.mark.skipif(sys.version_info[:2] != (3, 12), reason='Cloud deployment explicitly targets Python 3.12')
def test_cloud_entrypoint_renders_and_forces_demo(monkeypatch):
    monkeypatch.setenv('RETAILPULSE_DASHBOARD_MODE', 'local')
    app = AppTest.from_file(str(ENTRY)).run(timeout=30)
    assert not app.exception and not app.error
    assert app.title[0].value == 'Executive overview'
    assert any('DEMO' in caption.value for caption in app.caption)


@pytest.mark.skipif(sys.version_info[:2] != (3, 12), reason='Cloud deployment explicitly targets Python 3.12')
def test_cloud_missing_dependency_is_actionable(monkeypatch):
    import importlib.util
    original = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, 'find_spec', lambda module, *a, **kw: None if module == 'dotenv' else original(module, *a, **kw))
    app = AppTest.from_file(str(ENTRY)).run(timeout=30)
    assert not app.exception
    assert 'python-dotenv' in app.error[0].value
    assert not app.title


def test_cloud_rejects_wrong_runtime(monkeypatch):
    monkeypatch.setattr(sys, 'version_info', (3, 14, 0))
    # Use the lightweight guard through direct execution with a minimal Streamlit
    # stand-in; patching sys.version_info inside AppTest affects its own runtime.
    import runpy
    messages = []
    class Halt(Exception): pass
    class Streamlit:
        error = staticmethod(messages.append)
        info = staticmethod(messages.append)
        @staticmethod
        def stop(): raise Halt()
    monkeypatch.setitem(sys.modules, 'streamlit', Streamlit)
    with pytest.raises(Halt): runpy.run_path(str(ENTRY))
    assert any('3.12' in message for message in messages)
