"""D-08 tests for scripts/capture_screenshots.py's pure helpers -- no browser, no Streamlit
server, CI-safe. Playwright is never imported here; one test asserts the module itself never
imports it either (T-06-SC)."""

from __future__ import annotations

import sys

from scripts.capture_screenshots import (
    budget_ok,
    expected_filenames,
    gallery_filename,
    nn_for,
    slug_for,
)
from wii.app.pages_registry import PAGE_SPECS, PageSpec


def test_expected_filenames_count_and_first_entry():
    names = expected_filenames(PAGE_SPECS)
    assert len(names) == 2 * len(PAGE_SPECS) + 1
    assert names[0] == "01-overview-desktop.png"


def test_expected_filenames_registry_order_and_hero_last():
    names = expected_filenames(PAGE_SPECS)
    assert names[-1] == "hero.png"
    # First page's pair appears before the second page's pair (registry order, all sets per page
    # before the next page).
    assert names[:2] == ["01-overview-desktop.png", "01-overview-mobile.png"]
    second_slug = slug_for(PAGE_SPECS[1])
    assert names[2] == f"02-{second_slug}-desktop.png"


def test_spec_with_empty_url_path_maps_to_overview_slug():
    spec = PageSpec("Injury Overview", "src/wii/app/pages/overview.py", "")
    assert slug_for(spec) == "overview"


def test_nn_is_zero_padded():
    assert nn_for(1) == "01"
    assert nn_for(8) == "08"
    assert nn_for(12) == "12"


def test_gallery_filename_shape():
    assert gallery_filename("03", "body-map", "desktop") == "03-body-map-desktop.png"


def test_budget_ok_flags_oversized_desktop_file():
    assert budget_ok(600 * 1024, "desktop") is False


def test_budget_ok_flags_oversized_mobile_file():
    assert budget_ok(300 * 1024, "mobile") is False


def test_budget_ok_passes_undersized_desktop_file():
    assert budget_ok(400 * 1024, "desktop") is True


def test_importing_module_does_not_import_playwright():
    assert "playwright" not in sys.modules
    assert "playwright.sync_api" not in sys.modules


def test_pyproject_and_lockfile_never_mention_playwright():
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    pyproject = (repo_root / "pyproject.toml").read_text()
    lockfile = (repo_root / "uv.lock").read_text()
    assert "playwright" not in pyproject.lower()
    assert "playwright" not in lockfile.lower()
