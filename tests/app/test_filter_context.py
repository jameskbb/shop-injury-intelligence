"""Active-filter context (reference template: "Show active filter context visibly").

Covers the chip model in `wii.app.filters` and the presentation component that renders it,
including the honesty cases: the always-on scope chips at their defaults, the explicit
"no advanced filters" statement, page-owned tool selections, and the family/tool combination that
looks broken but is legal.

Also pins the deliberate NON-cascading behaviour of the advanced-filter option lists
(`filters.OPTIONS_DO_NOT_CASCADE`): narrowing them would let a saved selection vanish from under
the persistent-key contract, so a future change in that direction has to face this test first.
"""

from __future__ import annotations

import streamlit as st
from streamlit.testing.v1 import AppTest

from wii.app import filters
from wii.app.components import filter_context
from wii.app.data import load_dims
from wii.app.filters import FilterChip

_SCOPE_KEYS = ("year_range", "age_scope", "basis")


def _fresh_state() -> None:
    st.session_state.clear()
    filters.ensure_defaults()


def _chip(chips, key: str) -> FilterChip:
    return next(chip for chip in chips if chip.key == key)


def _two_tools() -> tuple[str, str]:
    tool_ids = load_dims()["tools"]["tool_id"].to_list()
    return tool_ids[0], tool_ids[1]


def test_scope_chips_always_render_even_at_their_defaults():
    """The default view is adults 18+ over the most recent five years -- not "everything" -- so
    the scope chips must be present and marked as defaults rather than hidden."""
    _fresh_state()
    chips = filters.active_filter_chips(resolve_labels=False)
    assert [c.key for c in chips] == list(_SCOPE_KEYS)
    assert all(c.is_default for c in chips)
    assert all(c.count == 0 for c in chips)
    start, end = st.session_state["year_range"]
    assert _chip(chips, "year_range").value == f"{start}–{end}"
    assert _chip(chips, "age_scope").value == "Adults 18+"
    assert _chip(chips, "basis").value == "Annual average"


def test_changed_scope_chip_is_not_marked_default():
    _fresh_state()
    st.session_state["age_scope"] = "all"
    st.session_state["basis"] = "period_total"
    chips = filters.active_filter_chips(resolve_labels=False)
    assert _chip(chips, "age_scope").value == "All ages"
    assert _chip(chips, "age_scope").is_default is False
    assert _chip(chips, "basis").value == "Period total"
    assert _chip(chips, "basis").is_default is False


def test_dimension_chip_shows_resolved_labels_not_ids():
    _fresh_state()
    tools = load_dims()["tools"]
    tool_id = tools["tool_id"].to_list()[0]
    tool_label = tools["label"].to_list()[0]
    st.session_state["global_tool_filter"] = (tool_id,)
    chip = _chip(filters.active_filter_chips(), "global_tool_filter")
    assert chip.value == tool_label
    assert chip.count == 1
    assert chip.label == "Tool"


def test_dimension_chip_truncates_long_selections():
    _fresh_state()
    tool_ids = load_dims()["tools"]["tool_id"].to_list()[:5]
    assert len(tool_ids) == 5
    st.session_state["global_tool_filter"] = tuple(tool_ids)
    chip = _chip(filters.active_filter_chips(), "global_tool_filter")
    assert chip.value.endswith("+2 more")
    assert chip.count == 5


def test_tools_override_describes_the_page_owned_selection():
    """Tool Profiles/Comparison hide the global Tool widget and query with their own selection;
    the context row must describe what the page actually queried, flagged as page-owned."""
    _fresh_state()
    first, second = _two_tools()
    st.session_state["global_tool_filter"] = (first,)
    chip = _chip(filters.active_filter_chips(tools_override=(second,)), "global_tool_filter")
    assert chip.label == "Tool (this page)"
    labels = dict(
        zip(
            load_dims()["tools"]["tool_id"].to_list(),
            load_dims()["tools"]["label"].to_list(),
            strict=True,
        )
    )
    assert chip.value == labels[second]
    # An empty page-owned selection means "no tool filter", not the stale global one.
    assert not [c for c in filters.active_filter_chips(tools_override=()) if c.count]


def test_active_dimension_count_ignores_scope_chips():
    _fresh_state()
    assert filters.active_dimension_count() == 0
    st.session_state["f_sexes"] = (1,)
    st.session_state["f_body_regions"] = ("hand",)
    assert filters.active_dimension_count() == 2


