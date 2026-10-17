from datetime import date
from decimal import Decimal
import shutil

import pytest
from retailpulse import kpis
from retailpulse.dashboard import data, filters, formatting, queries


@pytest.fixture(scope='module')
def meta(): return data.metadata('demo')


def report(meta,page='overview',selected=None,start='2025-01-01',end='2026-07-31',grain='month'):
    return data.payload('demo',page,start,end,selected or {},meta['run_id'],grain)


def test_demo_matches_precomputed_stage4_reconciliation(meta):
    row=report(meta)['summary']
    expected=data.validate_demo()['expected']
    assert row['orders']==expected['orders']==500
    for key in ('gmv','net_sales','net_cogs','gross_profit'):
        assert row[key]==Decimal(expected[key])
    assert row['net_sales']==row['gross_sales']-row['merchandise_credits']
    assert row['gross_profit']==row['net_sales']-row['net_cogs']
    assert row['revenue_growth_percent'] is None  # Prior history isn't available.


@pytest.mark.parametrize('page',['overview','stores','products','customers','health'])
def test_all_demo_pages(meta,page):
    result=report(meta,page)
    assert result['lineage']['snapshot_id']==meta['snapshot_id']
    if page=='health': assert result['history']==[]


@pytest.mark.parametrize('selected',[{'channel':'WEB'},{'store_id':1},{'category_id':1},{'sku_id':1},{'state_id':1},{'city':"unknown' OR 1=1 --"}])
def test_filters_and_contribution_totals(meta,selected):
    result=report(meta,selected=selected)
    assert sum(r['net_sales'] for r in result['categories'])==result['summary']['net_sales']
    assert sum(r['net_sales'] for r in result['channels'])==result['summary']['net_sales']
    if 'city' in selected: assert result['summary']['orders']==0


@pytest.mark.parametrize('grain',['day','week','month'])
def test_date_boundaries_and_no_double_counting(meta,grain):
    result=report(meta,start='2025-10-02',end='2025-11-12',grain=grain)
    assert sum(r['net_sales'] for r in result['trend'])==result['summary']['net_sales']
    assert sum(r['orders'] for r in result['order_trend'])==result['summary']['orders']
    if grain=='month': assert all(r['previous_period_growth_percent'] is None for r in result['trend'])


def test_empty_scope_and_customer_denominators(meta):
    result=report(meta,'customers',selected={'sku_id':99999})
    assert result['summary']['net_sales']==0
    assert result['summary']['booked_aov'] is None
    assert result['customer_summary'][0]['purchasing_customers']==0
    assert result['customer_summary'][0]['orders_per_customer'] is None
    assert not result['cohorts']


def test_store_and_product_unsupported_denominators_not_silently_unfiltered(meta):
    assert report(meta,'stores',{'channel':'WEB'})['footfall']==[]
    assert report(meta,'stores',{'sku_id':1})['footfall']==[]
    assert report(meta,'products',{'channel':'POS'})['inventory']==[]
    assert report(meta,'products',{'state_id':1})['sell_through']==[]
    assert report(meta,'customers',{'sku_id':1})['refunds']==[]
    assert report(meta,'stores',{'channel':'POS'})['footfall']


def test_customer_history_and_cohorts(meta):
    result=report(meta,'customers')
    assert sum(r['customers'] for r in result['segments'])==50
    assert sum(r['orders'] for r in result['segments'])==500
    assert sum(r['orders'] for r in result['outcomes'])==500
    assert result['customer_summary'][0]['purchasing_customers']==50
    assert all(r['cohort_month']<=r['activity_month'] for r in result['cohorts'])


def test_inventory_is_one_observed_date_not_sum_of_snapshots(meta):
    result=report(meta,'products')
    assert len({r['snapshot_date'] for r in result['inventory']})==1
    assert all((r['available_to_promise'] is None if r['safety_stock'] is None else r['available_to_promise']==r['sellable']-r['reserved']-r['safety_stock']) for r in result['inventory'])
    assert len({(r['sku_id'],r['location_id']) for r in result['inventory']})==len(result['inventory'])


def test_dependent_filters(meta):
    stores=filters.store_options(meta,state_id=1)
    assert all(r['state_id']==1 and r['type']=='STORE' for r in stores)
    assert filters.store_options(meta,channel='WEB')==[]
    products=filters.product_options(meta,category_id=1)
    assert products and all(p['category_id']==1 for p in products)


@pytest.mark.parametrize('value,expected',[(Decimal('12345678.9'),'₹1,23,45,678.90'),(-12.5,'₹-12.50'),(None,'—'),(Decimal('1.005'),'₹1.01')])
def test_indian_currency(value,expected): assert formatting.currency(value)==expected


def test_percent_number_and_csv():
    assert formatting.percent(0)=='0.00%'
    assert formatting.percent(None)=='—'
    assert formatting.number(100000)=='1,00,000'
    assert formatting.currency(10000000,True)=='₹1.00 Cr'
    csv=formatting.csv_bytes([{'label':'=1+1','net_sales':Decimal('-1.23')}]).decode('utf-8-sig')
    assert "'=1+1" in csv and '-1.23' in csv


def test_corrupt_demo_rejected(tmp_path):
    target=tmp_path/'bundle';shutil.copytree(data.DEMO_ROOT,target)
    path=target/'fact_sales.parquet';path.write_bytes(path.read_bytes()+b'bad')
    with pytest.raises(ValueError,match='checksum'): data.validate_demo(target)


def test_snapshot_change_rejected(meta):
    with pytest.raises(data.SnapshotChanged): data.payload('demo','overview','2025-01-01','2026-01-01',{},'wrong')


def test_invalid_mode_and_local_connection_failure():
    with pytest.raises(ValueError): data.metadata('invalid')
    class Broken:
        def connect(self): raise ConnectionError('simulated offline database')
    with pytest.raises(ConnectionError): data.metadata('local',Broken())


def test_stage4_query_builder_keeps_parameters_bound():
    sql,params=kpis.build_query('sales','2025-01-01','2025-01-31',{'city':"x'; DROP SCHEMA source;--"})
    assert params['city'] not in sql and ':city' in sql
    assert params['start']==date(2025,1,1)
    with pytest.raises(ValueError): queries.query('order_trend','2025-01-01','2025-01-31',{},"month'); DROP SCHEMA analytics;")
