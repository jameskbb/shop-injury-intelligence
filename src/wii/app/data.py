"""D-02/APP-05 cached data layer: the only module that touches `wii.metrics` or agg-table SQL.

`st.cache_resource` holds the one process-wide DuckDB connection; `st.cache_data` wraps every
query, keyed on the frozen `CaseFilter` tuple (Wave-0 spike A3: confirmed hashable). The default
view (no active sidebar filter) reads the committed `agg_overall_year` table; any non-default
filter falls through to the `wii.metrics` query path via `cached_estimate_total`. The app imports
ONLY `wii.metrics` (+ `wii.utils` for path resolution) -- never `wii.narratives` or
`wii.data.download`, and never reads `data/raw` or `data/interim` (D-02, enforced by
`tests/app/test_app_boundaries.py`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace

import polars as pl
import streamlit as st

from wii.analytics.insights import (
    CategoryCell,
    DimensionCategory,
    InsightFrames,
    ToolCell,
    Watchout,
    build_watchout,
    load_thresholds,
)
from wii.analytics.mechanism_trend import GateResult, apply_consistency_gate, load_trend_gate
from wii.app import theme
from wii.app.charts import sankey, trend
from wii.app.components import kpi_card, stability_badge
from wii.app.components.body_map import RegionValue
from wii.metrics import (
    CaseFilter,
    connect,
    estimate_by,
    estimate_by_pair,
    estimate_total,
    severity_profile,
    share_by,
)
from wii.metrics.severity import SeverityMetric, load_severity_metrics
from wii.metrics.variance import t_quantile
from wii.utils.paths import repo_root

_DEFAULT_WINDOW = "latest_5"
_DEFAULT_AGE_SCOPE = "adults"


@st.cache_resource
def get_connection():
    """One process-wide DuckDB connection over the committed processed Parquet (D-02)."""
    return connect()


@st.cache_data(ttl=None)
def cached_estimate_total(flt: CaseFilter):
    """Query-path total burden -- used for any non-default filter (D-02)."""
    return estimate_total(get_connection(), flt)


@st.cache_data(ttl=None)
def cached_estimate_by(flt: CaseFilter, by: str) -> pl.DataFrame:
    return estimate_by(get_connection(), flt, by=by)


@st.cache_data(ttl=None)
def cached_share_by(flt: CaseFilter, numerator: CaseFilter, by: str) -> pl.DataFrame:
    return share_by(get_connection(), flt, numerator, by=by)


@st.cache_data(ttl=None)
def cached_severity_profile(flt: CaseFilter, by: str = "tool") -> pl.DataFrame:
    return severity_profile(get_connection(), flt, by=by)


@st.cache_data(ttl=None)
def cached_estimate_by_pair(flt: CaseFilter, by1: str, by2: str) -> pl.DataFrame:
    return estimate_by_pair(get_connection(), flt, by1, by2)


@st.cache_data(ttl=None)
def overall_severity(flt: CaseFilter) -> pl.DataFrame:
    """T-03-07 workshop-wide severity shares: `severity_profile(..., by="all")` pools the entire
    filtered population into a single group instead of per-tool, for the Overview KPI strip's
    hand/finger, laceration and escalated-care share cards. Reuses the same D-04/D-05 stability
    math as `cached_severity_profile` -- `by="all"` is just another entry in `wii.metrics`'
    group-expression map, no separate query path.
    """
    return severity_profile(get_connection(), flt, by="all")


@st.cache_data(ttl=None)
def tools_analyzed(flt: CaseFilter) -> int:
    """Count of tools with raw_n > 0 in the filtered population (D-09 "Tools analyzed" KPI)."""
    df = cached_estimate_by(flt, "tool")
    if df.height == 0:
        return 0
    return int((df["raw_n"] > 0).sum())


@st.cache_data(ttl=None)
def latest_data_year() -> int | None:
    """`latest_year_detection.year` from `data_metadata.json` -- never a fallback/guessed year
    (D-04/APP-04). Returns None (never a guessed or hard-coded year) when the file/key is missing
    or not an int.
    """
    path = repo_root() / "data" / "metadata" / "data_metadata.json"
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
        year = meta["latest_year_detection"]["year"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None
    return year if isinstance(year, int) and not isinstance(year, bool) else None


@st.cache_data(ttl=None)
def available_years() -> tuple[int, ...]:
    """Ascending distinct years present in `agg_overall_year` -- never hard-coded (D-03)."""
    con = get_connection()
    rows = con.execute("SELECT DISTINCT year FROM agg_overall_year ORDER BY year").fetchall()
    return tuple(int(r[0]) for r in rows)


@dataclass(frozen=True)
class OverallEstimate:
    """A pooled overall record: RAW numeric fields only (period_total/annual_average/se/se_annual
    /cv/ci_low(_annual)/ci_high(_annual)/raw_n) -- same field names `stability_badge.resolve()`
    reads per basis. This module never calls `apply_suppression` or sets a per-basis stability
    field itself: `stability_badge.py` is the ONLY place in `src/wii/app` that computes/derives
    suppression state (enforced by a boundary grep in `tests/app/test_app_boundaries.py`), so
    data.py stays a pure numeric aggregator.
    """

    start_year: int
    end_year: int
    n_years: int
    raw_n: int
    period_total: float
    annual_average: float
    se: float
    se_annual: float
    cv: float | None
    ci_low: float | None
    ci_high: float | None
    ci_low_annual: float | None
    ci_high_annual: float | None


@st.cache_data(ttl=None)
def default_overall_estimate(age_scope: str = _DEFAULT_AGE_SCOPE) -> OverallEstimate | None:
    """D-02 default-view agg read: pools the `window='latest_5'` rows of `agg_overall_year`
    (each already a single-year total, per `wii.metrics.aggregates._build_agg_overall_year`) into
    one period/annual record. The pooled CENTRAL estimate and SE are algebraically identical to
    `estimate_total(CaseFilter(years=(latest-4, latest)))` -- weighted sums over disjoint years
    add linearly, and NEISS PSU/stratum samples are independently redrawn each year so summing
    per-year Taylor-linearization variances equals the pooled variance (verified exactly equal,
    relative diff 0.0, against committed data this session). Any non-default filter must go
    through `cached_estimate_total` instead (D-02) -- this function only ever reads the default
    5-year adults/all-ages overall aggregate.
    """
    con = get_connection()
    row = con.execute(
        """
        SELECT
            MIN(year) AS start_year,
            MAX(year) AS end_year,
            COUNT(*) AS n_years,
            SUM(raw_n) AS raw_n,
            SUM(estimate) AS period_total,
            SUM(se * se) AS variance_sum,
            SUM(df) AS df
        FROM agg_overall_year
        WHERE "window" = ? AND age_scope = ?
        """,
        [_DEFAULT_WINDOW, age_scope],
    ).fetchone()
    if row is None or not row[2]:
        return None
    start_year, end_year, n_years, raw_n, period_total, variance_sum, df_sum = row
    n_years = int(n_years)
    raw_n = int(raw_n or 0)
    period_total = float(period_total or 0.0)
    se = (variance_sum or 0.0) ** 0.5
    annual_average = period_total / n_years
    se_annual = se / n_years
    cv = (se / period_total) if period_total else None
    df = int(df_sum or 0)
    if df > 0:
        tq = t_quantile(df)
        ci_low, ci_high = period_total - tq * se, period_total + tq * se
        ci_low_annual = annual_average - tq * se_annual
        ci_high_annual = annual_average + tq * se_annual
    else:
        ci_low = ci_high = ci_low_annual = ci_high_annual = None
    return OverallEstimate(
        start_year=int(start_year),
        end_year=int(end_year),
        n_years=n_years,
        raw_n=raw_n,
        period_total=period_total,
        annual_average=annual_average,
        se=se,
        se_annual=se_annual,
        cv=cv,
        ci_low=ci_low,
        ci_high=ci_high,
        ci_low_annual=ci_low_annual,
        ci_high_annual=ci_high_annual,
    )


@st.cache_data(ttl=None)
def load_dims() -> dict[str, pl.DataFrame]:
    """Static dimension tables the sidebar filters need, deterministically ordered (D-03):
    families by `sort_order`, tools by label, body regions by `sort_order`, sexes/diagnosis
    categories alphabetical.
    """
    con = get_connection()
    return {
        "families": con.execute(
            "SELECT family_id, label, sort_order FROM dim_tool_family ORDER BY sort_order"
        ).pl(),
        "tools": con.execute(
            "SELECT tool_id, label, family_id, family_sort_order, source "
            "FROM dim_tool ORDER BY label"
        ).pl(),
        "sexes": con.execute("SELECT code, label FROM dim_sex ORDER BY label").pl(),
        "body_regions": con.execute(
            "SELECT region_id, label, sort_order FROM dim_body_region "
            "WHERE kind = 'region' ORDER BY sort_order"
        ).pl(),
        "body_regions_all": con.execute(
            "SELECT region_id, label, kind, sort_order FROM dim_body_region ORDER BY sort_order"
        ).pl(),
        "diagnosis_categories": con.execute(
            "SELECT DISTINCT injury_category FROM dim_diagnosis ORDER BY injury_category"
        ).pl(),
        "dispositions": con.execute(
            "SELECT code, label, severity_bucket, escalated_care FROM dim_disposition "
            "ORDER BY sort_order"
        ).pl(),
    }


@st.cache_data(ttl=None)
def default_tool_id(flt: CaseFilter, basis: str) -> str | None:
    """D-10 backstop: Tool Profiles' computed default selection -- the highest-burden STABLE
    tool (basis-matched value via `stability_badge.resolve`) under `flt`, never a hard-coded
    tool id. Ties broken by `group_id` (tool_id) ascending for a deterministic result. Returns
    `None` only when no tool is stable under `flt` -- the caller falls back to the first
    alphabetical option rather than ever leaving the selector empty.
    """
    df = cached_estimate_by(flt, "tool")
    best_id: str | None = None
    best_value: float | None = None
    for row in df.iter_rows(named=True):
        resolved = stability_badge.resolve(row, basis)
        if resolved.state != stability_badge.STATE_STABLE or resolved.value is None:
            continue
        if (
            best_value is None
            or resolved.value > best_value
            or (resolved.value == best_value and row["group_id"] < best_id)
        ):
            best_value = resolved.value
            best_id = row["group_id"]
    return best_id


@st.cache_data(ttl=None)
def anatomy_snapshot_frame(flt: CaseFilter, basis: str) -> dict[str, RegionValue]:
    """Tool Profiles ANATOMY SNAPSHOT (D-10/D-12): `estimate_by(flt, by="body_region")` resolved
    per basis, then normalized WITHIN the tool -- each region's share of the TOOL's own total
    estimate (`flt` is already tool-scoped by the caller). Per-region rows are any-recorded, so
    shares do NOT sum to 1.0 across regions (D-07, documented, not a bug). Returns one
    `RegionValue` per `dim_body_region` row (both kind='region' silhouette fills and kind=
    'callout' chips) so the renderer's `values` mapping always has full region-id coverage, even
    for a region with zero matching cases for this tool.
    """
    total_resolved = stability_badge.resolve(cached_estimate_total(flt), basis)
    by_region = {
        row["group_id"]: row for row in cached_estimate_by(flt, "body_region").iter_rows(named=True)
    }
    dims = load_dims()["body_regions_all"]

    values: dict[str, RegionValue] = {}
    for row in dims.iter_rows(named=True):
        region_id = row["region_id"]
        region_row = by_region.get(region_id)
        if region_row is None or total_resolved.value is None or total_resolved.value == 0:
            values[region_id] = RegionValue(
                label=row["label"],
                value=None,
                display_value="—",
                state=stability_badge.STATE_SUPPRESSED,
                raw_n=0,
            )
            continue
        resolved = stability_badge.resolve(region_row, basis)
        share = resolved.value / total_resolved.value if resolved.value is not None else None
        display = stability_badge.format_share(share) if share is not None else "—"
        values[region_id] = RegionValue(
            label=row["label"],
            value=share,
            display_value=display,
            state=resolved.state,
            raw_n=resolved.raw_n,
        )
    return values


# ---------------------------------------------------------------------------
# D-13/INS-01 insight_frames() bundle builder. This is the ONE place `wii.app` resolves raw
# `wii.metrics` rows into the framework-free `wii.analytics.insights` shapes -- every ToolCell
# below goes through `stability_badge.resolve`/`resolve_share` first (T-03-07: the insight
# engine itself never computes suppression). `severity`/`disproportionate` cover diagnosis/
# body-region/primary-mechanism (per D-13, INS-01, closing 03-VERIFICATION gap 1): diagnosis and
# body-region reuse the SAME `severity_metrics.yaml`-defined metrics the KPI strip already
# queries (zero new query shapes); mechanism is built by `_mechanism_disproportionate_dims` below
# from `theme.MECHANISM_COLORS`. `burden_outcomes` and `trend` add a small, curated set of extra
# cached queries (bounded cost, not one query per tool per metric).
# ---------------------------------------------------------------------------

_SEVERITY_KINDS = frozenset({"disposition_bucket", "disposition_escalated"})
_DISPROPORTIONATE_KINDS = frozenset({"diagnosis_any", "body_rollup_any"})

# Mechanism-vs-workshop-baseline disproportionate dimension (INS-01, 03-05 gap closure). 'unknown'
# and 'other' are excluded: they are deterministic-narrative-classifier coverage residue, not an
# injury pattern (INS-01 honesty prohibition) -- so they must never surface as insight findings.
_MECHANISM_INSIGHT_EXCLUDED = frozenset({"unknown", "other"})
_MECHANISM_INSIGHT_IDS = tuple(
    key for key in theme.MECHANISM_COLORS if key not in _MECHANISM_INSIGHT_EXCLUDED
)
_MECHANISM_CATEGORY_PREFIX = "mechanism:"
# Lowercase, mid-sentence labels for the "narrative-derived share of cases associated with
# {label}" template -- lives here (not in the narrative-classifier package, T-03-13) since the app
# must never import wii.narratives or its config.
_MECHANISM_LABELS: dict[str, str] = {
    "direct_cutting_contact": "direct cutting contact",
    "kickback_ejection": "kickback/ejection",
    "flying_debris_fb": "flying debris/foreign body",
    "loss_of_tool_control": "loss of tool control",
    "workpiece_slip": "workpiece slip",
    "pinch_crush": "pinch/crush",
    "entanglement": "entanglement",
    "maintenance_setup": "maintenance/setup",
    "fastener_discharge": "fastener discharge",
    "fall_trip": "fall/trip",
    "electrical_burn": "electrical/burn",
}
# Curated subset of severity_metrics.yaml used for the "X% of burden but Y% of amputations"
# burden_vs_share kind -- bounded to keep insight_frames()'s extra query count small (2 queries
# per curated metric) rather than one pair per severity_metrics.yaml entry.
_BURDEN_OUTCOME_METRIC_IDS = ("amputation", "hand_finger")
_TREND_WINDOW_YEARS = 3

_METRIC_LABELS: dict[str, str] = {
    "released": "released-without-treatment share",
    "transferred": "transferred-care share",
    "admitted": "hospital-admission share",
    "observation": "observation share",
    "left_without_treatment": "left-without-treatment share",
    "fatality": "fatality share",
    "escalated_care": "escalated-care share",
    "amputation": "amputation share",
    "laceration": "laceration share",
    "fracture": "fracture share",
    "puncture": "puncture share",
    "foreign_body": "foreign-body share",
    "contusion": "contusion/abrasion share",
    "avulsion": "avulsion share",
    "crushing": "crushing-injury share",
    "eye": "eye-injury share",
    "eye_face": "eye/face-injury share",
    "hand_finger": "hand/finger-injury share",
    "head_face": "head/face-injury share",
}
_BURDEN_OUTCOME_LABELS: dict[str, str] = {
    "amputation": "amputations",
    "hand_finger": "hand/finger injuries",
}


def _cell_from_estimate_record(rec, basis: str, tool_id: str, label: str) -> ToolCell:
    resolved = stability_badge.resolve(rec, basis)
    return ToolCell(
        tool_id=tool_id,
        label=label,
        value=resolved.value,
        raw_n=resolved.raw_n,
        stable=resolved.state == stability_badge.STATE_STABLE,
        ci_low=resolved.ci_low,
        ci_high=resolved.ci_high,
    )


def _tool_cell_from_estimate_row(row: dict, basis: str) -> ToolCell:
    return _cell_from_estimate_record(row, basis, row["group_id"], row["display_label"])


def _tool_cell_from_share_row(row: dict, label: str | None = None) -> ToolCell:
    resolved = stability_badge.resolve_share(row)
    return ToolCell(
        tool_id=row["group_id"],
        label=label if label is not None else row.get("display_label", row["group_id"]),
        value=resolved.value,
        raw_n=resolved.raw_n,
        stable=resolved.state == stability_badge.STATE_STABLE,
        ci_low=resolved.ci_low,
        ci_high=resolved.ci_high,
    )


def _severity_and_disproportionate_dims(
    flt: CaseFilter,
) -> tuple[tuple[DimensionCategory, ...], tuple[DimensionCategory, ...]]:
    """Reuses `cached_severity_profile(flt, "tool")` + `overall_severity(flt)` -- the SAME two
    queries the Overview KPI strip already issues -- and re-groups `severity_metrics.yaml`'s
    metric_ids into the `severity` (disposition-based outcomes) vs `disproportionate`
    (diagnosis/body-region categories) kinds by their `SeverityMetric.kind`."""
    metrics = load_severity_metrics()
    by_tool_df = cached_severity_profile(flt, by="tool")
    baseline_df = overall_severity(flt)

    severity_dims: list[DimensionCategory] = []
    disproportionate_dims: list[DimensionCategory] = []
    for metric_id, metric in metrics.items():
        baseline_rows = baseline_df.filter(baseline_df["metric_id"] == metric_id)
        if baseline_rows.height == 0:
            continue
        baseline_cell = _tool_cell_from_share_row(
            baseline_rows.row(0, named=True), label="All tools (workshop-wide)"
        )
        tool_rows = by_tool_df.filter(by_tool_df["metric_id"] == metric_id)
        by_tool = {
            row["group_id"]: _tool_cell_from_share_row(row)
            for row in tool_rows.iter_rows(named=True)
        }
        dim = DimensionCategory(
            category_id=metric_id,
            category_label=_METRIC_LABELS.get(metric_id, f"{metric_id.replace('_', ' ')} share"),
            baseline=baseline_cell,
            by_tool=by_tool,
        )
        if metric.kind in _SEVERITY_KINDS:
            severity_dims.append(dim)
        elif metric.kind in _DISPROPORTIONATE_KINDS:
            disproportionate_dims.append(dim)
    return tuple(severity_dims), tuple(disproportionate_dims)


def _mechanism_disproportionate_dims(flt: CaseFilter) -> tuple[DimensionCategory, ...]:
    """Mechanism-vs-workshop-baseline disproportionate dimension (INS-01, closing 03-VERIFICATION
    gap 1): each tool's within-tool primary-mechanism share (narrative-derived, D-13) against the
    all-workshop share of that same mechanism. Private and not itself cached -- `insight_frames`
    is the cached boundary, same as `_severity_and_disproportionate_dims`. Makes exactly 2
    `share_by` calls per mechanism (one `by="tool"`, one `by="all"`), never a per-tool loop
    (D-14/T-03-16); the `by="all"` baseline shares its `cached_share_by` cache entry with
    `_comparison_mechanism_dims` on the same workshop filter. Values pass through
    `_tool_cell_from_share_row`/`resolve_share`'s raw `.value`, unrounded (D-13 precision
    contract). Stability and raw-N gating stay entirely in the engine's existing `_cells_qualify`
    -- this builder makes no suppression decisions of its own (T-03-07)."""
    dims: list[DimensionCategory] = []
    for key in _MECHANISM_INSIGHT_IDS:
        numerator = CaseFilter(mechanisms=(key,))
        baseline_df = cached_share_by(flt, numerator, by="all")
        if baseline_df.height == 0:
            continue
        label = _MECHANISM_LABELS.get(key, key.replace("_", " "))
        baseline_cell = _tool_cell_from_share_row(
            baseline_df.row(0, named=True), label="All tools (workshop-wide)"
        )
        by_tool = {
            row["group_id"]: _tool_cell_from_share_row(row)
            for row in cached_share_by(flt, numerator, by="tool").iter_rows(named=True)
        }
        dims.append(
            DimensionCategory(
                category_id=_MECHANISM_CATEGORY_PREFIX + key,
                category_label=f"narrative-derived share of cases associated with {label}",
                baseline=baseline_cell,
                by_tool=by_tool,
            )
        )
    return tuple(dims)


def _outcome_filter(flt: CaseFilter, metric: SeverityMetric) -> CaseFilter:
    if metric.kind == "diagnosis_any":
        return replace(flt, diagnosis_codes=metric.codes, diagnosis_basis="any")
    if metric.kind == "body_rollup_any":
        return replace(flt, body_regions=(metric.rollup_id,), body_basis="any")
    raise ValueError(f"unsupported burden-outcome metric kind {metric.kind!r}")


def _burden_outcome_dims(flt: CaseFilter, basis: str) -> tuple[DimensionCategory, ...]:
    """Each dimension's `by_tool` cell holds a tool's SHARE OF THE WORKSHOP-WIDE OUTCOME TOTAL
    (e.g. of all amputation cases, what fraction involve this tool) -- computed from two extra
    cached queries per curated metric (`cached_estimate_by`/`cached_estimate_total` filtered to
    that outcome), never a per-tool query."""
    metrics = load_severity_metrics()
    dims: list[DimensionCategory] = []
    for metric_id in _BURDEN_OUTCOME_METRIC_IDS:
        metric = metrics.get(metric_id)
        if metric is None:
            continue
        outcome_flt = _outcome_filter(flt, metric)
        total_cell = _cell_from_estimate_record(
            cached_estimate_total(outcome_flt), basis, "all", "all outcome cases"
        )
        by_tool: dict[str, ToolCell] = {}
        if total_cell.stable and total_cell.value:
            for row in cached_estimate_by(outcome_flt, "tool").iter_rows(named=True):
                tool_cell = _tool_cell_from_estimate_row(row, basis)
                if not tool_cell.stable or tool_cell.value is None:
                    continue
                by_tool[row["group_id"]] = ToolCell(
                    tool_id=row["group_id"],
                    label=tool_cell.label,
                    value=tool_cell.value / total_cell.value,
                    raw_n=tool_cell.raw_n,
                    stable=True,
                )
        # `baseline` is unused by the burden_vs_share candidate generator (it reads `total_burden`
        # from InsightFrames instead) -- a placeholder unstable cell documents that explicitly.
        placeholder_baseline = ToolCell(
            tool_id="all", label="all outcome cases", value=None, raw_n=0, stable=False
        )
        dims.append(
            DimensionCategory(
                category_id=metric_id,
                category_label=_BURDEN_OUTCOME_LABELS.get(metric_id, metric_id.replace("_", " ")),
                baseline=placeholder_baseline,
                by_tool=by_tool,
            )
        )
    return tuple(dims)


def _trend_cells(flt: CaseFilter) -> tuple[ToolCell | None, ToolCell | None]:
    """Latest-N-year vs prior-N-year ANNUAL AVERAGE burden -- always basis="annual_average"
    regardless of the page's active basis toggle (D-13: trend compares annual averages by
    definition). Returns `(None, None)` when fewer than 2*N years are available in the current
    filter's domain."""
    years = available_years()
    if len(years) < 2 * _TREND_WINDOW_YEARS:
        return None, None
    latest = years[-1]
    recent_start = latest - _TREND_WINDOW_YEARS + 1
    prior_end = recent_start - 1
    prior_start = prior_end - _TREND_WINDOW_YEARS + 1
    if prior_start < years[0]:
        return None, None
    recent_flt = replace(flt, years=(recent_start, latest))
    prior_flt = replace(flt, years=(prior_start, prior_end))
    recent_cell = _cell_from_estimate_record(
        cached_estimate_total(recent_flt), "annual_average", "all", "Workshop-wide"
    )
    prior_cell = _cell_from_estimate_record(
        cached_estimate_total(prior_flt), "annual_average", "all", "Workshop-wide"
    )
    return recent_cell, prior_cell


