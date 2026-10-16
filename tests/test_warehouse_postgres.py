"""Native PostgreSQL tests: ONLY the guarded disposable retailpulse_test fixture."""
from decimal import Decimal
import copy
import json
import pytest
from sqlalchemy import text
from retailpulse import etl, warehouse, kpis
from test_postgres import database, pg, loaded, scalar

pytestmark = pytest.mark.integration


@pytest.fixture
def lake(loaded, tmp_path, monkeypatch):
    e, _ = loaded
    monkeypatch.setattr(etl, 'RESERVE', 64 * 1024**2)
    # Real COPY/DDL/transactions; disk policy has separate tests.
    monkeypatch.setattr(warehouse, 'disk_preflight', lambda *a: (0, []))
    etl.migrate(e)
    warehouse.migrate(e)
    warehouse.migrate(e)
    root = tmp_path / 'lake'
    etl.run(e, root)
    yield e, root
    # Only warehouse-owned objects in the explicitly guarded disposable DB.
    with e.begin() as c:
        for name in reversed(warehouse.OBJECTS): c.exec_driver_sql(f'DROP VIEW IF EXISTS analytics.{name}')
        c.exec_driver_sql('DELETE FROM etl.warehouse_head')
    warehouse.cleanup(e, include_previous=True)


def test_full_idempotent_golden_reconciliation_and_reports(lake):
    e, root = lake
    result = warehouse.run(e, root)
    assert result['status'] == 'completed'
    assert warehouse.verify(e)['rows']['fact_order_items'] == scalar(e, 'SELECT count(*) FROM source.order_lines')
    assert warehouse.run(e, root)['status'] == 'unchanged'
    assert scalar(e, 'SELECT count(*) FROM analytics.fact_order_items') == scalar(e, 'SELECT count(*) FROM source.order_lines')
    assert scalar(e, 'SELECT count(*) FROM source.sales_orders') == 500
    # Full-data measures reconcile independently with existing approved views.
    report = kpis.report(e, 'sales', '2020-01-01', '2030-12-31')['rows'][0]
    assert report['net_sales'] == scalar(e, 'SELECT sum(net_sales) FROM analytics.sales_events')
    assert report['net_cogs'] == scalar(e, 'SELECT sum(net_cogs) FROM analytics.sales_events')
    assert report['gmv'] == scalar(e, 'SELECT sum(booked_gmv) FROM analytics.bookings')
    with e.connect() as c:
        golden = c.exec_driver_sql("SELECT order_id FROM source.sales_orders WHERE external_reference='SYNTH-GOLDEN-001'").scalar()
        row = c.execute(text('SELECT sum(net_sales) sales,sum(net_cogs) cogs FROM analytics.warehouse_sales_detail WHERE order_id=:id'), {'id': golden}).one()
        assert row.sales == Decimal('2400') and row.cogs == Decimal('1200')
        assert (row.sales-row.cogs)/row.sales == Decimal('0.5')
    for kind in ('daily','weekly','monthly','products','categories','stores','channels','refunds','footfall','inventory','sell-through'):
        assert kpis.report(e, kind, '2025-01-01', '2026-06-30')['snapshot_id'] == result['snapshot_id']
    for kind in ('daily','weekly','monthly'):
        periods = kpis.report(e, kind, '2025-02-10', '2025-03-15')['rows']
        detail = kpis.report(e, 'sales', '2025-02-10', '2025-03-15')['rows'][0]
        assert sum(r['net_sales'] for r in periods) == detail['net_sales']
    assert all(r['previous_period_growth_percent'] is None for r in kpis.report(e, 'monthly', '2025-02-10', '2025-03-15')['rows'])
    cutoff = scalar(e, 'SELECT max(date_key) FROM analytics.dim_date')
    assert kpis.report(e, 'returns', '2025-01-01', '2026-06-30', as_of=cutoff)['rows'][0]['return_percent'] >= 0
    empty = kpis.report(e, 'sales', '2000-01-01', '2000-01-31')['rows'][0]
    assert empty['opd'] == 0 and empty['booked_aov'] is None and empty['gross_margin_percent'] is None
    assert kpis.report(e, 'inventory', '2000-01-01', '2000-01-31')['rows'] == []
    assert kpis.report(e, 'sales', '2025-01-01', '2026-06-30', {'channel': 'WEB'})['rows'][0]['orders'] > 0


