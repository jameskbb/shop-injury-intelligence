"""Unit tests for src/wii/data/refresh_gate.py (D-04/D-05, OPS-03).

`check` tests inject a fake runner (never touch wii.data.sync/the network). `cache-keys`/`verify`
tests build tiny synthetic data/metadata/*.json fixtures in tmp_path -- never real CPSC files.
"""

from __future__ import annotations

import json
from pathlib import Path

from wii.data.refresh_gate import build_parser, cmd_cache_keys, cmd_verify, main
from wii.data.sync import _WINDOW_SIZE, ExitCode

# --- check --------------------------------------------------------------------------------


def test_check_ok_writes_status_current(tmp_path, monkeypatch):
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))

    def runner(argv):
        print("latest_available=2025 local_latest=2025 status=current")
        return int(ExitCode.OK)

    code = main(["check"], runner=runner)

    assert code == 0
    lines = out.read_text().splitlines()
    assert "status=current" in lines
    assert "latest_year=2025" in lines


def test_check_new_year_writes_status_new_year(tmp_path, monkeypatch):
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))

    def runner(argv):
        print("latest_available=2026 local_latest=2025 status=new_year_available")
        return int(ExitCode.NEW_YEAR_AVAILABLE)

    code = main(["check"], runner=runner)

    assert code == 0
    lines = out.read_text().splitlines()
    assert "status=new_year" in lines
    assert "latest_year=2026" in lines


def test_check_detection_failure_returns_nonzero_and_names_code(capsys):
    def runner(argv):
        return int(ExitCode.DETECTION_FAILED)

    code = main(["check"], runner=runner)

    assert code == int(ExitCode.DETECTION_FAILED)
    err = capsys.readouterr().err
    assert "4" in err


def test_check_new_year_without_latest_available_line_is_nonzero(capsys):
    def runner(argv):
        print("some unexpected output with no latest_available token")
        return int(ExitCode.NEW_YEAR_AVAILABLE)

    code = main(["check"], runner=runner)

    assert code != 0
    err = capsys.readouterr().err
    assert "latest_available" in err


def test_check_help_lists_check_subcommand():
    help_text = build_parser().format_help()
    assert "check" in help_text


# --- cache-keys -----------------------------------------------------------------------------


def _write_metadata(metadata_dir: Path, years: dict[str, dict]) -> None:
    metadata_dir.mkdir(parents=True, exist_ok=True)
    (metadata_dir / "data_metadata.json").write_text(json.dumps({"years": years}))


def test_cache_keys_restore_emits_window_size_slots(tmp_path, capsys):
    metadata_dir = tmp_path / "metadata"
    years = {str(y): {"raw_file_sha256": f"sha{y}"} for y in range(2025 - _WINDOW_SIZE + 1, 2026)}
    _write_metadata(metadata_dir, years)

    code = cmd_cache_keys("restore", 2025, metadata_dir)

    out = capsys.readouterr().out
    restore_keys = [line for line in out.splitlines() if line.startswith("restore_key_")]
    assert code == 0
    assert len(restore_keys) == _WINDOW_SIZE
    assert all(v for _, v in (line.split("=", 1) for line in restore_keys))


def test_cache_keys_restore_new_year_slot_gets_empty_key(tmp_path, capsys):
    metadata_dir = tmp_path / "metadata"
    # Committed metadata only covers through 2025; latest-year 2026 is new.
    years = {str(y): {"raw_file_sha256": f"sha{y}"} for y in range(2006, 2026)}
    _write_metadata(metadata_dir, years)

    code = cmd_cache_keys("restore", 2026, metadata_dir)

    out = capsys.readouterr().out
    lines = out.splitlines()
    assert code == 0
    restore_keys = {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in lines
        if line.startswith("restore_key_")
    }
    assert len(restore_keys) == _WINDOW_SIZE
    empty = [k for k, v in restore_keys.items() if v == ""]
    non_empty = [k for k, v in restore_keys.items() if v != ""]
    assert len(empty) == 1
    assert len(non_empty) == _WINDOW_SIZE - 1


def test_cache_keys_save_requires_every_slot_year_present(tmp_path, capsys):
    metadata_dir = tmp_path / "metadata"
    # Missing 2006 (the oldest slot for latest-year 2025) from fresh metadata.
    years = {str(y): {"raw_file_sha256": f"sha{y}"} for y in range(2007, 2026)}
    _write_metadata(metadata_dir, years)

    code = cmd_cache_keys("save", 2025, metadata_dir)

    assert code != 0
    err = capsys.readouterr().err
    assert "2006" in err


def test_cache_keys_save_emits_window_size_slots_when_all_present(tmp_path, capsys):
    metadata_dir = tmp_path / "metadata"
    years = {str(y): {"raw_file_sha256": f"sha{y}"} for y in range(2025 - _WINDOW_SIZE + 1, 2026)}
    _write_metadata(metadata_dir, years)

    code = cmd_cache_keys("save", 2025, metadata_dir)

    out = capsys.readouterr().out
    save_keys = [line for line in out.splitlines() if line.startswith("save_key_")]
    assert code == 0
    assert len(save_keys) == _WINDOW_SIZE