@st.cache_data(ttl=None)
def insight_frames(flt: CaseFilter, basis: str) -> InsightFrames:
    """D-13 bundle builder: every input `wii.analytics.insights.generate_insights` needs,
    pre-resolved to `basis` via `stability_badge` (T-03-07/T-03-09 -- built entirely from
    `st.cache_data`-wrapped queries keyed on the frozen `CaseFilter` + `basis`). `disproportionate`
    covers diagnosis/body-region/primary-mechanism (per D-13, INS-01, closing 03-VERIFICATION gap
    1): diagnosis/body-region dims come first (unchanged), mechanism dims are appended after."""
    tool_burden = {
        row["group_id"]: _tool_cell_from_estimate_row(row, basis)
        for row in cached_estimate_by(flt, "tool").iter_rows(named=True)
    }
    total_burden = _cell_from_estimate_record(cached_estimate_total(flt), basis, "all", "All tools")
    severity_dims, disproportionate_dims = _severity_and_disproportionate_dims(flt)
    mechanism_dims = _mechanism_disproportionate_dims(flt)
    burden_dims = _burden_outcome_dims(flt, basis)
    trend_recent, trend_prior = _trend_cells(flt)

    return InsightFrames(
        tool_burden=tool_burden,
        total_burden=total_burden,
        severity=severity_dims,
        disproportionate=(*disproportionate_dims, *mechanism_dims),
        burden_outcomes=burden_dims,
        trend_recent=trend_recent,
        trend_prior=trend_prior,
        trend_scope_label="Workshop-wide",
    )


# ---------------------------------------------------------------------------
# D-09 Overview chart data builders (`wii.app.charts.{landscape,injury_mix}`'s pure builders
# never touch wii.metrics/stability_badge directly -- this module resolves every value first).
# ---------------------------------------------------------------------------


@st.cache_data(ttl=None)
def landscape_frame(flt: CaseFilter, basis: str) -> pl.DataFrame:
    """One row per tool: basis-resolved burden estimate + escalated-care share, both
    stability-classified via `stability_badge`, joined to `dim_tool.family_id` for the family
    color lookup (`charts.landscape.build_landscape` never queries `wii.metrics` itself)."""
    tools_df = cached_estimate_by(flt, "tool")
    escalated_by_tool = {
        row["group_id"]: row
        for row in cached_severity_profile(flt, by="tool")
        .filter(pl.col("metric_id") == "escalated_care")
        .iter_rows(named=True)
    }
    tools_dim = load_dims()["tools"]
    family_by_tool = dict(zip(tools_dim["tool_id"], tools_dim["family_id"], strict=True))

    rows = []
    for row in tools_df.iter_rows(named=True):
        tool_id = row["group_id"]
        estimate_resolved = stability_badge.resolve(row, basis)
        escalated_row = escalated_by_tool.get(tool_id)
        escalated_resolved = (
            stability_badge.resolve_share(escalated_row) if escalated_row is not None else None
        )
        rows.append(
            {
                "tool_id": tool_id,
                "label": row["display_label"],
                "family_id": family_by_tool.get(tool_id, ""),
                "estimate": (
                    estimate_resolved.value if estimate_resolved.value is not None else 0.0
                ),
                "estimate_ci_low": estimate_resolved.ci_low,
                "estimate_ci_high": estimate_resolved.ci_high,
                "estimate_raw_n": estimate_resolved.raw_n,
                "estimate_state": estimate_resolved.state,
                "escalated_share": (
                    escalated_resolved.value
                    if escalated_resolved is not None and escalated_resolved.value is not None
                    else 0.0
                ),
                "escalated_ci_low": escalated_resolved.ci_low if escalated_resolved else None,
                "escalated_ci_high": escalated_resolved.ci_high if escalated_resolved else None,
                "escalated_raw_n": escalated_resolved.raw_n if escalated_resolved else 0,
                "escalated_state": (
                    escalated_resolved.state
                    if escalated_resolved is not None
                    else stability_badge.STATE_SUPPRESSED
                ),
            }
        )
    return pl.DataFrame(rows, infer_schema_length=None)


