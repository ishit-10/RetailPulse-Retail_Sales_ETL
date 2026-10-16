"""Stage 2 single-writer loader. Checkpoints and COPY rows commit together."""
from contextlib import contextmanager
from decimal import Decimal
import hashlib
import json
import logging
from pathlib import Path
import shutil
import time

import pyarrow.parquet as pq
from psycopg import sql
from sqlalchemy import Integer, MetaData, Numeric, text

from .db import LOAD_ORDER, ROOT, verify_connection

LOG = logging.getLogger(__name__)
TABLES = ['source_events'] + LOAD_ORDER
REQUIRED_TABLES = {'source_events', 'sales_orders', 'order_lines', 'skus',
                   'payment_transactions', 'inventory_movements', 'inventory_snapshots'}
LOCK = 847210
GIB = 1024 ** 3
RESERVE = 10 * GIB


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def inspect_dataset(path):
    """Validate identity and every file without materializing the full dataset."""
    path = Path(path).expanduser().resolve()
    manifest = json.loads((path / 'manifest.json').read_text())
    identity = dict(manifest)
    dataset_id = identity.pop('dataset_id', None)
    if hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest() != dataset_id:
        raise ValueError('Manifest dataset_id checksum mismatch')
    if manifest.get('synthetic') is not True:
        raise ValueError('Missing synthetic provenance')
    if set(manifest['tables']) - set(TABLES):
        raise ValueError('Manifest contains unsupported tables')
    if not REQUIRED_TABLES <= set(manifest['tables']):
        raise ValueError('Manifest is missing required tables')
    sizes = {}
    for name, entry in manifest['tables'].items():
        file = path / f'{name}.parquet'
        if digest(file) != entry['sha256']:
            raise ValueError(f'Checksum mismatch: {name}')
        with pq.ParquetFile(file) as parquet:
            metadata = parquet.metadata
            if metadata.num_rows != entry['rows']:
                raise ValueError(f'Row count mismatch: {name}')
            sizes[name] = max(file.stat().st_size, sum(
                metadata.row_group(i).total_byte_size for i in range(metadata.num_row_groups)))
    if manifest['tables']['sales_orders']['rows'] != manifest['config']['orders']:
        raise ValueError('Manifest order count mismatch')
    return manifest, sizes


@contextmanager
def locked(e):
    """Session lock spans chunk commits; invalidate pooled sessions on any exit."""
    with e.connect() as c:
        try:
            if c.connection.driver_connection.autocommit:
                raise ValueError('Loader requires transactional connections, not autocommit')
            if not c.execute(text('SELECT pg_try_advisory_lock(:key)'), {'key': LOCK}).scalar():
                raise ValueError('Another loader, migration or reset is running')
            c.commit()
            yield c
        finally:
            # Closing the physical session releases its lock even after a failed commit.
            c.invalidate()


def migrate(e):
    with locked(e) as c, c.begin():
        if c.execute(text('SELECT version FROM etl.schema_version')).scalar() != 1:
            raise ValueError('Initialize operational schema v1 first')
        c.connection.driver_connection.execute((ROOT / 'sql/005_seed_checkpoints.sql').read_text())
    return 'Stage 2 seed checkpoint migration applied (idempotent)'


def require_migration(c):
    if not c.execute(text("SELECT to_regclass('etl.seed_loader_version')")).scalar():
        raise ValueError('Run python -m retailpulse migrate-loader first')
    if c.execute(text('SELECT version FROM etl.seed_loader_version')).scalar() != 1:
        raise ValueError('Unsupported loader metadata version')


def disk_paths(c, supplied=None):
    """Only native, same-machine PostgreSQL is supported, not tunnels/containers."""
    settings = dict(c.exec_driver_sql(
        "SELECT name,setting FROM pg_settings WHERE name IN ('data_directory','archive_mode','fsync','full_page_writes')").all())
    if settings.get('fsync') != 'on' or settings.get('full_page_writes') != 'on':
        raise ValueError('Safe resume requires PostgreSQL fsync=on and full_page_writes=on')
    if settings.get('archive_mode') != 'off':
        raise ValueError('Disk estimate requires archive_mode=off; inspect WAL archiving first')
    if c.exec_driver_sql('SELECT count(*) FROM pg_replication_slots').scalar():
        raise ValueError('Replication slots can retain unbounded WAL; inspect them before loading')
    actual = settings.get('data_directory')
    if not actual and not supplied:
        raise ValueError('Cannot discover PostgreSQL data_directory. Pass --pg-data-dir after verifying it with your local administrator')
    path = Path(actual or supplied).expanduser().resolve()
    if actual and supplied and path != Path(supplied).expanduser().resolve():
        raise ValueError('--pg-data-dir does not match the connected PostgreSQL server')
    if not (path / 'PG_VERSION').is_file() or not (path / 'pg_wal').is_dir():
        raise ValueError('PostgreSQL data directory is not locally accessible; do not use a remote/tunneled server')
    paths = [path, (path / 'pg_wal').resolve()]
    for location, in c.exec_driver_sql("SELECT pg_tablespace_location(oid) FROM pg_tablespace"):
        if location:
            p = Path(location).resolve()
            if not p.is_dir():
                raise ValueError('Tablespace is not locally accessible')
            paths.append(p)
    return list(dict.fromkeys(paths))


