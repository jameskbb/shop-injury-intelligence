"""D-08/D-09 Case Explorer search term parsing + safe highlighting.

`highlight_narrative` is the SINGLE call site for turning a raw narrative + search terms into
HTML: match spans are found on the RAW narrative first (each term `re.escape`d, case-insensitive,
overlapping/touching spans merged), then the output is built by `html.escape()`-ing every segment
-- matched and unmatched -- independently, wrapping only the matched (already-escaped) segments in
`<mark>`. Raw text never reaches markup unescaped, so neither a narrative nor a search term can
inject markup, and HTML entities inserted by `html.escape()` can never be split by a `<mark>`
boundary (05-RESEARCH.md Pitfall 1: the literal "escape first, then regex the escaped text" order
has a verified entity-splitting bug -- this module deliberately does NOT do that).
"""

from __future__ import annotations

import html
import re

MAX_QUERY_CHARS = 100
MAX_TERMS = 5


def parse_search_terms(raw: str | None) -> tuple[str, ...]:
    """Coerces `None` to `""`, keeps only the first `MAX_QUERY_CHARS` characters, splits on
    whitespace, drops empty strings, dedupes case-insensitively (keeping the FIRST spelling seen),
    and keeps at most `MAX_TERMS` terms."""
    text = (raw or "")[:MAX_QUERY_CHARS]
    seen: set[str] = set()
    terms: list[str] = []
    for token in text.split():
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        terms.append(token)
        if len(terms) >= MAX_TERMS:
            break
    return tuple(terms)


def highlight_narrative(narrative: str | None, terms: tuple[str, ...]) -> str:
    """Match on RAW text first, then `html.escape()` each segment independently -- entities can
    never be split by a `<mark>` boundary because they are only ever created INSIDE one
    already-matched-or-unmatched segment, never straddling a segment boundary (05-RESEARCH.md
    "Code Examples", verified this phase's research session)."""
    text = narrative or ""
    if not terms:
        return html.escape(text)
    spans: list[tuple[int, int]] = []
    for term in terms:
        if not term:
            continue
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        spans.extend((m.start(), m.end()) for m in pattern.finditer(text))
    if not spans:
        return html.escape(text)
    spans.sort()
    merged = [spans[0]]
    for start, end in spans[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    out: list[str] = []
    pos = 0
    for start, end in merged:
        out.append(html.escape(text[pos:start]))
        out.append(f"<mark>{html.escape(text[start:end])}</mark>")
        pos = end
    out.append(html.escape(text[pos:]))
    return "".join(out)
