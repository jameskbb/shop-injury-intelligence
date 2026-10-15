"""Hand-computed regression tests for severity.py findings 1 and 9.

Design (all cases tool=table_saw, product 841, stratum S, psu 1; psu 2 is a zero-domain PSU
present in design_psu so the >=2-PSUs-per-stratum variance check passes):

  c1  2024 adult     disp=1 (released)      weight=1000
  c2  2024 adult     disp=2 (escalated)      weight=500
  c3  2024 adult     disp=9 (not_recorded)   weight=2000
  c4  2023 adult     disp=1 (released)       weight=100
  c5  2023 adult     disp=2 (escalated)      weight=100
  c6  2024 non-adult disp=2 (escalated)      weight=9000

For flt = CaseFilter(years=(2024, 2024), age_scope="all"):

Finding 1 (denominator must be caller flt AND known-disposition, not a year/age_scope-blind
known-disposition-only filter): known-disposition cases in 2024 (age_scope=all) = c1+c2+c6 =
1000+500+9000 = 10500 (raw_n=3). Before the fix the denominator ignored `flt` entirely (no
years, default age_scope="adults"), pooling 2023+2024 and dropping c6 -- 600/1700 != this
test's 9500/10500.

"transferred"/escalated_care numerator (disp=2) restricted to the same (years=2024, age_scope=all)
domain = c2 + c6 = 500 + 9000 = 9500 (raw_n=2). share = 9500/10500.

Finding 9: not-recorded must be derived from dim_disposition.severity_bucket == 'not_recorded'
(code 9 here, deliberately NOT hardcoded as a magic literal in the fixture's dim_disposition to
prove the derivation reads the table) and reported as a WEIGHTED estimate + raw_n, restricted to
the caller's own flt (years=2024, age_scope=all): c3 only = 2000 (raw_n=1).
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from wii.metrics.db import connect
from wii.metrics.filters import CaseFilter
from wii.metrics.severity import severity_profile


@pytest.fixture
def severity_processed_dir(tmp_path) -> Path:
    d = tmp_path / "processed"
    d.mkdir()

    pl.DataFrame(
        {
            "year": [2023, 2023, 2024, 2024],
            "stratum": ["S", "S", "S", "S"],
            "psu": [1, 2, 1, 2],
        }
    ).write_parquet(d / "design_psu.parquet")

    case_ids = ["c1", "c2", "c3", "c4", "c5", "c6"]
    pl.DataFrame(
        {
            "case_id": case_ids,
            "year": [2024, 2024, 2024, 2023, 2023, 2024],
            "is_adult": [True, True, True, True, True, False],
            "stratum": ["S"] * 6,
            "psu": [1] * 6,
            "weight": [1000.0, 500.0, 2000.0, 100.0, 100.0, 9000.0],
            "disposition_code": [1, 2, 9, 1, 2, 2],
        }
    ).write_parquet(d / "fact_cases.parquet")

    pl.DataFrame(
        {
            "case_id": case_ids,
            "product_code": [841] * 6,
            "seq": [1] * 6,
            "is_primary": [True] * 6,
            "tool_id": ["table_saw"] * 6,
            "family_id": ["saws"] * 6,
            "in_scope": [True] * 6,
            "is_adjacent": [False] * 6,
            "narrative_refined_tool": [None] * 6,
            "final_tool_id": ["table_saw"] * 6,
            "classification_confidence": [None] * 6,
        },
        schema_overrides={"classification_confidence": pl.Float32},
    ).write_parquet(d / "bridge_case_products.parquet")

    pl.DataFrame(
        {
            "tool_id": ["table_saw"],
            "label": ["Table Saw"],
            "family_id": ["saws"],
            "family_sort_order": [1],
        }
    ).write_parquet(d / "dim_tool.parquet")

    # Deliberately not code 9 hardcoded as "the" not-recorded code elsewhere -- severity.py must
    # read this table, not assume a literal.
    pl.DataFrame(
        {
            "code": [1, 2, 9],
            "severity_bucket": ["released", "escalated", "not_recorded"],
            "escalated_care": [False, True, False],
        }
    ).write_parquet(d / "dim_disposition.parquet")

    # Empty-but-correctly-shaped tables for the diagnosis_any/body_rollup_any metrics in
    # severity_metrics.yaml that this fixture doesn't otherwise exercise -- keeps severity_profile
    # from raising MissingTableError while iterating every configured metric.
    pl.DataFrame(schema={"case_id": pl.Utf8, "diagnosis_code": pl.Int64}).write_parquet(
        d / "bridge_case_diagnoses.parquet"
    )
    pl.DataFrame(schema={"code": pl.Int64, "injury_category": pl.Utf8}).write_parquet(
        d / "dim_diagnosis.parquet"
    )
    pl.DataFrame(schema={"case_id": pl.Utf8, "body_part_code": pl.Int64}).write_parquet(
        d / "bridge_case_body_parts.parquet"
    )
    pl.DataFrame(schema={"code": pl.Int64, "region_id": pl.Utf8}).write_parquet(
        d / "dim_body_part.parquet"
    )
    pl.DataFrame(schema={"rollup_id": pl.Utf8, "body_part_code": pl.Int64}).write_parquet(
        d / "dim_body_rollup.parquet"
    )

    return d


@pytest.fixture
def con(severity_processed_dir):
    return connect(severity_processed_dir)


def test_years_filter_changes_disposition_share_result(con):
    """Finding 1: caller's years filter must flow into the disposition denominator, not be
    silently dropped in favor of an all-years known-disposition-only filter."""
    flt = CaseFilter(years=(2024, 2024), age_scope="all")
    result = severity_profile(con, flt, by="tool")
    row = result.filter(
        (result["group_id"] == "table_saw") & (result["metric_id"] == "transferred")
    ).row(0, named=True)

    assert row["den_estimate"] == pytest.approx(10500.0)
    assert row["den_raw_n"] == 3
    assert row["num_estimate"] == pytest.approx(9500.0)
    assert row["num_raw_n"] == 2
    assert row["share"] == pytest.approx(9500.0 / 10500.0)


def test_not_recorded_is_weighted_estimate_derived_from_dim_disposition(con):
    """Finding 9: not-recorded is reported as a weighted estimate + raw_n (not an unweighted
    count), scoped to the caller's own flt, and the not-recorded code(s) come from
    dim_disposition.severity_bucket == 'not_recorded' rather than a hardcoded literal."""
    flt = CaseFilter(years=(2024, 2024), age_scope="all")
    result = severity_profile(con, flt, by="tool")
    row = result.filter(
        (result["group_id"] == "table_saw") & (result["metric_id"] == "transferred")
    ).row(0, named=True)

    assert row["not_recorded_estimate"] == pytest.approx(2000.0)
    assert row["not_recorded_raw_n"] == 1

    # Non-disposition metrics never report a not-recorded figure.
    non_disp_row = result.filter(
        (result["group_id"] == "table_saw") & (result["metric_id"] == "laceration")
    ).row(0, named=True)
    assert non_disp_row["not_recorded_estimate"] is None
    assert non_disp_row["not_recorded_raw_n"] is None
