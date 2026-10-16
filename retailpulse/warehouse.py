"""Build privately from a pinned lake manifest, publish all reader views atomically."""
import hashlib
import json
import math
import time
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq
from psycopg import sql
from sqlalchemy import MetaData, text

from . import db, etl, loader

OBJECTS = (
    'dim_date', 'dim_product', 'dim_store', 'dim_customer', 'dim_channel', 'dim_order', 'dim_state',
    'fact_order_items', 'fact_sales', 'fact_returns', 'fact_refunds',
    'fact_inventory_daily', 'fact_inventory_movements', 'fact_footfall_daily',
    'warehouse_sales_events', 'warehouse_sales_detail', 'warehouse_monthly_revenue',
)


def migrate(e):
    with loader.locked(e) as c, c.begin():
        etl.require_migration(c)
        c.connection.driver_connection.execute((db.ROOT / 'sql/008_warehouse.sql').read_text())
    return 'Warehouse metadata v1 applied; source and lake unchanged'


def require_migration(c):
    if not c.exec_driver_sql("SELECT to_regclass('etl.warehouse_version')").scalar():
        raise ValueError('Run python -m retailpulse migrate-warehouse first')
    if c.exec_driver_sql('SELECT version FROM etl.warehouse_version').scalar() != 1:
        raise ValueError('Unsupported warehouse version')


def manifest_hash(manifest):
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def check_freshness(row, max_age_hours, now=None):
    if not math.isfinite(max_age_hours) or max_age_hours <= 0 or max_age_hours > 24 * 365:
        raise ValueError('Snapshot max age must be between 0 and 8760 hours')
    now = now or datetime.now(timezone.utc)
    if row['status'] != 'completed' or row['completed_at'] is None or row['manifest'] is None:
        raise ValueError('Latest published snapshot is incomplete')
    # Freshness starts at source snapshot acquisition, not completion of a slow scan.
    age = (now - row['started_at']).total_seconds()
    if age < 0 or age > max_age_hours * 3600:
        raise ValueError('Published snapshot is stale; run etl-run before warehouse-load')
    if row['manifest']['run_id'] != str(row['run_id']):
        raise ValueError('Snapshot lineage mismatch')


def compatible(c, manifest):
    metadata = MetaData()
    metadata.reflect(bind=c, schema='source', postgresql_ignore_search_path=True)
    for name in etl.TABLES:
        table = metadata.tables['source.' + name]
        specs = {r.column_name: (r.numeric_precision, r.numeric_scale) for r in c.execute(text(
            "SELECT column_name,numeric_precision,numeric_scale FROM information_schema.columns "
            "WHERE table_schema='source' AND table_name=:name AND numeric_precision IS NOT NULL"), {'name': name})}
        entry = manifest['tables'][name]
        if entry['schema'] != etl.schema_token(etl.arrow_schema(table, specs)):
            raise ValueError(f'Unsupported warehouse input schema: {name}')
        if entry['primary_key'] != [col.name for col in table.primary_key.columns]:
            raise ValueError(f'Warehouse input primary key mismatch: {name}')
    return metadata


def disk_preflight(c, root, manifest, pg_data_dir=None):
    # Check actual database/WAL filesystems using the established local-only guard.
    paths = loader.disk_paths(c, pg_data_dir)
    size = 0
    for table in manifest['tables'].values():
        for part in table['partitions'].values():
            with pq.ParquetFile(root / part['file']) as parquet:
                size += sum(parquet.metadata.row_group(i).total_byte_size
                            for i in range(parquet.metadata.num_row_groups))
    # Staging, indexes, final facts, retained previous build and WAL need headroom.
    required = 3 * 1024**3 + 8 * size
    for path in paths:
        free = shutil.disk_usage(path).free
        etl.log('warehouse_disk', path=str(path), free_bytes=free, required_bytes=required)
        if free < required:
            raise ValueError('Insufficient warehouse disk headroom; free space before retry (do not delete pg_wal)')
    return required, paths


