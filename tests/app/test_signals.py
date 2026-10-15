"""PAGE-09 Signals tests: the AppTest render paths (default filters, non-default filters, the
two "nothing qualifies" paths) plus unit coverage of the page's own pure logic -- kind-normalized
strength, tier assignment AT and one step below every configured threshold, and feed ordering.

The tier cases are built from synthetic `Insight` objects and the REAL `load_thresholds()` /
`load_signal_config()` values, so they assert the behaviour the shipped config produces rather
than restating its numbers: an "at threshold" case is derived by multiplying the configured
threshold multiple back up by the kind's own qualifying gate.
"""

from __future__ import annotations

import wii.app.pages.signals as signals_page
from wii.analytics.insights import Insight, load_thresholds, scan_guardrail_vocabulary
from wii.app.components.insight_card import EMPTY_STATE_COPY
from wii.app.pages.signals import (
    SignalConfig,
    TierRule,
    assign_tier,
    load_signal_config,
    qualifying_threshold,
    rank_signals,
    signal_strength,
)

from .conftest import APP_TIMEOUT

_SIGNALS_PATH = "src/wii/app/pages/signals.py"

THRESHOLDS = load_thresholds()
CONFIG = load_signal_config()
HIGH, NOTABLE = CONFIG.tiers[0], CONFIG.tiers[1]


_CARD_MARKER = '<div class="wii-insight-card">'


def _feed_markdown(at) -> str:
    """Only the page's own feed block -- theme.py's injected <style> also mentions the shared
    card classes, so the card DIV itself is the unambiguous marker."""
    return "".join(m.value for m in at.markdown if _CARD_MARKER in m.value)


def _switch_to_signals(at):
    at.run(timeout=APP_TIMEOUT)
    at.switch_page(_SIGNALS_PATH)


def _insight(
    *, kind: str = "disproportionate", effect_size: float, raw_n: int, tool_id: str = "table_saw"
) -> Insight:
    return Insight(
        kind=kind,
        text=f"{tool_id} shows a higher share than the workshop baseline.",
        tool_id=tool_id,
        numbers=(("share", 0.4), ("workshop baseline", 0.1)),
        raw_n=raw_n,
        effect_size=effect_size,
    )


def _effect_for_strength(strength: float, kind: str = "disproportionate") -> float:
    """The `effect_size` a `kind` candidate needs to score exactly `strength` threshold
    multiples, derived from the configured gate rather than hard-coded."""
    return strength * qualifying_threshold(kind, THRESHOLDS)


# ---------------------------------------------------------------------------
# Render paths
# ---------------------------------------------------------------------------


