"""Quote-aware TSV parsing, field-count-accumulator record rejoin, and encoding fallback.

D-10: parse with stdlib csv (never a naive .split), try strict utf-8 first, fall back to a
whole-file cp1252 reparse only on UnicodeDecodeError. Rejoin always runs before any validation
(Pitfall 2): validating case_id uniqueness on un-rejoined rows produces a false collision on the
empty-string case_id from blank split-record fragments.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from wii.data.schema import NCOLS, RAW_COLUMNS, HeaderDriftError, MalformedRecordError
from wii.utils.hashing import sha256_file

_FRAGMENT_PREVIEW_LEN = 80


@dataclass(frozen=True)
class ParsedYear:
    year: int
    header: tuple[str, ...]
    rows: list[list[str]]
    encoding: str
    cp1252_fallback: bool
    rejoined_record_count: int
    blank_line_count: int
    raw_file_sha256: str


def rejoin_records(
    csv_rows: Iterable[list[str]], year: int
) -> tuple[Iterator[list[str]], dict[str, int]]:
    """Field-count-accumulator rejoin (RESEARCH Pattern 1).

    Any physical line with < NCOLS fields starts (or continues) a pending record. Zero-field
    lines are skipped, whether or not a record is pending, and counted in blank_line_count.
    Boundary fields are joined with a single space. Overflow past NCOLS, or EOF mid-record,
    raises MalformedRecordError.

    Returns (row_iterator, stats) where stats has "rejoined_record_count" and "blank_line_count".
    Stats are populated fully only once the returned iterator is exhausted.
    """
    stats = {"rejoined_record_count": 0, "blank_line_count": 0}

    def _generate() -> Iterator[list[str]]:
        pending: list[str] | None = None
        pending_started_at: int | None = None
        for lineno, fields in enumerate(csv_rows, start=1):
            if pending is None:
                if len(fields) == NCOLS:
                    yield fields
                elif len(fields) == 0:
                    stats["blank_line_count"] += 1
                else:
                    pending = fields
                    pending_started_at = lineno
                continue
            if len(fields) == 0:
                stats["blank_line_count"] += 1
                continue
            merged_boundary = pending[-1] + " " + fields[0]
            candidate = pending[:-1] + [merged_boundary] + fields[1:]
            if len(candidate) == NCOLS:
                stats["rejoined_record_count"] += 1
                yield candidate
                pending = None
                pending_started_at = None
            elif len(candidate) < NCOLS:
                pending = candidate
            else:
                preview = repr(candidate)[:_FRAGMENT_PREVIEW_LEN]
                raise MalformedRecordError(
                    year,
                    f"rejoin overflowed {NCOLS} fields at physical line {lineno} "
                    f"(fragment started at line {pending_started_at}): {preview}",
                )
        if pending is not None:
            preview = repr(pending)[:_FRAGMENT_PREVIEW_LEN]
            raise MalformedRecordError(
                year,
                f"file ended mid-record (fragment started at line {pending_started_at}): {preview}",
            )

    return _generate(), stats


def _read_all_rows(path: Path, encoding: str) -> list[list[str]]:
    with open(path, encoding=encoding, newline="") as f:
        reader = csv.reader(f, delimiter="\t", quotechar='"')
        return list(reader)


def parse_year_file(path: Path, year: int) -> ParsedYear:
    path = Path(path)
    raw_file_sha256 = sha256_file(path)

    encoding = "utf-8"
    cp1252_fallback = False
    try:
        all_rows = _read_all_rows(path, "utf-8")
    except UnicodeDecodeError:
        encoding = "cp1252"
        cp1252_fallback = True
        try:
            all_rows = _read_all_rows(path, "cp1252")
        except UnicodeDecodeError as e:
            # Bytes undefined in cp1252 too (0x81, 0x8D, 0x8F, 0x90, 0x9D): surface as a malformed
            # year so the pipeline records a structural QA failure instead of crashing the run.
            raise MalformedRecordError(
                year, f"file is neither valid UTF-8 nor cp1252 (byte offset {e.start})"
            ) from e

    if not all_rows:
        raise HeaderDriftError(year, RAW_COLUMNS, ())

    header = tuple(all_rows[0])
    if header != RAW_COLUMNS:
        raise HeaderDriftError(year, RAW_COLUMNS, header)

    body_iter, stats = rejoin_records(all_rows[1:], year)
    rows = list(body_iter)

    return ParsedYear(
        year=year,
        header=header,
        rows=rows,
        encoding=encoding,
        cp1252_fallback=cp1252_fallback,
        rejoined_record_count=stats["rejoined_record_count"],
        blank_line_count=stats["blank_line_count"],
        raw_file_sha256=raw_file_sha256,
    )