def copy_table(c, root, manifest, name, schema, paths):
    entry = manifest['tables'][name]
    for part in entry['partitions'].values():
        with pq.ParquetFile(root / part['file']) as parquet:
            for batch in parquet.iter_batches(batch_size=etl.BATCH_ROWS):
                if any(shutil.disk_usage(path).free < 3 * 1024**3 + 2 * batch.nbytes for path in paths):
                    raise ValueError('Warehouse disk reserve reached; free space and retry')
                with c.begin():
                    statement = sql.SQL('COPY {}.{} ({}) FROM STDIN').format(
                        sql.Identifier(schema), sql.Identifier('src_' + name),
                        sql.SQL(',').join(map(sql.Identifier, batch.schema.names)))
                    with c.connection.driver_connection.cursor().copy(statement) as copy:
                        for row in batch.to_pylist():
                            copy.write_row([row[col] for col in batch.schema.names])


def validate_replicas(c, metadata, schema, manifest):
    if not c.exec_driver_sql(f"SELECT EXISTS(SELECT 1 FROM \"{schema}\".src_ingestion_batches) AND NOT EXISTS(SELECT 1 FROM \"{schema}\".src_ingestion_batches WHERE status<>'LOADED')").scalar():
        raise ValueError('Snapshot contains incomplete operational ingestion')
    for name in etl.TABLES:
        count = c.exec_driver_sql(f'SELECT count(*) FROM "{schema}".src_{name}').scalar()
        if count != manifest['tables'][name]['rows']:
            raise ValueError(f'Warehouse staging count mismatch: {name}')
        table = metadata.tables['source.' + name]
        for fk in table.foreign_key_constraints:
            cols = ','.join('"' + x.parent.name + '"' for x in fk.elements)
            targets = ','.join('"' + x.column.name + '"' for x in fk.elements)
            target = next(iter(fk.elements)).column.table.name
            c.exec_driver_sql(f'ALTER TABLE "{schema}".src_{name} ADD FOREIGN KEY ({cols}) '
                              f'REFERENCES "{schema}".src_{target} ({targets})')
    reference = (db.ROOT / 'sql/003_analytics.sql').read_text()
    c.exec_driver_sql(reference.replace('source.', f'"{schema}".src_').replace('analytics.', f'"{schema}".'))
    for file in ('004_verify.sql', '006_seed_verify.sql'):
        query = (db.ROOT / 'sql' / file).read_text().replace('source.', f'"{schema}".src_').replace('analytics.', f'"{schema}".')
        failures = [r[0] for r in c.exec_driver_sql(query) if r[1]]
        if failures:
            raise ValueError('Lake business reconciliation failed: ' + ', '.join(failures))


def totals(c, prefix):
    return {
        'sales': [str(v) for v in c.exec_driver_sql(f'SELECT COALESCE(sum(net_sales),0),COALESCE(sum(net_cogs),0),COALESCE(sum(net_units),0),COALESCE(sum(tax_value),0) FROM {prefix}.warehouse_sales_events').one()],
        'bookings': [str(v) for v in c.exec_driver_sql(f'SELECT COALESCE(sum(booked_gmv),0),COALESCE(sum(discount),0),COALESCE(sum(ordered_units),0),COALESCE(sum(cancelled_units),0) FROM {prefix}.fact_order_items').one()],
        'inventory': [str(v) for v in c.exec_driver_sql(f'SELECT COALESCE(sum(sellable),0),COALESCE(sum(reserved),0),COALESCE(sum(in_transit),0),COALESCE(sum(damaged),0) FROM {prefix}.fact_inventory_daily').one()],
        'refunds': [str(v) for v in c.exec_driver_sql(f"SELECT COALESCE(sum(customer_refund_value),0) FROM {prefix}.fact_refunds WHERE status='SUCCEEDED'").one()],
        'footfall': [str(v) for v in c.exec_driver_sql(f'SELECT COALESCE(sum(valid_entries),0),COALESCE(sum(completed_orders),0) FROM {prefix}.fact_footfall_daily').one()],
    }


