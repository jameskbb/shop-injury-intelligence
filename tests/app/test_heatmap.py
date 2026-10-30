"""D-11 INJURY FINGERPRINT + DIAGNOSIS SIGNATURE heatmap tests (03-04 Task 2). `build_fingerprint`
is exercised with hand-built `DimensionCategory`/`ToolCell` frames (the same framework-free shapes
`tests/analytics/test_insights.py` uses) -- no database, no AppTest, mirroring 03-03's
`test_charts_profiles.py` convention.
"""

from __future__ import annotations

import polars as pl

from wii.analytics.insights import DimensionCategory, ToolCell
from wii.app.charts import heatmap
from wii.app.components.stability_badge import LIMITED_SAMPLE_LABEL, SUPPRESSED_LABEL
from wii.app.theme import (
    DIAGNOSIS_COLORS,
    DIVERGING_SCALE,
    MECHANISM_COLORS,
    SEQUENTIAL_SCALE,
)

# ---------------------------------------------------------------------------
# log2_ratio_capped
# ---------------------------------------------------------------------------


def test_log2_ratio_capped_ratio_one_is_zero():
    assert heatmap.log2_ratio_capped(0.20, 0.20) == 0.0


def test_log2_ratio_capped_eight_x_caps_at_two():
    # 1.6 / 0.2 = 8x -> log2(8) = 3, capped to 2 (D-11).
    assert heatmap.log2_ratio_capped(1.6, 0.2) == 2.0


def test_log2_ratio_capped_below_cap_is_uncapped():
    # 0.4 / 0.2 = 2x -> log2(2) = 1.0, well within the +-2 cap.
    assert heatmap.log2_ratio_capped(0.4, 0.2) == 1.0


def test_log2_ratio_capped_value_zero_caps_at_negative_two():
    assert heatmap.log2_ratio_capped(0.0, 0.2) == -2.0


def test_log2_ratio_capped_negative_value_caps_at_negative_two():
    # Shares never go negative in practice, but the cap must never emit -inf regardless.
    assert heatmap.log2_ratio_capped(-0.1, 0.2) == -2.0


def test_log2_ratio_capped_baseline_zero_is_none():
    assert heatmap.log2_ratio_capped(0.1, 0.0) is None


# ---------------------------------------------------------------------------
# build_fingerprint
# ---------------------------------------------------------------------------


def _cell(tool_id, value, raw_n=200, stable=True, ci_low=None, ci_high=None, label=None):
    return ToolCell(
        tool_id=tool_id,
        label=label or tool_id.replace("_", " ").title(),
        value=value,
        raw_n=raw_n,
        stable=stable,
        ci_low=ci_low,
        ci_high=ci_high,
    )


def _dim(category_id, category_label, baseline_value, by_tool, baseline_raw_n=500):
    baseline = _cell("all", baseline_value, raw_n=baseline_raw_n, label="All tools")
    return DimensionCategory(
        category_id=category_id, category_label=category_label, baseline=baseline, by_tool=by_tool
    )


_TOOL_LABELS = {"table_saw": "Table Saw", "hand_plane": "Hand Plane"}


def test_fingerprint_burden_is_a_bar_beside_the_ratio_matrix():
    """A share and a log2 ratio are different quantities; drawn as adjacent cells of one grid they
    read as one matrix. The burden column is a bar on the sequential ramp now, the ratio matrix a
    heatmap on the diverging one."""
    dims = (
        _dim("burden", "Burden", None, {"table_saw": _cell("table_saw", 0.3)}),
        _dim("escalated_care", "Escalated care", 0.10, {"table_saw": _cell("table_saw", 0.20)}),
    )
    fig = heatmap.build_fingerprint(dims, ("table_saw",), _TOOL_LABELS)
    assert len(fig.data) == 2
    assert fig.data[0].type == "bar"
    assert fig.data[1].type == "heatmap"
    ratio_colors = [step[1] for step in fig.data[1].colorscale]
    assert set(ratio_colors) == set(DIVERGING_SCALE)
    assert SEQUENTIAL_SCALE[3] in list(fig.data[0].marker.color)


def test_fingerprint_colorbar_is_ticked_in_ratios_not_log_units():
    dims = (
        _dim("burden", "Burden", None, {}),
        _dim("escalated_care", "Escalated care", 0.10, {"table_saw": _cell("table_saw", 0.20)}),
    )
    fig = heatmap.build_fingerprint(dims, ("table_saw",), _TOOL_LABELS)
    ticktext = list(fig.data[1].colorbar.ticktext)
    assert ticktext == ["¼×", "½×", "1×", "2×", "4×"]
    assert list(fig.data[1].colorbar.tickvals) == [-2.0, -1.0, 0.0, 1.0, 2.0]


