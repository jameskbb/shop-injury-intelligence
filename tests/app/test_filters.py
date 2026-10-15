"""D-03/APP-02 sidebar filter tests: deterministic option ordering, tuple-only CaseFilter, and
hidden-widget value persistence (Wave-0 spike finding (e)).
"""

from __future__ import annotations

import streamlit as st
from streamlit.testing.v1 import AppTest

from wii.app import filters
from wii.app.data import available_years, load_dims


def test_available_years_ascending():
    years = available_years()
    assert list(years) == sorted(years)


def test_family_options_ordered_by_sort_order():
    dims = load_dims()
    sort_orders = dims["families"]["sort_order"].to_list()
    assert sort_orders == sorted(sort_orders)


def test_tool_options_ordered_by_label():
    dims = load_dims()
    labels = dims["tools"]["label"].to_list()
    assert labels == sorted(labels)


def test_current_filter_fields_are_tuples():
    st.session_state.clear()
    filters.ensure_defaults()
    st.session_state["f_families"] = ("saws",)
    st.session_state["global_tool_filter"] = ("circular_saw",)
    flt = filters.current_filter()
    assert isinstance(flt.years, tuple)
    assert isinstance(flt.families, tuple)
    assert isinstance(flt.tools, tuple)
    assert isinstance(flt.sexes, tuple)
    assert isinstance(flt.body_regions, tuple)
    assert isinstance(flt.diagnosis_categories, tuple)


def test_current_filter_tools_override_ignores_global_tool_filter():
    st.session_state.clear()
    filters.ensure_defaults()
    st.session_state["global_tool_filter"] = ("circular_saw",)
    flt = filters.current_filter(tools_override=())
    assert flt.tools == ()


def _sidebar_probe(hide_tool: bool):
    import streamlit as st

    from wii.app import filters

    hide = st.session_state.get("_test_hide_tool", hide_tool)
    filters.render_sidebar(hide_tool=hide)


def test_global_tool_filter_survives_hidden_rerun():
    """The PERSISTED global_tool_filter value must survive a rerun where the widget itself is
    hidden and its own session_state key is dropped (Wave-0 spike finding (e), Pitfall 4).
    """
    at = AppTest.from_function(_sidebar_probe, kwargs={"hide_tool": False})
    at.run(timeout=30)
    assert not at.exception

    # tool_ids sorted by label -- the order render_sidebar's Tool multiselect uses.
    tool_ids = load_dims()["tools"]["tool_id"].to_list()
    expected_tool_id = tool_ids[0]

    tool_widget = next(m for m in at.sidebar.multiselect if m.key == "widget_global_tool_filter")
    # .options holds the FORMATTED (label) display strings; .select() translates the selection
    # back to the underlying raw tool_id via the widget's own format_func.
    tool_widget.select(tool_widget.options[0])
    at.run(timeout=30)
    assert not at.exception
    assert at.session_state["global_tool_filter"] == (expected_tool_id,)
    assert "widget_global_tool_filter" in at.session_state

    at.session_state["_test_hide_tool"] = True
    at.run(timeout=30)
    assert not at.exception
    assert "widget_global_tool_filter" not in at.session_state
    assert at.session_state["global_tool_filter"] == (expected_tool_id,)
