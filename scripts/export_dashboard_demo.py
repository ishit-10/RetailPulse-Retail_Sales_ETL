"""Export a complete small synthetic warehouse; never sample production facts.

Run from the repository: python scripts/export_dashboard_demo.py --help
The bundled schemas are the explicit column/privacy allowlist.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pyarrow as pa
import pyarrow.parquet as pq
from retailpulse import db
from retailpulse.dashboard import data


def export(engine, output, acknowledge_synthetic=False):
    if not acknowledge_synthetic:
        raise ValueError('Explicit synthetic-only acknowledgement required')
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Output must be a new directory; existing bundles are never overwritten')
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.demo-export-', dir=output.parent))
    try:
        with data.session('local', engine) as s:
            head = data.head(s, 'local')
            count = s.rows('SELECT count(DISTINCT order_id) n FROM analytics.fact_order_items')[0]['n']
            if not 1 <= count <= 5000:
                raise ValueError('Use a complete small synthetic test warehouse (1–5000 orders); never truncate the full warehouse')
            manifest = {'format_version': 1, 'synthetic': True,
                        'bundle_id': 'synthetic-' + head['run_id'],
                        'created_at': datetime.now(timezone.utc).isoformat(),
                        'provenance': 'Complete explicitly acknowledged small synthetic warehouse; column allowlist excludes customer/contact dimensions. No live audit history included.',
                        'tables': {}}
            coverage = s.rows('SELECT min(date_key) a,max(date_key) b FROM analytics.dim_date')[0]
            totals = data.core(s, 'sales', coverage['a'], coverage['b'], {})[0]
            manifest['expected'] = {key: totals[key] for key in ('orders','gmv','net_sales','net_cogs','gross_profit')}
            for name in data.DEMO_TABLES:
                schema = pq.read_schema(data.DEMO_ROOT / (name + '.parquet'))
                # Names come only from the checked-in schema, never CLI/user SQL.
                columns = ','.join('"' + column.replace('"', '""') + '"' for column in schema.names)
                rows = s.rows(f'SELECT {columns} FROM analytics.{name} LIMIT 100001')
                if len(rows) > 100000:
                    raise ValueError('Demo table exceeds compact export limit: ' + name)
                path = temporary / (name + '.parquet')
                pq.write_table(pa.Table.from_pylist(rows, schema=schema), path, compression='zstd')
                manifest['tables'][name] = {'rows': len(rows), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            (temporary / 'manifest.json').write_text(json.dumps(manifest, indent=2, default=str) + '\n')
        data.validate_demo(temporary)
        temporary.rename(output)
        return manifest
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--acknowledge-synthetic', action='store_true', required=True)
    args = parser.parse_args()
    result = export(db.engine(), args.output, args.acknowledge_synthetic)
    print(json.dumps({'bundle_id': result['bundle_id'], 'tables': len(result['tables'])}))
