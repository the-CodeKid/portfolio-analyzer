"""Ported from sipRollingXirr/__tests__/stepup.test.ts."""

from datetime import date, timedelta

from tests.xirr.conftest import close_to
from xirr import calculate_sip_rolling_xirr
from xirr.types import NavEntry


def _create_test_nav_data(start_nav: float, growth_rate: float, years: int) -> list[NavEntry]:
    data = []
    start_date = date(2020, 1, 1)
    total_days = years * 365 + 60
    for i in range(total_days + 1):
        d = start_date + timedelta(days=i)
        nav = start_nav * (1 + growth_rate) ** (i / 365)
        data.append(NavEntry(d, nav))
    return data


def test_increasing_investment_amounts_with_stepup():
    nav_data = _create_test_nav_data(100, 0.10, 2)
    result = calculate_sip_rolling_xirr([nav_data], 2, [100], False, 5, True, True, 10)
    last = result[-1]
    buys = [tx for tx in last.transactions if tx.type == "buy"]

    assert close_to(abs(buys[0].amount), 100, 2)
    assert close_to(abs(buys[11].amount), 100, 2)
    assert close_to(abs(buys[12].amount), 110, 2)
    assert close_to(abs(buys[23].amount), 110, 2)


def test_xirr_with_stepup():
    nav_data = _create_test_nav_data(100, 0.12, 2)
    result = calculate_sip_rolling_xirr([nav_data], 2, [100], False, 5, False, True, 10)
    last = result[-1]

    assert close_to(last.xirr, 0.12, 2)
    assert last.volatility == 0


def test_multiple_funds_with_stepup():
    nav_data1 = _create_test_nav_data(100, 0.12, 2)
    nav_data2 = _create_test_nav_data(100, 0.08, 2)

    result = calculate_sip_rolling_xirr([nav_data1, nav_data2], 2, [50, 50], False, 5, False, True, 10)
    last = result[-1]
    buys = [tx for tx in last.transactions if tx.type == "buy"]

    assert len(buys) == 48  # 24 months x 2 funds

    assert close_to(abs(buys[0].amount), 50, 2)
    assert close_to(abs(buys[1].amount), 50, 2)
    assert close_to(abs(buys[24].amount), 55, 2)
    assert close_to(abs(buys[25].amount), 55, 2)

    assert close_to(last.xirr, 0.1001, 4)
