def test_schema_creates_all_tables(db):
    tables = {
        row[0]
        for row in db.execute("SELECT table_name FROM information_schema.tables").fetchall()
    }
    assert tables == {
        "scheme_master",
        "nav",
        "scheme_lineage",
        "holdings",
        "scheme_meta",
        "benchmark",
    }


def test_connect_is_idempotent(tmp_path):
    from ingest.db import connect

    path = tmp_path / "test.duckdb"
    con1 = connect(path)
    con1.close()
    con2 = connect(path)  # re-running CREATE TABLE IF NOT EXISTS shouldn't error
    con2.close()
