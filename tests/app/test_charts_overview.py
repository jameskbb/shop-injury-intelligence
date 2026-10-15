"""D-09 Overview chart-builder tests: pure `go.Figure` builders exercised with hand-built Polars
frames -- no AppTest (03-02-PLAN Task 3 `<behavior>`). The one database read pins
`landscape.family_label` to the committed `dim_tool_family` labels so the two cannot drift.
"""

from __future__ import annotations

import polars as pl

from wii.app.charts.injury_mix import build_injury_mix
from wii.app.charts.landscape import (
    CAPTION_X,
    CAPTION_Y,
    build_landscape,
    family_label,
    log_ticks,
    suppressed_count,
)
from wii.app.charts.ranked_bars import build_ranked_bars
from wii.app.theme import (
    CAUTION,
    CONSTRUCTION,
    DIAGNOSIS_COLORS,
    FAMILY_COLORS,
    FONT_SANS,
    MUTED,
    TEXT,
)
from wii.metrics import connect

# ---------------------------------------------------------------------------
# landscape
# ---------------------------------------------------------------------------

_SLASH_LABEL = '3/8" Drill, (Electric)'


def _landscape_row(
    tool_id,
    label,
    family_id,
    estimate,
    escalated_share,
    estimate_state="stable",
    escalated_state="stable",
):
    return {
        "tool_id": tool_id,
        "label": label,
        "family_id": family_id,
        "estimate": estimate,
        "estimate_ci_low": estimate * 0.9,
        "estimate_ci_high": estimate * 1.1,
        "estimate_raw_n": 200,
        "estimate_state": estimate_state,
        "escalated_share": escalated_share,
        "escalated_ci_low": max(0.0, escalated_share - 0.05),
        "escalated_ci_high": min(1.0, escalated_share + 0.05),
        "escalated_raw_n": 150,
        "escalated_state": escalated_state,
    }


def _landscape_df():
    return pl.DataFrame(
        [
            _landscape_row("table_saw", "Table Saw", "saws", 50_000.0, 0.20),
            _landscape_row("hand_plane", "Hand Plane", "manual", 5_000.0, 0.05),
            _landscape_row(
                "router", _SLASH_LABEL, "cutting_shaping", 8_000.0, 0.10, escalated_state="limited"
            ),
            _landscape_row(
                "lathe", "Lathe", "cutting_shaping", 3_000.0, 0.15, estimate_state="suppressed"
            ),
        ]
    )


def test_landscape_excludes_suppressed_tools():
    df = _landscape_df()
    fig = build_landscape(df, "annual_average")
    all_text = " ".join(str(t) for trace in fig.data for t in (trace.text or []))
    assert "Lathe" not in all_text
    assert suppressed_count(df) == 1


def test_landscape_unstable_tools_render_hollow_with_family_outline():
    df = _landscape_df()
    fig = build_landscape(df, "annual_average")
    hollow_trace = next(t for t in fig.data if t.name == "Limited sample")
    assert hollow_trace.marker.color == "rgba(0,0,0,0)"
    # Limited points take the CONSTRUCTION outline, not a family colour: line treatment carries
    # confidence and colour carries category, and a family-coloured hollow ring made the two
    # channels argue. Family hue therefore appears only on points whose position is reportable.
    assert hollow_trace.marker.line.color == CONSTRUCTION
    assert _SLASH_LABEL in hollow_trace.text


def test_landscape_x_axis_is_log():
    fig = build_landscape(_landscape_df(), "annual_average")
    assert fig.layout.xaxis.type == "log"


def test_landscape_has_two_median_reference_lines():
    fig = build_landscape(_landscape_df(), "annual_average")
    shapes = fig.layout.shapes or ()
    assert len(shapes) == 2
    kinds = {s.type for s in shapes}
    assert kinds == {"line"}


def test_landscape_has_both_neutral_caption_strings():
    fig = build_landscape(_landscape_df(), "annual_average")
    annotation_texts = [a.text for a in (fig.layout.annotations or ())]
    assert CAPTION_X in annotation_texts
    assert CAPTION_Y in annotation_texts
    assert CAPTION_X == "Higher reported burden"
    assert CAPTION_Y == "Higher severity among ER cases"


