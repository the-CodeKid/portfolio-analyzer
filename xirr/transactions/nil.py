"""Ported from sipRollingXirr/transactions/nil.ts."""

from __future__ import annotations

from xirr.state import TransactionState
from xirr.types import NavEntry, Transaction


def create_nil_transactions(
    date_key: str,
    fund_date_maps: list[dict[str, NavEntry]],
    state: TransactionState,
) -> list[Transaction] | None:
    transactions: list[Transaction] = []
    total_portfolio_value = 0.0

    for fund_idx, date_map in enumerate(fund_date_maps):
        entry = date_map.get(date_key)
        if entry is None:
            return None

        current_value = state.cumulative_units[fund_idx] * entry.nav
        total_portfolio_value += current_value

        transactions.append(
            Transaction(
                fund_idx=fund_idx,
                when=entry.date,
                nav=entry.nav,
                units=0.0,
                amount=0.0,
                type="nil",
                cumulative_units=state.cumulative_units[fund_idx],
                current_value=current_value,
                allocation_percentage=0.0,
            )
        )

    for tx in transactions:
        tx.allocation_percentage = (
            (tx.current_value / total_portfolio_value) * 100 if total_portfolio_value > 0 else 0.0
        )

    return transactions
