"""PAGE-09 Signals: the one cross-cutting, ranked feed of every deterministic finding the INS-01
engine (`wii.analytics.insights`) produces across ALL tools at once, each card carrying a
significance tier.

This page detects nothing of its own. It calls the SAME `insight_frames`/`generate_insights` pair
the Overview's "What the data is saying" section calls (so it shares that section's
`st.cache_data` entry for the active filter + basis, and inherits every INS-01 gate: both compared
cells STABLE, both at or above `min_raw_n` sample cases, unrounded values, no lower/safer
direction). What it adds is scope and ordering: Overview shows the engine's own top
`max_cards` findings, while this page pulls the whole qualifying candidate pool, re-ranks it on a
kind-normalized scale, and tiers it.

WHY RE-RANK. `Insight.effect_size` is not comparable across the five kinds -- a `trend` effect
size is a percent change (tens), a share/burden effect size is a ratio (single digits) -- so a
straight effect-size sort is dominated by whichever kind happens to use the larger unit. Each
finding is instead normalized to its THRESHOLD MULTIPLE: its effect size divided by the gate its
own kind had to clear (`share_ratio_min`, `burden_outcome_ratio_min`, `trend_pct_min`). Every
qualifying finding therefore scores at least one, and a given multiple means the same thing for
every kind -- which is what makes a single tier scale defensible across all five of them.

Tier rules, the candidate pool size and the feed cap all live in `insights.yaml`'s `signals:`
block alongside the engine's own thresholds (read here the same way
`wii.analytics.mechanism_trend.load_trend_gate` reads its `mechanism_trend_*` keys) -- no tier
number is written as a literal in this module.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, replace
from pathlib import Path

import streamlit as st
import yaml

from wii.analytics import insights as insights_module
from wii.analytics.insights import Insight, Thresholds, generate_insights, load_thresholds
from wii.app import filters, theme
from wii.app.components.filter_context import render_title_block
from wii.app.components.insight_card import format_number, render_insight_section
from wii.app.components.stability_badge import empty_state
from wii.app.data import cached_estimate_total, default_overall_estimate, insight_frames
from wii.metrics import CaseFilter

TITLE = "What stands out across every tool"
SUBTITLE = (
    "One ranked feed of the deterministic patterns that clear the stability and effect-size "
    "thresholds under the current filter selection, pulled across all tools at once, not one "
    "tool at a time."
)

FEED_HEADER = "Ranked signals"
METHOD_HEADER = "How these signals are detected"

FEED_CAPTION = (
    "Ordered by significance tier, then by how far past its own qualifying threshold each finding "
    "reached. Every statement is an association measured among ER-treated cases, never a "
    "per-use rate: NEISS has no exposure denominator, so nothing here says how likely an injury "
    "is when a tool is used."
)

METHOD_INTRO = (
    "Detection is deterministic and reproducible: the same filter selection always produces the "
    "same feed. A finding is only eligible when both compared values are stable national "
    "estimates (never a limited or suppressed one) and both rest on at least the minimum number "
    "of sample cases. Nothing is padded to fill the page."
)

TIER_INTRO = (
    "Effect sizes are not comparable between kinds of finding, so each one is scored as a "
    "threshold multiple: how many times its own qualifying threshold it cleared. A tier also "
    "requires a supporting sample-case count, so a large difference measured on a handful of "
    "cases is never promoted."
)

SAMPLE_CASE_NOTE = (
    "Sample cases are the unweighted NEISS records behind an estimate, not a national count."
)

# One short, honest name per INS-01 kind, plus the comparison baseline each kind measures
# against. `Insight.text` already names the magnitude and the supporting sample size; these make
# the baseline explicit on the card instead of leaving it implied by the sentence.
KIND_LABELS: dict[str, str] = {
    "severity": "Outcome mix",
    "disproportionate": "Category mix",
    "burden_vs_share": "Disproportionate contribution",
    "trend": "Multi-year shift",
    "baseline_comparison": "Volume vs peer average",
}
KIND_BASELINES: dict[str, str] = {
    "severity": "Compared with the workshop-wide share of the same outcome.",
    "disproportionate": "Compared with the workshop-wide share of the same category.",
    "burden_vs_share": "Compared with the same tool's share of total estimated burden.",
    "trend": "Compared with the prior window of equal length.",
    "baseline_comparison": "Compared with the mean per-tool estimate across all stable tools.",
}

# Tier accents are applied by CLASS, never by an inline `style=` attribute: Streamlit's markdown
# sanitizer silently drops inline styles (verified against the rendered capture this session),
# which is also why `theme.py` ships one injected <style> block instead of styling elements
# inline. Classes are indexed by tier RANK (0 = strongest) rather than by tier name, so renaming
# or reordering a tier in `insights.yaml` needs no change here; anything past the last accent
# falls back to the muted base styling. No color literal appears below -- these are the `:root`
# custom properties theme.py already defines from its own tokens (D-06).
#
# The strongest tier deliberately takes the neutral primary accent rather than the severe red:
# the tier measures how far a finding cleared its detection threshold, and must not be read as a
# hazard rating for the tool it names.
# Only the strongest tier takes the accent. Tier 2 used to be painted `--wii-caution`, the app's
# limited/suppressed colour on every other surface, so a NOTABLE finding read as a limited-sample
# finding. Every tier below the first is now plain ink -- the ranking is carried by the tier's own
# name, its order in the feed and the strength bar, none of which can be mistaken for a data
# state.
_TIER_RANK_CLASSES: tuple[str, ...] = ("wii-signal-tier-1",)
_TIER_BASE_CLASS = "wii-signal-tier-base"

# Page-scoped CSS, injected with the feed itself. NO BLANK LINES inside the <style> block: per
# theme.py's own note, Streamlit's CommonMark raw-HTML-block handling passes a <style> through
# verbatim only until the first blank line, after which the remainder leaks out as visible text.
_TIER_CSS = f"""
<style>
/* Sentence case, at label size, with no tracking. A tracked-out all-caps eyebrow repeated above
   every card is the commonest tell of a generated page, and fifteen of them down one feed is
   exactly the pattern the title block's caps exception was carved out to avoid becoming. The
   tier still has to be visible, so the rule underline and the strength bar below carry that
   work -- and the bar says how far past its gate a finding reached, which a two-level word
   never could. */
