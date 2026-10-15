"""D-06 My Shop disclaimer: the brief's verbatim non-risk-calculator copy, rendered from ONE
string constant at two call sites in `pages/my_shop.py` (directly under the page title, and again
directly above the standard footer) -- the same "one verbatim string constant, one render
function, escape-then-markdown" shape as `components/footer.py`'s `source_line()`/
`render_footer()`.
"""

from __future__ import annotations

import html

import streamlit as st

DISCLAIMER_TEXT = (
    "These patterns describe U.S. emergency-department-treated consumer injuries associated "
    "with selected tools. They do not estimate your individual probability of injury and do "
    "not replace tool-specific manufacturer safety instructions."
)


def disclaimer_html(text: str = DISCLAIMER_TEXT) -> str:
    """One escaped, single-string, no-blank-line `<div>`."""
    return f'<div class="wii-disclaimer">{html.escape(text)}</div>'


def render_disclaimer() -> None:
    """The full verbatim statement. Rendered above the footer on every render path."""
    st.markdown(disclaimer_html(), unsafe_allow_html=True)


def render_disclaimer_line(text: str) -> None:
    """A one-line form of the same statement, for the top of the page.

    The full 40-word block appeared twice on My Shop and the first copy pushed the KPI strip
    below the fold on a phone: the reader met the caveat before meeting anything it qualified.
    The short line keeps the caveat in front of the numbers -- it is never omitted -- and points
    at the full text, which still renders in full at the foot of every path.
    """
    st.markdown(disclaimer_html(text), unsafe_allow_html=True)
