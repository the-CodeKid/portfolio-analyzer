"""Ported from sipRollingXirr/__tests__/basic.test.ts."""

from datetime import date

from tests.xirr.conftest import close_to
from xirr import calculate_sip_rolling_xirr
from xirr.types import NavEntry

steady_growth_fund = [
    NavEntry(date(2023, 1, 1), 100), NavEntry(date(2023, 2, 1), 105), NavEntry(date(2023, 3, 1), 110),
    NavEntry(date(2023, 4, 1), 115), NavEntry(date(2023, 5, 1), 120), NavEntry(date(2023, 6, 1), 125),
    NavEntry(date(2023, 7, 1), 130), NavEntry(date(2023, 8, 1), 135), NavEntry(date(2023, 9, 1), 140),
    NavEntry(date(2023, 10, 1), 145), NavEntry(date(2023, 11, 1), 150), NavEntry(date(2023, 12, 1), 155),
    NavEntry(date(2024, 1, 1), 160),
]

EXPECTED_TRANSACTIONS = [
    (0, 100, date(2023, 1, 1), 1.0000, -100.00, "buy"),
    (0, 105, date(2023, 2, 1), 0.9524, -100.00, "buy"),
    (0, 110, date(2023, 3, 1), 0.9091, -100.00, "buy"),
    (0, 115, date(2023, 4, 1), 0.8696, -100.00, "buy"),
    (0, 120, date(2023, 5, 1), 0.8333, -100.00, "buy"),
    (0, 125, date(2023, 6, 1), 0.8000, -100.00, "buy"),
    (0, 130, date(2023, 7, 1), 0.7692, -100.00, "buy"),
    (0, 135, date(2023, 8, 1), 0.7407, -100.00, "buy"),
    (0, 140, date(2023, 9, 1), 0.7143, -100.00, "buy"),
    (0, 145, date(2023, 10, 1), 0.6897, -100.00, "buy"),
    (0, 150, date(2023, 11, 1), 0.6667, -100.00, "buy"),
    (0, 155, date(2023, 12, 1), 0.6452, -100.00, "buy"),
    (0, 160, date(2024, 1, 1), 9.5901, 1534.42, "sell"),
]


def test_single_fund_exact_transactions_and_xirr():
    result = calculate_sip_rolling_xirr([steady_growth_fund], 1, [100])
    last = result[-1]

    assert len(last.transactions) == len(EXPECTED_TRANSACTIONS)
    for tx, (fund_idx, nav, when, units, amount, tx_type) in zip(last.transactions, EXPECTED_TRANSACTIONS):
        assert tx.fund_idx == fund_idx
        assert close_to(tx.nav, nav, 2)
        assert tx.when == when
        assert close_to(tx.units, units, 4)
        assert close_to(tx.amount, amount, 2)
        assert tx.type == tx_type

    assert close_to(last.xirr, 0.5488197128979718, 4)


def test_cumulative_units_and_current_value():
    result = calculate_sip_rolling_xirr([steady_growth_fund], 1, [100])
    last = result[-1]

    cumulative_units = 0.0
    for tx in last.transactions:
        if tx.type in ("buy", "sell"):
            if tx.type == "buy":
                cumulative_units += tx.units
            assert close_to(tx.cumulative_units, cumulative_units, 4)
            assert close_to(tx.current_value, cumulative_units * tx.nav, 2)
