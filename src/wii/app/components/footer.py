"""D-08/APP-04 footer: the exact source-label copy, on every page, exactly once. This is the
single place `source_line()` is composed -- no page constructs its own copy of this string.
"""

from __future__ import annotations

import html

import streamlit as st

from wii.app.data import latest_data_year

_SOURCE_PREFIX = "Source: U.S. Consumer Product Safety Commission – NEISS · Data through"

_HOW_TO_READ = (
    "Weights turn sample cases into national estimates. NEISS has no exposure denominator, "
    'so these are not probabilities of injury. Patterns shown are "associated with" a tool, '
    "never caused by it."
)


def source_line(year: int | None) -> str:
    """Exact D-08/APP-04 copy (en dash U+2013, middle dot U+00B7). `year=None` renders "Data
    through: unknown" -- never a guessed, hard-coded, or wall-clock-derived year.
    """
    if year is None:
        return f"{_SOURCE_PREFIX}: unknown"
    return f"{_SOURCE_PREFIX} {year}"


def render_footer() -> None:
    """Fixed order (D-08): source line, then (only when the year is missing) a warning calling
    out the missing metadata, then the "How to read these numbers" expander. 64px spacing above,
    via the `.wii-footer` CSS class (theme.py).
    """
    year = latest_data_year()
    line = html.escape(source_line(year))
    st.markdown(f'<div class="wii-footer">{line}</div>', unsafe_allow_html=True)
    if year is None:
        st.warning(
            "The latest data year could not be determined from data_metadata.json -- "
            'showing "unknown" rather than a guessed or hard-coded year.'
        )
    with st.expander("How to read these numbers"):
        st.caption(_HOW_TO_READ)