def disk_reserve(c, low_disk):
    """Opt-in smaller planning reserve, only for a narrowly supported WAL setup.

    These settings are admission checks, not hard bounds on PostgreSQL WAL.
    """
    if not low_disk:
        return RESERVE
    wal_max, wal_keep, summarization = c.exec_driver_sql(
        "SELECT pg_size_bytes(current_setting('max_wal_size')), "
        "pg_size_bytes(current_setting('wal_keep_size')), "
        "current_setting('summarize_wal', true)"
    ).one()
    if not 0 < wal_max <= GIB or wal_keep != 0 or summarization not in (None, 'off'):
        raise ValueError('--low-disk requires max_wal_size <= 1GB, wal_keep_size=0, '
                         'and WAL summarization off. No server settings were changed.')
    LOG.warning('Low-disk mode: 3 GiB reserve, at most 1000 rows per chunk. '
                'WAL and other applications can exceed estimates; completion is not guaranteed.')
    return 3 * GIB


def check_disk(paths, remaining_bytes, *, reserve_bytes=RESERVE):
    # Expansion, indexes, WAL and verification spill. This is an estimate, not a quota.
    required = reserve_bytes + 8 * remaining_bytes
    for path in paths:
        free = shutil.disk_usage(path).free
        LOG.info('disk path=%s free=%.2fGiB required=%.2fGiB', path, free / GIB, required / GIB)
        if free < required:
            raise ValueError(f'Unsafe disk space at {path}: {free/GIB:.2f} GiB free, '
                             f'{required/GIB:.2f} GiB required. Free space and rerun; never delete pg_wal.')
    return required


def expected(manifest, name):
    return manifest['tables'].get(name, {}).get('rows', 0)


def counts(c):
    return {name: c.exec_driver_sql(f'SELECT count(*) FROM source.{name}').scalar() for name in TABLES}


def check_progress(c, manifest):
    rows = c.exec_driver_sql('SELECT table_name,expected_rows,committed_rows FROM etl.seed_tables WHERE batch_id=1').all()
    if {r[0] for r in rows} != set(TABLES) or any(r[1] != expected(manifest, r[0]) for r in rows):
        raise ValueError('Checkpoint metadata does not match manifest; inspect without resetting production')
    progress = {r[0]: r[2] for r in rows}
    if c.exec_driver_sql('SELECT count(*) FROM source.ingestion_batches').scalar() != 1:
        raise ValueError('Expected exactly one operational batch')
    if counts(c) != progress:
        raise ValueError('Existing partial tables differ from checkpoints; refusing to guess or overwrite rows. See recovery guide')
    return progress


def status(e):
    with e.connect() as c:
        require_migration(c)
        runs = [dict(r) for r in c.exec_driver_sql('SELECT batch_id,dataset_id,state,started_at,updated_at,error_class,completed_at FROM etl.seed_runs').mappings()]
        tables = [dict(r) for r in c.exec_driver_sql('SELECT * FROM etl.seed_tables ORDER BY table_name').mappings()]
    return {'runs': runs, 'tables': tables, 'note': 'loading may mean interrupted; rerun seed to acquire the lock and resume'}


def copy_chunk(c, name, batch, table):
    columns = batch.schema.names
    if set(columns) != set(table.c.keys()):
        raise ValueError(f'Parquet columns do not match source.{name}')
    query = sql.SQL('COPY {} ({}) FROM STDIN').format(
        sql.Identifier('source', name), sql.SQL(',').join(map(sql.Identifier, columns)))
    with c.connection.driver_connection.cursor().copy(query) as cp:
        for record in batch.to_pylist():
            values = []
            for col in columns:
                value = record[col]
                if value is not None:
                    if isinstance(table.c[col].type, Integer):
                        value = int(value)
                    elif isinstance(table.c[col].type, Numeric):
                        value = Decimal(str(value))
                values.append(value)
            cp.write_row(values)


