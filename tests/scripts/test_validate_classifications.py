"""D-20/NARR-06 tests for wii.validation.classifier_eval and scripts/validate_classifications.py.
Synthetic-only: builds a tiny processed dir + a tiny labeled fixture CSV in tmp_path, never reads
the real committed data or the real labeled_validation.csv fixture."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import polars as pl
import pytest

from wii.validation.classifier_eval import per_class_precision_recall, per_field_accuracy

FIXTURE_HEADER = [
    "case_id",
    "year",
    "split",
    "official_product_code",
    "official_tool_family",
    "narrative",
    "label_tool_subtype",
    "label_final_tool_id",
    "label_activity",
    "label_primary_mechanism",
    "label_secondary_mechanisms",
    "label_maintenance_or_setup",
    "label_ppe_mentioned",
    "label_guard_mentioned",
    "label_maintenance_context",
    "label_power_state_mentioned",
    "labeler_notes",
]


def test_per_class_precision_recall_hand_computed():
    labels = ["a", "a", "b", "b", "b", "c"]
    predictions = ["a", "b", "b", "b", "a", "c"]

    result = per_class_precision_recall(labels, predictions)

    assert result["a"]["precision"] == pytest.approx(0.5)
    assert result["a"]["recall"] == pytest.approx(0.5)
    assert result["a"]["support"] == 2

    assert result["b"]["precision"] == pytest.approx(2 / 3)
    assert result["b"]["recall"] == pytest.approx(2 / 3)
    assert result["b"]["support"] == 3

    assert result["c"]["precision"] == pytest.approx(1.0)
    assert result["c"]["recall"] == pytest.approx(1.0)
    assert result["c"]["support"] == 1


def test_per_field_accuracy_covers_all_9_fields():
    rows = [
        {
            "label_tool_subtype": "miter_saw",
            "pred_tool_subtype": "miter_saw",
            "label_final_tool_id": "miter_saw",
            "pred_final_tool_id": "miter_saw",
            "label_activity": "cutting",
            "pred_activity": "cutting",
            "label_primary_mechanism": "kickback_ejection",
            "pred_primary_mechanism": "kickback_ejection",
            "label_maintenance_or_setup": "FALSE",
            "pred_maintenance_or_setup": False,
            "label_ppe_mentioned": "not_mentioned",
            "pred_ppe_mentioned": "not_mentioned",
            "label_guard_mentioned": "not_mentioned",
            "pred_guard_mentioned": "not_mentioned",
            "label_maintenance_context": "not_maintenance",
            "pred_maintenance_context": "not_maintenance",
            "label_power_state_mentioned": "not_mentioned",
            "pred_power_state_mentioned": "not_mentioned",
        },
        {
            "label_tool_subtype": "table_saw",
            "pred_tool_subtype": "unknown",
            "label_final_tool_id": "table_saw",
            "pred_final_tool_id": "table_saw",
            "label_activity": "cutting",
            "pred_activity": "unknown",
            "label_primary_mechanism": "direct_cutting_contact",
            "pred_primary_mechanism": "unknown",
            "label_maintenance_or_setup": "TRUE",
            "pred_maintenance_or_setup": False,
            "label_ppe_mentioned": "mentioned_worn",
            "pred_ppe_mentioned": "not_mentioned",
            "label_guard_mentioned": "not_mentioned",
            "pred_guard_mentioned": "not_mentioned",
            "label_maintenance_context": "blade_change",
            "pred_maintenance_context": "not_maintenance",
            "label_power_state_mentioned": "mentioned_unplugged",
            "pred_power_state_mentioned": "not_mentioned",
        },
    ]

    result = per_field_accuracy(rows)

    assert set(result) == {
        "tool_subtype",
        "final_tool_id",
        "activity",
        "primary_mechanism",
        "maintenance_or_setup",
        "ppe_mentioned",
        "guard_mentioned",
        "maintenance_context",
        "power_state_mentioned",
    }
    assert result["tool_subtype"] == pytest.approx(0.5)
    assert result["final_tool_id"] == pytest.approx(1.0)
    assert result["activity"] == pytest.approx(0.5)
    assert result["primary_mechanism"] == pytest.approx(0.5)
    assert result["maintenance_or_setup"] == pytest.approx(0.5)
    assert result["ppe_mentioned"] == pytest.approx(0.5)
    assert result["guard_mentioned"] == pytest.approx(1.0)
    assert result["maintenance_context"] == pytest.approx(0.5)
    assert result["power_state_mentioned"] == pytest.approx(0.5)


def _write_synthetic_processed_dir(d: Path) -> None:
    d.mkdir(parents=True, exist_ok=True)

    fact_cases = pl.DataFrame(
        {
            "case_id": ["c1", "c2", "c3"],
            "narrative": [
                "43YOM USING A MITER SAW CUT FINGER",
                "SOME NARRATIVE WITH NO CUES",
                "",
            ],
        }
    )
    fact_cases.write_parquet(d / "fact_cases.parquet")

    bridge = pl.DataFrame(
        {
            "case_id": ["c1", "c2", "c3"],
            "product_code": [895, 841, 899],
            "seq": [1, 1, 1],
            "tool_id": ["other_power_saw", "table_saw", "grinder_unspecified"],
            "in_scope": [True, True, True],
            "narrative_refined_tool": ["miter_saw", None, None],
            "final_tool_id": ["miter_saw", "table_saw", "grinder_unspecified"],
            "classification_confidence": [0.9, 0.0, 0.0],
        },
        schema_overrides={"classification_confidence": pl.Float32},
    )
    bridge.write_parquet(d / "bridge_case_products.parquet")

    dim_product = pl.DataFrame({"product_code": [895, 841, 899], "broad": [True, False, True]})
    dim_product.write_parquet(d / "dim_product.parquet")

    enrichment = pl.DataFrame(
        {
            "case_id": ["c1", "c2", "c3"],
            "tool_subtype": ["miter_saw", "unknown", "unknown"],
            "primary_mechanism": ["direct_cutting_contact", "unknown", "unknown"],
            "confidence": [0.9, 0.0, 0.0],
            "classification_source": ["rules", "rules", "rules"],
        }
    )
    enrichment.write_parquet(d / "narrative_enrichment.parquet")


def _write_tiny_fixture(path: Path) -> None:
    rows = [
        {
            "case_id": "1",
            "year": "2024",
            "split": "holdout",
            "official_product_code": "895",
            "official_tool_family": "saws",
            "narrative": "43YOM USING A MITER SAW CUT FINGER",
            "label_tool_subtype": "miter_saw",
            "label_final_tool_id": "miter_saw",
            "label_activity": "cutting",
            "label_primary_mechanism": "direct_cutting_contact",
            "label_secondary_mechanisms": "",
            "label_maintenance_or_setup": "FALSE",
            "label_ppe_mentioned": "not_mentioned",
            "label_guard_mentioned": "not_mentioned",
            "label_maintenance_context": "not_maintenance",
            "label_power_state_mentioned": "not_mentioned",
            "labeler_notes": "",
        },
        {
            "case_id": "2",
            "year": "2024",
            "split": "holdout",
            "official_product_code": "841",
            "official_tool_family": "saws",
            "narrative": "SOME NARRATIVE WITH NO CUES",
            "label_tool_subtype": "unknown",
            "label_final_tool_id": "table_saw",
            "label_activity": "unknown",
            "label_primary_mechanism": "unknown",
            "label_secondary_mechanisms": "",
            "label_maintenance_or_setup": "FALSE",
            "label_ppe_mentioned": "not_mentioned",
            "label_guard_mentioned": "not_mentioned",
            "label_maintenance_context": "not_maintenance",
            "label_power_state_mentioned": "not_mentioned",
            "labeler_notes": "",
        },
        {
            "case_id": "3",
            "year": "2024",
            "split": "dev",
            "official_product_code": "899",
            "official_tool_family": "abrasive",
            "narrative": "38YOM USING ANGLE GRINDER SPARKS IN EYE",
            "label_tool_subtype": "angle_grinder",
            "label_final_tool_id": "angle_grinder",
            "label_activity": "sanding_grinding",
            "label_primary_mechanism": "flying_debris_fb",
            "label_secondary_mechanisms": "",
            "label_maintenance_or_setup": "FALSE",
            "label_ppe_mentioned": "not_mentioned",
            "label_guard_mentioned": "not_mentioned",
            "label_maintenance_context": "not_maintenance",
            "label_power_state_mentioned": "not_mentioned",
            "labeler_notes": "",
        },
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIXTURE_HEADER)
        writer.writeheader()
        writer.writerows(rows)


def test_cli_writes_json_csv_and_markdown_with_required_keys(tmp_path):
    from scripts.validate_classifications import main

    processed_dir = tmp_path / "processed"
    _write_synthetic_processed_dir(processed_dir)

    fixture_path = tmp_path / "fixture.csv"
    _write_tiny_fixture(fixture_path)

    out_json = tmp_path / "out" / "classifier_validation.json"
    out_sample = tmp_path / "out" / "classifier_review_sample.csv"
    out_md = tmp_path / "out" / "classifier_validation.md"

    exit_code = main(
        [
            "--processed-dir",
            str(processed_dir),
            "--fixture",
            str(fixture_path),
            "--out-json",
            str(out_json),
            "--out-sample",
            str(out_sample),
            "--out-md",
            str(out_md),
        ]
    )

    assert exit_code == 0
    assert out_json.is_file()
    data = json.loads(out_json.read_text())
    for key in (
        "coverage",
        "unknown_pct",
        "confidence_distribution",
        "source_counts",
        "disagreements",
        "subtype_coverage",
        "per_field_accuracy",
        "per_class",
        "labeled",
    ):
        assert key in data, key
    assert data["labeled"]["holdout_n"] == 2
    assert data["labeled"]["dev_n"] == 1

    assert out_sample.is_file()
    assert out_md.is_file()
    md_text = out_md.read_text()
    assert "AI developer assistant" in md_text
    assert "no accuracy target" in md_text.lower()