def test_cache_keys_slot_key_format_matches_expected_pattern(tmp_path, capsys):
    metadata_dir = tmp_path / "metadata"
    years = {"2025": {"raw_file_sha256": "deadbeef"}}
    _write_metadata(metadata_dir, years)

    cmd_cache_keys("restore", 2025, metadata_dir)

    out = capsys.readouterr().out
    lines = {
        line.split("=", 1)[0]: line.split("=", 1)[1] for line in out.splitlines() if "=" in line
    }
    assert lines[f"restore_key_{_WINDOW_SIZE:02d}"] == "neiss-raw-2025-deadbeef"
    assert lines[f"restore_path_{_WINDOW_SIZE:02d}"] == "data/raw/neiss2025.tsv"


def test_cache_keys_rejects_non_four_digit_latest_year_via_cli(tmp_path, capsys):
    metadata_dir = tmp_path / "metadata"
    metadata_dir.mkdir()

    code = main(
        [
            "cache-keys",
            "--mode",
            "restore",
            "--latest-year",
            "25",
            "--metadata-dir",
            str(metadata_dir),
        ]
    )

    assert code != 0
    assert "four digits" in capsys.readouterr().err


# --- verify -----------------------------------------------------------------------------------


def _write_metadata_and_qa(
    tmp_path: Path,
    *,
    aborted: bool,
    method: str,
    years_processed: list[int],
) -> Path:
    metadata_dir = tmp_path / "metadata"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    (metadata_dir / "data_metadata.json").write_text(
        json.dumps(
            {
                "latest_year_detection": {"method": method},
                "years_processed": years_processed,
            }
        )
    )
    (metadata_dir / "qa_report.json").write_text(json.dumps({"aborted": aborted}))
    return metadata_dir


def test_verify_passes_on_healthy_release(tmp_path):
    years_processed = list(range(2025 - _WINDOW_SIZE + 1, 2026))
    metadata_dir = _write_metadata_and_qa(
        tmp_path, aborted=False, method="dropdown", years_processed=years_processed
    )

    assert cmd_verify(2025, metadata_dir) == 0


def test_verify_fails_when_qa_aborted(tmp_path, capsys):
    years_processed = list(range(2025 - _WINDOW_SIZE + 1, 2026))
    metadata_dir = _write_metadata_and_qa(
        tmp_path, aborted=True, method="dropdown", years_processed=years_processed
    )

    assert cmd_verify(2025, metadata_dir) != 0
    assert "aborted" in capsys.readouterr().err


def test_verify_fails_when_detection_method_failed(tmp_path, capsys):
    years_processed = list(range(2025 - _WINDOW_SIZE + 1, 2026))
    metadata_dir = _write_metadata_and_qa(
        tmp_path, aborted=False, method="failed", years_processed=years_processed
    )

    assert cmd_verify(2025, metadata_dir) != 0
    assert "latest_year_detection.method" in capsys.readouterr().err


def test_verify_fails_when_window_does_not_end_at_latest_year(tmp_path, capsys):
    years_processed = list(range(2024 - _WINDOW_SIZE + 1, 2025))  # ends at 2024, not 2025
    metadata_dir = _write_metadata_and_qa(
        tmp_path, aborted=False, method="dropdown", years_processed=years_processed
    )

    assert cmd_verify(2025, metadata_dir) != 0
    assert "years_processed max" in capsys.readouterr().err


def test_verify_fails_when_window_length_is_not_window_size(tmp_path, capsys):
    years_processed = list(range(2025 - _WINDOW_SIZE + 2, 2026))  # one year short
    metadata_dir = _write_metadata_and_qa(
        tmp_path, aborted=False, method="dropdown", years_processed=years_processed
    )

    assert cmd_verify(2025, metadata_dir) != 0
    assert "years_processed has" in capsys.readouterr().err


def test_verify_fails_cleanly_on_non_numeric_years_processed_entry(tmp_path, capsys):
    # WR-01: a malformed/partially-written metadata file with a non-numeric entry must fail the
    # gate cleanly (one reason per failed condition), not crash with an unhandled ValueError.
    years_processed = [*range(2025 - _WINDOW_SIZE + 2, 2026), "not-a-year"]
    metadata_dir = _write_metadata_and_qa(
        tmp_path, aborted=False, method="dropdown", years_processed=years_processed
    )

    code = cmd_verify(2025, metadata_dir)

    assert code == 1
    err = capsys.readouterr().err
    assert "non-numeric" in err
    assert "Traceback" not in err


def test_verify_latest_year_arg_rejects_non_four_digit_values(tmp_path, capsys):
    metadata_dir = tmp_path / "metadata"
    metadata_dir.mkdir()

    code = main(["verify", "--latest-year", "25", "--metadata-dir", str(metadata_dir)])

    assert code != 0
    assert "four digits" in capsys.readouterr().err
