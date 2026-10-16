"""Only the existing explicitly configured, initially empty disposable DB fixture."""
import pytest
from retailpulse import etl
from test_postgres import database, pg, loaded, scalar  # reuse Stage 2 safety gates

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def small_disposable_lake_reserve(monkeypatch):
    # These tests write a 500-order lake in pytest's temporary directory.
    # Keep real free-space checks, using a 64 MiB reserve for this tiny fixture.
    # The normal CLI's 3 GiB policy is unchanged and independently unit-tested.
    monkeypatch.setattr(etl, 'RESERVE', 64 * 1024**2)


@pytest.fixture
def lake(loaded, tmp_path):
    e, _ = loaded
    etl.migrate(e)
    etl.migrate(e)
    return e, tmp_path / 'lake'


def test_full_then_noop_and_late_changes(lake):
    e, root = lake
    first = etl.run(e, root)
    assert first['partitions_changed'] == len(etl.TABLES) * etl.BUCKETS
    old = etl.current_manifest(e, root)
    assert etl.verify(e, root)['rows']['sales_orders'] == 500
    assert etl.run(e, root)['partitions_changed'] == 0
    with e.begin() as c:
        c.exec_driver_sql("UPDATE source.customers SET synthetic_name='Changed without timestamp' WHERE customer_id=1")
        c.exec_driver_sql("INSERT INTO source.customers VALUES(999999,'SYNTH-LATE','Late arrival','2020-01-01')")
        c.exec_driver_sql("INSERT INTO source.order_status_events SELECT 99999999,order_id,'DELIVERED','DELIVERED','2020-01-01',event_id FROM source.order_status_events LIMIT 1")
    changed = etl.run(e, root)
    assert changed['partitions_changed'] == 3
    customers = etl.read_table(e, 'customers', root).to_pandas()
    assert customers.customer_id.is_unique
    assert customers.loc[customers.customer_id == 1, 'synthetic_name'].item() == 'Changed without timestamp'
    assert 999999 in set(customers.customer_id)
    # Old pinned snapshot remains readable; source records/triggers are preserved.
    assert etl.read_table(e, 'customers', root, old).num_rows == 50
    with e.begin() as c: c.exec_driver_sql('DELETE FROM source.customers WHERE customer_id=999999')
    assert etl.run(e, root)['partitions_changed'] == 1
    assert etl.read_table(e, 'customers', root).num_rows == 50


def test_interrupted_publication_retries_without_duplicates(lake, monkeypatch):
    e, root = lake
    etl.run(e, root)
    head = etl.current_manifest(e, root)
    with monkeypatch.context() as m:
        def fail(*args, **kwargs): raise KeyboardInterrupt()
        m.setattr(etl, 'publish', fail)
        with pytest.raises(KeyboardInterrupt): etl.run(e, root)
    assert etl.current_manifest(e, root) == head
    assert etl.status(e, root)['runs'][0]['status'] == 'failed'
    assert etl.run(e, root)['partitions_changed'] == 0
    assert etl.verify(e, root)['rows']['order_lines'] == head['tables']['order_lines']['rows']


def test_first_failure_does_not_publish(lake, monkeypatch):
    e, root = lake
    original = etl.write_partition
    calls = 0
    def fail(*args, **kwargs):
        nonlocal calls
        part = original(*args, **kwargs)
        calls += 1
        if calls == 3: raise OSError('injected disk write failure')
        return part
    with monkeypatch.context() as m:
        m.setattr(etl, 'write_partition', fail)
        with pytest.raises(OSError): etl.run(e, root)
    with pytest.raises(ValueError, match='No published'): etl.current_manifest(e, root)
    assert etl.run(e, root)['status'] == 'completed'
    assert etl.verify(e, root)['rows']['sales_orders'] == 500


def test_partial_source_and_simultaneous_loader_refused(pg, tmp_path):
    from retailpulse import loader
    e, _ = pg
    etl.migrate(e)
    with pytest.raises(ValueError, match='incomplete'): etl.run(e, tmp_path)
    with loader.locked(e):
        with pytest.raises(ValueError, match='Another loader'): etl.run(e, tmp_path)


