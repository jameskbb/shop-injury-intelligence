"""D-07 BODY PART x TOOL MATRIX figure-builder tests (pure -- no Streamlit calls)."""

from __future__ import annotations

import polars as pl

from wii.analytics.insights import scan_guardrail_vocabulary
from wii.app.charts import region_tool_matrix
from wii.app.data import cached_share_by, region_share_by_tool_frame
from wii.metrics import CaseFilter


def _row(
    tool_id: str,
    region_id: str,
    value: float | None,
    state: str,
    ci_low: float | None = None,
    ci_high: float | None = None,
    num_estimate: float | None = None,
    raw_n: int = 100,
) -> dict:
    return {
        "tool_id": tool_id,
        "tool_label": tool_id.replace("_", " ").title(),
        "region_id": region_id,
        "value": value,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "num_estimate": num_estimate,
        "raw_n": raw_n,
        "state": state,
    }


def test_matrix_suppressed_cell_not_reportable_and_label():
    df = pl.DataFrame([_row("table_saw", "finger", None, "suppressed", raw_n=5)])
    fig = region_tool_matrix.build_region_tool_matrix(
        df, ["finger"], {"finger": "Finger"}, ["table_saw"], {"table_saw": "Table Saw"}
    )
    heatmap = fig.data[0]
    assert heatmap.z[0][0] is None
    assert heatmap.text[0][0] == "N/R"
    assert heatmap.customdata[0][0][5] == "Estimate suppressed – insufficient statistical stability"


def test_matrix_limited_cell_flagged():
    df = pl.DataFrame(
        [_row("table_saw", "finger", 0.3, "limited", ci_low=0.1, ci_high=0.5, num_estimate=1500)]
    )
    fig = region_tool_matrix.build_region_tool_matrix(
        df, ["finger"], {"finger": "Finger"}, ["table_saw"], {"table_saw": "Table Saw"}
    )
    heatmap = fig.data[0]
    # Confidence is line treatment now: the cell keeps its number, and the dashed construction
    # outline (not a "(limited)" suffix in 8px type) is what marks it.
    assert heatmap.text[0][0] == "30.0%"
    assert any(shape.line.dash in ("dash", "dot") for shape in fig.layout.shapes)
    assert heatmap.customdata[0][0][5] == "Limited sample – interpret cautiously"


def test_matrix_single_shared_scale():
    df = pl.DataFrame(
        [
            _row("table_saw", "finger", 0.8, "stable", num_estimate=5000),
            _row("router", "hand", 0.2, "stable", num_estimate=2000),
        ]
    )
    fig = region_tool_matrix.build_region_tool_matrix(
        df,
        ["finger", "hand"],
        {"finger": "Finger", "hand": "Hand"},
        ["table_saw", "router"],
        {"table_saw": "Table Saw", "router": "Router"},
    )
    heatmap = fig.data[0]
    assert heatmap.zmin == 0
    # The colour domain is capped (Finger is ~0.8 for every saw and saturated the scale, leaving
    # every other region near-blank); the capped cell's own number is still its real share.
    assert heatmap.zmax == region_tool_matrix.DEFAULT_ZCAP
    assert heatmap.text[0][0] == "80.0%"
    assert heatmap.colorbar.ticktext[-1].startswith(">")


def test_matrix_uncapped_scale_when_zcap_is_none():
    df = pl.DataFrame([_row("table_saw", "finger", 0.8, "stable", num_estimate=5000)])
    fig = region_tool_matrix.build_region_tool_matrix(
        df,
        ["finger"],
        {"finger": "Finger"},
        ["table_saw"],
        {"table_saw": "Table Saw"},
        zcap=None,
    )
    assert fig.data[0].zmax == 0.8


def test_matrix_show_text_false_drops_in_cell_numbers_but_keeps_hover():
    df = pl.DataFrame([_row("table_saw", "finger", 0.3, "stable", num_estimate=5000)])
    fig = region_tool_matrix.build_region_tool_matrix(
        df,
        ["finger"],
        {"finger": "Finger"},
        ["table_saw"],
        {"table_saw": "Table Saw"},
        show_text=False,
    )
    assert fig.data[0].texttemplate is None
    assert "%{customdata[0]}" in fig.data[0].hovertemplate


def test_matrix_column_labels_are_short_and_rotated():
    long_label = "Face (including eyelid, eye area and nose)"
    df = pl.DataFrame([_row("table_saw", "face", 0.3, "stable", num_estimate=5000)])
    fig = region_tool_matrix.build_region_tool_matrix(
        df, ["face"], {"face": long_label}, ["table_saw"], {"table_saw": "Table Saw"}
    )
    assert list(fig.data[0].x) == ["Face"]
    assert all(len(x) <= region_tool_matrix.MAX_LABEL_CHARS for x in fig.data[0].x)
    # Rotated, because truncation alone cannot fit a dozen labels across a 390px plot: the
    # shipped chart ran them together into "HeadFaceEyeMouth Ear Ne...Shoulder,Arm, up".
    assert fig.layout.xaxis.tickangle == -45
    # The full label still reaches hover.
    assert fig.data[0].customdata[0][0][4] == long_label


def test_matrix_column_order_follows_sort_order():
    df = pl.DataFrame(
        [
            _row("table_saw", "hand", 0.1, "stable", num_estimate=2000),
            _row("table_saw", "finger", 0.5, "stable", num_estimate=5000),
        ]
    )
    fig = region_tool_matrix.build_region_tool_matrix(
        df,
        ["finger", "hand"],
        {"finger": "Finger", "hand": "Hand"},
        ["table_saw"],
        {"table_saw": "Table Saw"},
    )
    heatmap = fig.data[0]
    assert list(heatmap.x) == ["Finger", "Hand"]


def test_matrix_within_tool_share_matches_share_by():
    flt = CaseFilter(years=(2021, 2025), age_scope="adults")
    frame = region_share_by_tool_frame(flt)
    cell = frame.filter((pl.col("tool_id") == "table_saw") & (pl.col("region_id") == "finger"))
    share_df = cached_share_by(
        flt, CaseFilter(body_regions=("finger",), body_basis="any"), by="tool"
    )
    expected_row = share_df.filter(pl.col("group_id") == "table_saw")
    if cell.height == 0 or expected_row.height == 0:
        return
    assert abs(cell["value"][0] - expected_row["share"][0]) < 1e-9


def test_matrix_copy_passes_guardrails():
    assert scan_guardrail_vocabulary(region_tool_matrix.SUBTITLE) == []
