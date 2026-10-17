"""Display-only rounding; business metrics remain SQL calculations."""
import csv
import io
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def number(value, places=0):
    if value is None:
        return '—'
    try:
        d = Decimal(str(value))
        if not d.is_finite(): return '—'
    except (InvalidOperation, ValueError): return '—'
    rounded = d.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    sign = '-' if rounded < 0 else ''
    whole, _, fraction = f'{abs(rounded):.{places}f}'.partition('.')
    tail, lead = whole[-3:], whole[:-3]
    groups = []
    while lead:
        groups.insert(0, lead[-2:])
        lead = lead[:-2]
    grouped = ','.join(groups + [tail])
    return sign + grouped + ('.' + fraction if places else '')


def currency(value, compact=False):
    if value is None: return '—'
    d = Decimal(str(value))
    if not d.is_finite(): return '—'
    if compact and abs(d) >= 10_000_000: return '₹' + number(d / 10_000_000, 2) + ' Cr'
    if compact and abs(d) >= 100_000: return '₹' + number(d / 100_000, 2) + ' L'
    return '₹' + number(d, 2)


def percent(value):
    return '—' if number(value, 2) == '—' else number(value, 2) + '%'


def csv_bytes(rows):
    """Prevent spreadsheet formula execution in exported text; preserve numbers."""
    if not rows: return b''
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=list(rows[0]))
    writer.writeheader()
    for row in rows:
        safe = {k: ("'" + v if isinstance(v, str) and v.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else v)
                for k, v in row.items()}
        writer.writerow(safe)
    return out.getvalue().encode('utf-8-sig')
