"""Deterministic discrete-event generator. All behavior is fictional."""
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib
import heapq
import json
import platform
from importlib.metadata import version

from faker import Faker
import numpy as np
import pandas as pd

from .money import money, allocate, tax_rule, tax_components

IST = ZoneInfo('Asia/Kolkata')

@dataclass(frozen=True)
class Config:
    orders: int = 100_000
    seed: int = 42
    start: str = '2025-01-01'
    end: str = '2026-06-30'
    styles: int = 40
    customers: int = 20_000

class Generator:
    def __init__(self, config):
        self.config = config
        self.rng = np.random.default_rng(config.seed)
        self.fake = Faker('en_IN')
        self.fake.seed_instance(config.seed)
        self.tables = defaultdict(list)
        self.ids = defaultdict(int)
        self.stock = defaultdict(list)
        self.queue = []
        self.sequence = 0
        self.counts = defaultdict(int)
        self.prices = {}
        self.costs = {}
        self.start = date.fromisoformat(config.start)
        self.end = date.fromisoformat(config.end)
        if config.orders < 10 or config.styles < 2 or config.customers < 1 or self.end < self.start:
            raise ValueError('Need >=10 orders, >=2 styles, >=1 customer and a valid date range')
        if not self.start <= date(2025,10,1) <= self.end:
            raise ValueError('Date range must include 2025-10-01 for the golden transaction')
        self.asof = datetime.combine(self.end + timedelta(days=31), time.max, IST)

    def add(self, table, pk=None, **row):
        if pk:
            self.ids[table] += 1
            row = {pk: self.ids[table], **row}
        self.tables[table].append(row)
        return row

    def event(self, kind, when):
        n = self.ids['source_events']+1
        return self.add('source_events','event_id',source_identity=f'SYNTH-{kind}-{n}',source_system='retailpulse-v1',
                        event_type=kind,event_at=when,batch_id=1)['event_id']

    def schedule(self, when, callback, *args):
        self.sequence += 1
        heapq.heappush(self.queue,(when,self.sequence,callback,args))

    def flush(self, when):
        while self.queue and self.queue[0][0] <= when:
            at, _, callback, args = heapq.heappop(self.queue)
            callback(at,*args)

    def movement(self, sku, loc, lot, qty, kind, at, state='SELLABLE', item=None, returned=None):
        self.add('inventory_movements','movement_id',sku_id=sku,location_id=loc,lot_id=lot,quantity=qty,
                 type=kind,stock_state=state,event_at=at,event_id=self.event(kind,at),
                 fulfillment_item_id=item,return_item_id=returned)

    def receive(self, sku, loc, qty, at, opening=False):
        cost = self.costs[sku] if opening or sku==1 else money(self.costs[sku] * Decimal(str(self.rng.choice([.96,1,1.04]))))
        lot = self.add('stock_lots','lot_id',sku_id=sku,receipt_reference=f'SYNTH-RECEIPT-{self.ids["stock_lots"]+1}',
                       receipt_at=at,initial_quantity=qty,unit_cost=cost)
        self.stock[(sku,loc)].append([lot['lot_id'],qty,cost,at])
        self.movement(sku,loc,lot['lot_id'],qty,'OPENING_BALANCE' if opening else 'PURCHASE_RECEIPT',at)

    def available(self, sku, loc):
        return sum(x[1] for x in self.stock[(sku,loc)]) - 1

    def take(self, sku, loc, qty):
        result=[]
        for lot in sorted(self.stock[(sku,loc)],key=lambda x:(x[3],x[0])):
            n=min(qty,lot[1])
            if n:
                lot[1]-=n; qty-=n
                result.append((lot[0],n,lot[2]))
            if not qty: break
        if qty: raise ValueError('Insufficient FIFO stock')
        return result

    def masters(self):
        at=datetime.combine(self.start,time(0),IST)
        cities=['Bengaluru','Mumbai','Delhi','Hyderabad','Pune','Chennai']
        states=[('Karnataka','29'),('Maharashtra','27'),('Delhi','07'),('Telangana','36'),('Tamil Nadu','33')]
        city_states=[1,2,3,4,2,5]
        for i,(name,code) in enumerate(states,1):
            self.add('states',state_id=i,name=name,gst_state_code=code)
            self.add('gst_registrations',registration_id=i,state_id=i,synthetic_gstin=f'SYNTH-GST-{i:02}',status='ACTIVE')
        self.loc_states={}
        for i in range(1,16):
            ci=((i-1)//2)%6 if i<=12 else (0 if i==13 else 2)
            state=city_states[ci]; self.loc_states[i]=state
            self.add('locations',location_id=i,state_id=state,registration_id=state,name=f'Synthetic {cities[ci]} {i}',
                     city=cities[ci],type='STORE' if i<=12 else ('FULFILLMENT_CENTER' if i<=14 else 'TRANSIT'),opening_date=self.start)
        for i in range(1,self.config.customers+1):
            self.add('customers',customer_id=i,customer_reference=f'SYNTH-CUSTOMER-{i:06}',synthetic_name='Synthetic '+self.fake.name(),created_at=at)
        cats=['T-shirts','Shirts','Jeans','Trousers','Dresses','Kurtas','Jackets','Other apparel']
        for i,name in enumerate(cats,1): self.add('categories',category_id=i,parent_category_id=None,name=name)
        for style in range(1,self.config.styles+1):
            self.add('product_styles',style_id=style,category_id=(style-1)%8+1,style_code=f'SYNTH-STYLE-{style}',
                     name=f'Synthetic {cats[(style-1)%8]} {style}',hsn='6205',collection='Everyday' if style%2 else 'Festive',launch_date=self.start)
            for color in ['Navy','Ivory']:
                for size in ['S','M','L']:
                    sku=self.add('skus','sku_id',style_id=style,barcode=f'SYNTH-SKU-{self.ids["skus"]+1}',color=color,size=size,active=True)['sku_id']
                    price=money(3000 if sku==1 else int(self.rng.choice([799,999,1000,1499,1999,2500,2999,3999])))
                    self.prices[sku]=price; self.costs[sku]=money(1200 if sku==1 else price*Decimal('.45'))
                    for ch in ['POS','WEB','APP']:
                        self.add('price_versions','price_id',sku_id=sku,channel=ch,list_price=price,mrp=money(price*Decimal('1.18')),
                                 effective_from=self.start,effective_to=self.end+timedelta(days=90))
                    for loc in range(1,15):
                        self.add('inventory_policies',sku_id=sku,location_id=loc,safety_stock=1)
                        self.receive(sku,loc,int(self.rng.integers(3,10)),at,True)
        for i,(low,high,rate,begin,end) in enumerate([
            (-1,1000,'.05',date(2020,1,1),date(2025,9,22)),(1000,999999,'.12',date(2020,1,1),date(2025,9,22)),
            (-1,2500,'.05',date(2025,9,22),date(2030,1,1)),(2500,999999,'.18',date(2025,9,22),date(2030,1,1))],1):
            self.add('tax_rules',tax_rule_id=i,hsn='6205',lower_exclusive=money(low),upper_inclusive=money(high),gst_rate=Decimal(rate),effective_from=begin,effective_to=end)
        self.add('promotions',promotion_id=1,code='SYNTH-MARKDOWN20',type='PERCENT',value=money(20),eligibility_rules='All apparel; seeded campaign selection',valid_from=self.start,valid_to=self.end)
        self.add('promotions',promotion_id=2,code='SYNTH-CART100',type='FIXED',value=money(100),eligibility_rules='All lines after markdown; basket >= INR 100',valid_from=self.start,valid_to=self.end)

    def payment(self, order, amount, at, kind='CAPTURE', parent=None, credit=None, failed=False):
        method=parent['method'] if parent else str(self.rng.choice(['UPI','CARD','NETBANKING'] if order['channel']!='POS' else ['UPI','CARD','CASH']))
        row=self.add('payment_transactions','payment_txn_id',order_id=order['order_id'],type=kind,method=method,amount=amount,
                     status='FAILED' if failed else 'SUCCEEDED',provider_reference=f'SYNTH-PAY-{self.ids["payment_transactions"]+1}',
                     original_transaction_id=parent['payment_txn_id'] if parent else None,credit_note_id=credit,
                     event_at=at,event_id=self.event(kind,at))
        statuses=['INITIATED','APPROVED','PROCESSING',row['status']] if kind=='REFUND' else ['INITIATED',row['status']]
        for st in statuses:
            self.add('payment_status_events','payment_status_event_id',payment_txn_id=row['payment_txn_id'],status=st,event_at=at)
        return row

    def status(self, order, statuses, at):
        old=None
        for st in statuses:
            self.add('order_status_events','status_event_id',order_id=order['order_id'],old_status=old,new_status=st,
                     event_at=at,event_id=self.event('ORDER_STATUS',at))
            old=st

    def order(self, at, golden=False):
        ch='POS' if golden else str(self.rng.choice(['POS','WEB','APP'],p=[.6,.24,.16]))
        # Store 12 is deliberately closed on day 1 of each month.
        store=1 if golden else int(self.rng.integers(1,12 if at.day==1 else 13))
        loc=store if ch=='POS' else int(self.rng.choice([13,14]))
        pos_state=self.loc_states[loc] if ch=='POS' else int(self.rng.integers(1,6))
        basket=[1,1] if golden else [int(x) for x in self.rng.integers(1,len(self.prices)+1,size=int(self.rng.choice([1,2,3,4],p=[.30,.40,.20,.10])))]
        # Reject unavailable demand BEFORE confirmation; substitute an available SKU.
        chosen=defaultdict(int)
        for sku in basket:
            if golden and self.available(sku,loc)<2:
                self.receive(sku,loc,4,at)
            if self.available(sku,loc)-chosen.get(sku,0)<1:
                self.add('demand_rejections','rejection_id',sku_id=sku,location_id=loc,event_at=at,quantity=1,
                         available_to_promise=max(0,self.available(sku,loc)-chosen.get(sku,0)),reason='OUT_OF_STOCK')
                candidates=[s for s in self.prices if self.available(s,loc)-chosen.get(s,0)>0]
                if not candidates: self.receive(sku,loc,8,at); candidates=[sku]
                sku=int(self.rng.choice(candidates))
            chosen[sku]+=1
        order=self.add('sales_orders','order_id',external_reference='SYNTH-GOLDEN-001' if golden else f'SYNTH-ORDER-{self.ids["sales_orders"]+1:07}',
                       customer_id=int(self.rng.integers(1,self.config.customers+1)),channel=ch,originating_store_id=loc if ch=='POS' else None,
                       place_of_supply_state_id=pos_state,placed_at=at-timedelta(minutes=1),confirmed_at=at,event_id=self.event('ORDER',at))
        self.counts[(at.date(),ch,loc if ch=='POS' else 0)]+=1
        bases=[self.prices[s]*q for s,q in chosen.items()]
        promo=golden or self.rng.random()<(.65 if at.month in [10,11,1,6] else .25)
        markdown=[money(x*Decimal('.20')) if promo else money(0) for x in bases]
        remains=[b-d for b,d in zip(bases,markdown)]
        coupon=allocate(money(100),remains) if not golden and self.rng.random()<.15 and sum(remains)>=100 else [money(0)]*len(remains)
        all_cancel=ch!='POS' and self.rng.random()<.04 and not golden
        items=[]; total=money(0); cancelled_value=money(0)
        for number,((sku,qty),base,mark,cart) in enumerate(zip(chosen.items(),bases,markdown,coupon),1):
            line=self.add('order_lines','order_line_id',order_id=order['order_id'],sku_id=sku,line_number=number,quantity=qty,
                          original_unit_price=self.prices[sku],price_id=(sku-1)*3+['POS','WEB','APP'].index(ch)+1)
            for pid,value in [(1,mark),(2,cart)]:
                if value: self.add('order_line_discounts','discount_id',order_line_id=line['order_line_id'],promotion_id=pid,amount=value)
            values=allocate(base-mark-cart,[self.prices[sku]]*qty)
            cancel=qty if all_cancel else (1 if ch!='POS' and self.rng.random()<.025 else 0)
            if cancel:
                self.add('order_line_cancellations','cancellation_id',order_line_id=line['order_line_id'],quantity=cancel,reason='CUSTOMER_REQUEST',cancelled_at=at,event_id=self.event('CANCEL',at))
            for i,value in enumerate(values):
                rule,rate=tax_rule(at.date(),value)
                taxes=tax_components(value,rate,self.loc_states[loc]!=pos_state)
                payable=value+sum(taxes); total+=payable
                if i<cancel: cancelled_value+=payable
                else: items.append((line,value,rule,taxes))
        capture=self.payment(order,total,at)
        if cancelled_value: self.payment(order,cancelled_value,at,'REFUND',capture)
        if not items:
            self.status(order,['CREATED','CONFIRMED','CANCELLED'],at); return
        self.status(order,['CREATED','CONFIRMED','PAID','HANDED_OVER','COMPLETED'] if ch=='POS' else ['CREATED','CONFIRMED','ALLOCATED','PICKED','PACKED','DISPATCHED'],at)
        # One or two shipments; a split is two real fulfillment/invoice parents.
        split=ch!='POS' and len(items)>1 and self.rng.random()<.2
        groups=[items[::2],items[1::2]] if split else [items]
        deliveries=[]
        for group_index,group in enumerate(groups):
            delivery=at if ch=='POS' else at+timedelta(days=int(self.rng.integers(2,6))+group_index)
            deliveries.append(delivery)
            fulfillment=self.add('fulfillments','fulfillment_id',order_id=order['order_id'],source_location_id=loc,
                                 type='HANDOVER' if ch=='POS' else 'SHIPMENT',status='DELIVERED',dispatched_at=at,delivered_at=delivery,event_id=self.event('FULFILLMENT',at))
            inv=self.add('sales_invoices','invoice_id',invoice_number=f'SYNTH-INV-{self.ids["sales_invoices"]+1}',order_id=order['order_id'],
                         fulfillment_id=fulfillment['fulfillment_id'],registration_id=self.loc_states[loc],invoice_date=at.date(),event_id=self.event('INVOICE',at))
            for line,value,rule,taxes in group:
                fi=self.add('fulfillment_items','fulfillment_item_id',fulfillment_id=fulfillment['fulfillment_id'],order_line_id=line['order_line_id'],quantity=1)
                il=self.add('invoice_lines','invoice_line_id',invoice_id=inv['invoice_id'],fulfillment_item_id=fi['fulfillment_item_id'],tax_rule_id=rule,
                            quantity=1,taxable_value=value,cgst=taxes[0],sgst=taxes[1],igst=taxes[2])
                allocation=self.take(line['sku_id'],loc,1)[0]
                lot,qty,cost=allocation
                # Golden order's FIFO cost is deliberately fixed at 1200.
                ca=self.add('fulfillment_cost_allocations','allocation_id',fulfillment_item_id=fi['fulfillment_item_id'],lot_id=lot,quantity=1,unit_cost=cost)
                self.add('inventory_reservations','reservation_id',order_line_id=line['order_line_id'],location_id=loc,quantity=1,
                         status='CONSUMED',created_at=at,released_at=at)
                if ch=='POS': self.movement(line['sku_id'],loc,lot,-1,'SALE',at,item=fi['fulfillment_item_id'])
                else:
                    self.movement(line['sku_id'],loc,lot,-1,'TRANSFER_OUT',at,item=fi['fulfillment_item_id'])
                    self.movement(line['sku_id'],15,lot,1,'TRANSFER_IN',at,'TRANSIT',fi['fulfillment_item_id'])
                    self.schedule(delivery,self.deliver,line['sku_id'],lot,fi['fulfillment_item_id'])
                do_return=self.rng.random()<(.13 if ch!='POS' else .04)
                if golden: do_return=(group.index((line,value,rule,taxes))==0 and not getattr(self,'golden_return_scheduled',False))
                if do_return:
                    if golden: self.golden_return_scheduled=True
                    requested=delivery+timedelta(days=7 if golden else int(self.rng.integers(1,15)))
                    self.schedule(requested+timedelta(days=1),self.accept_return,order,line,inv,il,ca,capture,requested,golden)
        if ch!='POS':
            self.schedule(max(deliveries),self.close_order,order)

    def close_order(self, at, order):
        for old,new in [('DISPATCHED','DELIVERED'),('DELIVERED','CLOSED')]:
            self.add('order_status_events','status_event_id',order_id=order['order_id'],old_status=old,new_status=new,event_at=at,event_id=self.event('ORDER_STATUS',at))

    def deliver(self, at, sku, lot, item):
        self.movement(sku,15,lot,-1,'SALE',at,'TRANSIT',item)

    def accept_return(self, at, order, line, inv, il, ca, capture, requested, golden):
        loc=1 if golden else int(self.rng.integers(1,15))
        sellable=golden or self.rng.random()<.85
        rr=self.add('return_requests','return_id',order_id=order['order_id'],requested_at=requested,accepted_at=at,status='ACCEPTED',receiving_location_id=loc,event_id=self.event('RETURN',at))
        ri=self.add('return_items','return_item_id',return_id=rr['return_id'],invoice_line_id=il['invoice_line_id'],quantity=1,
                    reason='SIZE_FIT' if sellable else 'QUALITY',condition='SELLABLE' if sellable else 'DAMAGED',disposition='RESTOCK' if sellable else 'QUARANTINE')
        credit=self.add('credit_notes','credit_note_id',credit_number=f'SYNTH-CREDIT-{self.ids["credit_notes"]+1}',return_id=rr['return_id'],
                        invoice_id=inv['invoice_id'],registration_id=inv['registration_id'],issued_at=at,event_id=self.event('CREDIT',at))
        self.add('credit_note_lines','credit_line_id',credit_note_id=credit['credit_note_id'],return_item_id=ri['return_item_id'],invoice_line_id=il['invoice_line_id'],quantity=1,
                 **{k:il[k] for k in ['taxable_value','cgst','sgst','igst']})
        self.add('return_cost_allocations','return_allocation_id',return_item_id=ri['return_item_id'],allocation_id=ca['allocation_id'],quantity=1)
        lot=self.tables['stock_lots'][ca['lot_id']-1]
        if sellable:
            self.stock[(line['sku_id'],loc)].append([ca['lot_id'],1,ca['unit_cost'],lot['receipt_at']])
        self.movement(line['sku_id'],loc,ca['lot_id'],1,'RETURN_RESTOCK' if sellable else 'DAMAGE',at,
                      'SELLABLE' if sellable else 'DAMAGED',returned=ri['return_item_id'])
        amount=sum(il[k] for k in ['taxable_value','cgst','sgst','igst'])
        if not golden and self.rng.random()<.08:
            self.payment(order,amount,at+timedelta(days=1),'REFUND',capture,credit['credit_note_id'],True)
            # Some credits remain unsettled; others retry under a new transaction reference.
            if self.rng.random()<.4: return
        self.payment(order,amount,at+timedelta(days=2),'REFUND',capture,credit['credit_note_id'])

    def run(self):
        self.masters()
        days=pd.date_range(self.start,self.end,freq='D')
        weights=np.array([(1.35 if d.dayofweek>=5 else 1)*(1.6 if d.month in [10,11] else 1.25 if d.month in [1,6] else 1) for d in days])
        offsets=self.rng.choice(len(days),self.config.orders-1,p=weights/weights.sum())
        orders=[(datetime.combine(days[int(d)].date(),time(10),IST)+timedelta(seconds=int(self.rng.integers(0,10*3600))),False) for d in offsets]
        orders.append((datetime(2025,10,1,12,tzinfo=IST),True)); orders.sort()
        index=0
        for stamp in days:
            day=stamp.date(); morning=datetime.combine(day,time(8),IST)
            self.flush(morning)
            if day.weekday()==0:
                for sku in self.prices:
                    for loc in range(1,15):
                        target=(3 if sku%3==1 else 12) if loc<=12 else (8 if sku%3==1 else 28)
                        qty=target-(self.available(sku,loc)+1)
                        if qty>0: self.receive(sku,loc,qty,morning)
            # Independent synthetic traffic draws, based on expected demand, not realized sales.
            demand=self.config.orders*weights[days.get_loc(stamp)]/weights.sum()
            for traffic_loc in range(1,13):
                closed=traffic_loc==12 and day.day==1
                conversion=.12 if traffic_loc==11 else .22
                entries=0 if closed else int(self.rng.poisson(max(20,demand*.6/12/conversion)))
                self.add('store_footfall_daily',store_id=traffic_loc,business_date=day,valid_entries=entries,
                         excluded_entries=0 if closed else int(self.rng.integers(2,12)),sensor_status='CLOSED' if closed else 'OK')
            for channel,share in [('WEB',.24),('APP',.16)]:
                self.add('digital_traffic_daily',business_date=day,channel=channel,
                         eligible_sessions=int(self.rng.poisson(max(100,demand*share/.025))),excluded_sessions=int(self.rng.integers(10,100)))
            while index<len(orders) and orders[index][0].date()==day:
                at,golden=orders[index]; self.flush(at); self.order(at,golden); index+=1
            self.flush(datetime.combine(day,time.max,IST))
        self.flush(self.asof)
        self.snapshots()
        return self.tables

    def snapshots(self):
        # Month-end derived snapshots plus the final, settled as-of date.
        frame=pd.DataFrame(self.tables['inventory_movements'])
        frame['business_date']=pd.to_datetime(frame.event_at,utc=True).dt.tz_convert('Asia/Kolkata').dt.date
        dates=list(pd.date_range(self.start,self.end,freq='ME').date)+[self.asof.date()]
        for day in dates:
            balances=frame[frame.business_date<=day].groupby(['sku_id','location_id','stock_state']).quantity.sum().unstack(fill_value=0)
            for (sku,loc),row in balances.iterrows():
                self.add('inventory_snapshots',snapshot_date=day,sku_id=int(sku),location_id=int(loc),
                         sellable=int(row.get('SELLABLE',0)),reserved=0,in_transit=int(row.get('TRANSIT',0)),damaged=int(row.get('DAMAGED',0)))

def generate(config, destination):
    destination=Path(destination)
    if destination.exists(): raise FileExistsError(f'{destination} exists; choose a new output directory')
    generator=Generator(config); tables=generator.run()
    destination.mkdir(parents=True)
    manifest={'generator_version':'1.0.0','synthetic':True,'config':asdict(config),'as_of':generator.asof.isoformat(),
              'python':platform.python_version(),'dependencies':{x:version(x) for x in ['Faker','numpy','pandas','pyarrow']},'tables':{}}
    for name,rows in tables.items():
        if not rows: continue
        path=destination/f'{name}.parquet'
        pd.DataFrame(rows).to_parquet(path,index=False,compression='zstd')
        manifest['tables'][name]={'rows':len(rows),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    manifest['dataset_id']=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()
    (destination/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest
