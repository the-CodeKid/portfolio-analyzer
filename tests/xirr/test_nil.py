"""Ported from sipRollingXirr/__tests__/nil.test.ts."""

from datetime import date

from tests.xirr.conftest import close_to
from tests.xirr.fixtures import moderate_growth_fund
from xirr import calculate_sip_rolling_xirr
from xirr.types import NavEntry, Transaction


def _expect_transaction(
    tx: Transaction, fund_idx: int, nav: float, units: float, amount: float,
    tx_type: str, cumulative_units: float, current_value: float,
    allocation_percentage: float | None = None,
) -> None:
    assert tx.fund_idx == fund_idx
    assert tx.nav == nav
    assert tx.units == units
    assert tx.amount == amount
    assert tx.type == tx_type
    assert close_to(tx.cumulative_units, cumulative_units, 4)
    assert close_to(tx.current_value, current_value, 2)
    if allocation_percentage is not None:
        assert tx.allocation_percentage == allocation_percentage


def test_single_fund_nil_transactions():
    result = calculate_sip_rolling_xirr([moderate_growth_fund], 1, [100], False, 5, True)
    last = result[-1]
    nils = [tx for tx in last.transactions if tx.type == "nil"]

    assert len(nils) == 353  # 365 days - 12 SIP days

    jan2 = nils[0]
    assert jan2.when == date(2023, 1, 2)
    _expect_transaction(jan2, 0, 105, 0, 0, "nil", 1.0000, 105.00, 100)

    feb2 = next(tx for tx in nils if tx.when == date(2023, 2, 2))
    _expect_transaction(feb2, 0, 110, 0, 0, "nil", 1.9524, 214.76, 100)

    dec31 = nils[-1]
    assert dec31.when == date(2023, 12, 31)
    _expect_transaction(dec31, 0, 160, 0, 0, "nil", 9.5901, 1534.42, 100)


def test_multi_fund_nil_transactions_with_allocation_percentages():
    fund1 = [NavEntry(date(2023, 1, 1), 100), NavEntry(date(2023, 2, 1), 110), NavEntry(date(2023, 3, 1), 120)]
    fund2 = [NavEntry(date(2023, 1, 1), 50), NavEntry(date(2023, 2, 1), 55), NavEntry(date(2023, 3, 1), 60)]

    result = calculate_sip_rolling_xirr([fund1, fund2], 2 / 12, [60, 40], False, 5, True)
    last = result[-1]

    jan2_nils = [tx for tx in last.transactions if tx.when == date(2023, 1, 2) and tx.type == "nil"]
    assert len(jan2_nils) == 2
    _expect_transaction(jan2_nils[0], 0, 110, 0, 0, "nil", 0.6000, 66.00, 60)
    _expect_transaction(jan2_nils[1], 1, 55, 0, 0, "nil", 0.8000, 44.00, 40)

    feb2_nils = [tx for tx in last.transactions if tx.when == date(2023, 2, 2) and tx.type == "nil"]
    assert len(feb2_nils) == 2
    _expect_transaction(feb2_nils[0], 0, 120, 0, 0, "nil", 1.1455, 137.45, 60)
    _expect_transaction(feb2_nils[1], 1, 60, 0, 0, "nil", 1.5273, 91.64, 40)