def test_landscape_captions_sit_outside_the_plot_area():
    """The captions used to sit at y=-0.12 / x=-0.08: on the tick labels and on top of the y-axis
    title. Now the x caption hangs below the plot's bottom edge and the y caption sits above its
    top edge, each clear of the axes."""
    fig = build_landscape(_landscape_df(), "annual_average")
    by_text = {a.text: a for a in fig.layout.annotations}
    x_cap, y_cap = by_text[CAPTION_X], by_text[CAPTION_Y]
    assert (x_cap.y, x_cap.yanchor) == (0.0, "top") and x_cap.yshift < -30
    assert (y_cap.y, y_cap.yanchor) == (1.0, "bottom")
    assert y_cap.textangle in (None, 0)
    assert x_cap.font.color == y_cap.font.color == MUTED


def test_landscape_legend_keys_are_neutral_and_data_traces_stay_out_of_it():
    """A data trace's own legend swatch took whichever family colour came first; the key is now
    explicit: one swatch per family present, then stable/limited in neutral grey."""
    fig = build_landscape(_landscape_df(), "annual_average")
    data_traces = [t for t in fig.data if t.x and t.x[0] is not None]
    assert data_traces and all(t.showlegend is False for t in data_traces)
    keys = {t.name: t for t in fig.data if t.x == (None,)}
    assert keys["Stable"].marker.color == MUTED
    assert keys["Limited sample"].marker.line.color == CONSTRUCTION
    assert keys["Saws"].marker.color == FAMILY_COLORS["saws"]
    assert "Abrasive" not in keys  # no abrasive tool in the frame -> no swatch


def test_family_label_matches_the_committed_dimension():
    rows = connect().execute("SELECT family_id, label FROM dim_tool_family").fetchall()
    assert rows
    for family_id, label in rows:
        assert family_label(family_id) == label


def test_log_ticks_follow_a_1_2_5_sequence_across_the_data():
    assert log_ticks(1_400, 31_000) == [1_000, 2_000, 5_000, 10_000, 20_000]
    assert log_ticks(0, 10) == []


def test_landscape_labels_more_than_one_x_tick():
    fig = build_landscape(_landscape_df(), "annual_average")
    assert len(fig.layout.xaxis.tickvals) >= 3


def test_overview_charts_carry_the_product_typeface_explicitly():
    """Streamlit's chart theme replaces the template, so the font must be on each figure."""
    figs = [
        build_landscape(_landscape_df(), "annual_average"),
        build_ranked_bars(_ranked_bars_df(), "period_total", "Estimated injuries"),
        build_injury_mix(_injury_mix_df()),
    ]
    for fig in figs:
        assert fig.layout.font.family == FONT_SANS
        assert fig.layout.margin.t is not None and fig.layout.margin.t <= 48
        assert fig.layout.height


def test_landscape_empty_frame_does_not_raise():
    empty_df = _landscape_df().filter(pl.col("tool_id") == "does_not_exist")
    fig = build_landscape(empty_df, "annual_average")
    assert fig.layout.xaxis.type == "log"


# ---------------------------------------------------------------------------
# ranked_bars
# ---------------------------------------------------------------------------


def _estimate_row(group_id, label, estimate, raw_n=200, cv=0.10):
    se = estimate * cv
    return {
        "group_id": group_id,
        "display_label": label,
        "estimate": estimate,
        "raw_n": raw_n,
        "se": se,
        "cv": cv,
        "ci_low": estimate - 1.96 * se,
        "ci_high": estimate + 1.96 * se,
        "df": 20,
        "stable": True,
        "suppression_reason": [],
        "period_total": estimate,
        "annual_average": estimate,
        "se_annual": se,
        "ci_low_annual": estimate - 1.96 * se,
        "ci_high_annual": estimate + 1.96 * se,
        "n_years": 1,
        "basis": "period",
        "stable_annual": True,
        "suppression_reason_annual": [],
    }


