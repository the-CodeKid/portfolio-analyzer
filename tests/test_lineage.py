import datetime

import pandas as pd

from ingest import amfi, lineage


def _give_nav_history(db, scheme_code: int, start: datetime.date, end: datetime.date) -> None:
    """Real NAV span — lineage refuses to stitch without one (see has_span)."""
    rows = pd.DataFrame(
        {
            "scheme_code": scheme_code,
            "date": pd.date_range(start, end, freq="7D").date,
            "nav": 100.0,
        }
    )
    db.register("hist", rows)
    db.execute("INSERT INTO nav SELECT scheme_code, date, nav FROM hist ON CONFLICT DO NOTHING")
    db.unregister("hist")


def test_isin_lineage_links_reissued_scheme_code(db, navall_text, today):
    snapshot = amfi.parse_navall(navall_text)
    amfi.snapshot_scheme_master(db, snapshot, today)

    died_date = today + datetime.timedelta(days=30)
    day2 = snapshot[snapshot["scheme_code"] != 119528].copy()
    amfi.snapshot_scheme_master(db, day2, died_date)

    reissue = snapshot[snapshot["scheme_code"] == 119528].iloc[0].copy()
    reissue["scheme_code"] = 999900001  # new code, same ISIN as 119528
    reissue_date = today + datetime.timedelta(days=60)
    day3 = pd.concat([day2, pd.DataFrame([reissue])], ignore_index=True)
    amfi.snapshot_scheme_master(db, day3, reissue_date)

    # old code lived and died; new code picked up afterwards — no overlap
    _give_nav_history(db, 119528, today - datetime.timedelta(days=365), today)
    _give_nav_history(db, 999900001, reissue_date, reissue_date + datetime.timedelta(days=365))

    lin = lineage.stitch_isin_lineage(db, write_csv=False)
    old_cid = lin[lin["scheme_code"] == 119528]["canonical_id"].iloc[0]
    new_cid = lin[lin["scheme_code"] == 999900001]["canonical_id"].iloc[0]
    assert old_cid == new_cid


def test_shared_isin_but_overlapping_lives_is_not_stitched(db, navall_text, today):
    """AMFI ships duplicate ISINs across distinct concurrent schemes — those
    are a data error, not lineage."""
    snapshot = amfi.parse_navall(navall_text)
    twin = snapshot[snapshot["scheme_code"] == 119528].iloc[0].copy()
    twin["scheme_code"] = 999900003  # same ISIN, same plan/option, alive concurrently
    both = pd.concat([snapshot, pd.DataFrame([twin])], ignore_index=True)
    amfi.snapshot_scheme_master(db, both, today)

    span_start = today - datetime.timedelta(days=365)
    _give_nav_history(db, 119528, span_start, today)
    _give_nav_history(db, 999900003, span_start, today)

    lin = lineage.stitch_isin_lineage(db, write_csv=False)
    assert (
        lin[lin["scheme_code"] == 119528]["canonical_id"].iloc[0]
        != lin[lin["scheme_code"] == 999900003]["canonical_id"].iloc[0]
    )


def test_shared_isin_across_different_plans_is_not_stitched(db, navall_text, today):
    """Merging Direct into Regular would erase the TER difference."""
    snapshot = amfi.parse_navall(navall_text)
    regular_twin = snapshot[snapshot["scheme_code"] == 119528].iloc[0].copy()
    regular_twin["scheme_code"] = 999900004
    regular_twin["plan"] = "Regular"
    both = pd.concat([snapshot, pd.DataFrame([regular_twin])], ignore_index=True)
    amfi.snapshot_scheme_master(db, both, today)

    _give_nav_history(db, 119528, today - datetime.timedelta(days=365), today)
    _give_nav_history(db, 999900004, today + datetime.timedelta(days=30),
                      today + datetime.timedelta(days=395))

    lin = lineage.stitch_isin_lineage(db, write_csv=False)
    assert (
        lin[lin["scheme_code"] == 119528]["canonical_id"].iloc[0]
        != lin[lin["scheme_code"] == 999900004]["canonical_id"].iloc[0]
    )


