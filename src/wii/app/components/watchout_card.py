"""My Shop WATCH-OUTS card (D-03, 05-03): pure HTML builders over
`wii.analytics.insights.Watchout` -- no streamlit, no `wii.metrics`/SQL, every interpolated
string `html.escape`'d, single-string no-blank-line markup (T-05-15, matching
`components.body_map.top_region_chips_html`'s established shape). This module makes no
suppression/threshold decisions of its own (T-03-07) -- every field is already resolved by the
`Watchout` it renders.
"""

from __future__ import annotations

import html

from wii.analytics.insights import CategoryCell, Watchout
from wii.app.components.stability_badge import (
    LIMITED_SAMPLE_LABEL,
    STATE_LIMITED,
    STATE_STABLE,
    SUPPRESSED_LABEL,
    format_share,
)

FIELD_LABELS: tuple[str, str, str, str] = (
    "Primary body region",
    "Distinctive injury",
    "Common mechanism",
    "What stands out",
)
NO_THRESHOLD_COPY = (
    "Nothing in the current data clears the reporting threshold for this tool; showing no "
    "distinctive pattern rather than a weak one."
)
_MECHANISM_SUFFIX = " (narrative-derived)"
_SUPPRESSED_VALUE_HTML = f"— · {html.escape(SUPPRESSED_LABEL)}"


def _category_value_html(cell: CategoryCell | None, suffix: str = "") -> str:
    """ "{label}{suffix} · {share}" for a stable/limited cell (LIMITED_SAMPLE_LABEL appended when
    limited); "— · {SUPPRESSED_LABEL}" for a missing/suppressed cell -- shared by the "Primary
    body region" and "Common mechanism" fields."""
    if cell is None or cell.value is None or cell.state not in (STATE_STABLE, STATE_LIMITED):
        return _SUPPRESSED_VALUE_HTML
    value = f"{html.escape(cell.label)}{suffix} · {html.escape(format_share(cell.value))}"
    if cell.state == STATE_LIMITED:
        value = f"{value} · {html.escape(LIMITED_SAMPLE_LABEL)}"
    return value


def _distinctive_value_html(w: Watchout) -> str:
    """ "{distinctive_label} · {tool share} vs {workshop baseline share}", read from the
    `Insight.numbers` tuple `_share_vs_baseline` built (D-03) -- "—" plus SUPPRESSED_LABEL when
    this tool has no qualifying distinctive-injury comparison."""
    if w.distinctive is None or w.distinctive_label is None:
        return _SUPPRESSED_VALUE_HTML
    tool_value = w.distinctive.numbers[0][1]
    baseline_value = w.distinctive.numbers[1][1]
    return (
        f"{html.escape(w.distinctive_label)} · {html.escape(format_share(tool_value))} vs "
        f"{html.escape(format_share(baseline_value))}"
    )


def _stands_out_value_html(w: Watchout) -> str:
    if w.stands_out is None:
        return _SUPPRESSED_VALUE_HTML
    return html.escape(w.stands_out.text)


def _field_row(label: str, value_html: str) -> str:
    return (
        '<div class="wii-watchout-field">'
        f'<div class="wii-watchout-label">{html.escape(label)}</div>'
        f'<div class="wii-watchout-value">{value_html}</div>'
        "</div>"
    )


#: Printed ONCE when a card has no field that clears the floor. The Sander card used to repeat
#: "— · Estimate suppressed – insufficient statistical stability" on three consecutive rows: the
#: same sentence three times is not three disclosures, it is one disclosure shouted. The state is
#: still stated in full, once, and no field is silently blanked.
ALL_SUPPRESSED_COPY = (
    "Too few sample cases under the current filters for a profile of this tool; no field clears "
    "the reporting floor."
)


def _all_fields_suppressed(w: Watchout) -> bool:
    return (
        _category_value_html(w.primary_region) == _SUPPRESSED_VALUE_HTML
        and _category_value_html(w.common_mechanism, _MECHANISM_SUFFIX) == _SUPPRESSED_VALUE_HTML
        and (w.nothing_clears or _distinctive_value_html(w) == _SUPPRESSED_VALUE_HTML)
    )


def watchout_card_html(w: Watchout) -> str:
    """One WATCH-OUTS card. "Primary body region" always renders; when `w.nothing_clears` the
    "Distinctive injury" and "What stands out" rows collapse into a single `.wii-watchout-note`
    row carrying `NO_THRESHOLD_COPY` verbatim (no filler) -- "Common mechanism" always renders on
    its own row regardless. When NO field clears, the whole body collapses to
    `ALL_SUPPRESSED_COPY`, stated once. Every interpolated value is `html.escape`'d; a card whose
    fields individually lack stable data still renders every row, each showing "—" plus
    `SUPPRESSED_LABEL` for that field alone."""
    title = html.escape(w.tool_label)
    if _all_fields_suppressed(w):
        return (
            '<div class="wii-watchout-card">'
            f'<div class="wii-watchout-title">{title}</div>'
            f'<div class="wii-watchout-note">{html.escape(ALL_SUPPRESSED_COPY)}</div>'
            "</div>"
        )
    rows: list[str] = [_field_row(FIELD_LABELS[0], _category_value_html(w.primary_region))]
    if w.nothing_clears:
        rows.append(f'<div class="wii-watchout-note">{html.escape(NO_THRESHOLD_COPY)}</div>')
        rows.append(
            _field_row(FIELD_LABELS[2], _category_value_html(w.common_mechanism, _MECHANISM_SUFFIX))
        )
    else:
        rows.append(_field_row(FIELD_LABELS[1], _distinctive_value_html(w)))
        rows.append(
            _field_row(FIELD_LABELS[2], _category_value_html(w.common_mechanism, _MECHANISM_SUFFIX))
        )
        rows.append(_field_row(FIELD_LABELS[3], _stands_out_value_html(w)))
    return (
        '<div class="wii-watchout-card">'
        f'<div class="wii-watchout-title">{title}</div>'
        f"{''.join(rows)}"
        "</div>"
    )


def watchout_grid_html(ws: tuple[Watchout, ...]) -> str:
    """Wraps `watchout_card_html` output for every `w` in `ws` inside `.wii-watchout-grid`."""
    cards = "".join(watchout_card_html(w) for w in ws)
    return f'<div class="wii-watchout-grid">{cards}</div>'
