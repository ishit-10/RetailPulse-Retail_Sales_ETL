"""Dashboard SQL extensions. Core KPI SQL is imported unchanged from Stage 4."""
from datetime import timedelta
from .. import kpis


def scope(filters):
    return f'WITH scoped AS (SELECT * FROM ({kpis.SCOPE}) o WHERE {kpis.predicate(filters)}) '


def query(kind, start, end, filters=None, grain='month'):
    filters = filters or {}
    params = kpis.parameters(start, end, filters)
    if grain not in ('day', 'week', 'month'): raise ValueError('Unsupported date grain')
    prefix = scope(filters)
    if kind == 'comparison':
        current, params = kpis.build_query('sales', start, end, filters)
        previous_end = params['start'] - timedelta(days=1)
        previous_start = previous_end - (params['end'] - params['start'])
        previous, previous_params = kpis.build_query('sales', previous_start, previous_end, filters)
        # Rename bind parameters only; all identifiers originate in the shared SQL.
        previous = previous.replace(':start', ':previous_start').replace(':end', ':previous_end')
        params.update(previous_start=previous_start, previous_end=previous_end)
        sql = f'''WITH now AS ({current}), before AS ({previous}) SELECT now.*,
 before.net_sales previous_net_sales,before.orders previous_orders,before.gmv previous_gmv,
 before.booked_aov previous_aov,before.gross_profit previous_profit,before.gross_margin_percent previous_margin,
 CASE WHEN before.net_sales>0 THEN 100*(now.net_sales-before.net_sales)/before.net_sales END revenue_growth_percent,
 CASE WHEN before.orders>0 THEN 100*(now.orders-before.orders)::numeric/before.orders END orders_growth_percent,
 CASE WHEN before.gmv>0 THEN 100*(now.gmv-before.gmv)/before.gmv END gmv_growth_percent,
 CASE WHEN before.booked_aov>0 THEN 100*(now.booked_aov-before.booked_aov)/before.booked_aov END aov_growth_percent,
 CASE WHEN before.gross_profit>0 THEN 100*(now.gross_profit-before.gross_profit)/before.gross_profit END profit_growth_percent,
 now.gross_margin_percent-before.gross_margin_percent margin_change_points
 FROM now CROSS JOIN before'''
        sql = sql.replace('CASE WHEN before.', 'CASE WHEN CAST(:previous_start AS date)>=(SELECT min(date_key) FROM analytics.dim_date) AND before.')
        sql = sql.replace('now.gross_margin_percent-before.gross_margin_percent margin_change_points', 'CASE WHEN CAST(:previous_start AS date)>=(SELECT min(date_key) FROM analytics.dim_date) THEN now.gross_margin_percent-before.gross_margin_percent END margin_change_points')
        return sql, params
    if kind == 'order_trend':
        return prefix + f'''SELECT date_trunc('{grain}',booking_date)::date period_start,
 count(DISTINCT order_id) orders,sum(booked_merchandise) booked_merchandise
 FROM scoped WHERE booking_date BETWEEN :start AND :end GROUP BY 1 ORDER BY 1''', params
    if kind in ('store_scorecard', 'channel_scorecard'):
        key = 'store_id' if kind == 'store_scorecard' else 'channel'
        return prefix + f''', bookings AS (SELECT {key},count(DISTINCT order_id) orders,
 sum(booked_merchandise) booked_merchandise FROM scoped WHERE booking_date BETWEEN :start AND :end GROUP BY 1),
 sales AS (SELECT o.{key},sum(e.net_sales) net_sales,sum(e.net_cogs) net_cogs
 FROM analytics.warehouse_sales_events e JOIN scoped o USING(order_line_id)
 WHERE event_date BETWEEN :start AND :end GROUP BY 1),
 keys AS (SELECT {key} FROM bookings UNION SELECT {key} FROM sales),
 joined AS (SELECT k.{key},COALESCE(b.orders,0) orders,
 COALESCE(b.booked_merchandise,0) booked_merchandise,COALESCE(s.net_sales,0) net_sales,
 COALESCE(s.net_cogs,0) net_cogs FROM keys k LEFT JOIN bookings b ON k.{key} IS NOT DISTINCT FROM b.{key}
 LEFT JOIN sales s ON k.{key} IS NOT DISTINCT FROM s.{key}),
 measured AS (SELECT *,orders::numeric/(CAST(:end AS date)-CAST(:start AS date)+1) opd,
 CASE WHEN orders>0 THEN booked_merchandise/orders END booked_aov,
 net_sales-net_cogs gross_profit,CASE WHEN net_sales>0 THEN 100*(net_sales-net_cogs)/net_sales END gross_margin_percent
 FROM joined)
 SELECT *,dense_rank() OVER(ORDER BY net_sales DESC) revenue_rank,
 CASE WHEN sum(net_sales) OVER()>0 THEN 100*net_sales/sum(net_sales) OVER() END revenue_share_percent
 FROM measured ORDER BY revenue_rank''', params
    if kind == 'customer_summary':
        return prefix + '''
, bookings AS (SELECT count(DISTINCT customer_id) ordering_customers,
 count(DISTINCT order_id) FILTER(WHERE customer_id IS NOT NULL) identified_orders
 FROM scoped WHERE booking_date BETWEEN :start AND :end),
 purchases AS (SELECT count(DISTINCT o.customer_id) purchasing_customers
 FROM analytics.fact_sales s JOIN scoped o USING(order_line_id) WHERE recognition_date BETWEEN :start AND :end)
 SELECT b.*,p.*,CASE WHEN ordering_customers>0 THEN identified_orders::numeric/ordering_customers END orders_per_customer
 FROM bookings b CROSS JOIN purchases p''',params
    if kind == 'customer_segments':
        return prefix + ''', first_order AS (SELECT customer_id,min(booking_date) first_date FROM analytics.fact_order_items
 WHERE customer_id IS NOT NULL GROUP BY 1),
 selected AS (SELECT o.customer_id,o.order_id,sum(o.booked_merchandise) booked_merchandise,
 CASE WHEN f.first_date>=:start THEN 'New in period' ELSE 'Returning' END segment
 FROM scoped o JOIN first_order f USING(customer_id) WHERE booking_date BETWEEN :start AND :end GROUP BY 1,2,4)
 SELECT segment,count(DISTINCT customer_id) customers,count(*) orders,sum(booked_merchandise) booked_merchandise,
 CASE WHEN count(*)>0 THEN sum(booked_merchandise)/count(*) END booked_aov,
 count(*)::numeric/NULLIF(count(DISTINCT customer_id),0) orders_per_customer FROM selected GROUP BY 1 ORDER BY 1''', params
    if kind == 'customer_trend':
        return prefix + f'''SELECT date_trunc('{grain}',booking_date)::date period_start,
 count(DISTINCT customer_id) identified_customers,count(DISTINCT order_id) orders,
 count(DISTINCT order_id) FILTER(WHERE customer_id IS NULL) guest_orders
 FROM scoped WHERE booking_date BETWEEN :start AND :end GROUP BY 1 ORDER BY 1''', params
    if kind == 'cohorts':
        return prefix + ''', firsts AS (SELECT customer_id,date_trunc('month',min(booking_date))::date cohort_month
 FROM analytics.fact_order_items WHERE customer_id IS NOT NULL GROUP BY 1),
 activity AS (SELECT DISTINCT o.customer_id,f.cohort_month,date_trunc('month',o.booking_date)::date activity_month
 FROM scoped o JOIN firsts f USING(customer_id) WHERE o.booking_date BETWEEN :start AND :end)
 SELECT cohort_month,activity_month,count(*) active_customers FROM activity GROUP BY 1,2 ORDER BY 1,2''', params
    if kind == 'order_outcomes':
        return prefix + ''', selected AS (SELECT DISTINCT order_id FROM scoped WHERE booking_date BETWEEN :start AND :end),
 quantities AS (SELECT o.order_id,sum(o.ordered_units) ordered,sum(o.cancelled_units) cancelled
 FROM analytics.fact_order_items o JOIN selected USING(order_id) GROUP BY 1),
 fulfilled AS (SELECT o.order_id,sum(s.units) units FROM analytics.fact_sales s
 JOIN analytics.fact_order_items o USING(order_line_id) JOIN selected USING(order_id) GROUP BY 1),
 classified AS (SELECT q.order_id,CASE WHEN q.cancelled=q.ordered THEN 'Fully cancelled'
 WHEN COALESCE(f.units,0)=q.ordered THEN 'All units recognized'
 WHEN COALESCE(f.units,0)+q.cancelled=q.ordered THEN 'Recognized with cancellation'
 ELSE 'Unrecognized units remain' END outcome FROM quantities q LEFT JOIN fulfilled f USING(order_id))
 SELECT outcome,count(*) orders FROM classified GROUP BY 1 ORDER BY 2 DESC''', params
    if kind == 'inventory_latest':
        if set(filters) - {'state_id','city','store_id','category_id','sku_id','style_id'}:
            raise ValueError('Inventory has no sales-channel attribution')
        return f'''WITH snapshot AS (SELECT max(snapshot_date) d FROM analytics.fact_inventory_daily
 WHERE snapshot_date BETWEEN :start AND :end),
 inventory AS (SELECT f.*,f.location_id store_id,l.city,l.state_id,p.category_id,p.style_id,
 p.product_name,l.name location_name FROM analytics.fact_inventory_daily f
 JOIN analytics.dim_store l USING(location_id) JOIN analytics.dim_product p USING(sku_id)
 JOIN snapshot s ON f.snapshot_date=s.d),
 sold AS (SELECT o.sku_id,s.source_location_id location_id,sum(s.units) sold_units FROM analytics.fact_sales s
 JOIN analytics.fact_order_items o USING(order_line_id) WHERE s.recognition_date BETWEEN :start AND :end GROUP BY 1,2)
 SELECT o.*,CASE WHEN available_to_promise IS NOT NULL THEN available_to_promise>0 END stock_available,COALESCE(s.sold_units,0) fulfilled_units_in_period,
 (o.sellable>0 AND COALESCE(s.sold_units,0)=0) no_sales_with_stock
 FROM inventory o LEFT JOIN sold s USING(sku_id,location_id) WHERE {kpis.predicate(filters)}
 ORDER BY o.location_id,o.sku_id''', params
    raise ValueError('Unknown dashboard query')


