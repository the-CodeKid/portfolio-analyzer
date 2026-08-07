"""Ported from sipRollingXirr/volatility/sipPortfolioValue.ts.

(There's also a compositeNav.ts upstream with the same exports and near-
identical logic — it's an unreferenced duplicate, confirmed by a repo-wide
grep finding no imports of it anywhere. Not ported.)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_type

from xirr.helpers import to_date_key
from xirr.types import Transaction


@dataclass
class DailySipPortfolioValue:
    date: date_type  # aliased: the field name would otherwise shadow the type
    total_value: float
    cash_flow: float  # negative for buy (money out), positive for sell (money in)


def calculate_daily_sip_portfolio_value(
    transactions: list[Transaction],
) -> list[DailySipPortfolioValue]:
    relevant = [tx for tx in transactions if tx.type in ("nil", "buy")]
    if not relevant:
        return []

    by_date: dict[str, list[Transaction]] = {}
    for tx in relevant:
        by_date.setdefault(to_date_key(tx.when), []).append(tx)

    daily_values: list[DailySipPortfolioValue] = []
    for txs in by_date.values():
        total_value = sum(tx.current_value for tx in txs)
        cash_flow = sum(tx.amount for tx in txs if tx.type == "buy")
        if total_value > 0:
            daily_values.append(
                DailySipPortfolioValue(date=txs[0].when, total_value=total_value, cash_flow=cash_flow)
            )

    daily_values.sort(key=lambda dv: dv.date)
    return daily_values
