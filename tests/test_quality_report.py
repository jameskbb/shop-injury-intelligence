"""QA-01 gate completion (Task 2 of 01-03): advisory coverage, classifier-readiness metrics,
and a readable qa_report.md/qa_report.json.

Most tests run the full offline pipeline via `wii.data.sync.main` on tiny synthetic fixtures and
inspect the written reports (matching the established pattern in tests/test_dictionaries.py and
tests/test_multiyear.py). The classifier_coverage/broad_code_refinement_rate tests build a
QAContext directly, since those read Phase 2's D-06 columns that no CLI flow populates yet.
"""

from __future__ import annotations

import json

import polars as pl
import pytest

from tests.conftest import write_neiss_tsv
from wii.data.quality import QAContext, run_all
from wii.data.sync import main
from wii.taxonomy.dims import build_product_dims
from wii.taxonomy.loader import load_product_taxonomy

# Verified against product_taxonomy.yaml: 845 (saws, not specified) is flagged broad; 841
# (bench/table saw) is a specific, non-broad code.
_BROAD_CODE = 845
_NONBROAD_CODE = 841


def _run(tmp_path, records, year=2024):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    data_root = tmp_path / "data_root"
    write_neiss_tsv(raw_dir / f"neiss{year}.tsv", records)
    exit_code = main(
        [
            "--years",
            str(year),
            "--offline",
            "--raw-dir",
            str(raw_dir),
            "--data-root",
            str(data_root),
        ]
    )
    return exit_code, data_root


def _load_report(data_root) -> dict:
    return json.loads((data_root / "metadata" / "qa_report.json").read_text())


def _result(report: dict, check_id: str) -> dict:
    return next(r for r in report["results"] if r["check_id"] == check_id)


# --- missing_narratives -----------------------------------------------------------------


def test_missing_narratives_flags_empty_narrative_and_run_still_exits_ok(tmp_path, neiss_record):
    case = neiss_record(Narrative_1="")
    exit_code, data_root = _run(tmp_path, [case])
    assert exit_code == 0  # advisory only -- never aborts

    result = _result(_load_report(data_root), "missing_narratives")
    assert result["category"] == "advisory"
    assert result["passed"] is False
    assert result["count"] == 1
    assert case[0] in result["examples"]


def test_missing_narratives_passes_when_every_case_has_a_narrative(tmp_path, neiss_record):
    exit_code, data_root = _run(tmp_path, [neiss_record()])
    assert exit_code == 0

    result = _result(_load_report(data_root), "missing_narratives")
    assert result["passed"] is True
    assert result["count"] == 0


# --- secondary_field_availability -----------------------------------------------------------------


def test_secondary_field_availability_always_passes_and_reports_the_year(tmp_path, neiss_record):
    exit_code, data_root = _run(tmp_path, [neiss_record(), neiss_record()], year=2024)
    assert exit_code == 0

    result = _result(_load_report(data_root), "secondary_field_availability")
    assert result["passed"] is True
    assert result["category"] == "advisory"
    assert "2024" in result["detail"]
    assert "body_part_2" in result["detail"]


# --- workshop_case_volume_sanity -----------------------------------------------------------------


def test_workshop_case_volume_sanity_flags_low_volume_year(tmp_path, neiss_record):
    # Only 2 in-scope cases for the year -- well below the [1500, 6000] sanity band.
    exit_code, data_root = _run(tmp_path, [neiss_record(), neiss_record()], year=2024)
    assert exit_code == 0

    result = _result(_load_report(data_root), "workshop_case_volume_sanity")
    assert result["passed"] is False
    assert result["count"] == 1
    assert "2024" in result["detail"]


# --- qa_report.md structure -----------------------------------------------------------------


