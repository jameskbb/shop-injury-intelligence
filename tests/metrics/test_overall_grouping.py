"""Hand-computed test for `by="all"` (T-03-07): a single-group, weighted, CI-bearing total/share
over the whole filtered population, using the same synthetic fixture and hand-computed numbers
documented in conftest.py's docstring.

conftest.py's `estimate_total` derivation (all 8 cases, all-ages, year 2024) gives estimate=157.0,
raw_n=8 -- `estimate_by(..., by="all")` must reproduce exactly that as its single "all" row, since
both are "every case, no grouping split" over the identical domain. `share_by(..., by="all")`
against the product_code=841 numerator (hand-computed estimate=140.0) must equal 140.0/157.0, the
same ratio a caller would get by hand-dividing the two independently-verified totals.
"""

from __future__ import annotations

import pytest

from wii.metrics import CaseFilter, estimate_by, estimate_total, share_by

ALL_AGES_2024 = CaseFilter(years=(2024, 2024), age_scope="all")


def test_estimate_by_all_matches_estimate_total(con):
    result = estimate_by(con, ALL_AGES_2024, by="all")
    assert result.height == 1
    row = result.row(0, named=True)
    assert row["group_id"] == "all"

    total = estimate_total(con, ALL_AGES_2024)
    assert row["estimate"] == pytest.approx(total.estimate) == pytest.approx(157.0)
    assert row["raw_n"] == total.raw_n == 8
    assert row["se"] == pytest.approx(total.se)
    assert row["df"] == total.df


def test_share_by_all_matches_hand_computed_weighted_ratio(con):
    """Numerator = product_code 841 cases (hand-computed estimate 140.0, conftest.py); denominator
    = the whole all-ages 2024 domain (157.0) -- share must equal the manually computed ratio
    140.0/157.0, not a per-tool/per-product split."""
    numerator = CaseFilter(product_codes=(841,), age_scope="all")
    result = share_by(con, ALL_AGES_2024, numerator, by="all")
    assert result.height == 1
    row = result.row(0, named=True)
    assert row["group_id"] == "all"
    assert row["num_estimate"] == pytest.approx(140.0)
    assert row["den_estimate"] == pytest.approx(157.0)
    assert row["share"] == pytest.approx(140.0 / 157.0)


def test_per_tool_grouping_unchanged_by_all_addition(con):
    """Adding the "all" grouping must not alter any existing per-tool grouping's output -- same
    hand-computed values as test_estimator.py's product_code=841/tool=table_saw assertions."""
    by_tool = estimate_by(con, ALL_AGES_2024, by="tool")
    row = by_tool.filter(by_tool["group_id"] == "table_saw").row(0, named=True)
    assert row["raw_n"] == 6
    assert row["estimate"] == pytest.approx(140.0)

    other_saw = by_tool.filter(by_tool["group_id"] == "other_power_saw").row(0, named=True)
    assert other_saw["raw_n"] == 1
    assert other_saw["estimate"] == pytest.approx(12.0)
