"""D-05 region_select component tests (04-01 Task 2 `<behavior>`). `validate_region` is a pure
unit test; the state-machine functions (`sync_from_query_params`, `clear_region`,
`render_region_picker`) are exercised via `AppTest.from_function` scripts, mirroring
`tests/app/test_filters.py`'s persistent-key/widget-key probe pattern.
"""

from __future__ import annotations

from streamlit.testing.v1 import AppTest

from wii.app.components import body_map, region_select
from wii.app.components.region_select import validate_region

from .conftest import APP_TIMEOUT

_VALID_IDS = {"hand", "finger"}
_LABELS = {"hand": "Hand", "finger": "Finger"}


# ---------------------------------------------------------------------------
# validate_region (pure unit tests)
# ---------------------------------------------------------------------------


def test_validate_region_accepts_exact_member():
    assert validate_region("hand", _VALID_IDS) == "hand"


def test_validate_region_rejects_wrong_case_unknown_and_wrong_type():
    assert validate_region("HAND", _VALID_IDS) is None
    assert validate_region("<script>", _VALID_IDS) is None
    assert validate_region(None, _VALID_IDS) is None
    assert validate_region(["hand"], _VALID_IDS) is None


# ---------------------------------------------------------------------------
# sync_from_query_params (deep-link valid / invalid / absent)
# ---------------------------------------------------------------------------


def _sync_probe():
    import streamlit as st

    from wii.app.components import region_select

    region_select.sync_from_query_params({"hand", "finger"})
    st.write("ran")


def test_sync_from_query_params_valid_deep_link_sets_persist_key():
    at = AppTest.from_function(_sync_probe)
    at.query_params["region"] = "finger"
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception
    assert at.session_state[region_select.PERSIST_KEY] == "finger"


def test_sync_from_query_params_invalid_deep_link_clears_persist_key_and_removes_param():
    at = AppTest.from_function(_sync_probe)
    at.query_params["region"] = "not_a_region"
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception
    assert at.session_state[region_select.PERSIST_KEY] is None
    assert "region" not in at.query_params


def test_sync_from_query_params_absent_param_leaves_persisted_value_unchanged():
    at = AppTest.from_function(_sync_probe)
    at.session_state[region_select.PERSIST_KEY] = "hand"
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception
    assert at.session_state[region_select.PERSIST_KEY] == "hand"


# ---------------------------------------------------------------------------
# clear_region via the "Clear region" button
# ---------------------------------------------------------------------------


def _clear_control_probe():
    import streamlit as st

    from wii.app.components import region_select

    labels = {"hand": "Hand", "finger": "Finger"}
    if region_select.WIDGET_KEY not in st.session_state:
        st.session_state[region_select.WIDGET_KEY] = st.session_state.get(region_select.PERSIST_KEY)
    region_select.render_clear_control(labels)


def test_clear_region_button_resets_persist_widget_and_query_param():
    at = AppTest.from_function(_clear_control_probe)
    at.session_state[region_select.PERSIST_KEY] = "hand"
    at.query_params["region"] = "hand"
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception
    assert at.session_state[region_select.PERSIST_KEY] == "hand"
    assert "Showing supporting data for: Hand." in at.caption[0].value

    button = at.button(key="clear_body_map_region")
    button.click().run(timeout=APP_TIMEOUT)
    assert not at.exception
    assert at.session_state[region_select.PERSIST_KEY] is None
    assert at.session_state[region_select.WIDGET_KEY] is None
    assert "region" not in at.query_params


# ---------------------------------------------------------------------------
# render_region_picker (pills mode is PRIMARY per the recorded spike verdict; links mode is
# exercised by monkeypatching REGION_CLICK_MODE inside the AppTest script)
# ---------------------------------------------------------------------------


def _picker_probe():
    from wii.app.components import region_select

    region_select.render_region_picker(["hand", "finger"], {"hand": "Hand", "finger": "Finger"})


def test_render_region_picker_pills_mode_renders_widget_and_syncs_selection(monkeypatch):
    # monkeypatch (not a mutation inside the probe) so the module attribute is restored at
    # teardown regardless of test outcome -- AppTest.from_function executes the probe's source
    # against the SAME real region_select module object, so an un-restored mutation there would
    # leak into every later test in this process (observed: it broke test_mode_follows_verdict).
    monkeypatch.setattr(region_select, "REGION_CLICK_MODE", "pills")
    at = AppTest.from_function(_picker_probe)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception
    assert len(at.pills) == 1
    pill = at.pills[0]
    assert pill.key == region_select.WIDGET_KEY

    # .select() takes the RAW option value ("hand"), not the format_func-rendered label.
    pill.select("hand").run(timeout=APP_TIMEOUT)
    assert not at.exception
    assert at.session_state[region_select.PERSIST_KEY] == "hand"


def test_render_region_picker_links_mode_renders_nothing(monkeypatch):
    monkeypatch.setattr(region_select, "REGION_CLICK_MODE", "links")
    at = AppTest.from_function(_picker_probe)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception
    assert len(at.pills) == 0


# ---------------------------------------------------------------------------
# REGION_CLICK_MODE follows the recorded SPIKE_VERDICT
# ---------------------------------------------------------------------------


def test_mode_follows_verdict():
    """The mode is derived from the recorded spike verdicts, never set by hand.

    HIT_ZONE_VERDICT (2026-09-16, invisible-button mechanism) outranks SPIKE_VERDICT
    (2026-09-15, `<a href>` mechanism) because it is strictly better where both work: an
    ordinary widget rerun keeps session state, while the anchor is a full navigation that
    cannot. Pills remain the floor if both mechanisms are ever found broken.
    """
    if region_select.HIT_ZONE_VERDICT == "click_ok_session_preserved":
        assert region_select.REGION_CLICK_MODE == "hit_zones"
    elif region_select.SPIKE_VERDICT == "click_ok_session_preserved":
        assert region_select.REGION_CLICK_MODE == "links"
    else:
        assert region_select.REGION_CLICK_MODE == "pills"


def test_hit_zone_mode_renders_no_pills_and_one_button_per_region(app_test):
    """The whole point of the redesign: in hit-zone mode the 22-item pill row is gone, replaced
    by one focusable button per region carrying that region's label as its accessible name."""
    assert region_select.REGION_CLICK_MODE == "hit_zones"
    at = app_test
    # The 3D map is now the Body Map's primary selector; the 2D hit-zone map is the accessible
    # "Flat body map" view (and the WebGL fallback). This test covers the 2D view explicitly.
    at.session_state["body_map_flat_view"] = True
    at.run(timeout=APP_TIMEOUT)
    at.switch_page("src/wii/app/pages/body_map.py")
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception
    assert len(at.pills) == 0
    labels = {b.label for b in at.button}
    # Front and back are separate widgets for the same region, so the accessible name must say
    # which view it is -- otherwise a screen reader hears 17 pairs of identical button names.
    assert "Finger (front view)" in labels
    assert "Finger (back view)" in labels
    assert "Hand (front view)" in labels
    # One button per region per view, not a handful of spot-checked labels.
    front = {label for label in labels if label.endswith("(front view)")}
    assert len(front) == len(body_map.region_hit_boxes("front"))