def test_corrupt_or_stale_snapshot_preserves_published_warehouse(lake):
    e, root = lake
    first = warehouse.run(e, root)
    manifest = etl.current_manifest(e, root)
    part = next(iter(manifest['tables']['price_versions']['partitions'].values()))
    path = root / part['file']
    original = path.read_bytes()
    path.write_bytes(original + b'corrupt')
    try:
        with pytest.raises(ValueError, match='checksum'): warehouse.run(e, root)
        assert warehouse.verify(e)['run_id'] == first['run_id']
    finally: path.write_bytes(original)
    broken = copy.deepcopy(manifest)
    del broken['tables']['price_versions']
    with e.begin() as c:
        c.execute(text('UPDATE etl.extract_runs SET manifest=CAST(:m AS jsonb) WHERE run_id=:id'), {'m': json.dumps(broken), 'id': manifest['run_id']})
    with pytest.raises(ValueError, match='incomplete'): warehouse.run(e, root)
    assert warehouse.verify(e)['run_id'] == first['run_id']
    with e.begin() as c:
        c.execute(text('UPDATE etl.extract_runs SET manifest=CAST(:m AS jsonb) WHERE run_id=:id'), {'m': json.dumps(manifest), 'id': manifest['run_id']})
        c.execute(text("UPDATE etl.extract_runs SET started_at=clock_timestamp()-interval '2 days' WHERE run_id=:id"), {'id': manifest['run_id']})
    with pytest.raises(ValueError, match='stale'): warehouse.run(e, root)
    assert warehouse.verify(e)['run_id'] == first['run_id']


def test_failure_after_private_copy_and_retry(lake, monkeypatch):
    e, root = lake
    first = warehouse.run(e, root)
    etl.run(e, root)
    original = warehouse.copy_table
    calls = 0
    def fail(*args):
        nonlocal calls
        original(*args)
        calls += 1
        if calls == 2: raise KeyboardInterrupt('simulated interruption')
    with monkeypatch.context() as m:
        m.setattr(warehouse, 'copy_table', fail)
        with pytest.raises(KeyboardInterrupt): warehouse.run(e, root)
    assert warehouse.verify(e)['run_id'] == first['run_id']
    assert warehouse.cleanup(e)['removed_unpublished_builds']
    second = warehouse.run(e, root)
    assert second['run_id'] != first['run_id']
    assert warehouse.verify(e)['rows'] == first['rows']
    warehouse.cleanup(e, include_previous=True)
    assert warehouse.verify(e)['run_id'] == second['run_id']


def test_failed_publication_is_atomic(lake, monkeypatch):
    e, root = lake
    first = warehouse.run(e, root)
    etl.run(e, root)
    def rollback_publish(c, schema, *args):
        with c.begin():
            c.exec_driver_sql(f'CREATE OR REPLACE VIEW analytics.dim_channel AS SELECT * FROM "{schema}".dim_channel')
            raise RuntimeError('publication failure')
    with monkeypatch.context() as m:
        m.setattr(warehouse, 'publish', rollback_publish)
        with pytest.raises(RuntimeError): warehouse.run(e, root)
    assert warehouse.verify(e)['run_id'] == first['run_id']
    assert scalar(e, 'SELECT count(*) FROM analytics.dim_channel') == 3


def test_zero_footfall_negative_adjustment_and_missing_policy(lake):
    e, root = lake
    warehouse.run(e, root)
    # Mutate only the disposable warehouse, not operational business records.
    schema = warehouse.status(e)['runs'][0]['build_schema']
    with e.begin() as c:
        c.exec_driver_sql(f'UPDATE "{schema}".fact_footfall_daily SET valid_entries=0,completed_orders=0,conversion=NULL')
        c.exec_driver_sql(f'UPDATE "{schema}".fact_inventory_daily SET safety_stock=NULL,available_to_promise=NULL')
        c.exec_driver_sql(f"INSERT INTO \"{schema}\".fact_inventory_movements SELECT '2020-01-01',min(sku_id),min(location_id),'STOCK_ADJUSTMENT','SELLABLE',-1 FROM \"{schema}\".fact_inventory_daily")
    assert all(row['conversion_percent'] is None for row in kpis.report(e, 'footfall', '2020-01-01', '2030-12-31')['rows'])
    assert all(row['stock_available'] is None for row in kpis.report(e, 'inventory', '2020-01-01', '2030-12-31')['rows'])
    pool = kpis.report(e, 'sell-through', '2020-02-01', '2020-02-28')['rows'][0]
    assert pool['available_pool'] == -1 and pool['sell_through_percent'] is None


def test_disconnected_copy_does_not_publish(lake, monkeypatch):
    e, root = lake
    first = warehouse.run(e, root)
    etl.run(e, root)
    original = warehouse.copy_table
    def disconnect(c, *args):
        original(c, *args)
        c.connection.driver_connection.close()
        raise ConnectionError('simulated disconnect after committed private COPY')
    with monkeypatch.context() as m:
        m.setattr(warehouse, 'copy_table', disconnect)
        with pytest.raises(ConnectionError): warehouse.run(e, root)
    assert warehouse.verify(e)['run_id'] == first['run_id']
    assert warehouse.cleanup(e)['removed_unpublished_builds']
    assert warehouse.run(e, root)['status'] == 'completed'
