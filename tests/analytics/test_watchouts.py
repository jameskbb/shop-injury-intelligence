"""D-03/D-17 WATCH-OUTS threshold-boundary tests for `wii.analytics.insights` -- synthetic
`InsightFrames`/`CategoryCell` frames only (no database, no wii.metrics calls), mirroring
`tests/analytics/test_insights.py`'s hand-built-frame pattern. `THRESHOLDS` is constructed
directly here (never read from `insights.yaml`) so these tests are independent of the file's
committed values (05-03 Task 2 `<action>`).
"""

from __future__ import annotations

import ast

import pytest

from wii.analytics.insights import (
    CategoryCell,
    DimensionCategory,
    Insight,
    InsightFrames,
    Thresholds,
    ToolCell,
    Watchout,
    build_watchout,
    distinctive_for_tool,
    pick_top_stable,
)
from wii.utils.paths import repo_root

THRESHOLDS = Thresholds(
    min_raw_n=50,
    share_ratio_min=1.5,
    share_pp_min=5.0,
    burden_outcome_ratio_min=1.5,
    trend_pct_min=20.0,
    trend_window_years=3,
    trend_require_nonoverlapping_ci=True,
    max_cards=5,
    min_cards_for_section=3,
    narrative_min_confidence=0.70,
)
MIN_N = THRESHOLDS.min_raw_n


def _tool_cell(tool_id, value, raw_n=MIN_N, stable=True, label=None):
    return ToolCell(
        tool_id=tool_id,
        label=label or tool_id.replace("_", " ").title(),
        value=value,
        raw_n=raw_n,
        stable=stable,
    )


def _cat_cell(category_id, value, raw_n=MIN_N, state="stable", label=None):
    return CategoryCell(
        category_id=category_id,
        label=label or category_id.replace("_", " ").title(),
        value=value,
        raw_n=raw_n,
        state=state,
    )


def _diag_dim(category_id, baseline_value, tool_value, tool_id="table_saw", **tool_kwargs):
    baseline = _tool_cell("all", baseline_value, label="All tools")
    tool = _tool_cell(tool_id, tool_value, **tool_kwargs)
    return DimensionCategory(
        category_id=category_id,
        category_label=f"{category_id} share",
        baseline=baseline,
        by_tool={tool_id: tool},
    )


# ---------------------------------------------------------------------------
# distinctive_for_tool boundaries
# ---------------------------------------------------------------------------


def test_distinctive_for_tool_none_when_ratio_one_step_below_threshold():
    dim = _diag_dim("amputation", 0.20, 0.298)  # ratio 1.49, pp 9.8 (clears pp gate alone)
    frames = InsightFrames(tool_burden={}, total_burden=None, disproportionate=(dim,))
    assert distinctive_for_tool(frames, THRESHOLDS, "table_saw", frozenset({"amputation"})) is None


def test_distinctive_for_tool_none_when_pp_below_threshold_despite_ratio():
    dim = _diag_dim("amputation", 0.02, 0.031)  # ratio 1.55 clears, pp 1.1 fails
    frames = InsightFrames(tool_burden={}, total_burden=None, disproportionate=(dim,))
    assert distinctive_for_tool(frames, THRESHOLDS, "table_saw", frozenset({"amputation"})) is None


def test_distinctive_for_tool_none_when_tool_cell_unstable():
    dim = _diag_dim("amputation", 0.20, 0.30, stable=False)
    frames = InsightFrames(tool_burden={}, total_burden=None, disproportionate=(dim,))
    assert distinctive_for_tool(frames, THRESHOLDS, "table_saw", frozenset({"amputation"})) is None


def test_distinctive_for_tool_none_when_raw_n_below_min():
    dim = _diag_dim("amputation", 0.20, 0.30, raw_n=MIN_N - 1)
    frames = InsightFrames(tool_burden={}, total_burden=None, disproportionate=(dim,))
    assert distinctive_for_tool(frames, THRESHOLDS, "table_saw", frozenset({"amputation"})) is None


def test_distinctive_for_tool_none_when_baseline_value_zero():
    dim = _diag_dim("amputation", 0.0, 0.30)
    frames = InsightFrames(tool_burden={}, total_burden=None, disproportionate=(dim,))
    assert distinctive_for_tool(frames, THRESHOLDS, "table_saw", frozenset({"amputation"})) is None


def test_distinctive_for_tool_picks_largest_effect_size_among_qualifying_diagnosis_dims():
    amputation = _diag_dim("amputation", 0.10, 0.20)  # ratio 2.0
    laceration = _diag_dim("laceration", 0.05, 0.15)  # ratio 3.0
    frames = InsightFrames(
        tool_burden={}, total_burden=None, disproportionate=(amputation, laceration)
    )
    result = distinctive_for_tool(
        frames, THRESHOLDS, "table_saw", frozenset({"amputation", "laceration"})
    )
    assert result is not None
    assert result.effect_size == pytest.approx(3.0)


def test_distinctive_for_tool_never_returns_dim_outside_category_ids_even_with_larger_ratio():
    amputation = _diag_dim("amputation", 0.10, 0.20)  # ratio 2.0, IN category_ids
    eye_rollup = _diag_dim("eye", 0.05, 0.50)  # ratio 10.0, body_rollup, NOT in category_ids
    mechanism_dim = _diag_dim(
        "mechanism:kickback_ejection", 0.05, 0.50
    )  # ratio 10.0, mechanism-prefixed, NOT in category_ids
    frames = InsightFrames(
        tool_burden={},
        total_burden=None,
        disproportionate=(amputation, eye_rollup, mechanism_dim),
    )
    result = distinctive_for_tool(frames, THRESHOLDS, "table_saw", frozenset({"amputation"}))
    assert result is not None
    assert result.effect_size == 2.0


