from unittest.mock import patch

from ingest import mfapi

_FAKE_PAYLOAD = {
    "meta": {"scheme_code": 120503, "scheme_name": "Fake Fund - Direct Plan - Growth"},
    "data": [
        {"date": "02-08-2026", "nav": "111.50"},
        {"date": "01-08-2026", "nav": "110.00"},
    ],
}


def test_cached_fetch_hits_network_once_then_caches(tmp_path):
    cache_dir = tmp_path / "cache"
    with patch("ingest.mfapi.fetch_history", return_value=_FAKE_PAYLOAD) as mock_fetch:
        df1, hit1 = mfapi.cached_fetch(120503, cache_dir=cache_dir)
        df2, hit2 = mfapi.cached_fetch(120503, cache_dir=cache_dir)

    assert mock_fetch.call_count == 1  # second call served from disk cache
    assert hit1 is False
    assert hit2 is True
    assert len(df1) == 2 and len(df2) == 2
    assert df1["nav"].tolist() == [111.50, 110.00]


def test_cached_fetch_force_bypasses_cache(tmp_path):
    cache_dir = tmp_path / "cache"
    with patch("ingest.mfapi.fetch_history", return_value=_FAKE_PAYLOAD) as mock_fetch:
        mfapi.cached_fetch(120503, cache_dir=cache_dir)
        mfapi.cached_fetch(120503, cache_dir=cache_dir, force=True)
    assert mock_fetch.call_count == 2


def test_backfill_upserts_and_records_failures(db, tmp_path):
    cache_dir = tmp_path / "cache"

    def fake_fetch(scheme_code, timeout=30):
        if scheme_code == 999:
            raise mfapi.FetchError("scheme 999: no data in response")
        return _FAKE_PAYLOAD

    with patch("ingest.mfapi.fetch_history", side_effect=fake_fetch):
        stats = mfapi.backfill(db, [120503, 999], cache_dir=cache_dir, min_interval=0)

    assert stats["fetched"] == 1
    assert stats["failed"] == 1
    assert stats["nav_rows_upserted"] == 2
    n = db.execute("SELECT count(*) FROM nav WHERE scheme_code = 120503").fetchone()[0]
    assert n == 2


def test_backfill_rerun_is_cache_only_and_idempotent(db, tmp_path):
    cache_dir = tmp_path / "cache"
    with patch("ingest.mfapi.fetch_history", return_value=_FAKE_PAYLOAD) as mock_fetch:
        mfapi.backfill(db, [120503], cache_dir=cache_dir, min_interval=0)
        stats2 = mfapi.backfill(db, [120503], cache_dir=cache_dir, min_interval=0)

    assert mock_fetch.call_count == 1
    assert stats2["fetched"] == 0
    assert stats2["cached"] == 1
    n = db.execute("SELECT count(*) FROM nav WHERE scheme_code = 120503").fetchone()[0]
    assert n == 2  # no duplicates from re-run
