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
                "isin": None if isin_growth == "-" else isin_growth,
                "isin_reinvestment": None if isin_reinvest == "-" else isin_reinvest,
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


def snapshot_scheme_master(con, snapshot: pd.DataFrame, run_date: date_cls) -> dict:
    """SCD2 upsert of scheme_master from one day's parsed snapshot.

    New scheme_codes, or ones whose attributes changed since the last known
    row, get a fresh row (first_seen=last_seen=run_date). Scheme_codes whose
    attributes are unchanged just have last_seen extended to run_date. Dead
    scheme_codes (absent from `snapshot`) are left untouched — their last
    known row's last_seen stays frozen, which is the survivorship signal.
    Idempotent: re-running with the same snapshot/run_date is a no-op beyond
    re-setting last_seen.
    """
    today = snapshot[["scheme_code", *_MASTER_ATTR_COLS]].copy()
    con.register("today_master", today)

    con.execute(
        """
        CREATE OR REPLACE TEMP VIEW current_master AS
        SELECT * FROM scheme_master
        QUALIFY row_number() OVER (PARTITION BY scheme_code ORDER BY first_seen DESC) = 1
        """
    )

    match_expr = " AND ".join(f"t.{c} IS NOT DISTINCT FROM c.{c}" for c in _MASTER_ATTR_COLS)

    to_insert = con.execute(
        f"""
        SELECT t.*
        FROM today_master t
        LEFT JOIN current_master c USING (scheme_code)
        WHERE c.scheme_code IS NULL OR NOT ({match_expr})
        """
    ).df()

    n_extended = con.execute(
        f"""
        UPDATE scheme_master
        SET last_seen = ?
        FROM today_master t, current_master c
        WHERE scheme_master.scheme_code = c.scheme_code
          AND scheme_master.first_seen = c.first_seen
          AND t.scheme_code = c.scheme_code
          AND ({match_expr})
        """,
        [run_date],
    ).fetchall()

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

    con.unregister("today_master")

    return {"new_or_changed": len(to_insert), "extended": len(today) - len(to_insert)}


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
