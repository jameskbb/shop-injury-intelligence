"""Persistent active-filter context (reference template: "Show active filter context visibly").

Until now `filters.filter_summary()` was only ever reached from an EMPTY state -- so the one
moment a user could read what they were filtered to was the moment there was nothing to look at.
This component renders the same `FilterChip` model at the top of a page whether or not the view
has data, which is the only way a number on screen can be read correctly.

Two honesty rules drive the shape:

- The three scope chips (year range, population, basis) always render, marked `(default)` when
  they are still at their defaults. The default view is NOT "all of NEISS" -- it is adults 18+
  over the most recent five years -- so a bare "no filters applied" would be false.
- When no advanced filter is set, that is stated explicitly (`NO_FILTERS_LABEL`) rather than left
  as blank space the user has to interpret.

Presentation only (D-05..D-08): every value arrives already resolved from `wii.app.filters`, no
query is issued here, and no suppression/stability decision is made. Markup reuses the existing
`.wii-body-chips`/`.wii-body-chip` classes from `theme.py` (same escape-every-interpolated-string,
single-string, no-blank-line shape as `body_map.build_callout_chips_html`).

Wiring (left to each page's owner): call `render_filter_context()` immediately under the page
title, before the first KPI/chart section. Pages that own their own tool selector (Tool Profiles /
Comparison) pass the same `tools_override` they pass to `filters.current_filter(...)`, so the chip
row describes the filter the page actually queried with.
"""

from __future__ import annotations

import html
from collections.abc import Sequence

import streamlit as st

from wii.app import filters
from wii.app.filters import FilterChip

NO_FILTERS_LABEL = "No advanced filters"
NO_FILTERS_VALUE = "all tools, sexes, body regions and diagnoses"
SHOWING_LABEL = "Showing"
DEFAULT_MARKER = "default"
# "Most numbers", not "every number": a chart may legitimately opt out of a chip and say so
# in its own caption. The temporal heatmap does exactly that -- it always spans the full
# available year range, because the default 5-year window would leave a 5-column grid --
# and the KPI deltas compare two fixed post-2019 windows. Claiming "every number" here while
# those captions say otherwise would make the page contradict itself.
SCOPE_NOTE = (
    "These chips scope most numbers on this page; any chart that spans a different period "
    "says so in its own caption. The starting view is already scoped (adults 18+ over the "
    "most recent five years), not the full dataset."
)
RESET_LABEL = "Clear advanced filters"
RESET_HELP = (
    "Resets the advanced filters only; the year range, population and basis stay as they are."
)
_RESET_KEY = "filter_context_reset"

_LABEL_STYLE = "color: var(--wii-muted); font-size: var(--wii-text-label); margin-right: 4px;"


def _chip_html(chip: FilterChip) -> str:
    label = html.escape(chip.label)
    value = html.escape(chip.value)
    marker = (
        f'<span class="wii-body-chip-label">({html.escape(DEFAULT_MARKER)})</span>'
        if chip.is_default
        else ""
    )
    return (
        '<span class="wii-body-chip">'
        f'<span class="wii-body-chip-label">{label}</span>'
        f'<span class="wii-body-chip-value">{value}</span>'
        f"{marker}"
        "</span>"
    )


def _no_filters_chip_html() -> str:
    return (
        '<span class="wii-body-chip">'
        f'<span class="wii-body-chip-label">{html.escape(NO_FILTERS_LABEL)}</span>'
        f'<span class="wii-body-chip-value">{html.escape(NO_FILTERS_VALUE)}</span>'
        "</span>"
    )


def filter_context_html(chips: Sequence[FilterChip]) -> str:
    """Pure builder (no `st.*` call). Always returns a chip row -- there is no "nothing to show"
    case, because the scope chips apply even when no advanced filter is set, and the absence of
    advanced filters is itself stated (`NO_FILTERS_LABEL`)."""
    parts = [f'<span style="{_LABEL_STYLE}">{html.escape(SHOWING_LABEL)}</span>']
    parts.extend(_chip_html(chip) for chip in chips)
    if not any(chip.count for chip in chips):
        parts.append(_no_filters_chip_html())
    return f'<div class="wii-body-chips">{"".join(parts)}</div>'


# ---------------------------------------------------------------------------------------------
# Title block
# ---------------------------------------------------------------------------------------------
#
# A drawing opens with a title block: what is drawn, on what basis, over what scope, from which
# sheet. That is exactly the set of facts a reader needs before a single number on these pages can
# be read correctly, and it is why this replaces the scattered per-page filter captions -- it is
# information, not chrome, and it costs one strip of page height instead of four caption lines.
#
# It is also where the source/freshness line lives. The data year is read from the processed
# dataset's own metadata via `data.latest_data_year`, NEVER hard-coded and never derived from the
# wall clock: a product about a surveillance dataset with a reporting lag has to be able to say
# how stale it is.