HEAD = '''SELECT w.run_id::text run_id,w.snapshot_id::text snapshot_id,w.started_at warehouse_started_at,
 w.completed_at warehouse_completed_at,w.status,w.counts,w.totals,
 e.started_at extraction_started_at,e.completed_at extraction_completed_at,
 e.rows_scanned,e.partitions_changed,h.run_id::text latest_snapshot_id,
 latest.started_at latest_extraction_started_at,latest.completed_at latest_extraction_completed_at,latest.rows_scanned latest_rows_scanned
 FROM etl.warehouse_head p JOIN etl.warehouse_runs w USING(run_id)
 JOIN etl.extract_runs e ON e.run_id=w.snapshot_id LEFT JOIN etl.extract_heads h ON h.lake_root=w.lake_root
 LEFT JOIN etl.extract_runs latest ON latest.run_id=h.run_id'''

HISTORY = '''SELECT 'warehouse' pipeline,r.run_id::text run_id,r.started_at,r.completed_at,r.status,
 extract(epoch FROM r.completed_at-r.started_at) duration_seconds,r.error_class,
 r.error_context->>'phase' phase,r.error_context->>'table' table_name
 FROM etl.warehouse_runs r WHERE r.lake_root=(SELECT w.lake_root FROM etl.warehouse_head h JOIN etl.warehouse_runs w USING(run_id))
 UNION ALL SELECT 'extraction',r.run_id::text,r.started_at,r.completed_at,r.status,
 extract(epoch FROM r.completed_at-r.started_at),r.error_class,r.error_context->>'phase',r.error_context->>'table'
 FROM etl.extract_runs r WHERE r.lake_root=(SELECT w.lake_root FROM etl.warehouse_head h JOIN etl.warehouse_runs w USING(run_id))
 ORDER BY started_at DESC LIMIT 30'''
