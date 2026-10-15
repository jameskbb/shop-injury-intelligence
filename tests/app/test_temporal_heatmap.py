"""TOOL BURDEN BY YEAR (tool x year heatmap) tests.

Two tiers, matching the repo's existing split: pure `go.Figure` builder tests against hand-built
Polars frames (no database, mirroring `test_charts_overview.py`/`test_heatmap.py`), then AppTest
render tests of the Overview host page at default AND non-default filters (mirroring
`test_body_map_page.py`), plus real-data checks of the `wii.app.data.tool_year_matrix` frame
contract.
"""

from __future__ import annotations

import math

import polars as pl

from wii.app import theme
from wii.app.charts import temporal_heatmap
from wii.app.charts.temporal_heatmap import (
    SCALE_FLOOR,
    SCHEMA_CHANGE_YEAR,
    build_temporal_heatmap,
    limited_cell_count,
    log10_floored,
    suppressed_cell_count,
)
from wii.app.components.stability_badge import LIMITED_SAMPLE_LABEL, SUPPRESSED_LABEL
from wii.app.data import TEMPORAL_HEATMAP_TOOL_LIMIT, available_years, tool_year_matrix
from wii.app.theme import MUTED, SEQUENTIAL_SCALE
from wii.metrics import CaseFilter

from .conftest import APP_TIMEOUT

_OVERVIEW_PATH = "src/wii/app/pages/overview.py"


def _cell(tool_id, tool_label, year, value, state="stable", raw_n=200):
    return {
        "tool_id": tool_id,
        "tool_label": tool_label,
        "year": year,
        "value": value,
        "ci_low": value * 0.9 if value is not None else None,
        "ci_high": value * 1.1 if value is not None else None,
        "raw_n": raw_n,
        "state": state,
    }


def _matrix_df():
    """2 tools x 4 years (2017-2020, straddling the 2019 boundary) with one stable, one limited
    and one suppressed cell so every rendering path is covered by one frame."""
    rows = []
    for year, value in ((2017, 20_000.0), (2018, 18_000.0), (2019, 16_000.0), (2020, 15_000.0)):
        rows.append(_cell("table_saw", "Table Saw", year, value))
    rows.append(_cell("router", "Router", 2017, 3_000.0))
    rows.append(_cell("router", "Router", 2018, 2_500.0, state="limited", raw_n=25))
    rows.append(_cell("router", "Router", 2019, None, state="suppressed", raw_n=7))
    rows.append(_cell("router", "Router", 2020, 2_200.0))
    return pl.DataFrame(rows)


def _heatmap_trace(fig):
    return next(t for t in fig.data if t.type == "heatmap")


def _overlay(fig, name):
    return next((t for t in fig.data if t.type == "scatter" and t.name == name), None)


# ---------------------------------------------------------------------------
# log10_floored
# ---------------------------------------------------------------------------


def test_log10_floored_maps_real_estimate():
    assert log10_floored(10_000.0) == 4.0


def test_log10_floored_clamps_below_the_cpsc_reportability_floor():
    # Nothing under 1,200 is ever displayed, so the scale floors there instead of running off.
    assert log10_floored(500.0) == math.log10(SCALE_FLOOR)


def test_log10_floored_none_and_zero_never_produce_negative_infinity():
    assert log10_floored(None) is None
    assert log10_floored(0.0) is None


# ---------------------------------------------------------------------------
# matrix shape
# ---------------------------------------------------------------------------


def test_matrix_shape_is_tools_by_years():
    fig = build_temporal_heatmap(_matrix_df())
    trace = _heatmap_trace(fig)
    assert list(trace.x) == [2017, 2018, 2019, 2020]
    assert list(trace.y) == ["Table Saw", "Router"]
    assert len(trace.z) == 2
    assert all(len(row) == 4 for row in trace.z)


def test_matrix_row_order_follows_frame_tool_order():
    df = _matrix_df().reverse()
    trace = _heatmap_trace(build_temporal_heatmap(df))
    assert list(trace.y) == ["Router", "Table Saw"]


