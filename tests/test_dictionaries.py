"""Diagnosis/disposition/location/sex dictionaries drive dims, the diagnosis bridge and
known-code gates (Task 2 of 01-02)."""

from __future__ import annotations

import hashlib
import json

import polars as pl
import pytest
import yaml

from tests.conftest import write_neiss_tsv
from wii.data.quality import QAContext, run_all
from wii.data.sync import main
from wii.taxonomy.dims import build_product_dims
from wii.taxonomy.loader import (
    CONFIG_DIR,
    TaxonomyConfigError,
    load_code_dictionary,
    load_product_taxonomy,
)


def _run(tmp_path, records, years="2024"):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    data_root = tmp_path / "data_root"
    write_neiss_tsv(raw_dir / f"neiss{years}.tsv", records)
    exit_code = main(
        ["--years", years, "--offline", "--raw-dir", str(raw_dir), "--data-root", str(data_root)]
    )
    return exit_code, data_root


def test_diagnosis_two_slots_produce_paired_bridge_row_with_other_text(tmp_path, neiss_record):
    case = neiss_record(Diagnosis="57", Diagnosis_2="59", Other_Diagnosis_2="SPLINTER")
    exit_code, data_root = _run(tmp_path, [case])
    assert exit_code == 0

    bridge = pl.read_parquet(data_root / "processed" / "bridge_case_diagnoses.parquet")
    rows = bridge.filter(pl.col("case_id") == case[0]).sort("seq")
    assert rows.height == 2
    row1 = rows.row(0, named=True)
    row2 = rows.row(1, named=True)
    assert row1["diagnosis_code"] == 57
    assert row1["is_primary"] is True
    assert row1["other_diagnosis_text"] is None
    assert row2["diagnosis_code"] == 59
    assert row2["is_primary"] is False
    assert row2["other_diagnosis_text"] == "SPLINTER"


def test_diagnosis_slot2_null_produces_single_row(tmp_path, neiss_record):
    case = neiss_record(Diagnosis="57", Diagnosis_2="")
    exit_code, data_root = _run(tmp_path, [case])
    assert exit_code == 0

    bridge = pl.read_parquet(data_root / "processed" / "bridge_case_diagnoses.parquet")
    rows = bridge.filter(pl.col("case_id") == case[0])
    assert rows.height == 1


@pytest.mark.parametrize(
    ("overrides", "check_id", "expected_code"),
    [
        ({"Diagnosis": "70"}, "known_diagnosis_codes", "70"),
        ({"Disposition": "3"}, "known_disposition_codes", "3"),
        ({"Location": "3"}, "known_location_codes", "3"),
        ({"Sex": "7"}, "known_sex_codes", "7"),
    ],
)
def test_unknown_code_aborts_and_names_field(
    tmp_path, neiss_record, overrides, check_id, expected_code
):
    case = neiss_record(**overrides)
    exit_code, data_root = _run(tmp_path, [case])
    assert exit_code == 1

    report = json.loads((data_root / "metadata" / "qa_report.json").read_text())
    result = next(r for r in report["results"] if r["check_id"] == check_id)
    assert result["passed"] is False
    assert expected_code in result["detail"]


def test_dim_shapes_after_run(tmp_path, neiss_record):
    exit_code, data_root = _run(tmp_path, [neiss_record()])
    assert exit_code == 0

    dim_diagnosis = pl.read_parquet(data_root / "processed" / "dim_diagnosis.parquet")
    assert dim_diagnosis.height == 30
    assert dim_diagnosis["injury_category"].null_count() == 0

    dim_disposition = pl.read_parquet(data_root / "processed" / "dim_disposition.parquet")
    assert dim_disposition.height == 7
    escalated = sorted(dim_disposition.filter(pl.col("escalated_care"))["code"].to_list())
    assert escalated == [2, 4]

    dim_location = pl.read_parquet(data_root / "processed" / "dim_location.parquet")
    assert dim_location.height == 9
    assert set(dim_location.columns) == {"code", "label", "manual_page", "sort_order"}

    dim_sex = pl.read_parquet(data_root / "processed" / "dim_sex.parquet")
    assert dim_sex.height == 3


def test_load_code_dictionary_missing_required_attr_names_key_path(tmp_path):
    with open(CONFIG_DIR / "diagnoses.yaml", encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    del doc["codes"][59]["injury_category"]
    (tmp_path / "diagnoses.yaml").write_text(yaml.safe_dump(doc))

    with pytest.raises(TaxonomyConfigError, match=r"diagnoses\.codes\.59\.injury_category"):
        load_code_dictionary("diagnoses", required_attrs=("injury_category",), config_dir=tmp_path)


def test_in_scope_product_mapping_fails_on_null_tool_id():
    taxonomy = load_product_taxonomy()
    dim_product = build_product_dims(taxonomy)["dim_product"]
    bad_bridge = pl.DataFrame(
        {
            "case_id": ["000000001"],
            "product_code": [841],
            "seq": [1],
            "is_primary": [True],
            "tool_id": [None],
            "family_id": [None],
            "in_scope": [True],
            "is_adjacent": [False],
        },
        schema={
            "case_id": pl.Utf8,
            "product_code": pl.Int16,
            "seq": pl.Int8,
            "is_primary": pl.Boolean,
            "tool_id": pl.Utf8,
            "family_id": pl.Utf8,
            "in_scope": pl.Boolean,
            "is_adjacent": pl.Boolean,
        },
    )
    ctx = QAContext(
        years=[2024],
        parse_failures={},
        tables={"bridge_case_products": bad_bridge, "dim_product": dim_product},
        taxonomy=taxonomy,
        year_stats={},
    )
    report = run_all(ctx)
    result = next(r for r in report.results if r.check_id == "in_scope_product_mapping")
    assert result.passed is False


def test_non_taxonomy_coproduct_is_advisory_only(tmp_path, neiss_record):
    case = neiss_record(Product_1="841", Product_2="1267")
    exit_code, data_root = _run(tmp_path, [case])
    assert exit_code == 0

    report = json.loads((data_root / "metadata" / "qa_report.json").read_text())
    result = next(r for r in report["results"] if r["check_id"] == "non_taxonomy_coproduct_codes")
    assert result["category"] == "advisory"
    assert result["count"] == 1


def _sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_dims_deterministic_across_reruns(tmp_path, neiss_record):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    write_neiss_tsv(raw_dir / "neiss2024.tsv", [neiss_record()])

    data_root_a = tmp_path / "data_root_a"
    data_root_b = tmp_path / "data_root_b"
    for data_root in (data_root_a, data_root_b):
        exit_code = main(
            [
                "--years",
                "2024",
                "--offline",
                "--raw-dir",
                str(raw_dir),
                "--data-root",
                str(data_root),
            ]
        )
        assert exit_code == 0

    dim_files_a = sorted((data_root_a / "processed").glob("dim_*.parquet"))
    assert dim_files_a
    for fa in dim_files_a:
        fb = data_root_b / "processed" / fa.name
        assert _sha256(fa) == _sha256(fb), f"{fa.name} not byte-identical across reruns"
