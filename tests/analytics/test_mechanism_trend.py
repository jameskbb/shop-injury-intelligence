"""D-13 MECHANISM TREND consistency-gate tests for `wii.analytics.mechanism_trend` (04-07 Task
2). Framework-free unit tests, mirroring `tests/analytics/test_insights.py`'s convention -- no
database, no Streamlit, hand-built inputs only. The research-derived numbers below come from
04-RESEARCH.md's Metadata table (VERIFIED per-year weighted unknown-mechanism share, 2019-2025,
window average 33.73%, max deviation 1.59pp).
"""

from __future__ import annotations

import pytest
import yaml

from wii.analytics.mechanism_trend import (
    GateResult,
    TrendGate,
    apply_consistency_gate,
    load_trend_gate,
)

_WINDOW_SHARE = 0.3373
_RESEARCH_YEAR_SHARES: dict[int, float] = {
    2019: 0.3490,
    2020: 0.3355,
    2021: 0.3313,
    2022: 0.3446,
    2023: 0.3215,
    2024: 0.3449,
    2025: 0.3343,
}


def test_gate_all_years_pass_research_numbers():
    gate = TrendGate(tolerance_pp=5.0, min_years=3, top_n=5)
    result = apply_consistency_gate(_RESEARCH_YEAR_SHARES, _WINDOW_SHARE, gate)
    assert result.passing_years == tuple(sorted(_RESEARCH_YEAR_SHARES))
    assert result.excluded_years == ()
    assert result.show_chart is True
    assert result.window_share == _WINDOW_SHARE


def test_gate_boundary_inclusive_5pp():
    gate = TrendGate(tolerance_pp=5.0, min_years=3, top_n=5)
    window_share = 0.30
    exactly_at = {2020: 0.35}  # deviation exactly 5.0pp
    just_over = {2020: 0.3501}  # deviation 5.01pp
    result_at = apply_consistency_gate(exactly_at, window_share, gate)
    result_over = apply_consistency_gate(just_over, window_share, gate)
    assert result_at.passing_years == (2020,)
    assert result_over.passing_years == ()
    assert result_over.excluded_years == (2020,)


def test_gate_excludes_none_share():
    gate = TrendGate(tolerance_pp=5.0, min_years=3, top_n=5)
    unknown_by_year = {2019: 0.34, 2020: None, 2021: 0.33}
    result = apply_consistency_gate(unknown_by_year, 0.335, gate)
    assert 2020 not in result.passing_years
    assert 2020 in result.excluded_years
    assert 2019 in result.passing_years
    assert 2021 in result.passing_years


def test_gate_min_years():
    gate_min_3 = TrendGate(tolerance_pp=5.0, min_years=3, top_n=5)
    gate_min_2 = TrendGate(tolerance_pp=5.0, min_years=2, top_n=5)
    two_years = {2019: 0.34, 2020: 0.335}
    result_3 = apply_consistency_gate(two_years, 0.335, gate_min_3)
    result_2 = apply_consistency_gate(two_years, 0.335, gate_min_2)
    assert len(result_3.passing_years) == 2
    assert result_3.show_chart is False
    assert result_2.show_chart is True


def test_gate_window_share_none():
    gate = TrendGate(tolerance_pp=5.0, min_years=3, top_n=5)
    result = apply_consistency_gate(_RESEARCH_YEAR_SHARES, None, gate)
    assert result.show_chart is False
    assert result.passing_years == ()
    assert isinstance(result, GateResult)


def test_load_trend_gate_reads_yaml(tmp_path):
    p = tmp_path / "insights.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "mechanism_trend_unknown_tolerance_pp": 5.0,
                "mechanism_trend_min_years": 3,
                "mechanism_trend_top_n": 5,
            }
        ),
        encoding="utf-8",
    )
    gate = load_trend_gate(p)
    assert gate == TrendGate(tolerance_pp=5.0, min_years=3, top_n=5)


def test_load_trend_gate_missing_key_raises(tmp_path):
    p = tmp_path / "insights.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "mechanism_trend_unknown_tolerance_pp": 5.0,
                "mechanism_trend_top_n": 5,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="mechanism_trend_min_years"):
        load_trend_gate(p)


def test_load_thresholds_unaffected():
    from wii.analytics.insights import load_thresholds

    thresholds = load_thresholds()
    assert thresholds.min_raw_n == 50
