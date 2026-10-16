from collections import Counter
from decimal import Decimal
import pandas as pd
import pytest
from retailpulse.generate import Generator, Config, generate
from retailpulse.verify import validate, load_dataset, VerificationError

def test_dataset_reconciles(dataset):
    report=validate(dataset,1000)
    assert report['golden']['net_sales']=='2400.00'
    assert report['golden']['net_cogs']=='1200.00'

def test_required_exception_scenarios(dataset):
    f=Counter(x['order_id'] for x in dataset['fulfillments'])
    assert max(f.values())>=2
    assert any(x['status']=='FAILED' for x in dataset['payment_transactions'])
    assert any(x['condition']=='DAMAGED' for x in dataset['return_items'])
    assert any(x['valid_entries']==0 for x in dataset['store_footfall_daily'])
    cancelled={x['order_line_id'] for x in dataset['order_line_cancellations']}
    fulfilled={x['order_line_id'] for x in dataset['fulfillment_items']}
    assert cancelled
    # Partial cancellation at order level (not necessarily within the same line).
    lines={x['order_line_id']:x['order_id'] for x in dataset['order_lines']}
    assert {lines[x] for x in cancelled} & {lines[x] for x in fulfilled}

def test_damaged_returns_do_not_restock(dataset):
    damaged={x['return_item_id'] for x in dataset['return_items'] if x['condition']=='DAMAGED'}
    assert damaged
    movements=[x for x in dataset['inventory_movements'] if x['return_item_id'] in damaged]
    assert all(x['stock_state']=='DAMAGED' and x['type']=='DAMAGE' for x in movements)

def test_fifo_cost_allocation(dataset):
    # Replay sellable movements by time; the oldest available lot must be taken.
    lots={x['lot_id']:x for x in dataset['stock_lots']}; balances=Counter()
    for r in sorted(dataset['inventory_movements'],key=lambda x:(x['event_at'],x['movement_id'])):
        if r['stock_state']!='SELLABLE': continue
        key=(r['sku_id'],r['location_id'],r['lot_id'])
        if r['quantity']<0:
            candidates=[k[2] for k,v in balances.items() if k[:2]==key[:2] and v>0]
            assert r['lot_id']==min(candidates,key=lambda k:(lots[k]['receipt_at'],k))
        balances[key]+=r['quantity']

def test_seed_reproducibility(tmp_path):
    cfg=Config(orders=40,styles=2,customers=10)
    a=generate(cfg,tmp_path/'a'); b=generate(cfg,tmp_path/'b')
    assert a==b
    manifest,tables=load_dataset(tmp_path/'a')
    assert manifest['config']['orders']==40
    with pytest.raises(FileExistsError): generate(cfg,tmp_path/'a')
    path=tmp_path/'a'/'sales_orders.parquet'
    path.write_bytes(path.read_bytes()+b'broken')
    with pytest.raises(VerificationError,match='Checksum'): load_dataset(tmp_path/'a')

def test_verifier_detects_corruption(dataset):
    altered=dict(dataset)
    altered['inventory_snapshots']=[dict(r) for r in dataset['inventory_snapshots']]
    altered['inventory_snapshots'][0]['sellable']+=1
    with pytest.raises(VerificationError,match='snapshot_reconciliation'): validate(altered,1000)

def test_failure_refunds_are_not_settled(dataset):
    payments=dataset['payment_transactions']
    failed=[p for p in payments if p['type']=='REFUND' and p['status']=='FAILED']
    assert failed and all(p['amount']>0 for p in failed)
    references=[p['provider_reference'] for p in payments]
    assert len(references)==len(set(references))

def test_online_stock_recognized_at_delivery(dataset):
    fs={r['fulfillment_id']:r for r in dataset['fulfillments']}
    items={r['fulfillment_item_id']:r for r in dataset['fulfillment_items']}
    for r in dataset['inventory_movements']:
        if r['type']=='SALE':
            f=fs[items[r['fulfillment_item_id']]['fulfillment_id']]
            assert r['event_at']==f['delivered_at']
            if f['type']=='SHIPMENT': assert r['stock_state']=='TRANSIT'

def test_captured_totals_match_discounted_booking(dataset):
    from retailpulse.money import allocate, money, tax_rule, tax_components
    discounts=Counter()
    for r in dataset['order_line_discounts']: discounts[r['order_line_id']]+=r['amount']
    expected=Counter(); orders={r['order_id']:r for r in dataset['sales_orders']}
    for line in dataset['order_lines']:
        net=line['quantity']*line['original_unit_price']-discounts[line['order_line_id']]
        for value in allocate(net,[line['original_unit_price']]*line['quantity']):
            rate=tax_rule(orders[line['order_id']]['confirmed_at'].date(),value)[1]
            expected[line['order_id']]+=value+money(value*rate)
    for payment in dataset['payment_transactions']:
        if payment['type']=='CAPTURE': assert payment['amount']==expected[payment['order_id']]
