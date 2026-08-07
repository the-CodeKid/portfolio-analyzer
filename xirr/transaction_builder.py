"""Ported from sipRollingXirr/core/transactionBuilder.ts.

Two paths, same as upstream:
- calculate_transactions_for_date: no nil Transaction objects are created;
  daily portfolio values needed for volatility are computed inline instead.
  This is the path calculate_sip_rolling_xirr uses by default.
- calculate_transactions_for_date_with_nil: creates an explicit nil
  transaction for every non-SIP day. Slower, but some of the ported test
  fixtures assert against nil transactions directly, so both paths need to
  exist and agree.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from xirr.helpers import generate_sip_dates, to_date_key
from xirr.state import TransactionState
from xirr.transactions.buy import create_buy_transactions
from xirr.transactions.nil import create_nil_transactions
from xirr.transactions.rebalance import create_rebalance_transactions
from xirr.transactions.sell import create_final_sell_transactions
from xirr.types import NavEntry, Transaction
from xirr.volatility.sip_portfolio_value import DailySipPortfolioValue


@dataclass
class TransactionResult:
    transactions: list[Transaction]
    daily_values: list[DailySipPortfolioValue]


def calculate_transactions_for_date(
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
) -> TransactionResult | None:
    sip_dates, earliest_date = generate_sip_dates(current_date, months, first_date)
    if earliest_date is None:
        return None

    state = TransactionState.initialize(len(fund_date_maps))
    built = _build_daily_transactions(
        earliest_date, current_date, sip_dates, fund_date_maps, allocations,
        rebalancing_enabled, rebalancing_threshold, step_up_enabled, step_up_percentage,
        sip_amount, state,
    )
    if built is None:
        return None

    sell_transactions = create_final_sell_transactions(current_date, fund_date_maps, state.units_per_fund)
    if sell_transactions is None:
        return None

    return TransactionResult(
        transactions=[*built.transactions, *sell_transactions],
        daily_values=built.daily_values,
    )


def calculate_transactions_for_date_with_nil(
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
) -> list[Transaction] | None:
    sip_dates, earliest_date = generate_sip_dates(current_date, months, first_date)
    if earliest_date is None:
        return None

    state = TransactionState.initialize(len(fund_date_maps))
    transactions = _build_daily_transactions_with_nil(
        earliest_date, current_date, sip_dates, fund_date_maps, allocations,
        rebalancing_enabled, rebalancing_threshold, step_up_enabled, step_up_percentage,
        sip_amount, state,
    )
    if transactions is None:
        return None

    sell_transactions = create_final_sell_transactions(current_date, fund_date_maps, state.units_per_fund)
    if sell_transactions is None:
        return None

    return [*transactions, *sell_transactions]


# ────────────── Private helpers ────────────── #


@dataclass
class _DailyBuildResult:
    transactions: list[Transaction]
    daily_values: list[DailySipPortfolioValue]


def _build_daily_transactions(
    start_date: date,
    end_date: date,
    sip_dates: set[str],
    fund_date_maps: list[dict[str, NavEntry]],
    allocations: list[float],
    rebalancing_enabled: bool,
    rebalancing_threshold: float,
    step_up_enabled: bool,
    step_up_percentage: float,
    sip_amount: float,
    state: TransactionState,
) -> _DailyBuildResult | None:
    transactions: list[Transaction] = []
    daily_values: list[DailySipPortfolioValue] = []
    loop_date = start_date
    first_sip_date = start_date

    while loop_date <= end_date:
        date_key = to_date_key(loop_date)
        # Never SIP on the final day - that's the sell date, not a buy date
        is_sip_date = loop_date < end_date and date_key in sip_dates

        cash_flow_for_day = 0.0
        if is_sip_date:
            result = _process_sip_date(
                date_key, loop_date, fund_date_maps, allocations, rebalancing_enabled,
                rebalancing_threshold, first_sip_date, step_up_enabled, step_up_percentage,
                sip_amount, state,
            )
            if result is None:
                return None
            transactions.extend(result)
            cash_flow_for_day = sum(tx.amount for tx in result if tx.type == "buy")

        daily_value = _compute_daily_portfolio_value(date_key, loop_date, fund_date_maps, state, cash_flow_for_day)
        if daily_value is None:
            return None
        daily_values.append(daily_value)

        loop_date = loop_date + timedelta(days=1)

    return _DailyBuildResult(transactions=transactions, daily_values=daily_values)


def _build_daily_transactions_with_nil(
    start_date: date,
    end_date: date,
    sip_dates: set[str],
    fund_date_maps: list[dict[str, NavEntry]],
    allocations: list[float],
    rebalancing_enabled: bool,
    rebalancing_threshold: float,
    step_up_enabled: bool,
    step_up_percentage: float,
    sip_amount: float,
    state: TransactionState,
) -> list[Transaction] | None:
    transactions: list[Transaction] = []
    loop_date = start_date
    first_sip_date = start_date

    while loop_date < end_date:
        date_key = to_date_key(loop_date)
        is_sip_date = date_key in sip_dates

        result = (
            _process_sip_date(
                date_key, loop_date, fund_date_maps, allocations, rebalancing_enabled,
                rebalancing_threshold, first_sip_date, step_up_enabled, step_up_percentage,
                sip_amount, state,
            )
            if is_sip_date
            else create_nil_transactions(date_key, fund_date_maps, state)
        )

        if result is None:
            return None
        transactions.extend(result)

        loop_date = loop_date + timedelta(days=1)

    return transactions


def _compute_daily_portfolio_value(
    date_key: str,
    current_date: date,
    fund_date_maps: list[dict[str, NavEntry]],
    state: TransactionState,
    cash_flow: float,
) -> DailySipPortfolioValue | None:
    total_value = 0.0
    for fund_idx, date_map in enumerate(fund_date_maps):
        entry = date_map.get(date_key)
        if entry is None:
            return None
        total_value += state.cumulative_units[fund_idx] * entry.nav

    return DailySipPortfolioValue(date=current_date, total_value=total_value, cash_flow=cash_flow)


def _process_sip_date(
    date_key: str,
    loop_date: date,
    fund_date_maps: list[dict[str, NavEntry]],
    allocations: list[float],
    rebalancing_enabled: bool,
    rebalancing_threshold: float,
    first_sip_date: date,
    step_up_enabled: bool,
    step_up_percentage: float,
    sip_amount: float,
    state: TransactionState,
) -> list[Transaction] | None:
    buy_result = create_buy_transactions(
        date_key, fund_date_maps, allocations, state, loop_date, first_sip_date,
        step_up_enabled, step_up_percentage, sip_amount,
    )
    if buy_result is None:
        return None
    buy_transactions, portfolio_value = buy_result

    if rebalancing_enabled:
        rebalance_transactions = create_rebalance_transactions(
            date_key, loop_date, fund_date_maps, allocations, rebalancing_threshold,
            portfolio_value, state,
        )
        if rebalance_transactions is None:
            return None
    else:
        rebalance_transactions = []

    return [*buy_transactions, *rebalance_transactions]
