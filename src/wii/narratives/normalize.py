"""D-13 text normalization: ASCII fold, uppercase/whitespace collapse, redaction-placeholder
handling, abbreviation expansion, and a negation-window tagging pass.

Runs as one ordered pipeline so every rule table in `rules.py` reads against the same
normalized token stream: fold -> uppercase -> collapse whitespace -> redact -> expand
abbreviations -> tokenize -> tag negation.

NEGATIVE is deliberately excluded from the negation-cue set -- research found it is almost
always an imaging/lab result ("SCANS WERE NEGATIVE"), not a negated tool or mechanism statement
(orchestrator ruling; deviates from D-13's literal cue list on this one token).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

REDACTION_PLACEHOLDER = "ZREDZ"

NEGATION_WINDOW = 4
# D-13 negation cues, minus NEGATIVE (see module docstring). "NEG" only negates as the bigram
# "NEG FOR". "W/O" is expanded to WITHOUT by abbreviation expansion before this pass runs, so
# WITHOUT alone covers it here.
NEGATION_CUE_TOKENS = frozenset({"NO", "NOT", "DENIES", "DENIED", "WITHOUT", "R/O"})
CLAUSE_BREAK_TOKENS = frozenset({"AND", "BUT"})

# (R) and AE have no Unicode canonical decomposition, so NFKD alone won't fold them -- explicit
# map first, then NFKD + combining-mark strip handles everything else (accented Latin letters).
_EXPLICIT_ASCII_MAP = {
    "®": "R",  # (R) registered sign
    "©": "C",  # (C) copyright sign
    "™": "TM",  # (TM) trademark sign
    "Æ": "AE",  # AE ligature, upper
    "æ": "ae",  # ae ligature, lower
    "°": "",  # degree sign
}

_TOKEN_RE = re.compile(r"\S+")
_WS_RE = re.compile(r"\s+")
_REDACTION_RE = re.compile(r"\*{3,}")


@dataclass(frozen=True)
class NormalizedText:
    """Output of the normalization pipeline: the normalized string plus its token stream and a
    parallel negation mask (same length as `tokens`)."""

    text: str
    tokens: tuple[str, ...]
    negated_mask: tuple[bool, ...]


def _fold_to_ascii(raw: str) -> str:
    out: list[str] = []
    for ch in raw:
        mapped = _EXPLICIT_ASCII_MAP.get(ch)
        if mapped is not None:
            out.append(mapped)
            continue
        decomposed = unicodedata.normalize("NFKD", ch)
        out.append(decomposed.encode("ascii", "ignore").decode("ascii"))
    return "".join(out)


def _expand_abbreviations(text: str, table: dict[str, str]) -> str:
    # Longest abbreviation first so "W/O" (3 chars) is tried before "W/" (2 chars) -- otherwise
    # the shorter pattern would eat the "W/" out of "W/O " and leave a stray "O" behind.
    for abbrev in sorted(table, key=len, reverse=True):
        expansion = table[abbrev]
        escaped = re.escape(abbrev)
        if abbrev.endswith("/"):
            # Slash-terminated abbreviations (e.g. "W/") are often glued directly to the next
            # word ("W/GLOVES") -- only the leading boundary is required.
            pattern = rf"(?<![A-Z0-9]){escaped}"
        else:
            pattern = rf"(?<![A-Z0-9]){escaped}(?![A-Z0-9])"
        text = re.sub(pattern, expansion, text)
    return text


def _tag_negation(tokens: tuple[str, ...]) -> tuple[bool, ...]:
    negated = [False] * len(tokens)
    window = 0
    n = len(tokens)
    for i, tok in enumerate(tokens):
        bare = tok.strip(".,;")
        if window > 0:
            negated[i] = True
            window -= 1
        is_neg_for = bare == "NEG" and i + 1 < n and tokens[i + 1].strip(".,;") == "FOR"
        if bare in NEGATION_CUE_TOKENS or is_neg_for:
            window = NEGATION_WINDOW
            if is_neg_for and i + 1 < n:
                negated[i + 1] = True
        if bare in CLAUSE_BREAK_TOKENS or tok.endswith((".", ";", ",")):
            window = 0
    return tuple(negated)


def normalize(raw: str | None, abbreviations: dict[str, str] | None = None) -> NormalizedText:
    """Run the full D-13 pipeline. `raw=None`/empty/whitespace-only returns an empty
    `NormalizedText`."""
    if not raw or not raw.strip():
        return NormalizedText(text="", tokens=(), negated_mask=())

    folded = _fold_to_ascii(raw)
    upper = folded.upper()
    collapsed = _WS_RE.sub(" ", upper).strip()
    redacted = _REDACTION_RE.sub(REDACTION_PLACEHOLDER, collapsed)
    expanded = _expand_abbreviations(redacted, abbreviations or {})

    tokens = tuple(m.group(0) for m in _TOKEN_RE.finditer(expanded))
    negated_mask = _tag_negation(tokens)
    return NormalizedText(text=expanded, tokens=tokens, negated_mask=negated_mask)


def token_spans(text: str) -> list[tuple[int, int]]:
    """Char `(start, end)` spans of `_TOKEN_RE.finditer(text)`, in the same order as `.tokens`.
    Used by `rules.py` to map a regex match's character span back to token indices for
    negation-mask lookup."""
    return [(m.start(), m.end()) for m in _TOKEN_RE.finditer(text)]
