"""Mutable per-run portfolio state, shared across transaction builders.
Combines the duplicated `TransactionState` interfaces from buy.ts/nil.ts/
rebalance.ts/transactionBuilder.ts into one type."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TransactionState:
    cumulative_units: list[float]
    units_per_fund: list[float]

    @classmethod
    def initialize(cls, num_funds: int) -> TransactionState:
        return cls(cumulative_units=[0.0] * num_funds, units_per_fund=[0.0] * num_funds)
