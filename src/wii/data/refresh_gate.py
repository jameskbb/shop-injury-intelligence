"""OPS-03/D-04/D-05: turns `wii.data.sync`'s `--check-latest` exit-code contract into GitHub
Actions workflow outputs, per-year raw-file cache keys (D-05), and a publish-safety verification
gate (no silent partial publish, D-04/D-05).

`check` never downloads anything itself -- it calls an injectable runner (default:
`wii.data.sync.main(["--check-latest"])`) and maps its exit code + printed
`latest_available=YYYY` line to `status`/`latest_year` GITHUB_OUTPUT keys. `cache-keys` and
`verify` are pure functions over committed `data/metadata/*.json` -- no network, no subprocess.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path

from wii.data.sync import _WINDOW_SIZE, ExitCode
from wii.data.sync import main as sync_main

_LATEST_AVAILABLE_RE = re.compile(r"latest_available=(\d{4})\b")
_YEAR_RANGE = range(2006, 2101)  # generous forward bound; four-digit + sane lower bound only

_README_POINTER = "See README.md 'Refreshing NEISS data' for the local/manual refresh path."

CheckRunner = Callable[[list[str]], int]


def _write_outputs(pairs: dict[str, str]) -> None:
    """Append key=value lines to $GITHUB_OUTPUT (if set) and always print them."""
    lines = [f"{key}={value}" for key, value in pairs.items()]
    for line in lines:
        print(line)
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a") as fh:
            for line in lines:
                fh.write(line + "\n")


def _default_runner(argv: list[str]) -> int:
    return sync_main(argv)


def _capture_stdout(fn: Callable[[], int]) -> tuple[int, str]:
    """Run fn(), capturing and echoing its stdout, returning (exit_code, stdout_text)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = fn()
    text = buf.getvalue()
    if text:
        sys.stdout.write(text)
    return code, text


def cmd_check(runner: CheckRunner | None = None) -> int:
    """Run --check-latest (via `runner`, default wii.data.sync.main) and map its exit code +
    printed latest_available=YYYY line to status/latest_year GITHUB_OUTPUT keys.
    """
    run = runner if runner is not None else _default_runner
    code, stdout_text = _capture_stdout(lambda: run(["--check-latest"]))

    if code not in (int(ExitCode.OK), int(ExitCode.NEW_YEAR_AVAILABLE)):
        print(
            f"error: refresh_gate check: --check-latest exited {code} (expected 0 or 10). "
            f"{_README_POINTER}",
            file=sys.stderr,
        )
        return code

    match = _LATEST_AVAILABLE_RE.search(stdout_text)
    if match is None:
        print(
            "error: refresh_gate check: no latest_available=YYYY line found in --check-latest "
            f"output. {_README_POINTER}",
            file=sys.stderr,
        )
        return 2
    latest_year = int(match.group(1))
    if latest_year not in _YEAR_RANGE:
        print(
            f"error: refresh_gate check: latest_available year {latest_year} out of sane range. "
            f"{_README_POINTER}",
            file=sys.stderr,
        )
        return 2

    status = "current" if code == int(ExitCode.OK) else "new_year"
    _write_outputs({"status": status, "latest_year": str(latest_year)})
    return 0


def _load_metadata(metadata_dir: Path) -> dict:
    path = metadata_dir / "data_metadata.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text())


def _validate_latest_year_arg(value: str) -> int:
    if not re.fullmatch(r"\d{4}", value):
        raise ValueError(f"--latest-year must be four digits, got {value!r}")
    return int(value)


