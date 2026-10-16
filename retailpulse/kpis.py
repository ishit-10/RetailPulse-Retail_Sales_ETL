"""Parameterized warehouse reports. Dates are inclusive Indian business dates."""
from datetime import date
from sqlalchemy import text

FILTERS = {'city', 'state_id', 'store_id', 'channel', 'category_id', 'style_id', 'sku_id', 'customer_id'}
PRODUCT_FILTERS = {'category_id', 'style_id', 'sku_id'}
SCOPE = '''SELECT o.*,p.category_id,p.style_id,l.city
FROM analytics.fact_order_items o JOIN analytics.dim_product p USING(sku_id)
LEFT JOIN analytics.dim_store l ON l.location_id=o.store_id'''


def parameters(start, end, filters=None):
    start, end = date.fromisoformat(str(start)), date.fromisoformat(str(end))
    if end < start:
        raise ValueError('End date precedes start date')
    filters = {} if filters is None else filters
    if not isinstance(filters, dict):
        raise ValueError('Filters must be a JSON object')
    if set(filters) - FILTERS:
        raise ValueError('Unsupported filters (brand/region are absent from the source): ' + ', '.join(sorted(set(filters)-FILTERS)))
    if any(value is None for value in filters.values()):
        raise ValueError('Filters must have explicit non-null values')
    for key, value in filters.items():
        if key.endswith('_id') and (not isinstance(value, int) or isinstance(value, bool)):
            raise ValueError(f'{key} must be an integer')
        if key in ('city', 'channel') and not isinstance(value, str):
            raise ValueError(f'{key} must be text')
    return {'start': start, 'end': end, **filters}


def predicate(filters, alias='o'):
    if set(filters) - FILTERS:
        raise ValueError('Unsupported filter')
    return ' AND '.join(f'{alias}.{key}=:{key}' for key in sorted(filters)) or 'true'


