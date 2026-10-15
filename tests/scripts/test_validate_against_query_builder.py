"""Synthetic tests for wii.validation.query_builder -- parser and comparison logic only, no
committed data required."""

from __future__ import annotations

from wii.validation.query_builder import (
    OfficialRow,
    compare_row,
    parse_official_table,
    render_markdown,
)

_SAMPLE_TABLE = """NEISS Query Results
National Estimate of Injuries Treated in Emergency Departments by PROD
Treatment Dates: 2024


Product\tNational Estimate\tNumber of Cases\tCV\t95% CI Lower Bound\t95% CI Upper Bound
841 - BENCH OR TABLE SAWS\t33,052\t560\t0.10\t26,403\t39,701
1053 - FERTILIZERS AND OTHER CHEMICALS FOR OUTDOOR USE\t*\t*\t*\t*\t*
"""


def test_parse_official_table_normal_row():
    rows = parse_official_table(_SAMPLE_TABLE)
    row = rows[841]
    assert row.name == "BENCH OR TABLE SAWS"
    assert row.suppressed is False
    assert row.estimate == 33052
    assert row.n == 560
    assert row.cv == 0.10
    assert row.ci_low == 26403
    assert row.ci_high == 39701


def test_parse_official_table_suppressed_row():
    rows = parse_official_table(_SAMPLE_TABLE)
    row = rows[1053]
    assert row.suppressed is True
    assert row.estimate is None
    assert row.n is None
    assert row.cv is None
    assert row.ci_low is None
    assert row.ci_high is None


def test_parse_official_table_ignores_preamble_lines():
    rows = parse_official_table(_SAMPLE_TABLE)
    assert len(rows) == 2  # only the 2 data rows, not the 6 header/preamble lines


def test_compare_row_matches_when_all_fields_within_tolerance():
    official = OfficialRow(
        code=841,
        name="X",
        suppressed=False,
        estimate=33052,
        n=560,
        cv=0.10,
        ci_low=26403,
        ci_high=39701,
    )
    ours = {
        "estimate": 33051.85,
        "raw_n": 560,
        "cv": 0.100931,
        "ci_low": 26402.28,
        "ci_high": 39701.42,
        "stable": True,
    }
    result = compare_row(841, official, ours)
    assert result.match is True
    assert result.mismatches == ()


def test_compare_row_reports_synthetic_estimate_mismatch():
    """A synthetic mismatch (estimate off by 2) is reported with the field named."""
    official = OfficialRow(
        code=841,
        name="X",
        suppressed=False,
        estimate=33052,
        n=560,
        cv=0.10,
        ci_low=26403,
        ci_high=39701,
    )
    ours = {
        "estimate": 33054.0,  # 2 off from official
        "raw_n": 560,
        "cv": 0.10,
        "ci_low": 26403.0,
        "ci_high": 39701.0,
        "stable": True,
    }
    result = compare_row(841, official, ours)
    assert result.match is False
    assert any("estimate" in m for m in result.mismatches)


def test_compare_row_suppression_agreement():
    official = OfficialRow(
        code=805,
        name="X",
        suppressed=True,
        estimate=None,
        n=None,
        cv=None,
        ci_low=None,
        ci_high=None,
    )
    ours = {
        "estimate": 645.0,
        "raw_n": 10,
        "cv": 0.38,
        "ci_low": None,
        "ci_high": None,
        "stable": False,
    }
    result = compare_row(805, official, ours)
    assert result.match is True


def test_compare_row_code_not_in_official_table_is_not_a_failure():
    result = compare_row(
        999,
        None,
        {
            "estimate": 5000.0,
            "raw_n": 30,
            "cv": 0.1,
            "ci_low": None,
            "ci_high": None,
            "stable": True,
        },
    )
    assert result.published is False
    assert result.match is True


def test_render_markdown_contains_year_sections_and_totals():
    official = OfficialRow(
        code=841,
        name="X",
        suppressed=False,
        estimate=33052,
        n=560,
        cv=0.10,
        ci_low=26403,
        ci_high=39701,
    )
    ours = {
        "estimate": 33051.85,
        "raw_n": 560,
        "cv": 0.100931,
        "ci_low": 26402.28,
        "ci_high": 39701.42,
        "stable": True,
    }
    row = compare_row(841, official, ours)
    md = render_markdown({2024: [row]}, "Not run.")
    assert "## 2024" in md
    assert "| 841 |" in md
    assert "0 mismatch(es)" in md
    assert "## Pooled multi-year" in md
