"""D-11 aggregates: build_aggregates() must equal the query path (estimate_by/estimate_total/
severity_profile) it is built from, on a synthetic 6-year fixture designed so `all_years` (2019-
2024) and `latest_5` (2020-2024) genuinely differ (2019 is excluded from latest_5)."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from wii.metrics.aggregates import _windows, build_aggregates
from wii.metrics.db import connect
from wii.metrics.estimator import estimate_by, estimate_total
from wii.metrics.filters import CaseFilter
from wii.metrics.severity import severity_profile

LATEST_YEAR = 2024
YEARS = list(range(2019, 2025))  # 2019..2024 inclusive
_TOL = 1e-9


@pytest.fixture
def aggregates_processed_dir(tmp_path) -> Path:
    d = tmp_path / "processed"
    d.mkdir()

    case_ids = [f"c{i}" for i in range(12)]
    years = [YEARS[i // 2] for i in range(12)]
    psus = [1 if i % 2 == 0 else 2 for i in range(12)]
    product_codes = [841 if i % 2 == 0 else 832 for i in range(12)]
    tool_ids = ["table_saw" if i % 2 == 0 else "circular_saw" for i in range(12)]
    body_part_codes = [82 if i % 2 == 0 else 77 for i in range(12)]
    diagnosis_codes = [59 if i % 2 == 0 else 56 for i in range(12)]
    disposition_codes = [1 if i % 2 == 0 else 2 for i in range(12)]
    mechanisms = ["direct_cutting_contact" if i % 2 == 0 else "flying_debris_fb" for i in range(12)]

    design_psu_rows = []
    for year in YEARS:
        for psu in (1, 2):
            design_psu_rows.append({"year": year, "stratum": "S", "psu": psu})
    pl.DataFrame(design_psu_rows).write_parquet(d / "design_psu.parquet")

    fact_cases = pl.DataFrame(
        {
            "case_id": case_ids,
            "year": years,
            "stratum": ["S"] * 12,
            "psu": psus,
            "weight": [100.0] * 12,
            "is_adult": [True] * 12,
            "disposition_code": disposition_codes,
            "primary_body_part_code": body_part_codes,
            "primary_diagnosis_code": diagnosis_codes,
        }
    )
    fact_cases.write_parquet(d / "fact_cases.parquet")

    bridge_case_products = pl.DataFrame(
        {
            "case_id": case_ids,
            "product_code": product_codes,
            "seq": [1] * 12,
            "is_primary": [True] * 12,
            "tool_id": tool_ids,
            "family_id": ["saws"] * 12,
            "in_scope": [True] * 12,
            "is_adjacent": [False] * 12,
            "narrative_refined_tool": [None] * 12,
            "final_tool_id": tool_ids,
            "classification_confidence": [None] * 12,
        },
        schema_overrides={"classification_confidence": pl.Float32},
    )
    bridge_case_products.write_parquet(d / "bridge_case_products.parquet")

    bridge_case_body_parts = pl.DataFrame(
        {
            "case_id": case_ids,
            "year": years,
            "body_part_code": body_part_codes,
            "seq": [1] * 12,
            "is_primary": [True] * 12,
        }
    )
    bridge_case_body_parts.write_parquet(d / "bridge_case_body_parts.parquet")

    bridge_case_diagnoses = pl.DataFrame(
        {
            "case_id": case_ids,
            "year": years,
            "diagnosis_code": diagnosis_codes,
            "other_diagnosis_text": [None] * 12,
            "seq": [1] * 12,
            "is_primary": [True] * 12,
        }
    )
    bridge_case_diagnoses.write_parquet(d / "bridge_case_diagnoses.parquet")

    dim_tool = pl.DataFrame(
        {
            "tool_id": ["table_saw", "circular_saw"],
            "label": ["Table Saw", "Circular Saw"],
            "family_id": ["saws", "saws"],
            "family_sort_order": [1, 1],
        }
    )
    dim_tool.write_parquet(d / "dim_tool.parquet")

    dim_body_part = pl.DataFrame({"code": [82, 77], "region_id": ["hand", "head"]})
    dim_body_part.write_parquet(d / "dim_body_part.parquet")

    dim_body_rollup = pl.DataFrame(
        {"rollup_id": ["hand_finger", "eye_face"], "body_part_code": [82, 77]}
    )
    dim_body_rollup.write_parquet(d / "dim_body_rollup.parquet")

    dim_diagnosis = pl.DataFrame(
        {"code": [59, 56], "injury_category": ["laceration_puncture", "foreign_body"]}
    )
    dim_diagnosis.write_parquet(d / "dim_diagnosis.parquet")

    dim_disposition = pl.DataFrame(
        {
            "code": [1, 2],
            "severity_bucket": ["released", "escalated"],
            "escalated_care": [False, True],
        }
    )
    dim_disposition.write_parquet(d / "dim_disposition.parquet")

    narrative_enrichment = pl.DataFrame({"case_id": case_ids, "primary_mechanism": mechanisms})
    narrative_enrichment.write_parquet(d / "narrative_enrichment.parquet")

    return d


@pytest.fixture
def con(aggregates_processed_dir):
    return connect(aggregates_processed_dir)


@pytest.fixture
def aggregates(con):
    return build_aggregates(con, LATEST_YEAR).tables


def test_returns_exactly_the_7_named_tables(aggregates):
    assert set(aggregates) == {
        "agg_tool_year",
        "agg_tool_body_region",
        "agg_tool_diagnosis",
        "agg_tool_mechanism",
        "agg_tool_disposition",
        "agg_tool_severity",
        "agg_overall_year",
    }


def test_every_table_carries_both_windows(aggregates):
    for name, df in aggregates.items():
        assert df.height > 0, name
        assert set(df["window"].unique().to_list()) == {"all_years", "latest_5"}, name
        assert (df["age_scope"] == "adults").all(), name


def test_all_years_window_includes_2019_latest_5_excludes_it(aggregates):
    agg_overall_year = aggregates["agg_overall_year"]
    all_years_rows = agg_overall_year.filter(pl.col("window") == "all_years")
    latest5_rows = agg_overall_year.filter(pl.col("window") == "latest_5")
    assert 2019 in all_years_rows["year"].to_list()
    assert 2019 not in latest5_rows["year"].to_list()


def test_agg_tool_year_matches_estimate_by_per_year(con, aggregates):
    df = aggregates["agg_tool_year"]
    for row in df.iter_rows(named=True):
        flt = CaseFilter(years=(row["year"], row["year"]))
        expected = estimate_by(con, flt, by="tool")
        match = expected.filter(pl.col("group_id") == row["group_id"]).row(0, named=True)
        assert row["estimate"] == pytest.approx(match["estimate"], rel=_TOL)
        assert row["se"] == pytest.approx(match["se"], rel=_TOL) if match["se"] else True
        assert row["raw_n"] == match["raw_n"]
        assert row["stable"] == match["stable"]


def test_agg_overall_year_matches_estimate_total_per_year(con, aggregates):
    df = aggregates["agg_overall_year"]
    for row in df.iter_rows(named=True):
        flt = CaseFilter(years=(row["year"], row["year"]))
        expected = estimate_total(con, flt)
        assert row["estimate"] == pytest.approx(expected.estimate, rel=_TOL)
        assert row["raw_n"] == expected.raw_n
        assert row["stable"] == expected.stable


def _window_range(con, window_label: str) -> tuple[int, int]:
    windows = dict(_windows(con, LATEST_YEAR))
    return windows[window_label]


def test_agg_tool_body_region_matches_estimate_by_with_equivalent_filter(con, aggregates):
    df = aggregates["agg_tool_body_region"]
    assert set(df["basis"].unique().to_list()) == {"any", "primary"}
    for row in df.iter_rows(named=True):
        years_range = _window_range(con, row["window"])
        flt = CaseFilter(
            years=years_range, body_regions=(row["body_region"],), body_basis=row["basis"]
        )
        expected = estimate_by(con, flt, by="tool")
        match = expected.filter(pl.col("group_id") == row["group_id"]).row(0, named=True)
        assert row["estimate"] == pytest.approx(match["estimate"], rel=_TOL)
        assert row["raw_n"] == match["raw_n"]
        assert row["stable"] == match["stable"]


def test_agg_tool_diagnosis_matches_estimate_by_with_equivalent_filter(con, aggregates):
    df = aggregates["agg_tool_diagnosis"]
    assert set(df["basis"].unique().to_list()) == {"any", "primary"}
    for row in df.iter_rows(named=True):
        years_range = _window_range(con, row["window"])
        flt = CaseFilter(
            years=years_range,
            diagnosis_categories=(row["diagnosis_category"],),
            diagnosis_basis=row["basis"],
        )
        expected = estimate_by(con, flt, by="tool")
        match = expected.filter(pl.col("group_id") == row["group_id"]).row(0, named=True)
        assert row["estimate"] == pytest.approx(match["estimate"], rel=_TOL)
        assert row["raw_n"] == match["raw_n"]
        assert row["stable"] == match["stable"]


def test_agg_tool_mechanism_uses_primary_mechanism_and_matches(con, aggregates):
    df = aggregates["agg_tool_mechanism"]
    for row in df.iter_rows(named=True):
        years_range = _window_range(con, row["window"])
        flt = CaseFilter(years=years_range, mechanisms=(row["mechanism"],))
        expected = estimate_by(con, flt, by="tool")
        match = expected.filter(pl.col("group_id") == row["group_id"]).row(0, named=True)
        assert row["estimate"] == pytest.approx(match["estimate"], rel=_TOL)
        assert row["raw_n"] == match["raw_n"]
        assert row["stable"] == match["stable"]


def test_agg_tool_disposition_matches_estimate_by_with_equivalent_filter(con, aggregates):
    df = aggregates["agg_tool_disposition"]
    for row in df.iter_rows(named=True):
        years_range = _window_range(con, row["window"])
        flt = CaseFilter(years=years_range, dispositions=(row["disposition_code"],))
        expected = estimate_by(con, flt, by="tool")
        match = expected.filter(pl.col("group_id") == row["group_id"]).row(0, named=True)
        assert row["estimate"] == pytest.approx(match["estimate"], rel=_TOL)
        assert row["raw_n"] == match["raw_n"]
        assert row["stable"] == match["stable"]


def test_agg_tool_severity_matches_severity_profile(con, aggregates):
    df = aggregates["agg_tool_severity"]
    for window_label in ("all_years", "latest_5"):
        years_range = _window_range(con, window_label)
        expected = severity_profile(con, CaseFilter(years=years_range), by="tool")
        actual = df.filter(pl.col("window") == window_label)
        assert actual.height == expected.height
        for row in actual.iter_rows(named=True):
            match = expected.filter(
                (pl.col("group_id") == row["group_id"]) & (pl.col("metric_id") == row["metric_id"])
            ).row(0, named=True)
            assert (
                row["share"] == pytest.approx(match["share"], rel=_TOL)
                if match["share"] is not None
                else row["share"] is None
            )
            assert row["stable"] == match["stable"]