def test_matrix_y_axis_is_reversed_so_highest_burden_sits_on_top():
    fig = build_temporal_heatmap(_matrix_df())
    assert fig.layout.yaxis.autorange == "reversed"


def test_empty_frame_returns_a_figure_without_error():
    fig = build_temporal_heatmap(pl.DataFrame())
    assert fig.data == ()


def test_time_axis_is_numeric_years_not_categories():
    """A numeric x axis is what lets the 2019 boundary be drawn at a cell edge (x=2018.5)."""
    fig = build_temporal_heatmap(_matrix_df())
    assert all(isinstance(x, int) for x in _heatmap_trace(fig).x)
    assert fig.layout.xaxis.dtick == 1


# ---------------------------------------------------------------------------
# suppressed / limited cell treatment
# ---------------------------------------------------------------------------


def test_suppressed_cell_has_no_fill_from_the_color_scale():
    trace = _heatmap_trace(build_temporal_heatmap(_matrix_df()))
    # Router (row 1) 2019 (col 2) is suppressed -> z is None, so no sequential-scale color is
    # painted at all. Every other cell in that row carries a real z.
    assert trace.z[1][2] is None
    assert all(trace.z[1][i] is not None for i in (0, 1, 3))


def test_suppressed_cell_carries_an_explicit_glyph_not_just_an_absent_fill():
    """SEQUENTIAL_SCALE's bottom stop sits a hair off bare vellum, so "unfilled" alone would read
    as a measured low value. The overlay glyph is the thing that makes the two distinguishable."""
    assert SEQUENTIAL_SCALE[0] == theme.SEQUENTIAL_SCALE[0]
    overlay = _overlay(build_temporal_heatmap(_matrix_df()), SUPPRESSED_LABEL)
    assert overlay is not None
    assert overlay.marker.symbol == "x-thin"
    assert overlay.marker.line.color == MUTED
    assert list(overlay.x) == [2019]
    assert list(overlay.y) == ["Router"]


def test_limited_cell_keeps_its_value_under_a_hollow_ring():
    fig = build_temporal_heatmap(_matrix_df())
    trace = _heatmap_trace(fig)
    assert trace.z[1][1] == log10_floored(2_500.0)
    overlay = _overlay(fig, LIMITED_SAMPLE_LABEL)
    assert overlay is not None
    assert overlay.marker.symbol == "circle-open"
    # Amber is retired: the ring is a construction line, not a caution colour.
    assert overlay.marker.line.color == theme.CONSTRUCTION
    assert overlay.marker.line.color != theme.REDLINE
    assert list(overlay.x) == [2018]


def test_suppressed_and_limited_overlays_are_legended():
    fig = build_temporal_heatmap(_matrix_df())
    assert fig.layout.showlegend is True
    names = {t.name for t in fig.data if t.type == "scatter"}
    assert names == {SUPPRESSED_LABEL, LIMITED_SAMPLE_LABEL}


def test_all_stable_matrix_renders_no_overlay_traces():
    rows = [_cell("table_saw", "Table Saw", y, 20_000.0) for y in (2017, 2018)]
    fig = build_temporal_heatmap(pl.DataFrame(rows))
    assert [t.type for t in fig.data] == ["heatmap"]


def test_missing_cell_is_rendered_suppressed_never_dropped():
    """A tool x year pair absent from the frame must still occupy its grid slot."""
    rows = [_cell("table_saw", "Table Saw", 2017, 20_000.0), _cell("router", "Router", 2018, 900.0)]
    rows[1]["value"] = None
    rows[1]["state"] = "suppressed"
    fig = build_temporal_heatmap(pl.DataFrame(rows))
    trace = _heatmap_trace(fig)
    assert list(trace.x) == [2017, 2018]
    assert trace.z[0][1] is None  # table_saw has no 2018 row at all
    assert trace.z[1][0] is None  # router has no 2017 row at all
    overlay = _overlay(fig, SUPPRESSED_LABEL)
    assert sorted(zip(overlay.y, overlay.x, strict=True)) == [
        ("Router", 2017),
        ("Router", 2018),
        ("Table Saw", 2018),
    ]


