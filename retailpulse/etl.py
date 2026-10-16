"""Consistent full-scan change detection, incremental immutable Parquet publication.

PostgreSQL owns the publication pointer. Never glob the object directories: old
versions deliberately coexist. Readers pin one manifest via current_manifest().
"""
from contextlib import contextmanager
import base64
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import time
import uuid

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import Boolean, Date, DateTime, Integer, MetaData, Numeric, String, Text, cast, func, select, text

from . import db, loader

LOG = logging.getLogger(__name__)
TABLES = ['ingestion_batches'] + loader.TABLES
BUCKETS = 16
BATCH_ROWS = 5000
RESERVE = 3 * 1024**3


def log(event, **fields):
    LOG.info(json.dumps({'event': event, **fields}, sort_keys=True))


def migrate(e):
    with loader.locked(e) as c, c.begin():
        loader.require_migration(c)
        c.connection.driver_connection.execute((db.ROOT / 'sql/007_extract_checkpoints.sql').read_text())
    return 'Stage 3 ETL metadata v1 applied; source schema unchanged'


def require_migration(c):
    if not c.exec_driver_sql("SELECT to_regclass('etl.extract_version')").scalar():
        raise ValueError('Run python -m retailpulse migrate-etl first')
    if c.exec_driver_sql('SELECT version FROM etl.extract_version').scalar() != 1:
        raise ValueError('Unsupported ETL metadata version')


def arrow_schema(table, numeric_specs=None):
    numeric_specs = numeric_specs or {}
    fields = []
    for col in table.columns:
        typ = col.type
        # PostgreSQL money_amount is a DOMAIN over numeric(16,2).
        while hasattr(typ, 'data_type'):
            typ = typ.data_type
        if isinstance(typ, Boolean): at = pa.bool_()
        elif isinstance(typ, Integer): at = pa.int64()
        elif isinstance(typ, Numeric):
            precision, scale = numeric_specs.get(col.name, (typ.precision, typ.scale))
            if precision is None or scale is None:
                raise ValueError(f'Missing numeric precision/scale: {table.name}.{col.name}')
            at = pa.decimal128(precision, scale)
        elif isinstance(typ, DateTime): at = pa.timestamp('us', tz='UTC') if typ.timezone else pa.timestamp('us')
        elif isinstance(typ, Date): at = pa.date32()
        elif isinstance(typ, String): at = pa.string()
        else: raise ValueError(f'Unsupported source type: {table.name}.{col.name}')
        fields.append(pa.field(col.name, at, nullable=col.nullable))
    return pa.schema(fields)


def schema_token(schema):
    return base64.b64encode(schema.serialize().to_pybytes()).decode('ascii')


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


def space(path, extra=0):
    if shutil.disk_usage(path).free < RESERVE + extra:
        raise ValueError('ETL disk reserve reached (3 GiB); free space and rerun etl-run')


@contextmanager
def lake_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    fsync_dir(root.parent)
    with (root / '.retailpulse-etl.lock').open('a') as f:
        try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise ValueError('Another ETL process owns this lake directory') from None
        try: yield
        finally: fcntl.flock(f, fcntl.LOCK_UN)