def cmd_cache_keys(mode: str, latest_year: int, metadata_dir: Path) -> int:
    """D-05: per-year raw-file cache keys.

    restore mode reads committed metadata (the year known before download); a slot whose year
    isn't in committed metadata yet (the new year) gets an empty restore_key_NN. save mode reads
    freshly-written metadata; every slot's year must be present there (error otherwise).
    """
    metadata = _load_metadata(metadata_dir)
    years_meta: dict = metadata.get("years", {})

    for i in range(1, _WINDOW_SIZE + 1):
        slot = f"{i:02d}"
        year = latest_year - _WINDOW_SIZE + i
        year_key = str(year)
        path_out = f"data/raw/neiss{year}.tsv"
        year_entry = years_meta.get(year_key)

        if mode == "restore":
            if year_entry is None:
                _write_outputs({f"restore_path_{slot}": path_out, f"restore_key_{slot}": ""})
            else:
                sha = year_entry.get("raw_file_sha256", "")
                key = f"neiss-raw-{year}-{sha}"
                _write_outputs({f"restore_path_{slot}": path_out, f"restore_key_{slot}": key})
        elif mode == "save":
            if year_entry is None:
                print(
                    f"error: refresh_gate cache-keys --mode save: year {year} missing from "
                    f"freshly-written metadata (slot {slot})",
                    file=sys.stderr,
                )
                return 1
            sha = year_entry.get("raw_file_sha256", "")
            key = f"neiss-raw-{year}-{sha}"
            _write_outputs({f"save_path_{slot}": path_out, f"save_key_{slot}": key})
        else:
            print(f"error: refresh_gate cache-keys: unknown --mode {mode!r}", file=sys.stderr)
            return 2
    return 0


def _safe_int(value: object) -> int | None:
    """Parse `value` as an int, returning None instead of raising on non-numeric input."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def cmd_verify(latest_year: int, metadata_dir: Path) -> int:
    """D-04/D-05: publish-safety gate. Returns 0 only when QA did not abort, latest-year
    detection used a real method (not failed/skipped_offline), the processed window ends at
    latest_year, and the window has exactly _WINDOW_SIZE years -- one reason per failed condition.
    """
    metadata = _load_metadata(metadata_dir)
    qa_path = metadata_dir / "qa_report.json"
    qa_report = json.loads(qa_path.read_text()) if qa_path.is_file() else {}

    reasons: list[str] = []

    if qa_report.get("aborted", True):
        reasons.append("qa_report.json aborted is not false")

    detection_method = (metadata.get("latest_year_detection") or {}).get("method")
    if detection_method not in ("dropdown", "probe"):
        reasons.append(
            f"latest_year_detection.method is {detection_method!r}, expected dropdown or probe"
        )

    years_processed = metadata.get("years_processed") or []
    parsed_years = [_safe_int(y) for y in years_processed]
    if any(p is None for p in parsed_years):
        reasons.append(f"years_processed contains non-numeric entries: {years_processed!r}")
    valid_years = [p for p in parsed_years if p is not None]
    if not valid_years or max(valid_years) != latest_year:
        actual_max = max(valid_years, default=None)
        reasons.append(f"years_processed max is {actual_max!r}, expected {latest_year}")
    if len(years_processed) != _WINDOW_SIZE:
        reasons.append(f"years_processed has {len(years_processed)} years, expected {_WINDOW_SIZE}")

    if reasons:
        for reason in reasons:
            print(f"error: refresh_gate verify: {reason}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="refresh_gate")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check")

    keys_parser = sub.add_parser("cache-keys")
    keys_parser.add_argument("--mode", choices=("restore", "save"), required=True)
    keys_parser.add_argument("--latest-year", required=True)
    keys_parser.add_argument("--metadata-dir", default="data/metadata")

    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("--latest-year", required=True)
    verify_parser.add_argument("--metadata-dir", default="data/metadata")

    return parser


def main(argv: list[str] | None = None, *, runner: CheckRunner | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "check":
        return cmd_check(runner=runner)

    try:
        latest_year = _validate_latest_year_arg(args.latest_year)
    except ValueError as e:
        print(f"error: refresh_gate {args.command}: {e}", file=sys.stderr)
        return 2

    metadata_dir = Path(args.metadata_dir)
    if args.command == "cache-keys":
        return cmd_cache_keys(args.mode, latest_year, metadata_dir)
    if args.command == "verify":
        return cmd_verify(latest_year, metadata_dir)

    print(f"error: refresh_gate: unknown command {args.command!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
