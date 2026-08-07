"""FIFO ledger and tax engine.

REQUIREMENTS.md testing: "Hand-work at least one full SIP -> rebalance ->
redemption -> tax cycle and assert against it."

Expected values below are computed by hand in the comments, not read back
out of the implementation — a test that asserts whatever the code happens
to produce validates nothing.
"""

import datetime

import pytest

from backtest.lots import InsufficientUnitsError, LotLedger
from backtest.tax import DEBT, EQUITY, TaxEngine, financial_year

D = datetime.date


# ────────────── FIFO ledger ────────────── #


def test_sell_consumes_oldest_lot_first():
    led = LotLedger()
    led.buy(1, D(2020, 1, 1), units=10, nav=100)   # oldest
    led.buy(1, D(2021, 1, 1), units=10, nav=200)

    disposals = led.sell(1, D(2022, 1, 1), units=10, nav=300)

    assert len(disposals) == 1
    assert disposals[0].acquired == D(2020, 1, 1)
    assert disposals[0].buy_nav == 100
    assert led.units_held(1) == 10                  # newer lot untouched
    assert led.open_lots(1)[0].nav == 200


def test_sale_spanning_lots_splits_into_separate_disposals():
    """Each piece keeps its own acquisition date — that's what makes one
    part long-term and another short-term."""
    led = LotLedger()
    led.buy(1, D(2020, 1, 1), units=10, nav=100)
    led.buy(1, D(2023, 6, 1), units=10, nav=200)

    disposals = led.sell(1, D(2023, 12, 1), units=15, nav=250)

    assert len(disposals) == 2
    assert disposals[0].units == 10 and disposals[0].acquired == D(2020, 1, 1)
    assert disposals[1].units == 5 and disposals[1].acquired == D(2023, 6, 1)
    # gain = 10*(250-100) + 5*(250-200) = 1500 + 250 = 1750
    assert sum(d.gain for d in disposals) == pytest.approx(1750.0)


def test_partial_lot_consumption_leaves_remainder():
    led = LotLedger()
    led.buy(1, D(2020, 1, 1), units=10, nav=100)
    led.sell(1, D(2021, 1, 1), units=3, nav=150)
    assert led.units_held(1) == pytest.approx(7)
    assert led.open_lots(1)[0].units == pytest.approx(7)


def test_overselling_raises():
    led = LotLedger()
    led.buy(1, D(2020, 1, 1), units=5, nav=100)
    with pytest.raises(InsufficientUnitsError):
        led.sell(1, D(2021, 1, 1), units=6, nav=100)


def test_float_dust_does_not_block_selling_everything():
    """Thousands of SIP instalments leave rounding dust; 'sell all' must not
    fail on 1e-13 of it."""
    led = LotLedger()
    for i in range(1000):
        led.buy(1, D(2020, 1, 1) + datetime.timedelta(days=i), units=0.1, nav=100)
    disposals = led.sell_all(1, D(2025, 1, 1), nav=200)
    assert led.units_held(1) == pytest.approx(0, abs=1e-9)
    assert len(disposals) == 1000


def test_total_value_refuses_to_silently_value_a_holding_at_zero():
    led = LotLedger()
    led.buy(1, D(2020, 1, 1), units=10, nav=100)
    with pytest.raises(KeyError):
        led.total_value({})


# ────────────── financial year ────────────── #


def test_financial_year_boundaries():
    assert financial_year(D(2024, 3, 31)) == "2023-24"
    assert financial_year(D(2024, 4, 1)) == "2024-25"
    assert financial_year(D(2024, 12, 31)) == "2024-25"


# ────────────── holding period ────────────── #


def test_equity_long_term_needs_more_than_twelve_months():
    engine = TaxEngine()
    led = LotLedger()
    led.buy(1, D(2022, 1, 10), units=10, nav=100)

    exactly_12m = led.sell(1, D(2023, 1, 10), units=1, nav=150)[0]
    just_over = led.sell(1, D(2023, 1, 11), units=1, nav=150)[0]

    assert engine.is_long_term(exactly_12m, EQUITY) is False
    assert engine.is_long_term(just_over, EQUITY) is True


def test_debt_bought_after_section_50aa_is_always_short_term():
    """Section 50AA: post-1-Apr-2023 debt units are short-term however long
    they are held."""
    engine = TaxEngine()
    led = LotLedger()
    led.buy(1, D(2023, 6, 1), units=10, nav=100)
    after_five_years = led.sell(1, D(2028, 6, 1), units=10, nav=200)[0]
    assert engine.is_long_term(after_five_years, DEBT) is False


def test_debt_bought_before_50aa_still_gets_long_term_after_36m():
    engine = TaxEngine()
    led = LotLedger()
    led.buy(1, D(2022, 1, 1), units=10, nav=100)
    assert engine.is_long_term(led.sell(1, D(2025, 6, 1), units=10, nav=200)[0], DEBT) is True