def reconcile(c, schema):
    # Independent, approved source-reference views run against lake replicas.
    checks = [
        ('booking', 'SELECT sum(booked_gmv),sum(discount),sum(booked_merchandise),sum(ordered_units) FROM bookings',
         'SELECT sum(booked_gmv),sum(discount),sum(booked_merchandise),sum(ordered_units) FROM fact_order_items'),
        ('sales', 'SELECT sum(gross_sales),sum(tax),sum(cogs),sum(quantity) FROM recognized_sales',
         'SELECT sum(gross_sales),sum(tax),sum(cogs),sum(units) FROM fact_sales'),
        ('credits', 'SELECT sum(merchandise_credit),sum(tax_credit),sum(reversed_cogs),sum(quantity) FROM return_credits',
         'SELECT sum(merchandise_credit),sum(tax_credit),sum(reversed_cogs),sum(units) FROM fact_returns'),
        ('refunds', "SELECT sum(amount) FROM src_payment_transactions WHERE type='REFUND' AND status='SUCCEEDED'",
         "SELECT sum(customer_refund_value) FROM fact_refunds WHERE status='SUCCEEDED'"),
        ('inventory', 'SELECT sum(sellable),sum(reserved),sum(in_transit),sum(damaged) FROM src_inventory_snapshots',
         'SELECT sum(sellable),sum(reserved),sum(in_transit),sum(damaged) FROM fact_inventory_daily'),
        ('footfall', 'SELECT sum(valid_entries),sum(completed_orders) FROM store_conversion',
         'SELECT sum(valid_entries),sum(completed_orders) FROM fact_footfall_daily'),
    ]
    for label, expected, actual in checks:
        if tuple(c.exec_driver_sql(expected).one()) != tuple(c.exec_driver_sql(actual).one()):
            raise ValueError(f'Warehouse reconciliation failed: {label}')
    if c.exec_driver_sql('SELECT count(*) FROM fact_returns r LEFT JOIN fact_sales s USING(invoice_line_id) WHERE s.invoice_line_id IS NULL').scalar():
        raise ValueError('Orphan return')
    return {name: c.exec_driver_sql(f'SELECT count(*) FROM "{schema}".{name}').scalar() for name in OBJECTS[:-3]}


def publish(c, schema, run_id, snapshot_id, root, counts):
    # All reader bindings and lineage change in this one transaction.
    with c.begin():
        latest = c.execute(text('SELECT run_id FROM etl.extract_heads WHERE lake_root=:root'), {'root': str(root)}).scalar()
        if str(latest) != snapshot_id:
            raise ValueError('Snapshot superseded before warehouse publication')
        for name in OBJECTS:
            c.exec_driver_sql(f'CREATE OR REPLACE VIEW analytics.{name} AS SELECT * FROM "{schema}".{name}')
        c.execute(text("UPDATE etl.warehouse_runs SET status='completed',completed_at=clock_timestamp(),"
                       'counts=CAST(:counts AS jsonb),totals=CAST(:totals AS jsonb) WHERE run_id=:id'),
                  {'id': run_id, 'counts': json.dumps(counts), 'totals': json.dumps(totals(c, '"'+schema+'"'))})
        c.execute(text('INSERT INTO etl.warehouse_head VALUES(true,:id) ON CONFLICT(singleton) DO UPDATE SET run_id=EXCLUDED.run_id'), {'id': run_id})


