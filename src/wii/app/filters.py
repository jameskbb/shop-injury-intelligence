"""D-03/APP-02 global sidebar filters. Values are persisted under stable, NON-widget
`session_state` keys; every widget uses a separate `widget_*` key seeded from the persistent key
and copied back via `on_change`. This is required, not stylistic: Wave-0 spike A5 (03-01-SUMMARY.md
"Wave-0 spike results", finding (e)) confirmed Streamlit deletes a widget's own key from
`session_state` the moment that widget is not rendered on a rerun -- reading a widget key directly
as the source of truth would silently lose the filter's value the moment a page hides it (Tool
Profiles/Comparison hide the global tool filter, Pitfall 4).

The advanced-filter multiselects deliberately do NOT cascade: every one of them is populated from
the FULL dim table, so choosing a tool family never narrows the Tool list. That is a correctness
decision, not an oversight -- see `OPTIONS_DO_NOT_CASCADE` below for why cascading would break the
persistent-key contract this module exists to protect, and `filter_conflicts()` for the honest
alternative (say that two filters disagree; never silently drop one of them).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import streamlit as st

from wii.app.data import available_years, load_dims
from wii.metrics import CaseFilter

_PERSIST_DEFAULTS: dict[str, object] = {
    "age_scope": "adults",
    "basis": "annual_average",
    "f_families": (),
    "global_tool_filter": (),
    "f_sexes": (),
    "f_body_regions": (),
    "f_diagnosis_categories": (),
}

OPTIONS_DO_NOT_CASCADE = """Advanced-filter options are never narrowed by the other filters.

A cascading option list (Tool showing only the selected families' tools, Body region showing only
regions seen under the current selection, ...) would have to remove options that a user may
already have selected. Under this module's persistent-key/`widget_*`-twin contract that is not a
cosmetic difference -- it silently corrupts state in two distinct ways:

1. `st.multiselect` refuses a `session_state` value that is not in `options`. The widget twin is
   re-seeded from the persistent key on every rerun where the widget was hidden (Pitfall 4), so a
   narrowed `options` list turns a previously valid saved selection into a hard error or, worse,
   a silently truncated one that `on_change` then writes BACK over the persistent key -- the
   user's selection is gone with no message.
2. The persistent key survives pages that hide a widget entirely. A tool selected on Overview
   stays active while the user is on Tool Profiles (which hides the global Tool multiselect). If
   the Tool options were narrowed by a family chosen later, whether the tool survived would depend
   on which page happened to re-render the widget -- state that differs by navigation path.

