"""Ported from sipRollingXirr/__tests__/corpus.test.ts."""

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


def _corpus_value(entry):
    return sum(abs(tx.amount) for tx in entry.transactions if tx.type == "sell")


def test_corpus_value_with_sip_amount():
    nav_data = _create_test_nav_data(100, 0.12, 2)
    result = calculate_sip_rolling_xirr([nav_data], 2, [100], False, 5, False, False, 0, 10000)
    corpus = _corpus_value(result[-1])
    assert close_to(corpus, 270563.98, 2)


def test_corpus_scales_linearly_with_sip_amount():
    nav_data = _create_test_nav_data(100, 0.10, 2)

    result_5k = calculate_sip_rolling_xirr([nav_data], 2, [100], False, 5, False, False, 0, 5000)
    corpus_5k = _corpus_value(result_5k[-1])

    result_20k = calculate_sip_rolling_xirr([nav_data], 2, [100], False, 5, False, False, 0, 20000)
    corpus_20k = _corpus_value(result_20k[-1])

    assert close_to(corpus_20k / corpus_5k, 4, 1)


def test_corpus_with_step_up_sip():
    nav_data = _create_test_nav_data(100, 0.12, 2)
    result = calculate_sip_rolling_xirr([nav_data], 2, [100], False, 5, False, True, 10, 10000)
    corpus = _corpus_value(result[-1])
    assert close_to(corpus, 283326.44, 2)