.wii-signal-tier {{
  display: inline-block;
  font-family: {theme.FONT_NARROW};
  font-size: var(--wii-text-label);
  font-weight: var(--wii-weight-semibold);
  border: none;
  border-bottom: 1px solid var(--wii-rule);
  color: var(--wii-graphite-soft);
  border-radius: 0;
  padding: 0 0 2px;
  margin-bottom: var(--wii-space-sm);
}}
.wii-signal-tier-1 {{
  border-bottom-color: var(--wii-blueprint);
  color: var(--wii-blueprint);
}}
.wii-signal-kind {{
  font-size: var(--wii-text-label);
  color: var(--wii-graphite-soft);
  margin-bottom: var(--wii-space-xs);
}}
/* Rank made visible: a bar whose length is the finding's multiple of its own gate, capped so a
   very strong finding cannot run off the card. Without it a 5.9x and a 1.8x finding were the
   same card and the ranking existed only in reading order. */
.wii-signal-strength {{
  height: 3px;
  background: var(--wii-blueprint);
  margin: 0 0 var(--wii-space-sm);
}}
</style>
"""

_CONFIG_PATH = Path(insights_module.__file__).resolve().parent / "config" / "insights.yaml"

# Same rationale as wii.analytics.insights._FLOAT_EPS: an "exactly at threshold" inclusive
# comparison built from ordinary decimal inputs must not fail on binary-float representation
# error. Far smaller than any gap a real finding can sit at.
_FLOAT_EPS = 1e-9


@dataclass(frozen=True)
class TierRule:
    """One significance tier: a finding is assigned the FIRST rule (in configured order) whose
    threshold multiple AND supporting sample-case count it satisfies."""

    name: str
    min_strength: float
    min_raw_n: int


@dataclass(frozen=True)
class SignalConfig:
    """The `signals:` block of `insights.yaml`. `base_tier` is the name used for a finding that
    cleared the engine's gates but no tier rule -- it is still a fully-gated finding."""

    candidate_pool: int
    max_cards: int
    tiers: tuple[TierRule, ...]
    base_tier: str


@dataclass(frozen=True)
class Signal:
    """One feed row: an engine `Insight` plus the two page-derived values shown on its card."""

    insight: Insight
    tier: str
    strength: float