def write_partition(root, name, bucket, schema, keys, batches):
    """Stream one sorted partition; content-addressed files never replace live data."""
    raw = root / 'raw' / 'extract' / name / f'bucket={bucket:02d}'
    processed = root / 'processed' / 'extract' / name / f'bucket={bucket:02d}'
    raw.mkdir(parents=True, exist_ok=True)
    processed.mkdir(parents=True, exist_ok=True)
    # All lake directories must stay on one device for hardlinks and durability.
    if raw.stat().st_dev != processed.stat().st_dev:
        raise ValueError('Raw and processed extract directories must share one filesystem')
    temp = raw / '.writing.parquet'
    rows = 0
    previous = None
    try:
        space(root)
        with pq.ParquetWriter(temp, schema, compression='zstd') as writer:
            for records in batches:
                frame = pd.DataFrame(records, columns=schema.names, dtype=object)
                batch = pa.Table.from_pandas(frame, schema=schema, preserve_index=False, safe=True)
                for col in schema:
                    if not col.nullable and batch[col.name].null_count:
                        raise ValueError(f'Null in required column {name}.{col.name}')
                for record in records:
                    key = tuple(record[k] for k in keys)
                    if any(v is None for v in key) or (previous is not None and key <= previous):
                        raise ValueError(f'Duplicate or unordered primary key in {name}')
                    previous = key
                space(root, 2 * batch.nbytes)
                writer.write_table(batch)
                rows += batch.num_rows
        with temp.open('rb') as f: os.fsync(f.fileno())
        checksum = loader.digest(temp)
        target = raw / f'{checksum}.parquet'
        if target.exists():
            if loader.digest(target) != checksum:
                raise ValueError('Existing raw object is corrupt; restore it before retry')
            temp.unlink()
        else:
            os.replace(temp, target)
        fsync_dir(raw)
        dest = processed / target.name
        if not dest.exists(): os.link(target, dest)
        if loader.digest(dest) != checksum:
            raise ValueError('Existing processed object is corrupt')
        fsync_dir(processed)
        # Persist newly created ancestor entries as well as file entries.
        for directory in (raw.parent, raw.parent.parent, raw.parent.parent.parent,
                          processed.parent, processed.parent.parent, processed.parent.parent.parent, root):
            fsync_dir(directory)
        return {'raw': str(target.relative_to(root)), 'file': str(dest.relative_to(root)),
                'sha256': checksum, 'rows': rows}
    finally:
        temp.unlink(missing_ok=True)


def current_manifest(e, root):
    root = str(Path(root).expanduser().resolve())
    with e.connect() as c:
        require_migration(c)
        row = c.execute(text('SELECT r.manifest FROM etl.extract_heads h JOIN etl.extract_runs r '
                             'ON r.run_id=h.run_id WHERE h.lake_root=:root'), {'root': root}).first()
    if not row: raise ValueError('No published ETL snapshot; run etl-run')
    return row[0]


def validate_manifest(root, manifest):
    root = Path(root).resolve()
    counts = {}
    if manifest['format_version'] != 1 or set(manifest['tables']) != set(TABLES):
        raise ValueError('Unsupported or incomplete lake manifest')
    for name, entry in manifest['tables'].items():
        if set(entry['partitions']) != {str(i) for i in range(BUCKETS)}:
            raise ValueError(f'Missing partition: {name}')
        total = 0
        for part in entry['partitions'].values():
            for role in ('raw', 'file'):
                path = (root / part[role]).resolve()
                if not path.is_relative_to(root) or loader.digest(path) != part['sha256']:
                    raise ValueError(f'Lake checksum mismatch: {name}')
            with pq.ParquetFile(path) as parquet:
                if schema_token(parquet.schema_arrow.remove_metadata()) != entry['schema']:
                    raise ValueError(f'Lake schema mismatch: {name}')
                if parquet.metadata.num_rows != part['rows']:
                    raise ValueError(f'Lake row count mismatch: {name}')
            total += part['rows']
        if total != entry['rows']: raise ValueError(f'Lake total mismatch: {name}')
        counts[name] = total
    return counts


def batches(c, table, partition_column, bucket):
    query = select(table).where(((partition_column % BUCKETS) + BUCKETS) % BUCKETS == bucket).order_by(*table.primary_key.columns)
    result = c.execute(query.execution_options(stream_results=True))
    try:
        for chunk in result.mappings().partitions(BATCH_ROWS):
            yield [dict(row) for row in chunk]
    finally: result.close()


def publish(c, root, run_id, manifest, rows, changed):
    """Only transaction that moves the extraction checkpoint / reader pointer."""
    with c.begin():
        c.execute(text("UPDATE etl.extract_runs SET status='completed',completed_at=clock_timestamp(),"
                       'rows_scanned=:rows,partitions_changed=:changed,manifest=CAST(:manifest AS jsonb) '
                       'WHERE run_id=:id'),
                  {'rows': rows, 'changed': changed, 'manifest': json.dumps(manifest), 'id': run_id})
        c.execute(text('INSERT INTO etl.extract_heads VALUES(:root,:id) '
                       'ON CONFLICT(lake_root) DO UPDATE SET run_id=EXCLUDED.run_id'), {'root': str(root), 'id': run_id})