SOURCE_PUBLISHER = "CPSC NEISS"
_TB_FIELD_KEYS = ("DRAWING", "BASIS", "SCOPE", "SOURCE")
_UNKNOWN_YEAR = "year unknown"


def _tb_field(key: str, value: str) -> str:
    return (
        '<div class="wii-tb-field">'
        f'<span class="wii-tb-key">{html.escape(key)}</span>'
        f'<span class="wii-tb-value" title="{html.escape(value)}">{html.escape(value)}</span>'
        "</div>"
    )


def title_block_html(drawing: str, basis: str, scope: str, source: str) -> str:
    """Pure builder (no `st.*` call). One line, no blank line anywhere, every value escaped."""
    fields = (drawing, basis, scope, source)
    body = "".join(_tb_field(k, v) for k, v in zip(_TB_FIELD_KEYS, fields, strict=True))
    return f'<div class="wii-titleblock">{body}</div>'


def source_field(latest_year: int | None) -> str:
    """ "CPSC NEISS · through 2025". `None` renders "year unknown" rather than a guessed year --
    the same rule `components.footer.source_line` follows."""
    through = str(latest_year) if latest_year is not None else _UNKNOWN_YEAR
    return f"{SOURCE_PUBLISHER} · through {through}"


def render_title_block(
    drawing: str,
    *,
    tools_override: Sequence[str] | None = None,
    scope_extra: str = "",
    chips: Sequence[FilterChip] | None = None,
    latest_year: int | None = None,
) -> None:
    """The one strip every analytical page opens with.

    `drawing` says what this sheet draws. Basis, scope and source are read from the live filter
    state, so the strip always describes the filter the page actually queried with --
    `tools_override` is the same argument `filters.current_filter` takes, for pages that own
    their own tool selector. `scope_extra` appends a page fact that belongs with the scope rather
    than in a KPI slot (Overview folds "31 tools with reportable data" in here).
    """
    from wii.app.data import latest_data_year  # local: keeps this module import-cheap for tests

    resolved = (
        tuple(chips)
        if chips is not None
        else filters.active_filter_chips(tools_override=tools_override)
    )
    by_key = {chip.key: chip for chip in resolved}
    basis_chip = by_key.get("basis")
    basis = basis_chip.value if basis_chip else "Annual average"
    scope_bits = [
        chip.value for chip in resolved if chip.key in {"year_range", "age_scope"} and chip.value
    ]
    scope_bits.extend(
        f"{chip.label}: {chip.value}"
        for chip in resolved
        if chip.key not in {"year_range", "age_scope", "basis"}
    )
    if scope_extra:
        scope_bits.append(scope_extra)
    year = latest_year if latest_year is not None else latest_data_year()
    st.markdown(
        title_block_html(drawing, basis, ", ".join(scope_bits), source_field(year)),
        unsafe_allow_html=True,
    )


def render_filter_context(
    chips: Sequence[FilterChip] | None = None,
    conflicts: Sequence[str] | None = None,
    *,
    tools_override: Sequence[str] | None = None,
    show_reset: bool = True,
    only_when_active: bool = False,
) -> None:
    """Render the chip row, any filter-combination warnings, and (only when something is actually
    selected) a reset button.

    `chips`/`conflicts` default to the live session state via `wii.app.filters`; passing them
    explicitly keeps the component testable and lets a page describe a view it built itself.

    `only_when_active` suppresses the whole block when nothing is filtered and nothing conflicts.
    A page that also renders a title block passes this: the title block already states basis, scope
    and source, so on a default view the chip row would restate the same three facts directly
    beneath it. With a filter actually applied the chips say something the title block does not --
    which is set, which is a default, and that a reset is available -- so it comes back.
    """
    resolved = (
        tuple(chips)
        if chips is not None
        else filters.active_filter_chips(tools_override=tools_override)
    )
    messages = (
        tuple(conflicts)
        if conflicts is not None
        else filters.filter_conflicts(tools_override=tools_override)
    )
    if only_when_active and not messages and not any(chip.count for chip in resolved):
        return
    st.markdown(filter_context_html(resolved), unsafe_allow_html=True)
    st.caption(SCOPE_NOTE)
    for message in messages:
        st.warning(message)
    if show_reset and any(chip.count for chip in resolved):
        st.button(RESET_LABEL, key=_RESET_KEY, help=RESET_HELP, on_click=filters.reset_filters)
