"""D-13/INS-01 threshold-boundary tests for `wii.analytics.insights`, built entirely from
hand-constructed synthetic `InsightFrames` (no database, no wii.metrics calls) -- the engine is
pure Python over frames, so its threshold gates are unit-testable in isolation (03-02-PLAN Task
2 `<behavior>`).
"""

from __future__ import annotations

import ast

import pytest

from wii.analytics.insights import (
    DimensionCategory,
    InsightFrames,
    ToolCell,
    compare_tools,
    generate_insights,
    load_thresholds,
    scan_guardrail_vocabulary,
)
from wii.utils.paths import repo_root

THRESHOLDS = load_thresholds()
MIN_N = THRESHOLDS.min_raw_n  # 50


def _cell(tool_id, value, raw_n=MIN_N, stable=True, ci_low=None, ci_high=None, label=None):
    return ToolCell(
        tool_id=tool_id,
        label=label or tool_id.replace("_", " ").title(),
        value=value,
        raw_n=raw_n,
        stable=stable,
        ci_low=ci_low,
        ci_high=ci_high,
    )


def _dimension_frames(category_label, baseline_value, tool_value, **tool_kwargs) -> InsightFrames:
    baseline = _cell("all", baseline_value, label="All tools")
    tool = _cell("table_saw", tool_value, **tool_kwargs)
    dim = DimensionCategory(
        category_id="hand_finger",
        category_label=category_label,
        baseline=baseline,
        by_tool={"table_saw": tool},
    )
    return InsightFrames(tool_burden={}, total_burden=None, severity=(dim,))


# ---------------------------------------------------------------------------
# Share ratio / pp boundaries (severity/disproportionate share both `_share_vs_baseline`)
# ---------------------------------------------------------------------------


def test_share_ratio_exactly_at_threshold_passes():
    # baseline 0.20, tool 0.30 -> ratio exactly 1.5, pp = 10.0 (clears pp gate too)
    frames = _dimension_frames("hand/finger share", 0.20, 0.30)
    out = generate_insights(frames, THRESHOLDS, basis="annual_average")
    assert len(out) == 1
    assert out[0].kind == "severity"


def test_share_ratio_just_below_threshold_fails():
    # ratio 1.49 (0.20 baseline, 0.298 tool), pp = 9.8 (still clears pp gate) -> ratio gate fails
    frames = _dimension_frames("hand/finger share", 0.20, 0.298)
    out = generate_insights(frames, THRESHOLDS, basis="annual_average")
    assert out == []


def test_share_pp_exactly_at_threshold_passes():
    # baseline 0.10, tool 0.15 -> pp exactly 5.0, ratio 1.5 (clears ratio gate too)
    frames = _dimension_frames("hand/finger share", 0.10, 0.15)
    out = generate_insights(frames, THRESHOLDS, basis="annual_average")
    assert len(out) == 1


def test_share_pp_just_below_threshold_fails():
    # pp = 4.99 (0.10 baseline, 0.1499 tool), ratio 1.499 -> both gates fail
    frames = _dimension_frames("hand/finger share", 0.10, 0.1499)
    out = generate_insights(frames, THRESHOLDS, basis="annual_average")
    assert out == []


# ---------------------------------------------------------------------------
# raw_n boundary
# ---------------------------------------------------------------------------


def test_raw_n_49_excluded():
    frames = _dimension_frames("hand/finger share", 0.20, 0.30, raw_n=49)
    out = generate_insights(frames, THRESHOLDS, basis="annual_average")
    assert out == []


def test_raw_n_50_included():
    frames = _dimension_frames("hand/finger share", 0.20, 0.30, raw_n=50)
    out = generate_insights(frames, THRESHOLDS, basis="annual_average")
    assert len(out) == 1


def test_baseline_below_min_raw_n_excludes_insight():
    baseline = _cell("all", 0.20, raw_n=49, label="All tools")
    tool = _cell("table_saw", 0.30, raw_n=1000)
    dim = DimensionCategory("hand_finger", "hand/finger share", baseline, {"table_saw": tool})
    frames = InsightFrames(tool_burden={}, total_burden=None, severity=(dim,))
    out = generate_insights(frames, THRESHOLDS, basis="annual_average")
    assert out == []


