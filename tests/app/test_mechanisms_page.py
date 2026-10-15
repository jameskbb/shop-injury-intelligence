"""PAGE-04 Mechanisms AppTest smoke tests (D-15). Task 1 covers the tracer path (default render,
one non-default filter); Task 2 adds the single-tool-focus AppTest case; 04-07 Task 1 adds the
MECHANISM BY TOOL heatmap tests, Task 3 the MECHANISM TREND tests.
"""

from __future__ import annotations

import json
import time

import polars as pl

import wii.app.data as data_module
from wii.analytics.insights import scan_guardrail_vocabulary
from wii.analytics.mechanism_trend import GateResult
from wii.app import filters as filters_module
from wii.app.charts import heatmap as heatmap_module
from wii.app.charts import sankey as sankey_module
from wii.app.charts import trend as trend_module
from wii.app.pages import mechanisms as mechanisms_page

from .conftest import APP_TIMEOUT

_MECHANISMS_PATH = "src/wii/app/pages/mechanisms.py"

#: ONE KPI. The four mechanism-share cards that used to sit beside it (direct contact, kickback,
#: flying debris, maintenance/setup) are now bars under `MECHANISM_SHARES_HEADING`, where each
#: share is readable against the others; as isolated cards they carried no comparator.
_KPI_LABELS = ("Classified narrative coverage",)
_RETIRED_KPI_LABELS = (
    "Direct-contact share",
    "Kickback / workpiece-ejection share",
    "Flying-debris share",
)


def _switch_to_mechanisms(at):
    at.run(timeout=APP_TIMEOUT)
    at.switch_page(_MECHANISMS_PATH)


def _plotly_chart_specs(at) -> list[dict]:
    """AppTest exposes no dedicated `.plotly_chart` accessor -- `st.plotly_chart` elements surface
    as generic `UnknownElement` nodes (type=="plotly_chart") when walking the element tree, each
    carrying the figure's Plotly JSON spec as a string."""
    specs: list[dict] = []

    def _walk(node) -> None:
        if (
            type(node).__name__ == "UnknownElement"
            and getattr(node, "type", None) == "plotly_chart"
        ):
            specs.append(json.loads(node.proto.spec))
        children = getattr(node, "children", None)
        if children:
            for child in children.values() if isinstance(children, dict) else children:
                _walk(child)

    _walk(at.main)
    return specs


