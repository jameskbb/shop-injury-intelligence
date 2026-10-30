"""PAGE-06 My Shop tests (D-01, D-02, D-06). 05-02 Task 1: empty-start selector, the verbatim
disclaimer rendered on every path, and the injury profile's KPI strip.
"""

from __future__ import annotations

import ast
import html as html_module
from dataclasses import replace

import polars as pl

import wii.app.data as data_module
import wii.app.pages.my_shop as my_shop_module
from wii.analytics.insights import load_thresholds, scan_guardrail_vocabulary
from wii.app import filters as filters_module
from wii.app.components.disclaimer_banner import DISCLAIMER_TEXT, disclaimer_html
from wii.app.components.narrative_card import narrative_card_html
from wii.app.components.stability_badge import STATE_STABLE, resolve
from wii.metrics import CaseFilter
from wii.utils.paths import repo_root

from .conftest import APP_TIMEOUT

_MY_SHOP_PATH = "src/wii/app/pages/my_shop.py"


def _switch_to_my_shop(at):
    at.run(timeout=APP_TIMEOUT)
    at.switch_page(_MY_SHOP_PATH)


def _expected_default_top_tools(n: int) -> list[str]:
    """The `n` highest-burden STABLE tools under the default filter, computed LIVE (never a
    literal tool id) -- mirrors `test_pages_smoke._expected_default_comparison_tools`."""
    years = filters_module._default_year_range()
    default_flt = CaseFilter(years=years, age_scope="adults")
    scored = []
    for row in data_module.cached_estimate_by(default_flt, "tool").iter_rows(named=True):
        resolved = resolve(row, "annual_average")
        if resolved.state != STATE_STABLE or resolved.value is None:
            continue
        scored.append((resolved.value, row["group_id"]))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [tool_id for _, tool_id in scored[:n]]


