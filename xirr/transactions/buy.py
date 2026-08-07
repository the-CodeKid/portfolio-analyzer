"""Ported from sipRollingXirr/transactions/buy.ts."""

from __future__ import annotations

from datetime import date

from xirr.helpers import get_investment_year
from xirr.state import TransactionState
from xirr.types import NavEntry, Transaction


def create_buy_transactions(
    date_key: str,
    fund_date_maps: list[dict[str, NavEntry]],
    allocations: list[float],
    state: TransactionState,
    current_date: date,
    first_sip_date: date,
    step_up_enabled: bool,
    step_up_percentage: float,
    sip_amount: float,
) -> tuple[list[Transaction], float] | None:
    total_investment = sip_amount
    if step_up_enabled and step_up_percentage > 0:
        investment_year = get_investment_year(current_date, first_sip_date)
        # Compound step-up: Year 1 = sipAmount, Year 2 = sipAmount*(1+r), ...
        total_investment = sip_amount * (1 + step_up_percentage / 100) ** (investment_year - 1)

    transactions: list[Transaction] = []
    total_portfolio_value = 0.0
    fund_values: list[float] = []

    for fund_idx, date_map in enumerate(fund_date_maps):
        entry = date_map.get(date_key)
        if entry is None:
            return None

        investment_amount = total_investment * (allocations[fund_idx] / 100)
        units = investment_amount / entry.nav

        state.cumulative_units[fund_idx] += units
        state.units_per_fund[fund_idx] += units

        current_value = state.cumulative_units[fund_idx] * entry.nav
        fund_values.append(current_value)
        total_portfolio_value += current_value

        transactions.append(
            Transaction(
                fund_idx=fund_idx,
                nav=entry.nav,
                when=entry.date,
                units=units,
                amount=-investment_amount,
                type="buy",
                cumulative_units=state.cumulative_units[fund_idx],
                current_value=current_value,
                allocation_percentage=0.0,
            )
        )

    for tx, fund_value in zip(transactions, fund_values):
        tx.allocation_percentage = (
            (fund_value / total_portfolio_value) * 100 if total_portfolio_value > 0 else 0.0
        )

    return transactions, total_portfolio_value
