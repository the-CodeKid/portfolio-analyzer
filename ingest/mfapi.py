"""Historical NAV backfill from api.mfapi.in.

AMFI's daily dump only carries each scheme's latest NAV. Full history comes
from api.mfapi.in/mf/{scheme_code}, one scheme at a time. Backfilling every
scheme means a lot of requests against a free, unauthenticated API, so this
module rate-limits outbound calls and caches raw responses to disk — a
re-run should cost near-zero network calls once a scheme has been fetched.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
import requests

DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache" / "mfapi"
BASE_URL = "https://api.mfapi.in/mf"
_HEADERS = {"User-Agent": "Mozilla/5.0 (portfolio-analyzer ingest bot)"}


class FetchError(Exception):
    pass


def fetch_history(scheme_code: int, timeout: int = 30) -> dict:
    """Raw JSON payload from api.mfapi.in for one scheme. Raises FetchError."""
    resp = requests.get(f"{BASE_URL}/{scheme_code}", headers=_HEADERS, timeout=timeout)
    if resp.status_code != 200:
        raise FetchError(f"scheme {scheme_code}: HTTP {resp.status_code}")
    payload = resp.json()
    if payload.get("status") == "404" or not payload.get("data"):
        raise FetchError(f"scheme {scheme_code}: no data in response")
    return payload


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
) -> tuple[pd.DataFrame, bool]:
    """Returns (nav_dataframe, was_cache_hit). Raises FetchError on API failure
    with no usable cache to fall back on."""
    path = _cache_path(scheme_code, cache_dir)
    if not force and path.exists():
        payload = json.loads(path.read_text())
        return _payload_to_df(scheme_code, payload), True

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
    min_interval: float = 0.34,
    force: bool = False,
) -> dict:
    """Backfill full NAV history for each scheme_code. Cache hits are free;
    only actual network fetches are rate-limited."""
    n_fetched = n_cached = n_rows = 0
    failures: list[str] = []

    for code in scheme_codes:
        try:
            df, was_cached = cached_fetch(code, cache_dir=cache_dir, force=force)
        except FetchError as exc:
            failures.append(str(exc))
            continue

        n_rows += upsert_nav_df(con, df)
        if was_cached:
            n_cached += 1
        else:
            n_fetched += 1
            time.sleep(min_interval)

    return {
        "requested": len(scheme_codes),
        "fetched": n_fetched,
        "cached": n_cached,
        "failed": len(failures),
        "nav_rows_upserted": n_rows,
        "failures": failures,
    }
