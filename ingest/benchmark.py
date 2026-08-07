"""Benchmark TRI ingestion.

REQUIREMENTS.md #5: benchmarks must be Total Return Indices, not price
indices — a price-index comparison silently overstates every fund's alpha by
roughly the dividend yield. NSE publishes TRI but gates it behind a
cookie/session flow; asrajavel/mf-index-data runs that scrape on a schedule
and commits the JSON, which is what we consume here.

That repo is one person's project and its updater has stalled before, so
treat it as a bootstrap rather than permanent infrastructure: it carries
history back to 1995 (Nifty 500), which is the expensive part to reproduce,
and going forward the same NSE endpoint can be scraped directly if needed.
`staleness_days` surfaces the risk instead of letting it pass silently.

Only NSE equity indices are available here. Debt-category benchmarks
(CRISIL) are not, so debt categories stay unmapped rather than being given
a wrong benchmark.
"""

from __future__ import annotations

import json
from datetime import date as date_cls
from pathlib import Path

import pandas as pd
import requests

RAW_BASE = "https://raw.githubusercontent.com/asrajavel/mf-index-data/main/index%20data"
DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache" / "indices"
_HEADERS = {"User-Agent": "Mozilla/5.0 (portfolio-analyzer ingest bot)"}

# SEBI category code (mftool const.json) -> NSE index whose TRI benchmarks it.
# Deliberately partial: only mappings defensible from the category mandate are
# listed. Sectoral/Thematic (12) spans dozens of unrelated mandates and has no
# single benchmark; debt, hybrid, and solution categories have no NSE TRI here.
# An unmapped category means "no benchmark yet", never a silently wrong one.
CATEGORY_BENCHMARK: dict[int, str] = {
    1: "NIFTY 100",                      # Large Cap
    2: "NIFTY LARGEMIDCAP 250",          # Large & Mid Cap
    3: "NIFTY 500",                      # Flexi Cap
    4: "NIFTY500 MULTICAP 50:25:25",     # Multi Cap
    5: "NIFTY MIDCAP 150",               # Mid Cap
    6: "NIFTY SMALLCAP 250",             # Small Cap
    7: "NIFTY 500",                      # Value
    8: "NIFTY 500",                      # ELSS
    9: "NIFTY 500",                      # Contra
    10: "NIFTY DIVIDEND OPPORTUNITIES 50",  # Dividend Yield
    11: "NIFTY 500",                     # Focused
}

# The passive baseline Module 4 measures every strategy against.
BASELINE_EQUITY_INDEX = "NIFTY 500"


class BenchmarkFetchError(Exception):
    pass


def fetch_index(index_name: str, timeout: int = 45) -> list[dict]:
    """Raw records for one index. The payload is a JSON object whose single
    key "d" holds the actual array as an embedded JSON *string*."""
    url = f"{RAW_BASE}/{requests.utils.quote(index_name)}.json"
    resp = requests.get(url, headers=_HEADERS, timeout=timeout)
    if resp.status_code != 200:
        raise BenchmarkFetchError(f"{index_name}: HTTP {resp.status_code}")

    payload = resp.json()
    inner = payload["d"] if isinstance(payload, dict) and "d" in payload else payload
    records = json.loads(inner) if isinstance(inner, str) else inner
    if not records:
        raise BenchmarkFetchError(f"{index_name}: empty payload")
    return records


def records_to_df(index_name: str, records: list[dict]) -> pd.DataFrame:
    """Normalize to (index_name, date, tri_value).

    The upstream "Index Name" field is inconsistently cased within a single
    file ("NIFTY 500" and "Nifty 500" both appear), so the canonical name we
    requested is used instead — otherwise one index would split into two
    series and every join against it would half-miss.
    """
    df = pd.DataFrame(records)
    df = df[df["TotalReturnsIndex"].notna() & (df["TotalReturnsIndex"] != "-")].copy()
    df["index_name"] = index_name
    df["date"] = pd.to_datetime(df["Date"], format="%d %b %Y").dt.date
    df["tri_value"] = df["TotalReturnsIndex"].astype(float)
    return (
        df[["index_name", "date", "tri_value"]]
        .drop_duplicates(subset=["index_name", "date"], keep="first")
        .sort_values("date")
        .reset_index(drop=True)
    )


def cached_fetch(
    index_name: str, cache_dir: Path = DEFAULT_CACHE_DIR, force: bool = False
) -> tuple[pd.DataFrame, bool]:
    path = cache_dir / f"{index_name}.json"
    if not force and path.exists():
        return records_to_df(index_name, json.loads(path.read_text())), True

    records = fetch_index(index_name)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records))
    return records_to_df(index_name, records), False


def upsert_benchmark(con, df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    con.register("new_benchmark", df)
    con.execute(
        """
        INSERT INTO benchmark (index_name, date, tri_value)
        SELECT index_name, date, tri_value FROM new_benchmark
        ON CONFLICT (index_name, date) DO UPDATE SET tri_value = excluded.tri_value
        """
    )
    con.unregister("new_benchmark")
    return len(df)


def ingest(
    con,
    index_names: list[str] | None = None,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    force: bool = False,
    as_of: date_cls | None = None,
) -> dict:
    """Load TRI for the given indices (default: every mapped benchmark plus
    the passive baseline). Idempotent."""
    if index_names is None:
        index_names = sorted({*CATEGORY_BENCHMARK.values(), BASELINE_EQUITY_INDEX})

    as_of = as_of or date_cls.today()
    fetched = cached = rows = 0
    failures: list[str] = []
    staleness: dict[str, int] = {}

    for name in index_names:
        try:
            df, was_cached = cached_fetch(name, cache_dir=cache_dir, force=force)
        except (BenchmarkFetchError, requests.RequestException, ValueError, KeyError) as exc:
            failures.append(f"{name}: {exc}")
            continue

        rows += upsert_benchmark(con, df)
        cached += was_cached
        fetched += not was_cached
        staleness[name] = (as_of - df["date"].max()).days

    return {
        "requested": len(index_names),
        "fetched": fetched,
        "cached": cached,
        "failed": len(failures),
        "rows_upserted": rows,
        "staleness_days": staleness,
        "failures": failures,
    }
