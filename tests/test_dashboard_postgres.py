"""Native adapter parity tests use only the established disposable database."""
import pytest
from retailpulse import warehouse, kpis
from retailpulse.dashboard import data
from test_warehouse_postgres import database, pg, loaded, lake

pytestmark=pytest.mark.integration


def test_dashboard_matches_stage4_and_all_pages(lake):
    engine,root=lake
    warehouse.run(engine,root)
    meta=data.metadata('local',engine)
    before=warehouse.verify(engine)
    for selected in ({},{'channel':'WEB'},{'store_id':1},{'sku_id':1}):
        result=data.payload('local','overview','2025-01-01','2026-07-31',selected,meta['run_id'],engine=engine)
        expected=kpis.report(engine,'sales','2025-01-01','2026-07-31',selected)['rows'][0]
        for field in expected: assert result['summary'][field]==expected[field]
    for page in ('stores','products','customers','health'):
        result=data.payload('local',page,'2025-01-01','2026-07-31',{},meta['run_id'],engine=engine)
        assert result['lineage']['snapshot_id']==meta['snapshot_id']
    assert warehouse.verify(engine)==before
    with data.session('local',engine) as s:
        assert s.rows('SHOW transaction_read_only')[0]['transaction_read_only']=='on'


def test_demo_native_money_and_customer_parity(lake):
    engine,root=lake
    warehouse.run(engine,root)
    local=data.metadata('local',engine);demo=data.metadata('demo')
    for page in ('overview','customers','stores','products'):
        a=data.payload('local',page,'2025-01-01','2026-07-31',{},local['run_id'],engine=engine)
        b=data.payload('demo',page,'2025-01-01','2026-07-31',{},demo['run_id'])
        for key in ('gmv','discounts','net_sales','net_cogs','gross_profit','orders','net_units'):
            assert a['summary'][key]==b['summary'][key]
        if page=='customers':
            left,right=a['customer_summary'][0],b['customer_summary'][0]
            for key in ('ordering_customers','identified_orders','purchasing_customers'):
                assert left[key]==right[key]
            assert float(left['orders_per_customer'])==pytest.approx(float(right['orders_per_customer']))
            assert sorted(a['outcomes'],key=lambda r:r['outcome'])==sorted(b['outcomes'],key=lambda r:r['outcome'])
        if page=='products':
            assert sum(r['sellable'] for r in a['inventory'])==sum(r['sellable'] for r in b['inventory'])


def test_small_synthetic_export_and_readonly_safety(lake, tmp_path):
    import importlib.util
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('demo_export',Path(__file__).resolve().parents[1]/'scripts/export_dashboard_demo.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    engine,root=lake
    warehouse.run(engine,root)
    target=tmp_path/'demo-export'
    with pytest.raises(ValueError,match='acknowledgement'):
        module.export(engine,target)
    manifest=module.export(engine,target,True)
    assert int(manifest['expected']['orders'])==500
    assert data.validate_demo(target)['synthetic'] is True
    meta=data.metadata('demo',root=target)
    result=data.payload('demo','overview','2025-01-01','2026-07-31',{},meta['run_id'],root=target)
    assert result['summary']['net_sales']==manifest['expected']['net_sales']
    with pytest.raises(ValueError,match='new directory'):
        module.export(engine,target,True)