def test_qa_report_md_lists_structural_before_advisory_with_secondary_table(tmp_path, neiss_record):
    exit_code, data_root = _run(tmp_path, [neiss_record()])
    assert exit_code == 0

    text = (data_root / "metadata" / "qa_report.md").read_text()
    assert "STATUS: PASSED" in text
    assert "raw NEISS sample records, not national estimates" in text
    assert "## Structural" in text
    assert "## Advisory" in text
    assert text.index("## Structural") < text.index("## Advisory")
    assert "Secondary-field availability by year" in text


# --- qa_report.json shape -----------------------------------------------------------------


def test_qa_report_json_has_report_version_and_full_result_shape(tmp_path, neiss_record):
    exit_code, data_root = _run(tmp_path, [neiss_record()])
    assert exit_code == 0

    report = _load_report(data_root)
    assert report["report_version"] == 1
    for r in report["results"]:
        for key in ("check_id", "category", "passed", "detail", "count", "examples"):
            assert key in r


def test_qa_report_json_includes_every_qa01_check_id_and_is_not_aborted(tmp_path, neiss_record):
    exit_code, data_root = _run(tmp_path, [neiss_record()])
    assert exit_code == 0

    report = _load_report(data_root)
    assert report["aborted"] is False
    check_ids = {r["check_id"] for r in report["results"]}
    expected = {
        "missing_narratives",
        "secondary_field_availability",
        "workshop_case_volume_sanity",
        "classifier_coverage",
        "broad_code_refinement_rate",
        "non_taxonomy_coproduct_codes",
        "rejoined_records",
        "cp1252_fallback_years",
        "yoy_workshop_volume_within_40pct",
    }
    assert expected.issubset(check_ids)


# --- classifier_coverage / broad_code_refinement_rate (D-06 synthetic) --------------------


def _taxonomy_and_dim_product():
    taxonomy = load_product_taxonomy()
    dim_product = build_product_dims(taxonomy)["dim_product"]
    return taxonomy, dim_product


