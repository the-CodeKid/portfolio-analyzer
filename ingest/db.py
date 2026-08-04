"""DuckDB schema for the NAV store.

scheme_master is slowly-changing-dimension (SCD2): a row's attribute columns
(isin, name, amc, category_code, plan, option, ...) are never updated once
written. When a scheme's attributes change between runs, a new row is
inserted with a fresh first_seen; the prior row's last_seen stays frozen at
the last date it was observed with the old attributes. The only column that
is ever mutated on an existing row is last_seen, and only to extend it
forward when the same attributes are observed again — see
ingest.amfi.snapshot_scheme_master. This is what lets Module 4 reconstruct
the survivorship-free universe as of any past date T: schemes with
first_seen <= T <= last_seen, including ones that later died.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "portfolio_analyzer.duckdb"

SCHEMA = """
CREATE TABLE IF NOT EXISTS scheme_master (
    scheme_code         BIGINT NOT NULL,
    isin                VARCHAR,
    isin_reinvestment   VARCHAR,
    name                VARCHAR NOT NULL,
    amc                 VARCHAR,
    scheme_type         VARCHAR,   -- raw AMFI grouping, e.g. 'Open Ended Schemes'
    category_raw        VARCHAR,   -- raw AMFI section header, e.g. 'Debt Scheme - Banking and PSU Fund'
    category_code       INTEGER,   -- mftool const.json code; NULL if unmapped (legacy/closed-ended header)
    plan                VARCHAR,   -- 'Direct' | 'Regular' | NULL if undetermined
    option              VARCHAR,   -- 'Growth' | 'IDCW' | NULL if undetermined
    first_seen          DATE NOT NULL,
    last_seen           DATE NOT NULL,
    PRIMARY KEY (scheme_code, first_seen)
);

CREATE TABLE IF NOT EXISTS nav (
    scheme_code  BIGINT NOT NULL,
    date         DATE NOT NULL,
    nav          DOUBLE NOT NULL,
    PRIMARY KEY (scheme_code, date)
);

CREATE TABLE IF NOT EXISTS scheme_lineage (
    canonical_id  VARCHAR NOT NULL,
    scheme_code   BIGINT NOT NULL,
    valid_from    DATE NOT NULL,
    valid_to      DATE,
    reason        VARCHAR,
    PRIMARY KEY (scheme_code, valid_from)
);

CREATE TABLE IF NOT EXISTS holdings (
    canonical_id  VARCHAR NOT NULL,
    as_of_month   DATE NOT NULL,
    isin          VARCHAR NOT NULL,
    weight        DOUBLE NOT NULL,
    sector        VARCHAR,
    PRIMARY KEY (canonical_id, as_of_month, isin)
);

CREATE TABLE IF NOT EXISTS scheme_meta (
    canonical_id  VARCHAR NOT NULL,
    as_of_date    DATE NOT NULL,
    ter           DOUBLE,
    aum           DOUBLE,
    exit_load     VARCHAR,
    PRIMARY KEY (canonical_id, as_of_date)
);

CREATE TABLE IF NOT EXISTS benchmark (
    index_name  VARCHAR NOT NULL,
    date        DATE NOT NULL,
    tri_value   DOUBLE NOT NULL,
    PRIMARY KEY (index_name, date)
);
"""


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> duckdb.DuckDBPyConnection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    con.execute(SCHEMA)
    return con
