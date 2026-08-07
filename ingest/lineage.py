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


ISIN_COLLISION_CSV_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "review" / "isin_collisions.csv"
)


def stitch_isin_lineage(con, write_csv: bool = True) -> pd.DataFrame:
    """Rebuild scheme_lineage from the current scheme_master via ISIN union-find.

    Two scheme_codes are only stitched when ALL of these hold:

    1. They share a validated growth/payout ISIN. isin_reinvestment is
       deliberately not used -- it is the noisiest column in the dump and
       adds no lineage signal a real ISIN doesn't already carry.
    2. Their plan and option agree. Merging a Direct plan into a Regular one
       would erase the TER difference that is the whole point of the split.
    3. Their lifespans do NOT overlap. This is the load-bearing guard: a
       genuine scheme_code reissue means the old code went dead *before* the
       new one appeared. Two codes alive simultaneously under one ISIN is an
       AMFI data error (it ships duplicate ISINs across distinct close-ended
       series), not a lineage relationship.

    Lifespan comes from actual NAV history where available, falling back to
    the scheme_master snapshot span. Rejected collisions are written to
    ISIN_COLLISION_CSV_PATH for manual review rather than silently dropped.

    Returns the new scheme_lineage DataFrame (also written to the DB).
    """
    master = con.execute(
        """
        SELECT m.scheme_code,
               any_value(m.isin)   AS isin,
               any_value(m.plan)   AS plan,
               any_value(m.option) AS option,
               min(m.first_seen)   AS first_seen,
               max(m.last_seen)    AS last_seen,
               (SELECT min(date) FROM nav n WHERE n.scheme_code = m.scheme_code) AS nav_start,
               (SELECT max(date) FROM nav n WHERE n.scheme_code = m.scheme_code) AS nav_end,
               (SELECT count(DISTINCT date) FROM nav n WHERE n.scheme_code = m.scheme_code)
                   AS nav_points
        FROM scheme_master m
        GROUP BY m.scheme_code
        """
    ).df()

    if master.empty:
        return pd.DataFrame(
            columns=["canonical_id", "scheme_code", "valid_from", "valid_to", "reason"]
        )

    master["life_start"] = master["nav_start"].fillna(master["first_seen"])
    master["life_end"] = master["nav_end"].fillna(master["last_seen"])
    # A lifespan only proves anything if it actually spans something. Two
    # dead funds each known from a single frozen NAV date are two *points* --
    # they trivially "don't overlap" no matter how unrelated they are, which
    # makes the overlap guard vacuous. Require real duration from either NAV
    # history or our own accumulated snapshots before trusting it.
    master["has_span"] = (master["nav_points"] >= 2) | (master["first_seen"] < master["last_seen"])
    info = master.set_index("scheme_code").to_dict("index")

    def overlaps(a: int, b: int) -> bool:
        ia, ib = info[a], info[b]
        return ia["life_start"] <= ib["life_end"] and ib["life_start"] <= ia["life_end"]

    def compatible(a: int, b: int) -> str | None:
        """None if the pair may be stitched, else the rejection reason."""
        ia, ib = info[a], info[b]
        if ia["plan"] != ib["plan"]:
            return "plan_mismatch"
        if ia["option"] != ib["option"]:
            return "option_mismatch"
        if not (ia["has_span"] and ib["has_span"]):
            return "insufficient_history"
        if overlaps(a, b):
            return "lifespans_overlap"
        return None

    dsu = _DSU()
    for code in master["scheme_code"]:
        dsu.find(f"code:{code}")  # ensure every scheme_code is a node, even if unlinked

    isin_to_codes: dict[str, set[int]] = {}
    for code, row in info.items():
        if row["isin"]:
            isin_to_codes.setdefault(row["isin"], set()).add(code)

    rejections: list[dict] = []
    for isin_val, code_set in isin_to_codes.items():
        codes = sorted(code_set)
        if len(codes) < 2:
            continue
        for i, a in enumerate(codes):
            for b in codes[i + 1 :]:
                reason = compatible(a, b)
                if reason is None:
                    dsu.union(f"code:{a}", f"code:{b}")
                else:
                    rejections.append(
                        {
                            "isin": isin_val,
                            "scheme_code_a": a,
                            "name_a": info[a].get("name"),
                            "scheme_code_b": b,
                            "name_b": info[b].get("name"),
                            "rejected_because": reason,
                        }
                    )

    groups: dict[str, list[int]] = {}
    for code in master["scheme_code"]:
        groups.setdefault(dsu.find(f"code:{code}"), []).append(code)

    # Union-find is transitive but "non-overlapping" is not: A-B and B-C can
    # each be clean while A and C overlap. Re-verify each group pairwise and
    # dissolve any that fails, rather than shipping a bad merge.
    safe_groups: list[list[int]] = []
    for members in groups.values():
        if len(members) > 1 and any(
            compatible(a, b) is not None
            for i, a in enumerate(sorted(members))
            for b in sorted(members)[i + 1 :]
        ):
            rejections.append(
                {
                    "isin": info[members[0]]["isin"],
                    "scheme_code_a": min(members),
                    "name_a": None,
                    "scheme_code_b": max(members),
                    "name_b": None,
                    "rejected_because": "group_not_pairwise_compatible",
                }
            )
            safe_groups.extend([[m] for m in members])
        else:
            safe_groups.append(members)

    if write_csv:
        ISIN_COLLISION_CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(
            rejections,
            columns=["isin", "scheme_code_a", "name_a", "scheme_code_b", "name_b", "rejected_because"],
        ).to_csv(ISIN_COLLISION_CSV_PATH, index=False)

    rows = []
    for members in safe_groups:
        canonical_id = f"CID{min(members):06d}"
        reason = "isin_match" if len(members) > 1 else "single"
        for code in members:
            rows.append(
                {
                    "canonical_id": canonical_id,
                    "scheme_code": code,
                    "valid_from": info[code]["first_seen"],
                    "valid_to": info[code]["last_seen"],
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