def test_filter_summary_contract_is_unchanged():
    """`filter_summary()` is reused (not replaced) by the chip model: page empty states must keep
    reading exactly as before, and it must not need a dim lookup to build."""
    _fresh_state()
    start, end = st.session_state["year_range"]
    assert filters.filter_summary() == f"years {start}–{end}, Adults 18+, annual average"
    st.session_state["f_families"] = ("saws",)
    st.session_state["global_tool_filter"] = _two_tools()
    summary = filters.filter_summary()
    assert "1 family filter(s)" in summary
    assert "2 tool filter(s)" in summary


def test_filter_context_html_is_escaped_and_single_line():
    chip = FilterChip(
        key="f_families",
        label="<b>Tool family</b>",
        value='"><script>alert(1)</script>',
        summary_text="1 family filter(s)",
        is_default=False,
        count=1,
    )
    html_out = filter_context.filter_context_html([chip])
    assert "<script>" not in html_out
    assert "&lt;script&gt;" in html_out
    assert "\n" not in html_out
    assert html_out.startswith('<div class="wii-body-chips">')


def test_no_advanced_filters_is_stated_explicitly():
    _fresh_state()
    html_out = filter_context.filter_context_html(filters.active_filter_chips(resolve_labels=False))
    assert filter_context.NO_FILTERS_LABEL in html_out
    assert filter_context.NO_FILTERS_VALUE in html_out
    assert f"({filter_context.DEFAULT_MARKER})" in html_out


def test_no_filters_chip_disappears_once_a_filter_is_applied():
    _fresh_state()
    st.session_state["f_sexes"] = (1,)
    html_out = filter_context.filter_context_html(filters.active_filter_chips())
    assert filter_context.NO_FILTERS_LABEL not in html_out


def test_family_and_tool_conflict_is_surfaced_not_silently_dropped():
    """Families and tools are ANDed in `CaseFilter.to_sql`, so a tool outside the selected family
    nearly empties the view. The user is told; the selection is left exactly as they set it."""
    _fresh_state()
    tools = load_dims()["tools"]
    tool_ids = tools["tool_id"].to_list()
    family_ids = tools["family_id"].to_list()
    by_family = dict(zip(tool_ids, family_ids, strict=True))
    tool_id = tool_ids[0]
    other_family = next(f for f in set(family_ids) if f != by_family[tool_id])

    st.session_state["f_families"] = (other_family,)
    st.session_state["global_tool_filter"] = (tool_id,)
    conflicts = filters.filter_conflicts()
    assert len(conflicts) == 1
    assert "combined with AND" in conflicts[0]
    # Nothing was rewritten behind the user's back.
    assert st.session_state["global_tool_filter"] == (tool_id,)

    st.session_state["f_families"] = (by_family[tool_id],)
    assert filters.filter_conflicts() == ()


def test_reset_clears_dimension_filters_and_their_widget_twins():
    _fresh_state()
    st.session_state["global_tool_filter"] = _two_tools()
    st.session_state["widget_global_tool_filter"] = list(_two_tools())
    st.session_state["f_sexes"] = (1,)
    st.session_state["age_scope"] = "all"

    filters.reset_filters()
    assert st.session_state["global_tool_filter"] == ()
    assert st.session_state["widget_global_tool_filter"] == []
    assert st.session_state["f_sexes"] == ()
    # Scope is untouched by default -- the user asked to clear advanced filters, nothing else.
    assert st.session_state["age_scope"] == "all"

    filters.reset_filters(include_scope=True)
    assert st.session_state["age_scope"] == "adults"
    assert st.session_state["basis"] == "annual_average"


def _context_probe():
    import streamlit as st

    from wii.app import filters
    from wii.app.components import filter_context

    filters.ensure_defaults()
    if st.session_state.get("_test_select_tool"):
        st.session_state["global_tool_filter"] = (st.session_state["_test_select_tool"],)
    filter_context.render_filter_context()


def test_render_filter_context_runs_and_shows_context_with_data_present():
    """The gap this closes: filter context was only ever rendered inside an empty state. Here it
    renders with no error and no empty state in sight."""
    at = AppTest.from_function(_context_probe)
    at.run(timeout=30)
    assert not at.exception
    body = " ".join(m.value for m in at.markdown)
    assert filter_context.SHOWING_LABEL in body
    assert "wii-body-chips" in body
    assert filter_context.NO_FILTERS_LABEL in body
    assert any(filter_context.SCOPE_NOTE in c.value for c in at.caption)
    assert not at.button  # nothing selected -> no reset affordance