def test_cell_count_helpers():
    df = _matrix_df()
    assert suppressed_cell_count(df) == 1
    assert limited_cell_count(df) == 1
    assert suppressed_cell_count(pl.DataFrame()) == 0
    assert limited_cell_count(pl.DataFrame()) == 0


# ---------------------------------------------------------------------------
# hover content
# ---------------------------------------------------------------------------


def test_hover_carries_estimate_ci_raw_n_caveat_and_stability_for_a_stable_cell():
    trace = _heatmap_trace(build_temporal_heatmap(_matrix_df()))
    text = trace.hovertext[0][0]
    assert "Table Saw, 2017" in text
    assert "Estimated U.S. ER-treated injuries: 20,000" in text
    assert "95% CI: 18,000–22,000" in text
    assert "200 sample cases (not a national count)" in text
    assert "Stable" in text


def test_hover_on_a_limited_cell_names_the_limited_sample_state():
    trace = _heatmap_trace(build_temporal_heatmap(_matrix_df()))
    text = trace.hovertext[1][1]
    assert "Estimated U.S. ER-treated injuries: 2,500" in text
    assert LIMITED_SAMPLE_LABEL in text


def test_hover_on_a_suppressed_cell_never_shows_a_number():
    trace = _heatmap_trace(build_temporal_heatmap(_matrix_df()))
    text = trace.hovertext[1][2]
    assert "Estimated U.S. ER-treated injuries: —" in text
    assert SUPPRESSED_LABEL in text
    assert "7 sample cases (not a national count)" in text
    assert "95% CI" not in text


def test_suppressed_overlay_repeats_the_full_hover_text():
    """The glyph sits on top of the cell, so it must carry the hover itself -- otherwise a
    suppressed cell would silently have no tooltip at all."""
    fig = build_temporal_heatmap(_matrix_df())
    overlay = _overlay(fig, SUPPRESSED_LABEL)
    assert overlay.hoverinfo == "text"
    assert overlay.hovertext[0] == _heatmap_trace(fig).hovertext[1][2]


def test_raw_n_caveat_matches_the_wording_used_everywhere_else():
    trace = _heatmap_trace(build_temporal_heatmap(_matrix_df()))
    assert all("(not a national count)" in text for row in trace.hovertext for text in row)


# ---------------------------------------------------------------------------
# color axis
# ---------------------------------------------------------------------------


def test_color_axis_starts_at_the_cpsc_reportability_floor():
    trace = _heatmap_trace(build_temporal_heatmap(_matrix_df()))
    assert trace.zmin == math.log10(SCALE_FLOOR)
    assert trace.zmax == log10_floored(20_000.0)


def test_colorbar_is_ticked_with_real_estimates_not_log_units():
    trace = _heatmap_trace(build_temporal_heatmap(_matrix_df()))
    assert list(trace.colorbar.ticktext) == ["1,200", "2,000", "3,000", "5,000", "10,000", "20,000"]
    assert [round(v, 6) for v in trace.colorbar.tickvals] == [
        round(math.log10(v), 6) for v in (1_200, 2_000, 3_000, 5_000, 10_000, 20_000)
    ]


def test_color_axis_never_degenerates_to_zero_width():
    rows = [_cell("router", "Router", 2017, 900.0)]  # below the floor after clamping
    trace = _heatmap_trace(build_temporal_heatmap(pl.DataFrame(rows)))
    assert trace.zmax > trace.zmin
    assert len(trace.colorbar.ticktext) >= 2


def test_uses_the_projects_single_sequential_scale():
    trace = _heatmap_trace(build_temporal_heatmap(_matrix_df()))
    assert [stop[1] for stop in trace.colorscale] == SEQUENTIAL_SCALE