def test_fingerprint_burden_bar_carries_the_share_value():
    dims = (_dim("burden", "Burden", None, {"table_saw": _cell("table_saw", 0.3)}),)
    fig = heatmap.build_fingerprint(dims, ("table_saw",), _TOOL_LABELS)
    assert list(fig.data[0].x) == [0.3]


def test_fingerprint_ratio_column_uses_log2_ratio_capped():
    dims = (
        _dim("burden", "Burden", None, {}),
        _dim("escalated_care", "Escalated care", 0.10, {"table_saw": _cell("table_saw", 0.20)}),
    )
    fig = heatmap.build_fingerprint(dims, ("table_saw",), _TOOL_LABELS)
    assert fig.data[1].z[0][0] == 1.0  # 0.20 / 0.10 = 2x -> log2(2) = 1.0


def test_fingerprint_suppressed_cell_has_no_fill_and_reads_as_not_reportable():
    dims = (
        _dim("burden", "Burden", None, {}),
        _dim("escalated_care", "Escalated care", 0.10, {}),
    )
    fig = heatmap.build_fingerprint(dims, ("table_saw",), _TOOL_LABELS)
    # The burden bar: no fill at all, and its lettered value is not reportable.
    assert list(fig.data[0].marker.color) == ["rgba(0,0,0,0)"]
    assert "N/R" in list(fig.data[0].text)[0]
    assert fig.data[1].z[0][0] is None
    assert fig.data[1].text[0][0] == "N/R"


def test_fingerprint_limited_cell_keeps_its_value_under_a_dashed_outline():
    """Confidence is LINE TREATMENT: the "(limited)" text suffix printed in ~8px in most cells is
    gone; the cell keeps its number and gains a dashed construction outline."""
    dims = (
        _dim("burden", "Burden", None, {}),
        _dim(
            "escalated_care",
            "Escalated care",
            0.10,
            {"table_saw": _cell("table_saw", 0.20, stable=False)},
        ),
    )
    fig = heatmap.build_fingerprint(dims, ("table_saw",), _TOOL_LABELS)
    assert fig.data[1].z[0][0] == 1.0
    # Four characters at most in a cell: a fingerprint cell is ~31px wide at 390px. Hover and the
    # table view keep the unrounded share.
    assert fig.data[1].text[0][0] == "20%"
    assert "limited" not in fig.data[1].text[0][0].lower()
    dashed = [
        s_
        for s_ in fig.layout.shapes
        if s_.line.dash in ("dash", "dot") and s_.xref == "x2" and s_.yref == "y2"
    ]
    assert dashed, "a limited cell must carry the dashed construction outline"


def test_fingerprint_baseline_zero_ratio_cell_is_none():
    dims = (
        _dim("burden", "Burden", None, {}),
        _dim("escalated_care", "Escalated care", 0.0, {"table_saw": _cell("table_saw", 0.20)}),
    )
    fig = heatmap.build_fingerprint(dims, ("table_saw",), _TOOL_LABELS)
    assert fig.data[1].z[0][0] is None


def test_fingerprint_hover_customdata_has_value_ci_baseline_raw_n_stability():
    dims = (
        _dim("burden", "Burden", None, {"table_saw": _cell("table_saw", 0.3, raw_n=500)}),
        _dim(
            "escalated_care",
            "Escalated care",
            0.10,
            {
                "table_saw": _cell(
                    "table_saw", 0.20, raw_n=300, ci_low=0.18, ci_high=0.22, stable=False
                )
            },
        ),
    )
    fig = heatmap.build_fingerprint(dims, ("table_saw",), _TOOL_LABELS)

    burden_cd = fig.data[0].customdata[0]
    assert burden_cd[3] == 500  # raw N
    assert "Stable" in burden_cd[4]

    ratio_cd = fig.data[1].customdata[0][0]
    assert "20.0%" in ratio_cd[0]  # value display
    assert "18.0%" in ratio_cd[1] and "22.0%" in ratio_cd[1]  # CI text
    assert "10.0%" in ratio_cd[2]  # baseline display
    assert ratio_cd[3] == 300  # raw N
    assert ratio_cd[4] == LIMITED_SAMPLE_LABEL


def test_fingerprint_multiple_tools_row_order_matches_tool_ids():
    dims = (
        _dim(
            "burden",
            "Burden",
            None,
            {"table_saw": _cell("table_saw", 0.5), "hand_plane": _cell("hand_plane", 0.1)},
        ),
    )
    fig = heatmap.build_fingerprint(dims, ("hand_plane", "table_saw"), _TOOL_LABELS)
    assert list(fig.data[0].y) == ["Hand Plane", "Table Saw"]
    assert list(fig.data[0].x) == [0.1, 0.5]


