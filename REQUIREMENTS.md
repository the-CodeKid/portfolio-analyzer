# Indian Mutual Fund Analysis Engine — Requirements

## Context

Personal decision-support tool for allocating a monthly SIP across Indian mutual funds.
Single user, self-hosted, no auth, no multi-tenancy. Not a product.

The goal is **not** "pick the best funds." The goal is a system that makes a fund-selection
rule explicit, then honestly tests whether that rule beats a passive baseline. If it
doesn't, the tool has still done its job.

## Prior art — read before writing code

Three repos have already solved parts of this. Do not rebuild them.

| Repo | What it gives us | How to use it |
|---|---|---|
| `asrajavel/portfolio-simulator` | Rolling XIRR over SIPs, rebalancing, step-up, allocation transitions, volatility. TypeScript, ~15k LOC, 24 test files. | Port `src/utils/calculations/sipRollingXirr/` to Python, **including its tests as fixtures** |
| `codereverser/casparser` | CAS PDF parsing, FIFO tax lots, Section 112A, Schedule CG, current-FY CII | `pip install casparser` — library dependency, do not vendor |
| `NayakwadiS/mftool` | `const.json` holds the full SEBI category taxonomy (39 codes → names) | Copy that one file. Skip the rest of the library. |

Confirmed absent from **all** existing Indian MF open source: Sharpe, Sortino, max drawdown,
alpha/beta regression, holdings overlap, survivorship-clean scheme master, walk-forward
backtesting. That is what we are building.

## Stack

