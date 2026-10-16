"""Data checks independent of PostgreSQL; money stays Decimal throughout."""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import pandas as pd
from .money import tax_rule, tax_components, money, allocate

class VerificationError(ValueError):
    pass

def load_dataset(path):
    path=Path(path)
    manifest=json.loads((path/'manifest.json').read_text())
    if not manifest.get('synthetic'): raise VerificationError('Missing synthetic provenance')
    tables={}
    for name,meta in manifest['tables'].items():
        if not name.replace('_','').isalnum(): raise VerificationError('Invalid table name')
        file=path/f'{name}.parquet'
        if hashlib.sha256(file.read_bytes()).hexdigest()!=meta['sha256']:
            raise VerificationError(f'Checksum mismatch: {name}')
        frame=pd.read_parquet(file)
        if len(frame)!=meta['rows']: raise VerificationError(f'Row count mismatch: {name}')
        tables[name]=frame
    return manifest,tables

def validate(tables, expected_orders=None):
    t={k:(v if isinstance(v,pd.DataFrame) else pd.DataFrame(v)) for k,v in tables.items()}
    failures=[]; checks=[]
    def check(name, valid):
        checks.append(name)
        if not bool(valid): failures.append(name)
    def rows(name): return t.get(name,pd.DataFrame()).to_dict('records')
    def indexed(name,key): return {r[key]:r for r in rows(name)}
    orders=indexed('sales_orders','order_id'); lines=indexed('order_lines','order_line_id')
    fi=indexed('fulfillment_items','fulfillment_item_id'); fs=indexed('fulfillments','fulfillment_id')
    invoices=indexed('sales_invoices','invoice_id'); il=indexed('invoice_lines','invoice_line_id')
    returns=indexed('return_items','return_item_id'); rr=indexed('return_requests','return_id')
    lots=indexed('stock_lots','lot_id'); prices=indexed('price_versions','price_id')
    locations=indexed('locations','location_id'); events=indexed('source_events','event_id')
    check('order_count',len(orders)==(expected_orders if expected_orders is not None else len(orders)))
    check('all_channels',{o['channel'] for o in orders.values()}=={'POS','WEB','APP'})
    for name,frame in t.items():
        id_cols=[c for c in frame if c.endswith('_id')]
        if id_cols and name not in ['inventory_policies','inventory_snapshots','store_footfall_daily','digital_traffic_daily']:
            check(f'{name}_primary_key',not frame[id_cols[0]].duplicated().any())
    for name,key in [('sales_orders','external_reference'),('source_events','source_identity'),('skus','barcode'),('payment_transactions','provider_reference')]:
        check(f'{name}_business_key',not t[name][key].duplicated().any())
    check('event_lineage',all(r['event_id'] in events for name in t for r in rows(name) if name!='source_events' and 'event_id' in r))
    check('batch_lineage',all(r['batch_id']==1 for r in events.values()))
    discounts=defaultdict(lambda: money(0)); cancelled=defaultdict(int); fulfilled=defaultdict(int)
    for r in rows('order_line_discounts'): discounts[r['order_line_id']]+=r['amount']
    for r in rows('order_line_cancellations'): cancelled[r['order_line_id']]+=r['quantity']
    for r in fi.values(): fulfilled[r['order_line_id']]+=r['quantity']
    check('quantities',all(l['quantity']>0 and fulfilled[k]+cancelled[k]==l['quantity'] for k,l in lines.items()))
    check('discount_limits',all(0<=discounts[k]<=l['quantity']*l['original_unit_price'] for k,l in lines.items()))
    check('fulfillment_ownership',all(lines[r['order_line_id']]['order_id']==fs[r['fulfillment_id']]['order_id'] for r in fi.values()))
    check('historical_prices',all(prices[l['price_id']]['list_price']==l['original_unit_price'] and prices[l['price_id']]['sku_id']==l['sku_id']
                                 and prices[l['price_id']]['channel']==orders[l['order_id']]['channel'] for l in lines.values()))
    check('invoice_ownership',all(fi[r['fulfillment_item_id']]['fulfillment_id']==invoices[r['invoice_id']]['fulfillment_id'] and
                                 fs[invoices[r['invoice_id']]['fulfillment_id']]['order_id']==invoices[r['invoice_id']]['order_id'] for r in il.values()))
    valid_tax=True; valid_mrp=True
    for r in il.values():
        inv=invoices[r['invoice_id']]; f=fs[inv['fulfillment_id']]; order=orders[inv['order_id']]
        rule,rate=tax_rule(inv['invoice_date'],r['taxable_value']/r['quantity'])
        interstate=locations[f['source_location_id']]['state_id']!=order['place_of_supply_state_id']
        valid_tax &= rule==r['tax_rule_id'] and tax_components(r['taxable_value'],rate,interstate)==(r['cgst'],r['sgst'],r['igst'])
        line=lines[fi[r['fulfillment_item_id']]['order_line_id']]
        valid_mrp &= sum(r[k] for k in ['taxable_value','cgst','sgst','igst'])<=prices[line['price_id']]['mrp']*r['quantity']
    invoiced_by_line=defaultdict(lambda: money(0))
    for row in il.values(): invoiced_by_line[fi[row['fulfillment_item_id']]['order_line_id']]+=row['taxable_value']
    line_values={k:allocate(l['quantity']*l['original_unit_price']-discounts[k],[l['original_unit_price']]*l['quantity']) for k,l in lines.items()}
    check('discounted_invoice_reconciliation',all(invoiced_by_line[k]==sum(values[cancelled[k]:]) for k,values in line_values.items()))
    coupon_by_order=defaultdict(lambda: money(0))
    for row in rows('order_line_discounts'):
        if row['promotion_id']==2: coupon_by_order[lines[row['order_line_id']]['order_id']]+=row['amount']
    check('exact_cart_coupons',all(value==100 for value in coupon_by_order.values()))
    check('tax_rules_and_components',valid_tax); check('mrp_ceiling',valid_mrp)
    returned=defaultdict(int); credit_totals=defaultdict(lambda: [0,money(0),money(0),money(0),money(0)])
    for r in returns.values(): returned[r['invoice_line_id']]+=r['quantity']
    check('return_quantity',all(q<=il[k]['quantity'] for k,q in returned.items()))
    check('return_window',all(fs[invoices[il[r['invoice_line_id']]['invoice_id']]['fulfillment_id']]['delivered_at'] <= rr[r['return_id']]['requested_at'] <=
                             fs[invoices[il[r['invoice_line_id']]['invoice_id']]['fulfillment_id']]['delivered_at']+timedelta(days=14) for r in returns.values()))
    credits=indexed('credit_notes','credit_note_id')
    for r in rows('credit_note_lines'):
        for i,k in enumerate(['quantity','taxable_value','cgst','sgst','igst']): credit_totals[r['invoice_line_id']][i]+=r[k]
    check('credit_original_amounts',all(all(v<=il[k][col] for v,col in zip(values,['quantity','taxable_value','cgst','sgst','igst'])) for k,values in credit_totals.items()))
    check('credit_lineage',all(returns[r['return_item_id']]['invoice_line_id']==r['invoice_line_id'] and
                             returns[r['return_item_id']]['return_id']==credits[r['credit_note_id']]['return_id'] for r in rows('credit_note_lines')))
    pay=indexed('payment_transactions','payment_txn_id'); refunds=defaultdict(lambda: money(0))
    for r in pay.values():
        if r['type']=='REFUND' and r['status']=='SUCCEEDED': refunds[r['original_transaction_id']]+=r['amount']
    check('refund_limit',all(value<=pay[k]['amount'] and pay[k]['type']=='CAPTURE' and pay[k]['status']=='SUCCEEDED' for k,value in refunds.items()))
    check('refund_routes',all(r['order_id']==pay[r['original_transaction_id']]['order_id'] and r['method']==pay[r['original_transaction_id']]['method'] for r in pay.values() if r['type']=='REFUND'))
    credit_values=defaultdict(lambda: money(0)); credit_paid=defaultdict(lambda: money(0))
    for row in rows('credit_note_lines'):
        credit_values[row['credit_note_id']]+=sum(row[k] for k in ['taxable_value','cgst','sgst','igst'])
    for row in pay.values():
        if row['type']=='REFUND' and row['status']=='SUCCEEDED' and pd.notna(row['credit_note_id']):
            credit_paid[row['credit_note_id']]+=row['amount']
    check('credit_refund_limits',all(value<=credit_values[k] for k,value in credit_paid.items()))
    allocations=indexed('fulfillment_cost_allocations','allocation_id'); costs=defaultdict(lambda: money(0)); cost_qty=defaultdict(int)
    for r in allocations.values(): costs[r['fulfillment_item_id']]+=r['quantity']*r['unit_cost']; cost_qty[r['fulfillment_item_id']]+=r['quantity']
    check('cost_quantities',all(cost_qty[k]==r['quantity'] for k,r in fi.items()))
    check('historical_cost',all(r['unit_cost']==lots[r['lot_id']]['unit_cost'] for r in allocations.values()))
    check('stock_lot_sku',all(lots[r['lot_id']]['sku_id']==r['sku_id'] for r in rows('inventory_movements')))
    movements=t['inventory_movements'].sort_values(['event_at','movement_id'])
    balances=movements.groupby(['sku_id','location_id','lot_id','stock_state']).quantity.cumsum()
    check('no_negative_lot_inventory',balances.min()>=0)
    # Monthly snapshots must be derived from the ledger, including transit and damaged stock.
    moves=movements.copy(); moves['day']=pd.to_datetime(moves.event_at,utc=True).dt.tz_convert('Asia/Kolkata').dt.date
    snapshot_ok=True
    for day,snap in t['inventory_snapshots'].groupby('snapshot_date'):
        agg=moves[moves.day<=day].groupby(['sku_id','location_id','stock_state']).quantity.sum().to_dict()
        for r in snap.to_dict('records'):
            for column,state in [('sellable','SELLABLE'),('in_transit','TRANSIT'),('damaged','DAMAGED')]:
                snapshot_ok &= r[column]==agg.get((r['sku_id'],r['location_id'],state),0)
            snapshot_ok &= r['reserved']==0
    check('snapshot_reconciliation',snapshot_ok)
    pos=defaultdict(int); digital=defaultdict(int)
    completed={r['order_id'] for r in rows('order_status_events') if r['new_status']=='COMPLETED'}
    for o in orders.values():
        day=o['confirmed_at'].astimezone(__import__('zoneinfo').ZoneInfo('Asia/Kolkata')).date()
        if o['channel']=='POS' and o['order_id'] in completed: pos[(o['originating_store_id'],day)]+=1
        elif o['channel']!='POS': digital[(o['channel'],day)]+=1
    check('store_traffic',all(r['valid_entries']>=pos[(r['store_id'],r['business_date'])] for r in rows('store_footfall_daily')))
    check('digital_traffic',all(r['eligible_sessions']>=digital[(r['channel'],r['business_date'])] for r in rows('digital_traffic_daily')))
    golden=next(o for o in orders.values() if o['external_reference']=='SYNTH-GOLDEN-001')
    oid=golden['order_id']; gl=[l for l in lines.values() if l['order_id']==oid]
    gi=[r for r in il.values() if invoices[r['invoice_id']]['order_id']==oid]
    gc=[r for r in rows('credit_note_lines') if credits[r['credit_note_id']]['return_id'] in [k for k,r in rr.items() if r['order_id']==oid]]
    gmv=sum(l['quantity']*l['original_unit_price'] for l in gl); discount=sum(discounts[l['order_line_id']] for l in gl)
    gross=sum(r['taxable_value'] for r in gi); credited=sum(r['taxable_value'] for r in gc)
    sold_cost=sum(costs[r['fulfillment_item_id']] for r in gi)
    reversed_cost=sum(r['quantity']*allocations[r['allocation_id']]['unit_cost'] for r in rows('return_cost_allocations') if rr[returns[r['return_item_id']]['return_id']]['order_id']==oid and returns[r['return_item_id']]['disposition']=='RESTOCK')
    settled=sum(r['amount'] for r in pay.values() if r['order_id']==oid and r['type']=='REFUND' and r['status']=='SUCCEEDED')
    golden_values={'gmv':gmv,'discount':discount,'gross':gross,'tax':sum(r['cgst']+r['sgst']+r['igst'] for r in gi),
                   'net_sales':gross-credited,'net_cogs':sold_cost-reversed_cost,'refund':settled,
                   'net_units':sum(r['quantity'] for r in gi)-sum(r['quantity'] for r in gc)}
    check('golden_transaction',golden_values=={'gmv':6000,'discount':1200,'gross':4800,'tax':240,'net_sales':2400,'net_cogs':1200,'refund':2520,'net_units':1})
    if failures: raise VerificationError('Failed: '+', '.join(failures))
    return {'checks_passed':len(checks),'checks':checks,'orders':len(orders),'golden':{k:str(v) for k,v in golden_values.items()}}
