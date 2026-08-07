"""Ported from sipRollingXirr/volatility/volatilityCalculator.ts."""

from __future__ import annotations

import math

from xirr.volatility.sip_portfolio_value import DailySipPortfolioValue

TRADING_DAYS_PER_YEAR = 252


def calculate_volatility(daily_values: list[DailySipPortfolioValue]) -> float:
    if len(daily_values) < 2:
        return 0.0

    daily_returns = _calculate_daily_returns(daily_values)
    if len(daily_returns) < 2:
        return 0.0

    mean_return = sum(daily_returns) / len(daily_returns)
    variance = sum((r - mean_return) ** 2 for r in daily_returns) / len(daily_returns)
    daily_volatility = math.sqrt(variance)

    # Annualize using the actual trading-day ratio in the data rather than a
    # fixed 252, so weekends/holidays forward-filled out of daily_returns
    # don't silently understate volatility.
    total_days = len(daily_values) - 1
    trading_days = len(daily_returns)
    trading_days_per_year = (
        round((trading_days / total_days) * 365) if total_days > 0 else TRADING_DAYS_PER_YEAR
    )

    annualized_volatility = daily_volatility * math.sqrt(trading_days_per_year)
    volatility_percent = annualized_volatility * 100
    return 0.0 if math.isnan(volatility_percent) else volatility_percent


def _calculate_daily_returns(daily_values: list[DailySipPortfolioValue]) -> list[float]:
    """Skips forward-filled non-trading days (value unchanged, no cash flow)
    so they don't drag volatility down artificially."""
    returns: list[float] = []

    for prev, curr in zip(daily_values, daily_values[1:]):
        if prev.total_value > 0:
            value_change = curr.total_value - prev.total_value
            if value_change == 0 and curr.cash_flow == 0:
                continue
            market_return = (value_change + curr.cash_flow) / prev.total_value
            returns.append(market_return)

    return returns