def test_lake_corruption_cannot_advance_checkpoint(lake):
    e, root = lake
    etl.run(e, root)
    manifest = etl.current_manifest(e, root)
    part = manifest['tables']['sales_orders']['partitions']['0']
    (root / part['file']).write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='checksum'): etl.run(e, root)
    assert etl.current_manifest(e, root)['run_id'] == manifest['run_id']


def test_snapshot_excludes_concurrent_change_until_next_run(lake, monkeypatch):
    e, root = lake
    original = etl.write_partition
    injected = False
    def concurrent(*args, **kwargs):
        nonlocal injected
        part = original(*args, **kwargs)
        if not injected:
            injected = True
            with e.begin() as c:
                c.exec_driver_sql("UPDATE source.customers SET synthetic_name='Concurrent change' WHERE customer_id=1")
        return part
    with monkeypatch.context() as m:
        m.setattr(etl, 'write_partition', concurrent)
        etl.run(e, root)
    before = etl.read_table(e, 'customers', root).to_pandas().set_index('customer_id')
    assert before.loc[1, 'synthetic_name'] != 'Concurrent change'
    assert etl.run(e, root)['partitions_changed'] == 1
    after = etl.read_table(e, 'customers', root).to_pandas().set_index('customer_id')
    assert after.loc[1, 'synthetic_name'] == 'Concurrent change'


def test_hard_exit_resumes(lake):
    import os
    import subprocess
    import sys
    e, root = lake
    code = '''
import os
from retailpulse import db, etl
e = db.engine(os.environ['RETAILPULSE_TEST_DATABASE_URL'])
# Mirror the parent pytest fixture; monkeypatches do not cross process boundaries.
etl.RESERVE = 64 * 1024**2
original = etl.write_partition
calls = 0
def stop(*args, **kwargs):
    global calls
    result = original(*args, **kwargs)
    calls += 1
    if calls == 3: os._exit(29)
    return result
etl.write_partition = stop
etl.run(e, os.environ['TEST_LAKE_ROOT'])
'''
    result = subprocess.run([sys.executable, '-c', code], env={**os.environ, 'TEST_LAKE_ROOT': str(root)}, timeout=120)
    assert result.returncode == 29
    assert etl.status(e, root)['runs'][0]['status'] == 'running'
    assert etl.run(e, root)['status'] == 'completed'
    assert etl.status(e, root)['runs'][1]['error_class'] == 'ProcessInterrupted'
    assert etl.verify(e, root)['rows']['sales_orders'] == 500


def test_cancellation_and_refund_updates_and_validation_gate(lake):
    e, root = lake
    etl.run(e, root)
    with e.begin() as c:
        c.exec_driver_sql("UPDATE source.order_line_cancellations SET reason='Late correction' WHERE cancellation_id=(SELECT min(cancellation_id) FROM source.order_line_cancellations)")
        c.exec_driver_sql("UPDATE source.payment_transactions SET provider_reference='SYNTH-LATE-REFUND' WHERE payment_txn_id=(SELECT min(payment_txn_id) FROM source.payment_transactions WHERE type='REFUND')")
        c.exec_driver_sql("INSERT INTO source.payment_status_events SELECT 99999999,payment_txn_id,status,'2020-01-01' FROM source.payment_transactions WHERE type='REFUND' LIMIT 1")
    assert etl.run(e, root)['partitions_changed'] == 3
    assert 'Late correction' in etl.read_table(e, 'order_line_cancellations', root)['reason'].to_pylist()
    assert 'SYNTH-LATE-REFUND' in etl.read_table(e, 'payment_transactions', root)['provider_reference'].to_pylist()
    head = etl.current_manifest(e, root)
    # Legal source UPDATE that violates cross-table final reconciliation must
    # not publish, even though all files were successfully written.
    with e.begin() as c:
        c.exec_driver_sql("UPDATE source.inventory_snapshots SET reserved=1 WHERE (snapshot_date,sku_id,location_id)=(SELECT snapshot_date,sku_id,location_id FROM source.inventory_snapshots LIMIT 1)")
    with pytest.raises(ValueError, match='reserved_snapshot'): etl.run(e, root)
    assert etl.current_manifest(e, root) == head
