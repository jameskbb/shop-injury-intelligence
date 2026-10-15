"""CLI entry point: detect/download official NEISS years, run the pipeline, report exit codes.

D-21/D-22/D-23: the network is only ever touched via an injectable Fetcher, constructed lazily
and only for modes that need it. --offline never opens a network connection.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from dataclasses import asdict
from enum import IntEnum
from pathlib import Path

from wii.data.download import (
    CurlCffiFetcher,
    DownloadError,
    Fetcher,
    LatestYearDetectionError,
    detect_latest_year,
    download_year,
    tsv_url,
)
from wii.data.pipeline import run_pipeline
from wii.utils.paths import DataPaths
from wii.utils.years import YearSpecError, parse_years_arg

_YEAR_FILE_RE = re.compile(r"^neiss(\d{4})\.tsv$")

# D-22: process the latest-detected year and the 19 years before it.
_WINDOW_SIZE = 20


class ExitCode(IntEnum):
    OK = 0
    QA_FAILED = 1
    USAGE = 2
    DATA_UNAVAILABLE = 3
    DETECTION_FAILED = 4
    NEW_YEAR_AVAILABLE = 10


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sync_neiss")
    parser.add_argument(
        "--years", help='Years to process: "2019:2025" (inclusive range) or "2019,2021" or "2024"'
    )
    parser.add_argument(
        "--latest",
        action="store_true",
        help="Detect the latest available year and process the full latest-19..latest window",
    )
    parser.add_argument(
        "--refresh", action="store_true", help="Re-download years even if already cached locally"
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Never touch the network; use data/raw/ only",
    )
    parser.add_argument(
        "--check-latest",
        action="store_true",
        help=(
            "Print the latest available year and whether local data is current; "
            "exits 0 (current) or 10 (new year available)"
        ),
    )
    parser.add_argument("--raw-dir", help="Directory holding local neiss{YEAR}.tsv files")
    parser.add_argument("--data-root", help="Root directory for interim/processed/metadata output")
    return parser


def manual_fallback_instructions(years: list[int], raw_dir: Path) -> str:
    lines = ["Missing local NEISS raw file(s). Manual download required:"]
    for year in years:
        lines.append(f"  Year {year}:")
        lines.append(f"    URL: {tsv_url(year)}")
        lines.append(f"    Expected filename: neiss{year}.tsv")
        lines.append(f"    Destination: {raw_dir.resolve()}")
    lines.append("Place the file(s) at the destination above, then rerun with --offline.")
    return "\n".join(lines)


def _years_present_in_raw_dir(raw_dir: Path) -> list[int]:
    if not raw_dir.is_dir():
        return []
    years: list[int] = []
    for p in raw_dir.iterdir():
        if not p.is_file():
            continue
        m = _YEAR_FILE_RE.match(p.name)
        if m:
            years.append(int(m.group(1)))
    return sorted(years)


def _local_latest_year(data_metadata_path: Path) -> int | None:
    if not data_metadata_path.is_file():
        return None
    try:
        payload = json.loads(data_metadata_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    years = payload.get("years_processed") or []
    if not years:
        return None
    return max(int(y) for y in years)


def _resolve_offline(years: list[int], raw_dir: Path) -> tuple[dict[int, Path], list[int]]:
    year_files: dict[int, Path] = {}
    missing: list[int] = []
    for year in years:
        candidate = raw_dir / f"neiss{year}.tsv"
        if candidate.is_file():
            year_files[year] = candidate
        else:
            missing.append(year)
    return year_files, missing


def _resolve_online(
    years: list[int], raw_dir: Path, fetcher: Fetcher, *, refresh: bool
) -> tuple[dict[int, Path], list[int]]:
    year_files: dict[int, Path] = {}
    failed: list[int] = []
    for year in years:
        try:
            outcome = download_year(fetcher, year, raw_dir, refresh=refresh)
            year_files[year] = outcome.path
        except DownloadError:
            failed.append(year)
    return year_files, failed


def main(
    argv: list[str] | None = None,
    *,
    fetcher: Fetcher | None = None,
    today: datetime.date | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.offline and (args.latest or args.check_latest or args.refresh):
        print(
            "error: --offline cannot be combined with --latest, --check-latest, or --refresh",
            file=sys.stderr,
        )
        return int(ExitCode.USAGE)
    if args.years and args.latest:
        print("error: --years cannot be combined with --latest", file=sys.stderr)
        return int(ExitCode.USAGE)

    today_date = today if today is not None else datetime.date.today()
    raw_dir = Path(args.raw_dir) if args.raw_dir else Path("data/raw")
    data_root = Path(args.data_root) if args.data_root else Path("data")
    paths = DataPaths.from_root(data_root, raw_dir=raw_dir)

    def _fetcher() -> Fetcher:
        nonlocal fetcher
        if fetcher is None:
            fetcher = CurlCffiFetcher()
        return fetcher

    if args.check_latest:
        try:
            detection = detect_latest_year(_fetcher(), today_date)
        except LatestYearDetectionError as e:
            print(f"error: {e}", file=sys.stderr)
            return int(ExitCode.DETECTION_FAILED)
        local_latest = _local_latest_year(paths.metadata / "data_metadata.json")
        current = local_latest is not None and local_latest >= detection.year
        status = "current" if current else "new_year_available"
        local_str = str(local_latest) if local_latest is not None else "none"
        print(f"latest_available={detection.year} local_latest={local_str} status={status}")
        return int(ExitCode.OK) if current else int(ExitCode.NEW_YEAR_AVAILABLE)

    latest_year_detection: dict | None = None

    if args.latest:
        try:
            detection = detect_latest_year(_fetcher(), today_date)
        except LatestYearDetectionError as e:
            print(f"error: {e}", file=sys.stderr)
            return int(ExitCode.DETECTION_FAILED)
        latest_year_detection = asdict(detection)
        window_years = list(range(detection.year - (_WINDOW_SIZE - 1), detection.year + 1))
        year_files, missing_or_failed = _resolve_online(
            window_years, paths.raw, _fetcher(), refresh=args.refresh
        )
    elif args.years:
        try:
            years = parse_years_arg(args.years, today=today_date)
        except YearSpecError as e:
            print(f"error: {e}", file=sys.stderr)
            return int(ExitCode.USAGE)
        if args.offline:
            year_files, missing_or_failed = _resolve_offline(years, paths.raw)
        else:
            year_files, missing_or_failed = _resolve_online(
                years, paths.raw, _fetcher(), refresh=args.refresh
            )
    else:
        if args.offline:
            latest_year_detection = {"method": "skipped_offline"}
            target_years = _years_present_in_raw_dir(paths.raw)
            year_files, missing_or_failed = _resolve_offline(target_years, paths.raw)
        else:
            try:
                detection = detect_latest_year(_fetcher(), today_date)
                latest_year_detection = asdict(detection)
                window_years = range(detection.year - (_WINDOW_SIZE - 1), detection.year + 1)
                target_years = sorted(set(window_years) | set(_years_present_in_raw_dir(paths.raw)))
                year_files, missing_or_failed = _resolve_online(
                    target_years, paths.raw, _fetcher(), refresh=args.refresh
                )
            except LatestYearDetectionError as e:
                print(f"warning: latest-year detection failed: {e}", file=sys.stderr)
                latest_year_detection = {"method": "failed", "error": str(e)}
                target_years = _years_present_in_raw_dir(paths.raw)
                year_files, missing_or_failed = _resolve_offline(target_years, paths.raw)

    if missing_or_failed:
        print(manual_fallback_instructions(sorted(missing_or_failed), paths.raw), file=sys.stderr)
        return int(ExitCode.DATA_UNAVAILABLE)

    if not year_files:
        # Never run the pipeline on zero years: an empty build would pass QA vacuously and
        # replace the committed release with empty tables (e.g. --offline on a fresh clone).
        print(
            f"error: no NEISS annual files found in {paths.raw}; nothing to process. "
            "Download them with --latest or --years, or place neiss{YEAR}.tsv files there.",
            file=sys.stderr,
        )
        return int(ExitCode.DATA_UNAVAILABLE)

    run_context = (
        {"latest_year_detection": latest_year_detection} if latest_year_detection else None
    )
    result = run_pipeline(year_files, paths, run_context=run_context)

    print(
        f"Processed {result.workshop_case_count} raw sample cases "
        f"across {len(result.years_processed)} year(s)."
    )

    if result.report.aborted:
        print("QA FAILED - see qa_report.md for details", file=sys.stderr)
        return int(ExitCode.QA_FAILED)

    return int(ExitCode.OK)
