"""D-13/INS-01 deterministic insight engine.

Pure Python over metric-layer OUTPUT FRAMES only -- no streamlit import, no wii.narratives
import, no SQL. Every candidate is gated on: both compared cells STABLE (never limited or
suppressed) AND raw_n >= thresholds.min_raw_n (inclusive >=), evaluated on UNROUNDED values --
callers pass `ToolCell.value` as the raw float from `stability_badge.resolve`/`resolve_share`,
never a display-rounded string. Templates use "associated with"/"among ER cases" framing and
NEVER risk/danger/safer/caused-by language (docs/insights.md documents every threshold + the
wording guardrails; tests/analytics/test_insights.py scans every template and generated text for
the banned vocabulary).

Five INS-01 kinds:
- severity: a tool's share of a disposition-based outcome (escalated care, admitted, ...) vs the
  current-filter baseline share of that same outcome.
- disproportionate: a tool's within-tool share of a diagnosis/body-region/primary-mechanism
  (narrative-derived) category vs the current-filter baseline share of that category.
- burden_vs_share: a tool's share of total estimated burden vs its share of a specific severe
  outcome's current-filter total ("X% of burden but Y% of amputations").
- trend: latest-N-year vs prior-N-year annual average burden, gated on non-overlapping CIs.
- baseline_comparison: a tool's annual-average burden vs the mean across qualifying stable
  tools (a magnitude comparison, not a share).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from itertools import combinations, zip_longest
from pathlib import Path
from statistics import mean

import yaml

_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "insights.yaml"

GUARDRAIL_TERMS: tuple[str, ...] = ("risk", "danger", "dangerous", "safer", "caused by")


@dataclass(frozen=True)
class Insight:
    """One rendered insight statement. `numbers` is an ordered tuple of (label, value) pairs the
    template text is built from (insight_card.py renders these in IBM Plex Mono); `tool_id` is
    `None` for non-tool-scoped insights such as `trend`."""

    kind: str
    text: str
    tool_id: str | None
    numbers: tuple[tuple[str, float], ...]
    raw_n: int
    effect_size: float


@dataclass(frozen=True)
class Thresholds:
    min_raw_n: int
    share_ratio_min: float
    share_pp_min: float
    burden_outcome_ratio_min: float
    trend_pct_min: float
    trend_window_years: int
    trend_require_nonoverlapping_ci: bool
    max_cards: int
    min_cards_for_section: int
    narrative_min_confidence: float


def load_thresholds(path: Path | str | None = None) -> Thresholds:
    """Reads every insight threshold from `insights.yaml` (T-03-08) -- no finding text or number
    is hard-coded elsewhere in this module."""
    p = Path(path) if path is not None else _CONFIG_PATH
    with open(p, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    if not doc:
        raise ValueError(f"{p}: empty or invalid thresholds file")
    fields = Thresholds.__dataclass_fields__
    missing = [name for name in fields if name not in doc]
    if missing:
        raise ValueError(f"{p}: missing threshold keys {missing}")
    return Thresholds(**{name: doc[name] for name in fields})


@dataclass(frozen=True)
class ToolCell:
    """One resolved (basis-matched, stability-classified) value. `stable` is True only for
    `stability_badge.STATE_STABLE` -- limited and suppressed cells are equally ineligible for
    comparison (INS-01 prohibition: MUST NOT compare a basis-mismatched or limited/suppressed
    cell). `value` is the UNROUNDED float; callers must never pass a display-rounded number."""

    tool_id: str
    label: str
    value: float | None
    raw_n: int
    stable: bool
    ci_low: float | None = None
    ci_high: float | None = None


@dataclass(frozen=True)
class DimensionCategory:
    """One comparison dimension for the `severity`/`disproportionate` kinds: a named category
    (e.g. metric_id "amputation") with a current-filter baseline cell and a per-tool cell dict.
    `category_id` may carry a "mechanism:" prefix (e.g. "mechanism:flying_debris_fb") for the
    narrative-derived primary-mechanism disproportionate dimension -- the engine treats it like
    any other category_id, with no dimension-specific behavior."""

    category_id: str
    category_label: str
    baseline: ToolCell
    by_tool: dict[str, ToolCell]


@dataclass(frozen=True)
class InsightFrames:
    """Everything `generate_insights`/`compare_tools` need, pre-resolved to the active basis by
    the caller (`wii.app.data.insight_frames`). Never raw `wii.metrics` rows -- the caller has
    already applied `stability_badge.resolve`/`resolve_share` per basis, so this module stays
    framework-free and makes no suppression decisions of its own."""

    tool_burden: dict[str, ToolCell]
    total_burden: ToolCell | None
    severity: tuple[DimensionCategory, ...] = ()
    disproportionate: tuple[DimensionCategory, ...] = ()
    burden_outcomes: tuple[DimensionCategory, ...] = ()
    trend_recent: ToolCell | None = None
    trend_prior: ToolCell | None = None
    trend_scope_label: str = "Under the current filters,"
    # Peer membership always uses annual-average reportability, independent of display basis.
    peer_tool_burden: dict[str, ToolCell] = field(default_factory=dict)


def _raw_n_text(n: int) -> str:
    return f"{n:,} sample cases (not a national count)"


def _fmt_share(v: float) -> str:
    return f"{v * 100:.1f}%"


def _fmt_estimate(v: float) -> str:
    return f"{int(round(v / 100.0)) * 100:,}"


def _cells_qualify(*cells: ToolCell, min_raw_n: int) -> bool:
    return all(c.stable and c.value is not None and c.raw_n >= min_raw_n for c in cells)


# Binary floating-point cannot represent most decimal fractions exactly (0.30 / 0.20 evaluates to
# 1.4999999999999998, not 1.5) -- an "exactly at threshold" comparison built from ordinary decimal
# inputs would otherwise spuriously fail the documented inclusive `>=` rule (docs/insights.md).
# This tolerance is many orders of magnitude smaller than the gap used by every "one step below
# threshold" test case, so it never turns a genuine below-threshold value into a false pass.
_FLOAT_EPS = 1e-9


def _ge(value: float, threshold: float) -> bool:
    return value >= threshold - _FLOAT_EPS


def _share_vs_baseline(
    dimension: DimensionCategory, thresholds: Thresholds, kind: str
) -> list[Insight]:
    """Shared comparator for the `severity` and `disproportionate` kinds: a tool's share of
    `dimension` vs the current-filter baseline share, gated on ratio >= share_ratio_min AND
    pp difference >= share_pp_min (both inclusive >=, evaluated on unrounded values). Only the
    "higher than baseline" direction is reported -- this engine never emits a "lower"/"safer"
    framing (guardrail)."""
    baseline = dimension.baseline
    if not _cells_qualify(baseline, min_raw_n=thresholds.min_raw_n) or baseline.value == 0:
        return []
    out: list[Insight] = []
    for cell in dimension.by_tool.values():
        if not _cells_qualify(cell, min_raw_n=thresholds.min_raw_n):
            continue
        if cell.value < baseline.value:
            continue
        ratio = cell.value / baseline.value
        pp = (cell.value - baseline.value) * 100
        if _ge(ratio, thresholds.share_ratio_min) and _ge(pp, thresholds.share_pp_min):
            text = (
                f"{cell.label} shows a higher {dimension.category_label} than the current-filter "
                f"baseline: {_fmt_share(cell.value)} vs {_fmt_share(baseline.value)} among ER "
                f"cases, based on {_raw_n_text(cell.raw_n)}."
            )
            out.append(
                Insight(
                    kind=kind,
                    text=text,
                    tool_id=cell.tool_id,
                    numbers=(
                        (dimension.category_label, cell.value),
                        ("current-filter baseline", baseline.value),
                    ),
                    raw_n=cell.raw_n,
                    effect_size=ratio,
                )
            )
    return out


def _burden_vs_share_candidates(
    tool_burden: dict[str, ToolCell],
    total_burden: ToolCell | None,
    outcome: DimensionCategory,
    thresholds: Thresholds,
) -> list[Insight]:
    """`outcome.by_tool` here holds each tool's SHARE OF THE CURRENT-FILTER OUTCOME TOTAL (not a
    within-tool share) -- i.e. of all amputation cases, what fraction involve this tool -- built
    by the caller the same way as `outcome.baseline` (which is unused for this kind)."""
    if not total_burden or not _cells_qualify(total_burden, min_raw_n=thresholds.min_raw_n):
        return []
    out: list[Insight] = []
    for tool_id, outcome_cell in outcome.by_tool.items():
        burden_cell = tool_burden.get(tool_id)
        if burden_cell is None:
            continue
        if not _cells_qualify(burden_cell, outcome_cell, min_raw_n=thresholds.min_raw_n):
            continue
        burden_share = burden_cell.value / total_burden.value if total_burden.value else None
        if not burden_share:
            continue
        outcome_share = outcome_cell.value
        ratio = outcome_share / burden_share
        if _ge(ratio, thresholds.burden_outcome_ratio_min):
            text = (
                f"{burden_cell.label} accounts for {_fmt_share(burden_share)} of estimated "
                f"burden but {_fmt_share(outcome_share)} of {outcome.category_label}, based on "
                f"{_raw_n_text(outcome_cell.raw_n)}."
            )
            out.append(
                Insight(
                    kind="burden_vs_share",
                    text=text,
                    tool_id=tool_id,
                    numbers=(
                        ("share of burden", burden_share),
                        (f"share of {outcome.category_label}", outcome_share),
                    ),
                    raw_n=outcome_cell.raw_n,
                    effect_size=ratio,
                )
            )
    return out


def _trend_candidates(frames: InsightFrames, thresholds: Thresholds) -> list[Insight]:
    recent, prior = frames.trend_recent, frames.trend_prior
    if recent is None or prior is None:
        return []
    if not _cells_qualify(recent, prior, min_raw_n=thresholds.min_raw_n):
        return []
    if not prior.value:
        return []
    pct_change = (recent.value - prior.value) / prior.value * 100
    if not _ge(abs(pct_change), thresholds.trend_pct_min):
        return []
    if thresholds.trend_require_nonoverlapping_ci:
        if None in (recent.ci_low, recent.ci_high, prior.ci_low, prior.ci_high):
            return []
        overlap = not (recent.ci_low > prior.ci_high or prior.ci_low > recent.ci_high)
        if overlap:
            return []
    direction = "higher" if pct_change > 0 else "lower"
    text = (
        f"{frames.trend_scope_label} estimated annual average is {abs(pct_change):.1f}% "
        f"{direction} in the most recent {thresholds.trend_window_years}-year window than the "
        f"prior {thresholds.trend_window_years}-year window, based on {_raw_n_text(recent.raw_n)}."
    )
    return [
        Insight(
            kind="trend",
            text=text,
            tool_id=None,
            numbers=(
                ("recent annual average", recent.value),
                ("prior annual average", prior.value),
            ),
            raw_n=recent.raw_n,
            effect_size=abs(pct_change),
        )
    ]


def _baseline_comparison_candidates(
    tool_burden: dict[str, ToolCell], thresholds: Thresholds
) -> list[Insight]:
    """A tool's annual-average burden vs the mean across annual-stable, min-N-qualified tools.
    This magnitude comparison reuses `share_ratio_min` as the ratio gate. Callers must supply
    annual-average cells, independent of the active display basis.
    """
    qualified = [
        c for c in tool_burden.values() if _cells_qualify(c, min_raw_n=thresholds.min_raw_n)
    ]
    if len(qualified) < 2:
        return []
    avg_burden = mean(c.value for c in qualified)
    if not avg_burden:
        return []
    out: list[Insight] = []
    for cell in qualified:
        ratio = cell.value / avg_burden
        if _ge(ratio, thresholds.share_ratio_min):
            text = (
                f"{cell.label} has an estimated annual average of {_fmt_estimate(cell.value)} "
                f"injuries, {ratio:.1f}x the mean among qualifying tools under the current "
                "filters, based on "
                f"{_raw_n_text(cell.raw_n)}."
            )
            out.append(
                Insight(
                    kind="baseline_comparison",
                    text=text,
                    tool_id=cell.tool_id,
                    numbers=(
                        ("annual average", cell.value),
                        ("qualifying-tool annual average", avg_burden),
                    ),
                    raw_n=cell.raw_n,
                    effect_size=ratio,
                )
            )
    return out


# Fixed presentation order, not a ranking of evidence or consequences across kinds.
INSIGHT_KIND_ORDER = (
    "severity",
    "disproportionate",
    "burden_vs_share",
    "trend",
    "baseline_comparison",
    "pairwise_comparison",
)


def rank_insights(candidates: list[Insight], max_cards: int) -> list[Insight]:
    """Rank only within each kind, then take turns across kinds to fill a bounded selection.

    Ratios and percent changes have different units and attainable ranges. Neither is a
    statistical significance score. Fixed kind order carries no strength interpretation.
    Text breaks ties between dimensions for the same tool, independent of input ordering.
    """
    kinds = [kind for kind in INSIGHT_KIND_ORDER if any(c.kind == kind for c in candidates)]
    kinds.extend(sorted({c.kind for c in candidates} - set(kinds)))
    groups = [
        sorted(
            (c for c in candidates if c.kind == kind),
            key=lambda ins: (-ins.effect_size, ins.tool_id or "", ins.text),
        )
        for kind in kinds
    ]
    return [c for row in zip_longest(*groups) for c in row if c is not None][:max_cards]


def _rank_and_cap(candidates: list[Insight], thresholds: Thresholds) -> list[Insight]:
    return rank_insights(candidates, thresholds.max_cards)


def generate_insights(
    frames: InsightFrames, thresholds: Thresholds, basis: str, scope_tool: str | None = None
) -> list[Insight]:
    """Generates every INS-01 candidate statement from `frames`, keeps only those passing
    `thresholds`, and returns at most `thresholds.max_cards`, ranked within each kind and
    selected in turns across kinds.
    `basis` is accepted for API symmetry with the metric layer (which basis-resolves `frames`)
    but this module makes no basis decisions itself -- `frames` already carries basis-resolved
    values. `scope_tool`, when given, restricts share/disproportionate/burden candidates to that
    tool only (Tool Profiles reuse, 03-03)."""
    del basis  # frames are pre-resolved to the active basis by the caller
    candidates: list[Insight] = []
    for dimension in frames.severity:
        candidates.extend(_share_vs_baseline(dimension, thresholds, kind="severity"))
    for dimension in frames.disproportionate:
        candidates.extend(_share_vs_baseline(dimension, thresholds, kind="disproportionate"))
    for outcome in frames.burden_outcomes:
        candidates.extend(
            _burden_vs_share_candidates(
                frames.tool_burden, frames.total_burden, outcome, thresholds
            )
        )
    candidates.extend(_trend_candidates(frames, thresholds))
    candidates.extend(_baseline_comparison_candidates(frames.peer_tool_burden, thresholds))

    if scope_tool is not None:
        candidates = [c for c in candidates if c.tool_id in (scope_tool, None)]

    return _rank_and_cap(candidates, thresholds)


def compare_tools(
    frames: InsightFrames, tool_ids: tuple[str, ...], thresholds: Thresholds, basis: str
) -> list[Insight]:
    """Pairwise "how they differ" statements for exactly `tool_ids` (2-6, Comparison page 03-04):
    the same share-vs-share gates as `_share_vs_baseline`, but comparing two tools directly
    instead of a tool against the current-filter baseline."""
    del basis
    candidates: list[Insight] = []
    dimensions = (*frames.severity, *frames.disproportionate)
    for dimension in dimensions:
        for a, b in combinations(tool_ids, 2):
            cell_a = dimension.by_tool.get(a)
            cell_b = dimension.by_tool.get(b)
            if cell_a is None or cell_b is None:
                continue
            if not _cells_qualify(cell_a, cell_b, min_raw_n=thresholds.min_raw_n):
                continue
            higher, lower = (cell_a, cell_b) if cell_a.value >= cell_b.value else (cell_b, cell_a)
            if lower.value == 0:
                continue
            ratio = higher.value / lower.value
            pp = (higher.value - lower.value) * 100
            if _ge(ratio, thresholds.share_ratio_min) and _ge(pp, thresholds.share_pp_min):
                text = (
                    f"{higher.label} shows a higher {dimension.category_label} than "
                    f"{lower.label}: {_fmt_share(higher.value)} vs {_fmt_share(lower.value)} "
                    f"among ER cases, based on {_raw_n_text(higher.raw_n)}."
                )
                candidates.append(
                    Insight(
                        kind="pairwise_comparison",
                        text=text,
                        tool_id=f"{higher.tool_id}|{lower.tool_id}",
                        numbers=(
                            (f"{higher.label} share", higher.value),
                            (f"{lower.label} share", lower.value),
                        ),
                        raw_n=higher.raw_n,
                        effect_size=ratio,
                    )
                )
    return _rank_and_cap(candidates, thresholds)


# ---------------------------------------------------------------------------
# WATCH-OUTS (D-03, 05-03): one `Watchout` per selected tool, built entirely from `InsightFrames`
# plus two page-scoped `CategoryCell` sequences the caller (`wii.app.data.watchout_frames`)
# resolves via `stability_badge` -- this module still makes no suppression decisions of its own
# (T-03-07) and reuses `_share_vs_baseline` unchanged for "Distinctive injury" (no new threshold
# logic, per D-03).
# ---------------------------------------------------------------------------

WATCHOUT_EXCLUDED_MECHANISMS: frozenset[str] = frozenset({"unknown", "other"})

_STATE_STABLE = "stable"
_STATE_LIMITED = "limited"


@dataclass(frozen=True)
class CategoryCell:
    """One STABLE-or-not category value for a watch-out card field (primary body region / common
    mechanism) -- `state` is the `stability_badge` state string, passed in pre-resolved by the
    caller (this module never computes suppression itself)."""

    category_id: str
    label: str
    value: float | None
    raw_n: int
    state: str


@dataclass(frozen=True)
class Watchout:
    """One WATCH-OUTS card's four fields (D-03). `distinctive_label` is the diagnosis category's
    display label (e.g. "Amputation") for the `distinctive` `Insight`'s numbers -- carried
    alongside it since `Insight` itself has no category id. `nothing_clears` is the "no filler"
    fallback trigger: True only when NEITHER the distinctive-injury comparison NOR any
    tool-scoped `generate_insights` candidate clears its threshold."""

    tool_id: str
    tool_label: str
    primary_region: CategoryCell | None
    distinctive: Insight | None
    distinctive_label: str | None
    common_mechanism: CategoryCell | None
    stands_out: Insight | None

    @property
    def nothing_clears(self) -> bool:
        return self.distinctive is None and self.stands_out is None


def pick_top_stable(
    cells: Iterable[CategoryCell], exclude: frozenset[str] = frozenset()
) -> CategoryCell | None:
    """The max-value cell with `state == "stable"`, a non-None value, and `category_id` not in
    `exclude` -- ties broken by label ascending. `None` when nothing qualifies (limited/suppressed
    cells and excluded ids -- e.g. `WATCHOUT_EXCLUDED_MECHANISMS` -- are never returned)."""
    candidates = [c for c in cells if c.state == _STATE_STABLE and c.value is not None]
    candidates = [c for c in candidates if c.category_id not in exclude]
    if not candidates:
        return None
    candidates.sort(key=lambda c: (-c.value, c.label))
    return candidates[0]


def _distinctive_candidate_for_tool(
    frames: InsightFrames, thresholds: Thresholds, tool_id: str, category_ids: frozenset[str]
) -> tuple[str, Insight] | None:
    """Reuses the EXISTING `_share_vs_baseline` gate on a one-tool `DimensionCategory` (same
    baseline, `by_tool={tool_id: cell}`) for every `frames.disproportionate` dimension whose
    `category_id` is in `category_ids` -- no new threshold logic (D-03). Returns the
    `(category_id, Insight)` pair with the largest effect size (ties by `category_id`), so
    `distinctive_for_tool` can drop the id for its public `Insight | None` API while
    `build_watchout` keeps it to resolve `distinctive_label`."""
    candidates: list[tuple[str, Insight]] = []
    for dimension in frames.disproportionate:
        if dimension.category_id not in category_ids:
            continue
        cell = dimension.by_tool.get(tool_id)
        if cell is None:
            continue
        one_tool_dim = DimensionCategory(
            category_id=dimension.category_id,
            category_label=dimension.category_label,
            baseline=dimension.baseline,
            by_tool={tool_id: cell},
        )
        for insight in _share_vs_baseline(one_tool_dim, thresholds, kind="disproportionate"):
            candidates.append((dimension.category_id, insight))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[1].effect_size, item[0]))
    return candidates[0]


def distinctive_for_tool(
    frames: InsightFrames, thresholds: Thresholds, tool_id: str, category_ids: frozenset[str]
) -> Insight | None:
    """ "Distinctive injury" (D-03): the `diagnosis_any` dimension (restricted to `category_ids`,
    never a `body_rollup`/`mechanism:`-prefixed dimension) whose tool-vs-baseline share clears
    `_share_vs_baseline`'s existing gates, with the largest effect size. `None` when no dimension
    in `category_ids` qualifies for `tool_id`."""
    candidate = _distinctive_candidate_for_tool(frames, thresholds, tool_id, category_ids)
    return candidate[1] if candidate is not None else None


def build_watchout(
    tool_id: str,
    tool_label: str,
    regions: Iterable[CategoryCell],
    mechanisms: Iterable[CategoryCell],
    frames: InsightFrames,
    thresholds: Thresholds,
    basis: str,
    diagnosis_labels: dict[str, str],
) -> Watchout:
    """Assembles one WATCH-OUTS card (D-03): `primary_region`/`common_mechanism` via
    `pick_top_stable` (mechanism excludes `WATCHOUT_EXCLUDED_MECHANISMS`); `distinctive` via
    `_distinctive_candidate_for_tool` restricted to `diagnosis_labels`' keys (`diagnosis_any`
    dimensions only); `stands_out` is the first `generate_insights(frames, thresholds, basis,
    scope_tool=tool_id)` candidate whose own `tool_id` equals this tool (never a non-tool-scoped
    `tool_id=None` candidate such as `trend`, which `generate_insights(scope_tool=...)` still lets
    through unfiltered)."""
    primary_region = pick_top_stable(regions)
    common_mechanism = pick_top_stable(mechanisms, WATCHOUT_EXCLUDED_MECHANISMS)

    category_ids = frozenset(diagnosis_labels)
    candidate = _distinctive_candidate_for_tool(frames, thresholds, tool_id, category_ids)
    if candidate is None:
        distinctive, distinctive_label = None, None
    else:
        cat_id, insight = candidate
        distinctive, distinctive_label = insight, diagnosis_labels[cat_id]

    scoped = generate_insights(frames, thresholds, basis, scope_tool=tool_id)
    own = [c for c in scoped if c.tool_id == tool_id]
    stands_out = own[0] if own else None

    return Watchout(
        tool_id=tool_id,
        tool_label=tool_label,
        primary_region=primary_region,
        distinctive=distinctive,
        distinctive_label=distinctive_label,
        common_mechanism=common_mechanism,
        stands_out=stands_out,
    )


def scan_guardrail_vocabulary(text: str) -> list[str]:
    """Returns every banned guardrail term found (case-insensitive) in `text` -- used by both the
    test suite (scanning every template + generated text) and available for any future caller
    that wants a runtime safety net."""
    lowered = text.lower()
    return [term for term in GUARDRAIL_TERMS if term in lowered]
