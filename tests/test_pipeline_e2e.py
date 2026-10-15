"""Synthetic end-to-end walking-skeleton tests: CLI -> parse -> taxonomy -> case model -> QA."""

from __future__ import annotations

import json

import polars as pl

from tests.conftest import write_neiss_tsv
from wii.data.sync import build_parser, main

_BANNED_COLUMNS = {"race", "other_race", "hispanic"}


def _assert_no_banned_columns(df: pl.DataFrame) -> None:
    lowered = {c.lower() for c in df.columns}
    assert not (lowered & _BANNED_COLUMNS)


def test_help_lists_all_flags(capsys):
    parser = build_parser()
    help_text = parser.format_help()
    for flag in ("--years", "--offline", "--raw-dir", "--data-root"):
        assert flag in help_text


def test_walking_skeleton_synthetic_2024(tmp_path, neiss_record):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    data_root = tmp_path / "data_root"

    table_saw_case = neiss_record(Product_1="841")
    dup_841_case = neiss_record(Product_1="841", Product_2="841")
    circular_plus_coproduct_case = neiss_record(Product_1="832", Product_2="1267")
    non_workshop_only_case = neiss_record(Product_1="1267")

    records = [
        table_saw_case,
        dup_841_case,
        circular_plus_coproduct_case,
        non_workshop_only_case,
    ]
    write_neiss_tsv(raw_dir / "neiss2024.tsv", records)

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

    fact_cases = pl.read_parquet(data_root / "processed" / "fact_cases.parquet")
    bridge = pl.read_parquet(data_root / "processed" / "bridge_case_products.parquet")
    dim_product = pl.read_parquet(data_root / "processed" / "dim_product.parquet")
    dim_tool = pl.read_parquet(data_root / "processed" / "dim_tool.parquet")
    dim_tool_family = pl.read_parquet(data_root / "processed" / "dim_tool_family.parquet")

    assert fact_cases.height == 3

    dup_case_id = dup_841_case[0]
    dup_bridge_rows = bridge.filter(pl.col("case_id") == dup_case_id)
    assert dup_bridge_rows.height == 2
    assert sorted(dup_bridge_rows["seq"].to_list()) == [1, 2]
    assert fact_cases.filter(pl.col("case_id") == dup_case_id).height == 1

    coproduct_case_id = circular_plus_coproduct_case[0]
    coproduct_row = bridge.filter(
        (pl.col("case_id") == coproduct_case_id) & (pl.col("product_code") == 1267)
    )
    assert coproduct_row.height == 1
    row = coproduct_row.row(0, named=True)
    assert row["in_scope"] is False
    assert row["tool_id"] is None

    non_workshop_case_id = non_workshop_only_case[0]
    assert fact_cases.filter(pl.col("case_id") == non_workshop_case_id).height == 0

    assert dim_product.height == 32
    # D-14 (Phase 2): dim_tool always carries the 27 official tools + the 4 narrative-refined
    # tools (miter_saw, planer, angle_grinder, bench_grinder), regardless of whether any case in
    # this run actually refined to one.
    assert dim_tool.height == 31
    assert dim_tool_family.height == 5

    for df in (fact_cases, bridge, dim_product, dim_tool, dim_tool_family):
        _assert_no_banned_columns(df)


def test_duplicate_case_id_aborts(tmp_path, neiss_record):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    data_root = tmp_path / "data_root"

    dup_id = "000000001"
    records = [
        neiss_record(**{"CPSC_Case_Number": dup_id}),
        neiss_record(**{"CPSC_Case_Number": dup_id}),
    ]
    write_neiss_tsv(raw_dir / "neiss2024.tsv", records)

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
    assert exit_code == 1

    report = json.loads((data_root / "metadata" / "qa_report.json").read_text())
    assert report["aborted"] is True
    assert not (data_root / "processed" / "fact_cases.parquet").exists()
    assert (data_root / "metadata" / "qa_report.md").exists()


def test_missing_year_file_exits_data_unavailable(tmp_path, capsys):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    data_root = tmp_path / "data_root"

    exit_code = main(
        [
            "--years",
            "2023",
            "--offline",
            "--raw-dir",
            str(raw_dir),
            "--data-root",
            str(data_root),
        ]
    )
    assert exit_code == 3

    captured = capsys.readouterr()
    assert "neiss2023.tsv" in captured.err
    assert "Archived%20Data/2023" in captured.err
