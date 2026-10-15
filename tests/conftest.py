"""Shared pytest fixtures: network guard, synthetic NEISS record builders, realdata resolver."""

from __future__ import annotations

import itertools
import os
import socket
from pathlib import Path

import pytest

from wii.data.download import FetchError, FetchResponse
from wii.data.schema import RAW_COLUMNS
from wii.utils.paths import repo_root

_DEFAULTS = {
    "Treatment_Date": "01/15/2024",
    "Age": "35",
    "Sex": "1",
    "Race": "0",
    "Other_Race": "",
    "Hispanic": "0",
    "Body_Part": "82",
    "Diagnosis": "59",
    "Other_Diagnosis": "",
    # Non-blank by default: the shared 2024 default year is post-2019, where a real file's
    # Body_Part_2 fill rate is ~19-24% (never 0%, D-16/PITFALLS Pitfall 6). A blank default here
    # would make secondary_fill_rate_by_era fail on almost every synthetic fixture that doesn't
    # care about body-part-2 semantics. Tests exercising the null/blank slot-2 case override this
    # explicitly per-record.
    "Body_Part_2": "92",
    "Diagnosis_2": "",
    "Other_Diagnosis_2": "",
    "Disposition": "1",
    "Location": "1",
    "Fire_Involvement": "0",
    "Product_1": "841",
    "Product_2": "0",
    "Product_3": "0",
    "Alcohol": "",
    "Drug": "",
    "Narrative_1": "35YOM LACERATED FINGER ON TABLE SAW",
    "Stratum": "S",
    "PSU": "29",
    "Weight": "50.0",
}


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    """Never let a test touch a real socket."""

    def _raise(*_args, **_kwargs):
        raise RuntimeError("network blocked in tests")

    monkeypatch.setattr(socket.socket, "connect", _raise)
    monkeypatch.setattr(socket, "create_connection", _raise)


@pytest.fixture
def neiss_record():
    """Factory fixture: neiss_record(**overrides) -> list[str] of 25 fields in RAW_COLUMNS order."""
    counter = itertools.count(1)

    def _make(**overrides) -> list[str]:
        record = dict(_DEFAULTS)
        record["CPSC_Case_Number"] = f"{next(counter):09d}"
        record.update(overrides)
        return [record[col] for col in RAW_COLUMNS]

    return _make


def write_neiss_tsv(
    path: Path,
    records: list[list[str]],
    *,
    header: tuple[str, ...] = RAW_COLUMNS,
    encoding: str = "utf-8",
    raw_lines: list[str] | None = None,
) -> None:
    """Write a quoted header + CRLF-terminated, tab-joined body.

    If raw_lines is given, it fully replaces the body (records is ignored) -- lets fixtures
    control physical line framing byte-for-byte for quirk reproductions (split records, blank
    lines, etc).
    """
    quoted_header = "\t".join(f'"{h}"' for h in header)
    if raw_lines is not None:
        body_lines = list(raw_lines)
    else:
        body_lines = ["\t".join(rec) for rec in records]
    content = "\r\n".join([quoted_header, *body_lines]) + "\r\n"
    Path(path).write_bytes(content.encode(encoding))


class FakeFetcher:
    """Test double -- maps url -> FetchResponse, records every call, never opens a socket.

    A `max_bytes` request against a stored 200 response is served as a sliced 206 (simulating a
    Range response); any other stored status is returned unsliced. A url with no stored response
    returns 404.
    """

    def __init__(self, responses: dict[str, FetchResponse]):
        self._responses = responses
        self.calls: list[tuple[str, int | None]] = []

    def get(self, url: str, *, max_bytes: int | None = None) -> FetchResponse:
        self.calls.append((url, max_bytes))
        resp = self._responses.get(url)
        if resp is None:
            return FetchResponse(status_code=404, content=b"")
        if max_bytes is not None and resp.status_code == 200:
            return FetchResponse(
                status_code=206, content=resp.content[:max_bytes], content_type=resp.content_type
            )
        return resp


class RaisingFetcher:
    """Test double that raises FetchError on every call -- proves --offline/cached paths never
    touch it."""

    def get(self, url: str, *, max_bytes: int | None = None) -> FetchResponse:
        raise FetchError(f"RaisingFetcher: refused to fetch {url}")


def synthetic_year_bytes(year: int, *, n_records: int = 3) -> bytes:
    """A tiny valid NEISS TSV for `year` as raw bytes: header + n_records synthetic rows.

    Respects D-16's per-era Body_Part_2 fill-rate assertion (0.0 for year<=2018, >0.05 for
    year>=2019) so a run over a full 20-year synthetic window doesn't trip
    secondary_fill_rate_by_era.
    """
    header = "\t".join(f'"{h}"' for h in RAW_COLUMNS)
    lines = [header]
    for i in range(1, n_records + 1):
        record = dict(_DEFAULTS)
        record["CPSC_Case_Number"] = f"{year}{i:05d}"
        record["Treatment_Date"] = f"06/15/{year}"
        if year <= 2018:
            record["Body_Part_2"] = ""
            record["Diagnosis_2"] = ""
            record["Other_Diagnosis_2"] = ""
            record["Alcohol"] = ""
            record["Drug"] = ""
        lines.append("\t".join(record[col] for col in RAW_COLUMNS))
    content = "\r\n".join(lines) + "\r\n"
    return content.encode("utf-8")


class _RealDataRawDir:
    def __init__(self, raw_dir: Path):
        self.dir = raw_dir

    def require(self, year: int) -> Path:
        p = self.dir / f"neiss{year}.tsv"
        if not p.is_file():
            pytest.fail(f"Required real data file missing: {p}")
        return p


@pytest.fixture
def realdata_raw_dir() -> _RealDataRawDir:
    raw_dir = Path(os.environ.get("WII_RAW_DIR") or (repo_root() / "data" / "raw"))
    return _RealDataRawDir(raw_dir)
