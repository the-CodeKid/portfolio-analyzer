from ingest.categories import HEADER_TO_CODE, load_taxonomy, map_category


def test_known_current_headers_map_correctly():
    assert map_category("Equity Scheme - Large Cap Fund") == 1
    assert map_category("Equity Scheme - Small Cap Fund") == 6
    assert map_category("Debt Scheme - Banking and PSU Fund") == 25
    assert map_category("Debt Scheme - Liquid Fund") == 20
    assert map_category("Hybrid Scheme - Arbitrage Fund") == 33
    assert map_category("Exchange Traded Funds (ETFs) - Gold ETF") == 38
    assert map_category("Fund of Funds Scheme (Domestic) - Fund of Funds Scheme (Domestic)") == 39


def test_legacy_and_bare_headers_are_unmapped_not_guessed():
    assert map_category("Income") is None
    assert map_category("Growth") is None
    assert map_category("Income/Debt Oriented Schemes - Liquid Fund") is None
    assert map_category("Hybrid Scheme - Dynamic Asset Allocation or Balanced Advantage") is None


def test_normalization_handles_whitespace_variants():
    # AMFI's dump has a double-space typo in this header; input can be messy too
    assert map_category("Other Scheme - Other  ETFs") == 38
    assert map_category("  Equity Scheme - Large Cap Fund  ") == 1
    assert map_category("Equity Scheme -    Large Cap Fund") == 1


def test_unknown_header_returns_none():
    assert map_category("Not A Real Category") is None


def test_every_mapped_code_exists_in_taxonomy():
    taxonomy = load_taxonomy()
    assert len(taxonomy) == 39
    for header, code in HEADER_TO_CODE.items():
        assert code in taxonomy, f"{header!r} maps to unknown code {code}"
