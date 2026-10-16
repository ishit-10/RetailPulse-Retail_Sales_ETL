import argparse
import json
import logging
import sys
from .generate import Config, generate
from .verify import load_dataset, validate
from . import db

def main():
    p=argparse.ArgumentParser(description='RetailPulse — entirely synthetic local retail data')
    sub=p.add_subparsers(dest='command',required=True)
    for name in ['doctor','init','verify-db','seed-status','migrate-loader','migrate-etl','migrate-warehouse','verify-warehouse','warehouse-status']: sub.add_parser(name)
    g=sub.add_parser('generate'); g.add_argument('--orders',type=int,default=100000); g.add_argument('--seed',type=int,default=42)
    g.add_argument('--start',default='2025-01-01'); g.add_argument('--end',default='2026-06-30')
    g.add_argument('--styles',type=int,default=40); g.add_argument('--customers',type=int,default=20000)
    g.add_argument('--out',default='data/raw/seed-42')
    for name in ['seed','verify-files']:
        s=sub.add_parser(name); s.add_argument('--data',default='data/raw/seed-42')
        if name == 'seed':
            s.add_argument('--chunk-rows', type=int, default=5000)
            s.add_argument('--pg-data-dir')
            s.add_argument('--preflight-only', action='store_true')
            s.add_argument('--low-disk', action='store_true',
                           help='Opt in to guarded 3 GiB reserve and at most 1000 rows per chunk')
    for name in ['etl-run', 'etl-status', 'verify-lake']:
        command = sub.add_parser(name)
        command.add_argument('--lake-root', default='data')
    cleanup=sub.add_parser('warehouse-cleanup')
    cleanup.add_argument('--include-previous', action='store_true')
    w=sub.add_parser('warehouse-load')
    w.add_argument('--lake-root', default='data')
    w.add_argument('--max-snapshot-age-hours', type=float, default=24)
    w.add_argument('--preflight-only', action='store_true')
    w.add_argument('--pg-data-dir')
    k=sub.add_parser('warehouse-kpi')
    k.add_argument('report', choices=['sales','daily','weekly','monthly','products','categories','stores','channels','returns','refunds','footfall','inventory','sell-through'])
    k.add_argument('--start', required=True)
    k.add_argument('--end', required=True)
    k.add_argument('--as-of')
    k.add_argument('--filters', default='{}', help='JSON object: city/state_id/store_id/channel/category_id/style_id/sku_id/customer_id')
    r=sub.add_parser('reset'); r.add_argument('--confirm-database',required=True)
    a=p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if a.command=='generate':
            result=generate(Config(a.orders,a.seed,a.start,a.end,a.styles,a.customers),a.out)
        elif a.command=='verify-files':
            manifest,tables=load_dataset(a.data); result=validate(tables,manifest['config']['orders'])
        else:
            e=db.engine()
            try:
                if a.command=='doctor': result=db.doctor(e)
                elif a.command=='init': result=db.initialize(e)
                elif a.command=='seed': result=db.seed(e,a.data,chunk_rows=a.chunk_rows,pg_data_dir=a.pg_data_dir,preflight_only=a.preflight_only,low_disk=a.low_disk)
                elif a.command in ('migrate-etl', 'etl-run', 'etl-status', 'verify-lake'):
                    from . import etl
                    if a.command == 'migrate-etl': result = etl.migrate(e)
                    elif a.command == 'etl-run': result = etl.run(e, a.lake_root)
                    elif a.command == 'etl-status': result = etl.status(e, a.lake_root)
                    else: result = etl.verify(e, a.lake_root)
                elif a.command in ('migrate-warehouse','warehouse-load','verify-warehouse','warehouse-status','warehouse-kpi','warehouse-cleanup'):
                    from . import warehouse, kpis
                    if a.command == 'migrate-warehouse': result = warehouse.migrate(e)
                    elif a.command == 'warehouse-load': result = warehouse.run(e, a.lake_root, a.max_snapshot_age_hours, a.preflight_only, a.pg_data_dir)
                    elif a.command == 'verify-warehouse': result = warehouse.verify(e)
                    elif a.command == 'warehouse-cleanup': result = warehouse.cleanup(e, a.include_previous)
                    elif a.command == 'warehouse-status': result = warehouse.status(e)
                    else: result = kpis.report(e, a.report, a.start, a.end, json.loads(a.filters), a.as_of)
                elif a.command=='migrate-loader':
                    from .loader import migrate
                    result=migrate(e)
                elif a.command=='seed-status':
                    from .loader import status
                    result=status(e)
                elif a.command=='verify-db': result=db.verify_database(e)
                else: result=db.reset(e,a.confirm_database)
            finally: e.dispose()
        print(json.dumps(result,indent=2,default=str))
    except Exception as exc:
        # Connection errors may contain credentials; report class and a safe recovery path.
        from sqlalchemy.exc import SQLAlchemyError
        import psycopg
        message='Database operation failed. Check local service, credentials, schema and README troubleshooting.' if isinstance(exc,(SQLAlchemyError,psycopg.Error)) else str(exc)
        print(f'{type(exc).__name__}: {message}',file=sys.stderr)
        return 1
    return 0

if __name__=='__main__': sys.exit(main())
