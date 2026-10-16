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
    for name in ['doctor','init','verify-db','seed-status','migrate-loader']: sub.add_parser(name)
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
