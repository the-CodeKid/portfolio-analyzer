"""Ported from sipRollingXirr/volatility/__tests__/volatilityCalculator.test.ts."""

from datetime import date

from tests.xirr.conftest import close_to
from xirr.volatility.sip_portfolio_value import DailySipPortfolioValue
from xirr.volatility.volatility_calculator import calculate_volatility


def test_zero_volatility_for_insufficient_data():
    daily_values = [DailySipPortfolioValue(date(2024, 1, 1), 100.00, 0)]
    assert calculate_volatility(daily_values) == 0


def test_zero_volatility_when_all_forward_filled_days_skipped():
    daily_values = [
        DailySipPortfolioValue(date(2024, 1, 1), 100.00, 0),
        DailySipPortfolioValue(date(2024, 1, 2), 100.00, 0),
        DailySipPortfolioValue(date(2024, 1, 3), 100.00, 0),
    ]
    assert calculate_volatility(daily_values) == 0


def test_calculates_annualized_volatility_correctly():
    daily_values = [
        DailySipPortfolioValue(date(2024, 1, 1), 100.00, 0),
        DailySipPortfolioValue(date(2024, 1, 2), 105.00, 0),
        DailySipPortfolioValue(date(2024, 1, 3), 110.00, 0),
        DailySipPortfolioValue(date(2024, 1, 4), 115.00, 0),
    ]
    result = calculate_volatility(daily_values)
    assert close_to(result, 3.55, 1)


def test_skips_forward_filled_weekends_but_includes_trading_days():
    daily_values = [
        DailySipPortfolioValue(date(2024, 1, 1), 100.00, 0),
        DailySipPortfolioValue(date(2024, 1, 2), 105.00, 0),
        DailySipPortfolioValue(date(2024, 1, 3), 105.00, 0),
        DailySipPortfolioValue(date(2024, 1, 4), 105.00, 0),
        DailySipPortfolioValue(date(2024, 1, 5), 110.00, 0),
    ]
    result = calculate_volatility(daily_values)
    assert result > 0
    assert close_to(result, 1.63, 1)


def test_includes_buy_days_even_when_value_unchanged():
    daily_values = [
        DailySipPortfolioValue(date(2024, 1, 1), 100.00, 0),
        DailySipPortfolioValue(date(2024, 1, 2), 200.00, -100.00),
        DailySipPortfolioValue(date(2024, 1, 3), 200.00, 0),
    ]
    assert calculate_volatility(daily_values) == 0


def test_mix_of_trading_and_forward_filled_days():
    daily_values = [
        DailySipPortfolioValue(date(2024, 1, 1), 100.00, 0),
        DailySipPortfolioValue(date(2024, 1, 2), 110.00, 0),
        DailySipPortfolioValue(date(2024, 1, 3), 110.00, 0),
        DailySipPortfolioValue(date(2024, 1, 4), 110.00, 0),
        DailySipPortfolioValue(date(2024, 1, 5), 100.00, 0),
        DailySipPortfolioValue(date(2024, 1, 6), 100.00, 0),
        DailySipPortfolioValue(date(2024, 1, 7), 110.00, 0),
    ]
    result = calculate_volatility(daily_values)
    assert result > 0
    assert result > 50
