"""Parsing of the --years CLI argument into a safe, bounded list of ints."""

from __future__ import annotations

import datetime
import re

_YEAR_RE = re.compile(r"^\d{4}$")
_MIN_YEAR = 1990


class YearSpecError(ValueError):
    """Raised when --years cannot be parsed into a valid, bounded list of years."""


def _validate_year_token(token: str, today: datetime.date) -> int:
    if not _YEAR_RE.match(token):
        raise YearSpecError(f"Invalid year token {token!r}: must be exactly 4 digits")
    year = int(token)
    if not (_MIN_YEAR <= year <= today.year):
        raise YearSpecError(f"Year {year} out of range [{_MIN_YEAR}, {today.year}]")
    return year


def parse_years_arg(spec: str, today: datetime.date | None = None) -> list[int]:
    """Parse "2019:2025" (inclusive range) or "2019,2021" or "2024" into sorted unique years.

    Paths must only ever be built from the returned ints, never from the raw spec string.
    """
    if today is None:
        today = datetime.date.today()
    if not spec or not spec.strip():
        raise YearSpecError("Empty --years spec")
    spec = spec.strip()

    if ":" in spec:
        parts = spec.split(":")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise YearSpecError(f"Invalid range spec {spec!r}: expected START:END")
        start = _validate_year_token(parts[0], today)
        end = _validate_year_token(parts[1], today)
        if start > end:
            raise YearSpecError(f"Invalid range {spec!r}: start {start} > end {end}")
        return list(range(start, end + 1))

    if "," in spec:
        tokens = [t.strip() for t in spec.split(",")]
        if any(not t for t in tokens):
            raise YearSpecError(f"Invalid list spec {spec!r}: empty token")
        years = {_validate_year_token(t, today) for t in tokens}
        return sorted(years)

    return [_validate_year_token(spec, today)]
