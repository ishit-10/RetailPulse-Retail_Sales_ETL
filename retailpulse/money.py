from datetime import date
from decimal import Decimal, ROUND_HALF_UP

D = Decimal
PAISE = D('0.01')
TRANSITION = date(2025, 9, 22)

def money(value):
    return D(str(value)).quantize(PAISE, rounding=ROUND_HALF_UP)

def allocate(total, weights):
    """Largest remainder allocation; ties use original input position."""
    total = money(total)
    weights = [D(str(w)) for w in weights]
    if total < 0 or any(w < 0 for w in weights) or total > sum(weights):
        raise ValueError('Invalid allocation')
    if not sum(weights):
        return [money(0) for _ in weights]
    exact = [total * 100 * w / sum(weights) for w in weights]
    cents = [int(x) for x in exact]
    remaining = int(total * 100) - sum(cents)
    for i in sorted(range(len(weights)), key=lambda i: (-(exact[i]-cents[i]), i))[:remaining]:
        cents[i] += 1
    return [money(D(c)/100) for c in cents]

def tax_rule(day, unit_value):
    if day < TRANSITION:
        return (1, D('.05')) if unit_value <= 1000 else (2, D('.12'))
    return (3, D('.05')) if unit_value <= 2500 else (4, D('.18'))

def tax_components(value, rate, interstate):
    total = money(value * rate)
    if interstate:
        return money(0), money(0), total
    cgst = money(total / 2)
    return cgst, total-cgst, money(0)

def ratio(numerator, denominator):
    return None if denominator <= 0 else numerator / denominator
