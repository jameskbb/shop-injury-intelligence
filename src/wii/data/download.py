"""Network layer: Fetcher protocol, official CPSC retrieval, latest-year detection (D-21/D-22/D-23).

Only cpsc.gov constants exist below -- there is no environment variable or CLI flag that can
redirect downloads to any other host, and no alternate/unofficial data source is ever consulted.
All download and detection logic is behind the injectable `Fetcher` protocol so tests never
touch a real socket.
"""

from __future__ import annotations

import csv
import datetime
import os
from dataclasses import dataclass
from datetime import UTC
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal, Protocol

from wii.data.schema import RAW_COLUMNS

HOME_URL = "https://www.cpsc.gov/cgibin/NEISSQuery/home.aspx"
TSV_URL_TEMPLATE = (
    "https://www.cpsc.gov/cgibin/NEISSQuery/Data/Archived%20Data/{year}/neiss{year}.tsv"
)
MAX_DOWNLOAD_BYTES = 500_000_000

_HEADER_PROBE_BYTES = 4096


def tsv_url(year: int) -> str:
    """The official annual-file URL for `year`. Accepts an int only -- no free-form host/path."""
    return TSV_URL_TEMPLATE.format(year=int(year))


@dataclass(frozen=True)
class FetchResponse:
    status_code: int
    content: bytes
    content_type: str | None = None


class Fetcher(Protocol):
    """Injected into every network call so tests never open a socket."""

    def get(self, url: str, *, max_bytes: int | None = None) -> FetchResponse: ...


class FetchError(Exception):
    """Wraps any network-layer exception raised while fetching `url`."""


class CurlCffiFetcher:
    """cpsc.gov sits behind Akamai: plain HTTP clients get 403. curl_cffi with
    impersonate="chrome" matches Chrome's TLS/HTTP2 fingerprint and gets 200/206."""

    def __init__(self, impersonate: str = "chrome", timeout: float = 120.0):
        self._impersonate = impersonate
        self._timeout = timeout

    def get(self, url: str, *, max_bytes: int | None = None) -> FetchResponse:
        from curl_cffi import requests as cffi_requests

        headers = None
        if max_bytes is not None:
            headers = {"Range": f"bytes=0-{max_bytes - 1}"}

        try:
            resp = cffi_requests.get(
                url,
                impersonate=self._impersonate,
                timeout=self._timeout,
                headers=headers,
                # TLS verification is left at the curl_cffi default (enabled) -- never disabled.
            )
        except Exception as e:  # noqa: BLE001 - any curl_cffi/network exception becomes FetchError
            raise FetchError(f"request to {url} failed: {e}") from e

        return FetchResponse(
            status_code=resp.status_code,
            content=resp.content,
            content_type=resp.headers.get("content-type") if resp.headers else None,
        )


def is_neiss_header(prefix_bytes: bytes) -> bool:
    """True if the first physical line of `prefix_bytes` is exactly the 25-column NEISS header."""
    first_line = prefix_bytes.split(b"\n", 1)[0].split(b"\r", 1)[0]
    if not first_line:
        return False
    for encoding in ("utf-8", "cp1252"):
        try:
            text = first_line.decode(encoding)
        except UnicodeDecodeError:
            continue
        try:
            row = next(csv.reader([text], delimiter="\t", quotechar='"'))
        except (csv.Error, StopIteration):
            continue
        if tuple(row) == RAW_COLUMNS:
            return True
    return False


def probe_year_available(fetcher: Fetcher, year: int) -> bool:
    """D-21 fallback: True if `year`'s TSV responds 200/206 with a valid header prefix."""
    try:
        resp = fetcher.get(tsv_url(year), max_bytes=_HEADER_PROBE_BYTES)
    except FetchError:
        return False
    if resp.status_code not in (200, 206):
        return False
    return is_neiss_header(resp.content)


class _DropdownYearParser(HTMLParser):
    """Finds the <select> whose id or name ends with "ddlArchivedYear" and collects every
    4-digit option value/text no greater than `today.year`."""

    def __init__(self, today: datetime.date):
        super().__init__()
        self._today = today
        self._in_target_select = False
        self.years: set[int] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "select":
            ident = attrs_dict.get("id") or ""
            name = attrs_dict.get("name") or ""
            if ident.endswith("ddlArchivedYear") or name.endswith("ddlArchivedYear"):
                self._in_target_select = True
        elif tag == "option" and self._in_target_select:
            self._maybe_add(attrs_dict.get("value"))

    def handle_endtag(self, tag: str) -> None:
        if tag == "select":
            self._in_target_select = False

    def handle_data(self, data: str) -> None:
        if self._in_target_select:
            self._maybe_add(data.strip())

    def _maybe_add(self, token: str | None) -> None:
        if not token or not token.isdigit() or len(token) != 4:
            return
        year = int(token)
        if year <= self._today.year:
            self.years.add(year)


