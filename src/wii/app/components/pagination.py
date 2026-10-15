"""D-10/D-12 Case Explorer pagination: pure page-window math (`page_window`) plus the
Previous/Next button pair (`render_pagination`). Buttons are stateless by design (Pitfall 4) --
the page-number `session_state[persist_key]` is the only source of truth, mutated by each button's
own `on_click` callback.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import streamlit as st


@dataclass(frozen=True)
class PageWindow:
    page: int
    n_pages: int
    offset: int


def page_window(total: int, page: int, page_size: int) -> PageWindow:
    """Pure clamp math: `n_pages` is at least 1 (even for `total == 0`, so a zero-result page still
    reports "page 1 of 1"); `page` is clamped into `[1, n_pages]`; `offset` is computed from the
    CLAMPED page (never the raw, possibly out-of-range, input)."""
    n_pages = max(1, math.ceil(total / page_size)) if page_size > 0 else 1
    clamped_page = min(max(page, 1), n_pages)
    offset = (clamped_page - 1) * page_size
    return PageWindow(page=clamped_page, n_pages=n_pages, offset=offset)


def _decrement(persist_key: str) -> None:
    st.session_state[persist_key] = max(1, st.session_state.get(persist_key, 1) - 1)


def _increment(persist_key: str) -> None:
    st.session_state[persist_key] = st.session_state.get(persist_key, 1) + 1


def render_pagination(window: PageWindow, persist_key: str) -> None:
    """Renders nothing when there is only one page. Otherwise a Previous/indicator/Next row --
    Previous disabled on page 1, Next disabled on the last page, never wrapping (D-10/E7
    overflow)."""
    if window.n_pages <= 1:
        return
    col_prev, col_indicator, col_next = st.columns([1, 2, 1])
    with col_prev:
        st.button(
            "Previous",
            key="ce_prev",
            disabled=window.page <= 1,
            on_click=_decrement,
            args=(persist_key,),
        )
    with col_indicator:
        st.caption(f"Page {window.page} of {window.n_pages}")
    with col_next:
        st.button(
            "Next",
            key="ce_next",
            disabled=window.page >= window.n_pages,
            on_click=_increment,
            args=(persist_key,),
        )
