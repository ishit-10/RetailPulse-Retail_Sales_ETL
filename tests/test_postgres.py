"""Opt-in disposable database only. Refuses all pre-existing source rows."""
import os

import pytest
from retailpulse import db, loader
from retailpulse.generate import Config, generate

pytestmark = pytest.mark.integration


@pytest.fixture(scope='module')
def database(tmp_path_factory):
    url = os.getenv('RETAILPULSE_TEST_DATABASE_URL')
    if not url:
        pytest.skip('No disposable PostgreSQL test database configured')
    e = db.engine(url)
    if e.url.database != 'retailpulse_test':
        pytest.fail('Test database must be named retailpulse_test')
    db.initialize(e)
    with e.connect() as c:
        if any(loader.counts(c).values()) or c.exec_driver_sql('SELECT count(*) FROM source.ingestion_batches').scalar():
            pytest.fail('Test database must be empty; tests will not clear existing data')
    loader.migrate(e)
    loader.migrate(e)  # Versioned migration must be repeatable.
    with e.connect() as c:
        if c.exec_driver_sql('SELECT count(*) FROM etl.seed_runs').scalar():
            pytest.fail('Test database has existing loader metadata; refusing to clear it')
    path = tmp_path_factory.mktemp('pg') / 'batch'
    generate(Config(orders=500, styles=2, customers=50), path)
    yield e, path
    e.dispose()


@pytest.fixture
def pg(database, monkeypatch):
    e, path = database
    # CI service containers cannot expose the native server's filesystem to Python.
    # Disk policy is independently unit-tested; these tests exercise real SQL/COPY.
    monkeypatch.setattr(loader, 'disk_paths', lambda c, supplied=None: [path])
    monkeypatch.setattr(loader, 'check_disk', lambda paths, remaining, **kwargs: kwargs.get("reserve_bytes", loader.RESERVE) + 8*remaining)
    yield e, path
    # Safe because the module fixture proved the disposable database was empty.
    db.reset(e, 'retailpulse_test')


@pytest.fixture
def loaded(pg):
    e, path = pg
    db.seed(e, path, chunk_rows=1000)
    return e, path


def scalar(e, query):
    with e.connect() as c:
        return c.exec_driver_sql(query).scalar()


def test_initialization_load_and_idempotent_replay(loaded):
    e, path = loaded
    assert 'already' in db.initialize(e)
    assert 'already loaded' in db.seed(e, path)
    result = db.verify_database(e)
    manifest, _ = loader.inspect_dataset(path)
    assert result['rows'] == {n: loader.expected(manifest, n) for n in loader.TABLES}
    assert result['rows']['sales_orders'] == 500
    assert scalar(e, "SELECT status FROM source.ingestion_batches") == 'LOADED'
    assert scalar(e, "SELECT state FROM etl.seed_runs") == 'completed'
    assert scalar(e, 'SELECT bool_and(state=\'completed\') FROM etl.seed_tables')


def test_failed_chunk_rolls_back_and_resume_changes_chunk_size(pg, monkeypatch):
    e, path = pg
    original = loader.copy_chunk
    calls = 0
    def fail(c, name, batch, table):
        nonlocal calls
        original(c, name, batch, table)
        calls += 1
        if calls == 2:
            raise RuntimeError('injected after COPY, before checkpoint')
    with monkeypatch.context() as m:
        m.setattr(loader, 'copy_chunk', fail)
        with pytest.raises(RuntimeError):
            db.seed(e, path, chunk_rows=1000)
    assert scalar(e, 'SELECT count(*) FROM source.source_events') == 1000
    assert scalar(e, "SELECT committed_rows FROM etl.seed_tables WHERE table_name='source_events'") == 1000
    assert scalar(e, "SELECT state FROM etl.seed_runs") == 'failed'
    assert scalar(e, "SELECT status FROM source.ingestion_batches") == 'GENERATED'
    with pytest.raises(ValueError, match='fully loaded'):
        db.verify_database(e)
    db.seed(e, path, chunk_rows=777)
    assert db.verify_database(e)['rows']['sales_orders'] == 500