# ---------------------------------------------------------------------------
# build_watchout: stands_out tool_id-equality filter
# ---------------------------------------------------------------------------


def test_build_watchout_stands_out_none_when_only_workshop_wide_candidate_qualifies():
    frames = InsightFrames(
        tool_burden={},
        total_burden=None,
        trend_recent=_tool_cell("all", 12.0, label="Workshop-wide"),
        trend_prior=_tool_cell("all", 10.0, label="Workshop-wide"),
    )
    w = build_watchout(
        tool_id="table_saw",
        tool_label="Table Saw",
        regions=(),
        mechanisms=(),
        frames=frames,
        thresholds=THRESHOLDS,
        basis="annual_average",
        diagnosis_labels={},
    )
    assert w.stands_out is None


def test_build_watchout_stands_out_equals_tools_own_top_ranked_candidate():
    severity_dim = DimensionCategory(
        category_id="escalated_care",
        category_label="escalated-care share",
        baseline=_tool_cell("all", 0.10, label="All tools"),
        by_tool={"table_saw": _tool_cell("table_saw", 0.30)},
    )
    frames = InsightFrames(
        tool_burden={},
        total_burden=None,
        severity=(severity_dim,),
        trend_recent=_tool_cell("all", 12.0, label="Workshop-wide"),
        trend_prior=_tool_cell("all", 10.0, label="Workshop-wide"),
    )
    w = build_watchout(
        tool_id="table_saw",
        tool_label="Table Saw",
        regions=(),
        mechanisms=(),
        frames=frames,
        thresholds=THRESHOLDS,
        basis="annual_average",
        diagnosis_labels={},
    )
    assert w.stands_out is not None
    assert w.stands_out.tool_id == "table_saw"
    assert w.stands_out.kind == "severity"


# ---------------------------------------------------------------------------
# pick_top_stable
# ---------------------------------------------------------------------------


def test_pick_top_stable_skips_limited_and_suppressed_and_excluded_returns_none_when_empty():
    cells = [
        _cat_cell("hand", 0.9, state="limited"),
        _cat_cell("wrist", 0.8, state="suppressed"),
        _cat_cell("unknown", 0.95, state="stable"),
    ]
    assert pick_top_stable(cells, exclude=frozenset({"unknown"})) is None


def test_pick_top_stable_picks_max_value_among_stable():
    cells = [
        _cat_cell("a", 0.3, label="A"),
        _cat_cell("b", 0.7, label="B"),
    ]
    result = pick_top_stable(cells)
    assert result is not None
    assert result.label == "B"


def test_pick_top_stable_breaks_ties_by_label():
    cells = [
        _cat_cell("bravo_id", 0.5, label="Bravo"),
        _cat_cell("alpha_id", 0.5, label="Alpha"),
    ]
    result = pick_top_stable(cells)
    assert result is not None
    assert result.label == "Alpha"


# ---------------------------------------------------------------------------
# Watchout.nothing_clears
# ---------------------------------------------------------------------------

_STANDS_OUT_INSIGHT = Insight(
    kind="severity",
    text="Table Saw shows a higher escalated-care share than the workshop baseline.",
    tool_id="table_saw",
    numbers=(),
    raw_n=100,
    effect_size=2.0,
)


def test_nothing_clears_true_only_when_both_distinctive_and_stands_out_are_none():
    both_none = Watchout("table_saw", "Table Saw", None, None, None, None, None)
    assert both_none.nothing_clears is True

    only_distinctive = Watchout(
        "table_saw", "Table Saw", None, _STANDS_OUT_INSIGHT, "Amputation", None, None
    )
    assert only_distinctive.nothing_clears is False

    only_stands_out = Watchout(
        "table_saw", "Table Saw", None, None, None, None, _STANDS_OUT_INSIGHT
    )
    assert only_stands_out.nothing_clears is False

    both_present = Watchout(
        "table_saw",
        "Table Saw",
        None,
        _STANDS_OUT_INSIGHT,
        "Amputation",
        None,
        _STANDS_OUT_INSIGHT,
    )
    assert both_present.nothing_clears is False


# ---------------------------------------------------------------------------
# D-03 "nothing is hard-coded per tool": no tool_id string literal in the watch-out modules
# ---------------------------------------------------------------------------


def _string_constants(tree: ast.AST) -> list[str]:
    values: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            values.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            for part in node.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    values.append(part.value)
    return values


def test_no_tool_id_string_literal_in_watchout_modules():
    import wii.app.data as data_module

    tool_ids = set(data_module.load_dims()["tools"]["tool_id"].to_list())
    assert tool_ids, "fixture must have >=1 committed tool id"

    paths = [
        repo_root() / "src" / "wii" / "analytics" / "insights.py",
        repo_root() / "src" / "wii" / "app" / "pages" / "my_shop.py",
        repo_root() / "src" / "wii" / "app" / "components" / "watchout_card.py",
    ]
    offenses: list[str] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for value in _string_constants(tree):
            if value in tool_ids:
                offenses.append(f"{path}: {value!r}")
    assert not offenses, "\n".join(offenses)
