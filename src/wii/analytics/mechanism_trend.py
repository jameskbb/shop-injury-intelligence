"""D-13 MECHANISM TREND consistency gate: pure, framework-free (no streamlit, no wii.narratives,
no SQL) gate + config loader, mirroring `wii.analytics.insights.load_thresholds`'s own read
pattern. A year's weighted primary-mechanism unknown share is only trustworthy for a per-year
trend comparison when it stays within `mechanism_trend_unknown_tolerance_pp` percentage points of
the pooled 2019-latest window's own unknown share -- a large year-to-year swing in "unknown"
usually means the narrative classifier behaved differently that year (a shorter narrative field,
a different phrasing mix that year's ER staff happened to write), not that mechanism labeling
genuinely shifted. Every threshold value lives in `insights.yaml` (`mechanism_trend_*` keys,
documented there) -- this module never repeats a numeric literal in a docstring/comment, so a
grep for the configured tolerance always finds exactly one source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "insights.yaml"
_REQUIRED_KEYS: tuple[str, ...] = (
    "mechanism_trend_unknown_tolerance_pp",
    "mechanism_trend_min_years",
    "mechanism_trend_top_n",
)

# Same float-epsilon rationale as wii.analytics.insights._ge: ordinary decimal fractions (e.g.
# 0.30 - 0.25) are not exactly representable in IEEE-754 double precision, so an "exactly at
# threshold" inclusive comparison needs a tiny tolerance to never spuriously fail.
_FLOAT_EPS = 1e-9


@dataclass(frozen=True)
class TrendGate:
    """The 3 `mechanism_trend_*` config values, read once via `load_trend_gate`."""

    tolerance_pp: float
    min_years: int
    top_n: int


@dataclass(frozen=True)
class GateResult:
    """`passing_years`/`excluded_years` are sorted tuples partitioning every year key given to
    `apply_consistency_gate`. `show_chart` is the single boolean the caller needs to decide
    between rendering the trend chart and showing the insufficient-years note."""

    passing_years: tuple[int, ...]
    excluded_years: tuple[int, ...]
    window_share: float | None
    show_chart: bool


def load_trend_gate(path: Path | str | None = None) -> TrendGate:
    """Reads the 3 `mechanism_trend_*` keys from `insights.yaml` (the SAME `_CONFIG_PATH`
    resolution `wii.analytics.insights.load_thresholds` uses) -- raises `ValueError` naming every
    missing key. Every OTHER key in the same file (the INS-01 `Thresholds` fields) is ignored,
    matching `load_thresholds`'s own tolerance of unrelated keys in the same document.
    """
    p = Path(path) if path is not None else _CONFIG_PATH
    with open(p, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    if not doc:
        raise ValueError(f"{p}: empty or invalid thresholds file")
    missing = [key for key in _REQUIRED_KEYS if key not in doc]
    if missing:
        raise ValueError(f"{p}: missing threshold keys {missing}")
    return TrendGate(
        tolerance_pp=float(doc["mechanism_trend_unknown_tolerance_pp"]),
        min_years=int(doc["mechanism_trend_min_years"]),
        top_n=int(doc["mechanism_trend_top_n"]),
    )


def apply_consistency_gate(
    unknown_by_year: dict[int, float | None],
    window_share: float | None,
    gate: TrendGate,
) -> GateResult:
    """A year passes when its unknown share is not `None` AND its absolute deviation from
    `window_share` (in percentage points) is `<= gate.tolerance_pp` (inclusive, `_FLOAT_EPS`
    tolerance). `show_chart` is `True` only when `window_share` is not `None` AND at least
    `gate.min_years` years pass -- a `None` window share (the pooled 2019-latest unknown share
    itself failed stability) means there is nothing to gate against, so no year can pass.
    """
    if window_share is None:
        return GateResult(
            passing_years=(),
            excluded_years=tuple(sorted(unknown_by_year)),
            window_share=None,
            show_chart=False,
        )

    passing: list[int] = []
    excluded: list[int] = []
    for year, share in unknown_by_year.items():
        if share is None:
            excluded.append(year)
            continue
        deviation_pp = abs(share - window_share) * 100
        if deviation_pp <= gate.tolerance_pp + _FLOAT_EPS:
            passing.append(year)
        else:
            excluded.append(year)

    passing.sort()
    excluded.sort()
    return GateResult(
        passing_years=tuple(passing),
        excluded_years=tuple(excluded),
        window_share=window_share,
        show_chart=len(passing) >= gate.min_years,
    )