def test_no_stitching_without_lifespan_evidence(db, navall_text, today):
    """Two dead funds each known only from a frozen single NAV date must not
    merge just because their two points happen not to coincide."""
    snapshot = amfi.parse_navall(navall_text)
    twin = snapshot[snapshot["scheme_code"] == 119528].iloc[0].copy()
    twin["scheme_code"] = 999900005
    both = pd.concat([snapshot, pd.DataFrame([twin])], ignore_index=True)
    amfi.snapshot_scheme_master(db, both, today)
    amfi.upsert_nav(db, both)  # one NAV point each — no real span

    lin = lineage.stitch_isin_lineage(db, write_csv=False)
    assert (
        lin[lin["scheme_code"] == 119528]["canonical_id"].iloc[0]
        != lin[lin["scheme_code"] == 999900005]["canonical_id"].iloc[0]
    )


def test_isin_lineage_gives_every_scheme_a_canonical_id(db, navall_text, today):
    snapshot = amfi.parse_navall(navall_text)
    amfi.snapshot_scheme_master(db, snapshot, today)
    lin = lineage.stitch_isin_lineage(db)
    assert set(lin["scheme_code"]) == set(snapshot["scheme_code"])
    assert lin["canonical_id"].notna().all()


def test_isin_lineage_does_not_link_unrelated_schemes(db, navall_text, today):
    snapshot = amfi.parse_navall(navall_text)
    amfi.snapshot_scheme_master(db, snapshot, today)
    lin = lineage.stitch_isin_lineage(db)
    cid_119528 = lin[lin["scheme_code"] == 119528]["canonical_id"].iloc[0]
    cid_119551 = lin[lin["scheme_code"] == 119551]["canonical_id"].iloc[0]
    assert cid_119528 != cid_119551


def test_candidate_mergers_flags_similar_successor_not_auto_linked(db, navall_text, today):
    snapshot = amfi.parse_navall(navall_text)
    amfi.snapshot_scheme_master(db, snapshot, today)

    died_date = today + datetime.timedelta(days=30)
    day2 = snapshot[snapshot["scheme_code"] != 119528].copy()
    amfi.snapshot_scheme_master(db, day2, died_date)

    successor = snapshot[snapshot["scheme_code"] == 119528].iloc[0].copy()
    successor["scheme_code"] = 999900002
    successor["isin"] = "INF000000000"  # different ISIN -> ISIN-stitching can't see this
    successor["isin_reinvestment"] = None
    successor["name"] = successor["name"].replace("Aditya Birla Sun Life", "New Umbrella AMC")
    successor["amc"] = "New Umbrella AMC Mutual Fund"
    successor_date = today + datetime.timedelta(days=60)
    day3 = pd.concat([day2, pd.DataFrame([successor])], ignore_index=True)
    amfi.snapshot_scheme_master(db, day3, successor_date)

    # extend "latest" further so the died fund clears the gap_days cutoff
    later_date = today + datetime.timedelta(days=250)
    amfi.snapshot_scheme_master(db, day3, later_date)

    # ISIN stitching must NOT link them (different ISIN)
    lin = lineage.stitch_isin_lineage(db)
    assert lin[lin["scheme_code"] == 119528]["canonical_id"].iloc[0] != (
        lin[lin["scheme_code"] == 999900002]["canonical_id"].iloc[0]
    )

    candidates = lineage.find_candidate_mergers(db, gap_days=180, min_similarity=0.4, write_csv=False)
    match = candidates[
        (candidates["died_scheme_code"] == 119528)
        & (candidates["candidate_scheme_code"] == 999900002)
    ]
    assert len(match) == 1
    assert match.iloc[0]["name_similarity"] > 0.4


def test_candidate_mergers_empty_when_nothing_died(db, navall_text, today):
    snapshot = amfi.parse_navall(navall_text)
    amfi.snapshot_scheme_master(db, snapshot, today)
    candidates = lineage.find_candidate_mergers(db, write_csv=False)
    assert candidates.empty