# ---------------------------------------------------------------------------
# Instability gates
# ---------------------------------------------------------------------------


def test_unstable_tool_cell_excluded():
    frames = _dimension_frames("hand/finger share", 0.20, 0.30, stable=False)
    out = generate_insights(frames, THRESHOLDS, basis="annual_average")
    assert out == []


def test_unstable_baseline_cell_excludes_insight():
    baseline = _cell("all", 0.20, stable=False, label="All tools")
    tool = _cell("table_saw", 0.30)
    dim = DimensionCategory("hand_finger", "hand/finger share", baseline, {"table_saw": tool})
    frames = InsightFrames(tool_burden={}, total_burden=None, severity=(dim,))
    out = generate_insights(frames, THRESHOLDS, basis="annual_average")
    assert out == []


# ---------------------------------------------------------------------------
# burden_vs_share
# ---------------------------------------------------------------------------


def test_burden_vs_share_ratio_at_threshold_passes():
    total_burden = _cell("all", 100_000.0, label="All tools")
    tool_burden = {"table_saw": _cell("table_saw", 10_000.0)}  # burden share = 0.10
    outcome_cell = _cell("table_saw", 0.15)  # outcome share = 0.15 -> ratio 1.5 exactly
    outcome_dim = DimensionCategory(
        "amputation",
        "amputations",
        baseline=_cell("all", 0.0, raw_n=0),
        by_tool={"table_saw": outcome_cell},
    )
    frames = InsightFrames(
        tool_burden=tool_burden, total_burden=total_burden, burden_outcomes=(outcome_dim,)
    )
    out = generate_insights(frames, THRESHOLDS, basis="annual_average")
    assert len(out) == 1
    assert out[0].kind == "burden_vs_share"


def test_burden_vs_share_ratio_below_threshold_fails():
    total_burden = _cell("all", 100_000.0, label="All tools")
    tool_burden = {"table_saw": _cell("table_saw", 10_000.0)}  # burden share = 0.10
    outcome_cell = _cell("table_saw", 0.149)  # ratio 1.49
    outcome_dim = DimensionCategory(
        "amputation",
        "amputations",
        baseline=_cell("all", 0.0, raw_n=0),
        by_tool={"table_saw": outcome_cell},
    )
    frames = InsightFrames(
        tool_burden=tool_burden, total_burden=total_burden, burden_outcomes=(outcome_dim,)
    )
    out = generate_insights(frames, THRESHOLDS, basis="annual_average")
    assert out == []


# ---------------------------------------------------------------------------
# trend
# ---------------------------------------------------------------------------


def _trend_frames(recent_value, prior_value, recent_ci=(9.0, 11.0), prior_ci=(4.0, 6.0)):
    recent = _cell(
        "all", recent_value, ci_low=recent_ci[0], ci_high=recent_ci[1], label="Workshop-wide"
    )
    prior = _cell(
        "all", prior_value, ci_low=prior_ci[0], ci_high=prior_ci[1], label="Workshop-wide"
    )
    return InsightFrames(tool_burden={}, total_burden=None, trend_recent=recent, trend_prior=prior)


def test_trend_exactly_20_percent_with_nonoverlapping_ci_passes():
    # prior 10.0 -> recent 12.0 is exactly +20%; CIs [9,11] vs [4,6] do not overlap
    frames = _trend_frames(12.0, 10.0)
    out = generate_insights(frames, THRESHOLDS, basis="annual_average")
    assert len(out) == 1
    assert out[0].kind == "trend"


def test_trend_below_20_percent_fails():
    frames = _trend_frames(11.9, 10.0)  # +19%
    out = generate_insights(frames, THRESHOLDS, basis="annual_average")
    assert out == []


def test_trend_overlapping_ci_fails_even_with_large_percent_change():
    # +50% change but CIs overlap -> must fail
    frames = _trend_frames(15.0, 10.0, recent_ci=(9.0, 21.0), prior_ci=(4.0, 16.0))
    out = generate_insights(frames, THRESHOLDS, basis="annual_average")
    assert out == []