# ────────────── regime selection across the backtest window ────────────── #


def test_pre_2018_equity_ltcg_was_exempt():
    """A third of a 2013+ backtest sits in this regime. Applying modern
    rates here would invent a tax drag that never existed."""
    engine = TaxEngine()
    led = LotLedger()
    led.buy(1, D(2015, 1, 1), units=100, nav=100)
    disposals = led.sell(1, D(2017, 1, 1), units=100, nav=200)  # 10,000 LTCG

    result = engine.assess(disposals, EQUITY)["2016-17"]
    assert result.ltcg_gain == pytest.approx(10000.0)
    assert result.ltcg_tax == pytest.approx(0.0)
    assert result.total_tax == pytest.approx(0.0)


def test_2018_regime_taxes_ltcg_at_10pc_above_1L():
    engine = TaxEngine()
    led = LotLedger()
    led.buy(1, D(2019, 1, 1), units=100, nav=100)
    disposals = led.sell(1, D(2021, 1, 1), units=100, nav=2100)  # gain 200,000

    result = engine.assess(disposals, EQUITY)["2020-21"]
    # taxable = 200,000 - 100,000 exempt = 100,000 @ 10% = 10,000; cess 4% = 400
    assert result.ltcg_gain == pytest.approx(200000.0)
    assert result.ltcg_exempt_used == pytest.approx(100000.0)
    assert result.ltcg_tax == pytest.approx(10000.0)
    assert result.cess == pytest.approx(400.0)
    assert result.total_tax == pytest.approx(10400.0)


def test_post_jul_2024_regime_taxes_ltcg_at_12_5pc_above_1_25L():
    engine = TaxEngine()
    led = LotLedger()
    led.buy(1, D(2023, 1, 1), units=100, nav=100)
    disposals = led.sell(1, D(2025, 1, 1), units=100, nav=2100)  # gain 200,000

    result = engine.assess(disposals, EQUITY)["2024-25"]
    # taxable = 200,000 - 125,000 = 75,000 @ 12.5% = 9,375; cess 4% = 375
    assert result.ltcg_tax == pytest.approx(9375.0)
    assert result.cess == pytest.approx(375.0)
    assert result.total_tax == pytest.approx(9750.0)


def test_stcg_rate_rose_from_15_to_20_pc():
    engine = TaxEngine()
    led = LotLedger()
    led.buy(1, D(2023, 1, 1), units=100, nav=100)
    before = led.sell(1, D(2023, 6, 1), units=50, nav=200)   # gain 5,000 @15%
    led.buy(2, D(2024, 9, 1), units=100, nav=100)
    after = led.sell(2, D(2025, 1, 1), units=50, nav=200)    # gain 5,000 @20%

    assert engine.assess(before, EQUITY)["2023-24"].stcg_tax == pytest.approx(750.0)
    assert engine.assess(after, EQUITY)["2024-25"].stcg_tax == pytest.approx(1000.0)


def test_ltcg_exemption_applies_per_financial_year_not_per_sale():
    """Two sales in one FY share one allowance; the same two split across
    FYs get an allowance each. This is what makes the annual harvest real."""
    engine = TaxEngine()
    led = LotLedger()
    led.buy(1, D(2022, 1, 1), units=200, nav=100)

    same_fy = led.sell(1, D(2024, 8, 1), units=50, nav=2100) + led.sell(
        1, D(2024, 9, 1), units=50, nav=2100
    )
    # 100,000 + 100,000 = 200,000 gain, ONE 125,000 allowance -> 75,000 taxable
    assert engine.assess(same_fy, EQUITY)["2024-25"].ltcg_tax == pytest.approx(9375.0)

    split = led.sell(1, D(2025, 2, 1), units=50, nav=2100) + led.sell(
        1, D(2025, 6, 1), units=50, nav=2100
    )
    by_fy = engine.assess(split, EQUITY)
    # each FY gets its own 125,000 allowance against 100,000 -> nil both years
    assert by_fy["2024-25"].ltcg_tax == pytest.approx(0.0)
    assert by_fy["2025-26"].ltcg_tax == pytest.approx(0.0)


# ────────────── grandfathering ────────────── #


def test_grandfathering_steps_cost_up_to_31_jan_2018_fmv():
    engine = TaxEngine()
    led = LotLedger()
    led.buy(1, D(2015, 1, 1), units=100, nav=50)
    disposals = led.sell(1, D(2020, 1, 1), units=100, nav=300)

    raw = disposals[0].gain                                    # 100*(300-50) = 25,000
    adjusted = engine.taxable_gain(disposals[0], EQUITY, fmv_on_grandfather_date=200)
    assert raw == pytest.approx(25000.0)
    # cost stepped 50 -> 200, so gain = 100*(300-200) = 10,000
    assert adjusted == pytest.approx(10000.0)