def run(e, root='data'):
    root = Path(root).expanduser().resolve()
    run_id = str(uuid.uuid4())
    started = time.monotonic()
    context = {}
    # Same database lock as Stage 2 prevents seed/reset/migration overlap.
    with loader.locked(e) as control, lake_lock(root):
        require_migration(control)
        control.exec_driver_sql('SET synchronous_commit=on')
        control.commit()
        with control.begin():
            control.exec_driver_sql('SET LOCAL synchronous_commit=on')
            control.execute(text("UPDATE etl.extract_runs SET status='failed',completed_at=clock_timestamp(),"
                                 "error_class='ProcessInterrupted' WHERE status='running' AND lake_root=:root"), {'root': str(root)})
            control.execute(text("INSERT INTO etl.extract_runs(run_id,lake_root,status) VALUES(:id,:root,'running')"),
                            {'id': run_id, 'root': str(root)})
        try:
            try: old = current_manifest(e, root)
            except ValueError: old = None
            if old: validate_manifest(root, old)
            manifest = {'format_version': 1, 'run_id': run_id, 'tables': {}}
            rows = changed = 0
            with e.connect().execution_options(isolation_level='REPEATABLE READ') as source, source.begin():
                source.exec_driver_sql('SET TRANSACTION READ ONLY')
                source.exec_driver_sql("SET LOCAL TIME ZONE 'UTC'")
                source.exec_driver_sql("SET LOCAL work_mem='16MB'")
                snapshot = source.exec_driver_sql('SELECT pg_current_snapshot()::text').scalar()
                if not source.exec_driver_sql("SELECT EXISTS(SELECT 1 FROM source.ingestion_batches) AND "
                                             "NOT EXISTS(SELECT 1 FROM source.ingestion_batches WHERE status<>'LOADED')").scalar():
                    raise ValueError('Source ingestion is incomplete; complete Stage 2 first')
                manifest['source_snapshot'] = snapshot
                with control.begin():
                    control.execute(text('UPDATE etl.extract_runs SET source_snapshot=:s WHERE run_id=:id'), {'s': snapshot, 'id': run_id})
                metadata = MetaData()
                metadata.reflect(bind=source, schema='source', postgresql_ignore_search_path=True)
                for name in TABLES:
                    table = metadata.tables['source.' + name]
                    # DOMAIN reflection may omit its base type modifier. PostgreSQL's
                    # information_schema resolves numeric precision/scale through domains.
                    numeric_specs = {
                        r.column_name: (r.numeric_precision, r.numeric_scale)
                        for r in source.execute(text(
                            "SELECT column_name,numeric_precision,numeric_scale "
                            "FROM information_schema.columns "
                            "WHERE table_schema='source' AND table_name=:name "
                            "AND numeric_precision IS NOT NULL"), {'name': name})
                    }
                    schema = arrow_schema(table, numeric_specs)
                    token = schema_token(schema)
                    if old and old['tables'][name]['schema'] != token:
                        raise ValueError(f'Source schema changed: {name}; explicit lake format migration required')
                    keys = [col.name for col in table.primary_key.columns]
                    numeric_key = next((col for col in table.primary_key.columns if isinstance(col.type, Integer)), None)
                    if numeric_key is None:
                        first_key = list(table.primary_key.columns)[0]
                        numeric_key = func.get_byte(func.decode(func.md5(cast(first_key, Text)), 'hex'), 0)
                    entry = {'schema': token, 'primary_key': keys, 'partitions': {}, 'rows': 0}
                    for bucket in range(BUCKETS):
                        context = {'table': name, 'bucket': bucket}
                        part = write_partition(root, name, bucket, schema, keys, batches(source, table, numeric_key, bucket))
                        entry['partitions'][str(bucket)] = part
                        entry['rows'] += part['rows']
                        changed += int(old is None or part != old['tables'][name]['partitions'][str(bucket)])
                    count = source.execute(select(func.count()).select_from(table)).scalar()
                    if count != entry['rows']: raise ValueError(f'Source count reconciliation failed: {name}')
                    manifest['tables'][name] = entry
                    rows += count
                    with control.begin():
                        control.execute(text('UPDATE etl.extract_runs SET rows_scanned=:rows,partitions_changed=:changed WHERE run_id=:id'),
                                        {'rows': rows, 'changed': changed, 'id': run_id})
                    log('table_extracted', run_id=run_id, table=name, rows=count, elapsed_seconds=round(time.monotonic()-started, 2))
                context = {'phase': 'source_reconciliation'}
                failures = db.verify_connection(source)
                failures += [r[0] for r in source.exec_driver_sql((db.ROOT / 'sql/006_seed_verify.sql').read_text()) if r[1]]
                if failures: raise ValueError('Source business validation failed: ' + ', '.join(failures))
                # All reflected source FK constraints must remain validated.
                if source.exec_driver_sql("SELECT count(*) FROM pg_constraint WHERE connamespace='source'::regnamespace AND contype IN ('f','c') AND NOT convalidated").scalar():
                    raise ValueError('Unvalidated source constraints')
                context = {'phase': 'lake_reconciliation'}
                validate_manifest(root, manifest)
            # Verify lock session is still alive before publishing. It must not reconnect.
            if control.invalidated or control.connection.driver_connection.closed:
                raise ConnectionError('ETL lock session lost')
            space(root)
            context = {'phase': 'publication'}
            publish(control, root, run_id, manifest, rows, changed)
            log('etl_completed', run_id=run_id, rows=rows, partitions_changed=changed)
            return {'run_id': run_id, 'status': 'completed', 'rows_scanned': rows, 'partitions_changed': changed}
        except BaseException as exc:
            if isinstance(exc, ValueError):
                context = {**context, 'detail': str(exc)}
            log('etl_failed', run_id=run_id, error_class=type(exc).__name__, **context)
            # Never overwrite a committed completion after an ambiguous commit outcome.
            try:
                if control.invalidated or control.connection.driver_connection.closed: raise ConnectionError()
                control.rollback()
                with control.begin():
                    control.execute(text("UPDATE etl.extract_runs SET status='failed',completed_at=clock_timestamp(),"
                                         'error_class=:error,error_context=CAST(:context AS jsonb) '
                                         "WHERE run_id=:id AND status='running'"),
                                    {'error': type(exc).__name__, 'context': json.dumps(context), 'id': run_id})
            except Exception:
                log('failure_metadata_unavailable', run_id=run_id)
            raise


def status(e, root='data'):
    with e.connect() as c:
        require_migration(c)
        rows = c.execute(text('SELECT run_id,started_at,completed_at,status,rows_scanned,partitions_changed,error_class,error_context '
                              'FROM etl.extract_runs WHERE lake_root=:root ORDER BY started_at DESC LIMIT 20'),
                         {'root': str(Path(root).expanduser().resolve())}).mappings().all()
    return {'runs': [dict(row) for row in rows]}


def verify(e, root='data'):
    manifest = current_manifest(e, root)
    return {'run_id': manifest['run_id'], 'lake_checks': 'passed', 'rows': validate_manifest(root, manifest)}


def read_table(e, name, root='data', manifest=None):
    """Pin a manifest once and pass it for consistent multi-table reads."""
    if name not in TABLES: raise ValueError('Unknown lake table')
    manifest = manifest or current_manifest(e, root)
    entry = manifest['tables'][name]
    schema = pa.ipc.read_schema(pa.BufferReader(base64.b64decode(entry['schema'])))
    return pa.concat_tables([pq.ParquetFile(Path(root) / p['file']).read() for p in entry['partitions'].values()]).cast(schema)