# ---------------------------------------------------------------------------
# build_diagnosis_signature
# ---------------------------------------------------------------------------


def _diag_row(tool_id, tool_label, category, label, value, state="stable", raw_n=200):
    return {
        "tool_id": tool_id,
        "tool_label": tool_label,
        "category": category,
        "label": label,
        "value": value,
        "ci_low": value * 0.9 if value is not None else None,
        "ci_high": value * 1.1 if value is not None else None,
        "raw_n": raw_n,
        "state": state,
    }


def test_diagnosis_signature_empty_frame_returns_figure_without_error():
    df = pl.DataFrame(
        schema={
            "tool_id": pl.Utf8,
            "tool_label": pl.Utf8,
            "category": pl.Utf8,
            "label": pl.Utf8,
            "value": pl.Float64,
            "ci_low": pl.Float64,
            "ci_high": pl.Float64,
            "raw_n": pl.Int64,
            "state": pl.Utf8,
        }
    )
    fig = heatmap.build_diagnosis_signature(df)
    assert fig.data


def test_share_matrix_keeps_every_palette_column_when_asked():
    """Empty columns are dropped by default (22 body regions and 13 mechanisms do not fit a
    390px sheet, and a column that is empty for every row earns no width) -- but the full fixed
    column set is one argument away."""
    df = pl.DataFrame([_diag_row("table_saw", "Table Saw", "laceration", "Laceration", 0.4)])
    fig = heatmap.build_share_matrix(
        df, list(DIAGNOSIS_COLORS), zmax_fixed=1, drop_empty_categories=False
    )
    assert len(fig.data[0].x) == len(DIAGNOSIS_COLORS)


def test_diagnosis_signature_drops_columns_with_nothing_reportable():
    df = pl.DataFrame([_diag_row("table_saw", "Table Saw", "laceration", "Laceration", 0.4)])
    fig = heatmap.build_diagnosis_signature(df)
    assert list(fig.data[0].x) == ["Laceration"]
    note = "".join(a.text for a in fig.layout.annotations)
    assert "columns with no reportable cell are not shown" in note


def test_diagnosis_signature_suppressed_cell_is_none_z():
    df = pl.DataFrame(
        [_diag_row("table_saw", "Table Saw", "laceration", "Laceration", None, state="suppressed")]
    )
    fig = heatmap.build_diagnosis_signature(df)
    col = list(DIAGNOSIS_COLORS).index("laceration")
    assert fig.data[0].z[0][col] is None
    assert fig.data[0].customdata[0][col][3] == SUPPRESSED_LABEL


def test_diagnosis_signature_stable_cell_value_and_hover():
    df = pl.DataFrame([_diag_row("table_saw", "Table Saw", "laceration", "Laceration", 0.42)])
    fig = heatmap.build_diagnosis_signature(df)
    col = list(DIAGNOSIS_COLORS).index("laceration")
    assert fig.data[0].z[0][col] == 0.42
    assert fig.data[0].customdata[0][col][3] == "Stable"


def test_share_matrix_missing_category_for_a_tool_is_suppressed():
    df = pl.DataFrame([_diag_row("table_saw", "Table Saw", "laceration", "Laceration", 0.4)])
    fig = heatmap.build_share_matrix(
        df, list(DIAGNOSIS_COLORS), zmax_fixed=1, drop_empty_categories=False
    )
    col = list(DIAGNOSIS_COLORS).index("amputation")
    assert fig.data[0].z[0][col] is None


def test_diagnosis_signature_row_order_matches_frame_tool_order():
    df = pl.DataFrame(
        [
            _diag_row("hand_plane", "Hand Plane", "laceration", "Laceration", 0.1),
            _diag_row("table_saw", "Table Saw", "laceration", "Laceration", 0.5),
        ]
    )
    fig = heatmap.build_diagnosis_signature(df)
    assert list(fig.data[0].y) == ["Hand Plane", "Table Saw"]


# ---------------------------------------------------------------------------
# build_share_matrix / build_mechanism_heatmap (D-12, 04-07 Task 1)
# ---------------------------------------------------------------------------


def _unwrap(label: str) -> str:
    """Inverse of `heatmap._wrap` for assertions: a break after "/" carried no space."""
    return label.replace("/<br>", "/").replace("<br>", " ")


def _mech_row(tool_id, tool_label, category, label, value, state="stable", raw_n=200):
    return {
        "tool_id": tool_id,
        "tool_label": tool_label,
        "category": category,
        "label": label,
        "value": value,
        "ci_low": value * 0.9 if value is not None else None,
        "ci_high": value * 1.1 if value is not None else None,
        "raw_n": raw_n,
        "state": state,
    }


