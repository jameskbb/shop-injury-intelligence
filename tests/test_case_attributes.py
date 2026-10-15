"""Case attributes: age decoding, adult flag, schema era, escalated care, D-06 reserved columns
(Task 3 of 01-02)."""

from __future__ import annotations

import json

import polars as pl
import pytest

from tests.conftest import write_neiss_tsv
from wii.data.sync import main


def _run(tmp_path, records, years="2024", filename=None):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    data_root = tmp_path / "data_root"
    write_neiss_tsv(raw_dir / (filename or f"neiss{years.split(':')[0]}.tsv"), records)
    exit_code = main(
        ["--years", years, "--offline", "--raw-dir", str(raw_dir), "--data-root", str(data_root)]
    )
    return exit_code, data_root


@pytest.mark.parametrize(
    ("age", "unit", "years", "bracket", "is_adult"),
    [
        ("0", "unknown", None, "unknown", False),
        ("2", "years", 2.0, "age_0_17", False),
        ("17", "years", 17.0, "age_0_17", False),
        ("18", "years", 18.0, "age_18_24", True),
        ("115", "years", 115.0, "age_75_plus", True),
        ("201", "months", 1 / 12, "age_0_17", False),
        ("209", "months", 9 / 12, "age_0_17", False),
        ("218", "months", 18 / 12, "age_0_17", False),
        ("223", "months", 23 / 12, "age_0_17", False),
    ],
)
def test_age_decoding_behavior(tmp_path, neiss_record, age, unit, years, bracket, is_adult):
    case = neiss_record(Age=age)
    exit_code, data_root = _run(tmp_path, [case])
    assert exit_code == 0

    fact_cases = pl.read_parquet(data_root / "processed" / "fact_cases.parquet")
    row = fact_cases.filter(pl.col("case_id") == case[0]).row(0, named=True)
    assert row["age_unit"] == unit
    if years is None:
        assert row["age_years"] is None
    else:
        assert row["age_years"] == pytest.approx(years)
    assert row["age_bracket"] == bracket
    assert row["is_adult"] is is_adult


def test_age_115_is_top_coded(tmp_path, neiss_record):
    case = neiss_record(Age="115")
    exit_code, data_root = _run(tmp_path, [case])
    assert exit_code == 0
    fact_cases = pl.read_parquet(data_root / "processed" / "fact_cases.parquet")
    row = fact_cases.filter(pl.col("case_id") == case[0]).row(0, named=True)
    assert row["age_top_coded"] is True


@pytest.mark.parametrize("age", ["1", "116", "199", "200", "224", "299", "300"])
def test_invalid_ages_abort_and_list_offending_value(tmp_path, neiss_record, age):
    case = neiss_record(Age=age)
    exit_code, data_root = _run(tmp_path, [case])
    assert exit_code == 1

    report = json.loads((data_root / "metadata" / "qa_report.json").read_text())
    result = next(r for r in report["results"] if r["check_id"] == "ages_in_valid_domain")
    assert result["passed"] is False
    assert age in result["detail"]


def test_ages_5_and_40_both_kept_in_fact_cases(tmp_path, neiss_record):
    """Prohibition: no row is dropped for the adults-only default (D-14)."""
    case_child = neiss_record(Age="5")
    case_adult = neiss_record(Age="40")
    exit_code, data_root = _run(tmp_path, [case_child, case_adult])
    assert exit_code == 0

    fact_cases = pl.read_parquet(data_root / "processed" / "fact_cases.parquet")
    assert fact_cases.filter(pl.col("case_id") == case_child[0]).height == 1
    assert fact_cases.filter(pl.col("case_id") == case_adult[0]).height == 1
    assert not fact_cases.filter(pl.col("is_adult"))["case_id"].is_in([case_child[0]]).any()
    assert fact_cases.filter(pl.col("is_adult"))["case_id"].is_in([case_adult[0]]).any()


def test_schema_era_pre_2019_blank_body_part_2(tmp_path, neiss_record):
    case = neiss_record(Treatment_Date="01/15/2018", Body_Part_2="")
    exit_code, data_root = _run(tmp_path, [case], years="2018", filename="neiss2018.tsv")
    assert exit_code == 0
    fact_cases = pl.read_parquet(data_root / "processed" / "fact_cases.parquet")
    row = fact_cases.filter(pl.col("case_id") == case[0]).row(0, named=True)
    assert row["schema_era"] == "pre_2019"


