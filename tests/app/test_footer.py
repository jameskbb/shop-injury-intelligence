"""D-08/APP-04 footer copy tests."""

from __future__ import annotations

import re

from wii.app.components.footer import source_line


def test_source_line_exact_copy_with_year():
    assert source_line(2025) == (
        "Source: U.S. Consumer Product Safety Commission – NEISS · Data through 2025"
    )


def test_source_line_none_year_says_unknown():
    line = source_line(None)
    assert line.endswith("Data through: unknown")
    assert not re.search(r"\d{4}", line)
