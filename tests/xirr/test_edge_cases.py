"""Ported from sipRollingXirr/__tests__/edgeCases.test.ts."""

from tests.xirr.conftest import close_to
from tests.xirr.fixtures import (
    declining_fund,
    fast_growing_fund,
    moderate_growth_fund,
    slow_growing_fund,
)
from xirr import calculate_sip_rolling_xirr


def test_zero_threshold_always_rebalances():
    result = calculate_sip_rolling_xirr(
        [fast_growing_fund, slow_growing_fund], 1, [50, 50], True, 0
    )
    last = result[-1]
    rebalances = [tx for tx in last.transactions if tx.type == "rebalance"]
    assert len(rebalances) == 22  # 11 SIPs (excl. first) x 2 funds


def test_hundred_percent_threshold_never_rebalances():
    result = calculate_sip_rolling_xirr(
        [fast_growing_fund, slow_growing_fund], 1, [50, 50], True, 100
    )
    last = result[-1]
    rebalances = [tx for tx in last.transactions if tx.type == "rebalance"]
    assert len(rebalances) == 0


def test_three_funds_with_rebalancing():
    result = calculate_sip_rolling_xirr(
        [fast_growing_fund, slow_growing_fund, moderate_growth_fund],
        1, [33.33, 33.33, 33.34], True, 5,
    )
    last = result[-1]

    for fund_idx in range(3):
        buys = [tx for tx in last.transactions if tx.fund_idx == fund_idx and tx.type == "buy"]
        assert len(buys) == 12

    rebalances = [tx for tx in last.transactions if tx.type == "rebalance"]
    assert len(rebalances) == 15  # 5 rebalancing events x 3 funds


def test_negative_returns_with_rebalancing():
    result = calculate_sip_rolling_xirr(
        [declining_fund, slow_growing_fund], 1, [50, 50], True, 5
    )
    last = result[-1]

    assert close_to(last.xirr, -0.30606173842328904, 4)
    rebalances = [tx for tx in last.transactions if tx.type == "rebalance"]
    assert len(rebalances) == 6  # 3 rebalancing events x 2 funds
