"""Point-in-time data access — the harness's only route to the database.

REQUIREMENTS.md Module 2: "Every function takes an `as_of` date and may only
use data available on or before it. Enforce it with a decorator or a
data-access wrapper, not with discipline."

This is the wrapper. A decorator only protects the functions someone
remembers to decorate; this type protects everything by construction,
because it holds the connection privately and bakes `<= as_of` into every
query it issues. Strategies and metrics receive a view, never a connection,
so there is no unguarded path to post-as_of data.

The property that matters is tested directly: results must be *identical*
whether or not the database physically contains rows after as_of.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

DEFAULT_STALE_DAYS = 30


class PointInTimeView:
    """Read-only view of the store, frozen at `as_of`."""

    def __init__(self, con, as_of: date):
        self._con = con  # private on purpose: no unguarded path to the data
        self._as_of = as_of

    @property
    def as_of(self) -> date:
        return self._as_of

    def has_attribute_lookahead(self) -> bool:
        """True when no scheme_master snapshot exists at or before as_of, so
        category/plan/option must be read from a *later* snapshot.

        This is a genuine lookahead channel that cannot be fixed
        retroactively — only by accumulating snapshots going forward. Any
        report covering such dates is required to say so rather than bury
        it, so the harness can query this and print it.
        """
        earliest = self._con.execute("SELECT min(first_seen) FROM scheme_master").fetchone()[0]
        return earliest is None or earliest > self._as_of

    # ────────────── NAV ────────────── #

    def nav(self, scheme_code: int) -> pd.Series:
        """Daily NAV for one scheme, up to and including as_of."""
        df = self._con.execute(
            "SELECT date, nav FROM nav WHERE scheme_code = ? AND date <= ? ORDER BY date",
            [scheme_code, self._as_of],
        ).df()
        return pd.Series(
            df["nav"].to_numpy(),
            index=pd.DatetimeIndex(df["date"], name="date"),
            name=scheme_code,
            dtype="float64",
        )

    def nav_matrix(self, scheme_codes: list[int]) -> pd.DataFrame:
        """NAV for several schemes as one date-indexed frame (NaN where a
        scheme has no observation on a date)."""
        if not scheme_codes:
            return pd.DataFrame(index=pd.DatetimeIndex([], name="date"))
        placeholders = ", ".join("?" * len(scheme_codes))
        df = self._con.execute(
            f"""
            SELECT date, scheme_code, nav FROM nav
            WHERE scheme_code IN ({placeholders}) AND date <= ?
            ORDER BY date
            """,
            [*scheme_codes, self._as_of],
        ).df()
        if df.empty:
            return pd.DataFrame(index=pd.DatetimeIndex([], name="date"))
        wide = df.pivot(index="date", columns="scheme_code", values="nav")
        wide.index = pd.DatetimeIndex(wide.index, name="date")
        wide.columns.name = None
        return wide.reindex(columns=[c for c in scheme_codes if c in wide.columns])

    # ────────────── Benchmarks ────────────── #

    def benchmark(self, index_name: str) -> pd.Series:
        """Total-return index level, up to and including as_of."""
        df = self._con.execute(
            """
            SELECT date, tri_value FROM benchmark
            WHERE index_name = ? AND date <= ? ORDER BY date
            """,
            [index_name, self._as_of],
        ).df()
        return pd.Series(
            df["tri_value"].to_numpy(),
            index=pd.DatetimeIndex(df["date"], name="date"),
            name=index_name,
            dtype="float64",
        )

    # ────────────── Universe ────────────── #

    def universe(
        self,
        *,
        category_code: int | None = None,
        plan: str | None = "Direct",
        option: str | None = "Growth",
        min_history_days: int | None = None,
        stale_days: int = DEFAULT_STALE_DAYS,
    ) -> list[int]:
        """Scheme codes investable at as_of — including funds that later died.

        Liveness is derived from NAV observations rather than
        scheme_master.first_seen. Our own snapshots only begin in 2026, so
        first_seen is identical for every scheme and useless for historical
        dates; NAV history, by contrast, reaches back decades and ends when
        a fund actually stopped reporting. That is what lets already-dead
        funds appear in the universe at a historical T and drop out at their
        death date, which is the whole point of a survivorship-free backtest.

        A scheme qualifies when it has NAV starting on or before as_of and
        its most recent NAV on or before as_of is no more than `stale_days`
        old — funds stop reporting when they die, but ordinary holiday gaps
        must not evict a live fund.

        Known limitation: category/plan/option come from the earliest
        snapshot at or before as_of, falling back to the earliest snapshot
        we hold. Since snapshotting began in 2026, any historical backtest
        necessarily classifies funds by their *present* category. SEBI's
        2018 recategorisation moved many funds between categories, so this
        is a genuine lookahead channel for pre-2018 dates. It cannot be
        fixed retroactively — only by accumulating snapshots going forward —
        so it is surfaced rather than hidden.
        """
        cutoff_stale = self._as_of - timedelta(days=stale_days)

        sql = """
            WITH spans AS (
                SELECT scheme_code, min(date) AS first_nav, max(date) AS last_nav
                FROM nav WHERE date <= ?
                GROUP BY scheme_code
            ),
            attrs AS (
                SELECT scheme_code, category_code, plan, option
                FROM scheme_master
                QUALIFY row_number() OVER (
                    PARTITION BY scheme_code
                    ORDER BY (first_seen <= ?) DESC, first_seen ASC
                ) = 1
            )
            SELECT s.scheme_code
            FROM spans s JOIN attrs a USING (scheme_code)
            WHERE s.last_nav >= ?
        """
        params: list = [self._as_of, self._as_of, cutoff_stale]

        if category_code is not None:
            sql += " AND a.category_code = ?"
            params.append(category_code)
        if plan is not None:
            sql += " AND a.plan = ?"
            params.append(plan)
        if option is not None:
            sql += " AND a.option = ?"
            params.append(option)
        if min_history_days is not None:
            sql += " AND date_diff('day', s.first_nav, ?) >= ?"
            params.extend([self._as_of, min_history_days])

        sql += " ORDER BY s.scheme_code"
        return self._con.execute(sql, params).df()["scheme_code"].astype(int).tolist()

    # ────────────── Category aggregate ────────────── #

    def category_median_return(
        self,
        category_code: int,
        *,
        stale_days: int = DEFAULT_STALE_DAYS,
    ) -> pd.Series:
        """Median daily return across every live fund in a category.

        The baseline's liquid leg. Recomputing the alive-set from the same
        as_of-bounded universe on every date makes this survivorship-aware
        by construction, and being built from real fund NAVs it is already
        net of the TER those funds charged.
        """
        codes = self.universe(category_code=category_code, stale_days=stale_days)
        if not codes:
            return pd.Series(dtype="float64", index=pd.DatetimeIndex([], name="date"))

        navs = self.nav_matrix(codes)
        if navs.empty:
            return pd.Series(dtype="float64", index=pd.DatetimeIndex([], name="date"))

        returns = navs.ffill().pct_change()
        median = returns.median(axis=1, skipna=True)
        return median.dropna().rename(f"category_{category_code}_median_return")
