"""Shared AppTest fixtures (D-14). `AppTest.from_file` needs an ABSOLUTE path (Wave-0 spike A5,
03-01-SUMMARY.md "Wave-0 spike results") -- app.py is resolved via `wii.utils.paths.repo_root()`
so tests work regardless of the invoking cwd.
"""

from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from wii.utils.paths import repo_root

APP_TIMEOUT = 30


@pytest.fixture
def app_test() -> AppTest:
    """A fresh, not-yet-run `AppTest.from_file("app.py")` instance."""
    return AppTest.from_file(str(repo_root() / "app.py"))


def run_app_test() -> AppTest:
    """Build and `.run()` an `app.py` AppTest with the standard timeout. Used where a plain
    function (not a fixture) is more convenient, e.g. inside a timing assertion.
    """
    at = AppTest.from_file(str(repo_root() / "app.py"))
    at.run(timeout=APP_TIMEOUT)
    return at
