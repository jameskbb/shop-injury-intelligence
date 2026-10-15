#!/usr/bin/env python3
"""NARR-06/D-20: measured classifier quality -- coverage, unknown %, confidence distribution,
rules-vs-llm counts, product-code vs narrative disagreements, subtype coverage, per-field
accuracy, and per-class precision/recall (holdout and all). Writes
data/metadata/classifier_validation.json, data/metadata/classifier_review_sample.csv, and
docs/classifier_validation.md. No hard accuracy target is enforced (D-20) -- exits 0 whenever it
completes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from wii.utils.atomic import atomic_write_text
from wii.utils.paths import repo_root
from wii.validation.classifier_eval import (
    build_validation_report,
    render_markdown,
    write_review_sample,
)


def _resolve(root: Path, value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else root / p


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processed-dir", default=None)
    parser.add_argument("--fixture", default=None)
    parser.add_argument("--out-json", default="data/metadata/classifier_validation.json")
    parser.add_argument("--out-sample", default="data/metadata/classifier_review_sample.csv")
    parser.add_argument("--out-md", default="docs/classifier_validation.md")
    args = parser.parse_args(argv)

    root = repo_root()
    processed_dir = Path(args.processed_dir) if args.processed_dir else root / "data" / "processed"
    fixture_path = (
        Path(args.fixture)
        if args.fixture
        else root / "tests" / "fixtures" / "narratives" / "labeled_validation.csv"
    )

    report, predicted_rows = build_validation_report(processed_dir, fixture_path)

    out_json = _resolve(root, args.out_json)
    atomic_write_text(out_json, json.dumps(report, indent=2))

    out_sample = _resolve(root, args.out_sample)
    write_review_sample(predicted_rows, out_sample)

    out_md = _resolve(root, args.out_md)
    atomic_write_text(out_md, render_markdown(report))

    print(
        f"NARR-06: coverage={report['coverage']['rate']:.1%}, "
        f"mechanism_unknown={report['unknown_pct']['mechanism']:.1f}%, "
        f"labeled_n={report['labeled']['all_n']} (holdout={report['labeled']['holdout_n']})"
    )
    print(f"Wrote {out_json}, {out_sample}, {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