def test_my_shop_empty_default():
    from streamlit.testing.v1 import AppTest

    from wii.utils.paths import repo_root

    at = AppTest.from_file(str(repo_root() / "app.py"))
    _switch_to_my_shop(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    titles = [t.value for t in at.title]
    assert any("What should I be mindful of in my shop?" in t for t in titles)

    ms = at.multiselect(key="widget_my_shop_selected_tools")
    assert len(ms.options) == 31
    assert list(ms.value) == []

    headers = [h.value for h in at.subheader]
    assert any("Select your tools to see your shop's injury profile." in h for h in headers)

    # The caveat still brackets every render path -- a one-line form above the numbers and the
    # full verbatim statement above the footer. Two copies of the full 40-word block pushed the
    # KPI strip below the fold on a phone, so the reader met the caveat before meeting anything
    # it qualified; the short line keeps it in front of the numbers at a fraction of the height.
    md_values = [m.value for m in at.markdown]
    full = [v for v in md_values if DISCLAIMER_TEXT in v]
    short = [v for v in md_values if my_shop_module.DISCLAIMER_TOP_LINE in v]
    assert len(full) == 1, f"expected the full disclaimer exactly once, found {len(full)}"
    assert len(short) == 1, f"expected the short disclaimer exactly once, found {len(short)}"

    section_headers = [h.value for h in at.header]
    assert not any(my_shop_module.PROFILE_HEADING in h for h in section_headers)

    assert all(ms.key != "widget_global_tool_filter" for ms in at.multiselect)


def test_my_shop_empty_state_tool_count_matches_dim_tool():
    """WR-01: the empty-state copy's tool count must be read from `dim_tool`, never a literal."""
    from streamlit.testing.v1 import AppTest

    from wii.utils.paths import repo_root

    tool_count = data_module.load_dims()["tools"].height

    at = AppTest.from_file(str(repo_root() / "app.py"))
    _switch_to_my_shop(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    md_values = [m.value for m in at.markdown]
    assert any(f"from the {tool_count} tracked workshop tools" in v for v in md_values)


def test_my_shop_two_tools_profile():
    from streamlit.testing.v1 import AppTest

    from wii.utils.paths import repo_root

    tools = _expected_default_top_tools(2)
    assert len(tools) == 2, "fixture must have >=2 stable tools under the default filter"

    at = AppTest.from_file(str(repo_root() / "app.py"))
    _switch_to_my_shop(at)
    at.session_state["my_shop_selected_tools"] = tuple(tools)
    at.session_state["widget_my_shop_selected_tools"] = list(tools)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    section_headers = [h.value for h in at.header]
    assert any(my_shop_module.PROFILE_HEADING in h for h in section_headers)

    kpi_html = "".join(v for v in [m.value for m in at.markdown] if "wii-kpi-strip" in v)
    assert "Estimated injuries" in kpi_html
    assert "Share of workshop burden" in kpi_html

    md_values = [m.value for m in at.markdown]
    assert len([v for v in md_values if DISCLAIMER_TEXT in v]) == 1
    assert len([v for v in md_values if my_shop_module.DISCLAIMER_TOP_LINE in v]) == 1


def test_disclaimer_html_is_escaped_single_block():
    out = disclaimer_html()
    assert "\n\n" not in out
    assert out.startswith('<div class="wii-disclaimer">')
    assert out.endswith("</div>")
    inner = out[len('<div class="wii-disclaimer">') : -len("</div>")]
    assert html_module.unescape(inner) == DISCLAIMER_TEXT


# ---------------------------------------------------------------------------
# Task 2: selected_tool_share_ranking partition + top regions/diagnoses/mechanisms + ranked lists
# ---------------------------------------------------------------------------


def _default_flt(tools: tuple[str, ...]) -> CaseFilter:
    years = filters_module._default_year_range()
    return CaseFilter(years=years, age_scope="adults", tools=tools)


def test_selected_tool_share_ranking_partitions_with_no_overlap():
    tools_dim = data_module.load_dims()["tools"]
    all_tool_ids = tuple(tools_dim["tool_id"].to_list())
    flt = _default_flt(all_tool_ids)
    ranked = data_module.selected_tool_share_ranking(flt, "amputation")
    stable_ids = {e["tool_id"] for e in ranked.stable}
    limited_ids = {e["tool_id"] for e in ranked.limited}
    assert stable_ids.isdisjoint(limited_ids)
    assert all(e["state"] == "stable" for e in ranked.stable)
    assert all(e["state"] == "limited" for e in ranked.limited)
    values = [e["value"] for e in ranked.stable]
    assert values == sorted(values, reverse=True)
    assert (stable_ids | limited_ids) <= set(all_tool_ids)
    assert ranked.suppressed_count >= 0


def test_selected_tool_share_ranking_excludes_unselected_tools():
    tools_dim = data_module.load_dims()["tools"]
    all_tool_ids = tuple(tools_dim["tool_id"].to_list())
    subset = tuple(sorted(all_tool_ids)[:3])
    flt = _default_flt(subset)
    ranked = data_module.selected_tool_share_ranking(flt, "amputation")
    seen_ids = {e["tool_id"] for e in (*ranked.stable, *ranked.limited)}
    assert seen_ids <= set(subset)


def test_my_shop_one_tool_shows_top_lists_and_ranked_lists():
    from streamlit.testing.v1 import AppTest

    from wii.utils.paths import repo_root

    tool = _expected_default_top_tools(1)[0]
    at = AppTest.from_file(str(repo_root() / "app.py"))
    _switch_to_my_shop(at)
    at.session_state["my_shop_selected_tools"] = (tool,)
    at.session_state["widget_my_shop_selected_tools"] = [tool]
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    # The three ranked text lists are one small table now: as separate lists the stability
    # channel was a divider between blocks, so a limited row could sit above a stable one and
    # a 0.0% row was printed as "associated with" an outcome it had no estimate for.
    subheaders = [h.value for h in at.subheader]
    for expected in (
        "Top body regions (any recorded)",
        "Top diagnosis categories",
        "Top primary mechanisms",
        my_shop_module._OUTCOME_TABLE_HEADING,
    ):
        assert any(expected in s for s in subheaders), f"missing subheader {expected!r}"
    for retired in ("Tools most associated with", "Mechanism shares across your tools"):
        assert not any(retired in s for s in subheaders), f"{retired!r} should be retired"


def test_my_shop_all_31_tools_selected_no_exception():
    from streamlit.testing.v1 import AppTest

    from wii.utils.paths import repo_root

    tools_dim = data_module.load_dims()["tools"]
    all_tool_ids = tools_dim["tool_id"].to_list()
    assert len(all_tool_ids) == 31

    at = AppTest.from_file(str(repo_root() / "app.py"))
    _switch_to_my_shop(at)
    at.session_state["my_shop_selected_tools"] = tuple(all_tool_ids)
    at.session_state["widget_my_shop_selected_tools"] = list(all_tool_ids)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception


def test_my_shop_top_mechanisms_frame_excludes_unknown_and_other():
    import wii.app.pages.my_shop as my_shop_module

    tool = _expected_default_top_tools(1)[0]
    flt = _default_flt((tool,))
    df = my_shop_module._top_mechanisms_frame(flt)
    if df.height:
        assert "unknown" not in df["category"].to_list()
        assert "other" not in df["category"].to_list()


# ---------------------------------------------------------------------------
# Task 3: "Protect this" -- selection-scaled body map
# ---------------------------------------------------------------------------


def test_my_shop_protect_this_renders_static_body_map_no_region_links():
    from streamlit.testing.v1 import AppTest

    from wii.utils.paths import repo_root

    tools = _expected_default_top_tools(2)
    at = AppTest.from_file(str(repo_root() / "app.py"))
    _switch_to_my_shop(at)
    at.session_state["my_shop_selected_tools"] = tuple(tools)
    at.session_state["widget_my_shop_selected_tools"] = list(tools)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    section_headers = [h.value for h in at.header]
    assert any(my_shop_module.PROTECT_HEADING in h for h in section_headers)

    md_values = [m.value for m in at.markdown]
    assert any("wii-body-map" in v for v in md_values)
    assert not any("?region=" in v for v in md_values)

    captions = [c.value for c in at.caption]
    from wii.app.pages.my_shop import PROTECT_SUBTITLE

    assert any(PROTECT_SUBTITLE in c for c in captions)


def test_protect_scale_defaults_to_0_1_when_all_suppressed():
    import wii.app.pages.my_shop as my_shop_module
    from wii.app.components.body_map import RegionValue

    values = {
        "hand": RegionValue(
            label="Hand", value=None, display_value="Not reportable", state="suppressed", raw_n=0
        ),
    }
    scale = my_shop_module._protect_scale(values, {"hand"})
    assert scale.vmin == 0.0
    assert scale.vmax == 1.0


# ---------------------------------------------------------------------------
# 05-03 Task 1: WATCH-OUTS -- one card per selected tool (D-03)
# ---------------------------------------------------------------------------


def test_my_shop_watchouts_one_tool():
    from streamlit.testing.v1 import AppTest

    from wii.app.pages.my_shop import WATCHOUTS_HEADING
    from wii.utils.paths import repo_root

    tool = _expected_default_top_tools(1)[0]
    at = AppTest.from_file(str(repo_root() / "app.py"))
    _switch_to_my_shop(at)
    at.session_state["my_shop_selected_tools"] = (tool,)
    at.session_state["widget_my_shop_selected_tools"] = [tool]
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    section_headers = [h.value for h in at.header]
    assert any(WATCHOUTS_HEADING in h for h in section_headers)

    md_values = [m.value for m in at.markdown]
    grid_html = "".join(v for v in md_values if v.startswith('<div class="wii-watchout-grid">'))
    assert grid_html.count("wii-watchout-card") == 1
    assert not any("Show " in e.label and "more tools" in e.label for e in at.expander)


def test_my_shop_watchouts_seven_tools():
    from streamlit.testing.v1 import AppTest

    from wii.app.pages.my_shop import WATCHOUT_GRID_LIMIT
    from wii.utils.paths import repo_root

    tools = _expected_default_top_tools(7)
    assert len(tools) == 7, "fixture must have >=7 stable tools under the default filter"

    at = AppTest.from_file(str(repo_root() / "app.py"))
    _switch_to_my_shop(at)
    at.session_state["my_shop_selected_tools"] = tuple(tools)
    at.session_state["widget_my_shop_selected_tools"] = list(tools)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    md_values = [m.value for m in at.markdown]
    grid_htmls = [v for v in md_values if v.startswith('<div class="wii-watchout-grid">')]
    assert grid_htmls[0].count("wii-watchout-card") == WATCHOUT_GRID_LIMIT

    expander_labels = [e.label for e in at.expander]
    assert any(label == "Show 1 more tools" for label in expander_labels)


def test_watchout_frames_calls_insight_frames_once():
    import wii.app.data as data_module

    tools = tuple(_expected_default_top_tools(3))
    flt = _default_flt(())

    calls = {"n": 0}
    original_insight_frames = data_module.insight_frames

    def counting_wrapper(*args, **kwargs):
        calls["n"] += 1
        return original_insight_frames(*args, **kwargs)

    data_module.watchout_frames.clear()
    data_module.insight_frames = counting_wrapper
    try:
        data_module.watchout_frames(flt, "annual_average", tools)
    finally:
        data_module.insight_frames = original_insight_frames
        data_module.watchout_frames.clear()

    assert calls["n"] == 1


# ---------------------------------------------------------------------------
# 05-03 Task 3: WHAT THE NARRATIVES REPEAT (D-05)
# ---------------------------------------------------------------------------


def test_my_shop_narratives_section_renders_guardrail_and_bounded_cards():
    from streamlit.testing.v1 import AppTest

    from wii.app.pages.my_shop import NARRATIVES_GUARDRAIL, NARRATIVES_HEADING
    from wii.utils.paths import repo_root as _repo_root

    tools = _expected_default_top_tools(2)
    at = AppTest.from_file(str(_repo_root() / "app.py"))
    _switch_to_my_shop(at)
    at.session_state["my_shop_selected_tools"] = tuple(tools)
    at.session_state["widget_my_shop_selected_tools"] = list(tools)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    section_headers = [h.value for h in at.header]
    assert any(NARRATIVES_HEADING in h for h in section_headers)

    captions = [c.value for c in at.caption]
    assert any(NARRATIVES_GUARDRAIL in c for c in captions)

    md_values = [m.value for m in at.markdown]
    shop_list_htmls = [v for v in md_values if v.startswith('<div class="wii-shop-list">')]
    assert not any("Unknown ·" in v for v in shop_list_htmls)

    narrative_list_htmls = [
        v for v in md_values if v.startswith('<div class="wii-narrative-list">')
    ]
    total_cards = sum(v.count("wii-narrative-card") for v in narrative_list_htmls)
    assert total_cards <= 6
    assert all(v.count("wii-narrative-card") <= 2 for v in narrative_list_htmls)


def test_narrative_cards_match_filtered_narratives_verbatim():
    import wii.app.pages.my_shop as my_shop_module

    tools = tuple(_expected_default_top_tools(2))
    sel_flt = _default_flt(tools)
    mech_df = data_module.mechanism_profile_frame(sel_flt)
    ranked = my_shop_module._ranked_mechanism_rows(mech_df)
    stable_mechanisms = [r["category"] for r in ranked if r["state"] == "stable"]
    assert stable_mechanisms, "fixture must yield >=1 stable mechanism for the top tools"

    mechanism = stable_mechanisms[0]
    mech_flt = replace(sel_flt, mechanisms=(mechanism,))
    thresholds = load_thresholds()
    narratives = data_module.filtered_narratives(
        mech_flt, thresholds.narrative_min_confidence, limit=my_shop_module.NARRATIVES_PER_MECHANISM
    )
    assert narratives.height > 0, "fixture must have >=1 narrative for the top stable mechanism"

    for row in narratives.iter_rows(named=True):
        card = narrative_card_html(row)
        start = card.index('<div class="wii-narrative-text">') + len(
            '<div class="wii-narrative-text">'
        )
        end = card.index("</div>", start)
        rendered = html_module.unescape(card[start:end])
        assert rendered == (row["narrative"] or "")


def test_ranked_mechanism_rows_empty_when_all_suppressed():
    import wii.app.pages.my_shop as my_shop_module

    mech_df = pl.DataFrame(
        [
            {
                "category": "direct_cutting_contact",
                "label": "Direct Cutting Contact",
                "value": 0.0,
                "ci_low": None,
                "ci_high": None,
                "raw_n": 3,
                "state": "suppressed",
            }
        ]
    )
    ranked = my_shop_module._ranked_mechanism_rows(mech_df)
    stable = [r for r in ranked if r["state"] == "stable"]
    limited = [r for r in ranked if r["state"] == "limited"]
    assert stable == []
    assert limited == []


def test_ranked_mechanism_rows_excludes_unknown_and_other():
    import wii.app.pages.my_shop as my_shop_module

    mech_df = pl.DataFrame(
        [
            {
                "category": "unknown",
                "label": "Unknown",
                "value": 0.9,
                "ci_low": None,
                "ci_high": None,
                "raw_n": 500,
                "state": "stable",
            },
            {
                "category": "other",
                "label": "Other",
                "value": 0.8,
                "ci_low": None,
                "ci_high": None,
                "raw_n": 400,
                "state": "stable",
            },
            {
                "category": "kickback_ejection",
                "label": "Kickback Ejection",
                "value": 0.3,
                "ci_low": None,
                "ci_high": None,
                "raw_n": 100,
                "state": "stable",
            },
        ]
    )
    ranked = my_shop_module._ranked_mechanism_rows(mech_df)
    categories = {r["category"] for r in ranked}
    assert categories == {"kickback_ejection"}


def test_partition_maintenance_rows_empty_when_all_suppressed():
    import wii.app.pages.my_shop as my_shop_module

    df = pl.DataFrame(
        [
            {
                "display_label": "Blade change",
                "group_id": "blade_change",
                "annual_average": 0.0,
                "period_total": 0.0,
                "ci_low_annual": None,
                "ci_high_annual": None,
                "ci_low": None,
                "ci_high": None,
                "raw_n": 0,
                "cv": None,
            }
        ]
    )
    stable, limited = my_shop_module._partition_maintenance_rows(df, "annual_average")
    assert stable == []
    assert limited == []


def test_guardrail_vocabulary_clean_for_my_shop_and_watchout_card_and_disclaimer():
    from wii.app.components.disclaimer_banner import DISCLAIMER_TEXT as _DISCLAIMER_TEXT

    paths = [
        repo_root() / "src" / "wii" / "app" / "pages" / "my_shop.py",
        repo_root() / "src" / "wii" / "app" / "components" / "watchout_card.py",
    ]
    offenses: list[str] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                found = scan_guardrail_vocabulary(node.value.value)
                if found:
                    offenses.append(f"{path}: {node.value.value!r} -> {found}")
    assert not offenses, "\n".join(offenses)
    assert scan_guardrail_vocabulary(_DISCLAIMER_TEXT) == []
