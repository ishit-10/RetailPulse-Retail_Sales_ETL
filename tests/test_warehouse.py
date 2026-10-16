from datetime import datetime, timedelta, timezone
import uuid

import pytest
from retailpulse import warehouse, kpis


def published():
    run_id = uuid.uuid4()
    return {'run_id': run_id, 'status': 'completed', 'started_at': datetime.now(timezone.utc),
            'completed_at': datetime.now(timezone.utc), 'manifest': {'run_id': str(run_id)}}


def test_fresh_snapshot_and_stable_hash():
    row = published()
    warehouse.check_freshness(row, 24)
    assert warehouse.manifest_hash({'a': 1, 'b': 2}) == warehouse.manifest_hash({'b': 2, 'a': 1})
    assert warehouse.manifest_hash({'a': 1}) != warehouse.manifest_hash({'a': 2})


@pytest.mark.parametrize('change', ['old', 'future', 'loading', 'no_completion', 'lineage'])
def test_reject_bad_publication(change):
    row = published()
    if change == 'old': row['started_at'] -= timedelta(days=2)
    if change == 'future': row['started_at'] += timedelta(days=2)
    if change == 'loading': row['status'] = 'loading'
    if change == 'no_completion': row['completed_at'] = None
    if change == 'lineage': row['manifest']['run_id'] = str(uuid.uuid4())
    with pytest.raises(ValueError): warehouse.check_freshness(row, 24)


@pytest.mark.parametrize('hours', [0, -1, 8761])
def test_invalid_freshness_policy(hours):
    with pytest.raises(ValueError): warehouse.check_freshness(published(), hours)


@pytest.mark.parametrize('filters', [{'brand': 'X'}, {'region': 'north'}, {'sku_id; DROP SCHEMA source': 1}, {'city': None}])
def test_reject_unsupported_filters(filters):
    with pytest.raises(ValueError): kpis.parameters('2025-01-01', '2025-01-31', filters)


def test_dates_and_parameter_binding():
    p = kpis.parameters('2025-01-01', '2025-01-31', {'city': "O'Hare"})
    assert (p['end']-p['start']).days+1 == 31
    assert kpis.predicate({'city': "O'Hare"}) == 'o.city=:city'
    with pytest.raises(ValueError): kpis.parameters('2025-02-01', '2025-01-31')


@pytest.mark.parametrize('report,filters', [('footfall', {'sku_id': 1}), ('refunds', {'category_id': 1}),
    ('inventory', {'channel': 'APP'}), ('sell-through', {'city': 'Mumbai'})])
def test_inapplicable_denominators_rejected_before_database(report, filters):
    with pytest.raises(ValueError): kpis.report(None, report, '2025-01-01', '2025-01-31', filters)


def test_mature_cohort_requires_cutoff():
    with pytest.raises(ValueError, match='as-of'):
        kpis.report(None, 'returns', '2025-01-01', '2025-01-31')


def test_disk_preflight_rejects_before_copy(tmp_path, monkeypatch):
    import pyarrow as pa
    import pyarrow.parquet as pq
    from types import SimpleNamespace
    file = tmp_path / 'part.parquet'
    pq.write_table(pa.table({'id': [1]}), file)
    manifest = {'tables': {'x': {'partitions': {'0': {'file': file.name}}}}}
    monkeypatch.setattr(warehouse.loader, 'disk_paths', lambda *a: [tmp_path])
    monkeypatch.setattr(warehouse.shutil, 'disk_usage', lambda *a: SimpleNamespace(free=0))
    with pytest.raises(ValueError, match='headroom'):
        warehouse.disk_preflight(None, tmp_path, manifest)


@pytest.mark.parametrize('filters', [[1], {'sku_id': '1 OR 1=1'}, {'channel': ['APP']}, {'store_id': True}])
def test_typed_filters(filters):
    with pytest.raises(ValueError): kpis.parameters('2025-01-01','2025-01-31',filters)