def _dropdown_years(html_bytes: bytes, today: datetime.date) -> list[int]:
    parser = _DropdownYearParser(today)
    parser.feed(html_bytes.decode("utf-8", errors="replace"))
    return sorted(parser.years)


def parse_max_archived_year(html_bytes: bytes, today: datetime.date) -> int | None:
    """D-21 primary signal: the max year in home.aspx's ddlArchivedYear dropdown, or None."""
    years = _dropdown_years(html_bytes, today)
    return years[-1] if years else None


@dataclass(frozen=True)
class LatestYearDetection:
    year: int
    method: Literal["dropdown", "probe"]
    dropdown_years: list[int] | None
    probed: list[int]
    detected_at: str


class LatestYearDetectionError(Exception):
    """Raised when neither the dropdown nor the probe fallback finds an accessible year."""


def detect_latest_year(fetcher: Fetcher, today: datetime.date) -> LatestYearDetection:
    """D-21: dropdown first; on any failure (network, parse, or empty result), probe
    today.year, today.year-1, today.year-2 in order."""
    dropdown_years: list[int] | None = None
    try:
        resp = fetcher.get(HOME_URL)
        if resp.status_code == 200:
            years = _dropdown_years(resp.content, today)
            if years:
                dropdown_years = years
    except Exception:  # noqa: BLE001 - any dropdown-path failure falls through to the probe
        dropdown_years = None

    if dropdown_years:
        return LatestYearDetection(
            year=max(dropdown_years),
            method="dropdown",
            dropdown_years=dropdown_years,
            probed=[],
            detected_at=datetime.datetime.now(UTC).isoformat(),
        )

    probed: list[int] = []
    for candidate in (today.year, today.year - 1, today.year - 2):
        probed.append(candidate)
        if probe_year_available(fetcher, candidate):
            return LatestYearDetection(
                year=candidate,
                method="probe",
                dropdown_years=dropdown_years,
                probed=probed,
                detected_at=datetime.datetime.now(UTC).isoformat(),
            )

    raise LatestYearDetectionError(
        "Could not detect the latest NEISS year via the home.aspx dropdown or by probing "
        f"{today.year} down to {today.year - 2} "
        f"(dropdown_years={dropdown_years}, probed={probed})"
    )


@dataclass(frozen=True)
class DownloadOutcome:
    year: int
    path: Path
    action: Literal["cached", "downloaded", "refreshed"]


class DownloadError(Exception):
    def __init__(self, year: int, reason: str):
        self.year = year
        self.reason = reason
        super().__init__(f"Year {year}: download failed - {reason}")


def _cleanup_part(part_path: Path) -> None:
    if part_path.exists():
        part_path.unlink()


def download_year(
    fetcher: Fetcher, year: int, raw_dir: Path, *, refresh: bool = False
) -> DownloadOutcome:
    """Cache `year`'s TSV into `raw_dir/neiss{year}.tsv`.

    A final file that already exists is returned as "cached" with zero fetcher calls unless
    `refresh` is set. Otherwise: GET, validate status/size/header, write to a same-directory
    `.part` temp with fsync, then os.replace onto the final name -- no `.part` ever survives,
    and an existing final file is never touched if the fetch fails.
    """
    raw_dir = Path(raw_dir)
    final_path = raw_dir / f"neiss{year}.tsv"
    part_path = raw_dir / f"neiss{year}.tsv.part"

    if final_path.is_file() and not refresh:
        return DownloadOutcome(year, final_path, "cached")

    action: Literal["downloaded", "refreshed"] = (
        "refreshed" if final_path.is_file() else "downloaded"
    )

    try:
        resp = fetcher.get(tsv_url(year))
    except FetchError as e:
        raise DownloadError(year, f"network error: {e}") from e

    if resp.status_code != 200:
        raise DownloadError(year, f"unexpected HTTP status {resp.status_code}")

    content = resp.content
    if len(content) == 0:
        raise DownloadError(year, "empty response body")
    if len(content) > MAX_DOWNLOAD_BYTES:
        raise DownloadError(year, f"response exceeds MAX_DOWNLOAD_BYTES ({MAX_DOWNLOAD_BYTES})")
    if not is_neiss_header(content[:_HEADER_PROBE_BYTES]):
        raise DownloadError(
            year,
            "response does not match the NEISS TSV header contract "
            "(possible Akamai/WAF block page)",
        )

    raw_dir.mkdir(parents=True, exist_ok=True)
    try:
        with open(part_path, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(part_path, final_path)
    except BaseException:
        _cleanup_part(part_path)
        raise

    return DownloadOutcome(year, final_path, action)
