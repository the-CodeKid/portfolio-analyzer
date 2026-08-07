import datetime
import json
from unittest.mock import patch

import pytest

from ingest import benchmark

_RECORDS = [
    {"Index Name": "NIFTY 500", "Date": "29 Jun 2026", "TotalReturnsIndex": "36883.37", "NTR_Value": "33938.48"},
    {"Index Name": "Nifty 500", "Date": "26 Jun 2026", "TotalReturnsIndex": "36000.00", "NTR_Value": "-"},
    {"Index Name": "Nifty 500", "Date": "01 Jan 1995", "TotalReturnsIndex": "1000.00", "NTR_Value": "-"},
]


def test_records_to_df_normalizes_inconsistent_index_casing():
    df = benchmark.records_to_df("NIFTY 500", _RECORDS)
    # upstream mixes "NIFTY 500" / "Nifty 500"; splitting the series would
    # make every downstream join half-miss
    assert set(df["index_name"]) == {"NIFTY 500"}
    assert len(df) == 3
    assert df["date"].tolist() == [
        datetime.date(1995, 1, 1), datetime.date(2026, 6, 26), datetime.date(2026, 6, 29)
    ]
    assert df["tri_value"].tolist() == [1000.0, 36000.0, 36883.37]


def test_records_to_df_drops_missing_tri_rows():
    records = _RECORDS + [
        {"Index Name": "NIFTY 500", "Date": "30 Jun 2026", "TotalReturnsIndex": "-", "NTR_Value": "-"}
    ]
    df = benchmark.records_to_df("NIFTY 500", records)
    assert len(df) == 3
    assert datetime.date(2026, 6, 30) not in df["date"].tolist()


def test_fetch_index_unwraps_nested_json_string_payload():
    class FakeResp:
        status_code = 200

        def json(self):
            return {"d": json.dumps(_RECORDS)}

    with patch("ingest.benchmark.requests.get", return_value=FakeResp()):
        assert benchmark.fetch_index("NIFTY 500") == _RECORDS


def test_fetch_index_raises_on_empty_payload():
    class FakeResp:
        status_code = 200

        def json(self):
            return {"d": "[]"}

    with patch("ingest.benchmark.requests.get", return_value=FakeResp()):
        with pytest.raises(benchmark.BenchmarkFetchError):
            benchmark.fetch_index("NIFTY 500")


def test_ingest_is_idempotent_and_reports_staleness(db, tmp_path):
    with patch("ingest.benchmark.fetch_index", return_value=_RECORDS) as mock_fetch:
        s1 = benchmark.ingest(
            db, ["NIFTY 500"], cache_dir=tmp_path / "c", as_of=datetime.date(2026, 7, 9)
        )
        s2 = benchmark.ingest(
            db, ["NIFTY 500"], cache_dir=tmp_path / "c", as_of=datetime.date(2026, 7, 9)
        )

    assert mock_fetch.call_count == 1  # second run served from cache
    assert s1["fetched"] == 1 and s2["cached"] == 1
    assert s1["staleness_days"]["NIFTY 500"] == 10  # 29 Jun -> 9 Jul

    n = db.execute("SELECT count(*) FROM benchmark").fetchone()[0]
    assert n == 3  # re-run must not duplicate


def test_every_mapped_category_benchmark_is_a_real_index():
    # guards against a typo silently yielding a category with no benchmark
    assert benchmark.BASELINE_EQUITY_INDEX in set(benchmark.CATEGORY_BENCHMARK.values())
    for code, index_name in benchmark.CATEGORY_BENCHMARK.items():
        assert isinstance(code, int)
        assert index_name.startswith("NIFTY")


def test_debt_categories_are_deliberately_unmapped():
    # no NSE TRI exists for debt; a wrong benchmark is worse than none
    for debt_code in (20, 23, 25, 28):  # Liquid, Corp Bond, Banking&PSU, Gilt
        assert debt_code not in benchmark.CATEGORY_BENCHMARK
