"""Ported from sipRollingXirr/transactions/rebalance.ts."""

from __future__ import annotations

from datetime import date

from xirr.state import TransactionState
from xirr.types import NavEntry, Transaction


def create_rebalance_transactions(
    date_key: str,
    loop_date: date,
    fund_date_maps: list[dict[str, NavEntry]],
    allocations: list[float],
    rebalancing_threshold: float,
    portfolio_value: float,
    state: TransactionState,
) -> list[Transaction] | None:
    if not _is_rebalancing_needed(
        state.cumulative_units, fund_date_maps, date_key, allocations,
        rebalancing_threshold, portfolio_value,
    ):
        return []

    transactions: list[Transaction] = []

    for fund_idx, date_map in enumerate(fund_date_maps):
        entry = date_map.get(date_key)
        if entry is None:
            return None

        current_value = state.cumulative_units[fund_idx] * entry.nav
        target_value = portfolio_value * (allocations[fund_idx] / 100)
        rebalance_amount = target_value - current_value

        if abs(rebalance_amount) > 0.01:
            rebalance_units = rebalance_amount / entry.nav

            state.cumulative_units[fund_idx] += rebalance_units
            state.units_per_fund[fund_idx] += rebalance_units

            transactions.append(
                Transaction(
                    fund_idx=fund_idx,
                    when=loop_date,
                    nav=entry.nav,
                    units=rebalance_units,
                    amount=-rebalance_amount,
                    type="rebalance",
                    cumulative_units=state.cumulative_units[fund_idx],
                    current_value=state.cumulative_units[fund_idx] * entry.nav,
                    allocation_percentage=allocations[fund_idx],
                )
            )

    return transactions


def _is_rebalancing_needed(
    cumulative_units: list[float],
    fund_date_maps: list[dict[str, NavEntry]],
    date_key: str,
    allocations: list[float],
    threshold: float,
    portfolio_value: float,
) -> bool:
    for fund_idx, date_map in enumerate(fund_date_maps):
        entry = date_map.get(date_key)
        if entry is None:
            continue

        current_value = cumulative_units[fund_idx] * entry.nav
        current_allocation = (current_value / portfolio_value) * 100
        target_allocation = allocations[fund_idx]

        if abs(current_allocation - target_allocation) > threshold:
            return True

    return False
