"""Hand-computed variance/dedup tests against the synthetic fixture (see conftest.py docstring
for the full derivation)."""

from __future__ import annotations

import math

import pytest

from wii.metrics import CaseFilter, EstimateRecord, apply_suppression, estimate_by, estimate_total
from wii.metrics.stability import ANNUAL_BASIS_LABEL, LIMITED_SAMPLE_LABEL, PERIOD_BASIS_LABEL

ALL_AGES_2024 = CaseFilter(years=(2024, 2024), age_scope="all")


def test_estimate_by_product_code_841_matches_hand_computed_variance(con):
    result = estimate_by(con, ALL_AGES_2024, by="product_code")
    row = result.filter(result["group_id"] == 841).row(0, named=True)

    assert row["estimate"] == pytest.approx(140.0)
    assert row["raw_n"] == 6
    assert row["se"] == pytest.approx(math.sqrt(3100.0))
    assert row["df"] == 4
    assert row["stable"] is False
    assert set(row["suppression_reason"]) == {"est_lt_1200", "n_lt_20", "cv_gt_0.33"}


def test_duplicate_bridge_slot_for_same_code_counts_case_once(con):
    """c7 carries product 841 in two bridge slots -- the 841 group's PSU B2 total must be 40, not
    80, and raw_n for 841 must be 6, not 7."""
    result = estimate_by(con, ALL_AGES_2024, by="product_code")
    row = result.filter(result["group_id"] == 841).row(0, named=True)
    assert row["raw_n"] == 6
    assert row["estimate"] == pytest.approx(140.0)


def test_two_tool_case_counts_once_per_tool_and_once_in_total(con):
    """c8 carries two different in-scope tools (895, 832): it must appear in BOTH tool groups,
    each counted once, and exactly once in estimate_total's distinct-case total."""
    by_tool = estimate_by(con, ALL_AGES_2024, by="tool")

    other_saw = by_tool.filter(by_tool["group_id"] == "other_power_saw").row(0, named=True)
    assert other_saw["raw_n"] == 1
    assert other_saw["estimate"] == pytest.approx(12.0)

    circular = by_tool.filter(by_tool["group_id"] == "circular_saw").row(0, named=True)
    assert circular["raw_n"] == 2
    assert circular["estimate"] == pytest.approx(17.0)  # c4 (5) + c8 (12)

    total = estimate_total(con, ALL_AGES_2024)
    assert total.raw_n == 8
    assert total.estimate == pytest.approx(157.0)


def test_estimate_by_tool_table_saw_matches_product_code_841(con):
    by_tool = estimate_by(con, ALL_AGES_2024, by="tool")
    row = by_tool.filter(by_tool["group_id"] == "table_saw").row(0, named=True)
    assert row["raw_n"] == 6
    assert row["estimate"] == pytest.approx(140.0)


def test_estimate_total_returns_estimate_record(con):
    record = estimate_total(con, ALL_AGES_2024)
    assert isinstance(record, EstimateRecord)
    assert record.n_years == 1
    assert record.basis == "period"
    assert record.period_total == pytest.approx(record.annual_average)


def test_unsupported_by_raises(con):
    with pytest.raises(ValueError):
        estimate_by(con, ALL_AGES_2024, by="not_a_dimension")


@pytest.mark.parametrize(
    "estimate,raw_n,cv,expected_stable,expected_reasons",
    [
        (5000.0, 50, 0.10, True, []),
        (500.0, 50, 0.10, False, ["est_lt_1200"]),
        (5000.0, 10, 0.10, False, ["n_lt_20"]),
        (5000.0, 50, 0.50, False, ["cv_gt_0.33"]),
        (0.0, 5, None, False, ["est_lt_1200", "n_lt_20", "cv_gt_0.33"]),
    ],
)
def test_apply_suppression(estimate, raw_n, cv, expected_stable, expected_reasons):
    stable, reasons = apply_suppression(estimate, raw_n, cv)
    assert stable is expected_stable
    assert reasons == expected_reasons


def test_share_by_numerator_inherits_denominator_age_scope_all(con):
    """Finding 2 regression: share_by's numerator must be a SUBSET condition of the denominator,
    not an independently adults-only-by-default filter. circular_saw group (age_scope='all') is
    c4 (non-adult, weight 5, product 832) + c8 (adult, weight 12, product 832 in its circular_saw
    slot) -- both carry product 832, so a numerator of product_codes=(832,) restricted to the SAME
    domain as the (all-ages) denominator must match both, giving share == 1.0. Before the fix, the
    numerator's own default age_scope='adults' silently dropped c4, giving 12/17 instead."""
    from wii.metrics import share_by

    denominator = CaseFilter(years=(2024, 2024), age_scope="all", tools=("circular_saw",))
    numerator = CaseFilter(product_codes=(832,))  # age_scope left at its default ("adults")
    result = share_by(con, denominator, numerator, by="tool")
    row = result.filter(result["group_id"] == "circular_saw").row(0, named=True)
    assert row["den_estimate"] == pytest.approx(17.0)
    assert row["num_estimate"] == pytest.approx(17.0)
    assert row["share"] == pytest.approx(1.0)


def test_label_constants_exact_text():
    assert LIMITED_SAMPLE_LABEL == "Limited sample – interpret cautiously"
    assert PERIOD_BASIS_LABEL == "estimated injuries during selected period"
    assert ANNUAL_BASIS_LABEL == "estimated annual average"
