import datetime

import pandas as pd

from ingest import amfi


def test_parse_navall_row_count_and_columns(navall_text):
    df = amfi.parse_navall(navall_text)
    assert len(df) == 17
    assert set(df.columns) == {
        "scheme_code", "isin", "isin_reinvestment", "name", "amc",
        "scheme_type", "category_raw", "category_code", "plan", "option",
        "date", "nav",
    }


def test_dash_isin_becomes_none(navall_text):
    df = amfi.parse_navall(navall_text).set_index("scheme_code")
    assert df.loc[119528, "isin_reinvestment"] is None
    assert df.loc[120437, "isin"] is None
    assert df.loc[120437, "isin_reinvestment"] == "INF846K01CU0"


def test_category_mapping_applied_and_legacy_left_null(navall_text):
    df = amfi.parse_navall(navall_text).set_index("scheme_code")
    assert df.loc[119528, "category_code"] == 1  # Large Cap
    assert df.loc[119551, "category_code"] == 25  # Banking and PSU
    assert df.loc[112368, "category_code"] == 38  # Gold ETF
    assert pd.isna(df.loc[154049, "category_code"])  # legacy Income/Debt Oriented header
    assert pd.isna(df.loc[103062, "category_code"])  # bare legacy "Income" header


def test_plan_and_option_derivation(navall_text):
    df = amfi.parse_navall(navall_text).set_index("scheme_code")
    assert df.loc[119528, "plan"] == "Direct"
    assert df.loc[119528, "option"] == "Growth"
    assert df.loc[103173, "plan"] == "Regular"
    assert df.loc[103173, "option"] == "IDCW"
    assert df.loc[128952, "option"] == "Bonus"
    # ETF name has neither plan nor growth/IDCW wording -> left unknown, not guessed
    assert df.loc[112368, "plan"] is None
    assert df.loc[112368, "option"] is None
    # scheme with no "direct"/"regular" token in the name at all
    assert df.loc[103174, "plan"] is None
    assert df.loc[103174, "option"] == "Growth"


def test_per_row_date_is_preserved_not_globalized(navall_text):
    # scheme_code 128952's line carries a 2017 date even though the file is
    # otherwise a 2026 snapshot (a fund that stopped trading long ago)
    df = amfi.parse_navall(navall_text).set_index("scheme_code")
    assert df.loc[128952, "date"] == datetime.date(2017, 6, 14)
    assert df.loc[119528, "date"] == datetime.date(2026, 8, 3)


def test_duplicate_scheme_code_keeps_last(navall_text):
    df = amfi.parse_navall(navall_text + "\r\n119528;X;-;Duplicate Row;1.0;03-Aug-2026\r\n")
    dup_rows = df[df["scheme_code"] == 119528]
    assert len(dup_rows) == 1
    assert dup_rows.iloc[0]["name"] == "Duplicate Row"


def test_snapshot_scheme_master_first_run_inserts_all(db, navall_text, today):
    snapshot = amfi.parse_navall(navall_text)
    stats = amfi.snapshot_scheme_master(db, snapshot, today)
    assert stats == {"new_or_changed": 17, "extended": 0, "resurrected": 0}
    n = db.execute("SELECT count(*) FROM scheme_master").fetchone()[0]
    assert n == 17


def test_snapshot_scheme_master_rerun_is_idempotent(db, navall_text, today):
    snapshot = amfi.parse_navall(navall_text)
    amfi.snapshot_scheme_master(db, snapshot, today)
    stats = amfi.snapshot_scheme_master(db, snapshot, today)
    assert stats == {"new_or_changed": 0, "extended": 17, "resurrected": 0}
    n = db.execute("SELECT count(*) FROM scheme_master").fetchone()[0]
    assert n == 17  # no duplicate rows from re-running


def test_snapshot_scheme_master_attribute_change_versions_not_overwrites(db, navall_text, today):
    snapshot = amfi.parse_navall(navall_text)
    amfi.snapshot_scheme_master(db, snapshot, today)

    day2 = snapshot.copy()
    day2.loc[day2["scheme_code"] == 119528, "name"] = "RENAMED FUND"
    day2_date = today + datetime.timedelta(days=1)
    stats = amfi.snapshot_scheme_master(db, day2, day2_date)
    assert stats == {"new_or_changed": 1, "extended": 16, "resurrected": 0}

    rows = db.execute(
        "SELECT name, first_seen, last_seen FROM scheme_master WHERE scheme_code = 119528 ORDER BY first_seen"
    ).df()
    assert len(rows) == 2
    assert rows.iloc[0]["name"] != "RENAMED FUND"
    assert rows.iloc[0]["last_seen"].date() == today  # old row frozen, not overwritten
    assert rows.iloc[1]["name"] == "RENAMED FUND"
    assert rows.iloc[1]["first_seen"].date() == rows.iloc[1]["last_seen"].date() == day2_date