# ---------------------------------------------------------------------------
# Unrounded evaluation
# ---------------------------------------------------------------------------


def test_thresholds_evaluated_on_unrounded_values():
    """A share of 0.14949 rounds for DISPLAY to '14.9%', but 0.14949 vs a 0.09949 baseline is a
    pp difference of exactly 5.000 (14.949 - 9.949) -- must pass on the unrounded arithmetic even
    though both values round to the same display string difference boundary."""
    frames = _dimension_frames("hand/finger share", 0.09949, 0.14949)
    out = generate_insights(frames, THRESHOLDS, basis="annual_average")
    assert len(out) == 1
    # And rendered text still displays the 1-decimal rounded form.
    assert "14.9%" in out[0].text
    assert "9.9%" in out[0].text


# ---------------------------------------------------------------------------
# Guardrail vocabulary scan
# ---------------------------------------------------------------------------


def test_no_generated_text_contains_guardrail_vocabulary():
    frames = InsightFrames(
        tool_burden={"table_saw": _cell("table_saw", 100_000.0), "drill": _cell("drill", 5_000.0)},
        total_burden=_cell("all", 200_000.0, label="All tools"),
        severity=(
            DimensionCategory(
                "escalated_care",
                "escalated-care share",
                baseline=_cell("all", 0.10, label="All tools"),
                by_tool={"table_saw": _cell("table_saw", 0.30)},
            ),
        ),
        disproportionate=(
            DimensionCategory(
                "amputation",
                "amputation share",
                baseline=_cell("all", 0.05, label="All tools"),
                by_tool={"table_saw": _cell("table_saw", 0.20)},
            ),
        ),
        burden_outcomes=(
            DimensionCategory(
                "amputation",
                "amputations",
                baseline=_cell("all", 0.0, raw_n=0),
                by_tool={"table_saw": _cell("table_saw", 0.75)},
            ),
        ),
        trend_recent=_cell("all", 12.0, ci_low=9.0, ci_high=11.0, label="Workshop-wide"),
        trend_prior=_cell("all", 10.0, ci_low=4.0, ci_high=6.0, label="Workshop-wide"),
    )
    out = generate_insights(frames, THRESHOLDS, basis="annual_average")
    assert len(out) >= 3
    for insight in out:
        assert scan_guardrail_vocabulary(insight.text) == [], insight.text


def test_scan_guardrail_vocabulary_detects_every_banned_term():
    for term in ("risk", "danger", "dangerous", "safer", "caused by"):
        found = scan_guardrail_vocabulary(f"This tool carries {term} of injury.")
        assert term in found, f"{term!r} not detected in {found!r}"


