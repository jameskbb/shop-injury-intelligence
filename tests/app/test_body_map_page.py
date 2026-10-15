"""PAGE-03 Body Map AppTest smoke tests (D-15). Task 1 covers the tracer path (default render,
region deep link, invalid region deep link); Task 2/3 add the non-default-filter, guardrail, and
matrix-render cases.
"""

from __future__ import annotations

import sys
import types

import polars as pl
import pytest

import wii.app.pages.body_map as body_map_page
from wii.analytics.insights import scan_guardrail_vocabulary
from wii.app.components import body_map, region_select
from wii.app.data import _pick_top_stable, filtered_narratives
from wii.metrics import CaseFilter

from .conftest import APP_TIMEOUT

_BODY_MAP_PATH = "src/wii/app/pages/body_map.py"


def _switch_to_body_map(at):
    at.run(timeout=APP_TIMEOUT)
    at.switch_page(_BODY_MAP_PATH)


_BODY3D_MODULE = "wii.app.components.body3d"


class _FakeBody3d:
    """Stands in for the real 3D component (AppTest cannot render WebGL): records every payload
    it is handed and returns whatever `event` is set to, the way a component returns its last
    frontend value on every rerun."""

    def __init__(self, event=None):
        self.event = event
        self.calls: list[tuple[dict, str]] = []

    def __call__(self, payload, *, key):
        self.calls.append((payload, key))
        return self.event


@pytest.fixture
def fake_body3d(monkeypatch):
    fake = _FakeBody3d()
    module = types.ModuleType(_BODY3D_MODULE)
    module.body3d = fake
    monkeypatch.setitem(sys.modules, _BODY3D_MODULE, module)
    return fake


@pytest.fixture
def no_body3d(monkeypatch):
    """`None` in sys.modules makes `import` raise ImportError -- the component is unavailable."""
    monkeypatch.setitem(sys.modules, _BODY3D_MODULE, None)


def _captions(at) -> list[str]:
    return [c.value for c in at.caption]


def _has_hit_zones(at) -> bool:
    return any(b.key and b.key.startswith("body-map-front__") for b in at.button)


