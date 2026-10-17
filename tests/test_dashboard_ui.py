"""Each page is tested through its real renderer with the shared application shell."""
import pytest
from streamlit.testing.v1 import AppTest


@pytest.mark.parametrize('page',['overview','stores','products','customers','health'])
def test_page_renders_demo_without_database(page,monkeypatch):
    monkeypatch.setenv('RETAILPULSE_DASHBOARD_MODE','demo')
    app=AppTest.from_string(f"from retailpulse.dashboard.app import main\nmain(page_override='{page}')").run(timeout=30)
    assert not app.exception
    assert not app.error
    assert len(app.title)==1
    assert app.selectbox(key='global_state_id')


def test_filter_interaction_reset_and_empty_state(monkeypatch):
    monkeypatch.setenv('RETAILPULSE_DASHBOARD_MODE','demo')
    app=AppTest.from_string("from retailpulse.dashboard.app import main\nmain(page_override='overview')").run(timeout=30)
    app.selectbox(key='global_channel').set_value('WEB').run(timeout=30)
    assert not app.exception and not app.error
    assert app.selectbox(key='global_city').disabled
    assert app.selectbox(key='global_store_id').disabled
    app.selectbox(key='global_category_id').set_value(1).run(timeout=30)
    assert not app.exception and not app.error
    app.button[0].click().run(timeout=30)
    assert app.selectbox(key='global_channel').value is None
    assert app.selectbox(key='global_category_id').value is None


def test_database_error_is_sanitized(monkeypatch):
    from retailpulse.dashboard import app as module
    monkeypatch.setenv('RETAILPULSE_DASHBOARD_MODE','local')
    def fail(*args,**kw): raise RuntimeError('postgresql://secret-password@example.com')
    monkeypatch.setattr(module,'local_engine',fail)
    app=AppTest.from_string("from retailpulse.dashboard.app import main\nmain(page_override='overview')").run(timeout=30)
    assert not app.exception
    assert app.error
    assert 'secret-password' not in app.error[0].value
