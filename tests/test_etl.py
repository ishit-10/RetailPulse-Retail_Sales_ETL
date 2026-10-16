"""Filesystem contracts; no source database required."""
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from sqlalchemy import Column, MetaData, Table, BigInteger, Text, Numeric, DateTime, Date, Boolean
from retailpulse import etl


@pytest.fixture
def schema():
    table = Table('sample', MetaData(), Column('id', BigInteger, primary_key=True),
                  Column('amount', Numeric(16, 2)), Column('label', Text),
                  Column('at', DateTime(timezone=True)), Column('day', Date), Column('active', Boolean))
    return etl.arrow_schema(table)


@pytest.fixture
def records():
    return [{'id': 9007199254740993, 'amount': Decimal('123456789.12'), 'label': '₹ नमस्ते',
             'at': datetime(2025, 1, 1, tzinfo=timezone.utc), 'day': date(2025, 1, 1), 'active': True},
            {'id': 9007199254740994, 'amount': None, 'label': None, 'at': None, 'day': None, 'active': False}]


@pytest.fixture(autouse=True)
def plenty_of_space(monkeypatch):
    monkeypatch.setattr(etl.shutil, 'disk_usage', lambda _: SimpleNamespace(free=100 * 1024**3))


def test_exact_types_and_deterministic_retry(tmp_path, schema, records):
    a = etl.write_partition(tmp_path, 'sample', 0, schema, ['id'], [records])
    b = etl.write_partition(tmp_path, 'sample', 0, schema, ['id'], [records])
    assert a == b
    assert pq.ParquetFile(tmp_path / a['file']).read().to_pylist() == records
    assert (tmp_path / a['file']).stat().st_ino == (tmp_path / a['raw']).stat().st_ino
    assert len(list(tmp_path.rglob('*.parquet'))) == 2


@pytest.mark.parametrize('kind', ['duplicate', 'unordered', 'null'])
def test_invalid_keys_rejected(tmp_path, schema, records, kind):
    if kind == 'duplicate': records[1]['id'] = records[0]['id']
    elif kind == 'unordered': records.reverse()
    else: records[0]['id'] = None
    with pytest.raises(ValueError): etl.write_partition(tmp_path, 'sample', 0, schema, ['id'], [records])
    assert not list(tmp_path.rglob('*.parquet'))


def test_interruption_preserves_previous_object(tmp_path, schema, records):
    old = etl.write_partition(tmp_path, 'sample', 0, schema, ['id'], [records])
    def interrupted():
        yield records
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        etl.write_partition(tmp_path, 'sample', 0, schema, ['id'], interrupted())
    assert etl.loader.digest(tmp_path / old['file']) == old['sha256']
    assert not list(tmp_path.rglob('.writing.parquet'))


def test_changed_and_deleted_records_new_immutable_version(tmp_path, schema, records):
    old = etl.write_partition(tmp_path, 'sample', 0, schema, ['id'], [records])
    records[0]['label'] = 'updated'
    new = etl.write_partition(tmp_path, 'sample', 0, schema, ['id'], [records[:1]])
    assert new['sha256'] != old['sha256']
    assert new['rows'] == 1
    assert pq.ParquetFile(tmp_path / old['file']).metadata.num_rows == 2


def test_empty_partition_keeps_schema(tmp_path, schema):
    part = etl.write_partition(tmp_path, 'sample', 0, schema, ['id'], [])
    assert part['rows'] == 0
    assert pq.ParquetFile(tmp_path / part['file']).schema_arrow == schema


def test_disk_refusal(tmp_path, schema, records, monkeypatch):
    monkeypatch.setattr(etl.shutil, 'disk_usage', lambda _: SimpleNamespace(free=etl.RESERVE - 1))
    with pytest.raises(ValueError, match='reserve'):
        etl.write_partition(tmp_path, 'sample', 0, schema, ['id'], [records])


def test_lake_process_lock(tmp_path):
    with etl.lake_lock(tmp_path):
        with pytest.raises(ValueError, match='Another ETL'):
            with etl.lake_lock(tmp_path): pass


def test_corruption_rejected(tmp_path, schema, records):
    part = etl.write_partition(tmp_path, 'sample', 0, schema, ['id'], [records])
    (tmp_path / part['file']).write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='corrupt'):
        etl.write_partition(tmp_path, 'sample', 0, schema, ['id'], [records])


def test_domain_money_schema():
    from sqlalchemy.dialects.postgresql import DOMAIN
    table = Table('money', MetaData(), Column('amount', DOMAIN('money_amount', Numeric(16, 2))))
    assert etl.arrow_schema(table).field('amount').type == pa.decimal128(16, 2)


@pytest.mark.parametrize('damage', ['rows', 'schema', 'missing_partition'])
def test_manifest_reconciliation(tmp_path, schema, records, monkeypatch, damage):
    monkeypatch.setattr(etl, 'TABLES', ['sample'])
    monkeypatch.setattr(etl, 'BUCKETS', 1)
    part = etl.write_partition(tmp_path, 'sample', 0, schema, ['id'], [records])
    entry = {'schema': etl.schema_token(schema), 'rows': 2, 'partitions': {'0': part}}
    manifest = {'format_version': 1, 'tables': {'sample': entry}}
    assert etl.validate_manifest(tmp_path, manifest) == {'sample': 2}
    if damage == 'rows': entry['rows'] = 3
    elif damage == 'schema': entry['schema'] = etl.schema_token(pa.schema([]))
    else: entry['partitions'] = {}
    with pytest.raises(ValueError): etl.validate_manifest(tmp_path, manifest)


def test_reflected_domain_missing_typmod_uses_catalog(tmp_path):
    from sqlalchemy.dialects.postgresql import DOMAIN
    table = Table('price_versions', MetaData(),
                  Column('price_id', BigInteger, primary_key=True),
                  Column('mrp', DOMAIN('money_amount', Numeric())))
    schema = etl.arrow_schema(table, {'mrp': (16, 2)})
    assert schema.field('mrp').type == pa.decimal128(16, 2)
    records = [{'price_id': 1, 'mrp': Decimal('1415.99')}]
    part = etl.write_partition(tmp_path, 'price_versions', 0, schema, ['price_id'], [records])
    assert pq.ParquetFile(tmp_path / part['file']).read().to_pylist() == records
    with pytest.raises(ValueError, match='Missing numeric precision/scale'):
        etl.arrow_schema(table)