def _ranked_bars_df():
    stable = _estimate_row("table_saw", "Table Saw", 50_000.0, raw_n=500, cv=0.10)
    limited = _estimate_row(
        "router", _SLASH_LABEL, 8_000.0, raw_n=25, cv=0.40
    )  # raw_n<20? no it's 25>=20, but cv>0.33 -> limited
    limited["stable"] = False
    limited["suppression_reason"] = ["cv_gt_0.33"]
    limited["stable_annual"] = False
    limited["suppression_reason_annual"] = ["cv_gt_0.33"]
    suppressed = _estimate_row("lathe", "Lathe", 500.0, raw_n=5, cv=0.60)
    suppressed["stable"] = False
    suppressed["suppression_reason"] = ["est_lt_1200", "n_lt_20", "cv_gt_0.33"]
    suppressed["stable_annual"] = False
    suppressed["suppression_reason_annual"] = ["est_lt_1200", "n_lt_20", "cv_gt_0.33"]
    return pl.DataFrame([stable, limited, suppressed])


def test_ranked_bars_stable_before_limited_ordering():
    df = _ranked_bars_df()
    fig = build_ranked_bars(df, "period_total", "Estimated injuries")
    labels = list(fig.data[0].y)
    assert labels.index("Table Saw") < labels.index(_SLASH_LABEL)
    assert labels.index(_SLASH_LABEL) < labels.index("Lathe")


def test_ranked_bars_mark_limited_rows_without_a_caution_hatch():
    """The amber "/" hatch is retired everywhere, including here: it was drawn at 0.55-0.6 alpha,
    which made the limited rows the loudest marks in the chart. A limited bar now differs from a
    stable one by FILL LIGHTNESS and a dashed construction outline, and by nothing else."""
    df = _ranked_bars_df()
    fig = build_ranked_bars(df, "period_total", "Estimated injuries")
    bar = fig.data[0]
    labels = list(bar.y)
    stable_idx = labels.index("Table Saw")
    limited_idx = labels.index(_SLASH_LABEL)
    if bar.marker.pattern is not None and bar.marker.pattern.shape is not None:
        assert set(bar.marker.pattern.shape) <= {""}
    fills = list(bar.marker.color)
    assert fills[limited_idx] != fills[stable_idx]
    assert CAUTION not in fills


def test_ranked_bars_suppressed_row_shows_dash_and_zero_length_bar():
    df = _ranked_bars_df()
    fig = build_ranked_bars(df, "period_total", "Estimated injuries")
    labels = list(fig.data[0].y)
    values = list(fig.data[0].x)
    texts = list(fig.data[0].text)
    suppressed_idx = labels.index("Lathe")
    assert values[suppressed_idx] == 0.0
    assert texts[suppressed_idx] == "—"


def test_ranked_bars_error_x_from_basis_matched_ci():
    df = _ranked_bars_df()
    fig = build_ranked_bars(df, "period_total", "Estimated injuries")
    labels = list(fig.data[0].y)
    stable_idx = labels.index("Table Saw")
    assert fig.data[0].error_x.array[stable_idx] > 0


def test_ranked_bars_label_with_slash_and_paren_unmodified():
    df = _ranked_bars_df()
    fig = build_ranked_bars(df, "period_total", "Estimated injuries")
    assert _SLASH_LABEL in list(fig.data[0].y)
    hover = "".join(fig.data[0].hovertext)
    assert _SLASH_LABEL in hover


def test_ranked_bars_print_values_past_the_whisker_end():
    """Every non-suppressed bar gets its value, placed at value + upper CI so the number never
    sits on the interval line, and suppressed rows get no number (their "—" is on the bar trace)."""
    fig = build_ranked_bars(_ranked_bars_df(), "period_total", "Estimated injuries")
    bars, labels_trace = fig.data[0], fig.data[1]
    assert labels_trace.mode == "text"
    assert "Lathe" not in labels_trace.y
    y_bars = list(bars.y)
    for x, y in zip(labels_trace.x, labels_trace.y, strict=True):
        i = y_bars.index(y)
        assert x == bars.x[i] + bars.error_x.array[i]
    assert all(t.strip() for t in labels_trace.text)
    assert labels_trace.textfont.color == TEXT


def test_ranked_bars_height_grows_with_rows():
    small = build_ranked_bars(_ranked_bars_df(), "period_total", "Estimated injuries")
    rows = [_estimate_row(f"t{i}", f"Tool {i}", 1_000.0 * (i + 1)) for i in range(30)]
    big = build_ranked_bars(pl.DataFrame(rows), "period_total", "Estimated injuries")
    assert big.layout.height > small.layout.height


