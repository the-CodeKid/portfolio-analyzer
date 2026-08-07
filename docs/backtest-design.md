# Module 4 — Backtest harness design

## What it must be able to do

From REQUIREMENTS.md: *"the harness must be capable of concluding that the
engine loses to the baseline. If it can't produce that result, it isn't
measuring anything."*

That single line drives every decision below. The harness is not built to
make a strategy look good; it is built to be **capable of falsifying it**.
Anything that makes a losing strategy look like a winning one is a bug, and
the most common such bugs are lookahead and survivorship — so both are
handled structurally rather than by care.

## Layers

```
  strategy (Module 3 scoring, or baseline, or shuffled control)
      |  weights_at(view, T) -> {scheme_code: weight}
      v
  simulator            path-dependent: lots, taxes, drift, costs
      |
      v
  PointInTimeView      the ONLY way to read data; hard as-of cutoff
      |
      v
  DuckDB
```

The strategy never touches the database. It receives a `PointInTimeView`
that physically cannot return post-`as_of` rows.

## 1. As-of enforcement (`asof/view.py`)

REQUIREMENTS.md Module 2: *"Enforce it with a decorator or a data-access
wrapper, not with discipline."*

A decorator is the weaker option — it protects the functions someone
remembers to decorate. Instead `PointInTimeView` is constructed with an
`as_of` date and every query it issues has the cutoff baked in. It exposes
no connection handle, so there is no unguarded path to the data.

```python
class PointInTimeView:
    def __init__(self, con, as_of: date) -> None: ...
    def universe(self, *, category_code=None, min_history_days=None) -> list[int]
    def nav(self, scheme_code) -> pd.Series      # index <= as_of
    def benchmark(self, index_name) -> pd.Series # index <= as_of
    def as_of(self) -> date
```

**The test that proves it** (REQUIREMENTS.md demands this explicitly):
build two databases — one truncated at T, one containing post-T rows —
and assert every view method returns identical output. Not "similar":
identical. This is the single most valuable test in the module.

## 2. Universe reconstruction at T

The survivorship-critical piece, and the one with a wrinkle.

`scheme_master.first_seen` is useless for historical T because our snapshots
only begin 2026-08-07 — every scheme has the same `first_seen`. So for
historical dates, liveness is derived from NAV observations instead:

> a scheme is in the universe at T iff it has NAV data starting on or before
> T, and its last NAV on or before T is no more than `stale_days` old.

This is what makes the 39% of AMFI's file that is already-dead funds
valuable: they appear in the universe at historical T and correctly drop out
at their death date. As our own snapshots accumulate, `scheme_master` spans
become the more authoritative source and take over.

The `stale_days` tolerance matters: a fund that stopped reporting is dead,
but NAV gaps around holidays are normal. Default 30 days, configurable.

## 3. Strategy interface

```python
Strategy = Callable[[PointInTimeView, date], dict[int, float]]  # -> weights
```

Deliberately minimal, because it makes three things fall out for free:
- the **passive baseline** is just another strategy
- the **shuffled control** is a decorator around a strategy
- Module 3's scoring plugs in later without the harness changing

Weights must sum to 1.0 (validated); an empty dict means "hold cash".

## 4. Simulator

Path-dependent, so it cannot be vectorised away. At each step:

1. On a rebalance date, ask the strategy for target weights.
2. Diff against current holdings → sell and buy lists.
3. Sells consume tax lots **FIFO**, realising gains and accruing tax.
4. Apply exit load where the lot is younger than the fund's exit-load window.
5. Buys create new lots at that date's NAV.
6. Record every cashflow for XIRR.

**Reuse vs rebuild:** the ported `xirr` package assumes a *fixed* fund set
with fixed allocations across the whole window, so its transaction builder
doesn't fit — the whole point here is that holdings change at each
rebalance. What is reused is `xirr.xirr_calculator` (the verified solver)
and the `Transaction` shape. The lot ledger is new, and Module 5 should
reuse *it* rather than growing a second one.

### Tax model

