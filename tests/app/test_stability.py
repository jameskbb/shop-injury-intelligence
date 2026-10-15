"""D-04/D-07 stability display tests: basis-matched resolution, suppressed vs limited display,
ranked-list ordering, and display formatting.
"""

from __future__ import annotations

from wii.app.components.stability_badge import (
    STATE_LIMITED,
    STATE_STABLE,
    STATE_SUPPRESSED,
    basis_caption,
    format_estimate,
    format_share,
    order_ranked,
    order_resolved,
    raw_n_label,
    resolve,
    resolve_share,
)
from wii.metrics.stability import LIMITED_SAMPLE_LABEL, SUPPRESSED_LABEL


def _row(**overrides):
    """A comfortably stable row by default: value >= 1,200, raw_n >= 20, cv <= 0.33 on both
    bases."""
    base = {
        "annual_average": 1500.0,
        "ci_low_annual": 1200.0,
        "ci_high_annual": 1800.0,
        "period_total": 7500.0,
        "ci_low": 6000.0,
        "ci_high": 9000.0,
        "raw_n": 50,
        "cv": 0.1,
    }
    base.update(overrides)
    return base


def test_resolve_reads_annual_fields_for_annual_basis():
    row = _row(annual_average=1600.0)
    resolved = resolve(row, "annual_average")
    assert resolved.state == STATE_STABLE
    assert resolved.value == 1600.0


def test_resolve_reads_period_fields_for_period_basis():
    row = _row(period_total=9999.0)
    resolved = resolve(row, "period_total")
    assert resolved.state == STATE_STABLE
    assert resolved.value == 9999.0


def test_resolve_basis_matched_independently():
    """A row stable on one basis and not the other must resolve differently per basis: here the
    annual_average (period_total / n_years) drops below the 1,200 floor while period_total does
    not -- cv/raw_n are shared and fine on both bases, so only the est<1,200 rule differs.
    """
    row = _row(annual_average=800.0, period_total=5000.0, raw_n=50, cv=0.1)
    annual = resolve(row, "annual_average")
    period = resolve(row, "period_total")
    assert annual.state == STATE_SUPPRESSED
    assert period.state == STATE_STABLE


def test_resolve_suppressed_shows_dash_and_exact_label():
    """raw_n < 20 fails the hard floor regardless of a healthy value/cv."""
    row = _row(annual_average=5000.0, raw_n=10, cv=0.1)
    resolved = resolve(row, "annual_average")
    assert resolved.state == STATE_SUPPRESSED
    assert resolved.value is None
    assert resolved.display_value == "—"
    assert resolved.label == SUPPRESSED_LABEL


def test_resolve_limited_shows_value_and_exact_label():
    """A healthy value/raw_n but CV > 33% is "limited", not "suppressed" -- the value still
    shows."""
    row = _row(annual_average=1500.0, raw_n=50, cv=0.5)
    resolved = resolve(row, "annual_average")
    assert resolved.state == STATE_LIMITED
    assert resolved.value == 1500.0
    assert resolved.display_value != "—"
    assert resolved.label == LIMITED_SAMPLE_LABEL


def test_resolve_unsupported_basis_raises():
    row = _row()
    try:
        resolve(row, "bogus")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for unsupported basis")


def test_resolve_share_reads_share_fields():
    row = {
        "share": 0.42,
        "share_ci_low": 0.30,
        "share_ci_high": 0.55,
        "stable": True,
        "suppression_reason": [],
        "num_raw_n": 40,
        "den_raw_n": 400,
    }
    resolved = resolve_share(row)
    assert resolved.state == STATE_STABLE
    assert resolved.value == 0.42
    assert resolved.display_value == "42.0%"


def test_order_ranked_never_interleaves():
    rows = [
        _row(annual_average=1500, raw_n=50, cv=0.5),  # limited
        _row(annual_average=2000, raw_n=50, cv=0.1),  # stable, highest
        _row(annual_average=100, raw_n=5, cv=0.5),  # suppressed (raw_n<20 AND est<1200)
        _row(annual_average=1300, raw_n=30, cv=0.1),  # stable
    ]
    ordered = order_ranked(rows, "annual_average")
    states = [resolve(r, "annual_average").state for r in ordered]
    assert states == [STATE_STABLE, STATE_STABLE, STATE_LIMITED, STATE_SUPPRESSED]
    assert ordered[0]["annual_average"] == 2000
    assert ordered[1]["annual_average"] == 1300


