from pathlib import Path
import os
from datetime import datetime, timezone
from decimal import Decimal
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, URL, MetaData, Integer, Numeric, text
from psycopg import sql
from .verify import load_dataset, validate

ROOT=Path(__file__).resolve().parent.parent
LOAD_ORDER='states gst_registrations locations customers categories product_styles skus price_versions tax_rules promotions sales_orders order_lines order_line_discounts order_line_cancellations order_status_events fulfillments fulfillment_items sales_invoices invoice_lines return_requests return_items credit_notes credit_note_lines payment_transactions payment_status_events stock_lots inventory_movements fulfillment_cost_allocations return_cost_allocations inventory_reservations inventory_policies inventory_snapshots store_footfall_daily digital_traffic_daily demand_rejections'.split()

def engine(test_url=None):
    load_dotenv(ROOT/'.env')
    if test_url:
        result=create_engine(test_url,connect_args={'connect_timeout':5})
    else:
        required=['PGDATABASE','PGUSER','PGPASSWORD']
        missing=[k for k in required if not os.getenv(k)]
        if missing: raise ValueError('Set '+', '.join(missing)+' in .env first')
        result=create_engine(URL.create('postgresql+psycopg',username=os.environ['PGUSER'],password=os.environ['PGPASSWORD'],
                                        host=os.getenv('PGHOST','127.0.0.1'),port=int(os.getenv('PGPORT','5432')),database=os.environ['PGDATABASE']),
                             connect_args={'connect_timeout':5,'sslmode':os.getenv('PGSSLMODE','prefer')})
    if result.url.host not in ['localhost','127.0.0.1','::1']:
        raise ValueError('This local-only project requires a loopback database host')
    return result

def doctor(e):
    with e.connect() as c:
        row=c.execute(text('SELECT current_database() database, current_user username, version() version')).mappings().one()
        return dict(row)

def initialize(e):
    with e.begin() as c:
        c.execute(text('SELECT pg_advisory_xact_lock(847210)'))
        if c.execute(text("SELECT to_regclass('etl.schema_version')")).scalar():
            if c.execute(text('SELECT version FROM etl.schema_version')).scalar()!=1: raise ValueError('Unknown schema version')
            return 'Schema v1 already initialized'
        if c.execute(text("SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname IN ('source','analytics','etl'))")).scalar():
            raise ValueError('Existing schemas found; will not overwrite them')
        for filename in ['001_schema.sql','002_guards.sql','003_analytics.sql']:
            c.connection.driver_connection.execute((ROOT/'sql'/filename).read_text())
        c.exec_driver_sql('CREATE TABLE etl.schema_version(version integer PRIMARY KEY); INSERT INTO etl.schema_version VALUES(1)')
    return 'Schema v1 initialized'

def seed(e, path):
    manifest,tables=load_dataset(path)
    validate(tables,manifest['config']['orders'])
    with e.begin() as c:
        c.execute(text('SELECT pg_advisory_xact_lock(847210)'))
        old=c.execute(text('SELECT checksum FROM source.ingestion_batches WHERE batch_id=1')).scalar()
        if old:
            if old==manifest['dataset_id']: return 'Dataset already loaded; no changes'
            raise ValueError('Different dataset is loaded; use explicit reset or a separate database')
        c.execute(text("INSERT INTO source.ingestion_batches VALUES(1,:id,:path,:checksum,:at,'LOADED')"),
                  {'id':manifest['dataset_id'],'path':str(Path(path).resolve()),'checksum':manifest['dataset_id'],'at':datetime.now(timezone.utc)})
        metadata=MetaData(); metadata.reflect(bind=c,schema='source')
        for name in ['source_events']+LOAD_ORDER:
            if name not in tables: continue
            table=metadata.tables['source.'+name]; frame=tables[name]
            columns=list(frame.columns)
            query=sql.SQL('COPY {} ({}) FROM STDIN').format(sql.Identifier('source',name),sql.SQL(',').join(map(sql.Identifier,columns)))
            with c.connection.driver_connection.cursor().copy(query) as cp:
                for row in frame.itertuples(index=False,name=None):
                    values=[]
                    for col,value in zip(columns,row):
                        if pd.isna(value): value=None
                        elif isinstance(table.c[col].type,Integer): value=int(value)
                        elif isinstance(table.c[col].type,Numeric): value=Decimal(str(value))
                        elif isinstance(value,pd.Timestamp): value=value.to_pydatetime()
                        values.append(value)
                    cp.write_row(values)
        failures=verify_connection(c)
        if failures: raise ValueError('Database checks failed: '+', '.join(failures))
    return 'Dataset loaded atomically and verified'

def verify_connection(c):
    rows=c.exec_driver_sql((ROOT/'sql'/'004_verify.sql').read_text()).fetchall()
    return [row[0] for row in rows if row[1]!=0]

def verify_database(e):
    with e.connect() as c:
        failures=verify_connection(c)
        if failures: raise ValueError('Database checks failed: '+', '.join(failures))
        counts={name:c.exec_driver_sql('SELECT count(*) FROM source.'+name).scalar() for name in LOAD_ORDER}
    return {'database_checks':'passed','rows':counts}

def reset(e, confirmation):
    db=e.url.database
    if confirmation!=db or db not in ['retailpulse','retailpulse_test']:
        raise ValueError('Reset needs --confirm-database matching retailpulse or retailpulse_test')
    with e.begin() as c:
        c.execute(text('SELECT pg_advisory_xact_lock(847210)'))
        # No CASCADE: fail if unknown dependent objects exist.
        names=['source_events','ingestion_batches']+LOAD_ORDER
        query=sql.SQL('TRUNCATE {}').format(sql.SQL(',').join(sql.Identifier('source',name) for name in names))
        c.connection.driver_connection.execute(query)
    return 'Synthetic rows cleared; schema retained'