# ---------------------------------------------------------------------------
# 2019 NEISS coding boundary
# ---------------------------------------------------------------------------


def test_2019_boundary_is_marked_on_the_cell_edge_when_the_span_crosses_it():
    fig = build_temporal_heatmap(_matrix_df())
    vlines = [s for s in fig.layout.shapes if s.line.dash == "dot"]
    assert len(vlines) == 1
    assert vlines[0].x0 == SCHEMA_CHANGE_YEAR - 0.5
    assert any("2019 coding change" in (a.text or "") for a in fig.layout.annotations)


def test_2019_boundary_is_absent_when_the_span_does_not_cross_it():
    rows = [_cell("table_saw", "Table Saw", y, 20_000.0) for y in (2020, 2021, 2022)]
    fig = build_temporal_heatmap(pl.DataFrame(rows))
    assert not [s for s in fig.layout.shapes if s.line.dash == "dot"]


def test_schema_change_caption_is_the_same_text_the_trend_chart_uses():
    from wii.app.charts import trend

    assert temporal_heatmap.SCHEMA_CHANGE_CAPTION == trend.SCHEMA_CHANGE_CAPTION
    assert temporal_heatmap.SCHEMA_CHANGE_YEAR == trend.SCHEMA_CHANGE_YEAR


# ---------------------------------------------------------------------------
# title / subtitle convention
# ---------------------------------------------------------------------------


def test_title_and_subtitle_state_the_question_the_chart_answers():
    assert temporal_heatmap.TITLE
    assert "grew" in temporal_heatmap.SUBTITLE
    assert "tool per treatment year" in temporal_heatmap.SUBTITLE
    # The subtitle must tell the reader how to read a blank cell, not only how to read a color.
    assert "no reportable estimate" in temporal_heatmap.SUBTITLE
    assert "year range does not scope this chart" in temporal_heatmap.YEAR_RANGE_NOTE


# ---------------------------------------------------------------------------
# wii.app.data.tool_year_matrix (real committed data)
# ---------------------------------------------------------------------------


def _default_flt() -> CaseFilter:
    years = available_years()
    return CaseFilter(years=(years[-1] - 4, years[-1]), age_scope="adults")


def test_tool_year_matrix_is_a_complete_rectangle_over_available_years():
    df, omitted = tool_year_matrix(_default_flt())
    years = available_years()
    tools = df["tool_id"].unique().to_list()
    assert len(tools) <= TEMPORAL_HEATMAP_TOOL_LIMIT
    assert df.height == len(tools) * len(years)
    assert sorted(df["year"].unique().to_list()) == list(years)
    assert omitted >= 0


def test_tool_year_matrix_ignores_the_sidebar_year_range():
    """Documented Flagged Assumption, shared with `trend_frame`: the record is never cropped."""
    years = available_years()
    narrow, _ = tool_year_matrix(CaseFilter(years=(years[-1], years[-1]), age_scope="adults"))
    wide, _ = tool_year_matrix(CaseFilter(years=(years[0], years[-1]), age_scope="adults"))
    assert sorted(narrow["year"].unique().to_list()) == list(years)
    assert narrow.equals(wide)


def test_tool_year_matrix_responds_to_a_non_year_filter():
    baseline, _ = tool_year_matrix(_default_flt())
    filtered, _ = tool_year_matrix(
        CaseFilter(years=_default_flt().years, age_scope="adults", families=("saws",))
    )
    assert filtered.height > 0
    assert not baseline.equals(filtered)
    # A family filter can only ever narrow the population, never grow a cell.
    base_by_cell = {(r["tool_id"], r["year"]): r["value"] for r in baseline.iter_rows(named=True)}
    compared = 0
    for row in filtered.iter_rows(named=True):
        base = base_by_cell.get((row["tool_id"], row["year"]))
        if base is not None and row["value"] is not None:
            assert row["value"] <= base + 1e-6
            compared += 1
    assert compared > 0


