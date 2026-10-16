"""Never connects unless the user explicitly configures a disposable test DB."""
import os
import pytest
from sqlalchemy import text
from retailpulse import db
from retailpulse.generate import Config, generate

pytestmark=pytest.mark.integration

@pytest.fixture(scope='module')
def pg(tmp_path_factory):
    url=os.getenv('RETAILPULSE_TEST_DATABASE_URL')
    if not url: pytest.skip('No disposable PostgreSQL test database configured')
    e=db.engine(url)
    if e.url.database!='retailpulse_test': pytest.fail('Test database must be named retailpulse_test')
    db.initialize(e)
    # Never clear a pre-existing data set implicitly.
    with e.connect() as c:
        if c.execute(text('SELECT count(*) FROM source.sales_orders')).scalar():
            pytest.fail('Test database must be empty; reset it explicitly first')
    path=tmp_path_factory.mktemp('pg')/'batch'
    generate(Config(orders=500,styles=2,customers=50),path)
    db.seed(e,path)
    yield e,path
    e.dispose()

def test_initialization_load_and_idempotent_replay(pg):
    e,path=pg
    assert 'already' in db.initialize(e)
    assert 'already loaded' in db.seed(e,path)
    assert db.verify_database(e)['rows']['sales_orders']==500

def test_immutable_invoice(pg):
    e,_=pg
    with pytest.raises(Exception):
        with e.begin() as c: c.execute(text('UPDATE source.invoice_lines SET taxable_value=0'))

def test_overfulfillment_rejected(pg):
    e,_=pg
    with pytest.raises(Exception):
        with e.begin() as c:
            c.execute(text('INSERT INTO source.fulfillment_items SELECT 99999999,fulfillment_id,order_line_id,99999 FROM source.fulfillment_items LIMIT 1'))

def test_negative_price_rejected(pg):
    e,_=pg
    with pytest.raises(Exception):
        with e.begin() as c: c.execute(text('UPDATE source.price_versions SET list_price=-1 WHERE price_id=1'))