def test_schema_era_post_2019_ten_percent_filled(tmp_path, neiss_record):
    filled = [neiss_record(Treatment_Date="01/15/2019", Body_Part_2="82")]
    blanks = [neiss_record(Treatment_Date="01/15/2019", Body_Part_2="") for _ in range(9)]
    exit_code, data_root = _run(tmp_path, filled + blanks, years="2019", filename="neiss2019.tsv")
    assert exit_code == 0
    fact_cases = pl.read_parquet(data_root / "processed" / "fact_cases.parquet")
    assert fact_cases["schema_era"].unique().to_list() == ["post_2019"]


def test_secondary_fill_rate_by_era_fails_on_2018_with_any_value(tmp_path, neiss_record):
    case = neiss_record(Treatment_Date="01/15/2018", Body_Part_2="82")
    exit_code, data_root = _run(tmp_path, [case], years="2018", filename="neiss2018.tsv")
    assert exit_code == 1
    report = json.loads((data_root / "metadata" / "qa_report.json").read_text())
    result = next(r for r in report["results"] if r["check_id"] == "secondary_fill_rate_by_era")
    assert result["passed"] is False
    assert "2018" in result["detail"]


def test_secondary_fill_rate_by_era_fails_on_2019_at_zero(tmp_path, neiss_record):
    case = neiss_record(Treatment_Date="01/15/2019", Body_Part_2="")
    exit_code, data_root = _run(tmp_path, [case], years="2019", filename="neiss2019.tsv")
    assert exit_code == 1
    report = json.loads((data_root / "metadata" / "qa_report.json").read_text())
    result = next(r for r in report["results"] if r["check_id"] == "secondary_fill_rate_by_era")
    assert result["passed"] is False
    assert "2019" in result["detail"]


@pytest.mark.parametrize(
    ("disposition", "expected"),
    [
        ("1", False),
        ("2", True),
        ("4", True),
        ("5", False),
        ("6", False),
        ("8", False),
        ("9", False),
    ],
)
def test_escalated_care_true_only_for_2_and_4(tmp_path, neiss_record, disposition, expected):
    case = neiss_record(Disposition=disposition)
    exit_code, data_root = _run(tmp_path, [case])
    assert exit_code == 0
    fact_cases = pl.read_parquet(data_root / "processed" / "fact_cases.parquet")
    row = fact_cases.filter(pl.col("case_id") == case[0]).row(0, named=True)
    assert row["escalated_care"] is expected


def test_bridge_case_products_reserved_columns(tmp_path, neiss_record):
    case = neiss_record(Product_1="841", Product_2="1267")
    exit_code, data_root = _run(tmp_path, [case])
    assert exit_code == 0

    bridge = pl.read_parquet(data_root / "processed" / "bridge_case_products.parquet")
    rows = bridge.filter(pl.col("case_id") == case[0])
    assert set(rows.columns) >= {
        "narrative_refined_tool",
        "final_tool_id",
        "classification_confidence",
    }
    # 841 is not a broad code (D-14), so it is never refined -- narrative_refined_tool stays
    # null on every row. classification_confidence is now populated for in-scope rows (Phase 2,
    # D-22); the non-taxonomy/out-of-scope row (1267) is left untouched.
    assert rows["narrative_refined_tool"].is_null().all()

    in_scope_row = rows.filter(pl.col("product_code") == 841).row(0, named=True)
    assert in_scope_row["final_tool_id"] == in_scope_row["tool_id"]
    assert in_scope_row["classification_confidence"] is not None
    non_taxonomy_row = rows.filter(pl.col("product_code") == 1267).row(0, named=True)
    assert non_taxonomy_row["final_tool_id"] is None
    assert non_taxonomy_row["classification_confidence"] is None


def test_no_alcohol_or_drug_derived_columns(tmp_path, neiss_record):
    case = neiss_record()
    exit_code, data_root = _run(tmp_path, [case])
    assert exit_code == 0
    fact_cases = pl.read_parquet(data_root / "processed" / "fact_cases.parquet")
    alcohol_or_drug_cols = [
        c for c in fact_cases.columns if "alcohol" in c.lower() or "drug" in c.lower()
    ]
    assert set(alcohol_or_drug_cols) == {"alcohol_code", "drug_code"}
    assert fact_cases.schema["alcohol_code"] in (pl.Int8, pl.Int16, pl.Int32, pl.Int64)
    assert fact_cases.schema["drug_code"] in (pl.Int8, pl.Int16, pl.Int32, pl.Int64)


def test_dim_age_bracket_sorted_0_17_first_unknown_last(tmp_path, neiss_record):
    exit_code, data_root = _run(tmp_path, [neiss_record()])
    assert exit_code == 0
    dim_age_bracket = pl.read_parquet(data_root / "processed" / "dim_age_bracket.parquet")
    ids_by_sort_order = dim_age_bracket.sort("sort_order")["id"].to_list()
    assert ids_by_sort_order[0] == "age_0_17"
    assert ids_by_sort_order[-1] == "unknown"
