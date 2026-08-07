"""Historical NAV backfill from api.mfapi.in.

AMFI's daily dump only carries each scheme's latest NAV. Full history comes
from api.mfapi.in/mf/{scheme_code}, one scheme at a time. Backfilling every
scheme means a lot of requests against a free, unauthenticated API, so this
module rate-limits outbound calls and caches raw responses to disk — a
re-run should cost near-zero network calls once a scheme has been fetched.
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests

DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache" / "mfapi"
BASE_URL = "https://api.mfapi.in/mf"
_HEADERS = {"User-Agent": "Mozilla/5.0 (portfolio-analyzer ingest bot)"}


class FetchError(Exception):
    pass


class _RateLimiter:
    """Global pacing shared by all worker threads.

    Concurrency here is to hide per-request latency, not to multiply load:
    api.mfapi.in is a free community service. Workers contend for one
    schedule, so raising worker count shortens the run without raising the
    request rate above `min_interval`.
    """

    def __init__(self, min_interval: float):
        self._min_interval = min_interval
        self._lock = threading.Lock()
        self._next_at = 0.0

    def acquire(self) -> None:
        if self._min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._next_at - now)
            self._next_at = max(now, self._next_at) + self._min_interval
        if wait:
            time.sleep(wait)


def fetch_history(
    scheme_code: int,
    timeout: int = 30,
    max_attempts: int = 4,
    backoff: float = 2.0,
) -> dict:
    """Raw JSON payload from api.mfapi.in for one scheme.

    Every failure mode is normalized to FetchError so a caller looping over
    thousands of schemes can't be killed by a single transient hiccup.
    Timeouts, connection resets and 5xx are retried with exponential
    backoff; 4xx and malformed payloads fail immediately, since retrying a
    scheme the API genuinely doesn't have just wastes the rate limit.
    """
    last_error: str | None = None

    for attempt in range(max_attempts):
        try:
            resp = requests.get(f"{BASE_URL}/{scheme_code}", headers=_HEADERS, timeout=timeout)
        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        else:
            if resp.status_code == 200:
                try:
                    payload = resp.json()
                except ValueError as exc:
                    raise FetchError(f"scheme {scheme_code}: bad JSON ({exc})") from exc
                if payload.get("status") == "404" or not payload.get("data"):
                    raise FetchError(f"scheme {scheme_code}: no data in response")
                return payload
            if resp.status_code < 500:
                raise FetchError(f"scheme {scheme_code}: HTTP {resp.status_code}")
            last_error = f"HTTP {resp.status_code}"

        if attempt < max_attempts - 1:
            time.sleep(backoff * (2**attempt))

    raise FetchError(f"scheme {scheme_code}: {last_error} after {max_attempts} attempts")


def _payload_to_df(scheme_code: int, payload: dict) -> pd.DataFrame:
    records = payload["data"]
    df = pd.DataFrame(records)
    df["scheme_code"] = scheme_code
    df["date"] = pd.to_datetime(df["date"], format="%d-%m-%Y").dt.date
    df["nav"] = df["nav"].astype(float)
    return df[["scheme_code", "date", "nav"]]


def _cache_path(scheme_code: int, cache_dir: Path) -> Path:
    return cache_dir / f"{scheme_code}.json"


def cached_fetch(
    scheme_code: int,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    force: bool = False,
    limiter: "_RateLimiter | None" = None,
) -> tuple[pd.DataFrame, bool]:
    """Returns (nav_dataframe, was_cache_hit). Raises FetchError on API failure
    with no usable cache to fall back on.

    The rate limiter is only consulted for real network fetches — a resumed
    run served entirely from cache costs nothing and shouldn't be paced.
    """
    path = _cache_path(scheme_code, cache_dir)
    if not force and path.exists():
        payload = json.loads(path.read_text())
        return _payload_to_df(scheme_code, payload), True

    if limiter is not None:
        limiter.acquire()
    payload = fetch_history(scheme_code)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return _payload_to_df(scheme_code, payload), False


def upsert_nav_df(con, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    con.register("backfill_nav", df)
    con.execute(
        """
        INSERT INTO nav (scheme_code, date, nav)
        SELECT scheme_code, date, nav FROM backfill_nav
        ON CONFLICT (scheme_code, date) DO UPDATE SET nav = excluded.nav
        """
    )
    con.unregister("backfill_nav")
    return len(df)


def backfill(
    con,
    scheme_codes: list[int],
    cache_dir: Path = DEFAULT_CACHE_DIR,
    min_interval: float = 0.12,
    force: bool = False,
    workers: int = 6,
    abort_after_consecutive_failures: int = 25,
    progress_every: int = 250,
    on_progress=None,
) -> dict:
    """Backfill full NAV history for each scheme_code.

    Individual scheme failures are collected, never raised: one dead scheme
    or transient timeout must not abandon a multi-thousand-scheme run. The
    disk cache makes a re-run resume roughly where it left off.

    A long run of consecutive failures means the API is down rather than the
    schemes being bad, so the run aborts instead of spending an hour
    retrying into a wall. `aborted` in the result says whether that fired.
    """
    n_fetched = n_cached = n_rows = 0
    failures: list[str] = []
    consecutive_failures = 0
    aborted = False
    limiter = _RateLimiter(min_interval)

    def fetch_one(code: int):
        return code, cached_fetch(code, cache_dir=cache_dir, force=force, limiter=limiter)

    # Fetching is I/O-bound so it parallelises well, but DuckDB connections
    # are not thread-safe: workers only fetch, and every upsert happens here
    # on the main thread as results arrive.
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(fetch_one, code): code for code in scheme_codes}
        try:
            for i, future in enumerate(as_completed(futures), start=1):
                try:
                    _, (df, was_cached) = future.result()
                except FetchError as exc:
                    failures.append(str(exc))
                    consecutive_failures += 1
                    if consecutive_failures >= abort_after_consecutive_failures:
                        aborted = True
                        break
                    continue

                consecutive_failures = 0
                n_rows += upsert_nav_df(con, df)
                n_cached += was_cached
                n_fetched += not was_cached

                if on_progress and i % progress_every == 0:
                    on_progress(i, len(scheme_codes), n_fetched, n_cached, len(failures))
        finally:
            if aborted:
                for pending in futures:
                    pending.cancel()

    return {
        "requested": len(scheme_codes),
        "processed": n_fetched + n_cached + len(failures),
        "fetched": n_fetched,
        "cached": n_cached,
        "failed": len(failures),
        "aborted": aborted,
        "nav_rows_upserted": n_rows,
        "failures": failures,
    }
