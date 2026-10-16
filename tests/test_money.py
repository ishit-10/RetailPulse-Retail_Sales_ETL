from datetime import date
from decimal import Decimal as D
import pytest
from retailpulse.money import allocate, tax_rule, tax_components, money, ratio

@pytest.mark.parametrize('day,value,rate',[
 ('2025-09-21','999.99','.05'),('2025-09-21','1000','.05'),('2025-09-21','1000.01','.12'),
 ('2025-09-22','1000.01','.05'),('2025-09-22','2500','.05'),('2025-09-22','2500.01','.18'),
 ('2026-06-30','3000','.18')])
def test_tax_boundaries(day,value,rate):
    assert tax_rule(date.fromisoformat(day),D(value))[1]==D(rate)

@pytest.mark.parametrize('total,weights,expected',[
 ('0.01',[1,1,1],['0.01','0','0']),('1',[1,1,1],['.34','.33','.33']),
 ('100',[800,1600],['33.33','66.67']),('0',[0,0],['0','0'])])
def test_exact_cart_allocation(total,weights,expected):
    result=allocate(total,weights)
    assert result==list(map(D,expected))
    assert sum(result)==D(total)

@pytest.mark.parametrize('total,weights',[(-1,[1]),(2,[1]),(1,[-1,2])])
def test_invalid_discounts(total,weights):
    with pytest.raises(ValueError): allocate(total,weights)

def test_tax_odd_paise():
    assert tax_components(D('.10'),D('.05'),False)==(D('.01'),D('0'),D('0'))
    assert sum(tax_components(D('13.37'),D('.05'),False))==money(D('13.37')*D('.05'))

def test_zero_or_negative_denominator():
    assert ratio(1,0) is None
    assert ratio(1,-1) is None
    assert ratio(1,2)==.5

def test_original_tax_on_return():
    original=tax_components(D('2400'),tax_rule(date(2025,9,21),D('2400'))[1],False)
    assert sum(original)==D('288')
    assert sum(original)!=sum(tax_components(D('2400'),tax_rule(date(2025,9,22),D('2400'))[1],False))