def test_grandfathering_cannot_manufacture_a_loss():
    """FMV is capped at sale value, so a fund that fell after Jan-2018
    cannot produce a fictitious deduction."""
    engine = TaxEngine()
    led = LotLedger()
    led.buy(1, D(2015, 1, 1), units=100, nav=50)
    disposals = led.sell(1, D(2020, 1, 1), units=100, nav=120)

    adjusted = engine.taxable_gain(disposals[0], EQUITY, fmv_on_grandfather_date=500)
    # FMV 500 capped at sale 120 -> gain 100*(120-120) = 0, not negative
    assert adjusted == pytest.approx(0.0)


def test_grandfathering_does_not_apply_to_post_2018_purchases():
    engine = TaxEngine()
    led = LotLedger()
    led.buy(1, D(2019, 1, 1), units=100, nav=50)
    disposals = led.sell(1, D(2021, 1, 1), units=100, nav=300)
    assert engine.taxable_gain(disposals[0], EQUITY, fmv_on_grandfather_date=200) == pytest.approx(
        25000.0
    )
    assert engine.grandfathering_applies(disposals[0], EQUITY) is False


# ────────────── the hand-worked full cycle ────────────── #


def test_full_sip_rebalance_redemption_tax_cycle():
    """Worked by hand:

    SIP of 12,000 into fund A on 1-Jan-2022 @ NAV 100 -> 120 units
    SIP of 12,000 into fund A on 1-Jan-2023 @ NAV 150 ->  80 units
                                              holding  200 units, cost 24,000

    REBALANCE 1-Mar-2023 @ NAV 200: sell 50 units into fund B.
      FIFO takes 50 from the 1-Jan-2022 lot (NAV 100).
      proceeds  50*200 = 10,000 ; cost 50*100 = 5,000 ; gain 5,000
      held 1-Jan-2022 -> 1-Mar-2023 = >12m, so LONG term
      buys 10,000/250 = 40 units of fund B @ 250

    REDEEM everything 1-Jun-2025:
      Fund A @ 300, remaining 150 units:
        70 units from the 1-Jan-2022 lot (NAV 100) -> gain 70*200 = 14,000  LT
        80 units from the 1-Jan-2023 lot (NAV 150) -> gain 80*150 = 12,000  LT
      Fund B @ 400, 40 units from 1-Mar-2023 (NAV 250) -> 40*150 = 6,000    LT

    TAX:
      FY2022-23 (the rebalance sale, 1-Mar-2023): regime from 1-Apr-2018,
        LTCG 5,000 - 100,000 allowance -> nil tax
      FY2025-26 (the redemption, 1-Jun-2025): regime from 23-Jul-2024,
        LTCG 14,000 + 12,000 + 6,000 = 32,000
        32,000 - 125,000 allowance -> nil tax

    Both years land under the allowance, which is itself the point: a
    two-lakh gain spread across years and harvested against the annual
    exemption attracts no tax at all.
    """
    engine = TaxEngine()
    led = LotLedger()

    led.buy(1, D(2022, 1, 1), units=120, nav=100)
    led.buy(1, D(2023, 1, 1), units=80, nav=150)
    assert led.units_held(1) == pytest.approx(200)
    assert led.cost_basis(1) == pytest.approx(24000)

    rebalance = led.sell(1, D(2023, 3, 1), units=50, nav=200)
    assert len(rebalance) == 1
    assert rebalance[0].acquired == D(2022, 1, 1)
    assert rebalance[0].gain == pytest.approx(5000.0)
    assert engine.is_long_term(rebalance[0], EQUITY) is True

    proceeds = rebalance[0].proceeds
    assert proceeds == pytest.approx(10000.0)
    led.buy(2, D(2023, 3, 1), units=proceeds / 250, nav=250)
    assert led.units_held(2) == pytest.approx(40)

    redemption = led.sell_all(1, D(2025, 6, 1), nav=300) + led.sell_all(2, D(2025, 6, 1), nav=400)
    assert [pytest.approx(d.units) for d in redemption] == [70, 80, 40]
    assert sum(d.gain for d in redemption) == pytest.approx(32000.0)
    assert all(engine.is_long_term(d, EQUITY) for d in redemption)

    by_fy = engine.assess(rebalance + redemption, EQUITY)
    assert by_fy["2022-23"].ltcg_gain == pytest.approx(5000.0)
    assert by_fy["2022-23"].total_tax == pytest.approx(0.0)
    assert by_fy["2025-26"].ltcg_gain == pytest.approx(32000.0)
    assert by_fy["2025-26"].total_tax == pytest.approx(0.0)

    assert led.units_held(1) == pytest.approx(0, abs=1e-9)
    assert led.held_schemes() == []