def test_body_map_default(app_test, fake_body3d):
    """The 3D figure is the primary visual: it is handed a payload built from the page's own
    filter, the flat map's hit zones are not drawn, and the finger caveat sits by the map."""
    at = app_test
    _switch_to_body_map(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    titles = [t.value for t in at.title]
    assert any("Where workshop injuries happen" in t for t in titles)

    assert fake_body3d.calls, "3D component was never rendered"
    payload, key = fake_body3d.calls[-1]
    assert key == body_map_page.BODY3D_KEY
    assert payload["selected"] is None
    assert payload["active_tool"] is None
    assert payload["reduced_motion"] is False
    assert set(payload["regions"]) >= {"hand", "finger"}
    assert not _has_hit_zones(at)
    assert body_map_page.FINGER_CAPTION in _captions(at)
    assert body_map_page.MAP_3D_CAPTION in _captions(at)

    headers = [h.value for h in at.header]
    assert any("Region detail" in h for h in headers)

    caption_values = [c.value for c in at.caption]
    assert any("Showing all regions. Select a body region above" in v for v in caption_values)

    assert all(ms.key != "widget_global_tool_filter" for ms in at.multiselect)


def test_body_map_renders_one_hit_zone_per_piece_with_help_and_depth_layers(app_test, no_body3d):
    """The redrawn figure has two hands, two feet, two of every bilateral region. Each side gets
    its own tight hit zone (`...__hand__0` / `...__hand__1`) instead of one rectangle spanning the
    torso between them, both zones carry the D-04 help text -- the only textual channel for the
    "Estimate suppressed" / "Limited sample" labels -- and the figure itself arrives as per-piece
    depth layers, explained by the depth caption."""
    at = app_test
    _switch_to_body_map(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    md_values = [m.value for m in at.markdown]
    assert any("wii-body-map" in v for v in md_values)
    assert body_map_page.FINGER_CAPTION in _captions(at)

    buttons = {b.key: b for b in at.button if b.key and b.key.startswith("body-map-")}
    for view in ("front", "back"):
        pieces = body_map.region_hit_boxes(view)
        for region_id, boxes in pieces.items():
            for i in range(len(boxes)):
                key = f"body-map-{view}__{region_id}__{i}"
                assert key in buttons, key
                assert buttons[key].help, key
        assert (
            buttons[f"body-map-{view}__hand__0"].help == buttons[f"body-map-{view}__hand__1"].help
        )

    md_values = [m.value for m in at.markdown]
    assert any('class="wii-body-3d"' in v and "wii-l-hand-1" in v for v in md_values)
    assert any("translateZ(" in v and ".st-key-body-map-front__hand__1{" in v for v in md_values)
    caption_values = [c.value for c in at.caption]
    assert body_map_page.DEPTH_CAPTION in caption_values


# ---------------------------------------------------------------------------
# Task 2: measures, page filters, D-04 tooltips, region detail with narratives
# ---------------------------------------------------------------------------


def test_body_map_nondefault_amputations_table_saw_all_ages(app_test):
    at = app_test
    _switch_to_body_map(at)
    at.session_state["body_map_tool"] = "table_saw"
    at.session_state["body_map_measure"] = "amputation"
    at.session_state["age_scope"] = "all"
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception


def test_body_map_share_measure_no_tool_captions_workshop_wide(app_test):
    """WR-01: with no tool selected, the Share measure must caption itself as the workshop-wide
    share rather than silently implying a tool-specific share (04-CONTEXT D-02)."""
    at = app_test
    _switch_to_body_map(at)
    at.session_state["body_map_tool"] = None
    at.session_state["body_map_measure"] = "share"
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    caption_values = [c.value for c in at.caption]
    assert any(
        "share of all workshop injuries" in v.lower() and "no tool selected" in v.lower()
        for v in caption_values
    )


def test_body_map_share_measure_with_tool_no_workshop_caption(app_test):
    """WR-01 counterpart: when a tool IS selected, the workshop-wide caveat must not appear."""
    at = app_test
    _switch_to_body_map(at)
    at.session_state["body_map_tool"] = "table_saw"
    at.session_state["body_map_measure"] = "share"
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    caption_values = [c.value for c in at.caption]
    assert not any("no tool selected" in v.lower() for v in caption_values)


def test_body_map_copy_passes_guardrails():
    for name in dir(body_map_page):
        if name.startswith("_"):
            continue
        value = getattr(body_map_page, name)
        if isinstance(value, str):
            assert scan_guardrail_vocabulary(value) == [], f"{name} failed guardrail scan"


def test_region_top_categories_stable_only_excludes_unknown_other():
    rows = [
        {
            "group_id_1": "finger",
            "group_id_2": "unknown",
            "annual_average": 5000.0,
            "ci_low_annual": 4000.0,
            "ci_high_annual": 6000.0,
            "raw_n": 200,
            "cv": 0.05,
        },
        {
            "group_id_1": "finger",
            "group_id_2": "direct_cutting_contact",
            "annual_average": 3000.0,
            "ci_low_annual": 2500.0,
            "ci_high_annual": 3500.0,
            "raw_n": 150,
            "cv": 0.05,
        },
        {
            "group_id_1": "finger",
            "group_id_2": "kickback_ejection",
            "annual_average": 1500.0,
            "ci_low_annual": 100.0,
            "ci_high_annual": 4000.0,
            "raw_n": 10,
            "cv": 0.05,
        },
    ]
    df = pl.DataFrame(rows)
    best = _pick_top_stable(df, "annual_average", exclude=frozenset({"unknown", "other"}))
    assert best["finger"] == "Direct Cutting Contact"


def test_filtered_narratives_region_constraint_and_limit():
    flt = CaseFilter(
        years=(2021, 2025), age_scope="adults", body_regions=("finger",), body_basis="any"
    )
    df = filtered_narratives(flt, 0.70, limit=5)
    assert df.height <= 5
    confidences = df["confidence"].to_list()
    assert confidences == sorted(confidences, reverse=True)


def test_body_map_region_query_param(app_test):
    at = app_test
    _switch_to_body_map(at)
    at.query_params["region"] = "finger"
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    caption_values = [c.value for c in at.caption]
    assert any("Showing supporting data for: Finger." in v for v in caption_values)


def test_body_map_invalid_region_param(app_test):
    at = app_test
    _switch_to_body_map(at)
    at.query_params["region"] = "not_a_region"
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    caption_values = [c.value for c in at.caption]
    assert any("Showing all regions. Select a body region above" in v for v in caption_values)


# ---------------------------------------------------------------------------
# Task 3: BODY PART x TOOL MATRIX
# ---------------------------------------------------------------------------


def test_body_map_matrix_renders(app_test):
    at = app_test
    _switch_to_body_map(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    headers = [h.value for h in at.header]
    assert any("Body part × tool matrix" in h for h in headers)


# ---------------------------------------------------------------------------
# 3D body map wiring (the WebGL pixels themselves are checked by scripts/verify_body3d.py)
# ---------------------------------------------------------------------------


def test_body_map_falls_back_when_component_unavailable(app_test, no_body3d):
    at = app_test
    _switch_to_body_map(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception
    assert _has_hit_zones(at)
    assert body_map_page.WEBGL_FALLBACK_CAPTION not in _captions(at)
    assert all(t.label != body_map_page.FLAT_VIEW_LABEL for t in at.toggle)


@pytest.mark.parametrize(
    ("event", "shown", "hidden"),
    [
        (
            {"webgl": False, "reason": "no-webgl"},
            body_map_page.WEBGL_FALLBACK_CAPTION,
            body_map_page.LOAD_FALLBACK_CAPTION,
        ),
        (
            {"webgl": False, "reason": "three-load-failed"},
            body_map_page.LOAD_FALLBACK_CAPTION,
            body_map_page.WEBGL_FALLBACK_CAPTION,
        ),
        (
            {"webgl": False, "reason": "renderer-failed"},
            body_map_page.LOAD_FALLBACK_CAPTION,
            body_map_page.WEBGL_FALLBACK_CAPTION,
        ),
        # No reason reported: never blame WebGL for a failure we cannot name.
        (
            {"webgl": False},
            body_map_page.LOAD_FALLBACK_CAPTION,
            body_map_page.WEBGL_FALLBACK_CAPTION,
        ),
    ],
)
def test_body_map_fallback_copy_names_the_failure(app_test, fake_body3d, event, shown, hidden):
    fake_body3d.event = event
    at = app_test
    _switch_to_body_map(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception
    assert _has_hit_zones(at)
    captions = _captions(at)
    assert shown in captions
    assert hidden not in captions


def test_body_map_fallback_unmounts_3d_container(app_test, fake_body3d):
    """The run that learns of the fallback reruns before drawing the flat map, so the final render
    holds no 3D component (no empty fixed-height panel above the flat map) -- and later reruns
    never mount it again."""
    fake_body3d.event = {"webgl": False, "reason": "three-load-failed"}
    at = app_test
    _switch_to_body_map(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception
    # Mounted exactly once -- on the run that received the report -- then never again.
    assert len(fake_body3d.calls) == 1
    assert _has_hit_zones(at)
    assert body_map_page.MAP_3D_CAPTION not in _captions(at)
    assert all(t.label != body_map_page.FLAT_VIEW_LABEL for t in at.toggle)
    assert body_map_page.FINGER_CAPTION in _captions(at)

    at.run(timeout=APP_TIMEOUT)
    assert not at.exception
    assert len(fake_body3d.calls) == 1
    assert _has_hit_zones(at)
    assert body_map_page.LOAD_FALLBACK_CAPTION in _captions(at)


def test_body_map_all_tools_passes_no_active_tool(app_test, fake_body3d):
    """All workshop tools must not hand the figure a single tool to headline."""
    at = app_test
    _switch_to_body_map(at)
    at.run(timeout=APP_TIMEOUT)
    at.selectbox(key="widget_body_map_tool").set_value("table_saw").run(timeout=APP_TIMEOUT)
    assert fake_body3d.calls[-1][0]["active_tool"] == "table_saw"
    at.selectbox(key="widget_body_map_tool").set_value(None).run(timeout=APP_TIMEOUT)
    assert not at.exception
    assert fake_body3d.calls[-1][0]["active_tool"] is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"webgl": False, "reason": "no-webgl"}, "no-webgl"),
        ({"webgl": False, "reason": "three-load-failed"}, "three-load-failed"),
        ({"webgl": False, "reason": "<script>"}, None),
        ({"webgl": False}, None),
    ],
)
def test_interpret_result_carries_known_fallback_reason(raw, expected):
    from wii.app.components.body3d import interpret_result

    out = interpret_result(raw)
    assert out["webgl"] is False
    assert out.get("reason") == expected


def test_body_map_falls_back_when_component_raises(app_test, fake_body3d):
    def _boom(payload, *, key):
        raise RuntimeError("component failed")

    sys.modules[_BODY3D_MODULE].body3d = _boom
    at = app_test
    _switch_to_body_map(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception
    assert _has_hit_zones(at)


def test_body_map_flat_view_toggle(app_test, fake_body3d):
    at = app_test
    _switch_to_body_map(at)
    at.run(timeout=APP_TIMEOUT)
    assert not _has_hit_zones(at)
    at.toggle(key="widget_body_map_flat_view").set_value(True).run(timeout=APP_TIMEOUT)
    assert not at.exception
    assert _has_hit_zones(at)


def test_body_map_3d_click_selects_and_reclick_clears(app_test, fake_body3d):
    at = app_test
    _switch_to_body_map(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    fake_body3d.event = {"selected": "finger", "seq": 1}
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception
    assert at.session_state[region_select.PERSIST_KEY] == "finger"
    assert at.query_params["region"] == ["finger"]
    assert any("Showing supporting data for: Finger." in v for v in _captions(at))
    # The page reran after the click, so the figure itself was told about the selection.
    assert fake_body3d.calls[-1][0]["selected"] == "finger"

    # The component keeps returning its last value; a plain rerun must not re-toggle it.
    at.run(timeout=APP_TIMEOUT)
    assert at.session_state[region_select.PERSIST_KEY] == "finger"

    # A second click on the same region (a new event) clears it, like pick_region.
    fake_body3d.event = {"selected": "finger", "seq": 2}
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception
    assert at.session_state[region_select.PERSIST_KEY] is None
    assert "region" not in at.query_params
    assert any("Showing all regions. Select a body region above" in v for v in _captions(at))

    # And a component-side deselect (selected=None) clears an existing selection.
    fake_body3d.event = {"selected": "hand", "seq": 3}
    at.run(timeout=APP_TIMEOUT)
    assert at.session_state[region_select.PERSIST_KEY] == "hand"
    fake_body3d.event = {"selected": None, "seq": 4}
    at.run(timeout=APP_TIMEOUT)
    assert at.session_state[region_select.PERSIST_KEY] is None


def test_body_map_3d_ignores_unknown_region(app_test, fake_body3d):
    at = app_test
    _switch_to_body_map(at)
    fake_body3d.event = {"selected": "<script>", "seq": 1}
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception
    assert region_select.PERSIST_KEY not in at.session_state or (
        at.session_state[region_select.PERSIST_KEY] is None
    )
    assert "region" not in at.query_params


def test_body_map_3d_deep_link_reaches_payload(app_test, fake_body3d):
    at = app_test
    _switch_to_body_map(at)
    at.query_params["region"] = "hand"
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception
    assert fake_body3d.calls[-1][0]["selected"] == "hand"
    assert any("Showing supporting data for: Hand." in v for v in _captions(at))


def test_body_map_tool_drives_3d_drilldown(app_test, fake_body3d):
    at = app_test
    _switch_to_body_map(at)
    at.session_state["body_map_tool"] = "table_saw"
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception
    assert fake_body3d.calls[-1][0]["active_tool"] == "table_saw"


def test_body_map_3d_one_shot_trigger_reclick_clears(app_test, fake_body3d):
    """The real component reports each click once (then None), so two clicks on the same region
    produce identical event dicts separated by an empty rerun -- the second must still clear."""
    at = app_test
    _switch_to_body_map(at)
    fake_body3d.event = {"selected": "hand"}
    at.run(timeout=APP_TIMEOUT)
    assert at.session_state[region_select.PERSIST_KEY] == "hand"
    fake_body3d.event = None
    at.run(timeout=APP_TIMEOUT)
    assert at.session_state[region_select.PERSIST_KEY] == "hand"
    fake_body3d.event = {"selected": "hand"}
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception
    assert at.session_state[region_select.PERSIST_KEY] is None


def test_body_map_opens_with_a_title_block(app_test, fake_body3d):
    """Every analytical page opens with the strip: what is drawn, on what basis, over what
    scope, from which source. The source/freshness line is read from the data, never written
    into the page, and the basis is stated HERE rather than again in a caption above the
    figure."""
    at = app_test
    _switch_to_body_map(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    # The injected stylesheet also mentions the class; match the strip itself.
    strips = [m.value for m in at.markdown if '<div class="wii-titleblock"' in m.value]
    assert len(strips) == 1, "the page opens with exactly one title block"
    strip = strips[0]
    assert body_map_page.DRAWING in strip
    assert "CPSC NEISS · through" in strip
    # The measure the plate is drawn on is a scope fact on this page: the three diagnosis-subset
    # measures REPLACE the page's diagnosis selector, so the strip must say which was drawn.
    assert body_map_page.MEASURES["estimated"] in strip

    # ...and it is not restated directly above the figure. Only the any-recorded body basis,
    # which the strip cannot carry, remains as a caption.
    captions = _captions(at)
    assert body_map_page.BASIS_CAPTION_ANY in captions
    assert not any(
        c.startswith("Estimated U.S. ER-treated injuries")
        and ("annual average" in c or "selected period" in c)
        for c in captions
    ), [c for c in captions if c.startswith("Estimated U.S.")]


def test_body_map_title_block_follows_the_page_selectors(app_test, fake_body3d):
    """The strip describes the filter the page actually queried, including this page's own
    tool and measure selectors -- which the sidebar's filter chips know nothing about."""
    at = app_test
    _switch_to_body_map(at)
    at.run(timeout=APP_TIMEOUT)
    at.selectbox(key="widget_body_map_tool").set_value("table_saw").run(timeout=APP_TIMEOUT)
    at.selectbox(key="widget_body_map_measure").set_value(body_map_page.MEASURES["amputation"]).run(
        timeout=APP_TIMEOUT
    )
    assert not at.exception

    strip = next(m.value for m in at.markdown if '<div class="wii-titleblock"' in m.value)
    assert body_map_page.MEASURES["amputation"] in strip
    assert "Bench or Table Saw" in strip
