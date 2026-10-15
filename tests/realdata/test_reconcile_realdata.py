"""Opt-in real-data reconciliation against the official CPSC Query Builder figures.

Never run by default (pyproject addopts excludes the realdata marker). Run explicitly with:
    uv run pytest -m realdata tests/realdata/test_reconcile_realdata.py
"""

from __future__ import annotations

import json

import polars as pl
import pytest

from wii.data.parse import parse_year_file
from wii.data.sync import main
from wii.utils.paths import repo_root

pytestmark = pytest.mark.realdata

_EXPECTED_REJOINED_COUNTS = {2010: 1, 2013: 1, 2017: 1, 2018: 1, 2019: 1, 2021: 2, 2023: 3}
_EXPECTED_CP1252_YEARS = {2008, 2009, 2017}
_ALL_YEARS = range(2006, 2026)

# product_code -> (raw N, weighted estimate) per the official 2024 CPSC Query Builder.
_QUERY_BUILDER_2024 = {
    841: (560, 33052),
    832: (172, 9539),
    895: (276, 15914),
    882: (154, 9057),
    899: (445, 27932),
}


def test_2024_reconciles_with_query_builder(tmp_path, realdata_raw_dir):
    realdata_raw_dir.require(2024)
    data_root = tmp_path / "data_root"

    exit_code = main(
        [
            "--years",
            "2024",
            "--offline",
            "--raw-dir",
            str(realdata_raw_dir.dir),
            "--data-root",
            str(data_root),
        ]
    )
    assert exit_code == 0

    fact_cases = pl.read_parquet(data_root / "processed" / "fact_cases.parquet")
    bridge = pl.read_parquet(data_root / "processed" / "bridge_case_products.parquet")

    assert fact_cases.height == 2960
    assert fact_cases["case_id"].n_unique() == fact_cases.height

    lowered = {c.lower() for c in fact_cases.columns}
    assert not (lowered & {"race", "other_race", "hispanic"})

    for product_code, (expected_n, expected_estimate) in _QUERY_BUILDER_2024.items():
        case_ids = bridge.filter(pl.col("product_code") == product_code)["case_id"].unique()
        n = case_ids.len()
        weight_sum = fact_cases.filter(pl.col("case_id").is_in(case_ids.to_list()))["weight"].sum()
        assert n == expected_n, f"product {product_code}: N {n} != expected {expected_n}"
        assert round(weight_sum) == expected_estimate, (
            f"product {product_code}: weight sum {weight_sum} != expected {expected_estimate}"
        )


def test_all_years_parse_clean(realdata_raw_dir):
    cp1252_years = set()
    for year in _ALL_YEARS:
        path = realdata_raw_dir.require(year)
        parsed = parse_year_file(path, year)

        assert all(len(row) == 25 for row in parsed.rows), (
            f"year {year}: not all rows have 25 fields"
        )
        case_ids = [row[0] for row in parsed.rows]
        assert len(case_ids) == len(set(case_ids)), f"year {year}: duplicate case_id within year"

        expected_rejoined = _EXPECTED_REJOINED_COUNTS.get(year, 0)
        assert parsed.rejoined_record_count == expected_rejoined, (
            f"year {year}: rejoined_record_count {parsed.rejoined_record_count} "
            f"!= expected {expected_rejoined}"
        )

        if year == 2010:
            assert parsed.blank_line_count == 6
        if year == 2013:
            assert parsed.blank_line_count == 21

        if parsed.cp1252_fallback:
            cp1252_years.add(year)

    assert cp1252_years == _EXPECTED_CP1252_YEARS


def test_design_and_metadata_realdata(tmp_path, realdata_raw_dir):
    for year in (2018, 2019, 2024):
        realdata_raw_dir.require(year)
    data_root = tmp_path / "data_root"

    exit_code = main(
        [
            "--years",
            "2018,2019,2024",
            "--offline",
            "--raw-dir",
            str(realdata_raw_dir.dir),
            "--data-root",
            str(data_root),
        ]
    )
    assert exit_code == 0

    design_psu = pl.read_parquet(data_root / "processed" / "design_psu.parquet")
    design_2024 = design_psu.filter(pl.col("year") == 2024)
    assert design_2024.height == 77
    assert set(design_2024["stratum"].unique().to_list()) == {"C", "L", "M", "S", "V"}

    metadata = json.loads((data_root / "metadata" / "data_metadata.json").read_text())
    assert metadata["years"]["2024"]["raw_row_count"] == 361672
    assert metadata["years"]["2018"]["fill_rate"]["body_part_2"] == 0.0
    assert metadata["years"]["2019"]["fill_rate"]["body_part_2"] > 0.05
    assert "product_taxonomy.yaml" in metadata["config_sha256"]


def test_body_part_slice_2024(tmp_path, realdata_raw_dir):
    realdata_raw_dir.require(2024)
    data_root = tmp_path / "data_root"

    exit_code = main(
        [
            "--years",
            "2024",
            "--offline",
            "--raw-dir",
            str(realdata_raw_dir.dir),
            "--data-root",
            str(data_root),
        ]
    )
    assert exit_code == 0

    report = json.loads((data_root / "metadata" / "qa_report.json").read_text())
    result = next(r for r in report["results"] if r["check_id"] == "known_body_part_codes")
    assert result["passed"] is True

    fact_cases = pl.read_parquet(data_root / "processed" / "fact_cases.parquet")
    bridge_body_parts = pl.read_parquet(data_root / "processed" / "bridge_case_body_parts.parquet")
    assert bridge_body_parts.height >= fact_cases.height

    dim_body_part = pl.read_parquet(data_root / "processed" / "dim_body_part.parquet")
    assert dim_body_part.height == 26
    assert dim_body_part["region_id"].null_count() == 0