def run(e, root='data', max_age_hours=24, preflight_only=False, pg_data_dir=None):
    root = Path(root).expanduser().resolve()
    run_id = str(uuid.uuid4())
    schema = 'wh_' + uuid.UUID(run_id).hex
    context = {'phase': 'preflight'}
    started = time.monotonic()
    with loader.locked(e) as c, etl.lake_lock(root):
        require_migration(c)
        row = c.execute(text('SELECT r.* FROM etl.extract_heads h JOIN etl.extract_runs r USING(run_id) '
                             'WHERE h.lake_root=:root'), {'root': str(root)}).mappings().first()
        if row is None:
            raise ValueError('No published ETL snapshot; run etl-run first')
        check_freshness(row, max_age_hours)
        manifest = row['manifest']
        snapshot_id = str(row['run_id'])
        etl.validate_manifest(root, manifest)
        metadata = compatible(c, manifest)
        fingerprint = manifest_hash(manifest)
        current = c.execute(text('SELECT r.*,x.started_at snapshot_started_at FROM etl.warehouse_head h '
                                 'JOIN etl.warehouse_runs r USING(run_id) JOIN etl.extract_runs x ON x.run_id=r.snapshot_id')).mappings().first()
        c.commit()
        if current and row['started_at'] < current['snapshot_started_at']:
            raise ValueError('Snapshot predates the published warehouse; refusing rollback')
        if current and current['manifest_hash'] == fingerprint and current['lake_root'] == str(root):
            verify(e)
            return {'status': 'unchanged', 'run_id': str(current['run_id']), 'snapshot_id': snapshot_id}
        required, paths = disk_preflight(c, root, manifest, pg_data_dir)
        c.commit()
        if preflight_only:
            return {'preflight': 'passed', 'required_free_bytes': required, 'snapshot_id': snapshot_id}
        with c.begin():
            c.exec_driver_sql('SET synchronous_commit=on')
            c.execute(text("UPDATE etl.warehouse_runs SET status='failed',completed_at=clock_timestamp(),error_class='ProcessInterrupted' WHERE status='loading'"))
            c.execute(text("INSERT INTO etl.warehouse_runs(run_id,snapshot_id,lake_root,build_schema,manifest_hash,status) "
                           "VALUES(:id,:snapshot,:root,:schema,:hash,'loading')"),
                      {'id': run_id, 'snapshot': snapshot_id, 'root': str(root), 'schema': schema, 'hash': fingerprint})
            c.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        try:
            for name in etl.TABLES:
                context = {'table': name, 'phase': 'copy'}
                with c.begin():
                    c.exec_driver_sql(f'CREATE UNLOGGED TABLE "{schema}".src_{name} '
                                      f'(LIKE source.{name} INCLUDING CONSTRAINTS INCLUDING INDEXES)')
                copy_table(c, root, manifest, name, schema, paths)
                etl.log('warehouse_table_copied', run_id=run_id, table=name, rows=manifest['tables'][name]['rows'],
                        elapsed_seconds=round(time.monotonic()-started, 2))
            if any(shutil.disk_usage(path).free < 3 * 1024**3 for path in paths):
                raise ValueError('Warehouse disk reserve reached before model build')
            context = {'phase': 'validation_and_model'}
            with c.begin():
                c.exec_driver_sql(f'SET LOCAL search_path TO "{schema}",pg_catalog')
                c.exec_driver_sql("SET LOCAL work_mem='16MB'")
                validate_replicas(c, metadata, schema, manifest)
                c.exec_driver_sql((db.ROOT / 'sql/009_warehouse_model.sql').read_text())
                counts = reconcile(c, schema)
                # Only private replicas/reference views are removed, never source.
                for name in etl.TABLES:
                    c.exec_driver_sql(f'DROP TABLE "{schema}".src_{name} CASCADE')
            # Rehash after COPY to detect accidental file changes during ingestion.
            etl.validate_manifest(root, manifest)
            check_freshness(row, max_age_hours)
            if c.invalidated or c.connection.driver_connection.closed:
                raise ConnectionError('Warehouse lock session lost')
            context = {'phase': 'publication'}
            publish(c, schema, run_id, snapshot_id, root, counts)
            etl.log('warehouse_completed', run_id=run_id, snapshot_id=snapshot_id, counts=counts)
            return {'status': 'completed', 'run_id': run_id, 'snapshot_id': snapshot_id, 'rows': counts}
        except BaseException as exc:
            if isinstance(exc, ValueError):
                context = {**context, 'detail': str(exc)}
            etl.log('warehouse_failed', run_id=run_id, error_class=type(exc).__name__, **context)
            try:
                if c.invalidated or c.connection.driver_connection.closed:
                    raise ConnectionError('Lock connection lost')
                c.rollback()
                with c.begin():
                    # Do not mislabel an ambiguously successful publication.
                    c.execute(text("UPDATE etl.warehouse_runs SET status='failed',completed_at=clock_timestamp(),"
                                   'error_class=:error,error_context=CAST(:context AS jsonb) '
                                   "WHERE run_id=:id AND status='loading'"),
                              {'id': run_id, 'error': type(exc).__name__, 'context': json.dumps(context)})
            except Exception:
                etl.log('warehouse_failure_metadata_unavailable', run_id=run_id)
            raise


