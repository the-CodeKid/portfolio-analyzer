"""Ported from sipRollingXirr/__tests__/transactions.test.ts."""

from tests.xirr.conftest import close_to
from tests.xirr.fixtures import fast_growing_fund, slow_growing_fund
from xirr import calculate_sip_rolling_xirr


def test_buy_transactions_precede_rebalance_on_same_date():
    result = calculate_sip_rolling_xirr([fast_growing_fund, slow_growing_fund], 1, [50, 50], True, 5)

    for entry in result:
        by_date: dict = {}
        for tx in entry.transactions:
            by_date.setdefault(tx.when, []).append(tx)

        for txs in by_date.values():
            buy_indices = [i for i, tx in enumerate(txs) if tx.type == "buy"]
            rebalance_indices = [i for i, tx in enumerate(txs) if tx.type == "rebalance"]
            if buy_indices and rebalance_indices:
                assert max(buy_indices) < min(rebalance_indices)


def test_cumulative_units_correct_after_each_transaction():
    result = calculate_sip_rolling_xirr([fast_growing_fund, slow_growing_fund], 1, [50, 50], True, 5)
    last = result[-1]

    manual_cumulative_units = [0.0, 0.0]
    for tx in last.transactions:
        if tx.type != "sell":
            manual_cumulative_units[tx.fund_idx] += tx.units
            assert close_to(tx.cumulative_units, manual_cumulative_units[tx.fund_idx], 5)


def test_zero_net_cashflow_during_rebalancing():
    result = calculate_sip_rolling_xirr([fast_growing_fund, slow_growing_fund], 1, [50, 50], True, 5)

    for entry in result:
        rebalances = [tx for tx in entry.transactions if tx.type == "rebalance"]
        if rebalances:
            total = sum(tx.amount for tx in rebalances)
            assert close_to(total, 0, 10)