# WHY `infer_schema_length=None` ON EVERY FRAME BUILT FROM A LIST OF DICTS BELOW.
#
# Polars types a column from the first 100 rows. A suppressed row carries `None` in `value`,
# `ci_low` and `ci_high`, so a frame whose leading rows are all suppressed gets those columns
# typed `Null` -- and the first real float appended after row 100 raises
# `ComputeError: could not append value: 0.019132 of type: f64`.
#
# That is not a theoretical edge: the narrow Body Map measures (amputation, fracture, escalated
# care) suppress most regions, so the page died at RUNTIME for exactly the filter combinations a
# curious reader reaches for. It is invisible until one of them is selected, which is why it is
# applied uniformly here rather than at the sites where it has bitten. These frames are tens of
# rows, so scanning all of them to infer a schema costs nothing.


def _diagnosis_mix_row(
    share_df: pl.DataFrame, category: str, label: str, baseline: float | None = None
) -> dict | None:
    if share_df.height == 0:
        return None
    resolved = stability_badge.resolve_share(share_df.row(0, named=True))
    return {
        "category": category,
        "label": label,
        "value": resolved.value if resolved.value is not None else 0.0,
        "ci_low": resolved.ci_low,
        "ci_high": resolved.ci_high,
        "raw_n": resolved.raw_n,
        "state": resolved.state,
        "baseline": baseline,
    }


def _stable_baselines(frame: pl.DataFrame) -> dict[str, float]:
    """The all-workshop share per category, keyed by category id -- used as the comparison tick
    on a within-tool bar.

    ONLY STABLE workshop rows are offered. An unstable comparator is worse than none here: the
    reader anchors on whatever tick is drawn, and a tick that is itself below the reporting floor
    would turn "this tool differs from the workshop" into a statement neither number supports.
    """
    if frame.height == 0 or "state" not in frame.columns:
        return {}
    return {
        row["category"]: row["value"]
        for row in frame.iter_rows(named=True)
        if row["state"] == stability_badge.STATE_STABLE and row["value"] is not None
    }


@st.cache_data(ttl=None)
def injury_mix_frame(flt: CaseFilter, baseline_flt: CaseFilter | None = None) -> pl.DataFrame:
    """D-06 primary-diagnosis share per palette category (`theme.DIAGNOSIS_COLORS`). The
    estimator's `by="diagnosis_category"` grouping always joins the ANY-basis bridge table (no
    primary-basis grouping exists there, and adding one is out of this plan's file scope), so
    each of the 9 buckets is queried as its OWN `CaseFilter(diagnosis_categories=..., "primary")`
    via the existing `share_by` -- this is MORE correct than a single grouped query for the
    residual "other" bucket specifically (its variance needs the categories combined into one
    group, not summed from separate per-category runs), and applying the same pattern uniformly
    to all 9 buckets keeps this module's query shape consistent rather than special-casing one.

    `baseline_flt` adds a `baseline` column: the same per-category share computed over a WIDER
    domain, normally the whole workshop. It is what turns "74.6% laceration" from a number into a
    finding -- the chart draws it as a ghost tick on each bar, so "higher than the workshop" is
    seen rather than parsed. The baseline is computed by calling this same function recursively,
    so the workshop frame is built once per filter and served from `st.cache_data` to every tool
    page that asks for it. With `baseline_flt=None` the column is all-null and the chart draws no
    tick.
    """
    baselines = (
        _stable_baselines(injury_mix_frame(baseline_flt)) if baseline_flt is not None else {}
    )
    dims = load_dims()
    all_categories = dims["diagnosis_categories"]["injury_category"].to_list()
    direct_keys = [k for k in theme.DIAGNOSIS_COLORS if k != "other"]
    other_categories = tuple(c for c in all_categories if theme.diagnosis_palette_key(c) == "other")

    rows: list[dict] = []
    for key in direct_keys:
        numerator = CaseFilter(diagnosis_categories=(key,), diagnosis_basis="primary")
        share_df = cached_share_by(flt, numerator, by="all")
        row = _diagnosis_mix_row(share_df, key, key.replace("_", " ").title(), baselines.get(key))
        if row is not None:
            rows.append(row)
    if other_categories:
        numerator = CaseFilter(diagnosis_categories=other_categories, diagnosis_basis="primary")
        share_df = cached_share_by(flt, numerator, by="all")
        row = _diagnosis_mix_row(share_df, "other", "Other", baselines.get("other"))
        if row is not None:
            rows.append(row)
    return pl.DataFrame(rows, infer_schema_length=None)


# ---------------------------------------------------------------------------
# Tool Profiles (PAGE-02, D-10): trend, mechanism profile, disposition profile, median age,
# representative narratives. `injury_mix_frame` above is reused as-is for the diagnosis profile --
# it already takes an arbitrary `flt` as its denominator, so passing a tool-scoped `flt` (tools=
# (tool_id,)) yields within-tool primary-diagnosis shares with no new query shape needed.
# ---------------------------------------------------------------------------


@st.cache_data(ttl=None)
def mechanism_profile_frame(
    flt: CaseFilter, baseline_flt: CaseFilter | None = None
) -> pl.DataFrame:
    """Within-TOOL primary-mechanism shares -- the same per-category CaseFilter/share_by pattern
    as `injury_mix_frame`, just over `theme.MECHANISM_COLORS`' 13 keys via the `mechanisms` filter
    field instead of `diagnosis_categories`. `baseline_flt` adds the all-workshop share per
    mechanism in a `baseline` column, on the same terms (stable rows only); see
    `injury_mix_frame`."""
    baselines = (
        _stable_baselines(mechanism_profile_frame(baseline_flt)) if baseline_flt is not None else {}
    )
    rows: list[dict] = []
    for key in theme.MECHANISM_COLORS:
        numerator = CaseFilter(mechanisms=(key,))
        share_df = cached_share_by(flt, numerator, by="all")
        row = _diagnosis_mix_row(share_df, key, theme.mechanism_label(key), baselines.get(key))
        if row is not None:
            rows.append(row)
    return pl.DataFrame(rows, infer_schema_length=None)


# ---------------------------------------------------------------------------
# My Shop (PAGE-06, D-02, 05-02 Task 2): selected_tool_share_ranking partitions the SELECTED
# tools' within-tool share for one severity_metrics.yaml metric_id into stable/limited/suppressed
# buckets, for the three ranked severity lists (escalated care, amputation, eye involvement).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RankedShares:
    """Stable/limited entries are plain dicts (`tool_id`, `label`, `value`, `display_value`,
    `raw_n`, `state`) -- `stable`/`limited` are each already sorted value-desc (then label);
    `suppressed_count` is a count only, never individual entries (D-02: suppressed tools are
    omitted, not shown)."""

    stable: tuple[dict, ...]
    limited: tuple[dict, ...]
    suppressed_count: int


@st.cache_data(ttl=None)
def selected_tool_share_ranking(flt: CaseFilter, metric_id: str) -> RankedShares:
    """MY SHOP INJURY PROFILE's three ranked severity lists (D-02): partitions every tool in
    `flt.tools` that has a `cached_severity_profile(flt, by="tool")` row for `metric_id` into
    stable (sorted value desc, then label), limited (sorted the same way) or a suppressed count --
    a tool NOT in `flt.tools` never appears in any bucket (the profile's own denominator is
    already restricted to `flt.tools`, so this is also defensively re-filtered here)."""
    profile = cached_severity_profile(flt, by="tool")
    selected = set(flt.tools)
    rows = profile.filter(
        (profile["metric_id"] == metric_id) & profile["group_id"].is_in(list(selected))
    )
    tools_dim = load_dims()["tools"]
    tool_labels = dict(zip(tools_dim["tool_id"], tools_dim["label"], strict=True))

    stable: list[dict] = []
    limited: list[dict] = []
    suppressed_count = 0
    for row in rows.iter_rows(named=True):
        resolved = stability_badge.resolve_share(row)
        entry = {
            "tool_id": row["group_id"],
            "label": tool_labels.get(row["group_id"], row["group_id"]),
            "value": resolved.value,
            "display_value": resolved.display_value,
            "raw_n": resolved.raw_n,
            "state": resolved.state,
        }
        if resolved.state == stability_badge.STATE_STABLE:
            stable.append(entry)
        elif resolved.state == stability_badge.STATE_LIMITED:
            limited.append(entry)
        else:
            suppressed_count += 1
    stable.sort(key=lambda e: (-e["value"], e["label"]))
    limited.sort(key=lambda e: (-(e["value"] or 0.0), e["label"]))
    return RankedShares(
        stable=tuple(stable), limited=tuple(limited), suppressed_count=suppressed_count
    )


# ---------------------------------------------------------------------------
# WATCH-OUTS (PAGE-06, D-03, 05-03 Task 1): watchout_frames builds one `Watchout` per selected
# tool from exactly ONE `insight_frames(workshop_flt, basis)` call plus two grouped
# `cached_estimate_by_pair` queries (tool x body_region, tool x mechanism) -- never one query per
# tool (D-03, RESEARCH Pattern 1). This module resolves every raw row via `stability_badge` first
# (T-03-07); `wii.analytics.insights.build_watchout` itself makes no suppression decisions.
# ---------------------------------------------------------------------------


def _diagnosis_watchout_labels() -> dict[str, str]:
    """WATCH-OUTS "Distinctive injury" candidate labels (D-03): every `severity_metrics.yaml`
    metric whose `kind` is `diagnosis_any`, labelled from the SAME `_METRIC_LABELS` strings the
    insight engine's sentences use (its trailing " share" stripped, first letter capitalised) --
    never a second hard-coded label table."""
    metrics = load_severity_metrics()
    labels: dict[str, str] = {}
    for metric_id, metric in metrics.items():
        if metric.kind != "diagnosis_any":
            continue
        base = _METRIC_LABELS.get(metric_id, f"{metric_id.replace('_', ' ')} share")
        text = base[: -len(" share")] if base.endswith(" share") else base
        labels[metric_id] = text[:1].upper() + text[1:]
    return labels


def _watchout_pair_cells(
    pair_df: pl.DataFrame,
    tool_rows: dict[str, dict],
    basis: str,
    tool_ids: tuple[str, ...],
    allowed_categories: set[str] | None,
    labels: dict[str, str] | None,
) -> dict[str, list[CategoryCell]]:
    """One `CategoryCell` per (tool, category) pair row actually present: `value` is the resolved
    category share DIVIDED by the tool's own resolved total (None when either is None or the
    tool's total is 0) -- `state` is the CATEGORY's own resolved state (not the tool's), per D-03.
    `allowed_categories=None` accepts every category id (mechanism); a `set` restricts to it
    (body_region -- `kind='region'` ids only, callouts excluded)."""
    by_tool: dict[str, list[CategoryCell]] = {tid: [] for tid in tool_ids}
    for row in pair_df.iter_rows(named=True):
        tool_id = row["group_id_1"]
        if tool_id not in by_tool:
            continue
        category_id = row["group_id_2"]
        if allowed_categories is not None and category_id not in allowed_categories:
            continue
        category_resolved = stability_badge.resolve(row, basis)
        tool_row = tool_rows.get(tool_id)
        tool_resolved = stability_badge.resolve(tool_row, basis) if tool_row is not None else None
        if not tool_resolved or not tool_resolved.value or category_resolved.value is None:
            value = None
        else:
            value = category_resolved.value / tool_resolved.value
        label = (
            labels.get(category_id, str(category_id))
            if labels is not None
            else str(category_id).replace("_", " ").title()
        )
        by_tool[tool_id].append(
            CategoryCell(
                category_id=str(category_id),
                label=label,
                value=value,
                raw_n=category_resolved.raw_n,
                state=category_resolved.state,
            )
        )
    return by_tool


@st.cache_data(ttl=None)
def watchout_frames(
    workshop_flt: CaseFilter, basis: str, tool_ids: tuple[str, ...]
) -> tuple[Watchout, ...]:
    """WATCH-OUTS (D-03): one `Watchout` per `tool_ids`, ordered by estimated burden
    (`stability_badge.order_ranked`, stable tools first) -- a selected tool with no `by="tool"`
    row (zero cases under `workshop_flt`) sorts last, alphabetically."""
    if not tool_ids:
        return ()
    sel_flt = replace(workshop_flt, tools=tool_ids)
    tool_rows = {
        row["group_id"]: row
        for row in cached_estimate_by(sel_flt, "tool").iter_rows(named=True)
        if row["group_id"] in tool_ids
    }
    tools_dim = load_dims()["tools"]
    tool_labels = dict(zip(tools_dim["tool_id"], tools_dim["label"], strict=True))

    ranked_present = stability_badge.order_ranked(
        [tool_rows[tid] for tid in tool_ids if tid in tool_rows], basis
    )
    tool_order = [row["group_id"] for row in ranked_present] + sorted(
        tid for tid in tool_ids if tid not in tool_rows
    )

    region_ids = set(load_dims()["body_regions"]["region_id"].to_list())
    region_cells = _watchout_pair_cells(
        cached_estimate_by_pair(sel_flt, "tool", "body_region"),
        tool_rows,
        basis,
        tool_ids,
        allowed_categories=region_ids,
        labels=_region_label_map(),
    )
    mechanism_cells = _watchout_pair_cells(
        cached_estimate_by_pair(sel_flt, "tool", "mechanism"),
        tool_rows,
        basis,
        tool_ids,
        allowed_categories=None,
        labels=None,
    )

    frames = insight_frames(workshop_flt, basis)
    thresholds = load_thresholds()
    diagnosis_labels = _diagnosis_watchout_labels()

    return tuple(
        build_watchout(
            tool_id=tid,
            tool_label=tool_labels.get(tid, tid),
            regions=region_cells.get(tid, []),
            mechanisms=mechanism_cells.get(tid, []),
            frames=frames,
            thresholds=thresholds,
            basis=basis,
            diagnosis_labels=diagnosis_labels,
        )
        for tid in tool_order
    )


