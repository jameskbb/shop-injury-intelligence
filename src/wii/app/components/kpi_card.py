"""KPI card component. Renders a responsive strip of compact, rule-separated measured values --
no boxes: `theme.py` draws `.wii-kpi-card` with no background, border-radius or shadow. Every
interpolated string is HTML-escaped before reaching markup -- this module never trusts a
label/value/caption string as safe HTML, even though today's callers are internal, deterministic
strings.

`KpiDelta` is the reusable period-over-period comparison indicator. NEISS publishes annually with
a lag, so there is no "vs last month" to show and single-year change is noise at this sample size;
the comparison the app makes is the data's latest 5-year window against the preceding 5-year
window (`wii.app.data.comparison_windows`), and a delta is only ever built by
`wii.app.data.gated_delta`, which refuses to emit one unless BOTH windows are stable and their
95% CIs do not overlap (the same discipline `wii.analytics.insights._trend_candidates` applies via
`trend_require_nonoverlapping_ci`). A card with no defensible comparison renders NO delta line at
all -- blank, never a "0%" or a greyed placeholder.

DIRECTION IS NOT VALENCE. The indicator is deliberately monochrome: up and down deltas render with
byte-identical styling (`_DELTA_STYLE`), and direction is carried by three colour-independent
channels -- an arrow glyph, an explicit +/- sign, and the `aria-label`'s "higher"/"lower" wording.
A red/green treatment would assert that estimated burden moving is good or bad news, i.e. that a
tool got safer or more dangerous; NEISS has no exposure denominator, so that claim cannot be made
(`wii.analytics.insights.GUARDRAIL_TERMS` bans exactly that vocabulary). A delta here describes a
change in ESTIMATED BURDEN or in a SHARE AMONG ER CASES, nothing more.

A rate is compared in PERCENTAGE POINTS, never as a percent-change of a percent: "escalated care
fell 7%" is unreadable when the rate itself is a percent, and it inflates a 0.4-point move on a
small base into a headline.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

import streamlit as st

from wii.app.charts import marks

#: Relative change in a count-like estimate, expressed as a percent of the prior window.
UNIT_PERCENT = "percent"
#: Absolute change in a share/rate, expressed in PERCENTAGE POINTS ("-3.2 pts"). A rate is never
#: compared as a percent-change of a percent -- "escalated care fell 7%" is unreadable when the
#: rate itself is a percent, and it inflates a 0.4 pt move on a small base into a headline.
#: `share_pp_min` in insights.yaml is the same convention.
UNIT_PP = "percentage_points"

DIRECTION_UP = "up"
DIRECTION_DOWN = "down"
DIRECTION_FLAT = "flat"

_DIRECTION_GLYPH = {DIRECTION_UP: "▲", DIRECTION_DOWN: "▼", DIRECTION_FLAT: "▬"}
_DIRECTION_WORD = {DIRECTION_UP: "higher", DIRECTION_DOWN: "lower", DIRECTION_FLAT: "unchanged"}
#: Derived from `charts.marks.fmt_points`, never written twice: one formatting layer letters a
#: points delta identically whether it lands on a card, a bar label or a table cell. `fmt_points`
#: takes a FRACTION while `KpiDelta.magnitude` is already in points, so the suffix is read off a
#: probe rather than the function being called directly.
#: (`tests/app/test_kpi_card.py` pins the two together.)
_POINTS_SUFFIX = marks.fmt_points(0.01).removeprefix("+1.0")
_UNIT_SUFFIX = {UNIT_PERCENT: "%", UNIT_PP: _POINTS_SUFFIX}
_UNIT_SPOKEN = {UNIT_PERCENT: "percent", UNIT_PP: "percentage points"}

# One style string for EVERY direction -- see the module docstring. Inline rather than a theme.py
# class because the delta line reuses existing caption tokens and adds no new palette entry.
_DELTA_STYLE = (
    "font-size: var(--wii-text-label); color: var(--wii-muted); "
    "font-weight: var(--wii-weight-semibold);"
)


@dataclass(frozen=True)
class KpiDelta:
    """One defensible period-over-period comparison. `magnitude` is SIGNED and already in the
    display unit: percent of the prior window for `UNIT_PERCENT`, percentage points for `UNIT_PP`.
    `comparison_label` names both periods (e.g. "2021-2025 vs 2016-2020") so the indicator is
    self-describing and can never be read against the wrong period.
    """

    magnitude: float
    unit: str
    comparison_label: str

    def __post_init__(self) -> None:
        if self.unit not in _UNIT_SUFFIX:
            raise ValueError(f"unsupported unit={self.unit!r}")

    @property
    def direction(self) -> str:
        if self.formatted_magnitude.startswith("+"):
            return DIRECTION_UP
        if self.formatted_magnitude.startswith("-"):
            return DIRECTION_DOWN
        return DIRECTION_FLAT

    @property
    def formatted_magnitude(self) -> str:
        """Signed, 1 decimal, unit-suffixed: "+12.4%" / "-4.6 pts". A value that rounds to zero
        at display precision loses its sign ("0.0 pts") rather than claiming a direction the
        displayed number does not show."""
        rendered = f"{self.magnitude:+.1f}"
        if float(rendered) == 0.0:
            rendered = f"{abs(self.magnitude):.1f}"
        return f"{rendered}{_UNIT_SUFFIX[self.unit]}"

    @property
    def text(self) -> str:
        """Visible text of the indicator, glyph excluded."""
        return f"{self.formatted_magnitude}, {self.comparison_label}"

    @property
    def aria_label(self) -> str:
        """Spoken form. Carries the direction as a WORD so the indicator never depends on the
        arrow glyph (or on colour, which it does not use at all) to be understood."""
        number = self.formatted_magnitude.lstrip("+-").removesuffix(_UNIT_SUFFIX[self.unit])
        unit = _UNIT_SPOKEN[self.unit]
        word = _DIRECTION_WORD[self.direction]
        if self.direction == DIRECTION_FLAT:
            return f"unchanged, {self.comparison_label}"
        return f"{number} {unit} {word}, {self.comparison_label}"


@dataclass(frozen=True)
class KpiCard:
    """One measured value on the sheet. Not a box -- `theme.py` draws `.wii-kpi-card` as a
    rule-separated cell with no background, border-radius or shadow.

    `severe=True` carries the `--wii-severe` accent on the card's LABEL, not on its digits: a
    coloured 34px number with no comparator reads as an alarm rather than as an outcome class,
    and the escalated-care share is the smallest figure on most strips.

    `dimension` prints a drafting dimension line under the value spanning what the value covers
    ("2021–2025"), which is what makes period-vs-annual-average explicit at the point of reading
    rather than only in the caption.

    `baseline` is the comparison context every KPI is required to carry when a defensible one
    exists but no gated delta does -- a workshop-wide figure for a within-tool share
    ("workshop 5.4%"), monochrome, in the same slot as the delta. It is a level, not a change, so
    it never takes a direction glyph.

    `tooltip` is the metric's own definition, surfaced on hover. It is supplementary only: the
    basis, the denominator and the stability label all stay in the VISIBLE caption, because a
    tooltip is not a place to keep an honesty affordance.

    `delta` defaults to None: a card without a defensible comparison emits no delta element at
    all -- never a "0%" and never a greyed placeholder.
    """

    label: str
    value: str
    caption: str = ""
    severe: bool = False
    delta: KpiDelta | None = None
    hero: bool = False
    dimension: str = ""
    baseline: str = ""
    tooltip: str = ""


def _delta_markup(delta: KpiDelta) -> str:
    glyph = html.escape(_DIRECTION_GLYPH[delta.direction])
    return (
        f'<div class="wii-kpi-delta" style="{_DELTA_STYLE}" '
        f'aria-label="{html.escape(delta.aria_label)}">'
        f'<span aria-hidden="true">{glyph}</span> {html.escape(delta.text)}'
        "</div>"
    )


def _baseline_markup(text: str) -> str:
    """A level to read the value against, in the delta slot. No glyph and no sign: it states
    where the comparator sits, it does not claim a movement."""
    return f'<div class="wii-kpi-delta" style="{_DELTA_STYLE}">{html.escape(text)}</div>'


def _dimension_markup(text: str) -> str:
    return (
        f'<div class="wii-dim"><span class="wii-dim-label">'
        f"{_keep_ranges_together(html.escape(text))}</span></div>"
    )


# A year range ("2021–2025") must never break at its en dash and strand "2025" on a line of its
# own; U+2060 WORD JOINER on both sides forbids the break and renders as nothing.
_RANGE_DASH = re.compile(r"(\d)–(\d)")


def _keep_ranges_together(text: str) -> str:
    return _RANGE_DASH.sub("\\1\u2060–\u2060\\2", text)


#: Widest a KPI row may get at desktop. Captions carry the estimate's honesty label ("share of
#: estimated U.S. ER-treated injuries, 2021-2025"), so cards narrower than a quarter of the content
#: column wrap that label onto 4-5 lines and the strip stops reading as a strip.
MAX_COLUMNS = 4


def _balanced(n_cards: int, narrowest: int, widest: int) -> int:
    """Column count in `narrowest..widest` leaving the fewest empty slots in the last row; ties go
    to the wider row. `n_cards <= widest` is simply one row."""
    if n_cards <= widest:
        return max(n_cards, 1)
    return min(range(narrowest, widest + 1), key=lambda c: ((-n_cards) % c, -c))


def strip_columns(n_cards: int) -> tuple[int, int]:
    """`(desktop, tablet)` column counts for `n_cards`, chosen so rows are full or nearly full: a
    row is never left holding one orphaned card beside empty slots when a balanced split exists
    (6 cards used to wrap 5 + 1; now 3 + 3). Desktop allows up to `MAX_COLUMNS`, tablet up to 3;
    phones always get 2 (theme CSS)."""
    return _balanced(n_cards, 3, MAX_COLUMNS), _balanced(n_cards, 2, 3)


def render_kpi_strip(cards: list[KpiCard]) -> None:
    """Render `cards` as one deliberate grid: the column count comes from `strip_columns`, so the
    composition is balanced at desktop, 3-wide at tablet and 2-wide on phones (D-15). No KPI is
    ever silently dropped from the strip -- a suppressed estimate is still a card, just with value
    "—" (D-07), so the strip's layout stays stable regardless of stability state.
    """
    desktop, tablet = strip_columns(len(cards))
    parts = [
        f'<div class="wii-kpi-strip" style="--wii-kpi-cols: {desktop}; '
        f'--wii-kpi-cols-md: {tablet};">'
    ]
    for card in cards:
        label = html.escape(card.label)
        value = html.escape(card.value)
        caption = _keep_ranges_together(html.escape(card.caption))
        # The severe accent rides the LABEL, never the digits -- see `KpiCard`.
        label_style = ' style="color: var(--wii-severe)"' if card.severe else ""
        card_class = "wii-kpi-card wii-kpi-hero" if card.hero else "wii-kpi-card"
        tooltip = f' title="{html.escape(card.tooltip)}"' if card.tooltip else ""
        comparison = ""
        if card.delta is not None:
            comparison = _delta_markup(card.delta)
        elif card.baseline:
            comparison = _baseline_markup(card.baseline)
        dimension = _dimension_markup(card.dimension) if card.dimension else ""
        parts.append(
            f'<div class="{card_class}"{tooltip}>'
            f'<div class="wii-kpi-label"{label_style}>{label}</div>'
            f'<div class="wii-kpi-value">{value}</div>'
            f"{dimension}"
            f"{comparison}"
            f'<div class="wii-kpi-caption">{caption}</div>'
            "</div>"
        )
    parts.append("</div>")
    st.markdown("".join(parts), unsafe_allow_html=True)
