"""Capital gains tax, applied from the dated regime schedule in config/tax.yaml.

Rates are config, not code. A 2013+ backtest crosses three regime changes,
and the pre-2018 one matters most: long-term equity gains were entirely
exempt then, so applying today's 12.5% to that third of the window would
invent a tax drag that never existed and bias every strategy comparison
toward low-turnover ones.

Everything is decided by the date of *sale* except section 50AA, which keys
off the date of *acquisition* — see is_long_term.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml
from dateutil.relativedelta import relativedelta

from backtest.lots import Disposal

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "tax.yaml"

EQUITY = "equity"
DEBT = "debt"


@dataclass
class FYTaxResult:
    financial_year: str
    stcg_gain: float = 0.0
    ltcg_gain: float = 0.0
    ltcg_exempt_used: float = 0.0
    stcg_tax: float = 0.0
    ltcg_tax: float = 0.0
    cess: float = 0.0

    @property
    def total_tax(self) -> float:
        return self.stcg_tax + self.ltcg_tax + self.cess


def financial_year(d: date) -> str:
    """Indian FY runs 1 April - 31 March. 2024-05-01 -> '2024-25'."""
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> dict:
    return yaml.safe_load(Path(path).read_text())


class TaxEngine:
    def __init__(self, config: dict | None = None, config_path: Path = DEFAULT_CONFIG_PATH):
        self._cfg = config if config is not None else load_config(config_path)

    # ────────────── regime selection ────────────── #

    def _regime(self, asset: str, sale_date: date) -> dict:
        """The latest regime whose effective_from is on or before the sale."""
        regimes = sorted(
            self._cfg[asset]["regimes"],
            key=lambda r: _as_date(r["effective_from"]),
        )
        chosen = None
        for regime in regimes:
            if _as_date(regime["effective_from"]) <= sale_date:
                chosen = regime
            else:
                break
        if chosen is None:
            raise ValueError(f"no {asset} tax regime covers a sale on {sale_date}")
        return chosen

    def is_long_term(self, disposal: Disposal, asset: str) -> bool:
        """Long-term unless an acquisition rule overrides it.

        Section 50AA is why this keys off acquisition date as well as
        holding period: debt units bought on or after 1-Apr-2023 are deemed
        short-term however long they are held.
        """
        for rule in self._cfg[asset].get("acquisition_rules") or []:
            if disposal.acquired >= _as_date(rule["acquired_from"]) and rule.get(
                "always_short_term"
            ):
                return False

        months = self._cfg[asset]["long_term_months"]
        return disposal.sold > disposal.acquired + relativedelta(months=months)

    # ────────────── gain adjustment ────────────── #

    def taxable_gain(
        self, disposal: Disposal, asset: str, fmv_on_grandfather_date: float | None = None
    ) -> float:
        """Gain after any section 112A grandfathering step-up.

        For equity units bought on or before 31-Jan-2018 and sold from
        1-Apr-2018, cost becomes the higher of actual cost and the
        31-Jan-2018 FMV, itself capped at sale value so the step-up can
        never manufacture a loss.

        Passing fmv_on_grandfather_date=None when the step-up *does* apply
        means the caller could not supply that NAV. The gain is then
        returned unadjusted, which overstates tax — callers must surface
        that rather than let it pass as an exact figure.
        """
        gf = self._cfg[asset].get("grandfathering")
        if (
            asset != EQUITY
            or not gf
            or not gf.get("enabled")
            or fmv_on_grandfather_date is None
            or disposal.acquired > _as_date(gf["fmv_date"])
            or disposal.sold < _as_date(gf["applies_to_sales_from"])
        ):
            return disposal.gain

        stepped_cost = max(disposal.buy_nav, min(fmv_on_grandfather_date, disposal.sell_nav))
        return (disposal.sell_nav - stepped_cost) * disposal.units

    def grandfathering_applies(self, disposal: Disposal, asset: str) -> bool:
        gf = self._cfg[asset].get("grandfathering")
        if asset != EQUITY or not gf or not gf.get("enabled"):
            return False
        return disposal.acquired <= _as_date(gf["fmv_date"]) and disposal.sold >= _as_date(
            gf["applies_to_sales_from"]
        )

    # ────────────── assessment ────────────── #

    def assess(
        self,
        disposals: list[Disposal],
        asset: str,
        fmv_lookup: dict[int, float] | None = None,
    ) -> dict[str, FYTaxResult]:
        """Tax by financial year.

        The LTCG exemption is a per-FY allowance against *aggregate*
        long-term gains, so it can only be applied after grouping — which
        is also what makes the free-allowance harvest in Module 5 a real
        strategy rather than an accounting detail.
        """
        fmv_lookup = fmv_lookup or {}
        by_fy: dict[str, FYTaxResult] = {}

        for d in disposals:
            fy = financial_year(d.sold)
            result = by_fy.setdefault(fy, FYTaxResult(financial_year=fy))
            gain = self.taxable_gain(d, asset, fmv_lookup.get(d.scheme_code))
            if self.is_long_term(d, asset):
                result.ltcg_gain += gain
            else:
                result.stcg_gain += gain

        for fy, result in by_fy.items():
            sale_date = _fy_reference_date(fy, disposals)
            regime = self._regime(asset, sale_date)

            exemption = float(regime.get("ltcg_exemption") or 0)
            taxable_ltcg = max(0.0, result.ltcg_gain - exemption)
            result.ltcg_exempt_used = min(max(result.ltcg_gain, 0.0), exemption)
            result.ltcg_tax = taxable_ltcg * float(regime["ltcg_rate"])

            stcg_rate = regime.get("stcg_rate")
            if stcg_rate is None:  # "slab" — investor-specific
                stcg_rate = float(self._cfg["slab_rate"])
            result.stcg_tax = max(0.0, result.stcg_gain) * float(stcg_rate)

            result.cess = (result.stcg_tax + result.ltcg_tax) * float(self._cfg["cess_rate"])

        return by_fy


def _as_date(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _fy_reference_date(fy: str, disposals: list[Disposal]) -> date:
    """Latest sale date within the FY, so a year straddling a regime change
    (FY2024-25 around 23-Jul-2024) resolves against a real transaction date
    rather than an arbitrary year boundary.

    This is a simplification: a single FY can genuinely span two regimes and
    strictly each sale should use its own. Sales are grouped per FY here
    because the LTCG exemption is annual. Worth revisiting if a strategy
    trades across that specific boundary.
    """
    dates = [d.sold for d in disposals if financial_year(d.sold) == fy]
    return max(dates)
