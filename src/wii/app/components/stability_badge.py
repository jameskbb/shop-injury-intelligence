"""D-04/D-07 stability display: the ONLY module in src/wii/app that computes/derives suppression
state. `resolve()` calls `wii.metrics.stability.apply_suppression` itself on each row's raw
value/raw_n/cv -- it never reads a precomputed `stable`/`stable_annual` field (data.py's
`OverallEstimate` deliberately carries no such field; `wii.metrics.EstimateRecord`'s own
`.stable`/`.stable_annual` are equally ignored, even though they exist, for the SAME reason:
`apply_suppression` is pure and deterministic, so recomputing it here from the row's own
value/raw_n/cv reproduces `EstimateRecord`'s own classification exactly, while guaranteeing there
is exactly one call site for the CPSC suppression rule anywhere under src/wii/app.
`tests/app/test_app_boundaries.py::test_single_suppression_call_site` enforces that no other app
module calls `apply_suppression` or reads `stable_annual`; plain `stable` is not guarded because
share rows legitimately carry it (see `resolve_share` below). `resolve_share()` is the one
exception: share stability is a composite of two independent underlying totals (numerator AND
denominator each pass/fail the rule on their own), which `wii.metrics.severity.share_by` already
computes correctly and which cannot be re-derived from the share value alone -- so `resolve_share`
reads `wii.metrics`' own precomputed `stable`/`suppression_reason` fields for share rows only.

CPSC's own convention treats "too few sample cases" or "estimate too small to be reportable" as a
hard suppression (no number shown at all), while "the estimate/sample clears those floors but the
CV is still wide" is a softer "use with caution" tier that still shows a number:

- stable                                                      -> "stable"
- unstable, and (raw N < 20 OR estimate < 1,200 failed)        -> "suppressed" (no number shown)
- unstable, and ONLY the CV > 33% rule failed                  -> "limited" (number shown, flagged)
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal

import streamlit as st

from wii.metrics.stability import (
    EST_LT_1200,
    LIMITED_SAMPLE_LABEL,
    N_LT_20,
    SUPPRESSED_LABEL,
    apply_suppression,
)

STATE_STABLE = "stable"
STATE_LIMITED = "limited"
STATE_SUPPRESSED = "suppressed"
STATE_AGGREGATE = "aggregate"

_AGGREGATE_LABEL = "Combined flow: not individually reportable"

_HARD_SUPPRESS_REASONS = frozenset({EST_LT_1200, N_LT_20})

# `share_by` tags each reason with the side it came from ("num_est_lt_1200", "den_n_lt_20"), so a
# share's reasons never match the bare constants above. Stripping the side prefix before the
# membership test is what makes a share whose numerator fell under the CPSC floors classify as
# SUPPRESSED (no number) rather than LIMITED (a number, typically "0.0%") -- the defect behind a
# share reading "0.0% (Limited sample)" beside a suppressed estimate on the same strip.
_SHARE_SIDE_PREFIXES = ("num_", "den_")


def _bare_reason(reason: str) -> str:
    for prefix in _SHARE_SIDE_PREFIXES:
        if reason.startswith(prefix):
            return reason[len(prefix) :]
    return reason


def suppressed_share_row() -> dict[str, Any]:
    """Stand-in share row for a metric with no row in the current domain (e.g. a zero-case
    group): hard suppressed, never a zero share. A fresh dict per call so callers can't share
    mutable state."""
    return {
        "share": None,
        "share_ci_low": None,
        "share_ci_high": None,
        "stable": False,
        "suppression_reason": [N_LT_20],
        "num_raw_n": 0,
    }


Basis = Literal["annual_average", "period_total"]


@dataclass(frozen=True)
class ResolvedEstimate:
    """The single display-ready shape every KPI/chart/tooltip consumes."""

    state: str  # STATE_STABLE | STATE_LIMITED | STATE_SUPPRESSED
    value: float | None  # None only when suppressed
    display_value: str  # "Not reportable" when suppressed
    label: str  # "" when stable, else SUPPRESSED_LABEL / LIMITED_SAMPLE_LABEL
    raw_n: int
    ci_low: float | None
    ci_high: float | None


def _get(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row[key]
    return getattr(row, key)


def _classify(stable: bool, reasons: list[str]) -> str:
    if stable:
        return STATE_STABLE
    if any(_bare_reason(r) in _HARD_SUPPRESS_REASONS for r in reasons):
        return STATE_SUPPRESSED
    return STATE_LIMITED


def format_estimate(value: float) -> str:
    """Round to the nearest 100 (matches raw estimates' precision) with thousands separators."""
    rounded = int(round(value / 100.0)) * 100
    return f"{rounded:,}"


def format_share(value: float) -> str:
    """1-decimal percentage, e.g. 0.4567 -> '45.7%'."""
    return f"{value * 100:.1f}%"


def raw_n_label(raw_n: int) -> str:
    return f"{raw_n:,} sample cases (not a national count)"


def basis_caption(basis: Basis, start: int, end: int) -> str:
    if basis == "annual_average":
        return f"Estimated U.S. ER-treated injuries: annual average, {start}–{end}"
    if basis == "period_total":
        return f"Estimated U.S. ER-treated injuries during selected period, {start}–{end}"
    raise ValueError(f"unsupported basis={basis!r}")


def _finish(state: str, value: float | None, raw_n: int, ci_low, ci_high, fmt) -> ResolvedEstimate:
    if state == STATE_SUPPRESSED:
        return ResolvedEstimate(
            state=state,
            value=None,
            display_value="Not reportable",
            label=SUPPRESSED_LABEL,
            raw_n=raw_n,
            ci_low=None,
            ci_high=None,
        )
    label = LIMITED_SAMPLE_LABEL if state == STATE_LIMITED else ""
    return ResolvedEstimate(
        state=state,
        value=value,
        display_value=fmt(value),
        label=label,
        raw_n=raw_n,
        ci_low=ci_low,
        ci_high=ci_high,
    )


def resolve(row: Any, basis: Basis) -> ResolvedEstimate:
    """Basis-matched resolver (D-04): `basis="annual_average"` reads
    annual_average/ci_low_annual/ci_high_annual; `basis="period_total"` reads
    period_total/ci_low/ci_high. `cv` is basis-invariant (se/estimate is the same ratio whether
    scaled by n_years or not -- see wii.metrics.estimator._finish_record), so the SAME `cv` feeds
    `apply_suppression` for either basis; only `value` (and therefore the est<1,200 floor) can
    differ by basis, which is exactly how a row can be stable on one basis and not the other (not
    a bug -- see wii.metrics.estimator.EstimateRecord's own docstring).
    """
    if basis == "annual_average":
        value = _get(row, "annual_average")
        ci_low = _get(row, "ci_low_annual")
        ci_high = _get(row, "ci_high_annual")
    elif basis == "period_total":
        value = _get(row, "period_total")
        ci_low = _get(row, "ci_low")
        ci_high = _get(row, "ci_high")
    else:
        raise ValueError(f"unsupported basis={basis!r}")
    raw_n = _get(row, "raw_n")
    cv = _get(row, "cv")
    stable, reasons = apply_suppression(value, raw_n, cv)
    state = _classify(stable, reasons)
    return _finish(state, value, raw_n, ci_low, ci_high, format_estimate)


def resolve_aggregate(value: float, raw_n: int) -> ResolvedEstimate:
    """D-10/B2: the ONE stability rule for a Sankey "Other" bucket / merged aggregate link (the
    single suppression call site stays here, never duplicated in charts/sankey.py). An aggregate
    of disjoint `PairLink` point estimates has no single meaningful CV, so only the CPSC HARD
    floors gate it -- passing `cv=0.0` to `apply_suppression` makes the CV>0.33 check always pass,
    leaving raw N >= 20 AND estimate >= 1,200 as the only active rules. Never returns "limited":
    since `cv=0.0` can never add `CV_GT_033` to `reasons`, any failure is `EST_LT_1200` and/or
    `N_LT_20` -- both in `_HARD_SUPPRESS_REASONS` -- so the result is either drawn (STATE_AGGREGATE,
    no CI: a sum of disjoint cells has no single confidence interval) or hard-suppressed.
    """
    stable, _reasons = apply_suppression(value, raw_n, 0.0)
    if not stable:
        return ResolvedEstimate(
            state=STATE_SUPPRESSED,
            value=None,
            display_value="Not reportable",
            label=SUPPRESSED_LABEL,
            raw_n=raw_n,
            ci_low=None,
            ci_high=None,
        )
    return ResolvedEstimate(
        state=STATE_AGGREGATE,
        value=value,
        display_value=format_estimate(value),
        label=_AGGREGATE_LABEL,
        raw_n=raw_n,
        ci_low=None,
        ci_high=None,
    )


#: A share that formats to this under a non-stable label renders "Not reportable" instead.
#: "0.0% (Limited sample)" beside a real injury count reads as a contradiction rather than as a
#: small number. The digits claim a measured zero that the label simultaneously withdraws.
#: The text loses nothing: the stability label and the raw-N line still say a value exists and
#: is not reportable, which is the honest reading.
ZERO_SHARE_DISPLAY = "0.0%"


def resolve_share(row: Any) -> ResolvedEstimate:
    """Same resolver for `agg_tool_severity`/`share_by` rows (share, share_ci_low/high, stable,
    suppression_reason) -- shares have no separate period/annual basis.

    A non-stable share that formats to `ZERO_SHARE_DISPLAY` renders "Not reportable"
    instead of the digits: see that constant.
    """
    value = _get(row, "share")
    ci_low = _get(row, "share_ci_low")
    ci_high = _get(row, "share_ci_high")
    stable = _get(row, "stable")
    reasons = _get(row, "suppression_reason")
    try:
        raw_n = _get(row, "num_raw_n")
    except (KeyError, AttributeError):
        raw_n = _get(row, "raw_n")
    state = _classify(stable, reasons)
    resolved = _finish(state, value, raw_n, ci_low, ci_high, format_share)
    if state != STATE_STABLE and resolved.display_value == ZERO_SHARE_DISPLAY:
        return replace(resolved, display_value="Not reportable")
    return resolved


_STATE_ORDER = {STATE_STABLE: 0, STATE_LIMITED: 1, STATE_SUPPRESSED: 2}


def _rank_key(state: str, value: float | None) -> tuple[int, float]:
    """The single "stable first (desc by value), then limited, then suppressed" priority tuple
    (D-07) -- shared by `order_ranked` (raw rows, resolved via `resolve`/`basis`) and
    `order_resolved` (rows already resolved elsewhere, e.g. via `resolve_share`), so this rule
    lives in exactly one place."""
    return (_STATE_ORDER[state], -(value if value is not None else 0.0))


def order_ranked(rows: list[Any], basis: Basis) -> list[Any]:
    """Stable rows first (desc by value), then limited rows (desc by value), suppressed last --
    never interleaved (D-07).
    """
    resolved = [(row, resolve(row, basis)) for row in rows]
    resolved.sort(key=lambda item: _rank_key(item[1].state, item[1].value))
    return [row for row, _ in resolved]


def order_resolved(
    rows: list[dict], state_key: str = "state", value_key: str = "value"
) -> list[dict]:
    """Same stable-first-then-desc-by-value ordering as `order_ranked` (D-07), for rows that are
    already resolved (e.g. dicts built from `resolve_share`'s output) rather than raw rows needing
    `resolve(row, basis)`. Does not mutate `rows`."""
    return sorted(rows, key=lambda row: _rank_key(row[state_key], row[value_key]))


def empty_state(summary: str) -> None:
    """A filter combination matching zero cases: name the active filters and suggest how to
    widen them, instead of a blank chart or an exception.
    """
    st.info(
        f"No cases match the current filters ({summary}). Try widening the year range, "
        "switching to All ages, or clearing the advanced filters."
    )