The safe version of cascading, if it is ever wanted, is additive rather than subtractive: keep the
full option list, union the currently-selected values into it unconditionally, and only ever
re-ORDER or group options (in-scope first) -- plus surface a visible notice when a selection sits
outside the rest of the filter set. `filter_conflicts()` implements that notice today, so the user
learns that two filters disagree instead of having one of them quietly deleted."""


@dataclass(frozen=True)
class _DimensionSpec:
    """The one place a sidebar dimension's persistent key, its dim table, and its label column are
    mapped to each other -- used by `render_sidebar` (options) and by the filter-context chips
    (display labels) so the two can never drift apart."""

    persist_key: str
    chip_label: str
    summary_noun: str
    dim_table: str
    id_column: str
    label_column: str


_DIMENSION_SPECS: tuple[_DimensionSpec, ...] = (
    _DimensionSpec("f_families", "Tool family", "family", "families", "family_id", "label"),
    _DimensionSpec("global_tool_filter", "Tool", "tool", "tools", "tool_id", "label"),
    _DimensionSpec("f_sexes", "Sex", "sex", "sexes", "code", "label"),
    _DimensionSpec(
        "f_body_regions", "Body region", "body region", "body_regions", "region_id", "label"
    ),
    _DimensionSpec(
        "f_diagnosis_categories",
        "Diagnosis category",
        "diagnosis category",
        "diagnosis_categories",
        "injury_category",
        "injury_category",
    ),
)

_TOOL_KEY = "global_tool_filter"
_MAX_CHIP_VALUES = 3


@dataclass(frozen=True)
class FilterChip:
    """One piece of active-filter context, ready to render.

    `summary_text` is the legacy `filter_summary()` fragment (kept exactly as it was so page empty
    states read unchanged); `value` is the display string for the persistent chip row. `count` is
    0 for the always-on scope chips (year range / age scope / basis) and the number of selected
    values for a dimension chip -- a dimension chip only exists when something is selected.
    """

    key: str
    label: str
    value: str
    summary_text: str
    is_default: bool
    count: int = 0


def _default_year_range() -> tuple[int, int]:
    years = available_years()
    if not years:
        return (0, 0)
    return (max(years[0], years[-1] - 4), years[-1])


def ensure_defaults() -> None:
    """Seed every persisted filter key with `setdefault` -- never overwrites a value a widget's
    `on_change` already wrote this session. `year_range` is computed from `available_years()`,
    never hard-coded (D-03).
    """
    st.session_state.setdefault("year_range", _default_year_range())
    for key, value in _PERSIST_DEFAULTS.items():
        st.session_state.setdefault(key, value)


def _sync(widget_key: str, persist_key: str) -> None:
    st.session_state[persist_key] = st.session_state[widget_key]


def _sync_tuple(widget_key: str, persist_key: str) -> None:
    st.session_state[persist_key] = tuple(st.session_state[widget_key])


def _multiselect(label: str, persist_key: str, options: list, labels: dict | None) -> None:
    widget_key = f"widget_{persist_key}"
    if widget_key not in st.session_state:
        st.session_state[widget_key] = list(st.session_state.get(persist_key, ()))
    fmt = (lambda v: labels.get(v, v)) if labels else (lambda v: v)
    st.multiselect(
        label,
        options=options,
        key=widget_key,
        format_func=fmt,
        on_change=_sync_tuple,
        args=(widget_key, persist_key),
    )


def _dimension_options(spec: _DimensionSpec) -> tuple[list, dict | None]:
    """The FULL option list for `spec`, never narrowed by the other filters
    (`OPTIONS_DO_NOT_CASCADE`). Returns `(options, labels)`; `labels` is `None` when the id column
    is already the display string."""
    table = load_dims()[spec.dim_table]
    options = table[spec.id_column].to_list()
    if spec.id_column == spec.label_column:
        return options, None
    labels = dict(zip(options, table[spec.label_column].to_list(), strict=True))
    return options, labels


BASIS_HELP = (
    "Annual average divides the selected period's estimated total by the number of years in it. "
    "Period total is the whole selected period at once. Both are estimated U.S. ER-treated "
    "injuries; only the unit differs."
)
YEARS_HELP = (
    "The treatment years every estimate is computed over. A few charts deliberately span a "
    "different period and say so in their own caption."
)
AGE_SCOPE_HELP = (
    "The starting view is adults 18+, not all of NEISS. Switching to All ages adds paediatric "
    "cases, which change both the burden and the injury mix."
)


def render_sidebar(hide_tool: bool) -> None:
    """Renders once, in `app.py`, before `nav.run()`. `hide_tool=True` omits the global tool
    multiselect -- the caller (Tool Profiles/Comparison, later plans) owns its own tool selector
    instead (D-03).
    """
    ensure_defaults()
    years = available_years()
    min_year, max_year = (years[0], years[-1]) if years else (0, 0)

    # Basis comes FIRST. It changes the unit of nearly every burden number in the app -- 148,300 a
    # year or 741,700 over five -- yet it shipped third, as a plain radio styled identically to
    # Age scope, with nothing saying what it did.
    if "widget_basis" not in st.session_state:
        st.session_state["widget_basis"] = st.session_state["basis"]
    st.sidebar.radio(
        "Estimate basis",
        options=["annual_average", "period_total"],
        format_func=lambda v: "Annual average" if v == "annual_average" else "Period total",
        key="widget_basis",
        on_change=_sync,
        args=("widget_basis", "basis"),
        help=BASIS_HELP,
    )

    if "widget_year_range" not in st.session_state:
        st.session_state["widget_year_range"] = st.session_state["year_range"]
    st.sidebar.slider(
        "Year range",
        min_value=min_year,
        max_value=max_year,
        key="widget_year_range",
        on_change=_sync,
        args=("widget_year_range", "year_range"),
        help=YEARS_HELP,
    )

    # No "Default population: Adults 18+" caption under this: the radio already shows the
    # selection, and the fact that the DEFAULT view is scoped (not all of NEISS) is stated where
    # it is actually needed -- on the page, in the title block and the filter chips.
    if "widget_age_scope" not in st.session_state:
        st.session_state["widget_age_scope"] = st.session_state["age_scope"]
    st.sidebar.radio(
        "Age scope",
        options=["adults", "all"],
        format_func=lambda v: "Adults 18+" if v == "adults" else "All ages",
        key="widget_age_scope",
        on_change=_sync,
        args=("widget_age_scope", "age_scope"),
        help=AGE_SCOPE_HELP,
    )

    with st.sidebar.expander("Advanced filters", expanded=False):
        for spec in _DIMENSION_SPECS:
            if hide_tool and spec.persist_key == _TOOL_KEY:
                continue
            options, labels = _dimension_options(spec)
            _multiselect(spec.chip_label, spec.persist_key, options, labels)


def default_case_filter() -> CaseFilter:
    """The `CaseFilter` equivalent to "no active sidebar filter" -- used to decide whether a page
    may read the committed agg-table default view or must fall through to the query path (D-02).
    """
    return CaseFilter(years=_default_year_range(), age_scope="adults")


def current_filter(tools_override: tuple[str, ...] | None = None) -> CaseFilter:
    """Builds the frozen, hashable `CaseFilter` the data layer expects -- every multi-value field
    is a tuple (never a list). `tools_override` lets a page substitute its OWN tool selection
    (`profile_selected_tool`/`comparison_selected_tools`) without touching the global sidebar's
    `global_tool_filter` key (Pitfall 4).
    """
    year_range = st.session_state.get("year_range")
    tools = (
        tuple(tools_override)
        if tools_override is not None
        else tuple(st.session_state.get("global_tool_filter", ()))
    )
    return CaseFilter(
        years=tuple(year_range) if year_range else None,
        age_scope=st.session_state.get("age_scope", "adults"),
        families=tuple(st.session_state.get("f_families", ())),
        tools=tools,
        sexes=tuple(st.session_state.get("f_sexes", ())),
        body_regions=tuple(st.session_state.get("f_body_regions", ())),
        diagnosis_categories=tuple(st.session_state.get("f_diagnosis_categories", ())),
    )


def _selected_values(spec: _DimensionSpec, tools_override: Sequence[str] | None) -> tuple:
    """The values currently in force for `spec`. A page that owns its own tool selector passes
    `tools_override` (the same argument `current_filter` takes), so the displayed context matches
    the filter the page actually queried with -- never the hidden global tool key."""
    if spec.persist_key == _TOOL_KEY and tools_override is not None:
        return tuple(tools_override)
    return tuple(st.session_state.get(spec.persist_key, ()))


def _format_values(values: Sequence, labels: dict | None) -> str:
    shown = [str(labels.get(v, v)) if labels else str(v) for v in values[:_MAX_CHIP_VALUES]]
    text = ", ".join(shown)
    remaining = len(values) - len(shown)
    return f"{text} +{remaining} more" if remaining > 0 else text


def _scope_chips() -> list[FilterChip]:
    """Year range, age scope and basis ALWAYS render, including at their defaults: the default
    view is not "everything" (it is adults 18+ over the most recent five years), so hiding these
    at default would misrepresent what is on screen."""
    chips: list[FilterChip] = []
    year_range = st.session_state.get("year_range")
    if year_range:
        start, end = year_range[0], year_range[1]
        chips.append(
            FilterChip(
                key="year_range",
                label="Years",
                value=f"{start}–{end}",
                summary_text=f"years {start}–{end}",
                is_default=tuple(year_range) == _default_year_range(),
            )
        )
    age_scope = st.session_state.get("age_scope", "adults")
    age_value = "All ages" if age_scope == "all" else "Adults 18+"
    chips.append(
        FilterChip(
            key="age_scope",
            label="Population",
            value=age_value,
            summary_text=age_value,
            is_default=age_scope == _PERSIST_DEFAULTS["age_scope"],
        )
    )
    basis = st.session_state.get("basis", "annual_average")
    basis_summary = "annual average" if basis == "annual_average" else "period total"
    chips.append(
        FilterChip(
            key="basis",
            label="Basis",
            value=basis_summary.capitalize(),
            summary_text=basis_summary,
            is_default=basis == _PERSIST_DEFAULTS["basis"],
        )
    )
    return chips


def active_filter_chips(
    *, tools_override: Sequence[str] | None = None, resolve_labels: bool = True
) -> tuple[FilterChip, ...]:
    """Every chip needed to describe the CURRENT view: the three always-on scope chips followed by
    one chip per non-empty advanced filter, in sidebar order.

    `resolve_labels=False` skips `load_dims()` entirely (no DuckDB access) and leaves dimension
    chips with a bare count -- that is the mode `filter_summary()` uses, so an empty-state message
    never triggers a query of its own.
    """
    chips = _scope_chips()
    for spec in _DIMENSION_SPECS:
        values = _selected_values(spec, tools_override)
        if not values:
            continue
        labels = None
        if resolve_labels:
            _, labels = _dimension_options(spec)
        is_page_owned = spec.persist_key == _TOOL_KEY and tools_override is not None
        chips.append(
            FilterChip(
                key=spec.persist_key,
                label=f"{spec.chip_label} (this page)" if is_page_owned else spec.chip_label,
                value=_format_values(values, labels) if resolve_labels else str(len(values)),
                summary_text=f"{len(values)} {spec.summary_noun} filter(s)",
                is_default=False,
                count=len(values),
            )
        )
    return tuple(chips)


def active_dimension_count(*, tools_override: Sequence[str] | None = None) -> int:
    """How many advanced filters currently hold a selection. Zero does NOT mean "unfiltered" --
    the scope chips still apply."""
    return sum(1 for spec in _DIMENSION_SPECS if _selected_values(spec, tools_override))


def filter_summary(tools_override: Sequence[str] | None = None) -> str:
    """Short human-readable string of the active filters, used in empty-state messaging. Built
    from the same `FilterChip` model the persistent filter-context row renders, so the two can
    never disagree about what is active."""
    chips = active_filter_chips(tools_override=tools_override, resolve_labels=False)
    return ", ".join(chip.summary_text for chip in chips)


def filter_conflicts(*, tools_override: Sequence[str] | None = None) -> tuple[str, ...]:
    """Messages for filter combinations that are legal but will look broken to a user -- the
    honest substitute for cascading option lists (`OPTIONS_DO_NOT_CASCADE`). Nothing here removes
    or rewrites a selection; it only says what the combination means.

    Today there is one such combination: `families` and `tools` compile to two separate `EXISTS`
    clauses over `bridge_case_products` (`CaseFilter.to_sql`), i.e. they are ANDed. Selecting a
    tool outside the selected families therefore does not return "that tool" -- it returns only
    cases that involve BOTH the selected tool AND some tool from the selected families, which for
    most tools is a near-empty set.
    """
    families = tuple(st.session_state.get("f_families", ()))
    tool_spec = next(spec for spec in _DIMENSION_SPECS if spec.persist_key == _TOOL_KEY)
    tools = _selected_values(tool_spec, tools_override)
    if not families or not tools:
        return ()
    dims = load_dims()
    tool_table = dims["tools"]
    family_of = dict(
        zip(tool_table["tool_id"].to_list(), tool_table["family_id"].to_list(), strict=True)
    )
    tool_labels = dict(
        zip(tool_table["tool_id"].to_list(), tool_table["label"].to_list(), strict=True)
    )
    outside = [t for t in tools if family_of.get(t) not in families]
    if not outside:
        return ()
    named = ", ".join(str(tool_labels.get(t, t)) for t in outside[:_MAX_CHIP_VALUES])
    remaining = len(outside) - min(len(outside), _MAX_CHIP_VALUES)
    if remaining > 0:
        named = f"{named} +{remaining} more"
    return (
        f"Tool family and Tool are combined with AND, and {named} "
        f"{'is' if len(outside) == 1 else 'are'} outside the selected tool "
        f"{'family' if len(families) == 1 else 'families'}. Only cases involving both survive, "
        "so this view will look almost empty. Clear one of the two filters to widen it.",
    )


def reset_filters(include_scope: bool = False) -> None:
    """Clear the advanced filters (and, with `include_scope=True`, the year range / age scope /
    basis) back to their defaults.

    Both halves of the twin-key pair are written: the persistent key is the source of truth, and
    the `widget_*` twin is reset in step so a widget that IS currently mounted picks the change up
    instead of writing its stale value straight back through `on_change`. Safe to use as an
    `on_click` callback -- callbacks run before the script re-executes, so no widget has been
    instantiated yet on that run.
    """
    keys: list[str] = [spec.persist_key for spec in _DIMENSION_SPECS]
    for key in keys:
        st.session_state[key] = ()
        widget_key = f"widget_{key}"
        if widget_key in st.session_state:
            st.session_state[widget_key] = []
    if include_scope:
        st.session_state["year_range"] = _default_year_range()
        st.session_state["age_scope"] = _PERSIST_DEFAULTS["age_scope"]
        st.session_state["basis"] = _PERSIST_DEFAULTS["basis"]
        for key in ("year_range", "age_scope", "basis"):
            widget_key = f"widget_{key}"
            if widget_key in st.session_state:
                st.session_state[widget_key] = st.session_state[key]