def test_share_matrix_suppressed_and_limited_cells():
    df = pl.DataFrame(
        [
            _diag_row(
                "table_saw", "Table Saw", "laceration", "Laceration", None, state="suppressed"
            ),
            _diag_row("table_saw", "Table Saw", "amputation", "Amputation", 0.2, state="limited"),
        ]
    )
    fig = heatmap.build_share_matrix(df, ["laceration", "amputation"], drop_empty_categories=False)
    assert fig.data[0].z[0][0] is None
    assert fig.data[0].text[0][0] == "N/R"
    assert fig.data[0].customdata[0][0][3] == SUPPRESSED_LABEL
    assert fig.data[0].z[0][1] == 0.2
    # Line treatment, not a text suffix: the cell keeps its number and gains a dashed outline.
    assert fig.data[0].text[0][1] == "20.0%"
    assert any(shape.line.dash in ("dash", "dot") for shape in fig.layout.shapes)
    assert fig.data[0].customdata[0][1][3] == LIMITED_SAMPLE_LABEL


def test_share_matrix_single_scale_zmax():
    df = pl.DataFrame(
        [
            _diag_row("table_saw", "Table Saw", "laceration", "Laceration", 0.7),
            _diag_row("hand_plane", "Hand Plane", "laceration", "Laceration", 0.2),
        ]
    )
    fig = heatmap.build_share_matrix(df, ["laceration"])
    assert fig.data[0].zmax == 0.7
    assert fig.data[0].zmin == 0

    # zmax_fixed overrides the data-derived max (diagnosis signature's own zmax=1 behavior).
    fixed_fig = heatmap.build_share_matrix(df, ["laceration"], zmax_fixed=1)
    assert fixed_fig.data[0].zmax == 1


def test_mechanism_heatmap_unknown_last():
    df = pl.DataFrame(
        [
            _mech_row(
                "table_saw", "Table Saw", "direct_cutting_contact", "Direct Cutting Contact", 0.5
            ),
            _mech_row("table_saw", "Table Saw", "unknown", "Unknown", 0.1),
            _mech_row("table_saw", "Table Saw", "other", "Other", 0.05),
        ]
    )
    fig = heatmap.build_mechanism_heatmap(df)
    assert fig.data[0].x[-1] == "Unknown"
    # Column labels wrap rather than truncate, so compare on the unwrapped text.
    unwrapped = [_unwrap(label) for label in fig.data[0].x]
    assert "Other (classified)" in unwrapped
    assert fig.data[0].zmax == 0.5


def test_mechanism_heatmap_column_labels_are_never_truncated():
    """Every mechanism label must fit the two-line rotated tick. "Direct Cutting Contact" used to
    render as "Direct Cutting Cont…" -- an ellipsis on an axis is a label that failed."""
    df = pl.DataFrame(
        [_mech_row("table_saw", "Table Saw", key, key, 0.1) for key in MECHANISM_COLORS]
    )
    labels = list(heatmap.build_mechanism_heatmap(df).data[0].x)
    assert labels, "expected one column per mechanism"
    assert not any("…" in label for label in labels), labels


def test_mechanism_heatmap_labels_are_not_naive_title_case():
    """`key.replace("_", " ").title()` rendered `flying_debris_fb` as "Flying Debris Fb"."""
    df = pl.DataFrame(
        [_mech_row("table_saw", "Table Saw", key, key, 0.1) for key in MECHANISM_COLORS]
    )
    unwrapped = [_unwrap(label) for label in heatmap.build_mechanism_heatmap(df).data[0].x]
    assert "Flying debris/foreign body" in unwrapped
    assert "Flying Debris Fb" not in unwrapped


# ---------------------------------------------------------------------------
# _wrap
# ---------------------------------------------------------------------------


def test_wrap_leaves_a_short_label_on_one_line():
    assert heatmap._wrap("Amputation", 16) == "Amputation"


def test_wrap_breaks_on_whitespace():
    assert heatmap._wrap("Direct cutting contact", 16) == "Direct cutting<br>contact"


def test_wrap_breaks_after_a_slash_without_a_leading_space():
    """A slash compound is one whitespace token; without the slash rule it could only truncate."""
    assert heatmap._wrap("Flying debris/foreign body", 16) == "Flying debris/<br>foreign body"


def test_wrap_truncates_only_when_the_line_budget_is_exhausted():
    wrapped = heatmap._wrap("one two three four five six seven eight", 10, max_lines=2)
    assert wrapped.count("<br>") == 1
    assert wrapped.endswith("…")


def test_wrap_truncates_a_single_token_longer_than_the_budget():
    assert heatmap._wrap("Supercalifragilistic", 10) == "Supercali…"