def test_signals_default_renders_ranked_tiered_feed(app_test):
    at = app_test
    _switch_to_signals(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    titles = [t.value for t in at.title]
    assert any(signals_page.TITLE in t for t in titles)

    headers = [h.value for h in at.header]
    for heading in (signals_page.FEED_HEADER, signals_page.METHOD_HEADER):
        assert any(heading in h for h in headers), f"missing section heading {heading!r}"

    feed_html = _feed_markdown(at)
    assert feed_html, "no signal feed rendered at default filters"
    # A significance tier tag on every card, and more than one tier present -- the feed is a
    # ranked cross-cutting set, not a single flat list.
    card_count = feed_html.count(_CARD_MARKER)
    assert card_count == sum(
        feed_html.count(f">{rule.name}</span>") for rule in CONFIG.tiers
    ) + feed_html.count(f">{CONFIG.base_tier}</span>")
    assert card_count <= CONFIG.max_cards
    assert HIGH.name in feed_html

    # Requirement: each card carries its comparison baseline and its supporting raw N.
    assert "Compared with" in feed_html
    assert "sample cases" in feed_html

    # This page has no tool selector of its own, so the global sidebar tool filter stays visible.
    assert any(ms.key == "widget_global_tool_filter" for ms in at.multiselect)


def test_signals_nondefault_filter_renders(app_test):
    """A 2-year span + All ages + period-total basis is not the default view -- exercises the
    `cached_estimate_total` query path and a non-default basis through the same feed."""
    at = app_test
    _switch_to_signals(at)
    at.session_state["year_range"] = (2024, 2025)
    at.session_state["age_scope"] = "all"
    at.session_state["basis"] = "period_total"
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    headers = [h.value for h in at.header]
    assert any(signals_page.FEED_HEADER in h for h in headers)


def test_signals_insufficient_signals_shows_plain_note_not_padding(app_test):
    """A single selected tool leaves every share dimension comparing that tool against a baseline
    made of itself, and `baseline_comparison` needs at least two qualified tools -- so nothing
    clears the engine's gates. The page must reuse the shared component's empty-state copy and
    render no cards, rather than padding the feed with weaker statements."""
    at = app_test
    _switch_to_signals(at)
    at.session_state["global_tool_filter"] = ("table_saw",)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    info_values = [i.value for i in at.info]
    assert any(EMPTY_STATE_COPY in v for v in info_values)

    assert _feed_markdown(at) == ""


def test_signals_zero_case_filter_shows_empty_state(app_test):
    """poisoning + toe matches zero cases in the committed data (the same combination
    tests/app/test_pages_smoke.py uses) -- the page names the active filters instead of running
    the engine over an absent estimate."""
    at = app_test
    _switch_to_signals(at)
    at.session_state["f_diagnosis_categories"] = ("poisoning",)
    at.session_state["f_body_regions"] = ("toe",)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    info_values = [i.value for i in at.info]
    assert any("No cases match the current filters" in v for v in info_values)

    headers = [h.value for h in at.header]
    assert all(signals_page.FEED_HEADER not in h for h in headers)


# ---------------------------------------------------------------------------
# Honesty guardrails
# ---------------------------------------------------------------------------


def test_signals_copy_passes_guardrails():
    """Every string this page can put on screen -- module constants plus the per-kind label and
    baseline dictionaries -- is scanned for the INS-01 banned vocabulary, the same way
    tests/app/test_body_map_page.py scans the Body Map's copy."""
    for name in dir(signals_page):
        if name.startswith("_"):
            continue
        value = getattr(signals_page, name)
        if isinstance(value, str):
            assert scan_guardrail_vocabulary(value) == [], f"{name} failed guardrail scan"
        elif isinstance(value, dict):
            for key, item in value.items():
                if isinstance(item, str):
                    assert scan_guardrail_vocabulary(item) == [], (
                        f"{name}[{key!r}] failed guardrail scan"
                    )


def test_signals_rendered_card_copy_passes_guardrails(app_test):
    """The guardrail also has to hold for the ASSEMBLED card markup, which interpolates
    engine-generated text and data-derived tool labels into this page's own wording."""
    at = app_test
    _switch_to_signals(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    feed_html = _feed_markdown(at)
    assert feed_html, "no signal feed rendered at default filters"
    assert scan_guardrail_vocabulary(feed_html) == []


def test_tier_names_are_not_hazard_language():
    for rule in CONFIG.tiers:
        assert scan_guardrail_vocabulary(rule.name) == []
    assert scan_guardrail_vocabulary(CONFIG.base_tier) == []


# ---------------------------------------------------------------------------
# Kind-normalized strength
# ---------------------------------------------------------------------------


def test_qualifying_threshold_is_each_kinds_own_gate():
    assert qualifying_threshold("trend", THRESHOLDS) == THRESHOLDS.trend_pct_min
    assert (
        qualifying_threshold("burden_vs_share", THRESHOLDS) == THRESHOLDS.burden_outcome_ratio_min
    )
    for kind in ("severity", "disproportionate", "baseline_comparison"):
        assert qualifying_threshold(kind, THRESHOLDS) == THRESHOLDS.share_ratio_min


def test_signal_strength_is_one_for_a_finding_exactly_at_its_gate():
    for kind in ("severity", "disproportionate", "burden_vs_share", "trend"):
        insight = _insight(kind=kind, effect_size=qualifying_threshold(kind, THRESHOLDS), raw_n=100)
        assert signal_strength(insight, THRESHOLDS) == 1.0


def test_signal_strength_makes_kinds_comparable():
    """A percent-change trend and a ratio share that each cleared their own gate by the same
    factor must score the same, even though their raw effect sizes differ by an order of
    magnitude -- this is the whole reason the feed re-ranks rather than sorting on effect size."""
    trend = _insight(kind="trend", effect_size=_effect_for_strength(2.0, "trend"), raw_n=500)
    share = _insight(effect_size=_effect_for_strength(2.0), raw_n=500)
    assert trend.effect_size != share.effect_size
    assert signal_strength(trend, THRESHOLDS) == signal_strength(share, THRESHOLDS) == 2.0


# ---------------------------------------------------------------------------
# Tier assignment: at, and one step below, each configured threshold
# ---------------------------------------------------------------------------


def test_tier_at_high_impact_threshold():
    assert assign_tier(HIGH.min_strength, HIGH.min_raw_n, CONFIG) == HIGH.name


def test_tier_one_step_below_high_impact_strength_falls_to_notable():
    assert assign_tier(HIGH.min_strength - 0.01, HIGH.min_raw_n, CONFIG) == NOTABLE.name


def test_tier_one_step_below_high_impact_raw_n_falls_to_notable():
    """Sample size is a hard gate, not a tiebreaker: a finding with a top-tier effect measured on
    one case too few is demoted, never promoted on effect size alone."""
    assert assign_tier(HIGH.min_strength, HIGH.min_raw_n - 1, CONFIG) == NOTABLE.name


def test_tier_at_notable_threshold():
    assert assign_tier(NOTABLE.min_strength, NOTABLE.min_raw_n, CONFIG) == NOTABLE.name


def test_tier_one_step_below_notable_strength_falls_to_base_tier():
    assert assign_tier(NOTABLE.min_strength - 0.01, NOTABLE.min_raw_n, CONFIG) == CONFIG.base_tier


def test_tier_one_step_below_notable_raw_n_falls_to_base_tier():
    assert assign_tier(NOTABLE.min_strength, NOTABLE.min_raw_n - 1, CONFIG) == CONFIG.base_tier


def test_tier_at_the_engines_own_minimum_is_the_base_tier():
    """A finding that only just cleared the engine's gates (strength 1.0 on the engine's minimum
    sample count) is shown, but is never promoted into a named tier."""
    assert assign_tier(1.0, THRESHOLDS.min_raw_n, CONFIG) == CONFIG.base_tier


def test_tier_threshold_comparison_is_inclusive_under_float_error():
    """0.30/0.20 is 1.4999999999999998 in IEEE-754, so an at-threshold multiple assembled from
    ordinary decimals must still count as AT the threshold."""
    strength = 0.3 / 0.2 * (NOTABLE.min_strength / 1.5)
    assert assign_tier(strength, NOTABLE.min_raw_n, CONFIG) == NOTABLE.name


def test_tier_thresholds_come_from_the_config_not_the_page_source():
    source = (signals_page.__file__ or "").replace(".pyc", ".py")
    text = open(source, encoding="utf-8").read()
    for rule in CONFIG.tiers:
        assert str(rule.min_strength) not in text, (
            f"{rule.name} min_strength is a literal in signals.py; it belongs in insights.yaml"
        )


# ---------------------------------------------------------------------------
# Feed ordering and cap
# ---------------------------------------------------------------------------


def test_rank_signals_orders_by_tier_then_strength_not_raw_effect_size():
    weak_trend = _insight(
        kind="trend",
        effect_size=_effect_for_strength(1.2, "trend"),
        raw_n=HIGH.min_raw_n,
        tool_id=None,
    )
    strong_share = _insight(
        effect_size=_effect_for_strength(3.0), raw_n=HIGH.min_raw_n, tool_id="table_saw"
    )
    assert weak_trend.effect_size > strong_share.effect_size

    ranked = rank_signals([weak_trend, strong_share], THRESHOLDS, CONFIG)
    assert [s.insight for s in ranked] == [strong_share, weak_trend]
    assert ranked[0].tier == HIGH.name


def test_rank_signals_puts_a_lower_tier_below_a_higher_one_even_at_a_higher_strength():
    """A huge effect on a thin sample must not outrank a solidly-supported top-tier finding."""
    thin_but_extreme = _insight(
        effect_size=_effect_for_strength(9.0), raw_n=NOTABLE.min_raw_n, tool_id="router"
    )
    well_supported = _insight(
        effect_size=_effect_for_strength(HIGH.min_strength), raw_n=HIGH.min_raw_n, tool_id="sander"
    )
    ranked = rank_signals([thin_but_extreme, well_supported], THRESHOLDS, CONFIG)
    assert [s.tier for s in ranked] == [HIGH.name, NOTABLE.name]
    assert ranked[0].insight is well_supported


def test_rank_signals_caps_the_feed_and_never_pads_it():
    many = [
        _insight(effect_size=_effect_for_strength(3.0), raw_n=HIGH.min_raw_n, tool_id=f"tool_{i}")
        for i in range(CONFIG.max_cards + 7)
    ]
    assert len(rank_signals(many, THRESHOLDS, CONFIG)) == CONFIG.max_cards
    assert rank_signals([], THRESHOLDS, CONFIG) == []


def test_rank_signals_is_deterministic_for_equal_scores():
    a = _insight(effect_size=_effect_for_strength(2.0), raw_n=HIGH.min_raw_n, tool_id="b_tool")
    b = _insight(effect_size=_effect_for_strength(2.0), raw_n=HIGH.min_raw_n, tool_id="a_tool")
    assert [s.insight.tool_id for s in rank_signals([a, b], THRESHOLDS, CONFIG)] == [
        "a_tool",
        "b_tool",
    ]
    assert [s.insight.tool_id for s in rank_signals([b, a], THRESHOLDS, CONFIG)] == [
        "a_tool",
        "b_tool",
    ]


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def test_load_signal_config_reads_the_shipped_thresholds_file():
    assert CONFIG.tiers, "no significance tiers configured"
    assert CONFIG.base_tier
    # Tiers are strongest-first, and every tier demands more than the engine's own minimum
    # sample count -- otherwise a tier would mean nothing beyond "it qualified".
    strengths = [rule.min_strength for rule in CONFIG.tiers]
    assert strengths == sorted(strengths, reverse=True)
    for rule in CONFIG.tiers:
        assert rule.min_strength >= 1.0
        assert rule.min_raw_n > THRESHOLDS.min_raw_n
    # The feed must be able to show more than the engine's own per-section cap, or the page adds
    # nothing over the Overview's insight section.
    assert CONFIG.max_cards > THRESHOLDS.max_cards
    assert CONFIG.candidate_pool >= CONFIG.max_cards


def test_load_signal_config_rejects_a_truncated_block(tmp_path):
    path = tmp_path / "insights.yaml"
    path.write_text("signals:\n  candidate_pool: 10\n", encoding="utf-8")
    try:
        load_signal_config(path)
    except ValueError as exc:
        assert "max_cards" in str(exc)
    else:  # pragma: no cover - the call above must raise
        raise AssertionError("a signals block missing keys must raise")


def test_assign_tier_falls_back_to_the_base_tier_with_no_rules():
    empty = SignalConfig(candidate_pool=1, max_cards=1, tiers=(), base_tier="MODERATE")
    assert assign_tier(99.0, 10_000, empty) == "MODERATE"


def test_assign_tier_uses_the_first_satisfied_rule_in_configured_order():
    config = SignalConfig(
        candidate_pool=10,
        max_cards=5,
        tiers=(TierRule("TOP", 2.0, 100), TierRule("MIDDLE", 1.5, 50)),
        base_tier="BASE",
    )
    assert assign_tier(2.5, 100, config) == "TOP"
    assert assign_tier(2.5, 99, config) == "MIDDLE"
    assert assign_tier(2.5, 49, config) == "BASE"