Bakes in the rules from REQUIREMENTS.md Module 5, because they change
optimal behaviour and a pre-tax backtest would rank strategies wrongly:

- Equity STCG 20% (held ≤12m); LTCG 12.5% above ₹1.25L/FY (§112A)
- Debt bought on/after 1 Apr 2023: slab rate, no LT benefit (§50AA)
- Every instalment is its own lot; redemption is FIFO

Where the arithmetic overlaps `casparser.analysis.gains`, delegate rather
than reimplement.

### A correction on TER

I previously flagged missing historical TER as undermining "net of TER"
backtesting. That was wrong, and the distinction matters:

**Fund NAV is already net of expenses.** Backtesting a fund on its NAV
series is inherently net of whatever TER it actually charged, historically
accurate, no data needed.

Historical TER is only needed for (a) scoring/filtering funds on cost, and
(b) synthesising an index *fund* from a raw index TRI — the baseline's
equity leg. (b) is one assumed number, not a dataset. So this limitation is
much smaller than I said.

## 5. The baseline

*"70/30 Nifty 500 index fund + liquid fund, rebalanced annually, net of TER
and taxes. Every report prints the strategy and the baseline side by side."*

- **Equity leg**: Nifty 500 TRI (in `benchmark`, back to 1995) minus an
  assumed index-fund expense drag, applied daily. One config number.
- **Liquid leg**: open decision — see below.
- Annual rebalance, taxed through the same lot ledger as any strategy.

The baseline is not a footnote in the report. It is the null hypothesis.

## 6. Report

Per REQUIREMENTS.md: XIRR, annualised σ, max drawdown, Sortino, turnover,
total tax paid, and the spread vs baseline — strategy and baseline in
adjacent columns, always.

## 7. Randomisation control

Fixed-seed shuffle of the scoring signal across the eligible universe,
re-run end to end. If the real score does not beat the shuffled one, the
report says so **loudly and first**, not in a footnote. This is the check
that catches a scoring rule that is really just a beta or size tilt.

## Build order within the module

1. `PointInTimeView` + the identical-output isolation test
2. Universe reconstruction + a test that dead funds appear at historical T
3. Lot ledger + tax model, with a hand-worked cycle asserted against
4. Simulator loop
5. Baseline strategy
6. Walk-forward driver + report
7. Shuffled control

1–3 are the foundation; a bug in any of them silently flatters every result
that follows, so each gets tests before the next starts.

## Settled decisions

- **Liquid leg of the baseline: category-median liquid fund.** At each date,
  the median daily return across all Direct+Growth liquid funds alive on
  that date. Survivorship-aware by construction (the alive-set is
  recomputed daily from the same `PointInTimeView`), immune to any single
  fund's quirks, and built from data already ingested. Being a real fund
  series it is inherently net of TER — see the TER note above.

- **skfolio: not yet.** Rolling walk-forward splits are ~10 lines, and
  skfolio's CV assumes an sklearn-shaped return matrix that our
  path-dependent, tax-aware simulation is not. Hand-roll the splits behind
  a small interface so `CombinatorialPurgedCV` can be swapped in later when
  we actually want multi-path overfitting control — that is the one part of
  skfolio worth the dependency, and it is worth it only once single-path
  results exist to compare against.

- **Span: 2010 onwards.** Covers the 2013 taper tantrum, the 2018 IL&FS
  credit crisis and the 2020 crash — enough regime variety for the
  randomisation control to mean something. Pre-2010 is excluded because
  survivorship is weakest there: funds AMFI purged entirely are invisible
  to us, which would flatter early results.

  Note this interacts with Direct plans, which only exist from Jan 2013.
  Before that the harness must either fall back to Regular-plan NAV
  (understating returns by the TER gap) or start the equity sleeve later.
  Decide when implementing; whichever is chosen must be stated in the
  report, not buried.

## Still open

- **Rebalance cadence vs reconstitution cadence** — Module 5 specifies
  threshold-based drift (default 5% absolute) for *weights*. The
  walk-forward separately needs a cadence for revisiting *fund selection*.
  Two different knobs; conflating them would make results uninterpretable.
