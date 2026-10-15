"""Unit tests for wii.data.download: Fetcher protocol, latest-year detection, safe caching.

Every test uses FakeFetcher/RaisingFetcher (tests/conftest.py) -- the autouse socket guard stays
active, and none of these tests touch the network.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from tests.conftest import FakeFetcher, RaisingFetcher, synthetic_year_bytes
from wii.data import download as download_mod
from wii.data.download import (
    HOME_URL,
    DownloadError,
    FetchError,
    FetchResponse,
    LatestYearDetectionError,
    detect_latest_year,
    download_year,
    is_neiss_header,
    parse_max_archived_year,
    probe_year_available,
    tsv_url,
)
from wii.data.schema import RAW_COLUMNS

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "home_aspx_ddl.html"
_TODAY = datetime.date(2026, 9, 14)


def _fixture_bytes() -> bytes:
    return _FIXTURE_PATH.read_bytes()


# --- tsv_url -----------------------------------------------------------------


def test_tsv_url_builds_official_archived_data_path():
    url = tsv_url(2024)
    assert url == "https://www.cpsc.gov/cgibin/NEISSQuery/Data/Archived%20Data/2024/neiss2024.tsv"


# --- is_neiss_header -----------------------------------------------------------------


def test_is_neiss_header_true_for_real_header_prefix():
    header_line = "\t".join(f'"{h}"' for h in RAW_COLUMNS)
    assert is_neiss_header((header_line + "\r\n").encode("utf-8"))


def test_is_neiss_header_false_for_html_block_page():
    assert not is_neiss_header(b"<html><body>Access Denied</body></html>")


def test_is_neiss_header_false_for_empty_bytes():
    assert not is_neiss_header(b"")


# --- parse_max_archived_year -----------------------------------------------------------------


def test_parse_max_archived_year_from_real_fixture():
    assert parse_max_archived_year(_fixture_bytes(), _TODAY) == 2025


def test_parse_max_archived_year_handles_aspnet_prefixed_id():
    # Same markup shape, a different (but still ddlArchivedYear-suffixed) generated id.
    html = _fixture_bytes().replace(
        b"ctl00_ContentPlaceHolder1_ddlArchivedYear", b"ctl00_Main_ddlArchivedYear"
    )
    assert parse_max_archived_year(html, _TODAY) == 2025


def test_parse_max_archived_year_none_when_no_matching_select():
    assert parse_max_archived_year(b"<html><body>no dropdown here</body></html>", _TODAY) is None


def test_parse_max_archived_year_ignores_years_beyond_today():
    html = b'<select id="ddlArchivedYear"><option value="2099">2099</option></select>'
    assert parse_max_archived_year(html, _TODAY) is None


# --- probe_year_available -----------------------------------------------------------------


def test_probe_year_available_true_for_200_with_valid_header():
    fetcher = FakeFetcher({tsv_url(2025): FetchResponse(200, synthetic_year_bytes(2025))})
    assert probe_year_available(fetcher, 2025) is True


def test_probe_year_available_false_for_404():
    fetcher = FakeFetcher({})
    assert probe_year_available(fetcher, 2099) is False


def test_probe_year_available_false_for_html_block_page():
    fetcher = FakeFetcher({tsv_url(2025): FetchResponse(200, b"<html>blocked</html>")})
    assert probe_year_available(fetcher, 2025) is False


# --- detect_latest_year -----------------------------------------------------------------


def test_detect_latest_year_dropdown_success():
    fetcher = FakeFetcher({HOME_URL: FetchResponse(200, _fixture_bytes())})
    result = detect_latest_year(fetcher, _TODAY)
    assert (result.year, result.method) == (2025, "dropdown")
    assert result.dropdown_years is not None and max(result.dropdown_years) == 2025
    assert result.probed == []


class _HomeRaisesFetcher:
    """Raises FetchError for HOME_URL specifically; everything else is a plain lookup."""

    def __init__(self, tsv_responses: dict[str, FetchResponse]):
        self._tsv = tsv_responses
        self.calls: list[str] = []

    def get(self, url: str, *, max_bytes: int | None = None) -> FetchResponse:
        self.calls.append(url)
        if url == HOME_URL:
            raise FetchError("simulated network failure")
        resp = self._tsv.get(url)
        if resp is None:
            return FetchResponse(status_code=404, content=b"")
        if max_bytes is not None and resp.status_code == 200:
            return FetchResponse(status_code=206, content=resp.content[:max_bytes])
        return resp


def test_detect_latest_year_falls_back_to_probe_when_home_fetch_raises():
    fetcher = _HomeRaisesFetcher({tsv_url(2025): FetchResponse(200, synthetic_year_bytes(2025))})
    result = detect_latest_year(fetcher, _TODAY)
    assert (result.year, result.method) == (2025, "probe")
    assert result.probed == [2026, 2025]
    assert result.dropdown_years is None


def test_detect_latest_year_raises_when_nothing_available():
    fetcher = _HomeRaisesFetcher({})
    with pytest.raises(LatestYearDetectionError):
        detect_latest_year(fetcher, _TODAY)


def test_detect_latest_year_falls_back_when_dropdown_has_no_years():
    fetcher = FakeFetcher(
        {
            HOME_URL: FetchResponse(200, b"<html>no dropdown</html>"),
            tsv_url(2026): FetchResponse(200, synthetic_year_bytes(2026)),
        }
    )
    result = detect_latest_year(fetcher, _TODAY)
    assert (result.year, result.method) == (2026, "probe")


# --- download_year -----------------------------------------------------------------


def test_download_year_writes_part_then_renames_no_part_survives(tmp_path):
    fetcher = FakeFetcher({tsv_url(2024): FetchResponse(200, synthetic_year_bytes(2024))})
    outcome = download_year(fetcher, 2024, tmp_path)
    assert outcome.action == "downloaded"
    final_path = tmp_path / "neiss2024.tsv"
    assert outcome.path == final_path
    assert final_path.is_file()
    assert final_path.read_bytes() == synthetic_year_bytes(2024)
    assert list(tmp_path.glob("*.part")) == []


def test_download_year_html_block_page_rejected_before_caching(tmp_path):
    fetcher = FakeFetcher({tsv_url(2024): FetchResponse(200, b"<html>Access Denied</html>")})
    with pytest.raises(DownloadError):
        download_year(fetcher, 2024, tmp_path)
    assert not (tmp_path / "neiss2024.tsv").is_file()
    assert list(tmp_path.glob("*.part")) == []


def test_download_year_cached_without_refresh_makes_zero_fetcher_calls(tmp_path):
    final_path = tmp_path / "neiss2024.tsv"
    original = synthetic_year_bytes(2024)
    final_path.write_bytes(original)
    fetcher = RaisingFetcher()
    outcome = download_year(fetcher, 2024, tmp_path)
    assert outcome.action == "cached"
    assert final_path.read_bytes() == original


def test_download_year_refresh_with_failing_fetch_leaves_original_bytes_unchanged(tmp_path):
    final_path = tmp_path / "neiss2024.tsv"
    original = synthetic_year_bytes(2024)
    final_path.write_bytes(original)
    fetcher = FakeFetcher({})  # tsv_url(2024) not registered -> 404
    with pytest.raises(DownloadError):
        download_year(fetcher, 2024, tmp_path, refresh=True)
    assert final_path.read_bytes() == original
    assert list(tmp_path.glob("*.part")) == []


def test_download_year_manually_placed_file_sha256_unchanged_without_refresh(tmp_path):
    import hashlib

    final_path = tmp_path / "neiss2024.tsv"
    manual_content = synthetic_year_bytes(2024, n_records=1)
    final_path.write_bytes(manual_content)
    before = hashlib.sha256(final_path.read_bytes()).hexdigest()

    fetcher = FakeFetcher(
        {tsv_url(2024): FetchResponse(200, synthetic_year_bytes(2024, n_records=9))}
    )
    outcome = download_year(fetcher, 2024, tmp_path)

    after = hashlib.sha256(final_path.read_bytes()).hexdigest()
    assert outcome.action == "cached"
    assert before == after


def test_download_year_oversized_response_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(download_mod, "MAX_DOWNLOAD_BYTES", 50)
    fetcher = FakeFetcher({tsv_url(2024): FetchResponse(200, synthetic_year_bytes(2024))})
    with pytest.raises(DownloadError):
        download_year(fetcher, 2024, tmp_path)
    assert not (tmp_path / "neiss2024.tsv").is_file()
    assert list(tmp_path.glob("*.part")) == []


def test_download_year_network_error_wrapped_as_download_error(tmp_path):
    fetcher = RaisingFetcher()
    with pytest.raises(DownloadError):
        download_year(fetcher, 2024, tmp_path)
    assert list(tmp_path.glob("*.part")) == []
