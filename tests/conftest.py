import datetime
from pathlib import Path

import pytest

from ingest.db import connect

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "navall_sample.txt"


@pytest.fixture
def navall_text() -> str:
    return FIXTURE_PATH.read_text()


@pytest.fixture
def db(tmp_path):
    con = connect(tmp_path / "test.duckdb")
    yield con
    con.close()


@pytest.fixture
def today():
    return datetime.date(2026, 8, 3)