@st.cache_data(ttl=None)
def mechanism_tool_share_frame(flt: CaseFilter, basis: str) -> pl.DataFrame:
    """MECHANISM BY TOOL heatmap source frame (D-12, 04-07 Task 1): one row per (tool_id,
    mechanism) with denominator raw N > 0 -- exactly 13 `cached_share_by(flt,
    CaseFilter(mechanisms=(key,)), by="tool")` calls, one per `theme.MECHANISM_COLORS` key (03-05's
    `_mechanism_disproportionate_dims` issues the SAME `by="tool"` shares for its own baseline
    comparison at a different `by`, so cold-cache calls here still populate/reuse the identical
    `cached_share_by` cache entries). Each row is resolved via `stability_badge.resolve_share`.
    Row (tool_id) order follows `stability_badge.order_ranked` over `cached_estimate_by(flt,
    "tool")` at `basis` -- stable-highest-burden tools first, then limited, then suppressed,
    restricted to tools with a positive denominator raw N under this filter (D-12)."""
    share_by_mechanism: dict[str, pl.DataFrame] = {
        key: cached_share_by(flt, CaseFilter(mechanisms=(key,)), by="tool")
        for key in theme.MECHANISM_COLORS
    }
    den_raw_n_by_tool: dict[str, int] = {}
    for share_df in share_by_mechanism.values():
        for row in share_df.iter_rows(named=True):
            if row["den_raw_n"] > 0:
                den_raw_n_by_tool.setdefault(row["group_id"], row["den_raw_n"])

    ranked = stability_badge.order_ranked(
        list(cached_estimate_by(flt, "tool").iter_rows(named=True)), basis
    )
    tool_order = [row["group_id"] for row in ranked if row["group_id"] in den_raw_n_by_tool]

    tools_dim = load_dims()["tools"]
    tool_labels = dict(zip(tools_dim["tool_id"], tools_dim["label"], strict=True))

    rows: list[dict] = []
    for tool_id in tool_order:
        for key, share_df in share_by_mechanism.items():
            match = share_df.filter(pl.col("group_id") == tool_id)
            if match.height == 0:
                continue
            row = match.row(0, named=True)
            resolved = stability_badge.resolve_share(row)
            rows.append(
                {
                    "tool_id": tool_id,
                    "tool_label": tool_labels.get(tool_id, row["display_label"]),
                    "category": key,
                    "label": theme.mechanism_label(key),
                    "value": resolved.value,
                    "ci_low": resolved.ci_low,
                    "ci_high": resolved.ci_high,
                    "raw_n": resolved.raw_n,
                    "state": resolved.state,
                }
            )
    # `infer_schema_length=None` scans every row before choosing dtypes. Polars otherwise infers
    # from the first 100, and for a narrow measure (amputation, fracture, escalated care) the
    # leading regions are all suppressed -- `value`/`ci_low`/`ci_high` are None there, so the
    # column is typed Null and the first real float later in the frame raises ComputeError.
    return pl.DataFrame(rows, infer_schema_length=None)


_UNKNOWN_MECHANISM_FILTER = CaseFilter(mechanisms=("unknown",))
_MECHANISM_TREND_EXCLUDED = frozenset({"unknown", "other"})


@st.cache_data(ttl=None)
def mechanism_trend_frame(flt: CaseFilter) -> tuple[pl.DataFrame, GateResult]:
    """MECHANISM TREND source frame + gate result (D-13, 04-07 Task 3): the sidebar's YEAR RANGE
    is deliberately ignored (Flagged Assumption, matching `trend_frame`'s own precedent) -- the
    window is always `trend.SCHEMA_CHANGE_YEAR`..latest available year, since that is the only
    window with the richer post-2019 narrative/classification fields the D-13 gate protects.
    Per-year unknown-mechanism shares gate WHICH years are trustworthy (`apply_consistency_gate`);
    the frame itself then carries only the top `gate.top_n` STABLE, non-unknown/non-other
    mechanisms (via `mechanism_profile_frame`'s existing within-group share pattern, applied to
    the gate's own window filter) over exactly the gate's passing years -- an empty frame when the
    gate fails (`show_chart=False`), never a partially-gated one.
    """
    years = available_years()
    gate = load_trend_gate()
    if not years:
        return pl.DataFrame(), apply_consistency_gate({}, None, gate)

    latest = years[-1]
    window_flt = replace(flt, years=(trend.SCHEMA_CHANGE_YEAR, latest))

    year_shares: dict[int, float | None] = {}
    for row in cached_share_by(window_flt, _UNKNOWN_MECHANISM_FILTER, by="year").iter_rows(
        named=True
    ):
        year_shares[row["group_id"]] = stability_badge.resolve_share(row).value

    window_df = cached_share_by(window_flt, _UNKNOWN_MECHANISM_FILTER, by="all")
    window_share: float | None = None
    if window_df.height:
        window_resolved = stability_badge.resolve_share(window_df.row(0, named=True))
        if window_resolved.state == stability_badge.STATE_STABLE:
            window_share = window_resolved.value

    gate_result = apply_consistency_gate(year_shares, window_share, gate)
    if not gate_result.show_chart:
        return pl.DataFrame(), gate_result

    profile = mechanism_profile_frame(window_flt)
    stable_rows = [
        row
        for row in profile.iter_rows(named=True)
        if row["state"] == "stable"
        and row["value"] is not None
        and row["category"] not in _MECHANISM_TREND_EXCLUDED
    ]
    stable_rows.sort(key=lambda row: row["value"], reverse=True)
    top_mechanisms = [row["category"] for row in stable_rows[: gate.top_n]]

    rows: list[dict] = []
    for mechanism in top_mechanisms:
        numerator = CaseFilter(mechanisms=(mechanism,))
        share_df = cached_share_by(window_flt, numerator, by="year")
        label = theme.mechanism_label(mechanism)
        for row in share_df.iter_rows(named=True):
            year = row["group_id"]
            if year not in gate_result.passing_years:
                continue
            resolved = stability_badge.resolve_share(row)
            rows.append(
                {
                    "year": year,
                    "mechanism": mechanism,
                    "label": label,
                    "value": resolved.value,
                    "ci_low": resolved.ci_low,
                    "ci_high": resolved.ci_high,
                    "raw_n": resolved.raw_n,
                    "state": resolved.state,
                }
            )
    return pl.DataFrame(rows, infer_schema_length=None), gate_result


# D-14 (04-08): SETUP & MAINTENANCE labels -- "not_maintenance" is deliberately excluded (never
# shown as a category, UI-SPEC).
MAINTENANCE_CONTEXT_LABELS: dict[str, str] = {
    "blade_change": "Blade change",
    "bit_change": "Bit change",
    "jam_clearing": "Jam clearing",
    "cleaning": "Cleaning",
    "adjustment": "Adjustment",
    "material_setup": "Material setup",
    "other_maintenance": "Other maintenance",
}
POWER_STATE_LABELS: dict[str, str] = {
    "not_mentioned": "Not mentioned",
    "mentioned_unplugged": "Mentioned (unplugged)",
    "mentioned_plugged_in": "Mentioned (plugged in)",
}


_DISPOSITION_METRIC_IDS: tuple[str, ...] = (
    "released",
    "transferred",
    "admitted",
    "observation",
    "left_without_treatment",
    "fatality",
)

# Short legend-friendly labels for the disposition 100%-stacked bar -- distinct from
# `_METRIC_LABELS`' longer insight-sentence phrasing ("released-without-treatment share").
_DISPOSITION_LABELS: dict[str, str] = {
    "released": "Released",
    "transferred": "Transferred",
    "admitted": "Admitted",
    "observation": "Observation",
    "left_without_treatment": "Left without treatment",
    "fatality": "Fatality",
}


@st.cache_data(ttl=None)
def disposition_profile_rows(flt: CaseFilter) -> list[dict]:
    """The 6 disposition-bucket rows (D-10 "not a sequential process" 100%-stacked bar) for the
    tool-scoped `flt`, resolved via `stability_badge.resolve_share` -- reuses the SAME
    `cached_severity_profile(flt, by="tool")` shape the KPI row already queries, filtered to
    `_DISPOSITION_METRIC_IDS` and the tool's own `group_id` row per metric.
    """
    profile = cached_severity_profile(flt, by="tool")
    tool_id = flt.tools[0] if flt.tools else None
    rows: list[dict] = []
    metrics = load_severity_metrics()
    for metric_id in _DISPOSITION_METRIC_IDS:
        candidates = profile.filter(
            (profile["metric_id"] == metric_id) & (profile["group_id"] == tool_id)
        )
        label = _DISPOSITION_LABELS.get(metric_id, metric_id.replace("_", " ").title())
        if candidates.height == 0 or metric_id not in metrics:
            rows.append(
                {
                    "metric_id": metric_id,
                    "label": label,
                    "value": None,
                    "state": stability_badge.STATE_SUPPRESSED,
                    "raw_n": 0,
                }
            )
            continue
        resolved = stability_badge.resolve_share(candidates.row(0, named=True))
        rows.append(
            {
                "metric_id": metric_id,
                "label": label,
                "value": resolved.value,
                "state": resolved.state,
                "raw_n": resolved.raw_n,
            }
        )
    return rows


#: A per-year group's only honest basis is that year's own period total -- see `trend_frame`.
TREND_BASIS = "period_total"


@st.cache_data(ttl=None)
def trend_frame(flt: CaseFilter, basis: str = TREND_BASIS) -> pl.DataFrame:
    """INJURY TREND: per-year estimate for the tool over ALL available years -- the sidebar's
    YEAR RANGE does not scope the trend (Flagged Assumption), only its non-year filters
    (age_scope, families, sexes, body_regions, diagnosis_categories) do, plus the tool itself via
    `flt.tools`. Reuses `cached_estimate_by(..., by="year")`'s query path (already `st.cache_data`
    -cached) rather than a separate agg-table read -- `by="year"` groups by `fc.year` with every
    other WHERE clause from `flt` (including the tool) still applied.

    `basis` IS IGNORED, deliberately, and the parameter is kept only so existing call sites keep
    working. Each group here is ONE treatment year, so the only honest basis for a point is that
    year's own period total. Under "annual average" the estimator divides by the number of years
    in the whole domain -- about twenty -- so every point came out as a single year's estimate
    divided by twenty, relabelled "Estimated annual average" and plotted per year. The temporal
    heatmap already opts out of the basis toggle for exactly this reason and says so in its own
    caption; the trend now follows the same rule at the one place it can be enforced.
    """
    year_free_flt = replace(flt, years=None)
    rows = []
    for row in cached_estimate_by(year_free_flt, "year").iter_rows(named=True):
        resolved = stability_badge.resolve(row, TREND_BASIS)
        rows.append(
            {
                "year": row["group_id"],
                "value": resolved.value,
                "ci_low": resolved.ci_low,
                "ci_high": resolved.ci_high,
                "raw_n": resolved.raw_n,
                "state": resolved.state,
            }
        )
    return pl.DataFrame(rows, infer_schema_length=None)


# ---------------------------------------------------------------------------
# Tool Comparison (PAGE-05, D-11): comparison_metrics() bundles the 9 fixed comparison columns
# (burden + 5 severity_metrics.yaml-backed metrics + 3 mechanism shares) into DimensionCategory
# shapes -- the SAME framework-free hand-off shape insight_frames()/compare_tools() already use,
# so the fingerprint heatmap (03-04 Task 2) and HOW THEY DIFFER (Task 3) share one query layer
# with zero duplication.
# ---------------------------------------------------------------------------

_COMPARISON_METRIC_ORDER: tuple[str, ...] = (
    "burden",
    "escalated_care",
    "amputation",
    "laceration",
    "eye",
    "hand_finger",
    "direct_cutting_contact",
    "flying_debris_fb",
    "maintenance_setup",
)
_COMPARISON_METRIC_LABELS: dict[str, str] = {
    "burden": "Burden",
    "escalated_care": "Escalated care",
    "amputation": "Amputation",
    "laceration": "Laceration",
    "eye": "Eye",
    "hand_finger": "Hand/finger",
    "direct_cutting_contact": "Direct contact",
    "flying_debris_fb": "Flying debris",
    "maintenance_setup": "Maintenance/setup",
}
_COMPARISON_SEVERITY_METRIC_IDS: tuple[str, ...] = (
    "escalated_care",
    "amputation",
    "laceration",
    "eye",
    "hand_finger",
)
_COMPARISON_MECHANISM_IDS: tuple[str, ...] = (
    "direct_cutting_contact",
    "flying_debris_fb",
    "maintenance_setup",
)
_MISSING_COMPARISON_BASELINE = ToolCell(
    tool_id="all", label="All tools (workshop-wide)", value=None, raw_n=0, stable=False
)


def _cell_from_share_row(row: dict, tool_id: str, label: str) -> ToolCell:
    resolved = stability_badge.resolve_share(row)
    return ToolCell(
        tool_id=tool_id,
        label=label,
        value=resolved.value,
        raw_n=resolved.raw_n,
        stable=resolved.state == stability_badge.STATE_STABLE,
        ci_low=resolved.ci_low,
        ci_high=resolved.ci_high,
    )


