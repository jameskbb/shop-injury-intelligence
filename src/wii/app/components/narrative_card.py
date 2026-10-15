"""The one narrative card builder, shared by every page that shows verbatim ER text (Tool
Profiles, Body Map, Mechanisms, My Shop). It was previously copy-pasted per page as a private
`_narrative_card_html`, which is why four pages drifted into four slightly different meta lines.

Two honesty rules are built in and are not caller options:

  * Age is always a BRACKET, never an exact age. The card renders whatever bracket label the row
    carries and falls back to "Unknown age bracket" -- never to a number.
  * A missing field renders its "Unknown ..." label rather than an empty cell, so a blank never
    reads as a value.

The card also shortens the disposition. NEISS's own disposition text runs to ~150 characters
("Treated and released, or examined and released without treatment (also includes ...)"), which on
every card was longer than most of the narratives it annotated and pushed the rest of the meta
line onto a third row. `short_disposition` maps the dataset's own labels onto the clinical short
form a reader already knows and returns the ORIGINAL string unchanged when it recognises nothing,
so a re-coded dimension degrades to the long label rather than to a wrong one.
"""

from __future__ import annotations

import html
from collections.abc import Mapping

#: Longest-prefix map from `dim_disposition.label` to the short form. Keys are prefixes of the
#: committed labels, so a parenthetical suffix changing upstream cannot break the match.
_SHORT_DISPOSITION: tuple[tuple[str, str], ...] = (
    ("Treated and released", "Treated & released"),
    ("Treated and transferred", "Transferred"),
    ("Treated and admitted", "Admitted"),
    ("Held for observation", "Held for observation"),
    ("Left without being seen", "Left untreated"),
    ("Left before", "Left untreated"),
    ("Fatality", "Fatality"),
    ("Not recorded", "Disposition not recorded"),
)

UNKNOWN_AGE = "Unknown age bracket"
UNKNOWN_DIAGNOSIS = "Unknown diagnosis"
UNKNOWN_DISPOSITION = "Unknown disposition"


def short_disposition(label: str | None) -> str:
    """The clinical short form of a NEISS disposition label; the input unchanged when no known
    prefix matches, and `UNKNOWN_DISPOSITION` for a missing value."""
    if not label:
        return UNKNOWN_DISPOSITION
    for prefix, short in _SHORT_DISPOSITION:
        if label.startswith(prefix):
            return short
    return label


def narrative_card_html(row: Mapping, *, tool_label: str | None = None) -> str:
    """One card: the verbatim narrative, then `year · age bracket · diagnosis · disposition`.

    `tool_label` prepends the tool to the meta line, for the pages (Body Map, My Shop) where the
    tool is not otherwise on screen beside the card -- without it a reader cannot tell which tool
    a quoted case involved.

    Every interpolated value is escaped; the result is a single line with no blank line in it, so
    it can never end a raw-HTML block early.
    """
    narrative = html.escape(row["narrative"] or "")
    parts = [str(row["year"])]
    if tool_label:
        parts.insert(0, tool_label)
    parts.append(row["age_bracket_label"] or UNKNOWN_AGE)
    parts.append(row["diagnosis_label"] or UNKNOWN_DIAGNOSIS)
    parts.append(short_disposition(row["disposition_label"]))
    meta = html.escape(" · ".join(parts))
    return (
        '<div class="wii-narrative-card">'
        f'<div class="wii-narrative-text">{narrative}</div>'
        f'<div class="wii-narrative-meta">{meta}</div>'
        "</div>"
    )


def narrative_list_html(rows, *, tool_label: str | None = None) -> str:
    """`narrative_card_html` for every row, inside the `.wii-narrative-list` rule stack."""
    cards = "".join(narrative_card_html(row, tool_label=tool_label) for row in rows)
    return f'<div class="wii-narrative-list">{cards}</div>'
