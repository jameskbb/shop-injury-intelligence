"""D-01: nothing outside wii.metrics may sum the `weight` column (allowlisting Phase 1's
src/wii/data/ modules, which build design_psu full_file_weight_sum and QA stats -- that is a
design-time artifact, not a survey-weighted statistic). wii.metrics must never import
wii.narratives (D-12: narratives is preprocessing-only, the metric layer never depends on it)."""

from __future__ import annotations

import re
from pathlib import Path

from wii.utils.paths import repo_root

# Phase 1's src/wii/data/ modules legitimately aggregate `weight` to build design_psu's
# full_file_weight_sum column and QA fill-rate stats -- this is the *design* metadata wii.metrics
# itself reads (design_psu), not a survey estimate. Everything else outside wii.metrics must not
# touch `weight` at all.
_ALLOWLISTED_DIRS = ("src/wii/data",)

_WEIGHT_SUM_PATTERN = re.compile(
    r"""
    (?:SUM\s*\(\s*weight\s*\))          # SQL SUM(weight)
    | (?:\.sum\s*\(\s*["']?weight["']?) # pandas/polars .sum("weight")
    | (?:weight["']?\s*\]\s*\.sum)      # df["weight"].sum()
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _iter_candidate_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for base in (root / "src" / "wii", root / "scripts"):
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            rel = path.relative_to(root).as_posix()
            if rel.startswith("src/wii/metrics/"):
                continue
            if any(rel.startswith(allow) for allow in _ALLOWLISTED_DIRS):
                continue
            files.append(path)
    return files


def test_no_weight_summing_outside_wii_metrics():
    root = repo_root()
    offenders = []
    for path in _iter_candidate_files(root):
        text = path.read_text(encoding="utf-8")
        if _WEIGHT_SUM_PATTERN.search(text):
            offenders.append(path.relative_to(root).as_posix())
    assert not offenders, f"weight-summing found outside wii.metrics (D-01 violation): {offenders}"


def test_wii_metrics_never_imports_wii_narratives():
    root = repo_root()
    metrics_dir = root / "src" / "wii" / "metrics"
    offenders = []
    for path in metrics_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"^\s*(from|import)\s+wii\.narratives", text, re.MULTILINE):
            offenders.append(path.relative_to(root).as_posix())
    assert not offenders, (
        f"wii.metrics imports wii.narratives (D-01/architecture violation): {offenders}"
    )
