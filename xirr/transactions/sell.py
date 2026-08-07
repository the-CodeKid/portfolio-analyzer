"""Ported from sipRollingXirr/transactions/sell.ts."""

from __future__ import annotations

from datetime import date

from xirr.helpers import to_date_key
from xirr.types import NavEntry, Transaction


def create_final_sell_transactions(
    current_date: date,
    fund_date_maps: list[dict[str, NavEntry]],
    units_per_fund: list[float],
) -> list[Transaction] | None:
    date_key = to_date_key(current_date)
    sells: list[Transaction] = []

    for fund_idx, date_map in enumerate(fund_date_maps):
        entry = date_map.get(date_key)
        if entry is None:
            return None

        units = units_per_fund[fund_idx]
        amount = units * entry.nav

        sells.append(
            Transaction(
                fund_idx=fund_idx,
                nav=entry.nav,
                when=entry.date,
                units=units,
                amount=amount,
                type="sell",
                cumulative_units=units,
                current_value=units * entry.nav,
            )
        )

    return sells