def test_all_years_case_model_realdata(tmp_path, realdata_raw_dir):
    """2006-2025 full run: 63,425 in-scope cases, zero structural failures, correct era split,
    no invalid ages, is_adult has both values, every known_*_codes check passed. Expected to
    take about 1-2 minutes cold."""
    for year in _ALL_YEARS:
        realdata_raw_dir.require(year)
    data_root = tmp_path / "data_root"

    exit_code = main(
        [
            "--years",
            "2006:2025",
            "--offline",
            "--raw-dir",
            str(realdata_raw_dir.dir),
            "--data-root",
            str(data_root),
        ]
    )
    assert exit_code == 0

    report = json.loads((data_root / "metadata" / "qa_report.json").read_text())
    assert report["aborted"] is False
    assert report["structural_failure_count"] == 0
    for check_id in (
        "known_body_part_codes",
        "known_diagnosis_codes",
        "known_disposition_codes",
        "known_location_codes",
        "known_sex_codes",
    ):
        result = next(r for r in report["results"] if r["check_id"] == check_id)
        assert result["passed"] is True, f"{check_id}: {result['detail']}"

    fact_cases = pl.read_parquet(data_root / "processed" / "fact_cases.parquet")
    assert fact_cases.height == 63425
    assert fact_cases["year"].n_unique() == 20

    era_by_year = fact_cases.select(["year", "schema_era"]).unique().sort("year")
    for row in era_by_year.iter_rows(named=True):
        expected = "pre_2019" if row["year"] <= 2018 else "post_2019"
        assert row["schema_era"] == expected, f"year {row['year']}: schema_era {row['schema_era']}"

    assert not (fact_cases["age_unit"] == "invalid").any()
    is_adult_values = set(fact_cases["is_adult"].unique().to_list())
    assert is_adult_values == {True, False}


# product_code -> (raw N, weighted estimate) per the official CPSC Query Builder, by year.
_QUERY_BUILDER_BY_YEAR: dict[int, dict[int, tuple[int, int]]] = {
    2024: _QUERY_BUILDER_2024,
    2019: {841: (668, 32445), 899: (348, 20279)},
    2014: {841: (700, 33475), 832: (279, 12124), 862: (38, 1514)},
    2009: {841: (783, 35624), 870: (110, 5764), 897: (111, 6517)},
}


def test_committed_outputs_reconcile():
    """Task 3 of 01-03: the committed release build in data/processed/ + data/metadata/
    reproduces the official CPSC Query Builder figures for 2009/2014/2019/2024 and passes every
    structural QA gate. Reads the repository's own committed outputs directly -- never tmp_path,
    never re-runs the pipeline."""
    root = repo_root()
    processed = root / "data" / "processed"
    metadata = root / "data" / "metadata"

    data_metadata = json.loads((metadata / "data_metadata.json").read_text())
    years_processed = sorted(int(y) for y in data_metadata["years_processed"])
    assert years_processed == list(range(years_processed[0], years_processed[-1] + 1))
    assert len(years_processed) == 20
    max_year = years_processed[-1]

    fact_cases = pl.read_parquet(processed / "fact_cases.parquet")
    bridge = pl.read_parquet(processed / "bridge_case_products.parquet")
    if max_year == 2025:
        assert fact_cases.height == 63425

    # bridge_case_products carries no year column -- join fact_cases to scope by (year,
    # product_code) rather than product_code alone.
    bridge_with_year = bridge.join(
        fact_cases.select(["case_id", "year"]), on="case_id", how="inner"
    )
    for year, expectations in _QUERY_BUILDER_BY_YEAR.items():
        if year not in years_processed:
            continue
        year_bridge = bridge_with_year.filter(pl.col("year") == year)
        for product_code, (expected_n, expected_estimate) in expectations.items():
            case_ids = year_bridge.filter(pl.col("product_code") == product_code)[
                "case_id"
            ].unique()
            n = case_ids.len()
            weight_sum = fact_cases.filter(pl.col("case_id").is_in(case_ids.to_list()))[
                "weight"
            ].sum()
            assert n == expected_n, f"{year} product {product_code}: N {n} != expected {expected_n}"
            assert round(weight_sum) == expected_estimate, (
                f"{year} product {product_code}: weight sum {weight_sum} "
                f"!= expected {expected_estimate}"
            )

    design_psu = pl.read_parquet(processed / "design_psu.parquet")
    design_2024 = design_psu.filter(pl.col("year") == 2024)
    assert design_2024.height == 77

    qa_report = json.loads((metadata / "qa_report.json").read_text())
    assert qa_report["aborted"] is False
    assert qa_report["structural_failure_count"] == 0

    rejoined = next(r for r in qa_report["results"] if r["check_id"] == "rejoined_records")
    assert rejoined["count"] == 10

    cp1252 = next(r for r in qa_report["results"] if r["check_id"] == "cp1252_fallback_years")
    assert {int(y) for y in cp1252["examples"]} == _EXPECTED_CP1252_YEARS
