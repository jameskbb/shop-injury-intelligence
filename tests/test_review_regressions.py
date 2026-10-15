"""Regression tests for the Phase 1 code-review findings: empty runs must never replace the
committed release, and bytes that neither UTF-8 nor cp1252 can decode must surface as a recorded
structural QA failure instead of an uncaught traceback."""

from __future__ import annotations

import json

import polars as pl
import pytest

from tests.conftest import write_neiss_tsv
from wii.data.parse import parse_year_file
from wii.data.pipeline import run_pipeline
from wii.data.schema import MalformedRecordError
from wii.data.sync import ExitCode, main
from wii.utils.paths import DataPaths

# 0x81 is invalid as a UTF-8 lead byte and undefined in cp1252, so both decoders reject it.
_UNDECODABLE_BYTE = b"\x81"


def _seed_committed_release(data_root) -> pl.DataFrame:
    processed = data_root / "processed"
    processed.mkdir(parents=True)
    release = pl.DataFrame({"case_id": ["240000001"], "year": [2024]})
    release.write_parquet(processed / "fact_cases.parquet")
    return release


def test_offline_run_with_no_raw_files_exits_data_unavailable_and_keeps_release(tmp_path):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    data_root = tmp_path / "data_root"
    release = _seed_committed_release(data_root)

    exit_code = main(["--offline", "--raw-dir", str(raw_dir), "--data-root", str(data_root)])

    assert exit_code == int(ExitCode.DATA_UNAVAILABLE)
    kept = pl.read_parquet(data_root / "processed" / "fact_cases.parquet")
    assert kept.equals(release)


def test_pipeline_with_no_years_aborts_without_promoting(tmp_path):
    data_root = tmp_path / "data_root"
    release = _seed_committed_release(data_root)
    paths = DataPaths.from_root(data_root, raw_dir=tmp_path / "raw")

    result = run_pipeline({}, paths)

    assert result.report.aborted is True
    assert result.promoted is False
    failed = {r.check_id for r in result.report.results if not r.passed}
    assert "years_processed" in failed
    kept = pl.read_parquet(data_root / "processed" / "fact_cases.parquet")
    assert kept.equals(release)


def _write_file_with_undecodable_narrative(path, neiss_record) -> None:
    write_neiss_tsv(path, [neiss_record(Narrative_1="MARKERZZ")])
    content = path.read_bytes()
    path.write_bytes(content.replace(b"MARKERZZ", b"MARK" + _UNDECODABLE_BYTE + b"ZZ", 1))


def test_bytes_neither_utf8_nor_cp1252_raise_malformed_record(tmp_path, neiss_record):
    path = tmp_path / "neiss2024.tsv"
    _write_file_with_undecodable_narrative(path, neiss_record)

    with pytest.raises(MalformedRecordError) as excinfo:
        parse_year_file(path, 2024)
    assert excinfo.value.year == 2024


def test_undecodable_year_is_a_recorded_structural_failure_not_a_crash(tmp_path, neiss_record):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    data_root = tmp_path / "data_root"
    _write_file_with_undecodable_narrative(raw_dir / "neiss2024.tsv", neiss_record)

    exit_code = main(
        ["--years", "2024", "--offline", "--raw-dir", str(raw_dir), "--data-root", str(data_root)]
    )

    assert exit_code == int(ExitCode.QA_FAILED)
    report = json.loads((data_root / "metadata" / "qa_report.json").read_text())
    unparseable = next(r for r in report["results"] if r["check_id"] == "no_unparseable_rows")
    assert unparseable["passed"] is False