def test_snapshot_scheme_master_dead_scheme_keeps_frozen_last_seen(db, navall_text, today):
    snapshot = amfi.parse_navall(navall_text)
    amfi.snapshot_scheme_master(db, snapshot, today)

    day2 = snapshot[snapshot["scheme_code"] != 119528].copy()  # 119528 stops appearing
    day2_date = today + datetime.timedelta(days=5)
    amfi.snapshot_scheme_master(db, day2, day2_date)

    row = db.execute(
        "SELECT first_seen, last_seen FROM scheme_master WHERE scheme_code = 119528"
    ).fetchone()
    assert row == (today, today)  # never touched again -> survivorship signal


def test_resurrected_scheme_opens_new_row_not_a_bridged_span(db, navall_text, today):
    """A fund that vanishes then returns must not have its dead period erased."""
    snapshot = amfi.parse_navall(navall_text)
    amfi.snapshot_scheme_master(db, snapshot, today)

    # scheme 119528 absent at the next run
    absent = snapshot[snapshot["scheme_code"] != 119528].copy()
    amfi.snapshot_scheme_master(db, absent, today + datetime.timedelta(days=30))

    # ...then returns, unchanged, months later
    return_date = today + datetime.timedelta(days=165)
    stats = amfi.snapshot_scheme_master(db, snapshot, return_date)
    assert stats["resurrected"] == 1

    rows = db.execute(
        "SELECT first_seen, last_seen FROM scheme_master WHERE scheme_code = 119528 ORDER BY first_seen"
    ).df()
    assert len(rows) == 2, "resurrection must open a second row, not extend the first"
    assert rows.iloc[0]["last_seen"].date() == today  # dead period preserved
    assert rows.iloc[1]["first_seen"].date() == return_date


def test_last_seen_never_moves_backwards_on_stale_input(db, navall_text, today):
    """Re-running against a cached/stale file must not rewind history."""
    snapshot = amfi.parse_navall(navall_text)
    amfi.snapshot_scheme_master(db, snapshot, today)

    forward = today + datetime.timedelta(days=20)
    amfi.snapshot_scheme_master(db, snapshot, forward)

    amfi.snapshot_scheme_master(db, snapshot, today)  # stale re-run

    max_last_seen = db.execute("SELECT max(last_seen) FROM scheme_master").fetchone()[0]
    assert max_last_seen == forward


def test_junk_isins_are_dropped_not_stored(navall_text):
    df = amfi.parse_navall(
        navall_text
        + "\r\n999001;Redeemed;Redeemed;Junk ISIN Fund - Direct Plan - Growth;10.0;03-Aug-2026"
        + "\r\n999002;INF178L01BT0 ;HDFCNIVODG;Messy ISIN Fund - Direct Plan - Growth;10.0;03-Aug-2026\r\n"
    ).set_index("scheme_code")

    assert df.loc[999001, "isin"] is None
    assert df.loc[999001, "isin_reinvestment"] is None
    assert df.loc[999002, "isin"] == "INF178L01BT0"  # trailing space stripped
    assert df.loc[999002, "isin_reinvestment"] is None  # internal code rejected


def test_upsert_nav_idempotent_and_updates_on_conflict(db, navall_text):
    snapshot = amfi.parse_navall(navall_text)
    amfi.upsert_nav(db, snapshot)
    amfi.upsert_nav(db, snapshot)  # re-run, same rows
    n = db.execute("SELECT count(*) FROM nav").fetchone()[0]
    assert n == len(snapshot)

    changed = snapshot.copy()
    changed.loc[changed["scheme_code"] == 119528, "nav"] = 999.99
    amfi.upsert_nav(db, changed)
    nav_val = db.execute(
        "SELECT nav FROM nav WHERE scheme_code = 119528 AND date = '2026-08-03'"
    ).fetchone()[0]
    assert nav_val == 999.99
    n_after = db.execute("SELECT count(*) FROM nav").fetchone()[0]
    assert n_after == len(snapshot)  # updated in place, not duplicated
