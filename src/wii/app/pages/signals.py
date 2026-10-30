"""Signals: deterministic findings grouped by kind, with descriptive ordering within each kind.

The shared engine applies every eligibility gate. No cross-kind strength, impact or statistical
significance score is assigned here. The bounded selection takes turns across kinds so a large
pool of one kind cannot crowd all other kinds out of the feed.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, replace
from pathlib import Path

import streamlit as st
import yaml

from wii.analytics import insights as insights_module
from wii.analytics.insights import (
    INSIGHT_KIND_ORDER,
    Insight,
    Thresholds,
    generate_insights,
    load_thresholds,
    rank_insights,
)
from wii.app import filters
from wii.app.components.filter_context import render_title_block
from wii.app.components.insight_card import format_number, render_insight_section
from wii.app.components.stability_badge import empty_state
from wii.app.data import cached_estimate_total, default_overall_estimate, insight_frames
from wii.metrics import CaseFilter

TITLE = "What stands out under the current filters"
SUBTITLE = (
    "Patterns across the selected tools that clear the sample, stability and difference "
    "thresholds. Changing the filters changes the tools and cases being compared."
)
FEED_HEADER = "Signals by kind"
METHOD_HEADER = "How these signals are detected"
FEED_CAPTION = (
    "Findings are grouped by kind. Within each kind, larger ratios appear first; multi-year "
    "shifts use absolute percentage change. These descriptive differences are not measures of "
    "statistical significance or injury likelihood when a tool is used."
)
METHOD_INTRO = (
    "Detection is deterministic: the same filters produce the same findings. Both compared "
    "values must be stable and meet the minimum sample-case count. Nothing is padded to fill "
    "the page. NEISS has no exposure denominator."
)
SAMPLE_CASE_NOTE = (
    "Sample cases are the unweighted NEISS records behind an estimate, not a national count."
)
KIND_LABELS: dict[str, str] = {
    "severity": "Outcome mix",
    "disproportionate": "Category mix",
    "burden_vs_share": "Disproportionate contribution",
    "trend": "Multi-year shift",
    "baseline_comparison": "Volume vs peer average",
}
KIND_BASELINES: dict[str, str] = {
    "severity": "Compared with the share of the same outcome under the current filters.",
    "disproportionate": "Compared with the share of the same category under the current filters.",
    "burden_vs_share": "Compared with the tool's share of total burden under the current filters.",
    "trend": "Compared with the prior window of equal length under the same non-year filters.",
    "baseline_comparison": (
        "Compared with the mean annual average among tools that meet annual-average stability "
        "and sample thresholds under the current filters. This comparison always uses annual "
        "averages, regardless of the display basis."
    ),
}
_CONFIG_PATH = Path(insights_module.__file__).resolve().parent / "config" / "insights.yaml"


@dataclass(frozen=True)
class SignalConfig:
    candidate_pool: int
    max_cards: int


def load_signal_config(path: Path | str | None = None) -> SignalConfig:
    p = Path(path) if path is not None else _CONFIG_PATH
    with open(p, encoding="utf-8") as f:
        block = (yaml.safe_load(f) or {}).get("signals")
    if not block:
        raise ValueError(f"{p}: missing 'signals' block")
    missing = [k for k in ("candidate_pool", "max_cards") if k not in block]
    if missing:
        raise ValueError(f"{p}: signals block missing keys {missing}")
    return SignalConfig(
        candidate_pool=int(block["candidate_pool"]), max_cards=int(block["max_cards"])
    )


def rank_signals(candidates: list[Insight], config: SignalConfig) -> list[Insight]:
    """Use the same within-kind ordering and bounded selection as Overview."""
    return rank_insights(candidates, config.max_cards)


def collect_signals(flt: CaseFilter, basis: str) -> list[Insight]:
    thresholds = load_thresholds()
    config = load_signal_config()
    frames = insight_frames(flt, basis)
    candidates = generate_insights(
        frames, replace(thresholds, max_cards=config.candidate_pool), basis
    )
    return rank_signals(candidates, config)


def _number_html(label: str, value: float) -> str:
    return (
        f'<span class="wii-insight-number">{html.escape(label)}: '
        f'<span class="wii-insight-number-value">{format_number(value)}</span></span>'
    )


def _card_html(insight: Insight) -> str:
    numbers = "".join(_number_html(label, value) for label, value in insight.numbers)
    baseline = KIND_BASELINES.get(insight.kind, "")
    return (
        '<div class="wii-insight-card">'
        f'<div class="wii-insight-text">{html.escape(insight.text)}</div>'
        f'<div class="wii-insight-numbers">{numbers}</div>'
        f'<div class="wii-insight-raw-n">{html.escape(baseline)}</div>'
        f'<div class="wii-insight-raw-n">n = {insight.raw_n:,} sample cases</div>'
        "</div>"
    )


def _render_feed(signals: list[Insight]) -> None:
    kinds = [*INSIGHT_KIND_ORDER, *sorted({s.kind for s in signals} - set(INSIGHT_KIND_ORDER))]
    for kind in kinds:
        group = [s for s in signals if s.kind == kind]
        if group:
            st.subheader(KIND_LABELS.get(kind, kind.replace("_", " ").capitalize()))
            st.markdown(
                '<div class="wii-insight-grid">' + "".join(_card_html(s) for s in group) + "</div>",
                unsafe_allow_html=True,
            )


def _render_method_section(thresholds: Thresholds, config: SignalConfig) -> None:
    st.header(METHOD_HEADER)
    st.caption(METHOD_INTRO)
    with st.expander("Detection thresholds and ordering", expanded=False):
        st.markdown(
            f"- Minimum sample cases behind each compared value: **{thresholds.min_raw_n:,}**\n"
            f"- Share comparisons must reach **{thresholds.share_ratio_min}x** the baseline "
            f"share and be at least **{thresholds.share_pp_min} percentage points** higher\n"
            f"- Contribution comparisons must reach "
            f"**{thresholds.burden_outcome_ratio_min}x** a tool's own share of burden\n"
            f"- Multi-year shifts must move at least **{thresholds.trend_pct_min}%** between two "
            f"**{thresholds.trend_window_years}-year** windows"
            + (
                " with non-overlapping confidence intervals\n"
                if thresholds.trend_require_nonoverlapping_ci
                else "\n"
            )
            + f"- Volume comparisons must reach **{thresholds.share_ratio_min}x** the peer mean; "
            "peers and values always use annual-average reportability\n"
            f"- At most **{config.max_cards}** cards are selected by taking turns across kinds, "
            "then displayed in groups. The kind order is fixed, not a strength ranking\n"
            "- Ratios have different attainable ranges for different categories. A larger ratio "
            "does not establish stronger evidence or greater injury severity\n"
        )
        st.caption(SAMPLE_CASE_NOTE)


def render() -> None:
    st.title(TITLE)
    st.caption(SUBTITLE)
    render_title_block("Deterministic findings grouped by kind")
    flt = filters.current_filter()
    basis = st.session_state.get("basis", "annual_average")
    overall = (
        default_overall_estimate(flt.age_scope)
        if flt == filters.default_case_filter()
        else cached_estimate_total(flt)
    )
    if overall is None or overall.raw_n == 0:
        empty_state(filters.filter_summary())
        return
    thresholds = load_thresholds()
    config = load_signal_config()
    signals = collect_signals(flt, basis)
    st.header(FEED_HEADER)
    st.caption(FEED_CAPTION)
    if not signals:
        render_insight_section([], thresholds.min_cards_for_section)
    else:
        _render_feed(signals)
        if len(signals) < thresholds.min_cards_for_section:
            st.caption(
                "Only these findings clear the detection thresholds under the current filters."
            )
    _render_method_section(thresholds, config)


render()
