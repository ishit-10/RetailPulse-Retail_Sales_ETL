"""Read-only adapters. One transaction pins all queries for a page payload."""
from contextlib import contextmanager
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re

from sqlalchemy import text
from .. import db, kpis
from . import queries

DEMO_ROOT = db.ROOT / 'demo' / 'warehouse'
DEMO_TABLES = ('dim_date','dim_product','dim_store','dim_state','dim_channel','fact_order_items',
               'fact_sales','fact_returns','fact_refunds','fact_inventory_daily','fact_inventory_movements','fact_footfall_daily')


class SnapshotChanged(ValueError):
    pass


class LocalSession:
    def __init__(self, connection): self.c = connection
    def rows(self, query, params=None):
        return [dict(row) for row in self.c.execute(text(query), params or {}).mappings()]


class DemoSession:
    def __init__(self, connection): self.c = connection
    def rows(self, query, params=None):
        sql = re.sub(r'(?<!:):([a-z_]+)', r'$\1', query)
        # PostgreSQL default NUMERIC is unconstrained; DuckDB's is scale 3.
        sql = re.sub(r'::numeric\b(?!\s*\()', '::DECIMAL(38,10)', sql, flags=re.I)
        sql = re.sub(r"(interval '1 (?:day|week|month)'\)) d\b", r'\1 AS series(d)', sql)
        used = set(re.findall(r'\$([a-z_]+)', sql))
        cursor = self.c.execute(sql, {key: value for key, value in (params or {}).items() if key in used})
        names = [col[0] for col in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]


def validate_demo(root=DEMO_ROOT):
    root = Path(root).resolve()
    manifest = json.loads((root / 'manifest.json').read_text())
    if manifest.get('format_version') != 1 or manifest.get('synthetic') is not True or set(manifest['tables']) != set(DEMO_TABLES):
        raise ValueError('Unsupported demo bundle')
    import pyarrow.parquet as pq
    for name, entry in manifest['tables'].items():
        path = root / (name + '.parquet')
        if path.resolve().parent != root or hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
            raise ValueError('Demo checksum mismatch: ' + name)
        with pq.ParquetFile(path) as file:
            if file.metadata.num_rows != entry['rows']: raise ValueError('Demo row count mismatch')
    return manifest


@contextmanager
def session(mode, engine=None, root=DEMO_ROOT):
    if mode == 'local':
        if engine is None: raise ValueError('Local mode requires a database engine')
        with engine.connect().execution_options(isolation_level='REPEATABLE READ') as c, c.begin():
            c.exec_driver_sql('SET TRANSACTION READ ONLY')
            c.exec_driver_sql("SET LOCAL statement_timeout='30s'")
            c.exec_driver_sql("SET LOCAL TIME ZONE 'UTC'")
            yield LocalSession(c)
    elif mode == 'demo':
        import duckdb
        import pyarrow.parquet as pq
        validate_demo(root)
        c = duckdb.connect(':memory:', config={'threads': 2, 'memory_limit': '256MB', 'enable_external_access': False})
        try:
            c.execute('CREATE SCHEMA analytics')
            for name in DEMO_TABLES:
                table = pq.ParquetFile(Path(root) / (name + '.parquet')).read()
                c.register('input_' + name, table)
                c.execute(f'CREATE VIEW analytics.{name} AS SELECT * FROM input_{name}')
            # Identical event model to the Stage 4 view, rather than a second KPI formula.
            model = (db.ROOT / 'sql/009_warehouse_model.sql').read_text()
            view = model.split('CREATE VIEW warehouse_sales_events AS\n', 1)[1].split('CREATE VIEW warehouse_sales_detail', 1)[0]
            for name in ('fact_sales','fact_returns'):
                view = re.sub(r'\b'+name+r'\b', 'analytics.'+name, view)
            c.execute('CREATE VIEW analytics.warehouse_sales_events AS ' + view)
            yield DemoSession(c)
        finally: c.close()
    else:
        raise ValueError('Mode must be local or demo')


def head(s, mode, root=DEMO_ROOT):
    if mode == 'demo':
        manifest = validate_demo(root)
        return {'run_id': manifest['bundle_id'], 'snapshot_id': manifest['bundle_id'],
                'status': 'demo', 'fixture_created_at': manifest['created_at'], 'counts': {k:v['rows'] for k,v in manifest['tables'].items()}}
    rows = s.rows(queries.HEAD)
    if not rows or rows[0]['status'] != 'completed': raise ValueError('No completed published warehouse; finish Stage 4 first')
    return rows[0]


