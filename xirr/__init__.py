"""Ported from sipRollingXirr/index.ts.

index.ts has its own local extractDailyValuesFromTransactions, used only on
the with-nil path -- it's a byte-for-byte duplicate of
calculateDailySipPortfolioValue from volatility/sipPortfolioValue.ts (same
filter, same grouping, same sums, same sort). Reused here instead of
re-porting the duplicate.
"""

from __future__ import annotations

from datetime import date

from xirr.helpers import build_date_map, ensure_continuous_dates, get_sorted_dates, is_valid_input
from xirr.transaction_builder import (
    calculate_transactions_for_date,
    calculate_transactions_for_date_with_nil,
)
from xirr.types import NavEntry, SipRollingXirrEntry, Transaction
from xirr.volatility.sip_portfolio_value import calculate_daily_sip_portfolio_value
from xirr.volatility.volatility_calculator import calculate_volatility
from xirr.xirr_calculator import calculate_xirr_from_transactions

__all__ = [
    "NavEntry",
    "SipRollingXirrEntry",
    "Transaction",
    "calculate_sip_rolling_xirr",
    "recalculate_transactions_for_date",
]


def calculate_sip_rolling_xirr(
    nav_data_list: list[list[NavEntry]],
    years: float = 1,
    allocations: list[float] | None = None,
    rebalancing_enabled: bool = False,
    rebalancing_threshold: float = 5,
    include_nil_transactions: bool = False,
    step_up_enabled: bool = False,
    step_up_percentage: float = 0,
    sip_amount: float = 100,
) -> list[SipRollingXirrEntry]:
    if allocations is None:
        raise TypeError("allocations is required")
    if not is_valid_input(nav_data_list):
        return []

    months = years * 12
    filled_navs = [ensure_continuous_dates(fund) for fund in nav_data_list]
    fund_date_maps = [build_date_map(fund) for fund in filled_navs]
    base_dates = get_sorted_dates(filled_navs[0])
    first_date = base_dates[0]

    results: list[SipRollingXirrEntry] = []
    for current_date in base_dates:
        results.extend(
            _compute_sip_xirr_for_date(
                current_date, fund_date_maps, months, first_date, allocations,
                rebalancing_enabled, rebalancing_threshold, include_nil_transactions,
                step_up_enabled, step_up_percentage, sip_amount,
            )
        )
    return results


def recalculate_transactions_for_date(
    nav_data_list: list[list[NavEntry]],
    target_date: date,
    years: float,
    allocations: list[float],
    rebalancing_enabled: bool,
    rebalancing_threshold: float,
    step_up_enabled: bool,
    step_up_percentage: float,
    sip_amount: float,
) -> list[Transaction] | None:
    """On-demand recompute with nil transactions included, for a single date."""
    if not is_valid_input(nav_data_list):
        return None

    months = years * 12
    filled_navs = [ensure_continuous_dates(fund) for fund in nav_data_list]
    fund_date_maps = [build_date_map(fund) for fund in filled_navs]
    base_dates = get_sorted_dates(filled_navs[0])
    first_date = base_dates[0]

    return calculate_transactions_for_date_with_nil(
        target_date, fund_date_maps, months, first_date, allocations,
        rebalancing_enabled, rebalancing_threshold, step_up_enabled, step_up_percentage, sip_amount,
    )


# ────────────── Private helpers ────────────── #


def _round4(x: float) -> float:
    return round(x * 10000) / 10000


def _compute_sip_xirr_for_date(
    current_date: date,
    fund_date_maps: list[dict[str, NavEntry]],
    months: float,
    first_date: date,
    allocations: list[float],
    rebalancing_enabled: bool,
    rebalancing_threshold: float,
    include_nil_transactions: bool,
    step_up_enabled: bool,
    step_up_percentage: float,
    sip_amount: float,
) -> list[SipRollingXirrEntry]:
    if include_nil_transactions:
        return _compute_sip_xirr_for_date_with_nil(
            current_date, fund_date_maps, months, first_date, allocations,
            rebalancing_enabled, rebalancing_threshold, step_up_enabled, step_up_percentage, sip_amount,
        )

    result = calculate_transactions_for_date(
        current_date, fund_date_maps, months, first_date, allocations,
        rebalancing_enabled, rebalancing_threshold, step_up_enabled, step_up_percentage, sip_amount,
    )
    if result is None:
        return []

    xirr_value = calculate_xirr_from_transactions(result.transactions)
    if xirr_value is None:
        return []

    volatility = calculate_volatility(result.daily_values)

    return [
        SipRollingXirrEntry(
            date=current_date,
            xirr=_round4(xirr_value),
            transactions=result.transactions,
            volatility=_round4(volatility),
        )
    ]


def _compute_sip_xirr_for_date_with_nil(
    current_date: date,
    fund_date_maps: list[dict[str, NavEntry]],
    months: float,
    first_date: date,
    allocations: list[float],
    rebalancing_enabled: bool,
    rebalancing_threshold: float,
    step_up_enabled: bool,
    step_up_percentage: float,
    sip_amount: float,
) -> list[SipRollingXirrEntry]:
    all_transactions = calculate_transactions_for_date_with_nil(
        current_date, fund_date_maps, months, first_date, allocations,
        rebalancing_enabled, rebalancing_threshold, step_up_enabled, step_up_percentage, sip_amount,
    )
    if all_transactions is None:
        return []

    xirr_value = calculate_xirr_from_transactions(all_transactions)
    if xirr_value is None:
        return []

    daily_values = calculate_daily_sip_portfolio_value(all_transactions)
    volatility = calculate_volatility(daily_values)

    return [
        SipRollingXirrEntry(
            date=current_date,
            xirr=_round4(xirr_value),
            transactions=all_transactions,
            volatility=_round4(volatility),
        )
    ]
