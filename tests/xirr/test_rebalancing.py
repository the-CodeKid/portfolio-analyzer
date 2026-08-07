"""Ported from sipRollingXirr/__tests__/rebalancing.test.ts."""

from datetime import date

from tests.xirr.conftest import close_to
from tests.xirr.fixtures import fast_growing_fund, slow_growing_fund, stable_fund_1, stable_fund_2
from xirr import calculate_sip_rolling_xirr

EXPECTED_TRANSACTIONS = [
    (0, 100, date(2023, 1, 1), 0.5, -50, "buy"),
    (1, 100, date(2023, 1, 1), 0.5, -50, "buy"),
    (0, 120, date(2023, 2, 1), 0.4167, -50, "buy"),
    (1, 102, date(2023, 2, 1), 0.4902, -50, "buy"),
    (0, 144, date(2023, 3, 1), 0.3472, -50, "buy"),
    (1, 104.04, date(2023, 3, 1), 0.4806, -50, "buy"),
    (0, 172.8, date(2023, 4, 1), 0.2894, -50, "buy"),
    (1, 106.12, date(2023, 4, 1), 0.4712, -50, "buy"),
    (0, 172.8, date(2023, 4, 1), -0.1803, 31.16, "rebalance"),
    (1, 106.12, date(2023, 4, 1), 0.2936, -31.16, "rebalance"),
    (0, 207.36, date(2023, 5, 1), 0.2411, -50, "buy"),
    (1, 108.24, date(2023, 5, 1), 0.4619, -50, "buy"),
    (0, 248.83, date(2023, 6, 1), 0.2009, -50, "buy"),
    (1, 110.41, date(2023, 6, 1), 0.4529, -50, "buy"),
    (0, 248.83, date(2023, 6, 1), -0.2086, 51.89, "rebalance"),
    (1, 110.41, date(2023, 6, 1), 0.4700, -51.89, "rebalance"),
    (0, 298.6, date(2023, 7, 1), 0.1674, -50, "buy"),
    (1, 112.61, date(2023, 7, 1), 0.4440, -50, "buy"),
    (0, 358.32, date(2023, 8, 1), 0.1395, -50, "buy"),
    (1, 114.87, date(2023, 8, 1), 0.4353, -50, "buy"),
    (0, 358.32, date(2023, 8, 1), -0.2355, 84.37, "rebalance"),
    (1, 114.87, date(2023, 8, 1), 0.7345, -84.37, "rebalance"),
    (0, 429.98, date(2023, 9, 1), 0.1163, -50, "buy"),
    (1, 117.16, date(2023, 9, 1), 0.4268, -50, "buy"),
    (0, 515.98, date(2023, 10, 1), 0.0969, -50, "buy"),
    (1, 119.51, date(2023, 10, 1), 0.4184, -50, "buy"),
    (0, 515.98, date(2023, 10, 1), -0.2415, 124.63, "rebalance"),
    (1, 119.51, date(2023, 10, 1), 1.0428, -124.63, "rebalance"),
    (0, 619.18, date(2023, 11, 1), 0.0808, -50, "buy"),
    (1, 121.9, date(2023, 11, 1), 0.4102, -50, "buy"),
    (0, 743.01, date(2023, 12, 1), 0.0673, -50, "buy"),
    (1, 124.34, date(2023, 12, 1), 0.4021, -50, "buy"),
    (0, 743.01, date(2023, 12, 1), -0.2349, 174.55, "rebalance"),
    (1, 124.34, date(2023, 12, 1), 1.4038, -174.55, "rebalance"),
    (0, 891.61, date(2024, 1, 1), 1.5627, 1393.34, "sell"),
    (1, 126.82, date(2024, 1, 1), 9.3383, 1184.28, "sell"),
]


def test_rebalancing_trigger_with_exact_transactions():
    result = calculate_sip_rolling_xirr([fast_growing_fund, slow_growing_fund], 1, [50, 50], True, 5)
    last = result[-1]

    assert len(last.transactions) == len(EXPECTED_TRANSACTIONS)
    for tx, (fund_idx, nav, when, units, amount, tx_type) in zip(last.transactions, EXPECTED_TRANSACTIONS):
        assert tx.fund_idx == fund_idx
        assert close_to(tx.nav, nav, 2)
        assert tx.when == when
        assert close_to(tx.units, units, 4)
        assert close_to(tx.amount, amount, 2)
        assert tx.type == tx_type

    assert close_to(last.xirr, 2.605716656746517, 4)


def test_no_rebalancing_within_threshold():
    result = calculate_sip_rolling_xirr([stable_fund_1, stable_fund_2], 1, [50, 50], True, 10)
    last = result[-1]
    rebalances = [tx for tx in last.transactions if tx.type == "rebalance"]
    assert len(rebalances) == 0


def test_xirr_differs_with_vs_without_rebalancing():
    with_reb = calculate_sip_rolling_xirr([fast_growing_fund, slow_growing_fund], 1, [50, 50], True, 5)
    without_reb = calculate_sip_rolling_xirr([fast_growing_fund, slow_growing_fund], 1, [50, 50], False, 5)

    assert len(with_reb) == len(without_reb)

    assert close_to(with_reb[-1].xirr, 2.605716656746517, 4)
    assert close_to(without_reb[-1].xirr, 3.6792974731956845, 4)
    assert without_reb[-1].xirr > with_reb[-1].xirr
