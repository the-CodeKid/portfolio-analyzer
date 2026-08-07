"""The isolation test REQUIREMENTS.md mandates:

    "An explicit test that as-of-date isolation holds: metrics computed at T
     must be byte-identical whether or not post-T rows are present in the
     database"

Everything else in the harness rests on this. If a view method can see past
its own as_of, every backtest built on it is silently optimistic.
"""

import datetime

import pandas as pd
import pytest

from asof.view import PointInTimeView
from ingest.db import connect

T = datetime.date(2020, 6, 30)


def _seed(con, *, include_post_t: bool) -> None:
    """Two schemes and a benchmark spanning T. With include_post_t=False the
    post-T rows are simply never written, so the database physically cannot
    leak them."""
    end = datetime.date(2021, 12, 31) if include_post_t else T

    nav_rows = []
    for code, start, nav0, death in (
        (1001, datetime.date(2015, 1, 1), 100.0, None),
        (1002, datetime.date(2016, 1, 1), 50.0, datetime.date(2019, 3, 31)),  # dies before T
        (1003, datetime.date(2021, 1, 1), 10.0, None),  # born after T
    ):
        d = start
        i = 0
        while d <= end:
            if death is not None and d > death:
                break
            nav_rows.append({"scheme_code": code, "date": d, "nav": nav0 * (1.0 + i / 1000)})
            d += datetime.timedelta(days=1)
            i += 1
    if nav_rows:
        con.register("seed_nav", pd.DataFrame(nav_rows))
        con.execute("INSERT INTO nav SELECT scheme_code, date, nav FROM seed_nav")
        con.unregister("seed_nav")

    master = pd.DataFrame(
        [
            {"scheme_code": c, "isin": f"INF00000000{c%10}", "isin_reinvestment": None,
             "name": f"Fund {c}", "amc": "Test AMC", "scheme_type": "Open Ended Schemes",
             "category_raw": "Equity Scheme - Large Cap Fund", "category_code": 1,
             "plan": "Direct", "option": "Growth",
             "first_seen": datetime.date(2026, 8, 7), "last_seen": datetime.date(2026, 8, 7)}
            for c in (1001, 1002, 1003)
        ]
    )
    con.register("seed_master", master)
    con.execute("INSERT INTO scheme_master SELECT * FROM seed_master")
    con.unregister("seed_master")

    bench = []
    d = datetime.date(2015, 1, 1)
    i = 0
    while d <= end:
        bench.append({"index_name": "NIFTY 500", "date": d, "tri_value": 1000.0 + i})
        d += datetime.timedelta(days=1)
        i += 1
    con.register("seed_bench", pd.DataFrame(bench))
    con.execute("INSERT INTO benchmark SELECT index_name, date, tri_value FROM seed_bench")
    con.unregister("seed_bench")


@pytest.fixture
def truncated_db(tmp_path):
    con = connect(tmp_path / "truncated.duckdb")
    _seed(con, include_post_t=False)
    yield con
    con.close()


@pytest.fixture
def full_db(tmp_path):
    con = connect(tmp_path / "full.duckdb")
    _seed(con, include_post_t=True)
    yield con
    con.close()


def test_nav_identical_with_and_without_post_t_rows(truncated_db, full_db):
    a = PointInTimeView(truncated_db, T).nav(1001)
    b = PointInTimeView(full_db, T).nav(1001)
    pd.testing.assert_series_equal(a, b)
    assert a.index.max() <= pd.Timestamp(T)


def test_nav_matrix_identical_with_and_without_post_t_rows(truncated_db, full_db):
    codes = [1001, 1002, 1003]
    pd.testing.assert_frame_equal(
        PointInTimeView(truncated_db, T).nav_matrix(codes),
        PointInTimeView(full_db, T).nav_matrix(codes),
    )


def test_benchmark_identical_with_and_without_post_t_rows(truncated_db, full_db):
    pd.testing.assert_series_equal(
        PointInTimeView(truncated_db, T).benchmark("NIFTY 500"),
        PointInTimeView(full_db, T).benchmark("NIFTY 500"),
    )


def test_universe_identical_with_and_without_post_t_rows(truncated_db, full_db):
    assert PointInTimeView(truncated_db, T).universe() == PointInTimeView(full_db, T).universe()


def test_category_median_return_identical_with_and_without_post_t_rows(truncated_db, full_db):
    pd.testing.assert_series_equal(
        PointInTimeView(truncated_db, T).category_median_return(1),
        PointInTimeView(full_db, T).category_median_return(1),
    )


# ────────────── Universe semantics ────────────── #


def test_universe_excludes_funds_not_yet_born(full_db):
    # 1003's first NAV is 2021-01-01, six months after T
    assert 1003 not in PointInTimeView(full_db, T).universe()


def test_universe_excludes_funds_already_dead_at_t(full_db):
    # 1002 stopped reporting 2019-03-31, well past stale_days before T
    assert 1002 not in PointInTimeView(full_db, T).universe()


def test_dead_fund_is_still_in_the_universe_while_it_was_alive(full_db):
    """The survivorship property: a fund that later dies must appear at
    dates when it was genuinely investable."""
    view = PointInTimeView(full_db, datetime.date(2018, 6, 30))
    assert 1002 in view.universe()


def test_universe_respects_min_history_days(full_db):
    view = PointInTimeView(full_db, T)
    assert 1001 in view.universe(min_history_days=365 * 5)   # born 2015, ~5.5y by T
    assert 1001 not in view.universe(min_history_days=365 * 10)


def test_universe_filters_by_category_plan_option(full_db):
    view = PointInTimeView(full_db, T)
    assert view.universe(category_code=1) == [1001]
    assert view.universe(category_code=999) == []
    assert view.universe(plan="Regular") == []
    assert view.universe(option="IDCW") == []


def test_view_exposes_no_connection_handle():
    """Structural enforcement: if a strategy can reach the connection, the
    as_of guarantee is advisory rather than real."""
    public = [a for a in dir(PointInTimeView) if not a.startswith("_")]
    assert "con" not in public and "connection" not in public


def test_attribute_lookahead_is_reported_for_historical_dates(full_db):
    """Snapshots begin 2026-08-07, so any historical backtest classifies
    funds by their present category. The report must be able to say so."""
    assert PointInTimeView(full_db, T).has_attribute_lookahead() is True
    assert PointInTimeView(full_db, datetime.date(2026, 12, 31)).has_attribute_lookahead() is False
