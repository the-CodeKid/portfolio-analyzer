"""XIRR solver — exact numerical port of the `xirr` (v1.1.0) and
`newton-raphson-method` npm packages, since portfolio-simulator uses them
and the ported test fixtures assert XIRR to 4 decimal places against their
output. Reimplemented rather than wrapped because there's no equivalent
package on PyPI with the same epoch-day/Newton-Raphson formulation.

Algorithm (from xirr.js): exponents are years-until-the-*last*-cashflow
(not years-since-the-first), which keeps every exponent >= 0 and avoids
fractional powers of a negative base in the common case. Root of NPV(r)=0
is unaffected by which reference date the exponents are measured from.
Solved via Newton-Raphson with an analytic derivative, tolerance=1e-7,
max 20 iterations, matching newton-raphson-method's defaults exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from xirr.types import Transaction

_EPOCH = date(1970, 1, 1)
DAYS_IN_YEAR = 365
_TOLERANCE = 1e-7
_MAX_ITERATIONS = 20
_EPS = 2.220446049250313e-16


class XirrError(ValueError):
    pass


@dataclass
class Cashflow:
    amount: float
    when: date


def _epoch_days(d: date) -> int:
    return (d - _EPOCH).days


@dataclass
class _Investment:
    amount: float
    years: float  # (end - this cashflow's date) / 365


@dataclass
class _ConvertedData:
    total: float
    deposits: float
    days: int
    investments: list[_Investment]
    max_amount: float


def _convert(cashflows: list[Cashflow]) -> _ConvertedData:
    if len(cashflows) < 2:
        raise XirrError("Argument is not an array with length of 2 or more.")

    start = _epoch_days(cashflows[0].when)
    end = start
    min_amount = float("inf")
    max_amount = float("-inf")
    total = 0.0
    deposits = 0.0
    raw = []

    for cf in cashflows:
        total += cf.amount
        if cf.amount < 0:
            deposits += -cf.amount
        epoch_days = _epoch_days(cf.when)
        start = min(start, epoch_days)
        end = max(end, epoch_days)
        min_amount = min(min_amount, cf.amount)
        max_amount = max(max_amount, cf.amount)
        raw.append((cf.amount, epoch_days))

    if start == end:
        raise XirrError("Transactions must not all be on the same day.")
    if min_amount >= 0:
        raise XirrError("Transactions must not all be nonnegative.")
    if max_amount < 0:
        raise XirrError("Transactions must not all be negative.")

    investments = [
        _Investment(amount=amount, years=(end - epoch_days) / DAYS_IN_YEAR)
        for amount, epoch_days in raw
    ]

    return _ConvertedData(
        total=total, deposits=deposits, days=end - start,
        investments=investments, max_amount=max_amount,
    )


def _value(investments: list[_Investment], rate: float) -> float:
    total = 0.0
    for inv in investments:
        a, y = inv.amount, inv.years
        if rate > -1:
            total += a * (1 + rate) ** y
        elif rate < -1:
            total -= abs(a) * (-1 - rate) ** y
        elif y == 0:
            total += a
    return total


def _derivative(investments: list[_Investment], rate: float) -> float:
    total = 0.0
    for inv in investments:
        a, y = inv.amount, inv.years
        if y == 0:
            continue
        if rate > -1:
            total += a * y * (1 + rate) ** (y - 1)
        elif rate < -1:
            total += abs(a) * y * (-1 - rate) ** (y - 1)
    return total


def _newton_raphson(f, fp, x0: float) -> float | None:
    for _ in range(_MAX_ITERATIONS):
        y = f(x0)
        yp = fp(x0)
        if abs(yp) <= _EPS * abs(y):
            return None
        x1 = x0 - y / yp
        if abs(x1 - x0) <= _TOLERANCE * abs(x1):
            return x1
        x0 = x1
    return None


def xirr(cashflows: list[Cashflow], guess: float | None = None) -> float:
    data = _convert(cashflows)
    if data.max_amount == 0:
        return -1.0

    if guess is None:
        guess = (data.total / data.deposits) / (data.days / DAYS_IN_YEAR)

    rate = _newton_raphson(
        lambda r: _value(data.investments, r),
        lambda r: _derivative(data.investments, r),
        guess,
    )
    if rate is None:
        raise XirrError("Newton-Raphson algorithm failed to converge.")
    return rate


# ────────────── Public API, mirrors core/xirrCalculator.ts ────────────── #


def calculate_xirr_from_transactions(transactions: list[Transaction]) -> float | None:
    cashflows = _aggregate_cashflows(transactions)
    try:
        return xirr(cashflows)
    except XirrError:
        return None


def _aggregate_cashflows(transactions: list[Transaction]) -> list[Cashflow]:
    by_date: dict[date, float] = {}
    for tx in transactions:
        if tx.type == "nil":
            continue
        by_date[tx.when] = by_date.get(tx.when, 0.0) + tx.amount

    return [Cashflow(amount=amount, when=d) for d, amount in sorted(by_date.items())]
