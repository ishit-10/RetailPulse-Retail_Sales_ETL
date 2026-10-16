"""Loader unit tests: no database or production files are needed."""
import hashlib
import json
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from retailpulse import loader


def write_manifest(path, manifest):
    manifest.pop('dataset_id', None)
    manifest['dataset_id'] = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    (path / 'manifest.json').write_text(json.dumps(manifest))


@pytest.fixture
def files(tmp_path):
    manifest = {'synthetic': True, 'config': {'orders': 1}, 'tables': {}}
    for name in loader.REQUIRED_TABLES:
        file = tmp_path / f'{name}.parquet'
        pq.write_table(pa.table({'id': [1]}), file)
        manifest['tables'][name] = {'rows': 1, 'sha256': loader.digest(file)}
    write_manifest(tmp_path, manifest)
    return tmp_path, manifest


def test_streaming_identity(files):
    path, manifest = files
    actual, sizes = loader.inspect_dataset(path)
    assert actual == manifest
    assert all(size > 0 for size in sizes.values())


def test_wrong_file_checksum(files):
    path, _ = files
    with (path / 'sales_orders.parquet').open('ab') as f:
        f.write(b'changed')
    with pytest.raises(ValueError, match='Checksum mismatch'):
        loader.inspect_dataset(path)


def test_wrong_manifest_identity(files):
    path, manifest = files
    manifest['config']['orders'] = 2
    (path / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='dataset_id checksum'):
        loader.inspect_dataset(path)


def test_wrong_count(files):
    path, manifest = files
    manifest['tables']['sales_orders']['rows'] = 2
    write_manifest(path, manifest)
    with pytest.raises(ValueError, match='Row count'):
        loader.inspect_dataset(path)


def test_unknown_table_path_rejected(files):
    path, manifest = files
    manifest['tables']['../escape'] = {'rows': 1, 'sha256': 'x'}
    write_manifest(path, manifest)
    with pytest.raises(ValueError, match='unsupported tables'):
        loader.inspect_dataset(path)


def test_disk_rejects_last_reported_mac_space(monkeypatch, tmp_path):
    monkeypatch.setattr(loader.shutil, 'disk_usage', lambda _: SimpleNamespace(free=int(8.3 * loader.GIB)))
    with pytest.raises(ValueError, match='Unsafe disk space'):
        loader.check_disk([tmp_path], 0)


def test_disk_expansion_and_each_volume(monkeypatch, tmp_path):
    calls = []
    def usage(path):
        calls.append(path)
        return SimpleNamespace(free=loader.RESERVE + 800)
    monkeypatch.setattr(loader.shutil, 'disk_usage', usage)
    assert loader.check_disk([tmp_path, tmp_path/'wal'], 100) == loader.RESERVE + 800
    assert len(calls) == 2
    with pytest.raises(ValueError):
        loader.check_disk([tmp_path], 101)


@pytest.mark.parametrize('size', [0, -1, 50001])
def test_invalid_chunk_size(size):
    with pytest.raises(ValueError, match='chunk-rows'):
        loader.seed(None, '/unused', chunk_rows=size)


class Result:
    def __init__(self, rows):
        self.rows = rows
    def all(self):
        return self.rows
    def scalar(self):
        return self.rows[0][0]
    def __iter__(self):
        return iter(self.rows)


class DiskConnection:
    def __init__(self, path, *, slots=0, archive='off', fsync='on', full_page_writes='on'):
        self.settings = [('data_directory', path), ('archive_mode', archive),
                         ('fsync', fsync), ('full_page_writes', full_page_writes)]
        self.slots = slots
    def exec_driver_sql(self, query):
        if 'pg_settings' in query:
            return Result(self.settings)
        if 'pg_replication_slots' in query:
            return Result([(self.slots,)])
        return Result([('',)])


@pytest.fixture
def pg_directory(tmp_path):
    (tmp_path / 'PG_VERSION').write_text('17')
    (tmp_path / 'pg_wal').mkdir()
    return tmp_path


def test_disk_discovers_server_data_and_wal(pg_directory):
    assert loader.disk_paths(DiskConnection(str(pg_directory))) == [pg_directory, pg_directory/'pg_wal']


def test_disk_hidden_setting_requires_explicit_path(pg_directory):
    with pytest.raises(ValueError, match='Cannot discover'):
        loader.disk_paths(DiskConnection(None))
    assert loader.disk_paths(DiskConnection(None), pg_directory)[0] == pg_directory


def test_wrong_explicit_server_path(pg_directory):
    with pytest.raises(ValueError, match='does not match'):
        loader.disk_paths(DiskConnection(str(pg_directory)), pg_directory/'wrong')


@pytest.mark.parametrize('kwargs', [{'slots': 1}, {'archive': 'on'}, {'fsync': 'off'}, {'full_page_writes': 'off'}])
def test_disk_refuses_unsafe_server_configuration(pg_directory, kwargs):
    with pytest.raises(ValueError):
        loader.disk_paths(DiskConnection(str(pg_directory), **kwargs))


class WalConnection:
    def __init__(self, wal_max=loader.GIB, wal_keep=0, summarization='off'):
        self.row = (wal_max, wal_keep, summarization)
    def exec_driver_sql(self, query):
        return SimpleNamespace(one=lambda: self.row)


def test_default_disk_reserve_unchanged():
    assert loader.disk_reserve(None, False) == 10 * loader.GIB


def test_low_disk_reserve_supported_wal():
    assert loader.disk_reserve(WalConnection(), True) == 3 * loader.GIB


@pytest.mark.parametrize('config', [
    {'wal_max': 2 * loader.GIB}, {'wal_keep': loader.GIB},
    {'summarization': 'on'}, {'wal_max': 0},
])
def test_low_disk_rejects_unsupported_wal(config):
    with pytest.raises(ValueError, match='--low-disk requires'):
        loader.disk_reserve(WalConnection(**config), True)


def test_low_disk_budget_still_rejects_falling_space(monkeypatch, tmp_path):
    remaining = int(.155 * loader.GIB)
    monkeypatch.setattr(loader.shutil, 'disk_usage', lambda _: SimpleNamespace(free=int(4.8 * loader.GIB)))
    required = loader.check_disk([tmp_path], remaining, reserve_bytes=3 * loader.GIB)
    assert 4.23 < required/loader.GIB < 4.25
    monkeypatch.setattr(loader.shutil, 'disk_usage', lambda _: SimpleNamespace(free=required-1))
    with pytest.raises(ValueError, match='Unsafe disk space'):
        loader.check_disk([tmp_path], remaining, reserve_bytes=3 * loader.GIB)
    monkeypatch.setattr(loader.shutil, 'disk_usage', lambda _: SimpleNamespace(free=3*loader.GIB-1))
    with pytest.raises(ValueError, match='Unsafe disk space'):
        loader.check_disk([tmp_path], 0, reserve_bytes=3 * loader.GIB)
