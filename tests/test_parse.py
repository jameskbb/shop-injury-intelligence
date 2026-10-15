"""Parser hardening: every verified NEISS framing/encoding quirk, plus loud parse-failure aborts."""

from __future__ import annotations

import json

import pytest

from tests.conftest import write_neiss_tsv
from wii.data.parse import parse_year_file
from wii.data.schema import RAW_COLUMNS, HeaderDriftError, MalformedRecordError, TypeCoercionError
from wii.data.sync import main

_NARRATIVE_IDX = RAW_COLUMNS.index("Narrative_1")
_OTHER_DIAGNOSIS_IDX = RAW_COLUMNS.index("Other_Diagnosis")


def _split_line(fields: list[str], split_at: int) -> tuple[str, str]:
    """Split a complete field list into two raw physical-line strings at field index split_at,
    breaking the narrative field's text into two halves joined at the split point."""
    return "\t".join(fields[:split_at]), "\t".join(fields[split_at:])


def test_rejoin_zero_blank_split(tmp_path, neiss_record):
    """Mirrors the 2023 shape: split mid-narrative, no blank lines between fragments."""
    fields = neiss_record(Narrative_1="PART_ONE")
    line1 = "\t".join(fields[:_NARRATIVE_IDX] + ["PART_ONE"])
    line2 = "\t".join(["PART_TWO", *fields[_NARRATIVE_IDX + 1 :]])

    path = tmp_path / "neiss2024.tsv"
    write_neiss_tsv(path, [], raw_lines=[line1, line2])

    parsed = parse_year_file(path, 2024)
    assert len(parsed.rows) == 1
    assert len(parsed.rows[0]) == 25
    assert parsed.rows[0][_NARRATIVE_IDX] == "PART_ONE PART_TWO"
    assert parsed.rejoined_record_count == 1
    assert parsed.blank_line_count == 0


def test_rejoin_multi_blank_split(tmp_path, neiss_record):
    """Mirrors the 2013 shape: 21 empty physical lines between fragments."""
    fields = neiss_record(Narrative_1="PART_ONE")
    line1 = "\t".join(fields[:_NARRATIVE_IDX] + ["PART_ONE"])
    line2 = "\t".join(["PART_TWO", *fields[_NARRATIVE_IDX + 1 :]])
    blanks = [""] * 21

    path = tmp_path / "neiss2013.tsv"
    write_neiss_tsv(path, [], raw_lines=[line1, *blanks, line2])

    parsed = parse_year_file(path, 2013)
    assert len(parsed.rows) == 1
    assert len(parsed.rows[0]) == 25
    assert parsed.rows[0][_NARRATIVE_IDX] == "PART_ONE PART_TWO"
    assert parsed.rejoined_record_count == 1
    assert parsed.blank_line_count == 21
    # No record with an empty case_id was emitted.
    assert all(row[0] != "" for row in parsed.rows)


def test_rejoin_empty_first_field_completion(tmp_path, neiss_record):
    """Mirrors the 2010 shape: the completion line's first field is empty; still 25 fields."""
    fields = neiss_record()
    # Everything through PSU (index 23) is complete on line 1; Weight (index 24) is missing.
    line1 = "\t".join(fields[:24])
    line2 = "\t".join(["", fields[24]])

    path = tmp_path / "neiss2010.tsv"
    write_neiss_tsv(path, [], raw_lines=[line1, line2])

    parsed = parse_year_file(path, 2010)
    assert len(parsed.rows) == 1
    assert len(parsed.rows[0]) == 25
    assert parsed.rejoined_record_count == 1


def test_eof_mid_record_raises(tmp_path, neiss_record):
    fields = neiss_record()
    line1 = "\t".join(fields[:20])  # short, never completed

    path = tmp_path / "neiss2024.tsv"
    write_neiss_tsv(path, [], raw_lines=[line1])

    with pytest.raises(MalformedRecordError):
        parse_year_file(path, 2024)


def test_overflow_raises(tmp_path, neiss_record):
    fields = neiss_record()
    line1 = "\t".join(fields[:20])
    line2 = "\t".join(["A", "B", "C", "D", "E", "F", "G"])  # way too many fields

    path = tmp_path / "neiss2024.tsv"
    write_neiss_tsv(path, [], raw_lines=[line1, line2])

    with pytest.raises(MalformedRecordError):
        parse_year_file(path, 2024)


