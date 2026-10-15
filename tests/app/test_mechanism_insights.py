"""INS-01 gap closure (03-05, closing 03-VERIFICATION.md Truth 6): the mechanism-vs-workshop-
baseline disproportionate dimension. Builder unit tests (Task 1) plus Tool Profiles / Comparison
AppTest renders of mechanism insight cards on real committed data (Tasks 1-2). Synthetic
boundary/precision/budget coverage lives in tests/analytics/test_insights.py and
tests/app/test_data_layer.py (Task 2) -- this file only asserts against the real dataset.
"""

from __future__ import annotations

import dataclasses
import html
import itertools

import wii.app.data as data_module
from wii.analytics.insights import (
    compare_tools,
    generate_insights,
    load_thresholds,
    scan_guardrail_vocabulary,
)
from wii.app import filters as filters_module
from wii.app import theme
from wii.metrics import CaseFilter

from .conftest import APP_TIMEOUT

_TOOL_PROFILES_PATH = "src/wii/app/pages/tool_profiles.py"
_COMPARISON_PATH = "src/wii/app/pages/comparison.py"

_MECHANISM_PHRASE = "narrative-derived share of cases associated with"


def _default_workshop_filter() -> CaseFilter:
    years = filters_module._default_year_range()
    return CaseFilter(years=years, age_scope="adults")


def test_mechanism_dims_exclude_unknown_and_other():
    flt = _default_workshop_filter()
    dims = data_module._mechanism_disproportionate_dims(flt)
    expected_ids = tuple(
        "mechanism:" + key for key in theme.MECHANISM_COLORS if key not in {"unknown", "other"}
    )
    assert tuple(d.category_id for d in dims) == expected_ids
    assert "unknown" not in data_module._MECHANISM_INSIGHT_IDS
    assert "other" not in data_module._MECHANISM_INSIGHT_IDS


def test_every_mechanism_insight_id_has_readable_label():
    for key in data_module._MECHANISM_INSIGHT_IDS:
        label = data_module._MECHANISM_LABELS.get(key)
        assert label is not None, f"missing readable label for mechanism key {key!r}"
        assert "_" not in label


def test_mechanism_dim_label_is_narrative_derived():
    flt = _default_workshop_filter()
    dims = data_module._mechanism_disproportionate_dims(flt)
    assert dims, "expected at least one mechanism dimension on the default workshop filter"
    for dim in dims:
        assert dim.category_label.startswith(f"{_MECHANISM_PHRASE} ")
        assert scan_guardrail_vocabulary(dim.category_label) == []


def test_mechanism_dims_values_are_unrounded_share():
    flt = _default_workshop_filter()
    dims = data_module._mechanism_disproportionate_dims(flt)
    assert dims
    for dim in dims:
        key = dim.category_id.removeprefix(data_module._MECHANISM_CATEGORY_PREFIX)
        numerator = CaseFilter(mechanisms=(key,))
        by_tool_rows = {
            row["group_id"]: row
            for row in data_module.cached_share_by(flt, numerator, by="tool").iter_rows(named=True)
        }
        for group_id, cell in dim.by_tool.items():
            if cell.value is None:
                # A share whose numerator falls under the CPSC hard floors resolves as SUPPRESSED
                # and carries no number at all -- it must never surface the raw share.
                continue
            assert cell.value == by_tool_rows[group_id]["share"]
        baseline_df = data_module.cached_share_by(flt, numerator, by="all")
        assert baseline_df.height == 1
        if dim.baseline.value is not None:
            assert dim.baseline.value == baseline_df.row(0, named=True)["share"]


def test_insight_frames_includes_mechanism_dimension():
    flt = _default_workshop_filter()
    frames = data_module.insight_frames(flt, "annual_average")
    mechanism_dims = data_module._mechanism_disproportionate_dims(flt)
    mechanism_ids = {d.category_id for d in mechanism_dims}
    frame_ids = {d.category_id for d in frames.disproportionate}
    assert mechanism_ids <= frame_ids

    _, existing_disproportionate = data_module._severity_and_disproportionate_dims(flt)
    existing_ids = [d.category_id for d in existing_disproportionate]
    frame_id_prefix = [d.category_id for d in frames.disproportionate][: len(existing_ids)]
    assert frame_id_prefix == existing_ids


def _find_qualifying_tool_insight(frames, thresholds, tools):
    for tool in tools:
        insights = generate_insights(frames, thresholds, "annual_average", scope_tool=tool)
        for insight in insights:
            if _MECHANISM_PHRASE in insight.text:
                return tool, insight
    return None, None


def test_tool_profiles_renders_mechanism_insight_card(app_test):
    flt = _default_workshop_filter()
    frames = data_module.insight_frames(flt, "annual_average")
    thresholds = load_thresholds()
    tools = sorted(data_module.load_dims()["tools"]["tool_id"].to_list())

    qualifying_tool, qualifying_insight = _find_qualifying_tool_insight(frames, thresholds, tools)
    assert qualifying_tool is not None, "no tool has a qualifying mechanism insight"

    at = app_test
    at.run(timeout=APP_TIMEOUT)
    at.switch_page(_TOOL_PROFILES_PATH)
    at.session_state["profile_selected_tool"] = qualifying_tool
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    md_values = [m.value for m in at.markdown]
    escaped_text = html.escape(qualifying_insight.text)
    matches = [v for v in md_values if escaped_text in v and "wii-insight-card" in v]
    assert matches, "mechanism insight card not found in rendered markdown"


def _find_qualifying_pair_insight(frames, thresholds, tools):
    for pair in itertools.combinations(tools, 2):
        insights = compare_tools(frames, pair, thresholds, "annual_average")
        for insight in insights:
            if _MECHANISM_PHRASE in insight.text:
                return pair, insight
    return None, None


def test_comparison_renders_mechanism_pairwise_statement(app_test):
    flt = _default_workshop_filter()
    frames = data_module.insight_frames(flt, "annual_average")
    thresholds = load_thresholds()
    tools = sorted(data_module.load_dims()["tools"]["tool_id"].to_list())

    qualifying_pair, qualifying_insight = _find_qualifying_pair_insight(frames, thresholds, tools)
    assert qualifying_pair is not None, "no tool pair has a qualifying mechanism pairwise insight"

    at = app_test
    at.run(timeout=APP_TIMEOUT)
    at.switch_page(_COMPARISON_PATH)
    at.session_state["comparison_selected_tools"] = list(qualifying_pair)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    md_values = [m.value for m in at.markdown]
    escaped_text = html.escape(qualifying_insight.text)
    matches = [v for v in md_values if escaped_text in v and "wii-insight-card" in v]
    assert matches, "mechanism pairwise insight card not found in rendered markdown"


def test_overview_candidate_pool_includes_mechanism_insights():
    flt = _default_workshop_filter()
    frames = data_module.insight_frames(flt, "annual_average")
    thresholds = load_thresholds()
    wide_thresholds = dataclasses.replace(thresholds, max_cards=10_000)

    wide_insights = generate_insights(frames, wide_thresholds, "annual_average")
    assert any(_MECHANISM_PHRASE in insight.text for insight in wide_insights)

    default_insights = generate_insights(frames, thresholds, "annual_average")
    for insight in default_insights:
        assert scan_guardrail_vocabulary(insight.text) == []
