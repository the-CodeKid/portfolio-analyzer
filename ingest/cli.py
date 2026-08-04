"""CLI orchestration for Module 1 ingestion. Idempotent: re-running any
command (or `all`) is always safe."""

from __future__ import annotations

import click

from ingest import amfi, lineage, mfapi
from ingest.db import DEFAULT_DB_PATH, connect


@click.group()
@click.option("--db-path", default=str(DEFAULT_DB_PATH), show_default=True)
@click.pass_context
def main(ctx: click.Context, db_path: str):
    ctx.ensure_object(dict)
    ctx.obj["db_path"] = db_path


@main.command()
@click.pass_context
def snapshot(ctx: click.Context):
    """Fetch AMFI NAVAll.txt, snapshot scheme_master, upsert today's NAV."""
    con = connect(ctx.obj["db_path"])
    try:
        stats = amfi.run(con)
        click.echo(stats)
    finally:
        con.close()


@main.command()
@click.option("--all-schemes", is_flag=True, help="Backfill every scheme, not just current Direct+Growth.")
@click.option("--force", is_flag=True, help="Ignore cache, re-fetch from api.mfapi.in.")
@click.option("--min-interval", default=0.34, show_default=True, help="Seconds between live API calls.")
@click.pass_context
def backfill(ctx: click.Context, all_schemes: bool, force: bool, min_interval: float):
    """Backfill full NAV history from api.mfapi.in for currently-listed
    schemes (Direct+Growth by default, per REQUIREMENTS.md #3)."""
    con = connect(ctx.obj["db_path"])
    try:
        if all_schemes:
            codes = con.execute("SELECT DISTINCT scheme_code FROM scheme_master").df()["scheme_code"].tolist()
        else:
            codes = con.execute(
                """
                SELECT DISTINCT scheme_code FROM scheme_master
                WHERE plan = 'Direct' AND option = 'Growth'
                  AND last_seen = (SELECT max(last_seen) FROM scheme_master)
                """
            ).df()["scheme_code"].tolist()
        click.echo(f"backfilling {len(codes)} scheme(s)...")
        stats = mfapi.backfill(con, codes, min_interval=min_interval, force=force)
        click.echo({k: v for k, v in stats.items() if k != "failures"})
        if stats["failures"]:
            click.echo(f"{len(stats['failures'])} failures (first 5): {stats['failures'][:5]}")
    finally:
        con.close()


@main.command()
@click.option("--gap-days", default=180, show_default=True)
@click.option("--min-similarity", default=0.4, show_default=True)
@click.pass_context
def stitch_lineage(ctx: click.Context, gap_days: int, min_similarity: float):
    """Rebuild scheme_lineage (ISIN-confirmed) and write candidate mergers
    (name/category heuristic, needs manual review) to data/review/."""
    con = connect(ctx.obj["db_path"])
    try:
        lin = lineage.stitch_isin_lineage(con)
        click.echo(f"scheme_lineage: {len(lin)} rows, {lin['canonical_id'].nunique()} canonical funds")
        candidates = lineage.find_candidate_mergers(con, gap_days=gap_days, min_similarity=min_similarity)
        click.echo(f"{len(candidates)} candidate merger(s) written to {lineage.REVIEW_CSV_PATH}")
    finally:
        con.close()


@main.command()
@click.pass_context
def all(ctx: click.Context):
    """Run snapshot -> stitch-lineage -> backfill, in that order."""
    ctx.invoke(snapshot)
    ctx.invoke(stitch_lineage)
    ctx.invoke(backfill)


if __name__ == "__main__":
    main()