def _comparison_burden_dim(
    flt: CaseFilter, tool_ids: tuple[str, ...], tool_labels: dict[str, str]
) -> DimensionCategory:
    """Burden column (D-11): each tool's share of the WORKSHOP-WIDE total estimated burden --
    rendered on the sequential share-of-total scale, never a ratio-to-baseline (the fingerprint's
    one hard-coded exception, D-11), so `baseline` here is an unused placeholder cell."""
    by_tool: dict[str, ToolCell] = {}
    for tool_id in tool_ids:
        tool_flt = replace(flt, tools=(tool_id,))
        share_df = cached_share_by(flt, tool_flt, by="all")
        if share_df.height:
            by_tool[tool_id] = _cell_from_share_row(
                share_df.row(0, named=True), tool_id, tool_labels.get(tool_id, tool_id)
            )
    return DimensionCategory(
        category_id="burden",
        category_label=_COMPARISON_METRIC_LABELS["burden"],
        baseline=_MISSING_COMPARISON_BASELINE,
        by_tool=by_tool,
    )


def _comparison_mechanism_dims(
    flt: CaseFilter, tool_ids: tuple[str, ...], tool_labels: dict[str, str]
) -> dict[str, DimensionCategory]:
    """The 3 mechanism-share columns (D-11): each tool's WITHIN-TOOL primary-mechanism share vs
    the WORKSHOP-WIDE mechanism share baseline -- one extra `share_by` query pair per mechanism
    (bounded: 3 mechanisms x (<=6 tools + 1 baseline), never one per tool per metric across the
    whole 31-tool catalog, T-03-16)."""
    dims: dict[str, DimensionCategory] = {}
    for key in _COMPARISON_MECHANISM_IDS:
        numerator = CaseFilter(mechanisms=(key,))
        baseline_df = cached_share_by(flt, numerator, by="all")
        baseline = (
            _cell_from_share_row(baseline_df.row(0, named=True), "all", "All tools (workshop-wide)")
            if baseline_df.height
            else _MISSING_COMPARISON_BASELINE
        )
        by_tool: dict[str, ToolCell] = {}
        for tool_id in tool_ids:
            tool_flt = replace(flt, tools=(tool_id,))
            share_df = cached_share_by(tool_flt, numerator, by="all")
            if share_df.height:
                by_tool[tool_id] = _cell_from_share_row(
                    share_df.row(0, named=True), tool_id, tool_labels.get(tool_id, tool_id)
                )
        dims[key] = DimensionCategory(
            category_id=key,
            category_label=_COMPARISON_METRIC_LABELS[key],
            baseline=baseline,
            by_tool=by_tool,
        )
    return dims


@st.cache_data(ttl=None)
def comparison_metrics(
    flt: CaseFilter, tool_ids: tuple[str, ...], basis: str
) -> tuple[DimensionCategory, ...]:
    """PAGE-05 comparison strip + fingerprint heatmap (D-11): one `DimensionCategory` per the 9
    fixed comparison columns, in `_COMPARISON_METRIC_ORDER` (burden first). `flt` is always the
    WORKSHOP-WIDE filter (never tool-scoped) -- the 5 severity_metrics.yaml-backed columns
    (escalated_care/amputation/laceration/eye/hand_finger) reuse `_severity_and_disproportionate_
    dims(flt)`'s EXISTING query shape (zero new queries, restricted down to `tool_ids`); burden
    and the 3 mechanism columns add a small, bounded set of extra `share_by` queries. Every value
    here is a SHARE (no separate period/annual basis) -- `basis` is accepted for API symmetry with
    `insight_frames(flt, basis)`, matching `generate_insights`'s own documented `del basis`
    precedent.
    """
    del basis
    tools_dim = load_dims()["tools"]
    tool_labels = dict(zip(tools_dim["tool_id"], tools_dim["label"], strict=True))

    severity_dims, disproportionate_dims = _severity_and_disproportionate_dims(flt)
    by_metric = {d.category_id: d for d in (*severity_dims, *disproportionate_dims)}

    dims: list[DimensionCategory] = [_comparison_burden_dim(flt, tool_ids, tool_labels)]
    for metric_id in _COMPARISON_SEVERITY_METRIC_IDS:
        source = by_metric.get(metric_id)
        if source is None:
            dims.append(
                DimensionCategory(
                    category_id=metric_id,
                    category_label=_COMPARISON_METRIC_LABELS[metric_id],
                    baseline=_MISSING_COMPARISON_BASELINE,
                    by_tool={},
                )
            )
            continue
        dims.append(
            DimensionCategory(
                category_id=metric_id,
                category_label=_COMPARISON_METRIC_LABELS[metric_id],
                baseline=source.baseline,
                by_tool={tid: source.by_tool[tid] for tid in tool_ids if tid in source.by_tool},
            )
        )
    mechanism_dims = _comparison_mechanism_dims(flt, tool_ids, tool_labels)
    dims.extend(mechanism_dims[key] for key in _COMPARISON_MECHANISM_IDS)
    return tuple(dims)


@st.cache_data(ttl=None)
def median_age(flt: CaseFilter) -> float | None:
    """Unweighted median of `fact_cases.age_years` over the tool's matching SAMPLE cases (D-10) --
    a raw sample statistic, deliberately NOT survey-weighted (weighted math stays in
    `wii.metrics`); the caller gates display on raw N >= 100. WHERE-clause structure is built via
    plain string concatenation (never an f-string passed to `.execute`, T-02-01/boundary test) --
    every VALUE still reaches DuckDB through `?` parameters via `CaseFilter.to_sql`.
    """
    con = get_connection()
    where_sql, params = flt.to_sql(alias="fc")
    query = "SELECT median(age_years) FROM fact_cases fc WHERE " + where_sql
    row = con.execute(query, params).fetchone()
    return float(row[0]) if row is not None and row[0] is not None else None


# ---------------------------------------------------------------------------
# Body Map (PAGE-03, D-01..D-07): body_map_frame dispatches D-02's 6 measures into one
# region-domain RegionValue mapping (regions AND callouts, same full-coverage contract as
# anatomy_snapshot_frame), decorated with the D-04 tooltip fields (share_display/top_diagnosis/
# top_mechanism) regardless of which measure drives the silhouette fill.
# ---------------------------------------------------------------------------


@st.cache_data(ttl=None)
def mechanism_options() -> list[str]:
    """Distinct `primary_mechanism` values present in `narrative_enrichment`, ordered by
    `theme.MECHANISM_COLORS`' fixed key order (D-01's mechanism selector -- narrative-derived,
    never a coded NEISS field)."""
    con = get_connection()
    rows = con.execute("SELECT DISTINCT primary_mechanism FROM narrative_enrichment").fetchall()
    present = {r[0] for r in rows}
    return [key for key in theme.MECHANISM_COLORS if key in present]


@st.cache_data(ttl=None)
def region_share_frame(flt: CaseFilter) -> pl.DataFrame:
    """D-02/D-04: each kind='region' region's any-recorded share of `flt`'s own total (the ratio
    estimator) -- one `cached_share_by` call per region, columns matching `share_by`'s own row
    shape (+ `region_id`) so `stability_badge.resolve_share` can be applied directly by callers."""
    dims = load_dims()["body_regions"]
    rows: list[dict] = []
    for region_id in dims["region_id"].to_list():
        numerator = CaseFilter(body_regions=(region_id,), body_basis="any")
        share_df = cached_share_by(flt, numerator, by="all")
        row = (
            share_df.row(0, named=True)
            if share_df.height
            else stability_badge.suppressed_share_row()
        )
        rows.append({"region_id": region_id, **row})
    return pl.DataFrame(rows, infer_schema_length=None)


def _pick_top_stable(rows: pl.DataFrame, basis: str, exclude: frozenset[str]) -> dict[str, str]:
    """Pure helper: for each `group_id_1`, the Title-Case label of the highest-value STABLE
    `group_id_2` category (excluding anything in `exclude`) -- a region absent from the result has
    no stable category at all."""
    best: dict[str, tuple[float, str]] = {}
    for row in rows.iter_rows(named=True):
        category = row["group_id_2"]
        if category in exclude:
            continue
        resolved = stability_badge.resolve(row, basis)
        if resolved.state != stability_badge.STATE_STABLE or resolved.value is None:
            continue
        region_id = row["group_id_1"]
        current = best.get(region_id)
        if current is None or resolved.value > current[0]:
            best[region_id] = (resolved.value, category)
    return {
        region_id: category.replace("_", " ").title() for region_id, (_, category) in best.items()
    }


@st.cache_data(ttl=None)
def region_top_categories(flt: CaseFilter, basis: str) -> dict[str, tuple[str | None, str | None]]:
    """D-04 "most common diagnosis"/"most common mechanism" per region, STABLE-only, mechanism
    never "unknown"/"other" -- (None, None) for a region with no stable category of either kind."""
    diag_df = cached_estimate_by_pair(flt, "body_region", "diagnosis_category")
    mech_df = cached_estimate_by_pair(flt, "body_region", "mechanism")
    diag_best = _pick_top_stable(diag_df, basis, exclude=frozenset())
    mech_best = _pick_top_stable(mech_df, basis, exclude=frozenset({"unknown", "other"}))
    region_ids = load_dims()["body_regions"]["region_id"].to_list()
    return {
        region_id: (diag_best.get(region_id), mech_best.get(region_id)) for region_id in region_ids
    }


@st.cache_data(ttl=None)
def body_map_frame(flt: CaseFilter, basis: str, measure: str) -> dict[str, RegionValue]:
    """One `RegionValue` per `dim_body_region` row (any-recorded body basis, per D-02). `measure`
    dispatches the silhouette's own fill value:
    - "estimated": a plain `estimate_by(..., by="body_region")`, unnormalized.
    - "share": `region_share_frame(flt)`'s ratio-estimator share (never a point ratio).
    - "amputation"/"laceration"/"fracture": the measure's own diagnosis category REPLACES the
      page's diagnosis selector (A1 -- `CaseFilter` cannot AND two category sets).
    - "escalated": `dispositions` restricted to `dim_disposition.escalated_care` codes.
    Every region is ALSO decorated with the D-04 tooltip fields (share_display from
    `region_share_frame(flt)`, top_diagnosis/top_mechanism from `region_top_categories(flt,
    basis)`) regardless of which measure drives the fill -- "—" when a field has no stable value.
    """
    if measure == "estimated":
        eff_flt = flt
    elif measure in ("amputation", "laceration", "fracture"):
        eff_flt = replace(flt, diagnosis_categories=(measure,), diagnosis_basis="any")
    elif measure == "escalated":
        dispositions = load_dims()["dispositions"]
        codes = tuple(
            int(row["code"]) for row in dispositions.iter_rows(named=True) if row["escalated_care"]
        )
        eff_flt = replace(flt, dispositions=codes)
    elif measure != "share":
        raise ValueError(f"unsupported measure={measure!r}")

    share_df = region_share_frame(flt)
    share_by_region = {row["region_id"]: row for row in share_df.iter_rows(named=True)}
    top_categories = region_top_categories(flt, basis)

    if measure == "share":
        by_measure = share_by_region
    else:
        by_measure = {
            row["group_id"]: row
            for row in cached_estimate_by(eff_flt, "body_region").iter_rows(named=True)
        }

    dims = load_dims()["body_regions_all"]
    values: dict[str, RegionValue] = {}
    for row in dims.iter_rows(named=True):
        region_id = row["region_id"]
        top_diag, top_mech = top_categories.get(region_id, (None, None))
        share_row = share_by_region.get(region_id)
        share_display = (
            stability_badge.resolve_share(share_row).display_value if share_row is not None else "—"
        )
        measure_row = by_measure.get(region_id)
        if measure_row is None:
            values[region_id] = RegionValue(
                label=row["label"],
                value=None,
                display_value="—",
                state=stability_badge.STATE_SUPPRESSED,
                raw_n=0,
                share_display=share_display,
                share_label=None if flt.tools else "Share of all workshop injuries",
                top_diagnosis=top_diag or "—",
                top_mechanism=top_mech or "—",
            )
            continue
        resolved = (
            stability_badge.resolve_share(measure_row)
            if measure == "share"
            else stability_badge.resolve(measure_row, basis)
        )
        values[region_id] = RegionValue(
            label=row["label"],
            value=resolved.value,
            display_value=resolved.display_value,
            state=resolved.state,
            raw_n=resolved.raw_n,
            share_display=share_display,
            share_label=None if flt.tools else "Share of all workshop injuries",
            top_diagnosis=top_diag or "—",
            top_mechanism=top_mech or "—",
        )
    return values


@st.cache_data(ttl=None)
def region_top_tools(flt: CaseFilter, basis: str, limit: int = 5) -> pl.DataFrame:
    """REGION DETAIL "Top tools" (D-06, "All workshop tools" only, A4): `estimate_by(...,
    by="tool")` ordered stable-first via `stability_badge.order_ranked`, head(limit) -- the raw
    (unresolved) row shape `charts.ranked_bars.build_ranked_bars` expects."""
    df = cached_estimate_by(flt, "tool")
    if df.height == 0:
        return df
    ordered = stability_badge.order_ranked(list(df.iter_rows(named=True)), basis)
    return pl.DataFrame(ordered[:limit], infer_schema_length=None)


_NARRATIVE_REGION_SQL_PREFIX = (
    "SELECT ne_out.case_id, fc.year, fc.narrative, ab.label AS age_bracket_label, "
    "dd.label AS diagnosis_label, disp.label AS disposition_label, ne_out.confidence "
    "FROM fact_cases fc "
    "JOIN narrative_enrichment ne_out ON ne_out.case_id = fc.case_id "
    "LEFT JOIN dim_age_bracket ab ON ab.id = fc.age_bracket "
    "LEFT JOIN dim_diagnosis dd ON dd.code = fc.primary_diagnosis_code "
    "LEFT JOIN dim_disposition disp ON disp.code = fc.disposition_code "
    "WHERE "
)


