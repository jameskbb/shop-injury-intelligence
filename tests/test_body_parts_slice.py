"""Body-part slice end-to-end: dictionary -> region/SVG map -> dim_body_part -> paired bridge
-> known-code gate."""

from __future__ import annotations

import json

import polars as pl
import pytest
import yaml

from tests.conftest import write_neiss_tsv
from wii.data.sync import main
from wii.taxonomy.loader import (
    CONFIG_DIR,
    TaxonomyConfigError,
    load_body_regions,
    load_code_dictionary,
)


def test_two_body_part_slots_produce_paired_bridge_rows(tmp_path, neiss_record):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    data_root = tmp_path / "data_root"

    case = neiss_record(Body_Part="82", Body_Part_2="92")
    write_neiss_tsv(raw_dir / "neiss2024.tsv", [case])

    exit_code = main(
        ["--years", "2024", "--offline", "--raw-dir", str(raw_dir), "--data-root", str(data_root)]
    )
    assert exit_code == 0

    bridge = pl.read_parquet(data_root / "processed" / "bridge_case_body_parts.parquet")
    rows = bridge.filter(pl.col("case_id") == case[0]).sort("seq")
    assert rows.height == 2
    assert rows.row(0, named=True) == {
        "case_id": case[0],
        "year": 2024,
        "body_part_code": 82,
        "seq": 1,
        "is_primary": True,
    }
    assert rows.row(1, named=True) == {
        "case_id": case[0],
        "year": 2024,
        "body_part_code": 92,
        "seq": 2,
        "is_primary": False,
    }


def test_body_part_zero_in_slot1_is_a_valid_row_and_null_slot2_produces_no_row(
    tmp_path, neiss_record
):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    data_root = tmp_path / "data_root"

    case = neiss_record(Body_Part="0", Diagnosis="41", Body_Part_2="")
    # Companion row keeps the file's overall Body_Part_2 fill rate above the post-2019 5%
    # threshold (secondary_fill_rate_by_era, D-16) without changing this test's own case.
    companion = neiss_record()
    write_neiss_tsv(raw_dir / "neiss2024.tsv", [case, companion])

    exit_code = main(
        ["--years", "2024", "--offline", "--raw-dir", str(raw_dir), "--data-root", str(data_root)]
    )
    assert exit_code == 0

    bridge = pl.read_parquet(data_root / "processed" / "bridge_case_body_parts.parquet")
    rows = bridge.filter(pl.col("case_id") == case[0])
    assert rows.height == 1
    assert rows.row(0, named=True)["body_part_code"] == 0


def test_body_part_2_zero_is_a_real_internal_slot2_row(tmp_path, neiss_record):
    """Body part 0 is "Internal (use with aspiration and ingestion)", a real code in either slot.
    Real post-2019 files pair a slot-2 0 with diagnosis 41/42 (e.g. 185 rows in 2024); only a
    blank Body_Part_2 (null after normalization) means "no second body part"."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    data_root = tmp_path / "data_root"

    case = neiss_record(Body_Part="82", Body_Part_2="0", Diagnosis_2="41")
    write_neiss_tsv(raw_dir / "neiss2024.tsv", [case])

    exit_code = main(
        ["--years", "2024", "--offline", "--raw-dir", str(raw_dir), "--data-root", str(data_root)]
    )
    assert exit_code == 0

    bridge = pl.read_parquet(data_root / "processed" / "bridge_case_body_parts.parquet")
    rows = bridge.filter(pl.col("case_id") == case[0]).sort("seq")
    assert rows["seq"].to_list() == [1, 2]
    assert rows["body_part_code"].to_list() == [82, 0]


def test_unknown_body_part_code_on_any_row_aborts_and_names_field(tmp_path, neiss_record):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    data_root = tmp_path / "data_root"

    workshop_case = neiss_record(Product_1="841")
    # Non-workshop-only row (never enters fact_cases/bridges) but the check runs on the FULL file.
    non_workshop_case = neiss_record(Product_1="1267", Body_Part="99")
    write_neiss_tsv(raw_dir / "neiss2024.tsv", [workshop_case, non_workshop_case])

    exit_code = main(
        ["--years", "2024", "--offline", "--raw-dir", str(raw_dir), "--data-root", str(data_root)]
    )
    assert exit_code == 1

    report = json.loads((data_root / "metadata" / "qa_report.json").read_text())
    result = next(r for r in report["results"] if r["check_id"] == "known_body_part_codes")
    assert result["passed"] is False
    assert "99" in result["detail"]
    assert "body_part" in result["detail"]

    md = (data_root / "metadata" / "qa_report.md").read_text()
    assert "99" in md
    assert not (data_root / "processed" / "fact_cases.parquet").exists()


def test_dim_body_part_has_26_rows_with_regions_and_callouts(tmp_path, neiss_record):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    data_root = tmp_path / "data_root"
    write_neiss_tsv(raw_dir / "neiss2024.tsv", [neiss_record()])

    exit_code = main(
        ["--years", "2024", "--offline", "--raw-dir", str(raw_dir), "--data-root", str(data_root)]
    )
    assert exit_code == 0

    dim_body_part = pl.read_parquet(data_root / "processed" / "dim_body_part.parquet")
    assert dim_body_part.height == 26
    assert dim_body_part["region_id"].null_count() == 0

    callout_rows = dim_body_part.filter(pl.col("code").is_in([0, 84, 85, 87]))
    assert callout_rows.height == 4
    assert callout_rows["is_callout"].all()
    for ids in callout_rows["svg_element_ids"].to_list():
        assert ids == []

    non_callout_rows = dim_body_part.filter(~pl.col("code").is_in([0, 84, 85, 87]))
    assert not non_callout_rows["is_callout"].any()

    dim_body_region = pl.read_parquet(data_root / "processed" / "dim_body_region.parquet")
    assert dim_body_region.height == 26  # 22 regions + 4 callouts

    dim_body_rollup = pl.read_parquet(data_root / "processed" / "dim_body_rollup.parquet")
    assert dim_body_rollup.filter(pl.col("rollup_id") == "hand_finger").height == 2
    assert dim_body_rollup.filter(pl.col("rollup_id") == "head_face").height == 5


def _write_body_regions_yaml(tmp_dir, mutate) -> None:
    with open(CONFIG_DIR / "body_regions.yaml", encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    mutate(doc)
    (tmp_dir / "body_regions.yaml").write_text(yaml.safe_dump(doc))


def test_load_body_regions_rejects_code_mapped_twice(tmp_path):
    body_parts = load_code_dictionary("body_parts")

    def mutate(doc):
        doc["regions"]["finger"]["body_part_codes"] = [92, 82]  # 82 already mapped to "hand"

    _write_body_regions_yaml(tmp_path, mutate)

    with pytest.raises(TaxonomyConfigError, match="82"):
        load_body_regions(body_parts, config_dir=tmp_path)


def test_load_body_regions_rejects_unmapped_code(tmp_path):
    body_parts = load_code_dictionary("body_parts")

    def mutate(doc):
        del doc["regions"]["ear"]  # leaves code 94 unmapped

    _write_body_regions_yaml(tmp_path, mutate)

    with pytest.raises(TaxonomyConfigError, match="94"):
        load_body_regions(body_parts, config_dir=tmp_path)