def test_lost_connection_resume(pg, monkeypatch):
    from sqlalchemy.exc import OperationalError

    e, path = pg
    original = loader.copy_chunk
    calls = 0
    def disconnect(c, name, batch, table):
        nonlocal calls
        calls += 1
        if calls == 2:
            c.connection.driver_connection.close()
            raise ConnectionError('simulated server disconnect')
        original(c, name, batch, table)
    with monkeypatch.context() as m:
        m.setattr(loader, 'copy_chunk', disconnect)
        # Transaction exit attempts rollback on the deliberately closed driver
        # connection. SQLAlchemy surfaces that failure as OperationalError.
        with pytest.raises(OperationalError, match='connection is closed') as error:
            db.seed(e, path, chunk_rows=1000)
        assert error.value.connection_invalidated
    assert scalar(e, 'SELECT count(*) FROM source.source_events') == 1000
    assert scalar(e, "SELECT committed_rows FROM etl.seed_tables WHERE table_name='source_events'") == 1000
    assert scalar(e, "SELECT state FROM etl.seed_runs") == 'loading'
    assert scalar(e, "SELECT status FROM source.ingestion_batches") == 'GENERATED'
    db.seed(e, path, chunk_rows=5000)
    assert db.verify_database(e)['rows']['sales_orders'] == 500
    assert scalar(e, "SELECT status FROM source.ingestion_batches") == 'LOADED'


def test_unmanaged_partial_refused(pg):
    e, path = pg
    with e.begin() as c:
        c.exec_driver_sql("INSERT INTO source.states VALUES(999,'test','99')")
    with pytest.raises(ValueError, match='without checkpoints'):
        db.seed(e, path)
    assert scalar(e, 'SELECT count(*) FROM source.states') == 1
    assert scalar(e, 'SELECT count(*) FROM etl.seed_runs') == 0


def test_checkpoint_drift_refused(loaded):
    e, path = loaded
    with e.begin() as c:
        c.exec_driver_sql("INSERT INTO source.states VALUES(999,'test','99')")
    with pytest.raises(ValueError, match='differ from checkpoints'):
        db.seed(e, path)


def test_database_constraint_failure_rolls_back_chunk(pg, monkeypatch):
    e, path = pg
    original = loader.copy_chunk
    def duplicate(c, name, batch, table):
        original(c, name, batch, table)
        if name == 'states':
            original(c, name, batch, table)
    with monkeypatch.context() as m:
        m.setattr(loader, 'copy_chunk', duplicate)
        with pytest.raises(Exception):
            db.seed(e, path)
    assert scalar(e, 'SELECT count(*) FROM source.states') == 0
    assert scalar(e, "SELECT committed_rows FROM etl.seed_tables WHERE table_name='states'") == 0
    db.seed(e, path)
    assert db.verify_database(e)['rows']['sales_orders'] == 500


def test_foreign_keys_and_financial_triggers_across_checkpoints(loaded):
    e, _ = loaded
    statements = [
        'UPDATE source.invoice_lines SET taxable_value=0',
        'UPDATE source.credit_note_lines SET taxable_value=0',
        'DELETE FROM source.inventory_movements',
        'INSERT INTO source.fulfillment_items SELECT 99999999,fulfillment_id,order_line_id,99999 FROM source.fulfillment_items LIMIT 1',
        'UPDATE source.price_versions SET list_price=-1 WHERE price_id=1',
        "INSERT INTO source.source_events VALUES(999999999,'bad-fk','test','test',now(),999)",
        "UPDATE source.payment_transactions SET amount=999999999 WHERE type='REFUND' AND status='SUCCEEDED'",
    ]
    for statement in statements:
        with pytest.raises(Exception):
            with e.begin() as c:
                c.exec_driver_sql(statement)
    assert db.verify_database(e)['rows']['sales_orders'] == 500


