import time
from unittest.mock import patch

import pytest
import requests

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


def test_transient_network_error_is_retried_then_succeeds():
    """A single timeout must not kill a multi-thousand-scheme run."""
    class OkResp:
        status_code = 200

        def json(self):
            return _FAKE_PAYLOAD

    attempts = []

    def flaky(*args, **kwargs):
        attempts.append(1)
        if len(attempts) < 3:
            raise requests.exceptions.ReadTimeout("boom")
        return OkResp()

    with patch("ingest.mfapi.requests.get", side_effect=flaky), patch("ingest.mfapi.time.sleep"):
        assert mfapi.fetch_history(120503) == _FAKE_PAYLOAD
    assert len(attempts) == 3


def test_network_error_becomes_fetcherror_after_max_attempts():
    with patch(
        "ingest.mfapi.requests.get", side_effect=requests.exceptions.ConnectionError("down")
    ), patch("ingest.mfapi.time.sleep"):
        with pytest.raises(mfapi.FetchError, match="ConnectionError"):
            mfapi.fetch_history(120503, max_attempts=2)


def test_client_error_is_not_retried():
    """A 404 means the scheme genuinely isn't there — retrying wastes the rate limit."""
    class NotFound:
        status_code = 404

    calls = []

    def counted(*args, **kwargs):
        calls.append(1)
        return NotFound()

    with patch("ingest.mfapi.requests.get", side_effect=counted), patch("ingest.mfapi.time.sleep"):
        with pytest.raises(mfapi.FetchError, match="HTTP 404"):
            mfapi.fetch_history(120503)
    assert len(calls) == 1


def test_backfill_aborts_after_sustained_failures(db, tmp_path):
    """A down API should stop the run, not grind through thousands of retries."""
    with patch("ingest.mfapi.fetch_history", side_effect=mfapi.FetchError("api down")):
        stats = mfapi.backfill(
            db, list(range(1, 200)), cache_dir=tmp_path / "c",
            min_interval=0, workers=1, abort_after_consecutive_failures=10,
        )
    assert stats["aborted"] is True
    assert stats["processed"] < 199  # stopped early rather than trying all


def test_backfill_runs_in_parallel_and_still_upserts_correctly(db, tmp_path):
    codes = list(range(500, 530))

    def slow_fetch(scheme_code, **kwargs):
        time.sleep(0.01)
        return _FAKE_PAYLOAD

    with patch("ingest.mfapi.fetch_history", side_effect=slow_fetch):
        stats = mfapi.backfill(
            db, codes, cache_dir=tmp_path / "c", min_interval=0, workers=8
        )

    assert stats["fetched"] == len(codes)
    assert stats["failed"] == 0
    assert stats["aborted"] is False
    n = db.execute("SELECT count(DISTINCT scheme_code) FROM nav").fetchone()[0]
    assert n == len(codes)  # every scheme's rows landed despite concurrent fetching


def test_rate_limiter_paces_globally_across_threads():
    from concurrent.futures import ThreadPoolExecutor

    limiter = mfapi._RateLimiter(0.02)
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: limiter.acquire(), range(10)))
    elapsed = time.monotonic() - start
    # 10 acquisitions at 20ms apart must take >=~180ms no matter the worker count
    assert elapsed >= 0.18
