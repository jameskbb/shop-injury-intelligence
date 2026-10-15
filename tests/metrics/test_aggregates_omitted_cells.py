"""Finding 3 regression tests: aggregates.py must raise/propagate any ValueError that is not the
min-PSU structural condition, and must record (not silently drop) every cell it omits because of
that specific condition."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from wii.metrics.aggregates import build_aggregates
from wii.metrics.db import connect

LATEST_YEAR = 2024
# 2022 has only 1 PSU for its stratum -- every query whose year range includes 2022 (all_years
# AND latest_5, since latest_5 = 2020-2024) must raise InsufficientPSUsError for that cell, while
# 2023/2024-only cells (agg_tool_year's per-year loop) succeed normally.
YEARS = [2022, 2023, 2024]


@pytest.fixture
def sparse_processed_dir(tmp_path) -> Path:
    d = tmp_path / "processed"
    d.mkdir()

    design_psu_rows = [{"year": 2022, "stratum": "S", "psu": 1}]  # only 1 PSU: insufficient
    for year in (2023, 2024):
        for psu in (1, 2):
            design_psu_rows.append({"year": year, "stratum": "S", "psu": psu})
    pl.DataFrame(design_psu_rows).write_parquet(d / "design_psu.parquet")

    case_ids = ["c2022", "c2023", "c2024"]
    years = [2022, 2023, 2024]
    pl.DataFrame(
        {
            "case_id": case_ids,
            "year": years,
            "stratum": ["S"] * 3,
            "psu": [1, 1, 1],
            "weight": [100.0] * 3,
            "is_adult": [True] * 3,
            "disposition_code": [1] * 3,
            "primary_body_part_code": [82] * 3,
            "primary_diagnosis_code": [59] * 3,
        }
    ).write_parquet(d / "fact_cases.parquet")

    pl.DataFrame(
        {
            "case_id": case_ids,
            "product_code": [841] * 3,
            "seq": [1] * 3,
            "is_primary": [True] * 3,
            "tool_id": ["table_saw"] * 3,
            "family_id": ["saws"] * 3,
            "in_scope": [True] * 3,
            "is_adjacent": [False] * 3,
            "narrative_refined_tool": [None] * 3,
            "final_tool_id": ["table_saw"] * 3,
            "classification_confidence": [None] * 3,
        },
        schema_overrides={"classification_confidence": pl.Float32},
    ).write_parquet(d / "bridge_case_products.parquet")

    pl.DataFrame(
        {
            "case_id": case_ids,
            "year": years,
            "body_part_code": [82] * 3,
            "seq": [1] * 3,
            "is_primary": [True] * 3,
        }
    ).write_parquet(d / "bridge_case_body_parts.parquet")

    pl.DataFrame(
        {
            "case_id": case_ids,
            "year": years,
            "diagnosis_code": [59] * 3,
            "other_diagnosis_text": [None] * 3,
            "seq": [1] * 3,
            "is_primary": [True] * 3,
        }
    ).write_parquet(d / "bridge_case_diagnoses.parquet")

    pl.DataFrame(
        {
            "tool_id": ["table_saw"],
            "label": ["Table Saw"],
            "family_id": ["saws"],
            "family_sort_order": [1],
        }
    ).write_parquet(d / "dim_tool.parquet")
    pl.DataFrame({"code": [82], "region_id": ["hand"]}).write_parquet(d / "dim_body_part.parquet")
    pl.DataFrame({"rollup_id": ["hand_finger"], "body_part_code": [82]}).write_parquet(
        d / "dim_body_rollup.parquet"
    )
    pl.DataFrame({"code": [59], "injury_category": ["laceration_puncture"]}).write_parquet(
        d / "dim_diagnosis.parquet"
    )
    pl.DataFrame(
        {"code": [1], "severity_bucket": ["released"], "escalated_care": [False]}
    ).write_parquet(d / "dim_disposition.parquet")
    pl.DataFrame(
        {"case_id": case_ids, "primary_mechanism": ["direct_cutting_contact"] * 3}
    ).write_parquet(d / "narrative_enrichment.parquet")

    return d


@pytest.fixture
def con(sparse_processed_dir):
    return connect(sparse_processed_dir)


def test_unrelated_value_error_propagates(con, monkeypatch):
    """A ValueError raised for any reason OTHER than the min-PSU structural condition must not be
    silently swallowed by aggregates.py's cell-omission handling."""
    import wii.metrics.aggregates as agg_mod

    def _boom(*_args, **_kwargs):
        raise ValueError("some unrelated problem, not insufficient PSUs")

    monkeypatch.setattr(agg_mod, "estimate_by", _boom)
    with pytest.raises(ValueError, match="some unrelated problem"):
        build_aggregates(con, LATEST_YEAR)


def test_insufficient_psu_cells_are_recorded_not_silently_dropped(con):
    result = build_aggregates(con, LATEST_YEAR)

    assert result.omitted_cells, "expected at least one recorded omitted cell"
    for cell in result.omitted_cells:
        assert cell["table"]
        assert cell["window"]
        assert "reason" in cell
        assert "insufficient" in cell["reason"].lower() or "psu" in cell["reason"].lower()

    # The sparse year (2022) must be missing from agg_tool_year while the two good years remain.
    agg_tool_year = result.tables["agg_tool_year"]
    assert 2022 not in agg_tool_year["year"].to_list()
    assert 2023 in agg_tool_year["year"].to_list()
    assert 2024 in agg_tool_year["year"].to_list()


def test_build_aggregates_still_returns_all_7_tables_despite_omissions(con):
    result = build_aggregates(con, LATEST_YEAR)
    assert set(result.tables) == {
        "agg_tool_year",
        "agg_tool_body_region",
        "agg_tool_diagnosis",
        "agg_tool_mechanism",
        "agg_tool_disposition",
        "agg_tool_severity",
        "agg_overall_year",
    }
