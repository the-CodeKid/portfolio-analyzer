"""Ported from portfolio-simulator/src/utils/calculations/sipRollingXirr/types.ts
and src/types/navData.ts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

TransactionType = Literal["buy", "sell", "rebalance", "nil"]


@dataclass
class NavEntry:
    date: date
    nav: float


@dataclass
class Transaction:
    fund_idx: int
    when: date
    nav: float
    units: float
    amount: float
    type: TransactionType
    cumulative_units: float
    current_value: float
    allocation_percentage: float | None = None


@dataclass
class SipRollingXirrEntry:
    date: date
    xirr: float
    transactions: list[Transaction]
    volatility: float
