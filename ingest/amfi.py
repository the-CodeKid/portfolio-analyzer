"""AMFI NAVAll.txt ingestion: scheme_master snapshotting + daily NAV.

NAVAll.txt has no columns for scheme_type/category/AMC/plan/option — they're
encoded as section headers and free-text scheme names. Structure:

    Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;Scheme Name;Net Asset Value;Date
    <blank>
    Open Ended Schemes(Debt Scheme - Banking and PSU Fund)
    <blank>
    Aditya Birla Sun Life Mutual Fund
    <blank>
    119551;INF209KA12Z1;INF209KA13Z9;...IDCW;106.8983;03-Aug-2026
    ...

scheme_type/category_raw come from the section header, AMC from the next
bare (non-semicolon) line, plan/option are derived from the free-text name
since AMFI doesn't break them out.
"""

from __future__ import annotations

import re
from datetime import date as date_cls

import pandas as pd
import requests

from ingest.categories import map_category
from ingest.db import connect

NAVALL_URL = "https://www.amfiindia.com/spages/NAVAll.txt"
_HEADERS = {"User-Agent": "Mozilla/5.0 (portfolio-analyzer ingest bot)"}

_SECTION_RE = re.compile(
    r"^(Open Ended Schemes|Close Ended Schemes|Interval Fund Schemes)\((.*)\)$"
)

# Indian MF ISINs are INF + 9 alphanumerics. AMFI's dump also carries "-" for
# "not applicable" plus occasional free-text sentinels ("Redeemed"), stray
# trailing spaces, and internal codes ("HDFCNIVODG"). Anything not matching
# the real shape is dropped to NULL rather than stored, because downstream
# lineage stitching joins on this column -- a sentinel like "Redeemed" shared
# by 9 unrelated schemes would otherwise merge them into one fund.
_ISIN_RE = re.compile(r"^INF[A-Z0-9]{9}$")


def clean_isin(raw: str | None) -> str | None:
    if raw is None:
        return None
    candidate = raw.strip().upper()
    return candidate if _ISIN_RE.match(candidate) else None