def reconcile(c, manifest):
    progress = check_progress(c, manifest)
    if any(progress[n] != expected(manifest, n) for n in TABLES):
        raise ValueError('Dataset is incomplete; run seed to resume')
    failures = verify_connection(c)
    failures += [r[0] for r in c.exec_driver_sql((ROOT / 'sql/006_seed_verify.sql').read_text()) if r[1]]
    # Explicit anti-joins check every FK, including self-references and composite keys.
    metadata = MetaData()
    metadata.reflect(bind=c, schema='source', postgresql_ignore_search_path=True)
    for table in metadata.tables.values():
        for fk in table.foreign_key_constraints:
            elements = list(fk.elements)
            child = sql.Identifier(table.schema, table.name)
            parent = sql.Identifier(elements[0].column.table.schema, elements[0].column.table.name)
            nonnull = sql.SQL(' AND ').join(sql.SQL('c.{} IS NOT NULL').format(sql.Identifier(x.parent.name)) for x in elements)
            match = sql.SQL(' AND ').join(sql.SQL('p.{} = c.{}').format(sql.Identifier(x.column.name), sql.Identifier(x.parent.name)) for x in elements)
            q = sql.SQL('SELECT EXISTS(SELECT 1 FROM {} c WHERE {} AND NOT EXISTS(SELECT 1 FROM {} p WHERE {}))').format(child, nonnull, parent, match)
            if c.connection.driver_connection.execute(q).fetchone()[0]:
                failures.append(fk.name)
    if failures:
        raise ValueError('Database checks failed: ' + ', '.join(failures))
    return progress