def test_tool_year_matrix_responds_to_the_age_scope_filter():
    adults, _ = tool_year_matrix(_default_flt())
    all_ages, _ = tool_year_matrix(CaseFilter(years=_default_flt().years, age_scope="all"))
    assert not adults.equals(all_ages)


def test_tool_year_matrix_states_are_only_the_three_known_ones():
    df, _ = tool_year_matrix(_default_flt())
    assert set(df["state"].unique().to_list()) <= {"stable", "limited", "suppressed"}


def test_tool_year_matrix_suppressed_rows_carry_no_value_or_interval():
    df, _ = tool_year_matrix(_default_flt())
    suppressed = df.filter(pl.col("state") == "suppressed")
    if suppressed.height:
        assert suppressed["value"].null_count() == suppressed.height
        assert suppressed["ci_low"].null_count() == suppressed.height
        assert suppressed["ci_high"].null_count() == suppressed.height


def test_tool_year_matrix_displayed_cells_clear_the_cpsc_floor():
    """Every rendered value is a single-year period total, so the 1,200 floor genuinely applies
    to it -- which is what lets the color scale start there."""
    df, _ = tool_year_matrix(_default_flt())
    shown = df.filter(pl.col("value").is_not_null())
    assert shown.height > 0
    assert shown["value"].min() >= SCALE_FLOOR


def test_tool_year_matrix_renders_end_to_end_on_real_data():
    df, _ = tool_year_matrix(_default_flt())
    fig = build_temporal_heatmap(df)
    trace = _heatmap_trace(fig)
    assert list(trace.x) == list(available_years())
    assert len(trace.z) == len(df["tool_id"].unique())


# ---------------------------------------------------------------------------
# host page render (AppTest)
# ---------------------------------------------------------------------------


def _run_overview(at):
    at.run(timeout=APP_TIMEOUT)
    at.switch_page(_OVERVIEW_PATH)
    at.run(timeout=APP_TIMEOUT)
    return at


def test_overview_renders_the_temporal_heatmap_at_default_filters(app_test):
    at = _run_overview(app_test)
    assert not at.exception
    assert any(temporal_heatmap.TITLE in h.value for h in at.header)
    captions = [c.value for c in at.caption]
    assert any(temporal_heatmap.SUBTITLE in c for c in captions)
    assert any(temporal_heatmap.YEAR_RANGE_NOTE in c for c in captions)
    assert any(temporal_heatmap.SCHEMA_CHANGE_CAPTION in c for c in captions)


def test_overview_renders_the_temporal_heatmap_at_non_default_filters(app_test):
    at = app_test
    at.run(timeout=APP_TIMEOUT)
    at.switch_page(_OVERVIEW_PATH)
    at.session_state["age_scope"] = "all"
    at.session_state["basis"] = "period_total"
    at.session_state["f_families"] = ("saws",)
    years = available_years()
    at.session_state["year_range"] = (years[0], years[0] + 1)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception
    assert any(temporal_heatmap.TITLE in h.value for h in at.header)


def test_overview_temporal_heatmap_survives_a_single_tool_filter(app_test):
    """A tool filter narrows the CASE population, not the row set: `by="tool"` is a
    multi-membership grouping, so cases involving a table saw still contribute rows for every
    other in-scope tool recorded on the same case. That is the same semantics the landscape and
    ranked bars on this page already have -- the heatmap must not invent a different one, and it
    must still come back as a complete rectangle."""
    at = app_test
    at.run(timeout=APP_TIMEOUT)
    at.switch_page(_OVERVIEW_PATH)
    at.session_state["global_tool_filter"] = ("table_saw",)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception
    df, _ = tool_year_matrix(CaseFilter(years=_default_flt().years, tools=("table_saw",)))
    tools = df["tool_id"].unique().to_list()
    assert "table_saw" in tools
    assert df.height == len(tools) * len(available_years())
    unfiltered, _ = tool_year_matrix(_default_flt())
    assert not df.equals(unfiltered)
