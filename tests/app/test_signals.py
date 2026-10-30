"""Signals rendering and within-kind descriptive ranking regressions."""

from __future__ import annotations

import pytest

import wii.app.pages.signals as signals_page
from wii.analytics.insights import Insight, load_thresholds, scan_guardrail_vocabulary
from wii.app.components.insight_card import EMPTY_STATE_COPY
from wii.app.pages.signals import (
    SignalConfig,
    load_signal_config,
    rank_signals,
)

from .conftest import APP_TIMEOUT

_SIGNALS_PATH = "src/wii/app/pages/signals.py"

THRESHOLDS = load_thresholds()
CONFIG = load_signal_config()


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


# ---------------------------------------------------------------------------
# Render paths
# ---------------------------------------------------------------------------


def test_signals_default_renders_grouped_feed(app_test):
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
    card_count = feed_html.count(_CARD_MARKER)
    assert 0 < card_count <= CONFIG.max_cards
    assert "High impact" not in feed_html
    assert "qualifying threshold" not in feed_html
    assert "wii-signal-strength" not in feed_html
    assert "workshop baseline" not in feed_html
    assert at.subheader

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
    at.session_state["f_families"] = ("saws",)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    headers = [h.value for h in at.header]
    assert any(signals_page.FEED_HEADER in h for h in headers)
    feed = _feed_markdown(at)
    assert feed
    assert "workshop-wide" not in feed
    assert "workshop baseline" not in feed
    assert "current-filter baseline" in feed


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


def test_signals_copy_passes_guardrails():
    for name in dir(signals_page):
        if name.startswith("_"):
            continue
        value = getattr(signals_page, name)
        if isinstance(value, str):
            assert scan_guardrail_vocabulary(value) == [], name
        elif isinstance(value, dict):
            for item in value.values():
                if isinstance(item, str):
                    assert scan_guardrail_vocabulary(item) == []


def test_ranking_does_not_compare_different_units_or_assign_tiers():
    share = _insight(kind="severity", effect_size=2, raw_n=100)
    trend = _insight(kind="trend", effect_size=90, raw_n=500)
    higher_share = _insight(kind="severity", effect_size=3, raw_n=100, tool_id="router")
    ranked = rank_signals([share, trend, higher_share], CONFIG)
    assert ranked == [higher_share, trend, share]
    assert rank_signals(list(reversed([share, trend, higher_share])), CONFIG) == ranked


def test_feed_cap_preserves_kind_representation():
    shares = [_insight(effect_size=10 + i, raw_n=200, tool_id=f"tool_{i}") for i in range(20)]
    trend = _insight(kind="trend", effect_size=20, raw_n=200)
    config = SignalConfig(candidate_pool=100, max_cards=3)
    ranked = rank_signals([*shares, trend], config)
    assert len(ranked) == 3
    assert trend in ranked
    assert rank_signals([], config) == []


def test_load_signal_config_rejects_a_truncated_block(tmp_path):
    import pytest

    path = tmp_path / "insights.yaml"
    path.write_text("signals:\n  candidate_pool: 10\n", encoding="utf-8")
    with pytest.raises(ValueError, match="max_cards"):
        load_signal_config(path)


def test_filtered_baseline_copy_names_current_filters():
    for kind in ("severity", "disproportionate", "baseline_comparison"):
        assert "current filters" in signals_page.KIND_BASELINES[kind]
        assert "workshop-wide" not in signals_page.KIND_BASELINES[kind]


def test_peer_findings_invariant_under_display_basis():
    from dataclasses import replace

    from wii.analytics.insights import generate_insights
    from wii.app.data import insight_frames
    from wii.metrics import CaseFilter

    frames = [insight_frames(CaseFilter(), basis) for basis in ("annual_average", "period_total")]
    peers = [frame.peer_tool_burden for frame in frames]
    assert peers[0]
    assert peers[0].keys() == peers[1].keys()
    # DuckDB's parallel weighted sums may differ at machine precision between queries.
    # Membership, reportability, sample counts, and displayed findings must be unchanged.
    for tool_id, annual in peers[0].items():
        period = peers[1][tool_id]
        assert (annual.label, annual.raw_n, annual.stable) == (
            period.label,
            period.raw_n,
            period.stable,
        )
        for field in ("value", "ci_low", "ci_high"):
            assert getattr(annual, field) == pytest.approx(getattr(period, field), rel=1e-12)
    results = [
        [
            i
            for i in generate_insights(
                frame, replace(THRESHOLDS, max_cards=CONFIG.candidate_pool), basis
            )
            if i.kind == "baseline_comparison"
        ]
        for frame, basis in zip(frames, ("annual_average", "period_total"), strict=True)
    ]
    assert results[0], "fixture must produce a peer comparison"
    assert len(results[0]) == len(results[1])
    for annual, period in zip(*results, strict=True):
        assert (annual.kind, annual.tool_id, annual.text, annual.raw_n) == (
            period.kind,
            period.tool_id,
            period.text,
            period.raw_n,
        )
        assert annual.effect_size == pytest.approx(period.effect_size, rel=1e-12)
        assert [label for label, _ in annual.numbers] == [label for label, _ in period.numbers]
        assert [value for _, value in annual.numbers] == pytest.approx(
            [value for _, value in period.numbers], rel=1e-12
        )


def test_trend_adapter_uses_configured_window_length(monkeypatch):
    from dataclasses import replace

    import wii.app.data as data_module
    from wii.metrics import CaseFilter

    queries = []
    monkeypatch.setattr(
        data_module, "load_thresholds", lambda: replace(THRESHOLDS, trend_window_years=2)
    )
    monkeypatch.setattr(data_module, "available_years", lambda: list(range(2019, 2026)))
    monkeypatch.setattr(data_module, "cached_estimate_total", lambda flt: queries.append(flt.years))
    monkeypatch.setattr(data_module, "_cell_from_estimate_record", lambda *args: None)
    data_module._trend_cells(CaseFilter())
    assert queries == [(2024, 2025), (2022, 2023)]


def test_unknown_finding_kind_is_rendered(monkeypatch):
    headings = []
    markup = []
    monkeypatch.setattr(signals_page.st, "subheader", headings.append)
    monkeypatch.setattr(signals_page.st, "markdown", lambda text, **kwargs: markup.append(text))
    finding = _insight(kind="future_kind", effect_size=2, raw_n=100)
    signals_page._render_feed([finding])
    assert headings == ["Future kind"]
    assert finding.text in markup[0]
