"""D-05 ratio-estimator share tests and D-09/D-10 severity_profile tests."""

from __future__ import annotations

import pytest

from wii.metrics import CaseFilter, share_by
from wii.metrics.severity import load_severity_metrics


def test_share_by_matches_direct_weighted_ratio(con):
    """table_saw group, share of cases with product_code 832 co-membership among all 841 cases
    is not meaningful on this fixture (no overlap); instead verify the share for the
    circular_saw group (c4 + c8) restricted to c8's product (895 co-membership isn't testable
    without a second filter axis) -- use product_codes as the numerator condition and confirm
    against a hand ratio: circular_saw group total = 17 (c4:5, c8:12); numerator = cases that
    also carry product 895 (only c8, weight 12) -> share = 12/17.
    """
    denominator = CaseFilter(years=(2024, 2024), age_scope="all", tools=("circular_saw",))
    numerator = CaseFilter(product_codes=(895,), age_scope="all")
    result = share_by(con, denominator, numerator, by="tool")
    row = result.filter(result["group_id"] == "circular_saw").row(0, named=True)
    assert row["share"] == pytest.approx(12.0 / 17.0)
    assert row["den_estimate"] == pytest.approx(17.0)
    assert row["num_estimate"] == pytest.approx(12.0)


def test_share_stable_requires_both_numerator_and_denominator_stable(con):
    denominator = CaseFilter(years=(2024, 2024), age_scope="all", tools=("table_saw",))
    numerator = CaseFilter(product_codes=(841,), age_scope="all")  # tiny numerator N < 20
    result = share_by(con, denominator, numerator, by="tool")
    row = result.filter(result["group_id"] == "table_saw").row(0, named=True)
    assert row["stable"] is False
    assert any(r.startswith("den_") or r.startswith("num_") for r in row["suppression_reason"])


def test_load_severity_metrics_has_at_least_19_entries():
    metrics = load_severity_metrics()
    assert len(metrics) >= 19
    assert "escalated_care" in metrics
    assert "fatality" in metrics
    assert metrics["eye_face"].kind == "body_rollup_any"
    assert metrics["laceration"].codes == (59,)


def test_severity_config_rejects_unknown_kind(tmp_path):
    from wii.metrics.severity import SeverityConfigError, load_severity_metrics

    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 1\nmetrics:\n  foo:\n    kind: not_a_real_kind\n")
    with pytest.raises(SeverityConfigError, match="metrics.foo.kind"):
        load_severity_metrics(bad)