@st.cache_data(ttl=None)
def filtered_narratives(flt: CaseFilter, min_confidence: float, limit: int = 5) -> pl.DataFrame:
    """D-06 REGION DETAIL representative narratives: `flt`'s own WHERE clause (region constraint
    arrives through `CaseFilter.body_regions`, T-04-14) + a fixed `?`-parameterized tail
    (confidence floor + LIMIT), built by plain string concatenation -- never an f-string passed to
    `.execute` (`_NARRATIVE_REGION_SQL_PREFIX` is a module-level literal, never built from `flt`).
    Defaults `flt.years` to the full committed range like `representative_narratives` does.
    """
    con = get_connection()
    years = flt.years or (min(available_years(), default=0), max(available_years(), default=0))
    eff_flt = replace(flt, years=years)
    where_sql, params = eff_flt.to_sql(alias="fc")
    query = (
        _NARRATIVE_REGION_SQL_PREFIX
        + where_sql
        + " AND ne_out.confidence >= ? ORDER BY ne_out.confidence DESC, fc.year DESC, fc.case_id "
        "LIMIT ?"
    )
    return con.execute(query, [*params, min_confidence, limit]).pl()


@st.cache_data(ttl=None)
def region_share_by_tool_frame(flt: CaseFilter) -> pl.DataFrame:
    """D-07 BODY PART x TOOL MATRIX source frame: one row per (tool_id, region_id) actually
    present, each a WITHIN-TOOL share of injuries involving that region (any recorded body basis)
    -- exactly 22 `cached_share_by(flt, CaseFilter(body_regions=(r,), body_basis="any"),
    by="tool")` calls (one per `kind='region'` region), never a per-tool loop. Columns: tool_id,
    tool_label, region_id, value, ci_low, ci_high, num_estimate, raw_n, state -- already resolved
    via `stability_badge.resolve_share` so `charts.region_tool_matrix` stays a pure formatter.
    """
    dims = load_dims()
    region_ids = dims["body_regions"]["region_id"].to_list()
    tool_labels = dict(zip(dims["tools"]["tool_id"], dims["tools"]["label"], strict=True))

    rows: list[dict] = []
    for region_id in region_ids:
        numerator = CaseFilter(body_regions=(region_id,), body_basis="any")
        share_df = cached_share_by(flt, numerator, by="tool")
        for row in share_df.iter_rows(named=True):
            resolved = stability_badge.resolve_share(row)
            tool_id = row["group_id"]
            rows.append(
                {
                    "tool_id": tool_id,
                    "tool_label": tool_labels.get(tool_id, tool_id),
                    "region_id": region_id,
                    "value": resolved.value,
                    "ci_low": resolved.ci_low,
                    "ci_high": resolved.ci_high,
                    "num_estimate": row["num_estimate"],
                    "raw_n": resolved.raw_n,
                    "state": resolved.state,
                }
            )
    # `infer_schema_length=None` scans every row before choosing dtypes. Polars otherwise infers
    # from the first 100, and for a narrow measure (amputation, fracture, escalated care) the
    # leading regions are all suppressed -- `value`/`ci_low`/`ci_high` are None there, so the
    # column is typed Null and the first real float later in the frame raises ComputeError.
    return pl.DataFrame(rows, infer_schema_length=None)


_NARRATIVE_SQL = (
    "SELECT fc.case_id, fc.year, fc.narrative, ab.label AS age_bracket_label, "
    "dd.label AS diagnosis_label, disp.label AS disposition_label, "
    "ne.confidence, ne.primary_mechanism "
    "FROM fact_cases fc "
    "JOIN bridge_case_products bcp ON bcp.case_id = fc.case_id "
    "AND bcp.in_scope AND bcp.final_tool_id = ? "
    "JOIN narrative_enrichment ne ON ne.case_id = fc.case_id AND ne.primary_mechanism = ? "
    "LEFT JOIN dim_age_bracket ab ON ab.id = fc.age_bracket "
    "LEFT JOIN dim_diagnosis dd ON dd.code = fc.primary_diagnosis_code "
    "LEFT JOIN dim_disposition disp ON disp.code = fc.disposition_code "
    "WHERE fc.year BETWEEN ? AND ? AND (fc.is_adult = TRUE OR ?) AND ne.confidence >= ? "
    "ORDER BY ne.confidence DESC, fc.year DESC, fc.case_id "
    "LIMIT ?"
)


