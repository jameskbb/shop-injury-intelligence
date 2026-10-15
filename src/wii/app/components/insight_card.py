"""Insight card renderer. Every interpolated string (`Insight.text`, each of `Insight.numbers`'
labels, the raw-N caption) is `html.escape`d before reaching markup -- an `Insight.text` is built
from data-derived tool labels, which ultimately come from `dim_tool.label`, so this is one of the
two call sites that must never trust a label as safe HTML (the other is `kpi_card.py`).

Cards are rule-separated blocks, not boxes: `theme.py` draws `.wii-insight-card` with no
background, border-radius or shadow.
"""

from __future__ import annotations

import html

import streamlit as st

from wii.analytics.insights import Insight
from wii.app.charts import marks

EMPTY_STATE_COPY = (
    "Not enough stable, high-effect-size patterns in the current filter selection to generate "
    "insight statements. Try widening the year range or switching to All ages."
)


def _under_min_note(n_passing: int, min_cards_for_section: int) -> str:
    return (
        f"Only {n_passing} pattern(s) cleared the stability/effect-size thresholds for the "
        f"current filter selection (fewer than {min_cards_for_section}) -- shown below without "
        "padding with weaker statements."
    )


#: Said ONCE per section instead of two or three times per card.
#:
#: "(not a national count)" appeared inside each card's sentence AND again on its footer line AND
#: in every chart tooltip. After the first reading it stops being a disclosure and becomes noise a
#: reader learns to skip -- which is the opposite of what an honesty affordance is for. The
#: qualifier is not dropped: it moves to the section caption, where it is unmissable and reads
#: once. Quiet is not hidden.
SAMPLE_CASE_NOTE = (
    "n is the count of unweighted NEISS sample cases behind a finding, not a national count."
)


def render_insight_section(insights: list[Insight], min_cards_for_section: int) -> None:
    """Renders "What the data is saying" (Overview) / "What happens with this tool?" (Tool
    Profiles, 03-03 reuse): the insight cards that passed thresholds (<= max_cards, already
    ranked by `generate_insights`), a one-line note when fewer than `min_cards_for_section` pass,
    or the UI-SPEC empty-state copy when zero pass. Never pads with threshold-failing statements.
    """
    if not insights:
        st.info(EMPTY_STATE_COPY)
        return
    if len(insights) < min_cards_for_section:
        st.caption(_under_min_note(len(insights), min_cards_for_section))
    _render_cards(insights)
    st.caption(SAMPLE_CASE_NOTE)


def format_number(value: float) -> str:
    """One formatting layer for every figure a card prints, so "78.4%" and "30,900" look the same
    here, in a KPI, in a tooltip and on an axis.

    `Insight.numbers` mixes SHARE values (always in [0, 1]) with raw ESTIMATE values (always at or
    above the stability floor of 1,200), so `abs(value) <= 1.0` separates the two without
    threading an "is_share" flag through every `Insight` -- no legitimate estimate can land in
    [0, 1].
    """
    return marks.fmt_share(value) if abs(value) <= 1.0 else marks.fmt_estimate(value)


def _render_cards(insights: list[Insight]) -> None:
    parts = ['<div class="wii-insight-grid">']
    for insight in insights:
        text = html.escape(insight.text)
        numbers_html = "".join(
            f'<span class="wii-insight-number">{html.escape(label)}: '
            f'<span class="wii-insight-number-value">{html.escape(format_number(value))}'
            "</span></span>"
            for label, value in insight.numbers
        )
        raw_n_caption = html.escape(f"n = {insight.raw_n:,} sample cases")
        parts.append(
            '<div class="wii-insight-card">'
            f'<div class="wii-insight-text">{text}</div>'
            f'<div class="wii-insight-numbers">{numbers_html}</div>'
            f'<div class="wii-insight-raw-n">{raw_n_caption}</div>'
            "</div>"
        )
    parts.append("</div>")
    st.markdown("".join(parts), unsafe_allow_html=True)