def test_quoted_tab_stays_single_field(tmp_path, neiss_record):
    fields = neiss_record(Other_Diagnosis="LACERATION\tDEEP")
    line_fields = list(fields)
    line_fields[_OTHER_DIAGNOSIS_IDX] = f'"{line_fields[_OTHER_DIAGNOSIS_IDX]}"'
    line = "\t".join(line_fields)

    path = tmp_path / "neiss2024.tsv"
    write_neiss_tsv(path, [], raw_lines=[line])

    parsed = parse_year_file(path, 2024)
    assert len(parsed.rows) == 1
    assert len(parsed.rows[0]) == 25
    assert parsed.rows[0][_OTHER_DIAGNOSIS_IDX] == "LACERATION\tDEEP"


def test_cp1252_fallback_registered_symbol(tmp_path, neiss_record):
    fields = neiss_record(Narrative_1="INJURY MARK ® ON ARM")
    path = tmp_path / "neiss2024.tsv"
    write_neiss_tsv(path, [fields], encoding="cp1252")

    parsed = parse_year_file(path, 2024)
    assert parsed.encoding == "cp1252"
    assert parsed.cp1252_fallback is True
    assert len(parsed.rows) == 1
    assert "®" in parsed.rows[0][_NARRATIVE_IDX]


def test_header_drift_raises(tmp_path, neiss_record):
    bad_header = (*RAW_COLUMNS, "Extra_Column")
    fields = neiss_record()
    path = tmp_path / "neiss2024.tsv"
    write_neiss_tsv(path, [fields + ["x"]], header=bad_header)

    with pytest.raises(HeaderDriftError):
        parse_year_file(path, 2024)


def test_header_drift_aborts_pipeline(tmp_path, neiss_record):
    bad_header = (*RAW_COLUMNS[:-1], "Renamed_Weight")
    fields = neiss_record()
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    data_root = tmp_path / "data_root"
    write_neiss_tsv(raw_dir / "neiss2024.tsv", [fields], header=bad_header)

    exit_code = main(
        ["--years", "2024", "--offline", "--raw-dir", str(raw_dir), "--data-root", str(data_root)]
    )
    assert exit_code == 1

    report = json.loads((data_root / "metadata" / "qa_report.json").read_text())
    assert report["aborted"] is True
    assert (data_root / "metadata" / "qa_report.md").exists()
    assert not (data_root / "processed" / "fact_cases.parquet").exists()


def test_weight_abc_type_coercion_error(tmp_path, neiss_record):
    fields = neiss_record(Weight="abc")
    path = tmp_path / "neiss2024.tsv"
    write_neiss_tsv(path, [fields])

    parsed = parse_year_file(path, 2024)
    from wii.data.normalize import normalize_rows

    with pytest.raises(TypeCoercionError) as excinfo:
        normalize_rows(parsed)
    assert excinfo.value.year == 2024
    assert excinfo.value.column == "weight"
    assert excinfo.value.value == "abc"


def test_weight_abc_aborts_pipeline_with_detail(tmp_path, neiss_record):
    fields = neiss_record(Weight="abc")
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    data_root = tmp_path / "data_root"
    write_neiss_tsv(raw_dir / "neiss2024.tsv", [fields])

    exit_code = main(
        ["--years", "2024", "--offline", "--raw-dir", str(raw_dir), "--data-root", str(data_root)]
    )
    assert exit_code == 1

    report = json.loads((data_root / "metadata" / "qa_report.json").read_text())
    assert report["aborted"] is True
    no_unparseable = next(r for r in report["results"] if r["check_id"] == "no_unparseable_rows")
    assert no_unparseable["passed"] is False
    assert "weight" in no_unparseable["detail"]
    assert "abc" in no_unparseable["detail"]
    assert "2024" in no_unparseable["detail"]


def test_advisory_checks_never_abort(tmp_path, neiss_record):
    """rejoined_records and cp1252_fallback_years are flagged but never abort the run."""
    fields = neiss_record(Narrative_1="PART_ONE")
    line1 = "\t".join(fields[:_NARRATIVE_IDX] + ["PART_ONE"])
    line2 = "\t".join(["PART_TWO", *fields[_NARRATIVE_IDX + 1 :]])

    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    data_root = tmp_path / "data_root"
    write_neiss_tsv(raw_dir / "neiss2024.tsv", [], raw_lines=[line1, line2])

    exit_code = main(
        ["--years", "2024", "--offline", "--raw-dir", str(raw_dir), "--data-root", str(data_root)]
    )
    assert exit_code == 0

    report = json.loads((data_root / "metadata" / "qa_report.json").read_text())
    assert report["aborted"] is False
    rejoined = next(r for r in report["results"] if r["check_id"] == "rejoined_records")
    assert rejoined["category"] == "advisory"
    assert rejoined["passed"] is False  # flagged: 1 rejoined record occurred
    assert rejoined["count"] == 1
