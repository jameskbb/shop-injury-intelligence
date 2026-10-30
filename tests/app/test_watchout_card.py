"""D-03/T-05-15 WATCH-OUTS card rendering tests: `watchout_card.py` builds pure HTML strings from
hand-built `Watchout`/`CategoryCell`/`Insight` values -- no streamlit, no database.
"""

from __future__ import annotations

from wii.analytics.insights import CategoryCell, Insight, Watchout
from wii.app.components.watchout_card import (
    FIELD_LABELS,
    NO_THRESHOLD_COPY,
    watchout_card_html,
    watchout_grid_html,
)
from wii.metrics.stability import SUPPRESSED_LABEL

_DISTINCTIVE_INSIGHT = Insight(
    kind="disproportionate",
    text="Table Saw shows a higher amputation share than the workshop baseline.",
    tool_id="table_saw",
    numbers=(("amputation share", 0.30), ("workshop baseline", 0.10)),
    raw_n=200,
    effect_size=3.0,
)
_STANDS_OUT_INSIGHT = Insight(
    kind="severity",
    text="Table Saw shows a higher escalated-care share than the workshop baseline.",
    tool_id="table_saw",
    numbers=(("escalated-care share", 0.25), ("workshop baseline", 0.10)),
    raw_n=150,
    effect_size=2.5,
)


def _region(state="stable", value=0.4):
    return CategoryCell(category_id="hand", label="Hand", value=value, raw_n=100, state=state)


def _mechanism(state="stable", value=0.3):
    return CategoryCell(
        category_id="kickback_ejection",
        label="Kickback Ejection",
        value=value,
        raw_n=100,
        state=state,
    )


def test_watchout_card_full_fields_render_all_four_labels():
    w = Watchout(
        tool_id="table_saw",
        tool_label="Table Saw",
        primary_region=_region(),
        distinctive=_DISTINCTIVE_INSIGHT,
        distinctive_label="Amputation",
        common_mechanism=_mechanism(),
        stands_out=_STANDS_OUT_INSIGHT,
    )
    out = watchout_card_html(w)
    for label in FIELD_LABELS:
        assert label in out
    assert "Amputation" in out
    assert "Kickback Ejection" in out
    assert NO_THRESHOLD_COPY not in out


def test_watchout_card_nothing_clears_shows_fallback_and_hides_two_labels():
    w = Watchout(
        tool_id="table_saw",
        tool_label="Table Saw",
        primary_region=_region(),
        distinctive=None,
        distinctive_label=None,
        common_mechanism=_mechanism(),
        stands_out=None,
    )
    out = watchout_card_html(w)
    assert NO_THRESHOLD_COPY in out
    assert FIELD_LABELS[1] not in out  # "Distinctive injury"
    assert FIELD_LABELS[3] not in out  # "What stands out"
    assert FIELD_LABELS[0] in out  # "Primary body region"
    assert FIELD_LABELS[2] in out  # "Common mechanism"


def test_watchout_card_suppressed_region_shows_not_reportable_and_label_other_fields_still_render():
    w = Watchout(
        tool_id="table_saw",
        tool_label="Table Saw",
        primary_region=CategoryCell(
            category_id="hand", label="Hand", value=None, raw_n=5, state="suppressed"
        ),
        distinctive=_DISTINCTIVE_INSIGHT,
        distinctive_label="Amputation",
        common_mechanism=_mechanism(),
        stands_out=_STANDS_OUT_INSIGHT,
    )
    out = watchout_card_html(w)
    assert "Not reportable" in out
    assert SUPPRESSED_LABEL in out
    assert "Amputation" in out
    assert "Kickback Ejection" in out


def test_watchout_card_escapes_tool_label():
    w = Watchout(
        tool_id="table_saw",
        tool_label="<b>Saw</b>",
        primary_region=None,
        distinctive=None,
        distinctive_label=None,
        common_mechanism=None,
        stands_out=None,
    )
    out = watchout_card_html(w)
    assert "<b>Saw</b>" not in out
    assert "&lt;b&gt;Saw&lt;/b&gt;" in out


def test_watchout_card_html_has_no_blank_line():
    w = Watchout(
        tool_id="table_saw",
        tool_label="Table Saw",
        primary_region=_region(),
        distinctive=_DISTINCTIVE_INSIGHT,
        distinctive_label="Amputation",
        common_mechanism=_mechanism(),
        stands_out=_STANDS_OUT_INSIGHT,
    )
    out = watchout_card_html(w)
    assert "\n" not in out


def test_watchout_grid_html_wraps_every_card():
    w1 = Watchout("table_saw", "Table Saw", None, None, None, None, None)
    w2 = Watchout("drill_press", "Drill Press", None, None, None, None, None)
    out = watchout_grid_html((w1, w2))
    assert out.startswith('<div class="wii-watchout-grid">')
    assert out.count("wii-watchout-card") == 2
    assert "Table Saw" in out
    assert "Drill Press" in out
