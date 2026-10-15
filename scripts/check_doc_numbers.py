#!/usr/bin/env python3
"""Authoring-time README/docs number-provenance check (D-07, 06-06 Task 2).

Computes a small set of canonical values from committed metadata
(`data/metadata/data_metadata.json`, `data/metadata/classifier_validation.json`) and checks that
each one appears verbatim in every given doc. Exits non-zero and names each canonical value
missing from a given doc; exits zero when every doc contains every value.

This does not scan for OTHER numbers in the doc; it only enforces that the specific values it
computes are present in the exact format it computes them in, so the doc is provably grounded in
the same committed metadata a reader could check.

Usage:
    uv run python scripts/check_doc_numbers.py README.md [more-doc.md ...] \\
        [--metadata-dir data/metadata]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def canonical_values(metadata_dir: Path) -> dict[str, str]:
    """Compute the canonical, doc-ready values from a metadata directory."""
    data_metadata = json.loads((metadata_dir / "data_metadata.json").read_text(encoding="utf-8"))
    classifier_validation = json.loads(
        (metadata_dir / "classifier_validation.json").read_text(encoding="utf-8")
    )

    raw_total = data_metadata["workshop_case_count_raw_total"]
    years = data_metadata["years_processed"]
    coverage_rate = classifier_validation["coverage"]["rate"]

    return {
        "workshop_case_count": f"{raw_total:,}",
        "year_range": f"{min(years)}–{max(years)}",
        "latest_year": str(max(years)),
        "classification_coverage_pct": f"{coverage_rate * 100:.1f}%",
    }


def check_doc(doc_path: Path, values: dict[str, str]) -> list[str]:
    """Return the names of any canonical value missing (verbatim) from doc_path."""
    text = doc_path.read_text(encoding="utf-8")
    return [name for name, value in values.items() if value not in text]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("docs", nargs="+", type=Path, help="Markdown doc paths to check")
    parser.add_argument(
        "--metadata-dir",
        type=Path,
        default=Path("data/metadata"),
        help="Directory containing data_metadata.json and classifier_validation.json",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    values = canonical_values(args.metadata_dir)

    exit_code = 0
    for doc_path in args.docs:
        missing = check_doc(doc_path, values)
        if missing:
            exit_code = 1
            missing_desc = ", ".join(f"{name}={values[name]!r}" for name in missing)
            print(f"{doc_path}: missing canonical value(s): {missing_desc}", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
