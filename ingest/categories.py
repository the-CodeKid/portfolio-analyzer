"""Map AMFI NAVAll.txt section headers to mftool's 39-code SEBI category taxonomy.

AMFI's dump mixes the current (post-2018) SEBI category names with legacy
pre-2018 names that still label old/matured/closed-ended schemes. Rather than
fuzzy-matching (which risks silently mis-scoring, e.g. lumping a legacy
"Ultra Short Term Fund" into today's "Ultra Short Duration Fund" bucket), this
is an explicit, auditable table. Headers with no entry here — legacy debt
groupings, bare "Gilt"/"Growth"/"Income"/"Money Market", close-ended,
interval-fund — resolve to category_code=None. category_raw is always kept so
nothing is lost, it's just excluded from within-category scoring until
someone decides how to fold it in.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

CONST_JSON_PATH = Path(__file__).resolve().parent.parent / "mftool" / "const.json"

# raw AMFI section-header text (content inside "Open Ended Schemes(...)" etc.,
# whitespace-normalized) -> mftool const.json category code
HEADER_TO_CODE: dict[str, int] = {
    # --- Equity, current naming ---
    "Equity Scheme - Contra Fund": 9,
    "Equity Scheme - Dividend Yield Fund": 10,
    "Equity Scheme - ELSS": 8,
    "Equity Scheme - Flexi Cap Fund": 3,
    "Equity Scheme - Focused Fund": 11,
    "Equity Scheme - Large & Mid Cap Fund": 2,
    "Equity Scheme - Large Cap Fund": 1,
    "Equity Scheme - Mid Cap Fund": 5,
    "Equity Scheme - Multi Cap Fund": 4,
    "Equity Scheme - Sectoral/ Thematic": 12,
    "Equity Scheme - Small Cap Fund": 6,
    "Equity Scheme - Value Fund": 7,
    # --- Equity, plural-header variant (same AMFI dump, inconsistent pluralization) ---
    "Equity Schemes - Contra Fund": 9,
    "Equity Schemes - ELSS- Tax Saver Fund": 8,
    "Equity Schemes - Flexi Cap Fund": 3,
    "Equity Schemes - Focused Fund": 11,
    "Equity Schemes - Large & Mid Cap Fund": 2,
    "Equity Schemes - Large Cap Fund": 1,
    "Equity Schemes - Mid Cap Fund": 5,
    "Equity Schemes - Multi Cap Fund": 4,
    "Equity Schemes - Sectoral Fund": 12,
    "Equity Schemes - Small Cap Fund": 6,
    "Equity Schemes - Thematic Fund": 12,
    "Equity Schemes - Value Fund": 7,
    # --- Debt, current naming ---
    "Debt Scheme - Banking and PSU Fund": 25,
    "Debt Scheme - Corporate Bond Fund": 23,
    "Debt Scheme - Credit Risk Fund": 24,
    "Debt Scheme - Dynamic Bond": 22,
    "Debt Scheme - Floater Fund": 26,
    "Debt Scheme - Gilt Fund": 28,
    "Debt Scheme - Gilt Fund with 10 year constant duration": 29,
    "Debt Scheme - Liquid Fund": 20,
    "Debt Scheme - Long Duration Fund": 13,
    "Debt Scheme - Low Duration Fund": 18,
    "Debt Scheme - Medium Duration Fund": 16,
    "Debt Scheme - Medium to Long Duration Fund": 14,
    "Debt Scheme - Money Market Fund": 17,
    "Debt Scheme - Overnight Fund": 21,
    "Debt Scheme - Short Duration Fund": 15,
    "Debt Scheme - Ultra Short Duration Fund": 19,
    # --- Hybrid, current naming ---
    # NB: const.json's hybrid group has no code for "Balanced Advantage /
    # Dynamic Asset Allocation" as a distinct sub-category (only 6 of SEBI's
    # 7 hybrid sub-types are present) — left unmapped rather than guessing.
    "Hybrid Scheme - Aggressive Hybrid Fund": 30,
    "Hybrid Scheme - Arbitrage Fund": 33,
    "Hybrid Scheme - Balanced Hybrid Fund": 40,
    "Hybrid Scheme - Conservative Hybrid Fund": 31,
    "Hybrid Scheme - Equity Savings": 32,
    "Hybrid Scheme - Multi Asset Allocation": 34,
    "Hybrid Schemes - Aggressive Hybrid Fund": 30,
    "Hybrid Schemes - Arbitrage Fund": 33,
    "Hybrid Schemes - Equity Savings Fund": 32,
    "Hybrid Schemes - Multi Asset Allocation Fund": 34,
    # --- Solution oriented ---
    "Solution Oriented Scheme - Children’s Fund": 36,
    "Solution Oriented Scheme - Retirement Fund": 37,
    # --- Other: ETFs, index funds, FoFs ---
    "Other Scheme - FoF Domestic": 39,
    "Other Scheme - FoF Overseas": 39,
    "Other Scheme - Gold ETF": 38,
    "Other Scheme - Index Funds": 38,
    "Other Scheme - Other  ETFs": 38,
    "Exchange Traded Funds (ETFs) - Debt ETF": 38,
    "Exchange Traded Funds (ETFs) - Equity ETF": 38,
    "Exchange Traded Funds (ETFs) - Gold ETF": 38,
    "Exchange Traded Funds (ETFs) - Other ETF": 38,
    "Fund of Funds Scheme (Domestic) - Fund of Funds Scheme (Domestic)": 39,
    "Overseas Fund of Funds - Fund of Funds investing overseas": 39,
    "Index Funds - Debt Funds": 38,
    "Index Funds - Equity Funds": 38,
}


def _normalize(header: str) -> str:
    return re.sub(r"\s+", " ", header).strip()


HEADER_TO_CODE = {_normalize(k): v for k, v in HEADER_TO_CODE.items()}


def load_taxonomy() -> dict[int, str]:
    """code -> category name, from mftool/const.json's four category groups."""
    raw = json.loads(CONST_JSON_PATH.read_text())
    codes: dict[int, str] = {}
    for key in (
        "open_ended_equity_category",
        "open_ended_debt_category",
        "open_ended_hybrid_category",
        "open_ended_solution_category",
        "open_ended_other_category",
    ):
        for code_str, name in raw[key].items():
            codes[int(code_str)] = name
    return codes


def map_category(raw_header: str) -> int | None:
    return HEADER_TO_CODE.get(_normalize(raw_header))
