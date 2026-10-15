"""Unit tests for wii.data.sync's D-22 CLI: flags, exit codes, network-mode wiring.

Every test injects a FakeFetcher/RaisingFetcher -- the autouse socket guard stays active, and
none of these tests touch the network.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

from tests.conftest import FakeFetcher, RaisingFetcher, synthetic_year_bytes, write_neiss_tsv
from wii.data.download import HOME_URL, FetchResponse, tsv_url
from wii.data.sync import ExitCode, build_parser, main

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "home_aspx_ddl.html"
_TODAY = datetime.date(2026, 9, 14)


def _fixture_bytes() -> bytes:
    return _FIXTURE_PATH.read_bytes()


def _full_window_responses() -> dict[str, FetchResponse]:
    responses = {HOME_URL: FetchResponse(200, _fixture_bytes())}
    for year in range(2006, 2026):
        responses[tsv_url(year)] = FetchResponse(200, synthetic_year_bytes(year))
    return responses


# --- --help -----------------------------------------------------------------


def test_help_lists_all_flags():
    help_text = build_parser().format_help()
    for flag in (
        "--years",
        "--latest",
        "--refresh",
        "--offline",
        "--check-latest",
        "--raw-dir",
        "--data-root",
    ):
        assert flag in help_text


# --- usage errors -----------------------------------------------------------------


def test_offline_and_latest_is_usage_error():
    assert main(["--offline", "--latest"]) == int(ExitCode.USAGE)


def test_offline_and_check_latest_is_usage_error():
    assert main(["--offline", "--check-latest"]) == int(ExitCode.USAGE)


def test_offline_and_refresh_is_usage_error():
    assert main(["--offline", "--refresh", "--years", "2024"]) == int(ExitCode.USAGE)


def test_years_and_latest_is_usage_error():
    assert main(["--years", "2024", "--latest"]) == int(ExitCode.USAGE)


# --- --check-latest -----------------------------------------------------------------


def test_check_latest_current_when_metadata_covers_detected_year(tmp_path, capsys):
    data_root = tmp_path / "data"
    metadata_dir = data_root / "metadata"
    metadata_dir.mkdir(parents=True)
    (metadata_dir / "data_metadata.json").write_text(
        json.dumps({"years_processed": list(range(2006, 2026))})
    )
    fetcher = FakeFetcher({HOME_URL: FetchResponse(200, _fixture_bytes())})

    exit_code = main(
        ["--check-latest", "--data-root", str(data_root), "--raw-dir", str(tmp_path / "raw")],
        fetcher=fetcher,
        today=_TODAY,
    )

    captured = capsys.readouterr()
    assert exit_code == int(ExitCode.OK)
    assert "status=current" in captured.out
    assert "latest_available=2025" in captured.out


def test_check_latest_new_year_available_when_metadata_is_behind(tmp_path, capsys):
    data_root = tmp_path / "data"
    metadata_dir = data_root / "metadata"
    metadata_dir.mkdir(parents=True)
    (metadata_dir / "data_metadata.json").write_text(
        json.dumps({"years_processed": list(range(2006, 2025))})
    )
    fetcher = FakeFetcher({HOME_URL: FetchResponse(200, _fixture_bytes())})

    exit_code = main(
        ["--check-latest", "--data-root", str(data_root), "--raw-dir", str(tmp_path / "raw")],
        fetcher=fetcher,
        today=_TODAY,
    )

    captured = capsys.readouterr()
    assert exit_code == int(ExitCode.NEW_YEAR_AVAILABLE)
    assert "status=new_year_available" in captured.out


def test_check_latest_detection_failure_exits_4(tmp_path):
    fetcher = FakeFetcher({})  # HOME_URL and every probe year -> 404
    exit_code = main(
        [
            "--check-latest",
            "--data-root",
            str(tmp_path / "data"),
            "--raw-dir",
            str(tmp_path / "raw"),
        ],
        fetcher=fetcher,
        today=_TODAY,
    )
    assert exit_code == int(ExitCode.DETECTION_FAILED)


# --- --latest -----------------------------------------------------------------


def test_latest_with_fake_fetcher_downloads_full_20_year_window(tmp_path):
    raw_dir = tmp_path / "raw"
    data_root = tmp_path / "data"
    fetcher = FakeFetcher(_full_window_responses())

    exit_code = main(
        ["--latest", "--raw-dir", str(raw_dir), "--data-root", str(data_root)],
        fetcher=fetcher,
        today=_TODAY,
    )

    assert exit_code == int(ExitCode.OK)
    assert len(list(raw_dir.glob("neiss*.tsv"))) == 20
    metadata = json.loads((data_root / "metadata" / "data_metadata.json").read_text())
    assert metadata["latest_year_detection"]["method"] == "dropdown"
    assert metadata["latest_year_detection"]["year"] == 2025


def test_one_year_download_failure_exits_data_unavailable_with_details(tmp_path, capsys):
    raw_dir = tmp_path / "raw"
    data_root = tmp_path / "data"
    responses = _full_window_responses()
    del responses[tsv_url(2020)]  # 2020 becomes unreachable -> 404 -> DownloadError
    fetcher = FakeFetcher(responses)

    exit_code = main(
        ["--latest", "--raw-dir", str(raw_dir), "--data-root", str(data_root)],
        fetcher=fetcher,
        today=_TODAY,
    )

    captured = capsys.readouterr()
    assert exit_code == int(ExitCode.DATA_UNAVAILABLE)
    assert tsv_url(2020) in captured.err
    assert "neiss2020.tsv" in captured.err
    assert str(raw_dir.resolve()) in captured.err


# --- --years -----------------------------------------------------------------


def test_years_flag_downloads_missing_year_then_processes(tmp_path):
    raw_dir = tmp_path / "raw"
    data_root = tmp_path / "data"
    fetcher = FakeFetcher({tsv_url(2024): FetchResponse(200, synthetic_year_bytes(2024))})

    exit_code = main(
        ["--years", "2024", "--raw-dir", str(raw_dir), "--data-root", str(data_root)],
        fetcher=fetcher,
        today=_TODAY,
    )

    assert exit_code == int(ExitCode.OK)
    assert (raw_dir / "neiss2024.tsv").is_file()


# --- --offline -----------------------------------------------------------------


def test_offline_never_calls_fetcher_and_uses_local_files(tmp_path, neiss_record):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    data_root = tmp_path / "data"
    write_neiss_tsv(raw_dir / "neiss2024.tsv", [neiss_record(), neiss_record()])
    fetcher = RaisingFetcher()  # raises if ever called

    exit_code = main(
        ["--offline", "--raw-dir", str(raw_dir), "--data-root", str(data_root)],
        fetcher=fetcher,
        today=_TODAY,
    )

    assert exit_code == int(ExitCode.OK)


# --- default mode -----------------------------------------------------------------


def test_default_mode_detection_failure_falls_back_to_local_years_only(
    tmp_path, neiss_record, capsys
):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    data_root = tmp_path / "data"
    write_neiss_tsv(raw_dir / "neiss2024.tsv", [neiss_record(), neiss_record()])
    fetcher = FakeFetcher({})  # HOME_URL and every probe year -> 404 -> detection fails

    exit_code = main(
        ["--raw-dir", str(raw_dir), "--data-root", str(data_root)],
        fetcher=fetcher,
        today=_TODAY,
    )

    captured = capsys.readouterr()
    assert exit_code == int(ExitCode.OK)
    assert "latest-year detection failed" in captured.err
    metadata = json.loads((data_root / "metadata" / "data_metadata.json").read_text())
    assert metadata["latest_year_detection"]["method"] == "failed"