def report(e, kind, start, end, filters=None, as_of=None):
    filters = {} if filters is None else filters
    params = parameters(start, end, filters)
    scope = f'WITH scoped AS (SELECT * FROM ({SCOPE}) o WHERE {predicate(filters)}) '
    if kind == 'sales':
        query = scope + '''
, b AS (SELECT COALESCE(sum(booked_gmv),0) gmv,COALESCE(sum(discount),0) discounts,
 COALESCE(sum(booked_merchandise),0) booked_merchandise,count(DISTINCT order_id) orders,
 COALESCE(sum(ordered_units),0) ordered_units,COALESCE(sum(cancelled_units),0) cancelled_units
 FROM scoped WHERE booking_date BETWEEN :start AND :end),
 s AS (SELECT COALESCE(sum(net_sales),0) net_sales,COALESCE(sum(net_cogs),0) net_cogs,
 COALESCE(sum(net_units),0) net_units,
 COALESCE(sum(net_sales) FILTER(WHERE event_type='SALE'),0) gross_sales,
 COALESCE(-sum(net_sales) FILTER(WHERE event_type='CREDIT'),0) merchandise_credits,
 COALESCE(sum(tax_value),0) net_tax
 FROM analytics.warehouse_sales_events JOIN scoped USING(order_line_id) WHERE event_date BETWEEN :start AND :end)
 SELECT b.*,s.*,orders::numeric/(CAST(:end AS date)-CAST(:start AS date)+1) opd,
 CASE WHEN orders>0 THEN booked_merchandise/orders END booked_aov,
 CASE WHEN orders>0 THEN ordered_units::numeric/orders END booked_units_per_order,
 CASE WHEN gmv>0 THEN 100*discounts/gmv END discount_percent,
 CASE WHEN ordered_units>0 THEN 100*cancelled_units::numeric/ordered_units END cancellation_percent,
 net_sales-net_cogs gross_profit,
 CASE WHEN net_sales>0 THEN 100*(net_sales-net_cogs)/net_sales END gross_margin_percent,
 CASE WHEN net_units>0 THEN net_sales/net_units END asp FROM b CROSS JOIN s'''
    elif kind in ('daily', 'weekly', 'monthly'):
        grain = {'daily': 'day', 'weekly': 'week', 'monthly': 'month'}[kind]
        # Prior full periods are available for comparison, even outside the filter.
        # Boundary periods honor exact input dates; their growth rates are NULL.
        query = scope + f''', calendar AS (
 SELECT d::date period_start FROM generate_series(
 date_trunc('{grain}',CAST(:start AS date)-interval '1 year'),
 date_trunc('{grain}',CAST(:end AS date)),interval '1 {grain}') d),
 totals AS (SELECT date_trunc('{grain}',event_date)::date period_start,sum(net_sales) net_sales,
 sum(net_cogs) net_cogs FROM analytics.warehouse_sales_events JOIN scoped USING(order_line_id)
 GROUP BY 1),
 displayed AS (SELECT date_trunc('{grain}',event_date)::date period_start,sum(net_sales) net_sales,sum(net_cogs) net_cogs
 FROM analytics.warehouse_sales_events JOIN scoped USING(order_line_id) WHERE event_date BETWEEN :start AND :end GROUP BY 1),
 series AS (SELECT period_start,COALESCE(net_sales,0) net_sales,COALESCE(net_cogs,0) net_cogs
 FROM calendar LEFT JOIN totals USING(period_start))
 SELECT s.period_start,COALESCE(d.net_sales,0) net_sales,COALESCE(d.net_cogs,0) net_cogs,
 COALESCE(d.net_sales,0)-COALESCE(d.net_cogs,0) gross_profit,
 CASE WHEN s.period_start>=CAST(:start AS date) AND s.period_start+interval '1 {grain}'-interval '1 day'<=CAST(:end AS date) AND p.net_sales>0 THEN 100*(s.net_sales-p.net_sales)/p.net_sales END previous_period_growth_percent,
 CASE WHEN s.period_start>=CAST(:start AS date) AND s.period_start+interval '1 {grain}'-interval '1 day'<=CAST(:end AS date) AND y.net_sales>0 THEN 100*(s.net_sales-y.net_sales)/y.net_sales END yoy_percent
 FROM series s LEFT JOIN displayed d USING(period_start) LEFT JOIN series p ON p.period_start=s.period_start-interval '1 {grain}'
 LEFT JOIN series y ON y.period_start=s.period_start-interval '1 year'
 WHERE s.period_start>=date_trunc('{grain}',CAST(:start AS date)) ORDER BY s.period_start'''
    elif kind in ('products', 'categories', 'stores', 'channels'):
        key = {'products': 'sku_id', 'categories': 'category_id', 'stores': 'store_id', 'channels': 'channel'}[kind]
        query = scope + f''', totals AS (SELECT {key},sum(net_sales) net_sales,sum(net_cogs) net_cogs,
 sum(net_units) net_units FROM analytics.warehouse_sales_events JOIN scoped USING(order_line_id)
 WHERE event_date BETWEEN :start AND :end GROUP BY 1)
 SELECT *,net_sales-net_cogs gross_profit,dense_rank() OVER(ORDER BY net_sales DESC) revenue_rank
 FROM totals ORDER BY revenue_rank,{key}'''
    elif kind == 'returns':
        if as_of is None:
            raise ValueError('returns requires --as-of: the observed outcome cutoff, not the booking end date')
        params['as_of'] = date.fromisoformat(str(as_of))
        query = scope + ''', cohort AS (
 SELECT s.* FROM analytics.fact_sales s JOIN scoped USING(order_line_id)
 WHERE recognition_date BETWEEN :start AND :end AND recognition_date<=CAST(:as_of AS date)-30),
 returned AS (SELECT r.invoice_line_id,sum(r.units) units FROM analytics.fact_returns r
 WHERE accepted_date<=:as_of GROUP BY 1)
 SELECT COALESCE(sum(c.units),0) matured_fulfilled_units,COALESCE(sum(r.units),0) accepted_returned_units,
 CASE WHEN sum(c.units)>0 THEN 100*COALESCE(sum(r.units),0)::numeric/sum(c.units) END return_percent
 FROM cohort c LEFT JOIN returned r USING(invoice_line_id)'''
    elif kind == 'refunds':
        if set(filters) & PRODUCT_FILTERS:
            raise ValueError('Refund transactions have no reliable product allocation; use merchandise credits for product impact')
        query = scope + '''SELECT COALESCE(sum(r.customer_refund_value) FILTER(WHERE r.status='SUCCEEDED'),0)
 customer_refunds_including_gst,count(*) FILTER(WHERE r.status='SUCCEEDED') succeeded_refund_transactions
 FROM analytics.fact_refunds r WHERE transaction_date BETWEEN :start AND :end
 AND EXISTS(SELECT 1 FROM scoped o WHERE o.order_id=r.order_id)'''
    elif kind == 'footfall':
        if set(filters) - {'store_id', 'city', 'state_id'}:
            raise ValueError('Footfall supports store, city and state only; product/customer/channel denominators are unavailable')
        query = f'''WITH scoped AS (SELECT f.*,l.city,l.state_id FROM analytics.fact_footfall_daily f
 JOIN analytics.dim_store l ON l.location_id=f.store_id)
 SELECT store_id,count(*) covered_store_days,sum(valid_entries) valid_entries,sum(completed_orders) completed_orders,
 CASE WHEN sum(valid_entries)>0 THEN 100*sum(completed_orders)::numeric/sum(valid_entries) END conversion_percent
 FROM scoped o WHERE business_date BETWEEN :start AND :end AND {predicate(filters)} GROUP BY 1 ORDER BY 1'''
    elif kind == 'inventory':
        if set(filters) - {'store_id', 'city', 'state_id', *PRODUCT_FILTERS}:
            raise ValueError('Inventory has no customer or sales-channel allocation')
        query = f'''WITH scoped AS (SELECT f.*,f.location_id store_id,l.city,l.state_id,p.category_id,p.style_id
 FROM analytics.fact_inventory_daily f JOIN analytics.dim_store l USING(location_id)
 JOIN analytics.dim_product p USING(sku_id))
 SELECT snapshot_date,location_id,sku_id,sellable,reserved,in_transit,damaged,safety_stock,available_to_promise,
 CASE WHEN available_to_promise IS NOT NULL THEN available_to_promise>0 END stock_available
 FROM scoped o WHERE snapshot_date BETWEEN :start AND :end AND {predicate(filters)} ORDER BY 1,2,3'''
    elif kind == 'sell-through':
        if set(filters) - {'store_id', 'city', 'state_id', *PRODUCT_FILTERS}:
            raise ValueError('Sell-through uses inventory locations and products, not customer/channel allocation')
        # Selected locations form one pool; internal transfers cancel by omitting
        # both directions for company scope. A single store includes inbound.
        if ('city' in filters or 'state_id' in filters) and 'store_id' not in filters:
            raise ValueError('Cross-location transfer boundaries cannot be paired reliably; choose one store or company scope')
        inbound = "'OPENING_BALANCE','PURCHASE_RECEIPT','RETURN_RESTOCK','TRANSFER_IN'" if 'store_id' in filters else "'OPENING_BALANCE','PURCHASE_RECEIPT','RETURN_RESTOCK'"
        query = f'''WITH locations AS (SELECT l.location_id store_id,l.city,l.state_id,p.sku_id,p.category_id,p.style_id
 FROM analytics.dim_store l CROSS JOIN analytics.dim_product p),
 chosen AS (SELECT * FROM locations o WHERE {predicate(filters)}),
 pool AS (SELECT COALESCE(sum(m.quantity) FILTER(WHERE movement_date<:start AND stock_state='SELLABLE'),0)
 +COALESCE(sum(m.quantity) FILTER(WHERE movement_date BETWEEN :start AND :end AND stock_state='SELLABLE'
 AND type IN ({inbound})),0) available_pool
 FROM analytics.fact_inventory_movements m JOIN chosen c ON c.store_id=m.location_id AND c.sku_id=m.sku_id),
 sold AS (SELECT COALESCE(sum(s.units),0) units FROM analytics.fact_sales s
 JOIN analytics.fact_order_items o USING(order_line_id) JOIN chosen c ON c.store_id=s.source_location_id AND c.sku_id=o.sku_id
 WHERE recognition_date BETWEEN :start AND :end),
 returned AS (SELECT COALESCE(sum(r.units),0) units FROM analytics.fact_returns r
 JOIN analytics.fact_sales s USING(invoice_line_id) JOIN analytics.fact_order_items o USING(order_line_id)
 JOIN chosen c ON c.store_id=s.source_location_id AND c.sku_id=o.sku_id WHERE accepted_date BETWEEN :start AND :end)
 SELECT available_pool,sold.units-returned.units net_fulfilled_units,
 CASE WHEN available_pool>0 THEN 100*(sold.units-returned.units)::numeric/available_pool END sell_through_percent
 FROM pool CROSS JOIN sold CROSS JOIN returned'''
    else:
        raise ValueError('Unknown report; refund rate has no approved denominator and is not implemented')
    with e.connect().execution_options(isolation_level='REPEATABLE READ') as c, c.begin():
        lineage = c.exec_driver_sql('SELECT r.snapshot_id FROM etl.warehouse_head h JOIN etl.warehouse_runs r USING(run_id)').scalar()
        if lineage is None:
            raise ValueError('No published warehouse')
        if as_of and c.exec_driver_sql('SELECT max(date_key) FROM analytics.dim_date').scalar() < params['as_of']:
            raise ValueError('Return observation cutoff exceeds available warehouse coverage')
        return {'snapshot_id': str(lineage), 'report': kind, 'start': str(start), 'end': str(end),
                'rows': [dict(row) for row in c.execute(text(query), params).mappings()]}