def verify(e):
    with e.connect().execution_options(isolation_level='REPEATABLE READ') as c, c.begin():
        require_migration(c)
        row = c.execute(text('SELECT r.* FROM etl.warehouse_head h JOIN etl.warehouse_runs r USING(run_id)')).mappings().first()
        if row is None or row['status'] != 'completed':
            raise ValueError('No published warehouse; run warehouse-load')
        for name, count in row['counts'].items():
            if name not in OBJECTS or c.exec_driver_sql(f'SELECT count(*) FROM analytics.{name}').scalar() != count:
                raise ValueError('Warehouse row count mismatch')
        if totals(c, 'analytics') != row['totals']:
            raise ValueError('Warehouse financial/operational totals differ from publication audit')
        invalid = c.execute(text("SELECT count(*) FROM pg_constraint WHERE connamespace=to_regnamespace(:s) AND NOT convalidated"),
                            {'s': row['build_schema']}).scalar()
        if invalid:
            raise ValueError('Unvalidated warehouse constraints')
        return {'warehouse_checks': 'passed', 'run_id': str(row['run_id']), 'snapshot_id': str(row['snapshot_id']), 'rows': row['counts']}


def status(e):
    with e.connect() as c:
        require_migration(c)
        return {'runs': [dict(r) for r in c.execute(text('SELECT r.*,h.run_id IS NOT NULL AS published '
            'FROM etl.warehouse_runs r LEFT JOIN etl.warehouse_head h USING(run_id) ORDER BY started_at DESC LIMIT 20')).mappings()]}


def cleanup(e, include_previous=False):
    """Explicitly reclaim audited, unpublished builds; never touch lake/source/head."""
    removed = []
    with loader.locked(e) as c:
        require_migration(c)
        c.execute(text("UPDATE etl.warehouse_runs SET status='failed',completed_at=clock_timestamp(),error_class='ProcessInterrupted' WHERE status='loading'"))
        c.commit()
        rows = c.execute(text("SELECT r.* FROM etl.warehouse_runs r LEFT JOIN etl.warehouse_head h USING(run_id) "
                              "WHERE h.run_id IS NULL AND r.storage_removed_at IS NULL")).mappings().all()
        c.commit()
        for row in rows:
            if row['status'] == 'completed' and not include_previous:
                continue
            schema = 'wh_' + uuid.UUID(str(row['run_id'])).hex
            if schema != row['build_schema']:
                raise ValueError('Unrecognized build schema; refusing cleanup')
            with c.begin():
                external = c.execute(text('''SELECT EXISTS(
                    SELECT 1 FROM pg_depend d JOIN pg_rewrite r ON r.oid=d.objid
                    JOIN pg_class v ON v.oid=r.ev_class JOIN pg_class target ON target.oid=d.refobjid
                    WHERE d.classid='pg_rewrite'::regclass AND d.refclassid='pg_class'::regclass
                    AND target.relnamespace=to_regnamespace(:schema) AND v.relnamespace<>target.relnamespace
                    UNION ALL SELECT 1 FROM pg_constraint k JOIN pg_class target ON target.oid=k.confrelid
                    WHERE target.relnamespace=to_regnamespace(:schema) AND k.connamespace<>target.relnamespace)'''),
                    {'schema': schema}).scalar()
                if external:
                    raise ValueError('External dependency on private warehouse build; refusing cleanup')
                c.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
                c.execute(text('UPDATE etl.warehouse_runs SET storage_removed_at=clock_timestamp() WHERE run_id=:id'), {'id': row['run_id']})
            removed.append(schema)
    return {'removed_unpublished_builds': removed}