def test_source_module_contains_no_guardrail_literal_in_templates():
    """Static scan of insights.py's own f-string TEMPLATE text (not module/docstring prose, which
    legitimately names the banned words while documenting the guardrail itself) for the banned
    vocabulary -- guards against a future template edit reintroducing it."""
    src = (repo_root() / "src" / "wii" / "analytics" / "insights.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for part in node.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    assert scan_guardrail_vocabulary(part.value) == [], part.value


# ---------------------------------------------------------------------------
# Zero candidates
# ---------------------------------------------------------------------------


def test_zero_qualifying_candidates_returns_empty_list():
    frames = InsightFrames(tool_burden={}, total_burden=None)
    out = generate_insights(frames, THRESHOLDS, basis="annual_average")
    assert out == []


# ---------------------------------------------------------------------------
# Ranking, cap, deterministic tie-break
# ---------------------------------------------------------------------------


def test_output_capped_at_max_cards_ranked_by_effect_size_descending():
    by_tool = {}
    for i in range(8):
        tool_id = f"tool_{i}"
        # ratio grows with i, all clear both gates
        by_tool[tool_id] = _cell(tool_id, 0.20 + 0.05 * (i + 1), label=f"Tool {i}")
    baseline = _cell("all", 0.10, label="All tools")
    dim = DimensionCategory("hand_finger", "hand/finger share", baseline, by_tool)
    frames = InsightFrames(tool_burden={}, total_burden=None, severity=(dim,))
    out = generate_insights(frames, THRESHOLDS, basis="annual_average")
    assert len(out) == THRESHOLDS.max_cards
    effect_sizes = [c.effect_size for c in out]
    assert effect_sizes == sorted(effect_sizes, reverse=True)


def test_deterministic_tie_break_by_tool_label_then_kind():
    # Two tools with IDENTICAL effect size (ratio) -- tie-break must be by label.
    baseline = _cell("all", 0.10, label="All tools")
    by_tool = {
        "zebra_tool": _cell("zebra_tool", 0.20, label="Zebra Tool"),
        "alpha_tool": _cell("alpha_tool", 0.20, label="Alpha Tool"),
    }
    dim = DimensionCategory("hand_finger", "hand/finger share", baseline, by_tool)
    frames = InsightFrames(tool_burden={}, total_burden=None, severity=(dim,))
    out = generate_insights(frames, THRESHOLDS, basis="annual_average")
    assert [c.tool_id for c in out] == ["alpha_tool", "zebra_tool"]


# ---------------------------------------------------------------------------
# Raw-N wording present in every rendered text
# ---------------------------------------------------------------------------


def test_every_generated_text_contains_raw_n_sample_cases_wording():
    frames = _dimension_frames("hand/finger share", 0.20, 0.30, raw_n=123)
    out = generate_insights(frames, THRESHOLDS, basis="annual_average")
    assert len(out) == 1
    assert "123 sample cases (not a national count)" in out[0].text


# ---------------------------------------------------------------------------
# scope_tool restriction
# ---------------------------------------------------------------------------


def test_scope_tool_restricts_to_matching_tool_and_untagged_insights():
    by_tool = {
        "table_saw": _cell("table_saw", 0.30),
        "drill": _cell("drill", 0.30, label="Drill"),
    }
    baseline = _cell("all", 0.10, label="All tools")
    dim = DimensionCategory("hand_finger", "hand/finger share", baseline, by_tool)
    frames = InsightFrames(
        tool_burden={},
        total_burden=None,
        severity=(dim,),
        trend_recent=_cell("all", 12.0, ci_low=9.0, ci_high=11.0, label="Workshop-wide"),
        trend_prior=_cell("all", 10.0, ci_low=4.0, ci_high=6.0, label="Workshop-wide"),
    )
    out = generate_insights(frames, THRESHOLDS, basis="annual_average", scope_tool="table_saw")
    kinds_tools = {(c.kind, c.tool_id) for c in out}
    assert ("severity", "table_saw") in kinds_tools
    assert ("severity", "drill") not in kinds_tools
    assert ("trend", None) in kinds_tools  # workshop-wide trend still included


# ---------------------------------------------------------------------------
# compare_tools
# ---------------------------------------------------------------------------


def test_compare_tools_pairwise_at_threshold_passes():
    by_tool = {
        "table_saw": _cell("table_saw", 0.30, label="Table Saw"),
        "hand_plane": _cell("hand_plane", 0.20, label="Hand Plane"),
    }
    dim = DimensionCategory(
        "hand_finger", "hand/finger share", baseline=_cell("all", 0.0, raw_n=0), by_tool=by_tool
    )
    frames = InsightFrames(tool_burden={}, total_burden=None, severity=(dim,))
    out = compare_tools(frames, ("table_saw", "hand_plane"), THRESHOLDS, basis="annual_average")
    assert len(out) == 1
    assert out[0].kind == "pairwise_comparison"
    for insight in out:
        assert scan_guardrail_vocabulary(insight.text) == []


def test_compare_tools_below_threshold_returns_empty():
    by_tool = {
        "table_saw": _cell("table_saw", 0.21, label="Table Saw"),
        "hand_plane": _cell("hand_plane", 0.20, label="Hand Plane"),
    }
    dim = DimensionCategory(
        "hand_finger", "hand/finger share", baseline=_cell("all", 0.0, raw_n=0), by_tool=by_tool
    )
    frames = InsightFrames(tool_burden={}, total_burden=None, severity=(dim,))
    out = compare_tools(frames, ("table_saw", "hand_plane"), THRESHOLDS, basis="annual_average")
    assert out == []


def test_compare_tools_unstable_cell_excluded():
    # Values clear ratio (1.5x) and pp (10.0) gates, but the higher cell is unstable -- INS-01
    # prohibition: MUST NOT compare a limited/suppressed cell (03-04 Task 3 boundary test).
    by_tool = {
        "table_saw": _cell("table_saw", 0.30, label="Table Saw", stable=False),
        "hand_plane": _cell("hand_plane", 0.20, label="Hand Plane"),
    }
    dim = DimensionCategory(
        "hand_finger", "hand/finger share", baseline=_cell("all", 0.0, raw_n=0), by_tool=by_tool
    )
    frames = InsightFrames(tool_burden={}, total_burden=None, severity=(dim,))
    out = compare_tools(frames, ("table_saw", "hand_plane"), THRESHOLDS, basis="annual_average")
    assert out == []


def test_compare_tools_raw_n_49_excluded():
    # Values clear ratio/pp gates, but one cell's raw_n (49) is one below min_raw_n (50).
    by_tool = {
        "table_saw": _cell("table_saw", 0.30, label="Table Saw", raw_n=49),
        "hand_plane": _cell("hand_plane", 0.20, label="Hand Plane"),
    }
    dim = DimensionCategory(
        "hand_finger", "hand/finger share", baseline=_cell("all", 0.0, raw_n=0), by_tool=by_tool
    )
    frames = InsightFrames(tool_burden={}, total_burden=None, severity=(dim,))
    out = compare_tools(frames, ("table_saw", "hand_plane"), THRESHOLDS, basis="annual_average")
    assert out == []


# ---------------------------------------------------------------------------
# load_thresholds / config sanity
# ---------------------------------------------------------------------------


def test_load_thresholds_reads_documented_defaults():
    t = load_thresholds()
    assert t.min_raw_n == 50
    assert t.share_ratio_min == pytest.approx(1.5)
    assert t.share_pp_min == pytest.approx(5.0)
    assert t.burden_outcome_ratio_min == pytest.approx(1.5)
    assert t.trend_pct_min == pytest.approx(20.0)
    assert t.max_cards == 5
    assert t.min_cards_for_section == 3


def test_no_streamlit_import_in_analytics_package():
    analytics_dir = repo_root() / "src" / "wii" / "analytics"
    for path in analytics_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "import streamlit" not in text, f"{path} imports streamlit"


# ---------------------------------------------------------------------------
# Mechanism-vs-workshop-baseline disproportionate dimension (03-05 gap closure, INS-01).
# Synthetic frames only -- the engine's existing dimension-agnostic `_share_vs_baseline`/
# `compare_tools` gates apply unchanged to a "mechanism:" category_id, so these tests pin the
# INS-01 boundary/precision/wording edges resolved explicit in the 03-05 plan.
# ---------------------------------------------------------------------------

_MECHANISM_PHRASE = "narrative-derived share of cases associated with flying debris/foreign body"


def _mechanism_frames(
    baseline_value,
    tool_value,
    *,
    tool_raw_n=MIN_N,
    baseline_raw_n=MIN_N,
    tool_stable=True,
    baseline_stable=True,
) -> InsightFrames:
    baseline = _cell(
        "all",
        baseline_value,
        raw_n=baseline_raw_n,
        stable=baseline_stable,
        label="All tools (workshop-wide)",
    )
    tool = _cell("grinder", tool_value, raw_n=tool_raw_n, stable=tool_stable, label="Grinder")
    dim = DimensionCategory(
        category_id="mechanism:flying_debris_fb",
        category_label=_MECHANISM_PHRASE,
        baseline=baseline,
        by_tool={"grinder": tool},
    )
    return InsightFrames(tool_burden={}, total_burden=None, disproportionate=(dim,))


def test_mechanism_ratio_exactly_at_threshold_passes():
    frames = _mechanism_frames(0.20, 0.30)
    out = generate_insights(frames, THRESHOLDS, basis="annual_average")
    assert len(out) == 1
    assert out[0].kind == "disproportionate"
    assert "narrative-derived share of cases associated with" in out[0].text
    assert "among ER cases" in out[0].text


def test_mechanism_ratio_just_below_threshold_fails():
    frames = _mechanism_frames(0.20, 0.2998)  # ratio 1.499, pp 9.98
    out = generate_insights(frames, THRESHOLDS, basis="annual_average")
    assert out == []


def test_mechanism_pp_exactly_at_threshold_passes():
    frames = _mechanism_frames(0.05, 0.10)  # ratio 2.0, pp 5.0
    out = generate_insights(frames, THRESHOLDS, basis="annual_average")
    assert len(out) == 1


def test_mechanism_pp_just_below_threshold_fails():
    frames = _mechanism_frames(0.05, 0.0999)  # pp 4.99
    out = generate_insights(frames, THRESHOLDS, basis="annual_average")
    assert out == []


def test_mechanism_tool_raw_n_49_excluded():
    frames = _mechanism_frames(0.20, 0.30, tool_raw_n=49)
    out = generate_insights(frames, THRESHOLDS, basis="annual_average")
    assert out == []


def test_mechanism_tool_raw_n_50_included():
    frames = _mechanism_frames(0.20, 0.30, tool_raw_n=50)
    out = generate_insights(frames, THRESHOLDS, basis="annual_average")
    assert len(out) == 1


def test_mechanism_baseline_raw_n_49_excluded():
    frames = _mechanism_frames(0.20, 0.30, baseline_raw_n=49)
    out = generate_insights(frames, THRESHOLDS, basis="annual_average")
    assert out == []


def test_mechanism_unstable_baseline_excluded():
    frames = _mechanism_frames(0.20, 0.30, baseline_stable=False)
    out = generate_insights(frames, THRESHOLDS, basis="annual_average")
    assert out == []


def test_mechanism_unstable_tool_excluded():
    frames = _mechanism_frames(0.20, 0.30, tool_stable=False)
    out = generate_insights(frames, THRESHOLDS, basis="annual_average")
    assert out == []


def test_mechanism_unrounded_value_evaluated():
    """0.14995 displays as '15.0%' and 0.10 as '10.0%', but the pp gap on the UNROUNDED values is
    4.995 (< 5.0) -- must fail even though the rendered display would read '15.0% vs 10.0%'."""
    frames = _mechanism_frames(0.10, 0.14995)
    out = generate_insights(frames, THRESHOLDS, basis="annual_average")
    assert out == []


def test_mechanism_lower_than_baseline_never_reported():
    frames = _mechanism_frames(0.40, 0.10)
    out = generate_insights(frames, THRESHOLDS, basis="annual_average")
    assert out == []


def test_mechanism_text_guardrail_clean():
    frames = _mechanism_frames(0.20, 0.30)
    out = generate_insights(frames, THRESHOLDS, basis="annual_average")
    assert len(out) == 1
    assert scan_guardrail_vocabulary(out[0].text) == []


def test_compare_tools_mechanism_pairwise():
    by_tool = {
        "table_saw": _cell("table_saw", 0.45, label="Table Saw"),
        "hand_plane": _cell("hand_plane", 0.15, label="Hand Plane"),
    }
    dim = DimensionCategory(
        category_id="mechanism:flying_debris_fb",
        category_label=_MECHANISM_PHRASE,
        baseline=_cell("all", 0.0, raw_n=0),
        by_tool=by_tool,
    )
    frames = InsightFrames(tool_burden={}, total_burden=None, disproportionate=(dim,))
    out = compare_tools(frames, ("table_saw", "hand_plane"), THRESHOLDS, basis="annual_average")
    assert len(out) == 1
    assert out[0].kind == "pairwise_comparison"
    assert "narrative-derived share of cases associated with" in out[0].text