def test_reconciliation_failure_keeps_generated_and_resumes(pg, monkeypatch):
    e, path = pg
    with monkeypatch.context() as m:
        m.setattr(loader, 'verify_connection', lambda c: ['injected_final_failure'])
        with pytest.raises(ValueError, match='injected_final_failure'):
            db.seed(e, path)
    assert scalar(e, "SELECT status FROM source.ingestion_batches") == 'GENERATED'
    assert scalar(e, 'SELECT count(*) FROM source.sales_orders') == 500
    db.seed(e, path)
    assert scalar(e, "SELECT status FROM source.ingestion_batches") == 'LOADED'


def test_disk_rejection_does_not_create_batch(pg, monkeypatch):
    e, path = pg
    def reject(*args, **kwargs):
        raise ValueError('Unsafe disk space')
    monkeypatch.setattr(loader, 'check_disk', reject)
    with pytest.raises(ValueError, match='Unsafe disk'):
        db.seed(e, path)
    assert scalar(e, 'SELECT count(*) FROM source.ingestion_batches') == 0


def test_preflight_read_only(pg):
    e, path = pg
    assert db.seed(e, path, preflight_only=True)['preflight'] == 'passed'
    assert scalar(e, 'SELECT count(*) FROM etl.seed_runs') == 0


def test_second_loader_refused(pg):
    e, path = pg
    with loader.locked(e):
        with pytest.raises(ValueError, match='Another loader'):
            db.seed(e, path)


def test_wrong_dataset_identity_refused(loaded, tmp_path):
    import hashlib
    import json
    e, path = loaded
    for file in path.glob('*.parquet'):
        (tmp_path / file.name).symlink_to(file)
    manifest = json.loads((path / 'manifest.json').read_text())
    manifest.pop('dataset_id')
    manifest['config']['seed'] += 1
    manifest['dataset_id'] = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    (tmp_path / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='Different dataset'):
        db.seed(e, tmp_path)
    assert scalar(e, 'SELECT count(*) FROM source.sales_orders') == 500


def test_abrupt_process_exit_and_resume(pg):
    import subprocess
    import sys
    e, path = pg
    code = '''
import os
from retailpulse import db, loader
loader.disk_paths = lambda c, supplied=None: []
loader.check_disk = lambda *args, **kwargs: 0
original = loader.copy_chunk
calls = 0
def kill(c, name, batch, table):
    global calls
    original(c, name, batch, table)
    calls += 1
    if calls == 2:
        os._exit(23)
loader.copy_chunk = kill
engine = db.engine(os.environ['RETAILPULSE_TEST_DATABASE_URL'])
db.seed(engine, os.environ['TEST_DATA_PATH'], chunk_rows=1000)
'''
    result = subprocess.run([sys.executable, '-c', code],
                            env={**os.environ, 'TEST_DATA_PATH': str(path)}, timeout=120)
    assert result.returncode == 23
    assert scalar(e, 'SELECT count(*) FROM source.source_events') == 1000
    assert scalar(e, "SELECT state FROM etl.seed_runs") == 'loading'
    assert scalar(e, "SELECT status FROM source.ingestion_batches") == 'GENERATED'
    db.seed(e, path)
    assert db.verify_database(e)['rows']['sales_orders'] == 500


def test_low_disk_load_and_preflight(pg):
    e, path = pg
    report = db.seed(e, path, low_disk=True, preflight_only=True, chunk_rows=5000)
    assert report['disk_mode'] == 'low-disk'
    assert report['reserve_gib'] == 3
    assert report['chunk_rows'] == 1000
    assert scalar(e, 'SELECT count(*) FROM etl.seed_runs') == 0
    db.seed(e, path, low_disk=True)
    assert db.verify_database(e)['rows']['sales_orders'] == 500
    assert scalar(e, "SELECT status FROM source.ingestion_batches") == 'LOADED'