@st.cache_data(ttl=None)
def representative_narratives(
    flt: CaseFilter,
    tool_id: str,
    mechanisms: tuple[str, ...],
    min_confidence: float,
    limit: int = 5,
) -> pl.DataFrame:
    """REPRESENTATIVE CASE PATTERNS (D-10): a parameterized query joining fact_cases,
    bridge_case_products(final_tool_id), narrative_enrichment, dim_age_bracket, dim_diagnosis,
    dim_disposition -- honors `flt.years`/`flt.age_scope`, `confidence >= min_confidence`
    (insights.yaml `narrative_min_confidence`), and spreads the result across `mechanisms` (the
    tool's own top mechanisms, excluding "unknown", chosen by the caller) by pulling an even
    per-mechanism share before the final confidence-desc/year-desc/case_id deterministic order.
    `tool_id` is always one of the page's own selectbox options (dim_tool-backed, never free
    text); every value -- including `tool_id` and every `mechanism` -- reaches DuckDB via `?`
    parameters (T-03-11), never f-string interpolation (`_NARRATIVE_SQL` is a module-level plain
    string literal, not built from `flt`/`tool_id` at all).
    """
    empty = pl.DataFrame(
        schema={
            "case_id": pl.Utf8,
            "year": pl.Int64,
            "narrative": pl.Utf8,
            "age_bracket_label": pl.Utf8,
            "diagnosis_label": pl.Utf8,
            "disposition_label": pl.Utf8,
            "confidence": pl.Float64,
            "primary_mechanism": pl.Utf8,
        }
    )
    if not mechanisms or limit <= 0:
        return empty
    con = get_connection()
    years = flt.years or (min(available_years(), default=0), max(available_years(), default=0))
    all_ages = flt.age_scope == "all"
    per_mechanism_limit = max(1, -(-limit // len(mechanisms)))  # ceil division

    frames: list[pl.DataFrame] = []
    for mechanism in mechanisms:
        params = [
            tool_id,
            mechanism,
            years[0],
            years[1],
            all_ages,
            min_confidence,
            per_mechanism_limit,
        ]
        result = con.execute(_NARRATIVE_SQL, params).pl()
        if result.height:
            frames.append(result)
    if not frames:
        return empty
    combined = pl.concat(frames, how="vertical")
    combined = combined.sort(
        by=["confidence", "year", "case_id"], descending=[True, True, False]
    ).head(limit)
    return combined


# ---------------------------------------------------------------------------
# Mechanisms (PAGE-04, D-08/D-09/D-10/D-11): mechanism_kpi_rows (5 stability-gated shares) and
# sankey_data (the INJURY PATHWAYS five-stage SankeyInput). This module only formats already-
# estimated wii.metrics rows into wii.app.charts.sankey's dataclasses -- collapse/merge/caption
# logic lives in charts.sankey, never here (D-11).
# ---------------------------------------------------------------------------


@st.cache_data(ttl=None)
def mechanism_kpi_rows(flt: CaseFilter) -> list[dict]:
    """D-08: 5 raw `share_by`-shaped rows, in KPI order (classified coverage, direct-contact,
    kickback/ejection, flying-debris, maintenance/setup) -- resolved by the page via
    `stability_badge.resolve_share`. A numerator with no matching row in the filtered domain
    becomes `stability_badge.suppressed_share_row()`, never a KeyError/missing card."""
    coverage_keys = tuple(k for k in theme.MECHANISM_COLORS if k != "unknown")
    numerators = (
        CaseFilter(mechanisms=coverage_keys),
        CaseFilter(mechanisms=("direct_cutting_contact",)),
        CaseFilter(mechanisms=("kickback_ejection",)),
        CaseFilter(mechanisms=("flying_debris_fb",)),
        CaseFilter(maintenance_or_setup=True),
    )
    rows: list[dict] = []
    for numerator in numerators:
        share_df = cached_share_by(flt, numerator, by="all")
        rows.append(
            share_df.row(0, named=True)
            if share_df.height
            else stability_badge.suppressed_share_row()
        )
    return rows


_DISPOSITION_SHORT_LABELS: dict[int, str] = {
    1: "Treated & released",
    2: "Transferred",
    4: "Admitted",
    5: "Observation",
    6: "Left without treatment",
    8: "Fatality",
}


def _region_label_map() -> dict[str, str]:
    dims = load_dims()["body_regions_all"]
    return dict(zip(dims["region_id"], dims["label"], strict=True))


def _disposition_label_map() -> dict[int, str]:
    dims = load_dims()["dispositions"]
    return {
        int(row["code"]): _DISPOSITION_SHORT_LABELS.get(int(row["code"]), row["label"])
        for row in dims.iter_rows(named=True)
    }


def _stage_display_label(
    stage: str,
    raw_cat_id,
    raw_label: str,
    region_labels: dict[str, str],
    disposition_labels: dict[int, str],
) -> str:
    """D-09 label rules: tool nodes use the pair's own dim_tool-backed display label (already
    correct); mechanism/diagnosis-category ids are Title-Cased from their snake_case id; body
    regions use `dim_body_region.label`; dispositions use the short legend labels (falling back to
    the full dim_disposition.label)."""
    if stage == "tool_primary":
        return raw_label
    if stage in ("mechanism", "diagnosis_category_primary"):
        return str(raw_cat_id).replace("_", " ").title()
    if stage == "body_region_primary":
        return region_labels.get(raw_cat_id, str(raw_cat_id))
    if stage == "disposition":
        return disposition_labels.get(raw_cat_id, raw_label)
    raise ValueError(f"unsupported sankey stage={stage!r}")


def _point_value(row: dict, basis: str) -> float:
    """The row's basis-matched RAW point value -- never gated to None by suppression (that's a
    display concern `stability_badge.resolve` handles separately); this is formatting of an
    already-estimated value, never a weight sum (D-11/T-04-20)."""
    value = row["annual_average"] if basis == "annual_average" else row["period_total"]
    return float(value) if value is not None else 0.0


def _pair_links(
    df: pl.DataFrame,
    basis: str,
    stage1: str,
    stage2: str,
    region_labels: dict[str, str],
    disposition_labels: dict[int, str],
) -> tuple[sankey.PairLink, ...]:
    links: list[sankey.PairLink] = []
    for row in df.iter_rows(named=True):
        resolved = stability_badge.resolve(row, basis)
        raw_src, raw_dst = row["group_id_1"], row["group_id_2"]
        links.append(
            sankey.PairLink(
                src_id=str(raw_src),
                src_label=_stage_display_label(
                    stage1, raw_src, row["display_label_1"], region_labels, disposition_labels
                ),
                dst_id=str(raw_dst),
                dst_label=_stage_display_label(
                    stage2, raw_dst, row["display_label_2"], region_labels, disposition_labels
                ),
                point=_point_value(row, basis),
                raw_n=resolved.raw_n,
                state=resolved.state,
                ci_low=resolved.ci_low,
                ci_high=resolved.ci_high,
            )
        )
    return tuple(links)


def _stage_categories(
    df: pl.DataFrame,
    basis: str,
    stage: str,
    region_labels: dict[str, str],
    disposition_labels: dict[int, str],
) -> tuple[sankey.StageCategory, ...]:
    cats: list[sankey.StageCategory] = []
    for row in df.iter_rows(named=True):
        resolved = stability_badge.resolve(row, basis)
        raw_id = row["group_id"]
        cats.append(
            sankey.StageCategory(
                id=str(raw_id),
                label=_stage_display_label(
                    stage, raw_id, row["display_label"], region_labels, disposition_labels
                ),
                point=_point_value(row, basis),
                state=resolved.state,
            )
        )
    return tuple(cats)


_STAGE_PAIRS: tuple[tuple[str, str], ...] = tuple(
    zip(sankey.STAGES[:-1], sankey.STAGES[1:], strict=True)
)


@st.cache_data(ttl=None)
def sankey_data(flt: CaseFilter, basis: str, focus_tool: str | None) -> sankey.SankeyInput:
    """D-09/D-11 INJURY PATHWAYS input: `domain` excludes disposition "not recorded" cases (the
    `dim_disposition.severity_bucket == "not_recorded"` code) and, in single-tool focus mode,
    restricts to that one tool. All 4 adjacent-stage columns and all 5 stage marginals are built
    from `cached_estimate_by_pair`/`cached_estimate_by` over `domain` -- in focus mode this
    naturally collapses the Tool stage to the one focus tool (its own `tool_primary` marginal/pair
    rows are the only ones left in the domain), so no separate query shape is needed."""
    dispositions_dim = load_dims()["dispositions"]
    recorded_codes = tuple(
        int(row["code"])
        for row in dispositions_dim.iter_rows(named=True)
        if row["severity_bucket"] != "not_recorded"
    )
    not_recorded_codes = tuple(
        int(row["code"])
        for row in dispositions_dim.iter_rows(named=True)
        if row["severity_bucket"] == "not_recorded"
    )
    domain = replace(
        flt, dispositions=recorded_codes, tools=(focus_tool,) if focus_tool else flt.tools
    )
    region_labels = _region_label_map()
    disposition_labels = _disposition_label_map()
    severe_ids = frozenset(
        str(int(row["code"]))
        for row in dispositions_dim.iter_rows(named=True)
        if row["escalated_care"]
    )

    stage_pairs = tuple(
        _pair_links(
            cached_estimate_by_pair(domain, s1, s2),
            basis,
            s1,
            s2,
            region_labels,
            disposition_labels,
        )
        for s1, s2 in _STAGE_PAIRS
    )
    marginals = tuple(
        _stage_categories(
            cached_estimate_by(domain, s), basis, s, region_labels, disposition_labels
        )
        for s in sankey.STAGES
    )

    excluded_flt = replace(flt, dispositions=not_recorded_codes)
    excluded_display = stability_badge.resolve(
        cached_estimate_total(excluded_flt), basis
    ).display_value

    focus_label = None
    if focus_tool:
        tools_dim = load_dims()["tools"]
        match = tools_dim.filter(tools_dim["tool_id"] == focus_tool)
        focus_label = match.row(0, named=True)["label"] if match.height else focus_tool

    return sankey.SankeyInput(
        stage_pairs=stage_pairs,
        marginals=marginals,
        severe_disposition_ids=severe_ids,
        excluded_display=excluded_display,
        focus_tool_label=focus_label,
    )


# ---------------------------------------------------------------------------
# SETUP & MAINTENANCE deep dive (PAGE-04, D-14, 04-08): maintenance-context breakdown,
# maintenance/setup share by tool, power-state mentions -- all scoped to
# `CaseFilter(maintenance_or_setup=True)`, reading the rules-1.2.0 narrative_enrichment columns
# (04-05) through the new `estimate_by(by="maintenance_context"/"power_state")` group specs
# (04-08 Task 1). This module only formats already-estimated rows; the page never re-derives a
# weighted estimate or a stability decision (D-14/T-04-26/T-04-29).
# ---------------------------------------------------------------------------

_MAINTENANCE_FLT = CaseFilter(maintenance_or_setup=True)


@st.cache_data(ttl=None)
def maintenance_context_frame(flt: CaseFilter, basis: str) -> pl.DataFrame:
    """ "Maintenance context" breakdown (D-14): `estimate_by(..., by="maintenance_context")` over
    the maintenance/setup-scoped domain, `not_maintenance` excluded (never shown as a category,
    UI-SPEC). Returns the SAME raw (unresolved) `EstimateRecord`-shaped frame
    `charts.ranked_bars.build_ranked_bars` expects -- reordered stable-first via
    `stability_badge.order_ranked` (which resolves internally to decide the order, but does not
    mutate the row's numeric fields) with `display_label` overridden from
    `MAINTENANCE_CONTEXT_LABELS`; basis/stability resolution for display happens in that chart
    builder, not here (same contract as `region_top_tools`)."""
    maint_flt = replace(flt, maintenance_or_setup=True)
    df = cached_estimate_by(maint_flt, "maintenance_context")
    if df.height == 0:
        return df
    df = df.filter(pl.col("group_id") != "not_maintenance")
    if df.height == 0:
        return df
    ordered = stability_badge.order_ranked(list(df.iter_rows(named=True)), basis)
    for row in ordered:
        row["display_label"] = MAINTENANCE_CONTEXT_LABELS.get(row["group_id"], row["group_id"])
    return pl.DataFrame(ordered, infer_schema_length=None)


@st.cache_data(ttl=None)
def maintenance_share_by_tool_frame(flt: CaseFilter) -> pl.DataFrame:
    """ "Maintenance/setup share by tool" (D-14): `share_by(flt, CaseFilter(maintenance_or_setup=
    True), by="tool")` resolved via `stability_badge.resolve_share`, stable-first then by value
    desc (never interleaved, D-07). One row per tool with a positive denominator raw N under this
    filter."""
    share_df = cached_share_by(flt, _MAINTENANCE_FLT, by="tool")
    if share_df.height == 0:
        return share_df
    tools_dim = load_dims()["tools"]
    tool_labels = dict(zip(tools_dim["tool_id"], tools_dim["label"], strict=True))

    rows: list[dict] = []
    for row in share_df.iter_rows(named=True):
        resolved = stability_badge.resolve_share(row)
        rows.append(
            {
                "tool_id": row["group_id"],
                "label": tool_labels.get(row["group_id"], row["display_label"]),
                "value": resolved.value,
                "display_value": resolved.display_value,
                "ci_low": resolved.ci_low,
                "ci_high": resolved.ci_high,
                "raw_n": resolved.raw_n,
                "state": resolved.state,
            }
        )
    rows = stability_badge.order_resolved(rows)
    return pl.DataFrame(rows, infer_schema_length=None)


# ---------------------------------------------------------------------------
# Case Explorer (PAGE-07, D-07..D-12): search + filter + paginate over raw narratives. No weighted
# estimate is ever computed here -- `case_explorer_count`/`case_explorer_page` return raw sample
# counts/rows only. Every value (search terms, filter values, page/offset) reaches DuckDB via `?`
# parameters; every SQL string is a module-level constant or built by plain concatenation, never an
# f-string passed to `.execute` (T-05-01, `test_app_boundaries`).
# ---------------------------------------------------------------------------

CASE_EXPLORER_PAGE_SIZE = 25

_LIKE_ESCAPE = "\\"


def _like_term(term: str) -> str:
    """Escapes the LIKE metacharacters (`\\`, `%`, `_`) in a raw search term, in that order (the
    escape character itself must be doubled FIRST), then wraps the result in `%` for a substring
    match. This builds a query PARAMETER value, never SQL text, so an f-string here is safe and
    does not trip the `.execute` f-string ban (D-08, 05-RESEARCH.md Pattern 2)."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _search_clause(n_terms: int) -> str:
    """`n_terms` copies of the constant per-term ILIKE clause, ANDed -- built via `str.join`, never
    an f-string (D-08/D-12)."""
    return " AND ".join(["fc.narrative ILIKE ? ESCAPE '" + _LIKE_ESCAPE + "'"] * n_terms)


def _case_explorer_where(
    flt: CaseFilter, terms: tuple[str, ...], min_confidence: float | None
) -> tuple[str, list]:
    where_sql, params = flt.to_sql(alias="fc")
    clauses = [where_sql]
    params = list(params)
    if terms:
        clauses.append(_search_clause(len(terms)))
        params.extend(_like_term(t) for t in terms)
    if min_confidence is not None:
        clauses.append("ne_c.confidence >= ?")
        params.append(min_confidence)
    return " AND ".join(clauses), params


_CASE_EXPLORER_COUNT_SQL_PREFIX = (
    "SELECT COUNT(*) FROM fact_cases fc "
    "LEFT JOIN narrative_enrichment ne_c ON ne_c.case_id = fc.case_id WHERE "
)


@st.cache_data(ttl=None)
def case_explorer_count(
    flt: CaseFilter, terms: tuple[str, ...], min_confidence: float | None = None
) -> int:
    """D-12: one cached parameterized COUNT over `fact_cases`, keyed on the frozen `flt` + search
    `terms` + `min_confidence`. With no filters, no search terms and no confidence floor this
    equals `SELECT COUNT(*) FROM fact_cases` exactly (RESEARCH Pitfall 2 -- no `is_primary`-only
    tool join is ever in this query's own WHERE, since the tool label is resolved separately in
    `case_explorer_page`, never used to restrict `case_explorer_count`)."""
    where_sql, params = _case_explorer_where(flt, terms, min_confidence)
    query = _CASE_EXPLORER_COUNT_SQL_PREFIX + where_sql
    row = get_connection().execute(query, params).fetchone()
    return int(row[0]) if row is not None and row[0] is not None else 0


# RESEARCH Pattern 3 / Pitfall 2: a naive `is_primary`-only join drops 6.3% of cases. `case_tools`
# dedups `bridge_case_products` to one row per (case_id, final_tool_id) over `in_scope` rows only;
# `case_tool_label` then string_aggs every distinct in-scope tool label per case (ordered primary-
# first, then first `seq`) so a multi-tool case shows every one of its tools, never just one.
_CASE_TOOLS_CTE = (
    "WITH case_tools AS ("
    "SELECT case_id, final_tool_id, MIN(seq) AS min_seq, "
    "MAX(CAST(is_primary AS INTEGER)) AS any_primary "
    "FROM bridge_case_products WHERE in_scope GROUP BY case_id, final_tool_id"
    "), case_tool_label AS ("
    "SELECT ct.case_id, "
    "string_agg(dt.label, ' + ' ORDER BY ct.any_primary DESC, ct.min_seq ASC) AS tool_label "
    "FROM case_tools ct JOIN dim_tool dt ON dt.tool_id = ct.final_tool_id "
    "GROUP BY ct.case_id"
    ") "
)

_CASE_EXPLORER_PAGE_SQL_PREFIX = (
    _CASE_TOOLS_CTE + "SELECT fc.case_id, fc.year, ctl.tool_label, "
    "ab_c.label AS age_bracket_label, bp_c.label AS body_part_label, "
    "dd_c.label AS diagnosis_label, disp_c.label AS disposition_label, "
    "ne_c.primary_mechanism, ne_c.confidence, fc.narrative "
    "FROM fact_cases fc "
    "LEFT JOIN case_tool_label ctl ON ctl.case_id = fc.case_id "
    "LEFT JOIN narrative_enrichment ne_c ON ne_c.case_id = fc.case_id "
    "LEFT JOIN dim_age_bracket ab_c ON ab_c.id = fc.age_bracket "
    "LEFT JOIN dim_body_part bp_c ON bp_c.code = fc.primary_body_part_code "
    "LEFT JOIN dim_diagnosis dd_c ON dd_c.code = fc.primary_diagnosis_code "
    "LEFT JOIN dim_disposition disp_c ON disp_c.code = fc.disposition_code "
    "WHERE "
)


@st.cache_data(ttl=None)
def case_explorer_page(
    flt: CaseFilter,
    terms: tuple[str, ...],
    page: int,
    min_confidence: float | None = None,
) -> pl.DataFrame:
    """D-10/D-12: one cached parameterized page query, `LIMIT CASE_EXPLORER_PAGE_SIZE OFFSET`,
    ordered year desc then case_id -- unique `case_id` per row (Pitfall 2 dedup), never
    `age_years`/`age_raw` (T-05-06). `page` is clamped to >= 1 before computing the offset."""
    where_sql, params = _case_explorer_where(flt, terms, min_confidence)
    offset = (max(page, 1) - 1) * CASE_EXPLORER_PAGE_SIZE
    query = (
        _CASE_EXPLORER_PAGE_SQL_PREFIX
        + where_sql
        + " ORDER BY fc.year DESC, fc.case_id LIMIT ? OFFSET ?"
    )
    return get_connection().execute(query, [*params, CASE_EXPLORER_PAGE_SIZE, offset]).pl()


@st.cache_data(ttl=None)
def case_explorer_filter_options() -> dict[str, list[tuple]]:
    """D-07: the eight Case Explorer filter dimensions' `(value, label)` option lists, sourced
    only from dims/enrichment data (never hard-coded). `tool_subtypes` excludes the literal
    `"unknown"` value (RESEARCH OQ3: 90% base rate) -- an empty subtype selection already includes
    those cases, since an unset filter means "all"."""
    con = get_connection()
    dims = load_dims()

    tools_df = dims["tools"]
    tools = [
        (
            row["tool_id"],
            f"{row['label']} (narrative-refined)"
            if row["source"] == "narrative_refined"
            else row["label"],
        )
        for row in tools_df.iter_rows(named=True)
    ]

    subtype_rows = con.execute(
        "SELECT DISTINCT tool_subtype FROM narrative_enrichment "
        "WHERE tool_subtype IS NOT NULL AND tool_subtype <> 'unknown' ORDER BY 1"
    ).fetchall()
    tool_subtypes = [(r[0], str(r[0]).replace("_", " ").title()) for r in subtype_rows]

    mechanisms = [(key, theme.mechanism_label(key)) for key in mechanism_options()]

    body_regions_df = dims["body_regions"]
    body_regions = [
        (row["region_id"], row["label"]) for row in body_regions_df.iter_rows(named=True)
    ]

    diagnosis_category_values = dims["diagnosis_categories"]["injury_category"].to_list()
    diagnosis_categories = [(c, c.replace("_", " ").title()) for c in diagnosis_category_values]

    dispositions_df = dims["dispositions"]
    dispositions = [
        (int(row["code"]), row["label"]) for row in dispositions_df.iter_rows(named=True)
    ]

    age_bracket_rows = con.execute(
        "SELECT id, label FROM dim_age_bracket ORDER BY sort_order"
    ).fetchall()
    age_brackets = [(r[0], r[1]) for r in age_bracket_rows]

    location_rows = con.execute(
        "SELECT code, label FROM dim_location ORDER BY sort_order"
    ).fetchall()
    locations = [(int(r[0]), r[1]) for r in location_rows]

    return {
        "tools": tools,
        "tool_subtypes": tool_subtypes,
        "mechanisms": mechanisms,
        "body_regions": body_regions,
        "diagnosis_categories": diagnosis_categories,
        "dispositions": dispositions,
        "age_brackets": age_brackets,
        "locations": locations,
    }


@st.cache_data(ttl=None)
def power_state_frame(flt: CaseFilter, basis: str) -> pl.DataFrame:
    """ "Power-state mentions" (D-14): `estimate_by(..., by="power_state")` over the
    maintenance/setup-scoped domain, resolved via `stability_badge.resolve` -- one row per
    `POWER_STATE_LABELS` key EVEN WHEN a category has zero matching cases in this filter (a
    suppressed placeholder row, never a missing category), so the page always renders a fixed
    3-row table."""
    maint_flt = replace(flt, maintenance_or_setup=True)
    df = cached_estimate_by(maint_flt, "power_state")
    present = {row["group_id"]: row for row in df.iter_rows(named=True)}

    rows: list[dict] = []
    for key, label in POWER_STATE_LABELS.items():
        row = present.get(key)
        if row is None:
            rows.append(
                {
                    "group_id": key,
                    "label": label,
                    "value": None,
                    "display_value": "—",
                    "raw_n": 0,
                    "state": stability_badge.STATE_SUPPRESSED,
                }
            )
            continue
        resolved = stability_badge.resolve(row, basis)
        rows.append(
            {
                "group_id": key,
                "label": label,
                "value": resolved.value,
                "display_value": resolved.display_value,
                "raw_n": resolved.raw_n,
                "state": resolved.state,
            }
        )
    return pl.DataFrame(rows, infer_schema_length=None)


# ---------------------------------------------------------------------------
# KPI period-over-period comparison (D-04/D-13 discipline applied to the Overview KPI strip).
#
# NEISS is an annual, lagged surveillance release: there is no "vs last month", and a single-year
# change is noise at this sample size. The only comparison this app makes on a KPI is the data's
# LATEST 5-YEAR WINDOW vs the PRECEDING 5-YEAR WINDOW, and `gated_delta` refuses to emit one
# unless both windows clear the stability floor AND their 95% confidence intervals do not overlap
# -- the same rule `wii.analytics.insights._trend_candidates` applies under
# `trend_require_nonoverlapping_ci`, read from the same `insights.yaml` via `load_thresholds()`
# (no second copy of the thresholds). Everything numeric still comes from `wii.metrics` through
# the existing cached query helpers; nothing here sums a weight.
# ---------------------------------------------------------------------------

# 3, not 5, and matched to `insights.trend_window_years` for the same reason that gate exists:
# only 2019+ is comparable (see comparison_windows), and 2019-2025 cannot hold two 5-year
# windows. Five would silently disable every delta on the page.
_KPI_DELTA_WINDOW_YEARS = 3

# The trend/effect-size floors (`trend_pct_min`, `share_ratio_min`, `share_pp_min`) are
# deliberately NOT applied here. Those are editorial "is this worth a written sentence" gates for
# the insight cards; a KPI delta whose two 95% CIs are fully separated is already a difference the
# survey design can resolve, and silently dropping it would misrepresent the estimate as unchanged.


@dataclass(frozen=True)
class ComparisonWindows:
    """The two equal-length year windows a KPI delta compares, derived from `available_years()`
    -- never hard-coded (D-03)."""

    recent: tuple[int, int]
    prior: tuple[int, int]

    @property
    def label(self) -> str:
        """Names BOTH periods, so the rendered indicator can never be read against the wrong
        one."""
        return f"{self.recent[0]}–{self.recent[1]} vs {self.prior[0]}–{self.prior[1]}"


def comparison_windows() -> ComparisonWindows | None:
    """Latest `_KPI_DELTA_WINDOW_YEARS` years vs the `_KPI_DELTA_WINDOW_YEARS` before them, with
    BOTH windows required to sit entirely on or after `trend.SCHEMA_CHANGE_YEAR`.

    The schema constraint is not a nicety, it is the difference between a finding and an artifact.
    NEISS gained a second diagnosis slot in 2019: `Diagnosis_2`'s fill rate is 0.000 for 2014-2018
    and 0.196-0.245 for 2019-2025 (`data/metadata/data_metadata.json`). Every `diagnosis_any`
    metric therefore steps up at 2019 for coding reasons alone -- measured laceration share on the
    ANY basis goes 50.8% (2018) -> 56.9% (2019) while the PRIMARY basis holds flat at 50.8% ->
    50.8%.

    Without this constraint the prior window was 2016-2020: three pre-schema years against two
    post. That produced the only delta on the page, "Laceration share +4.6 pp", which measured the
    arrival of a database column rather than any change in injuries. Two separated confidence
    intervals cannot detect a systematic artifact -- the CI gate was passing the one card that was
    wrong while correctly blocking three that were honest non-differences.

    `insights._trend_candidates` already had this right, comparing 3-year windows that both sit
    after the boundary. This aligns with it.
    """
    years = [y for y in available_years() if y >= trend.SCHEMA_CHANGE_YEAR]
    if len(years) < 2 * _KPI_DELTA_WINDOW_YEARS:
        return None
    latest = years[-1]
    recent_start = latest - _KPI_DELTA_WINDOW_YEARS + 1
    prior_end = recent_start - 1
    prior_start = prior_end - _KPI_DELTA_WINDOW_YEARS + 1
    if prior_start < years[0]:
        return None
    return ComparisonWindows(recent=(recent_start, latest), prior=(prior_start, prior_end))


def gated_delta(
    recent: ToolCell | None,
    prior: ToolCell | None,
    *,
    unit: str,
    comparison_label: str,
) -> kpi_card.KpiDelta | None:
    """The ONE gate for every KPI delta. Returns None -- meaning "render no delta at all", which
    is the honest state -- unless every condition holds:

    * both cells exist and carry a value;
    * both cells are STABLE (`stability_badge.STATE_STABLE`; limited and suppressed are equally
      ineligible, same rule as `wii.analytics.insights._cells_qualify`);
    * both cells meet `min_raw_n`;
    * when `trend_require_nonoverlapping_ci` is set, both cells have a full 95% CI and the two
      intervals are disjoint.

    `unit=UNIT_PP` subtracts the two shares and reports PERCENTAGE POINTS; `unit=UNIT_PERCENT`
    reports change relative to the prior window. A rate is never expressed as a percent-change of
    a percent.
    """
    if recent is None or prior is None:
        return None
    if recent.value is None or prior.value is None:
        return None
    thresholds = load_thresholds()
    if not (recent.stable and prior.stable):
        return None
    if recent.raw_n < thresholds.min_raw_n or prior.raw_n < thresholds.min_raw_n:
        return None
    if thresholds.trend_require_nonoverlapping_ci:
        if None in (recent.ci_low, recent.ci_high, prior.ci_low, prior.ci_high):
            return None
        if not (recent.ci_low > prior.ci_high or prior.ci_low > recent.ci_high):
            return None
    if unit == kpi_card.UNIT_PP:
        magnitude = (recent.value - prior.value) * 100.0
    else:
        if not prior.value:
            return None
        magnitude = (recent.value - prior.value) / prior.value * 100.0
    return kpi_card.KpiDelta(magnitude=magnitude, unit=unit, comparison_label=comparison_label)


def _delta_windows_for(flt: CaseFilter) -> ComparisonWindows | None:
    """A delta may only sit under a KPI whose own period IS the recent window. If the sidebar's
    year range is anything else, the card's number and the comparison would describe different
    periods -- so no delta is offered at all rather than a mislabelled one. Every NON-year facet
    of `flt` (age scope, family, tool, sex, body region, diagnosis) is held constant across both
    windows, exactly as `_trend_cells` does.
    """
    windows = comparison_windows()
    if windows is None or flt.years != windows.recent:
        return None
    return windows


def _burden_window_cell(flt: CaseFilter, years: tuple[int, int]) -> ToolCell | None:
    record = cached_estimate_total(replace(flt, years=years))
    if record is None:
        return None
    return _cell_from_estimate_record(record, "annual_average", "all", "Workshop-wide")


def _share_window_cell(flt: CaseFilter, years: tuple[int, int], metric_id: str) -> ToolCell:
    profile = overall_severity(replace(flt, years=years))
    rows = profile.filter(profile["metric_id"] == metric_id)
    # No row for this metric in the window (e.g. a zero-case group) is hard-suppressed, never a
    # zero share -- same stand-in the Overview share cards use.
    row = rows.row(0, named=True) if rows.height else stability_badge.suppressed_share_row()
    resolved = stability_badge.resolve_share(row)
    return ToolCell(
        tool_id="all",
        label=metric_id,
        value=resolved.value,
        raw_n=resolved.raw_n,
        stable=resolved.state == stability_badge.STATE_STABLE,
        ci_low=resolved.ci_low,
        ci_high=resolved.ci_high,
    )


@st.cache_data(ttl=None)
def burden_window_delta(flt: CaseFilter) -> kpi_card.KpiDelta | None:
    """Change in total estimated burden between the two windows, as a percent of the prior
    window. Always computed on the `annual_average` basis regardless of the page's basis toggle:
    the two windows are the same length, so the percent change is identical either way, and
    annual-average is the basis the stability floor is calibrated against for a multi-year window
    (same choice `_trend_cells` makes).
    """
    windows = _delta_windows_for(flt)
    if windows is None:
        return None
    return gated_delta(
        _burden_window_cell(flt, windows.recent),
        _burden_window_cell(flt, windows.prior),
        unit=kpi_card.UNIT_PERCENT,
        comparison_label=windows.label,
    )


@st.cache_data(ttl=None)
def share_window_delta(flt: CaseFilter, metric_id: str) -> kpi_card.KpiDelta | None:
    """Change in a workshop-wide severity/anatomy SHARE between the two windows, in percentage
    points. Reuses `overall_severity` (`severity_profile(..., by="all")`), so the numerator and
    denominator stability of each window is `wii.metrics`' own composite share rule, not a
    re-derivation.
    """
    windows = _delta_windows_for(flt)
    if windows is None:
        return None
    return gated_delta(
        _share_window_cell(flt, windows.recent, metric_id),
        _share_window_cell(flt, windows.prior, metric_id),
        unit=kpi_card.UNIT_PP,
        comparison_label=windows.label,
    )


# ---------------------------------------------------------------------------
# TOOL BURDEN BY YEAR heatmap source (PAGE-01, `wii.app.charts.temporal_heatmap`). NEISS carries
# no incident time-of-day, weekday or month -- `fact_cases.year` is the only temporal dimension in
# the schema -- so tool x year is the finest honest temporal cross-tab available.
#
# `agg_tool_year.parquet` is deliberately NOT read here even though its grain matches. That
# pre-aggregate is a first-paint cache of the DEFAULT population only: it is built at
# `CaseFilter(years=(year, year))` with `age_scope="adults"` hard-coded and no family/sex/body-
# region/diagnosis dimension, and it repeats every year under BOTH the `all_years` and `latest_5`
# window tags. It therefore cannot answer "respecting the sidebar filters" for anything but the
# untouched default, and branching on that would put two code paths behind one chart. One cached
# `cached_estimate_by_pair(flt, "tool", "year")` call serves every filter combination instead
# (~0.9s cold on the committed 20-year dataset, then `st.cache_data`-warm).
# ---------------------------------------------------------------------------

# Rows shown on the heatmap. 18 keeps the grid readable while still reaching past the fully-stable
# head of the ranking into tools whose cells genuinely suppress, so the suppression treatment is
# visible in the default view rather than only under unusual filters.
TEMPORAL_HEATMAP_TOOL_LIMIT = 18

# Per-year cells have exactly one honest basis: the period total of that one-year period. The
# sidebar's Annual average toggle is never threaded in -- `wii.metrics.estimator` divides
# `annual_average` by the whole window's `n_years`, so an "annual average" tool x year cell would
# be one year's estimate divided by the 20-year span. See `charts.temporal_heatmap`'s docstring.
_TEMPORAL_HEATMAP_BASIS = "period_total"


@st.cache_data(ttl=None)
def tool_year_matrix(
    flt: CaseFilter, limit: int = TEMPORAL_HEATMAP_TOOL_LIMIT
) -> tuple[pl.DataFrame, int]:
    """Returns `(frame, omitted_tool_count)` for the TOOL BURDEN BY YEAR heatmap.

    The frame is a COMPLETE tool x year rectangle -- one row for every (shown tool, available
    year) pair, with pairs absent from the query result carried as explicitly suppressed cells
    (`state="suppressed"`, `raw_n=0`) rather than dropped. A dropped cell would collapse the grid
    and let a year with no reportable cases read as a year that was never surveyed.

    Like `trend_frame`, the sidebar's YEAR RANGE is deliberately ignored (the same Flagged
    Assumption): the axis is always `available_years()`, so the record is never cropped to the
    default latest-5 window, which would leave a 5-column heatmap. Every other filter
    (age_scope, families, tools, sexes, body_regions, diagnosis_categories) IS applied, via the
    same `flt` that reaches every other chart on the page.

    Row order is `stability_badge.order_ranked` over the same year-free filter at the period
    total, i.e. highest total burden across the whole record first, then limited, then
    suppressed; `limit` truncates that ranking and `omitted_tool_count` reports how many tools
    fell off, for the page's caption.
    """
    years = available_years()
    if not years:
        return pl.DataFrame(), 0

    year_free_flt = replace(flt, years=None)
    ranked = stability_badge.order_ranked(
        list(cached_estimate_by(year_free_flt, "tool").iter_rows(named=True)),
        _TEMPORAL_HEATMAP_BASIS,
    )
    ranked_ids = [row["group_id"] for row in ranked]
    shown_ids = ranked_ids[:limit]
    omitted = max(0, len(ranked_ids) - len(shown_ids))
    if not shown_ids:
        return pl.DataFrame(), omitted

    tools_dim = load_dims()["tools"]
    tool_labels = dict(zip(tools_dim["tool_id"], tools_dim["label"], strict=True))
    shown = set(shown_ids)

    resolved_cells: dict[tuple[str, int], dict] = {}
    pair_df = cached_estimate_by_pair(year_free_flt, "tool", "year")
    for row in pair_df.iter_rows(named=True):
        tool_id = row["group_id_1"]
        if tool_id not in shown:
            continue
        resolved = stability_badge.resolve(row, _TEMPORAL_HEATMAP_BASIS)
        resolved_cells[(tool_id, int(row["group_id_2"]))] = {
            "value": resolved.value,
            "ci_low": resolved.ci_low,
            "ci_high": resolved.ci_high,
            "raw_n": resolved.raw_n,
            "state": resolved.state,
        }

    rows: list[dict] = []
    for tool_id in shown_ids:
        label = tool_labels.get(tool_id, tool_id)
        for year in years:
            cell = resolved_cells.get((tool_id, year))
            if cell is None:
                cell = {
                    "value": None,
                    "ci_low": None,
                    "ci_high": None,
                    "raw_n": 0,
                    "state": stability_badge.STATE_SUPPRESSED,
                }
            rows.append({"tool_id": tool_id, "tool_label": label, "year": year, **cell})
    return pl.DataFrame(rows, infer_schema_length=None), omitted