def fetch_navall(timeout: int = 30) -> str:
    resp = requests.get(NAVALL_URL, headers=_HEADERS, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def _derive_plan(name: str) -> str | None:
    low = name.lower()
    if "direct" in low:
        return "Direct"
    if "regular" in low:
        return "Regular"
    return None


def _derive_option(name: str) -> str | None:
    low = name.lower()
    if "idcw" in low or "dividend" in low:
        return "IDCW"
    if "bonus" in low:
        return "Bonus"
    if "growth" in low:
        return "Growth"
    return None


def parse_navall(text: str) -> pd.DataFrame:
    """Parse raw NAVAll.txt into one row per scheme with that day's NAV.

    Returns columns: scheme_code, isin, isin_reinvestment, name, amc,
    scheme_type, category_raw, category_code, plan, option, date, nav.
    """
    scheme_type: str | None = None
    category_raw: str | None = None
    category_code: int | None = None
    amc: str | None = None
    rows: list[dict] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("Scheme Code;"):
            continue

        section_match = _SECTION_RE.match(line)
        if section_match:
            scheme_type = section_match.group(1)
            category_raw = section_match.group(2)
            category_code = map_category(category_raw)
            amc = None  # next non-blank line will set it
            continue

        if ";" not in line:
            # bare line between a section header and its scheme rows -> AMC name
            amc = line
            continue

        fields = line.split(";")
        if len(fields) != 6:
            continue  # malformed row, skip rather than guess

        scheme_code_s, isin_growth, isin_reinvest, name, nav_s, date_s = fields
        try:
            scheme_code = int(scheme_code_s)
            nav = float(nav_s)
            nav_date = pd.to_datetime(date_s, format="%d-%b-%Y").date()
        except ValueError:
            continue  # header/footer noise, skip rather than guess

        rows.append(
            {
                "scheme_code": scheme_code,
                "isin": clean_isin(isin_growth),
                "isin_reinvestment": clean_isin(isin_reinvest),
                "name": name.strip(),
                "amc": amc,
                "scheme_type": scheme_type,
                "category_raw": category_raw,
                "category_code": category_code,
                "plan": _derive_plan(name),
                "option": _derive_option(name),
                "date": nav_date,
                "nav": nav,
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # scheme_code should be unique within a single day's dump; if AMFI ever
    # duplicates one, keep the last occurrence rather than silently summing/erroring
    df = df.drop_duplicates(subset="scheme_code", keep="last").reset_index(drop=True)
    return df


_MASTER_ATTR_COLS = [
    "isin",
    "isin_reinvestment",
    "name",
    "amc",
    "scheme_type",
    "category_raw",
    "category_code",
    "plan",
    "option",
]


def snapshot_scheme_master(
    con,
    snapshot: pd.DataFrame,
    run_date: date_cls,
    resurrection_gap_days: int = 7,
) -> dict:
    """SCD2 upsert of scheme_master from one day's parsed snapshot.

    A scheme_code gets a NEW row when it is unseen, when its attributes
    changed, or when it *resurrects* — i.e. it was absent from the previous
    run and has now reappeared. Otherwise its existing row's last_seen is
    extended forward. Dead scheme_codes (absent from `snapshot`) are left
    untouched, their last_seen frozen: that is the survivorship signal.

    Resurrection matters because extending last_seen straight across a gap
    would erase the period the fund was delisted, and every "was this fund
    alive at T?" query would then answer yes for months it was dead. It is
    detected against the *previous run's* global max last_seen rather than
    against wall-clock time, so an irregular ingest schedule (weekly, or a
    month's outage) doesn't mass-flag every scheme as resurrected.
    resurrection_gap_days tolerates AMFI publishing hiccups.

    last_seen only ever moves forward. Re-running against a stale or cached
    file must not rewind recorded history.
    """
    today = snapshot[["scheme_code", *_MASTER_ATTR_COLS]].copy()

    prev_global_last_seen = con.execute("SELECT max(last_seen) FROM scheme_master").fetchone()[0]

    current = con.execute(
        """
        SELECT * FROM scheme_master
        QUALIFY row_number() OVER (PARTITION BY scheme_code ORDER BY first_seen DESC) = 1
        """
    ).df()

    merged = today.merge(current, on="scheme_code", how="left", suffixes=("", "_prev"))

    if current.empty:
        is_known = pd.Series(False, index=merged.index)
        attrs_match = pd.Series(False, index=merged.index)
        resurrected = pd.Series(False, index=merged.index)
    else:
        is_known = merged["first_seen"].notna()
        attrs_match = pd.Series(True, index=merged.index)
        for col in _MASTER_ATTR_COLS:
            prev = merged[f"{col}_prev"]
            attrs_match &= (merged[col] == prev) | (merged[col].isna() & prev.isna())

        if prev_global_last_seen is None:
            resurrected = pd.Series(False, index=merged.index)
        else:
            gap_cutoff = pd.Timestamp(prev_global_last_seen) - pd.Timedelta(
                days=resurrection_gap_days
            )
            # absent at the previous run => its last_seen lags the run before this one
            resurrected = is_known & (pd.to_datetime(merged["last_seen"]) < gap_cutoff)

    needs_new_row = ~is_known | ~attrs_match | resurrected
    to_insert = merged.loc[needs_new_row, ["scheme_code", *_MASTER_ATTR_COLS]].copy()
    to_extend = merged.loc[~needs_new_row, ["scheme_code"]].copy()

    if not to_extend.empty:
        con.register("to_extend_master", to_extend)
        con.execute(
            """
            UPDATE scheme_master
            SET last_seen = greatest(last_seen, ?)
            FROM to_extend_master e
            WHERE scheme_master.scheme_code = e.scheme_code
              AND scheme_master.first_seen = (
                  SELECT max(first_seen) FROM scheme_master m2
                  WHERE m2.scheme_code = scheme_master.scheme_code
              )
            """,
            [run_date],
        )
        con.unregister("to_extend_master")

    if not to_insert.empty:
        to_insert["first_seen"] = run_date
        to_insert["last_seen"] = run_date
        con.register("to_insert_master", to_insert)
        con.execute(
            f"""
            INSERT INTO scheme_master
            SELECT scheme_code, {", ".join(_MASTER_ATTR_COLS)}, first_seen, last_seen
            FROM to_insert_master
            """
        )
        con.unregister("to_insert_master")

    return {
        "new_or_changed": int(len(to_insert)),
        "extended": int(len(to_extend)),
        "resurrected": int(resurrected.sum()),
    }


def upsert_nav(con, snapshot: pd.DataFrame) -> int:
    nav_rows = snapshot[["scheme_code", "date", "nav"]]
    con.register("today_nav", nav_rows)
    con.execute(
        """
        INSERT INTO nav (scheme_code, date, nav)
        SELECT scheme_code, date, nav FROM today_nav
        ON CONFLICT (scheme_code, date) DO UPDATE SET nav = excluded.nav
        """
    )
    con.unregister("today_nav")
    return len(nav_rows)


def run(con=None, text: str | None = None) -> dict:
    """Fetch (or use provided `text`), parse, and upsert scheme_master + nav.

    Snapshot date is taken from the data itself (max NAV date in the file)
    rather than wall-clock "today", so a re-run against a stale/cached file
    doesn't misdate first_seen/last_seen.
    """
    owns_con = con is None
    if owns_con:
        con = connect()
    try:
        if text is None:
            text = fetch_navall()
        snapshot = parse_navall(text)
        if snapshot.empty:
            return {"rows": 0}
        run_date = snapshot["date"].max()
        master_stats = snapshot_scheme_master(con, snapshot, run_date)
        n_nav = upsert_nav(con, snapshot)
        return {"run_date": run_date, "rows": len(snapshot), "nav_rows": n_nav, **master_stats}
    finally:
        if owns_con:
            con.close()