def metadata(mode, engine=None, root=DEMO_ROOT):
    with session(mode, engine, root) as s:
        result = head(s, mode, root)
        result['coverage'] = s.rows('SELECT min(date_key) min_date,max(date_key) max_date FROM analytics.dim_date')[0]
        result['booking_dates'] = s.rows('SELECT min(booking_date) min_date,max(booking_date) max_date FROM analytics.fact_order_items')[0]
        result['stores'] = s.rows("SELECT location_id,name,city,state_id,type FROM analytics.dim_store ORDER BY name")
        result['states'] = s.rows('SELECT state_id,name FROM analytics.dim_state ORDER BY name')
        result['products'] = s.rows('SELECT sku_id,product_name,color,size,category_id,category_name FROM analytics.dim_product ORDER BY product_name,sku_id')
        return result


def core(s, kind, start, end, filters, as_of=None):
    query, params = kpis.build_query(kind, start, end, filters, as_of)
    if kind in ('products','categories'):
        query = 'WITH base AS (' + query + ') SELECT *,CASE WHEN net_sales>0 THEN 100*gross_profit/net_sales END gross_margin_percent,CASE WHEN sum(net_sales) OVER()>0 THEN 100*net_sales/sum(net_sales) OVER() END revenue_share_percent FROM base ORDER BY revenue_rank'
    return s.rows(query, params)


def extra(s, kind, start, end, filters, grain='month'):
    query, params = queries.query(kind, start, end, filters, grain)
    return s.rows(query, params)


def payload(mode, page, start, end, filters, run_id, grain='month', as_of=None, engine=None, root=DEMO_ROOT):
    start, end = date.fromisoformat(str(start)), date.fromisoformat(str(end))
    kpis.parameters(start, end, filters)
    with session(mode, engine, root) as s:
        current = head(s, mode, root)
        if current['run_id'] != run_id: raise SnapshotChanged('Warehouse publication changed; reload the page')
        result = {'lineage': current, 'notices': []}
        if page == 'health':
            result['history'] = s.rows(queries.HISTORY) if mode == 'local' else []
            return result
        if page not in ('overview','stores','products','customers'): raise ValueError('Unknown page')
        result['summary'] = extra(s, 'comparison', start, end, filters)[0]
        result['trend'] = core(s, {'day':'daily','week':'weekly','month':'monthly'}[grain], start, end, filters)
        result['order_trend'] = extra(s, 'order_trend', start, end, filters, grain)
        if page in ('overview','stores'):
            result['channels'] = extra(s, 'channel_scorecard', start, end, filters)
            result['categories'] = core(s, 'categories', start, end, filters)
        if page == 'stores':
            result['stores'] = extra(s, 'store_scorecard', start, end, filters)
            if set(filters)-{'store_id','city','state_id','channel'} or filters.get('channel') in ('WEB','APP'):
                result['footfall'] = []
                result['notices'].append('Footfall is unavailable for the selected product or online-channel scope; filters are not silently removed.')
            else:
                result['footfall'] = core(s, 'footfall', start, end, {k:v for k,v in filters.items() if k!='channel'})
        if page == 'products':
            result['products'] = core(s, 'products', start, end, filters)
            result['categories'] = core(s, 'categories', start, end, filters)
            if 'channel' in filters:
                result['inventory'], result['sell_through'] = [], []
                result['notices'].append('Inventory and sell-through have no sales-channel attribution. Clear channel to inspect physical stock.')
            else:
                result['inventory'] = extra(s, 'inventory_latest', start, end, filters)
                if ('city' in filters or 'state_id' in filters) and 'store_id' not in filters:
                    result['sell_through'] = []
                    result['notices'].append('Sell-through needs one store or company scope; cross-location transfer boundaries are not paired in the source.')
                else: result['sell_through'] = core(s, 'sell-through', start, end, filters)
        if page == 'customers':
            result['customer_summary'] = extra(s, 'customer_summary', start, end, filters)
            result['segments'] = extra(s, 'customer_segments', start, end, filters)
            result['customer_trend'] = extra(s, 'customer_trend', start, end, filters, grain)
            result['cohorts'] = extra(s, 'cohorts', start, end, filters)
            result['outcomes'] = extra(s, 'order_outcomes', start, end, filters)
            maximum = s.rows('SELECT max(date_key) d FROM analytics.dim_date')[0]['d']
            cutoff = date.fromisoformat(str(as_of or maximum))
            if cutoff > maximum: raise ValueError('As-of exceeds warehouse observation coverage')
            result['returns'] = core(s, 'returns', start, end, filters, cutoff)
            if set(filters) & kpis.PRODUCT_FILTERS:
                result['refunds'] = []
                result['notices'].append('Cash refund transactions cannot be allocated to selected products; merchandise credits remain available.')
            else: result['refunds'] = core(s, 'refunds', start, end, filters)
        return result
