"""Registry integrity tests (D-03, 06-01 Task 2): titles/url_paths/files unique, exactly one
default, every spec.file exists, every page module under src/wii/app/pages/ is registered (an
unregistered page module fails), and app.py builds its navigation from PAGE_SPECS with no inline
`st.Page("src/...")` string-literal page paths left over from the pre-registry version.
"""

from __future__ import annotations

import re

from wii.app.pages_registry import PAGE_SPECS
from wii.utils.paths import repo_root

# Non-page helper modules under src/wii/app/pages/ that are never registered in PAGE_SPECS.
_NON_PAGE_ALLOWLIST = {"__init__.py"}


def test_titles_are_unique():
    titles = [spec.title for spec in PAGE_SPECS]
    assert len(titles) == len(set(titles)), f"duplicate titles: {titles}"


def test_url_paths_are_unique():
    url_paths = [spec.url_path for spec in PAGE_SPECS]
    assert len(url_paths) == len(set(url_paths)), f"duplicate url_paths: {url_paths}"


def test_files_are_unique():
    files = [spec.file for spec in PAGE_SPECS]
    assert len(files) == len(set(files)), f"duplicate files: {files}"


def test_exactly_one_default():
    defaults = [spec for spec in PAGE_SPECS if spec.is_default]
    assert len(defaults) == 1, f"expected exactly one default page, found {len(defaults)}"


def test_every_spec_file_exists_on_disk():
    for spec in PAGE_SPECS:
        path = repo_root() / spec.file
        assert path.is_file(), f"{spec.file!r} (spec {spec.title!r}) does not exist on disk"


def test_every_page_module_is_registered():
    """An unregistered page module under src/wii/app/pages/ (minus the allowlist) must fail this
    test -- a page cannot be silently skipped (D-03)."""
    pages_dir = repo_root() / "src" / "wii" / "app" / "pages"
    on_disk = {
        f"src/wii/app/pages/{p.name}"
        for p in pages_dir.glob("*.py")
        if p.name not in _NON_PAGE_ALLOWLIST
    }
    registered = {spec.file for spec in PAGE_SPECS}
    assert on_disk == registered, (
        f"page modules on disk but not in PAGE_SPECS: {on_disk - registered}; "
        f"PAGE_SPECS entries with no file on disk: {registered - on_disk}"
    )


def test_app_py_builds_from_page_specs():
    source = (repo_root() / "app.py").read_text(encoding="utf-8")
    assert "PAGE_SPECS" in source, "app.py must build its pages from PAGE_SPECS"
    assert not re.search(r'st\.Page\(\s*"src/', source), (
        'app.py still contains an inline st.Page("src/...") string-literal page path -- pages '
        "must be built by iterating PAGE_SPECS, not hard-coded a second time"
    )
