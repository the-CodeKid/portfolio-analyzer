"""Ported from sipRollingXirr/volatility/__tests__/sipPortfolioValue.test.ts."""

from datetime import date

from xirr.types import Transaction
from xirr.volatility.sip_portfolio_value import calculate_daily_sip_portfolio_value


def _tx(when: date, tx_type: str, current_value: float, amount: float = 0, fund_idx: int = 0) -> Transaction:
    return Transaction(
        fund_idx=fund_idx, nav=100, when=when, units=1, amount=amount, type=tx_type,
        cumulative_units=1, current_value=current_value, allocation_percentage=100,
    )


def test_calculates_daily_values_correctly():
    transactions = [
        _tx(date(2023, 1, 1), "buy", 100, -100),
        _tx(date(2023, 1, 2), "nil", 110, 0),
    ]
    result = calculate_daily_sip_portfolio_value(transactions)

    assert len(result) == 2
    assert result[0].total_value == 100
    assert result[0].cash_flow == -100
    assert result[1].total_value == 110
    assert result[1].cash_flow == 0


def test_groups_multiple_transactions_on_same_date():
    transactions = [
        _tx(date(2023, 1, 1), "buy", 50, -50, fund_idx=0),
        _tx(date(2023, 1, 1), "buy", 50, -50, fund_idx=1),
    ]
    result = calculate_daily_sip_portfolio_value(transactions)

    assert len(result) == 1
    assert result[0].total_value == 100
    assert result[0].cash_flow == -100


def test_empty_transactions_returns_empty_list():
    assert calculate_daily_sip_portfolio_value([]) == []
