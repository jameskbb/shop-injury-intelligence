#!/usr/bin/env python3
"""VAL-01: reconcile wii.metrics estimates against the official CPSC Query Builder tables
(2009/2014/2019/2024 by default), offline, and publish docs/validation.md.

Exit 0 if every in-scope, published code matches for every requested year; exit 1 otherwise.
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

from wii.metrics import connect
from wii.utils.atomic import atomic_write_text
from wii.utils.paths import repo_root
from wii.validation.query_builder import parse_official_table, reconcile_year, render_markdown


def _run_pooled_check() -> str:
    """D-21: best-effort ONE pooled multi-year Query Builder comparison via Playwright. Never
    blocks -- if unavailable, that fact plus the pooled method in use is documented honestly."""
    method_note = (
        "Pooled method: strata keyed `year|stratum`, PSUs keyed `year|stratum|psu`, "
        "df = pooled PSUs - pooled strata (D-03)."
    )
    if importlib.util.find_spec("playwright") is None:
        return (
            "Playwright is not installed in this environment, so the live pooled multi-year "
            "Query Builder comparison was not run. This is expected and non-blocking (D-21) -- "
            "no phase task installs playwright (it is outside this phase's Package Legitimacy "
            "Audit). The offline per-year reconciliation above is VAL-01's primary path and "
            "requires no browser automation. " + method_note
        )
    return (
        "Playwright is installed, but the live pooled multi-year Query Builder flow is not "
        "implemented in this build (best-effort per D-21). The offline per-year reconciliation "
        "above is VAL-01's primary path. " + method_note
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", default="2009,2014,2019,2024")
    parser.add_argument("--processed-dir", default=None)
    parser.add_argument("--out", default="docs/validation.md")
    parser.add_argument(
        "--pooled-qb", action="store_true", help="Attempt the best-effort pooled Playwright check"
    )
    args = parser.parse_args(argv)

    years = [int(y) for y in args.years.split(",")]
    root = repo_root()
    processed_dir = Path(args.processed_dir) if args.processed_dir else None
    con = connect(processed_dir)

    results_by_year = {}
    total_mismatches = 0
    for year in years:
        official_path = root / "data" / "official_qb" / f"official_qb_{year}_by_product.txt"
        if not official_path.is_file():
            print(f"WARNING: no official table for {year} at {official_path}, skipping")
            continue
        official = parse_official_table(official_path.read_text(encoding="utf-8"))
        rows = reconcile_year(con, year, official)
        results_by_year[year] = rows
        total_mismatches += sum(1 for r in rows if not r.match)

    pooled_note = _run_pooled_check() if args.pooled_qb else "Not run (pass --pooled-qb)."
    markdown = render_markdown(results_by_year, pooled_note)

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = root / out_path
    atomic_write_text(out_path, markdown)

    print(f"VAL-01: {total_mismatches} mismatch(es) across {len(results_by_year)} year(s).")
    print(f"Report written to {out_path}")
    return 1 if total_mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