def test_ranked_bars_hover_includes_basis_raw_n_and_stability():
    df = _ranked_bars_df()
    fig = build_ranked_bars(df, "period_total", "Estimated injuries")
    hover = "".join(fig.data[0].hovertext)
    assert "period total" in hover
    assert "sample cases (not a national count)" in hover
    assert "Limited sample" in hover


# ---------------------------------------------------------------------------
# injury_mix
# ---------------------------------------------------------------------------


def _injury_mix_df():
    return pl.DataFrame(
        [
            {
                "category": "laceration",
                "label": "Laceration",
                "value": 0.35,
                "ci_low": 0.30,
                "ci_high": 0.40,
                "raw_n": 300,
                "state": "stable",
            },
            {
                "category": "amputation",
                "label": "Amputation",
                "value": 0.05,
                "ci_low": 0.02,
                "ci_high": 0.08,
                "raw_n": 40,
                "state": "limited",
            },
            {
                "category": "other",
                "label": "Other",
                "value": 0.60,
                "ci_low": None,
                "ci_high": None,
                "raw_n": 0,
                "state": "suppressed",
            },
        ]
    )


def test_injury_mix_colors_by_name_from_diagnosis_palette():
    """A STABLE bar is its category's own ink, looked up by name -- never a rotating colorway --
    so laceration is the same colour here, on Tool Profiles, in the Sankey and on the comparison
    heatmap. A limited bar is drawn at a lightened fill instead (line treatment plus a lifted
    fill carry confidence; see `charts.marks.fill_for`), so its hue is deliberately not its
    category's."""
    fig = build_injury_mix(_injury_mix_df())
    colors = list(fig.data[0].marker.color)
    rows = list(_injury_mix_df().sort("value", descending=True).iter_rows(named=True))
    # The suppressed "other" row is dropped from the plot and named in the page caption instead.
    plotted = [r for r in rows if r["state"] != "suppressed"]
    assert len(colors) == len(plotted)
    for color, row in zip(colors, plotted, strict=True):
        if row["state"] == "stable":
            assert color == DIAGNOSIS_COLORS[row["category"]]
        else:
            assert color != DIAGNOSIS_COLORS[row["category"]]


def test_injury_mix_excludes_suppressed_category():
    fig = build_injury_mix(_injury_mix_df())
    labels = list(fig.data[0].x)
    assert "Other" not in labels


def test_injury_mix_prints_each_share_on_its_bar():
    fig = build_injury_mix(_injury_mix_df())
    # A leading thin space keeps a printed value off the bar end it labels.
    assert [t.strip("\u2002\u2009 ") for t in fig.data[0].text] == ["35.0%", "5.0%"]


def test_overview_prints_a_line_instead_of_an_all_suppressed_ranked_chart(monkeypatch):
    """`build_ranked_bars` returns `None` when nothing is drawable, and Overview must not hand
    that to `st.plotly_chart`.

    The builder refuses for a real reason: every bar is zero-length, so Plotly autoranges the
    value axis to [-1, 1] and the `~s` tick format prints SI milli-units ("-500m ... 500m") beside
    a column of em dashes. Overview reaches this case more easily than any other page because it
    drops suppressed rows off the axis first, so a narrow enough filter empties the frame
    entirely.
    """
    from wii.app.charts.ranked_bars import all_suppressed_note, build_ranked_bars

    df = _ranked_bars_df()
    suppressed = df.with_columns(
        pl.lit(0.0).alias("period_total"),
        pl.lit(0.0).alias("annual_average"),
        pl.lit(3).alias("raw_n"),
        pl.lit(0.9).alias("cv"),
    )
    assert build_ranked_bars(suppressed, "period_total", "Estimated injuries") is None
    assert (
        build_ranked_bars(suppressed, "period_total", "Estimated injuries", suppressed_footer=True)
        is None
    )
    note = all_suppressed_note(suppressed, "period_total", "tools")
    assert "reporting floor" in note
    # Nothing is silently dropped: every row this refuses to draw is still named.
    for label in suppressed["display_label"]:
        assert label in note
