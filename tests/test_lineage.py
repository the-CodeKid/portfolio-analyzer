import datetime

import pandas as pd

from ingest import amfi, lineage


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

    lin = lineage.stitch_isin_lineage(db)
    old_cid = lin[lin["scheme_code"] == 119528]["canonical_id"].iloc[0]
    new_cid = lin[lin["scheme_code"] == 999900001]["canonical_id"].iloc[0]
    assert old_cid == new_cid


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