def test_mechanisms_default(app_test):
    at = app_test
    _switch_to_mechanisms(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    titles = [t.value for t in at.title]
    assert any("How workshop injuries happen" in t for t in titles)

    md_values = [m.value for m in at.markdown]
    kpi_html = "".join(md_values)
    for label in _KPI_LABELS:
        assert label in kpi_html
    for label in _RETIRED_KPI_LABELS:
        assert label not in kpi_html

    headers = [h.value for h in at.header]
    assert any("Injury pathways" in h for h in headers)
    assert any(mechanisms_page.MECHANISM_SHARES_HEADING in h for h in headers)

    specs = _plotly_chart_specs(at)
    assert specs, "expected at least one plotly chart on the Mechanisms page"
    sankey_specs = [s for s in specs if s["data"] and s["data"][0].get("type") == "sankey"]
    assert sankey_specs, "expected at least one go.Sankey chart on the Mechanisms page"


def test_mechanisms_nondefault_filter(app_test):
    at = app_test
    _switch_to_mechanisms(at)
    at.session_state["year_range"] = (2024, 2025)
    at.session_state["age_scope"] = "all"
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception


def test_mechanisms_sankey_merged_and_hidden_volume_captioned_separately(app_test, monkeypatch):
    """WR-02: `merged_volume` (drawn into 'Other') and `hidden_volume` (dropped entirely below
    the CPSC hard floor) are two distinct facts (04-CONTEXT D-10) and must not be summed into
    one caption."""
    fake_graph = sankey_module.SankeyGraph(
        nodes=(
            {"id": "0:a", "label": "A", "color": "#ffffff"},
            {"id": "1:b", "label": "B", "color": "#ffffff"},
        ),
        links=(
            {
                "source": 0,
                "target": 1,
                "value": 100.0,
                "state": sankey_module.STATE_STABLE,
                "raw_n": 50,
                "ci_low": 80.0,
                "ci_high": 120.0,
            },
        ),
        merged_volume=5000.0,
        hidden_volume=800.0,
        column_totals=(100.0,),
    )
    monkeypatch.setattr(mechanisms_page.sankey, "collapse_pathways", lambda inp: fake_graph)

    at = app_test
    _switch_to_mechanisms(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    caption_values = [c.value for c in at.caption]
    volume_captions = [v for v in caption_values if "merged into" in v]
    assert len(volume_captions) == 1
    # One sentence now, but merged (drawn into "Other") and hidden (not drawn at all) stay two
    # distinct numbers within it, and are never summed.
    assert "5,000" in volume_captions[0]
    assert "800" in volume_captions[0]
    assert "reporting floor" in volume_captions[0]
    assert not any("5,800" in v for v in caption_values)


# ---------------------------------------------------------------------------
# Task 2: D-10 single-tool focus AppTest
# ---------------------------------------------------------------------------


def test_mechanisms_focus_single_tool(app_test):
    at = app_test
    _switch_to_mechanisms(at)
    at.session_state["mech_focus_tool"] = "table_saw"
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception


# ---------------------------------------------------------------------------
# 04-07 Task 1: D-12 MECHANISM BY TOOL heatmap
# ---------------------------------------------------------------------------


def test_mechanisms_heatmap_renders(app_test):
    at = app_test
    _switch_to_mechanisms(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    headers = [h.value for h in at.header]
    assert any("Mechanism by tool" in h for h in headers)

    specs = _plotly_chart_specs(at)
    heatmap_specs = [s for s in specs if s["data"] and s["data"][0].get("type") == "heatmap"]
    assert heatmap_specs, "expected at least one go.Heatmap chart on the Mechanisms page"


#: Same rationale as `test_data_layer.COLD_BUDGET_SECONDS`: these budgets guard that the frame is
#: cached and pre-aggregated rather than scanning raw rows per request -- a two-orders-of-magnitude
#: regression -- so the bound is loose enough to survive a shared, noisy CI runner. A perf test
#: that flakes gets re-run on reflex, which is worse than not having one.
COLD_BUDGET_SECONDS = 3.0


def test_mechanism_tool_share_frame_cold_is_fast():
    data_module.cached_share_by.clear()
    data_module.cached_estimate_by.clear()
    data_module.mechanism_tool_share_frame.clear()
    flt = filters_module.default_case_filter()
    start = time.perf_counter()
    data_module.mechanism_tool_share_frame(flt, "annual_average")
    elapsed = time.perf_counter() - start
    assert elapsed < COLD_BUDGET_SECONDS


# ---------------------------------------------------------------------------
# 04-07 Task 3: D-13 MECHANISM TREND
# ---------------------------------------------------------------------------


def test_mechanism_trend_frame_committed_all_years_pass():
    flt = filters_module.default_case_filter()
    df, gate_result = data_module.mechanism_trend_frame(flt)
    assert gate_result.show_chart is True
    assert gate_result.passing_years == (2019, 2020, 2021, 2022, 2023, 2024, 2025)
    assert df.height > 0
    mechanisms = set(df["mechanism"].to_list())
    assert "unknown" not in mechanisms
    assert "other" not in mechanisms
    assert len(mechanisms) <= 5


def test_share_trend_gaps_and_hollow_markers():
    df = pl.DataFrame(
        [
            {
                "year": 2019,
                "mechanism": "direct_cutting_contact",
                "label": "Direct Cutting Contact",
                "value": 0.4,
                "ci_low": 0.36,
                "ci_high": 0.44,
                "raw_n": 300,
                "state": "stable",
            },
            {
                "year": 2020,
                "mechanism": "direct_cutting_contact",
                "label": "Direct Cutting Contact",
                "value": None,
                "ci_low": None,
                "ci_high": None,
                "raw_n": 0,
                "state": "suppressed",
            },
            {
                "year": 2021,
                "mechanism": "direct_cutting_contact",
                "label": "Direct Cutting Contact",
                "value": 0.3,
                "ci_low": 0.2,
                "ci_high": 0.4,
                "raw_n": 60,
                "state": "limited",
            },
        ]
    )
    fig = trend_module.build_share_trend(df)
    assert len(fig.data) == 1
    trace = fig.data[0]
    assert trace.connectgaps is False
    assert trace.y[1] is None  # suppressed year is gapped, never interpolated
    assert list(trace.marker.symbol) == ["circle", "circle", "circle-open"]


def test_mechanism_trend_insufficient_years_note(app_test, monkeypatch):
    at = app_test
    fake_gate_result = GateResult(
        passing_years=(2024,),
        excluded_years=(2019, 2020, 2021, 2022, 2023, 2025),
        window_share=0.34,
        show_chart=False,
    )
    monkeypatch.setattr(
        data_module, "mechanism_trend_frame", lambda flt: (pl.DataFrame(), fake_gate_result)
    )
    _switch_to_mechanisms(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    info_values = [i.value for i in at.info]
    assert any("showing no trend rather than a misleading one" in text for text in info_values)


def test_mechanisms_trend_renders_default(app_test):
    at = app_test
    _switch_to_mechanisms(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    headers = [h.value for h in at.header]
    assert any("Mechanism trend" in h for h in headers)

    specs = _plotly_chart_specs(at)
    scatter_specs = [s for s in specs if s["data"] and s["data"][0].get("type") == "scatter"]
    assert scatter_specs, "expected at least one go.Scatter trend chart on the Mechanisms page"


def test_mechanism_trend_frame_cold_is_fast():
    data_module.cached_share_by.clear()
    data_module.mechanism_trend_frame.clear()
    flt = filters_module.default_case_filter()
    start = time.perf_counter()
    data_module.mechanism_trend_frame(flt)
    elapsed = time.perf_counter() - start
    assert elapsed < COLD_BUDGET_SECONDS


def test_trend_and_heatmap_copy_passes_guardrails():
    for module in (heatmap_module, trend_module, mechanisms_page):
        for name in dir(module):
            if name.startswith("_"):
                continue
            value = getattr(module, name)
            if isinstance(value, str):
                assert scan_guardrail_vocabulary(value) == [], f"{module.__name__}.{name}"


# ---------------------------------------------------------------------------
# 04-08 Task 1: D-14 tracer -- SETUP & MAINTENANCE header + intro render
# ---------------------------------------------------------------------------


def test_mechanisms_setup_maintenance_renders(app_test):
    """The section is one collapsed expander now (it is a fraction of a percent of cases and its
    first chart had no stable row), but it still exists and still carries its caveat verbatim."""
    at = app_test
    _switch_to_mechanisms(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    labels = [e.label for e in at.expander]
    assert any("Setup & maintenance" in label for label in labels)

    caption_values = [c.value for c in at.caption]
    assert any(mechanisms_page.MAINTENANCE_INTRO in text for text in caption_values)


def test_mechanisms_never_draws_an_all_suppressed_ranked_chart(app_test):
    """MECH-10, the review's P0: when no maintenance activity clears the floor the page must
    print the honest line, never a chart whose empty value axis Plotly labels in milli-units."""
    at = app_test
    _switch_to_mechanisms(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    specs = _plotly_chart_specs(at)
    for spec in specs:
        for trace in spec["data"]:
            if trace.get("type") != "bar":
                continue
            xs = [v for v in (trace.get("x") or []) if isinstance(v, (int, float))]
            assert any(v > 0 for v in xs), "a bar chart was drawn with no non-zero bar"


# ---------------------------------------------------------------------------
# 04-08 Task 2: D-15 all-sections-populated smoke, default + one non-default filter
# ---------------------------------------------------------------------------


def test_mechanisms_all_sections_default(app_test):
    at = app_test
    _switch_to_mechanisms(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception
    _assert_all_sections(at)

    at.session_state["year_range"] = (2024, 2025)
    at.session_state["age_scope"] = "all"
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception
    _assert_all_sections(at)


def _assert_all_sections(at) -> None:
    headers = [h.value for h in at.header]
    for expected in ("Injury pathways", "Mechanism by tool", "Mechanism trend"):
        assert any(expected in h for h in headers), expected
    labels = [e.label for e in at.expander]
    assert any("Setup & maintenance" in label for label in labels)


# ---------------------------------------------------------------------------
# Chart-treatment regressions
# ---------------------------------------------------------------------------


def test_mechanism_matrix_height_follows_the_shared_row_rule():
    """Heights come from the builder's row rule now (`heatmap.ROW_HEIGHT`), not a page-side
    `update_layout` -- one rule for every matrix in the app."""
    assert heatmap_module.ROW_HEIGHT == 28
    assert not hasattr(mechanisms_page, "_HEATMAP_HEIGHT_BASE")