def seed(e, path, *, chunk_rows=5000, pg_data_dir=None, preflight_only=False, low_disk=False):
    if not 1 <= chunk_rows <= 50000:
        raise ValueError('--chunk-rows must be between 1 and 50000')
    if low_disk:
        chunk_rows = min(chunk_rows, 1000)
    path = Path(path).expanduser().resolve()
    manifest, sizes = inspect_dataset(path)
    started = time.monotonic()
    current = None
    with locked(e) as c:
        require_migration(c)
        # Durable WAL acknowledgement, modest per-node sort/hash memory.
        c.exec_driver_sql("SET synchronous_commit=on")
        c.exec_driver_sql("SET work_mem='16MB'")
        c.exec_driver_sql("SET lock_timeout='10s'")
        run = c.exec_driver_sql('SELECT dataset_id,manifest,state FROM etl.seed_runs WHERE batch_id=1').first()
        batch = c.exec_driver_sql('SELECT checksum,status FROM source.ingestion_batches WHERE batch_id=1').first()
        if run:
            if run[0] != manifest['dataset_id'] or run[1] != manifest:
                raise ValueError('Different dataset from checkpoint; restore the original files')
            if not batch or batch[0] != manifest['dataset_id']:
                raise ValueError('Operational batch identity differs from checkpoint')
            progress = check_progress(c, manifest)
            if run[2] == 'completed':
                if batch[1] != 'LOADED':
                    raise ValueError('Completed checkpoint has inconsistent operational status')
                c.commit()
                return 'Dataset already loaded; no changes (use verify-db to recheck integrity)'
            if batch[1] != 'GENERATED':
                raise ValueError('Incomplete checkpoint has inconsistent operational status')
        else:
            if c.exec_driver_sql('SELECT count(*) FROM source.ingestion_batches').scalar() or any(counts(c).values()):
                raise ValueError('Existing partial tables without checkpoints; refusing adoption. See recovery guide')
            progress = dict.fromkeys(TABLES, 0)
        paths = disk_paths(c, pg_data_dir)
        reserve_bytes = disk_reserve(c, low_disk)
        remaining = lambda: sum(size * (expected(manifest, n) - progress[n]) // max(1, expected(manifest, n)) for n, size in sizes.items())
        required = check_disk(paths, remaining(), reserve_bytes=reserve_bytes)
        c.commit()
        if preflight_only:
            return {'preflight': 'passed', 'required_free_gib': required/GIB,
                    'disk_mode': 'low-disk' if low_disk else 'default',
                    'reserve_gib': reserve_bytes/GIB, 'chunk_rows': chunk_rows, 'dataset_id': manifest['dataset_id'], 'committed_rows': progress}
        if not run:
            with c.begin():
                c.execute(text("INSERT INTO etl.seed_runs(batch_id,dataset_id,manifest,state) VALUES(1,:id,CAST(:manifest AS jsonb),'pending')"),
                          {'id': manifest['dataset_id'], 'manifest': json.dumps(manifest)})
                for name in TABLES:
                    c.execute(text('INSERT INTO etl.seed_tables(batch_id,table_name,expected_rows) VALUES(1,:name,:rows)'),
                              {'name': name, 'rows': expected(manifest, name)})
                c.execute(text("INSERT INTO source.ingestion_batches VALUES(1,:id,:path,:id,NULL,'GENERATED')"),
                          {'id': manifest['dataset_id'], 'path': str(path)})
        try:
            with c.begin():
                c.exec_driver_sql("UPDATE etl.seed_runs SET state='loading',error_class=NULL,updated_at=clock_timestamp() WHERE batch_id=1")
                metadata = MetaData()
                metadata.reflect(bind=c, schema='source', postgresql_ignore_search_path=True)
            for name in TABLES:
                current = name
                total = expected(manifest, name)
                if progress[name] == total:
                    with c.begin():
                        c.execute(text("UPDATE etl.seed_tables SET state='completed',updated_at=clock_timestamp() WHERE batch_id=1 AND table_name=:n"), {'n': name})
                    continue
                with pq.ParquetFile(path / f'{name}.parquet') as parquet:
                    offset = 0
                    for chunk in parquet.iter_batches(batch_size=chunk_rows):
                        end = offset + chunk.num_rows
                        if end <= progress[name]:
                            offset = end
                            continue
                        # Allows a different chunk size on resume without losing rows.
                        if offset < progress[name]:
                            chunk = chunk.slice(progress[name] - offset)
                        if low_disk and chunk.nbytes > 8 * 1024 * 1024:
                            raise ValueError('Chunk exceeds low-disk mode 8 MiB decoded-data limit; use fewer --chunk-rows')
                        check_disk(paths, remaining(), reserve_bytes=reserve_bytes)
                        with c.begin():
                            copy_chunk(c, name, chunk, metadata.tables['source.' + name])
                            c.execute(text("UPDATE etl.seed_tables SET committed_rows=:end,state=:state,updated_at=clock_timestamp() WHERE batch_id=1 AND table_name=:n"),
                                      {'end': end, 'state': 'completed' if end == total else 'loading', 'n': name})
                            c.exec_driver_sql('UPDATE etl.seed_runs SET updated_at=clock_timestamp() WHERE batch_id=1')
                        progress[name] = end
                        offset = end
                        LOG.info('committed table=%s rows=%d/%d elapsed=%.1fs', name, end, total, time.monotonic()-started)
            current = None
            LOG.info('reconciling all row counts, foreign keys, financial, tax, refund, inventory and golden checks')
            # Detect changed files before certifying the result.
            if inspect_dataset(path)[0] != manifest:
                raise ValueError('Dataset changed during ingestion')
            check_disk(paths, 0, reserve_bytes=reserve_bytes)
            with c.begin():
                # Block concurrent DML during final checks and status transition.
                c.exec_driver_sql('LOCK TABLE ' + ','.join('source.'+n for n in ['ingestion_batches']+TABLES) + ' IN SHARE MODE')
                reconcile(c, manifest)
                c.exec_driver_sql("UPDATE source.ingestion_batches SET status='LOADED',loaded_at=clock_timestamp() WHERE batch_id=1")
                c.exec_driver_sql("UPDATE etl.seed_runs SET state='completed',completed_at=clock_timestamp(),updated_at=clock_timestamp(),error_class=NULL WHERE batch_id=1")
        except BaseException as exc:
            LOG.error('load stopped table=%s error=%s elapsed=%.1fs; rerun identical seed command', current, type(exc).__name__, time.monotonic()-started)
            try:
                # Never reconnect here without the session lock: another process
                # may already be resuming after this connection was lost.
                if c.invalidated or c.closed or c.connection.driver_connection.closed:
                    raise ConnectionError('Failure status cannot be recorded on a lost session')
                c.rollback()
                if c.invalidated:
                    raise ConnectionError('Session lost during rollback')
                with c.begin():
                    c.execute(text("UPDATE etl.seed_runs SET state='failed',error_class=:err,updated_at=clock_timestamp() WHERE batch_id=1"), {'err': type(exc).__name__})
                    if current:
                        c.execute(text("UPDATE etl.seed_tables SET state='failed' WHERE batch_id=1 AND table_name=:n"), {'n': current})
            except Exception:
                LOG.error('Could not record failure; durable checkpoints remain authoritative')
            raise
    return 'Dataset loaded with durable checkpoints and verified'


def verify(e):
    with locked(e) as c:
        require_migration(c)
        row = c.exec_driver_sql('SELECT manifest,state FROM etl.seed_runs WHERE batch_id=1').first()
        if not row or row[1] != 'completed':
            raise ValueError('No fully loaded checkpointed dataset; run seed to resume')
        c.exec_driver_sql("SET LOCAL work_mem='16MB'")
        c.exec_driver_sql("SET LOCAL lock_timeout='10s'")
        c.exec_driver_sql('LOCK TABLE ' + ','.join('source.'+n for n in ['ingestion_batches']+TABLES) + ' IN SHARE MODE')
        batch = c.exec_driver_sql('SELECT checksum,status FROM source.ingestion_batches WHERE batch_id=1').first()
        if not batch or batch != (row[0]['dataset_id'], 'LOADED'):
            raise ValueError('Operational ingestion status or identity is inconsistent')
        return {'database_checks': 'passed', 'rows': reconcile(c, row[0])}
