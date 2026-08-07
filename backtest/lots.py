"""FIFO tax-lot ledger.

REQUIREMENTS.md Module 5: "Every SIP instalment is its own tax lot;
redemption is FIFO."

Why this is written here rather than delegated to casparser.analysis.gains,
despite the requirement to prefer casparser: its FIFOUnits is built around
CAS-statement semantics — CASData, folios, stamp duty, STT, and a
31-Jan-2018 FMV looked up per ISIN from a bundled database — none of which
a simulation produces. More decisively, casparser computes *gains* but not
*tax*: an ITR statement leaves rate application to the taxpayer, so the rate
schedule that a 2013+ backtest actually needs isn't there to borrow.

casparser remains the right tool for Module 5's real-CAS ingestion. This
ledger is for the simulator, and the two should agree on any overlapping
case — worth a cross-check once Module 5 exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Lot:
    """One purchase, tracked separately because holding period and cost
    basis are per-lot under FIFO."""

    scheme_code: int
    acquired: date
    units: float
    nav: float

    @property
    def cost(self) -> float:
        return self.units * self.nav


@dataclass
class Disposal:
    """One lot (or part of one) consumed by a sale. The unit of taxation."""

    scheme_code: int
    acquired: date
    sold: date
    units: float
    buy_nav: float
    sell_nav: float

    @property
    def proceeds(self) -> float:
        return self.units * self.sell_nav

    @property
    def cost(self) -> float:
        return self.units * self.buy_nav

    @property
    def gain(self) -> float:
        return self.proceeds - self.cost

    def holding_days(self) -> int:
        return (self.sold - self.acquired).days


class InsufficientUnitsError(ValueError):
    pass


@dataclass
class LotLedger:
    """Per-scheme FIFO queues of open lots."""

    _lots: dict[int, list[Lot]] = field(default_factory=dict)

    def buy(self, scheme_code: int, when: date, units: float, nav: float) -> Lot:
        if units <= 0:
            raise ValueError(f"buy units must be positive, got {units}")
        lot = Lot(scheme_code=scheme_code, acquired=when, units=units, nav=nav)
        self._lots.setdefault(scheme_code, []).append(lot)
        return lot

    def units_held(self, scheme_code: int) -> float:
        return sum(lot.units for lot in self._lots.get(scheme_code, []))

    def value(self, scheme_code: int, nav: float) -> float:
        return self.units_held(scheme_code) * nav

    def cost_basis(self, scheme_code: int) -> float:
        return sum(lot.cost for lot in self._lots.get(scheme_code, []))

    def open_lots(self, scheme_code: int) -> list[Lot]:
        return list(self._lots.get(scheme_code, []))

    def sell(self, scheme_code: int, when: date, units: float, nav: float) -> list[Disposal]:
        """Consume `units` FIFO, returning one Disposal per lot touched.

        Lots are consumed oldest-first and partially where needed, so a sale
        spanning several lots yields several disposals with different
        holding periods — which is the whole point, since each is taxed on
        its own timeline.
        """
        if units <= 0:
            raise ValueError(f"sell units must be positive, got {units}")

        available = self.units_held(scheme_code)
        # Float accumulation across thousands of SIP instalments leaves dust;
        # a sale of "everything" must not fail on 1e-12 of rounding.
        if units > available + 1e-9:
            raise InsufficientUnitsError(
                f"scheme {scheme_code}: asked to sell {units}, holding {available}"
            )
        units = min(units, available)

        queue = self._lots.get(scheme_code, [])
        disposals: list[Disposal] = []
        remaining = units

        while remaining > 1e-12 and queue:
            lot = queue[0]
            taken = min(lot.units, remaining)
            disposals.append(
                Disposal(
                    scheme_code=scheme_code,
                    acquired=lot.acquired,
                    sold=when,
                    units=taken,
                    buy_nav=lot.nav,
                    sell_nav=nav,
                )
            )
            lot.units -= taken
            remaining -= taken
            if lot.units <= 1e-12:
                queue.pop(0)

        return disposals

    def sell_all(self, scheme_code: int, when: date, nav: float) -> list[Disposal]:
        held = self.units_held(scheme_code)
        return self.sell(scheme_code, when, held, nav) if held > 0 else []

    def total_value(self, navs: dict[int, float]) -> float:
        """Portfolio value given a NAV per scheme. Schemes missing from
        `navs` raise rather than being silently valued at zero."""
        total = 0.0
        for scheme_code, lots in self._lots.items():
            units = sum(lot.units for lot in lots)
            if units <= 1e-12:
                continue
            if scheme_code not in navs:
                raise KeyError(f"no NAV supplied for held scheme {scheme_code}")
            total += units * navs[scheme_code]
        return total

    def held_schemes(self) -> list[int]:
        return [c for c, lots in self._lots.items() if sum(x.units for x in lots) > 1e-12]