- Python 3.11+ (matches casparser's floor)
- DuckDB for the NAV store (single-file, columnar, fast for this shape of data)
- pandas / numpy / statsmodels
- `skfolio` for portfolio optimisation and walk-forward cross-validation (BSD-3, sklearn API)
- pytest
- CLI first. No web UI in v1.

## Non-goals for v1

- Web frontend
- Broker integration or order placement
- Real-time / intraday data
- Multi-user, auth, deployment
- Debt fund credit analysis (rating migration, duration modelling)
- Any recommendation that isn't traceable to a number the tool computed

---

## Module 1 — Data ingestion

Build `ingest/` producing a DuckDB file.

**Sources**
- AMFI daily dump: `https://www.amfiindia.com/spages/NAVAll.txt`
- Historical NAV: `https://api.mfapi.in/mf/{scheme_code}`
- Holdings + TER: `https://mfdata.in/api/v1/` (validate before depending on it)

**Tables**
```
scheme_master(scheme_code, isin, name, amc, category_code, plan, option, first_seen, last_seen)
nav(scheme_code, date, nav)
scheme_lineage(canonical_id, scheme_code, valid_from, valid_to, reason)
holdings(canonical_id, as_of_month, isin, weight, sector)
scheme_meta(canonical_id, as_of_date, ter, aum, exit_load)
benchmark(index_name, date, tri_value)
```

**Requirements**

1. **Snapshot the scheme master every run.** Never overwrite. `first_seen` / `last_seen`
   are how we reconstruct the survivorship-free universe later. This is the single most
   important line in this document — get it wrong on day one and every backtest is
   permanently optimistic.
2. **Scheme lineage.** AMC mergers split a fund's history across scheme codes; an old code
   returns a truncated or empty series while the fund continues under a new one. Map to
   ISIN, maintain `scheme_lineage`, expose a `canonical_id` that stitches the series.
   Flag ambiguous merges for manual review rather than guessing.
3. **Filter to Direct + Growth** for all analysis. Ingest Regular too (needed to compute
   the TER gap) but exclude it from scoring by default.
4. **Do not adjust NAV for dividends.** Growth-plan NAV is already total return. Exclude
   IDCW plans entirely.
5. **Benchmarks must be TRI**, not price indices. A price-index comparison silently
   overstates every fund's alpha by roughly the dividend yield.
6. Idempotent re-runs. Incremental by date. Rate-limit and cache API calls.

---

## Module 2 — Metrics

Build `metrics/`. **Every function takes an `as_of` date and may only use data available
on or before it.** This is not optional — it is what makes Module 4 meaningful. Enforce
it with a decorator or a data-access wrapper, not with discipline.

**Returns**
- CAGR at 1/3/5/10y
- Rolling returns: 3y CAGR computed daily over a 10y window. From the distribution
  report median, p25, worst, and **% of windows beating the category benchmark**.
  Treat that last figure as the primary return signal; point-to-point CAGR is mostly
  a statement about the start date.

**Risk**
- Annualised σ, downside deviation
- Max drawdown + time to recovery
- VaR / CVaR at 95%

**Risk-adjusted**
- Sharpe, Sortino, Calmar, Information Ratio vs category TRI
- Risk-free rate from RBI 91-day T-bill

**Vs benchmark**
- OLS of excess fund return on excess benchmark return → alpha, beta, R²
- Upside / downside capture ratios

**Structural**
- TER, AUM, portfolio turnover, exit load, fund manager tenure

**Portfolio-derived**
- Top-10 concentration, sector HHI, market-cap split vs mandate, cash drag
- **Pairwise holdings overlap** — weight-aware, not just name intersection.
  `overlap(A,B) = Σ min(w_A(i), w_B(i))` over common ISINs. This is the highest-value
  output in the whole system and nothing in the ecosystem currently computes it.

---

## Module 3 — Scoring

Build `scoring/`, driven entirely by a YAML config. No thresholds hardcoded in Python.

- Score **within SEBI category only**. Never rank a small-cap fund against a liquid fund.
- Hard filters run before scoring: TER above category median, AUM below a floor,
  manager tenure under N years, fund age under N years — all configurable.
- Convert each metric to a within-category percentile, then weighted-sum.
- Default weights: rolling-return consistency, Sortino, negative TER. Keep it simple;
  elaborate composites do not outperform.
- Output must be **explainable**: for any fund, emit the per-metric percentile and its
  contribution to the final score. A score with no decomposition is not usable.

---

## Module 4 — Backtest harness

**Build this before Module 3 is finished.** It is the part that determines whether any of
the rest is real.

- Walk-forward: construct as of date T using only data available at T, hold under the
  rebalancing rule, measure forward. Roll T across many start dates.
- Use `skfolio`'s `WalkForward` and `CombinatorialPurgedCV`.
- Universe at each T must come from `scheme_master` as of T, including funds that later
  died. If a backtest silently drops dead funds, it is broken.
- **Mandatory baseline:** 70/30 Nifty 500 index fund + liquid fund, rebalanced annually,
  net of TER and taxes. Every report prints the strategy and the baseline side by side.
- Report: XIRR, σ, max drawdown, Sortino, turnover, total tax paid, and the spread vs
  baseline.
- Add a fixed-seed randomisation test: shuffle the scoring signal and re-run. If the real
  score does not beat the shuffled one, say so loudly in the output.

**Acceptance:** the harness must be capable of concluding that the engine loses to the
baseline. If it can't produce that result, it isn't measuring anything.

---

## Module 5 — Portfolio construction

- Asset allocation (equity / debt / gold / international) is a **config input**, not an
  engine output. The tool does not decide risk tolerance.
- Sub-allocate equity across large / flexi / mid / small per config.
- Select the top-scoring fund per bucket subject to an **overlap constraint**: reject any
  candidate whose weight-aware overlap with an already-selected fund exceeds a configurable
  threshold (default 0.30). Two 4-star flexi-caps holding the same 40 stocks are one fund
  with two expense ratios.
- Cap total fund count (default 6).
- Rebalancing: threshold-based on drift (default 5% absolute), not calendar-based.

**Tax model** — bake in, since it changes optimal behaviour:
- Equity STCG 20% (≤12m), LTCG 12.5% above ₹1.25L/FY (Section 112A), effective 23 Jul 2024
- Debt acquired on/after 1 Apr 2023: slab rate, no LT benefit (Section 50AA)
- Every SIP instalment is its own tax lot; redemption is FIFO
- Model the free ₹1.25L LTCG harvest each FY as an explicit rebalancing opportunity
- Delegate the arithmetic to `casparser.analysis.gains` where possible rather than
  reimplementing it

---

## Build order

1. Ingestion + scheme master snapshotting + lineage
2. Port `sipRollingXirr` from portfolio-simulator, with its test suite
3. Backtest harness with the passive baseline wired in
4. Metrics
5. Scoring config + overlap matrix
6. Portfolio constructor + tax-lot ledger
7. CLI reports

Steps 1 and 3 before anything else. Everything after is tuning.

## Testing

- Port portfolio-simulator's XIRR fixtures verbatim — they are the correctness oracle
- Hand-work at least one full SIP → rebalance → redemption → tax cycle and assert against it
- Golden-file test on a known fund's 3y rolling return distribution
- **An explicit test that as-of-date isolation holds**: metrics computed at T must be
  byte-identical whether or not post-T rows are present in the database

## Output disclaimer

Every generated report carries a line stating this is a personal analysis tool, not
investment advice, and that past performance metrics have weak predictive power for
future fund selection.
