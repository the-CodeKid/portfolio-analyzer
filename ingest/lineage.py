"""Scheme lineage: stitch scheme_codes into a canonical_id per underlying fund.

Two distinct mechanisms, deliberately not conflated:

1. ISIN-based stitching (stitch_isin_lineage) — mechanical and confident.
   When AMFI reissues a scheme_code for the same ISIN (e.g. a scheme rename
   that doesn't touch the underlying instrument), the old and new codes
   share an ISIN and get linked automatically. Every scheme_code gets a
   canonical_id this way, including singletons with no link at all — that's
   what lets downstream joins (holdings, scheme_meta) always resolve one.
   Rebuilt from scratch and REPLACEs scheme_lineage each run: it's a derived
   view over scheme_master, not an append-only fact log.

2. Candidate merger detection (find_candidate_mergers) — heuristic, and
   deliberately NOT written into scheme_lineage. A true AMC merger often
   issues a brand-new ISIN for the surviving scheme, so ISIN matching can't
   see it; the only signal is "fund A died, fund B appeared shortly after in
   the same category with a similar name." That is exactly the kind of
   pattern-match REQUIREMENTS.md says to flag for manual review rather than
   guess into the data — false positives here would silently splice two
   unrelated funds' return series together. Results go to a CSV for a human
   to confirm; confirmed ones should be added to scheme_lineage by hand
   (reason='confirmed_merger') in a follow-up, not auto-merged.
"""

from __future__ import annotations

import difflib
from pathlib import Path

import pandas as pd

REVIEW_CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "review" / "candidate_mergers.csv"


class _DSU:
    def __init__(self):
        self.parent: dict = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def stitch_isin_lineage(con) -> pd.DataFrame:
    """Rebuild scheme_lineage from the current scheme_master via ISIN union-find.

    Returns the new scheme_lineage DataFrame (also written to the DB).
    """
    master = con.execute(
        """
        SELECT scheme_code, isin, isin_reinvestment,
               min(first_seen) AS first_seen, max(last_seen) AS last_seen
        FROM scheme_master
        GROUP BY scheme_code, isin, isin_reinvestment
        """
    ).df()

    dsu = _DSU()
    for code in master["scheme_code"].unique():
        dsu.find(f"code:{code}")  # ensure every scheme_code is a node, even if unlinked

    isin_to_codes: dict[str, set[int]] = {}
    for _, row in master.iterrows():
        for isin_val in (row["isin"], row["isin_reinvestment"]):
            if isin_val:
                isin_to_codes.setdefault(isin_val, set()).add(row["scheme_code"])

    for codes in isin_to_codes.values():
        codes = list(codes)
        for other in codes[1:]:
            dsu.union(f"code:{codes[0]}", f"code:{other}")

    # canonical_id = smallest scheme_code in the connected group, stable in
    # practice since AMFI assigns codes roughly chronologically and later
    # discoveries of a link almost always involve a *larger* new code — but
    # see module docstring: a group merge triggered by newly-observed data
    # can, in principle, still shift an existing canonical_id.
    groups: dict[str, list[int]] = {}
    for code in master["scheme_code"].unique():
        root = dsu.find(f"code:{code}")
        groups.setdefault(root, []).append(code)

    rows = []
    per_scheme_span = (
        master.groupby("scheme_code")
        .agg(valid_from=("first_seen", "min"), valid_to=("last_seen", "max"))
        .to_dict("index")
    )
    for members in groups.values():
        canonical_id = f"CID{min(members):06d}"
        reason = "isin_match" if len(members) > 1 else "single"
        for code in members:
            span = per_scheme_span[code]
            rows.append(
                {
                    "canonical_id": canonical_id,
                    "scheme_code": code,
                    "valid_from": span["valid_from"],
                    "valid_to": span["valid_to"],
                    "reason": reason,
                }
            )

    lineage_df = pd.DataFrame(rows)
    con.execute("DELETE FROM scheme_lineage")
    con.register("new_lineage", lineage_df)
    con.execute(
        """
        INSERT INTO scheme_lineage (canonical_id, scheme_code, valid_from, valid_to, reason)
        SELECT canonical_id, scheme_code, valid_from, valid_to, reason FROM new_lineage
        """
    )
    con.unregister("new_lineage")
    return lineage_df


def find_candidate_mergers(
    con,
    gap_days: int = 180,
    min_similarity: float = 0.4,
    write_csv: bool = True,
) -> pd.DataFrame:
    """Surface (died fund, plausibly-successor fund) pairs for manual review.

    A "died" fund is a scheme_code whose last_seen falls more than gap_days
    before the latest observed last_seen anywhere in scheme_master (i.e. it
    stopped appearing in the AMFI dump while the dataset as a whole kept
    going — as opposed to just being the most recent run). Candidates are
    schemes that first appeared within gap_days after that, in the same
    category, same plan/option, with a similar name. Nothing here is
    auto-applied to scheme_lineage.
    """
    latest = con.execute(
        "SELECT max(last_seen) FROM scheme_master"
    ).fetchone()[0]

    current = con.execute(
        """
        SELECT * FROM scheme_master
        QUALIFY row_number() OVER (PARTITION BY scheme_code ORDER BY first_seen DESC) = 1
        """
    ).df()

    cutoff = pd.Timestamp(latest) - pd.Timedelta(days=gap_days)
    died = current[
        (pd.to_datetime(current["last_seen"]) < cutoff)
        & current["category_code"].notna()
        & current["plan"].notna()
        & current["option"].notna()
    ]

    candidates = []
    for _, d in died.iterrows():
        window_start = pd.to_datetime(d["last_seen"])
        window_end = window_start + pd.Timedelta(days=gap_days)
        successors = current[
            (current["scheme_code"] != d["scheme_code"])
            & (current["category_code"] == d["category_code"])
            & (current["plan"] == d["plan"])
            & (current["option"] == d["option"])
            & (pd.to_datetime(current["first_seen"]) >= window_start)
            & (pd.to_datetime(current["first_seen"]) <= window_end)
        ]
        for _, s in successors.iterrows():
            score = difflib.SequenceMatcher(None, d["name"], s["name"]).ratio()
            if score >= min_similarity:
                candidates.append(
                    {
                        "died_scheme_code": d["scheme_code"],
                        "died_name": d["name"],
                        "died_amc": d["amc"],
                        "died_last_seen": d["last_seen"],
                        "candidate_scheme_code": s["scheme_code"],
                        "candidate_name": s["name"],
                        "candidate_amc": s["amc"],
                        "candidate_first_seen": s["first_seen"],
                        "name_similarity": round(score, 3),
                    }
                )

    result = pd.DataFrame(candidates).sort_values(
        "name_similarity", ascending=False
    ) if candidates else pd.DataFrame(columns=[
        "died_scheme_code", "died_name", "died_amc", "died_last_seen",
        "candidate_scheme_code", "candidate_name", "candidate_amc",
        "candidate_first_seen", "name_similarity",
    ])

    if write_csv:
        REVIEW_CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(REVIEW_CSV_PATH, index=False)

    return result