def test_render_filter_context_offers_reset_when_a_filter_is_active():
    tool_id = load_dims()["tools"]["tool_id"].to_list()[0]
    at = AppTest.from_function(_context_probe)
    at.session_state["_test_select_tool"] = tool_id
    at.run(timeout=30)
    assert not at.exception
    assert [b for b in at.button if b.label == filter_context.RESET_LABEL]
    at.button[0].click().run(timeout=30)
    assert not at.exception


def _sidebar_probe():
    from wii.app import filters

    filters.render_sidebar(hide_tool=False)


def test_tool_options_do_not_cascade_from_the_family_selection():
    """Deliberate: narrowing Tool by the selected families could drop a tool the user already
    chose (and that the persistent key is still filtering by on pages that hide the widget).
    See `filters.OPTIONS_DO_NOT_CASCADE`."""
    all_tool_labels = sorted(load_dims()["tools"]["label"].to_list())
    at = AppTest.from_function(_sidebar_probe)
    at.run(timeout=30)
    assert not at.exception
    tool_widget = next(m for m in at.sidebar.multiselect if m.key == "widget_global_tool_filter")
    assert sorted(tool_widget.options) == all_tool_labels

    family_widget = next(m for m in at.sidebar.multiselect if m.key == "widget_f_families")
    family_widget.select(family_widget.options[0])
    at.run(timeout=30)
    assert not at.exception
    tool_widget = next(m for m in at.sidebar.multiselect if m.key == "widget_global_tool_filter")
    assert sorted(tool_widget.options) == all_tool_labels
    assert "OPTIONS_DO_NOT_CASCADE" in dir(filters)


# ---------------------------------------------------------------------------
# Title block
# ---------------------------------------------------------------------------


def test_title_block_states_what_is_drawn_the_basis_the_scope_and_the_vintage():
    """A drawing opens with a title block, and those four fields are exactly what a reader needs
    before any number on the sheet can be read correctly. It replaces the scattered per-page
    filter captions: it is information, not chrome."""
    out = filter_context.title_block_html(
        "Workshop tool injury burden and severity",
        "Annual average",
        "2021–2025, Adults 18+",
        filter_context.source_field(2025),
    )
    assert "\n" not in out
    assert out.startswith('<div class="wii-titleblock">') and out.endswith("</div>")
    for key in ("DRAWING", "BASIS", "SCOPE", "SOURCE"):
        assert f'<span class="wii-tb-key">{key}</span>' in out
    assert "Workshop tool injury burden and severity" in out
    assert "Annual average" in out
    assert "2021–2025, Adults 18+" in out


def test_title_block_escapes_every_interpolated_value():
    out = filter_context.title_block_html("<script>x</script>", "b", "c", "d")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_source_field_reads_the_year_and_never_guesses_one():
    """Freshness is computed from the processed dataset's own metadata, never hard-coded and
    never taken from the wall clock: a product about a surveillance dataset with a reporting lag
    has to be able to say how stale it is -- and to say when it does not know."""
    assert filter_context.source_field(2025) == "CPSC NEISS · through 2025"
    assert filter_context.source_field(None).endswith("year unknown")
    assert "20" not in filter_context.source_field(None).replace("CPSC NEISS · through ", "")


def test_title_block_scope_folds_in_a_page_fact_without_taking_a_kpi_slot(monkeypatch):
    """ "31 tools with reportable data" is scope metadata, not a metric: a count of sampling
    coverage with no standard error and therefore no interval to compare against. It belongs in
    the title block, which is why the Overview strip is four cards."""
    captured: list[str] = []
    monkeypatch.setattr(
        "wii.app.components.filter_context.st.markdown",
        lambda body, **_kwargs: captured.append(body),
    )
    chips = (
        FilterChip(
            key="year_range", label="Years", value="2021–2025", summary_text="", is_default=True
        ),
        FilterChip(
            key="age_scope",
            label="Population",
            value="Adults 18+",
            summary_text="",
            is_default=True,
        ),
        FilterChip(
            key="basis", label="Basis", value="Annual average", summary_text="", is_default=True
        ),
    )
    filter_context.render_title_block(
        "Drawing", chips=chips, scope_extra="31 tools with reportable data", latest_year=2025
    )
    assert len(captured) == 1
    assert "31 tools with reportable data" in captured[0]
    assert "Annual average" in captured[0]
    assert "2021" in captured[0]