def test_order_resolved_never_interleaves():
    """WR-04: `order_resolved` is the shared stable-first-then-desc-by-value helper for rows
    already resolved elsewhere (e.g. `data.maintenance_share_by_tool_frame`'s `resolve_share`
    output) -- same D-07 ordering rule as `order_ranked`, applied without a third hand-rolled
    `_STATE_ORDER`/sort-key copy."""
    rows = [
        {"tool_id": "a", "state": STATE_LIMITED, "value": 0.10},
        {"tool_id": "b", "state": STATE_STABLE, "value": 0.05},
        {"tool_id": "c", "state": STATE_SUPPRESSED, "value": None},
        {"tool_id": "d", "state": STATE_STABLE, "value": 0.20},
    ]
    ordered = order_resolved(rows)
    assert [r["tool_id"] for r in ordered] == ["d", "b", "a", "c"]


def test_raw_n_label_exact_wording():
    assert raw_n_label(1234) == "1,234 sample cases (not a national count)"


def test_format_estimate_rounds_to_nearest_100():
    assert format_estimate(148348.32) == "148,300"
    assert format_estimate(149.0) == "100"


def test_format_share_one_decimal():
    assert format_share(0.4567) == "45.7%"


def test_basis_caption_annual_average():
    caption = basis_caption("annual_average", 2021, 2025)
    assert caption == "Estimated U.S. ER-treated injuries: annual average, 2021–2025"


def test_basis_caption_period_total():
    caption = basis_caption("period_total", 2021, 2025)
    assert caption == "Estimated U.S. ER-treated injuries during selected period, 2021–2025"


# ---------------------------------------------------------------------------
# Share stability: the side-prefixed suppression reasons `share_by` emits
# ---------------------------------------------------------------------------
#
# A share's stability is a COMPOSITE: its numerator and its denominator each pass or fail the CPSC
# rule on their own, so `wii.metrics.estimator.share_by` tags every reason with the side it came
# from ("num_est_lt_1200", "den_n_lt_20"). Those never matched the bare constants the classifier
# compared against, so EVERY share whose numerator or denominator fell under a hard floor was
# mis-tiered as "limited" -- which shows a number -- instead of "suppressed", which does not.
#
# Observed on My Shop's first screen: "Share of workshop burden: 0.0% (Limited sample)" printed
# beside an estimate the strip had already suppressed to "—". A visitor reads that as a bug in the
# numbers, and the reader who trusts it reads a measured zero where there is no measurement at
# all. The metric layer was correct throughout; only this classification was wrong.


def _share_row(share, stable, reasons, raw_n=5):
    return {
        "share": share,
        "share_ci_low": None,
        "share_ci_high": None,
        "stable": stable,
        "suppression_reason": reasons,
        "num_raw_n": raw_n,
    }


def test_share_with_a_hard_floor_failure_on_either_side_is_suppressed_not_limited():
    for reason in ("num_est_lt_1200", "num_n_lt_20", "den_est_lt_1200", "den_n_lt_20"):
        resolved = resolve_share(_share_row(0.0000096, False, [reason, "num_cv_gt_0.33"]))
        assert resolved.state == STATE_SUPPRESSED, reason
        assert resolved.display_value == "—"
        assert resolved.value is None
        assert resolved.label == SUPPRESSED_LABEL


def test_share_failing_only_the_cv_rule_is_still_limited_and_still_shows_its_number():
    resolved = resolve_share(_share_row(0.163, False, ["num_cv_gt_0.33"]))
    assert resolved.state == STATE_LIMITED
    assert resolved.display_value == "16.3%"
    assert resolved.label == LIMITED_SAMPLE_LABEL


def test_a_non_stable_share_rounding_to_zero_renders_a_dash_not_zero_point_zero():
    """The presentation guard behind the composite rule. "0.0% (Limited sample)" is a
    contradiction rather than a small number: the digits claim a measured zero that the label
    withdraws in the same breath. The dash loses nothing -- the label and the raw-N line still
    say a value exists and is not reportable."""
    resolved = resolve_share(_share_row(0.00004, False, ["num_cv_gt_0.33"]))
    assert resolved.display_value == "—"
    assert resolved.label == LIMITED_SAMPLE_LABEL  # quiet is not hidden
    assert resolved.state == STATE_LIMITED


def test_a_stable_share_rounding_to_zero_keeps_its_digits():
    """A genuinely measured, reportable near-zero share is a real finding and keeps its number;
    the guard applies only where a label already withdraws the value."""
    resolved = resolve_share(_share_row(0.00004, True, []))
    assert resolved.display_value == "0.0%"
    assert resolved.state == STATE_STABLE
