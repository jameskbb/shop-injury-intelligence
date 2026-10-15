"""Tests for scripts/check_doc_numbers.py (D-07, 06-06 Task 2) -- synthetic metadata only, never
reads the repository's real data/metadata/*.json.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.check_doc_numbers import canonical_values, check_doc, main


def _write_metadata(
    tmp_path: Path,
    *,
    raw_total: int = 1234,
    years: tuple[int, ...] = (2020, 2021, 2022),
    coverage_rate: float = 0.876,
) -> Path:
    metadata_dir = tmp_path / "metadata"
    metadata_dir.mkdir()
    (metadata_dir / "data_metadata.json").write_text(
        json.dumps({"workshop_case_count_raw_total": raw_total, "years_processed": list(years)}),
        encoding="utf-8",
    )
    (metadata_dir / "classifier_validation.json").write_text(
        json.dumps({"coverage": {"rate": coverage_rate}}), encoding="utf-8"
    )
    return metadata_dir


def test_canonical_values_computed_from_synthetic_metadata(tmp_path):
    metadata_dir = _write_metadata(tmp_path)
    values = canonical_values(metadata_dir)
    assert values["workshop_case_count"] == "1,234"
    assert values["year_range"] == "2020–2022"
    assert values["latest_year"] == "2022"
    assert values["classification_coverage_pct"] == "87.6%"


def test_canonical_values_thousands_separator_for_large_counts(tmp_path):
    metadata_dir = _write_metadata(tmp_path, raw_total=63425)
    values = canonical_values(metadata_dir)
    assert values["workshop_case_count"] == "63,425"


def test_check_doc_reports_no_missing_values_when_all_present(tmp_path):
    metadata_dir = _write_metadata(tmp_path)
    values = canonical_values(metadata_dir)
    doc = tmp_path / "doc.md"
    doc.write_text("1,234 cases, 2020–2022, latest year 2022, coverage 87.6%", encoding="utf-8")
    assert check_doc(doc, values) == []


def test_check_doc_reports_each_missing_value_by_name(tmp_path):
    metadata_dir = _write_metadata(tmp_path)
    values = canonical_values(metadata_dir)
    doc = tmp_path / "doc.md"
    doc.write_text("nothing relevant here", encoding="utf-8")
    missing = check_doc(doc, values)
    assert set(missing) == set(values)


def test_main_exits_zero_when_all_docs_contain_all_values(tmp_path):
    metadata_dir = _write_metadata(tmp_path)
    doc = tmp_path / "doc.md"
    doc.write_text("1,234 cases, 2020–2022, latest year 2022, coverage 87.6%", encoding="utf-8")
    assert main([str(doc), "--metadata-dir", str(metadata_dir)]) == 0


def test_main_exits_nonzero_and_names_missing_values_on_stderr(tmp_path, capsys):
    metadata_dir = _write_metadata(tmp_path)
    doc = tmp_path / "doc.md"
    doc.write_text("nothing relevant here", encoding="utf-8")
    exit_code = main([str(doc), "--metadata-dir", str(metadata_dir)])
    captured = capsys.readouterr()
    assert exit_code != 0
    assert "workshop_case_count" in captured.err
    assert "year_range" in captured.err
    assert "latest_year" in captured.err
    assert "classification_coverage_pct" in captured.err


def test_main_checks_multiple_docs_independently(tmp_path, capsys):
    metadata_dir = _write_metadata(tmp_path)
    complete_doc = tmp_path / "complete.md"
    complete_doc.write_text(
        "1,234 cases, 2020–2022, latest year 2022, coverage 87.6%", encoding="utf-8"
    )
    incomplete_doc = tmp_path / "incomplete.md"
    incomplete_doc.write_text("nothing relevant here", encoding="utf-8")

    exit_code = main([str(complete_doc), str(incomplete_doc), "--metadata-dir", str(metadata_dir)])
    captured = capsys.readouterr()
    assert exit_code != 0
    assert str(complete_doc) not in captured.err
    assert str(incomplete_doc) in captured.err