def load_signal_config(path: Path | str | None = None) -> SignalConfig:
    """Reads the `signals:` block from the SAME `insights.yaml` the engine's own thresholds live
    in -- raises `ValueError` naming what is missing, so a truncated config fails loudly instead
    of silently tiering everything into the base tier."""
    p = Path(path) if path is not None else _CONFIG_PATH
    with open(p, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    block = (doc or {}).get("signals")
    if not block:
        raise ValueError(f"{p}: missing 'signals' block")
    missing = [k for k in ("candidate_pool", "max_cards", "tiers", "base_tier") if k not in block]
    if missing:
        raise ValueError(f"{p}: signals block missing keys {missing}")
    tiers: list[TierRule] = []
    for entry in block["tiers"]:
        tier_missing = [k for k in ("name", "min_strength", "min_raw_n") if k not in entry]
        if tier_missing:
            raise ValueError(f"{p}: signals tier {entry!r} missing keys {tier_missing}")
        tiers.append(
            TierRule(
                name=str(entry["name"]),
                min_strength=float(entry["min_strength"]),
                min_raw_n=int(entry["min_raw_n"]),
            )
        )
    return SignalConfig(
        candidate_pool=int(block["candidate_pool"]),
        max_cards=int(block["max_cards"]),
        tiers=tuple(tiers),
        base_tier=str(block["base_tier"]),
    )


def qualifying_threshold(kind: str, thresholds: Thresholds) -> float:
    """The gate `kind` had to clear to become a candidate at all, in that kind's own units --
    the denominator that makes effect sizes comparable across kinds."""
    if kind == "trend":
        return thresholds.trend_pct_min
    if kind == "burden_vs_share":
        return thresholds.burden_outcome_ratio_min
    # severity, disproportionate and baseline_comparison all gate on share_ratio_min (see
    # wii.analytics.insights._share_vs_baseline / _baseline_comparison_candidates).
    return thresholds.share_ratio_min


def signal_strength(insight: Insight, thresholds: Thresholds) -> float:
    """How many times its own qualifying threshold this finding cleared. Always >= 1.0 for a
    finding the engine returned, and comparable between kinds."""
    gate = qualifying_threshold(insight.kind, thresholds)
    if not gate:
        return 1.0
    return insight.effect_size / gate


def assign_tier(strength: float, raw_n: int, config: SignalConfig) -> str:
    """First configured rule satisfied by BOTH the threshold multiple and the supporting
    sample-case count wins; `config.base_tier` otherwise. Comparisons are inclusive `>=`, matching
    the engine's own threshold convention."""
    for rule in config.tiers:
        if strength >= rule.min_strength - _FLOAT_EPS and raw_n >= rule.min_raw_n:
            return rule.name
    return config.base_tier


def rank_signals(
    candidates: list[Insight], thresholds: Thresholds, config: SignalConfig
) -> list[Signal]:
    """Tiers every candidate, then orders the feed by tier (configured order, base tier last),
    then by threshold multiple descending, then by tool id and kind so equal scores always
    produce the same feed. Capped at `config.max_cards` -- never padded."""
    signals: list[Signal] = []
    for insight in candidates:
        strength = signal_strength(insight, thresholds)
        tier = assign_tier(strength, insight.raw_n, config)
        signals.append(Signal(insight=insight, tier=tier, strength=strength))
    signals.sort(
        key=lambda s: (
            tier_rank(s.tier, config),
            -s.strength,
            s.insight.tool_id or "",
            s.insight.kind,
        )
    )
    return signals[: config.max_cards]


def collect_signals(flt: CaseFilter, basis: str) -> list[Signal]:
    """The whole page's analysis: the engine's qualifying candidate pool for `flt`/`basis`,
    re-ranked and tiered. Every value comes from `wii.app.data.insight_frames` (the cached,
    basis-resolved metric-layer frames) -- this module never touches `wii.metrics` itself."""
    thresholds = load_thresholds()
    config = load_signal_config()
    frames = insight_frames(flt, basis)
    pool_thresholds = replace(thresholds, max_cards=config.candidate_pool)
    candidates = generate_insights(frames, pool_thresholds, basis)
    return rank_signals(candidates, thresholds, config)


def tier_rank(tier: str, config: SignalConfig) -> int:
    """Position of `tier` in the configured order (0 = strongest); `len(config.tiers)` for the
    base tier, which therefore always sorts and styles last."""
    for idx, rule in enumerate(config.tiers):
        if rule.name == tier:
            return idx
    return len(config.tiers)


def _tier_tag_html(tier: str, rank: int) -> str:
    accent = _TIER_RANK_CLASSES[rank] if rank < len(_TIER_RANK_CLASSES) else _TIER_BASE_CLASS
    return f'<span class="wii-signal-tier {accent}">{html.escape(tier)}</span>'


#: A finding at or past this multiple of its own gate fills the bar. Chosen so the common range
#: (1x to 6x) spreads across the full width instead of bunching at the left.
STRENGTH_BAR_FULL = 6.0


def strength_bar_width(strength: float) -> float:
    """Percent width of a signal's strength bar: its multiple of its own qualifying threshold,
    clipped to [0, 100]. Every qualifying finding scores at least 1.0, so the bar never renders
    at zero for something that is on the page."""
    return max(0.0, min(1.0, strength / STRENGTH_BAR_FULL)) * 100.0


def _strength_bar_html(strength: float) -> str:
    width = strength_bar_width(strength)
    return (
        f'<div class="wii-signal-strength" style="width:{width:.0f}%" '
        f'role="img" aria-label="{strength:.1f} times its qualifying threshold"></div>'
    )


def _number_html(label: str, value: float) -> str:
    # One formatting layer for the whole product: the same helper the insight cards use, so a
    # share or an estimate is lettered identically wherever a reader meets it.
    formatted = format_number(value)
    return (
        f'<span class="wii-insight-number">{html.escape(label)}: '
        f'<span class="wii-insight-number-value">{formatted}</span></span>'
    )


def _card_html(signal: Signal, config: SignalConfig) -> str:
    """One feed card: tier tag, what kind of finding it is and how far past its own threshold it
    reached, the engine's own statement (what differs and by how much), both compared numbers,
    the comparison baseline, and the supporting sample-case count. Every interpolated value is
    `html.escape`d: `Insight.text` and every `numbers` label are built from data-derived
    tool/category labels, exactly like `insight_card.py`'s own escaping contract."""
    insight = signal.insight
    kind_label = KIND_LABELS.get(insight.kind, insight.kind.replace("_", " ").capitalize())
    baseline = KIND_BASELINES.get(insight.kind, "")
    numbers = "".join(_number_html(label, value) for label, value in insight.numbers)
    multiple = f"{signal.strength:.1f}x its qualifying threshold"
    return (
        '<div class="wii-insight-card">'
        f"{_tier_tag_html(signal.tier, tier_rank(signal.tier, config))}"
        f"{_strength_bar_html(signal.strength)}"
        f'<div class="wii-signal-kind">{html.escape(kind_label)} · {html.escape(multiple)}</div>'
        f'<div class="wii-insight-text">{html.escape(insight.text)}</div>'
        f'<div class="wii-insight-numbers">{numbers}</div>'
        f'<div class="wii-insight-raw-n">{html.escape(baseline)}</div>'
        f'<div class="wii-insight-raw-n">{html.escape(f"n = {insight.raw_n:,} sample cases")}</div>'
        "</div>"
    )


def _render_feed(signals: list[Signal], config: SignalConfig) -> None:
    parts = [_TIER_CSS, '<div class="wii-insight-grid">']
    parts.extend(_card_html(signal, config) for signal in signals)
    parts.append("</div>")
    st.markdown("".join(parts), unsafe_allow_html=True)


def _render_method_section(thresholds: Thresholds, config: SignalConfig) -> None:
    st.header(METHOD_HEADER)
    st.caption(METHOD_INTRO)
    with st.expander("Detection and tier thresholds", expanded=False):
        st.markdown(
            f"- Minimum sample cases behind each compared value: **{thresholds.min_raw_n:,}**\n"
            f"- Share comparisons must reach **{thresholds.share_ratio_min}x** the baseline "
            f"share *and* be at least **{thresholds.share_pp_min} percentage points** higher\n"
            f"- Contribution comparisons must reach "
            f"**{thresholds.burden_outcome_ratio_min}x** a tool's own share of burden\n"
            f"- Multi-year shifts must move at least **{thresholds.trend_pct_min}%** between two "
            f"**{thresholds.trend_window_years}-year** windows with non-overlapping confidence "
            "intervals\n"
            f"- At most **{config.max_cards}** cards are shown; fewer than "
            f"**{thresholds.min_cards_for_section}** qualifying findings shows a plain note "
            "instead of padding\n"
        )
        st.caption(TIER_INTRO)
        tier_lines = "\n".join(
            f"- **{rule.name}**: at least {rule.min_strength}x its qualifying threshold, on at "
            f"least {rule.min_raw_n:,} sample cases"
            for rule in config.tiers
        )
        st.markdown(
            f"{tier_lines}\n"
            f"- **{config.base_tier}**: cleared the detection thresholds above, but not a tier "
            "rule\n"
        )
        st.caption(SAMPLE_CASE_NOTE)


def render() -> None:
    st.title(TITLE)
    st.caption(SUBTITLE)
    render_title_block("Every deterministic finding, ranked by significance")

    flt = filters.current_filter()
    basis = st.session_state.get("basis", "annual_average")
    # Mirrors the Overview's own guard: `insight_frames` resolves a total-burden record, so a
    # filter combination matching zero cases is named here rather than reaching the engine.
    if flt == filters.default_case_filter():
        overall = default_overall_estimate(flt.age_scope)
    else:
        overall = cached_estimate_total(flt)
    if overall is None or overall.raw_n == 0:
        empty_state(filters.filter_summary())
        return

    thresholds = load_thresholds()
    config = load_signal_config()
    signals = collect_signals(flt, basis)

    st.header(FEED_HEADER)
    st.caption(FEED_CAPTION)
    if len(signals) < thresholds.min_cards_for_section:
        # Reuses the shared component's empty / under-minimum copy verbatim rather than restating
        # it: below the configured minimum there is nothing to tier, and nothing gets padded in.
        render_insight_section([s.insight for s in signals], thresholds.min_cards_for_section)
    else:
        _render_feed(signals, config)

    _render_method_section(thresholds, config)


render()