def _bridge_frame(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(
        rows,
        schema={
            "case_id": pl.Utf8,
            "product_code": pl.Int16,
            "seq": pl.Int8,
            "is_primary": pl.Boolean,
            "tool_id": pl.Utf8,
            "family_id": pl.Utf8,
            "in_scope": pl.Boolean,
            "is_adjacent": pl.Boolean,
            "narrative_refined_tool": pl.Utf8,
            "final_tool_id": pl.Utf8,
            "classification_confidence": pl.Float32,
        },
    )


def _ctx_for_bridge(bridge: pl.DataFrame, dim_product: pl.DataFrame, taxonomy) -> QAContext:
    return QAContext(
        years=[2024],
        parse_failures={},
        tables={"bridge_case_products": bridge, "dim_product": dim_product},
        taxonomy=taxonomy,
        year_stats={},
    )


def test_classifier_coverage_reports_zero_percent_when_all_confidence_null():
    taxonomy, dim_product = _taxonomy_and_dim_product()
    bridge = _bridge_frame(
        [
            {
                "case_id": f"{i:09d}",
                "product_code": _NONBROAD_CODE,
                "seq": 1,
                "is_primary": True,
                "tool_id": "table_saw",
                "family_id": "saws",
                "in_scope": True,
                "is_adjacent": False,
                "narrative_refined_tool": None,
                "final_tool_id": "table_saw",
                "classification_confidence": None,
            }
            for i in range(1, 4)
        ]
    )
    report = run_all(_ctx_for_bridge(bridge, dim_product, taxonomy))
    result = next(r for r in report.results if r.check_id == "classifier_coverage")
    assert result.passed is True  # informational -- never aborts
    assert "0.0%" in result.detail


def test_classifier_coverage_reports_hundred_percent_when_all_confidence_populated():
    taxonomy, dim_product = _taxonomy_and_dim_product()
    bridge = _bridge_frame(
        [
            {
                "case_id": f"{i:09d}",
                "product_code": _NONBROAD_CODE,
                "seq": 1,
                "is_primary": True,
                "tool_id": "table_saw",
                "family_id": "saws",
                "in_scope": True,
                "is_adjacent": False,
                "narrative_refined_tool": "table_saw",
                "final_tool_id": "table_saw",
                "classification_confidence": 0.9,
            }
            for i in range(1, 4)
        ]
    )
    report = run_all(_ctx_for_bridge(bridge, dim_product, taxonomy))
    result = next(r for r in report.results if r.check_id == "classifier_coverage")
    assert result.passed is True
    assert "100.0%" in result.detail


def test_broad_code_refinement_rate_counts_only_in_scope_broad_rows():
    taxonomy, dim_product = _taxonomy_and_dim_product()
    bridge = _bridge_frame(
        [
            # broad, in-scope, unrefined
            {
                "case_id": "000000001",
                "product_code": _BROAD_CODE,
                "seq": 1,
                "is_primary": True,
                "tool_id": "saw_unspecified",
                "family_id": "saws",
                "in_scope": True,
                "is_adjacent": False,
                "narrative_refined_tool": None,
                "final_tool_id": "saw_unspecified",
                "classification_confidence": None,
            },
            # broad, in-scope, refined
            {
                "case_id": "000000002",
                "product_code": _BROAD_CODE,
                "seq": 1,
                "is_primary": True,
                "tool_id": "saw_unspecified",
                "family_id": "saws",
                "in_scope": True,
                "is_adjacent": False,
                "narrative_refined_tool": "miter_saw",
                "final_tool_id": "miter_saw",
                "classification_confidence": 0.7,
            },
            # NOT broad, in-scope, unrefined -- must be excluded from the denominator
            {
                "case_id": "000000003",
                "product_code": _NONBROAD_CODE,
                "seq": 1,
                "is_primary": True,
                "tool_id": "table_saw",
                "family_id": "saws",
                "in_scope": True,
                "is_adjacent": False,
                "narrative_refined_tool": None,
                "final_tool_id": "table_saw",
                "classification_confidence": None,
            },
            # broad, but NOT in-scope -- must be excluded from the denominator
            {
                "case_id": "000000004",
                "product_code": _BROAD_CODE,
                "seq": 2,
                "is_primary": False,
                "tool_id": None,
                "family_id": None,
                "in_scope": False,
                "is_adjacent": False,
                "narrative_refined_tool": "something",
                "final_tool_id": None,
                "classification_confidence": None,
            },
        ]
    )
    report = run_all(_ctx_for_bridge(bridge, dim_product, taxonomy))
    result = next(r for r in report.results if r.check_id == "broad_code_refinement_rate")
    assert result.passed is True
    assert "1/2" in result.detail
    assert "50.0%" in result.detail


# --- run-to-completion contract holds for the new advisory checks -------------------------


def test_keyboard_interrupt_mid_run_leaves_prior_qa_report_json_byte_identical(
    tmp_path, neiss_record, monkeypatch
):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    data_root = tmp_path / "data_root"
    write_neiss_tsv(raw_dir / "neiss2024.tsv", [neiss_record(Product_1="841")])

    exit_code = main(
        ["--years", "2024", "--offline", "--raw-dir", str(raw_dir), "--data-root", str(data_root)]
    )
    assert exit_code == 0
    report_path = data_root / "metadata" / "qa_report.json"
    before = report_path.read_bytes()

    import wii.data.quality as quality_mod

    def _raise_keyboard_interrupt(ctx):
        raise KeyboardInterrupt

    monkeypatch.setattr(quality_mod, "ADVISORY_CHECKS", [_raise_keyboard_interrupt])

    from wii.data.pipeline import run_pipeline
    from wii.utils.paths import DataPaths

    paths = DataPaths.from_root(data_root, raw_dir=raw_dir)
    with pytest.raises(KeyboardInterrupt):
        run_pipeline({2024: raw_dir / "neiss2024.tsv"}, paths)

    after = report_path.read_bytes()
    assert before == after
