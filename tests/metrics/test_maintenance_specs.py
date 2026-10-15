"""D-14 (04-08): `estimate_by` support for `by="maintenance_context"`/`by="power_state"`,
narrative-derived group specs over `narrative_enrichment.maintenance_context`/
`.power_state_mentioned` (rules-1.2.0, 04-05). Hand-computed against `pair_con`'s fixture (see
`tests/metrics/conftest.py::pair_processed_dir` docstring): c1 (weight 10, blade_change,
mentioned_unplugged) and c2 (weight 20, cleaning, not_mentioned) are the only two
maintenance_or_setup=True cases, both stratum A PSU 1.
"""

from __future__ import annotations

import pytest

from wii.metrics import CaseFilter, estimate_by

FLT_MAINTENANCE_2024_ALL_AGES = CaseFilter(
    years=(2024, 2024), age_scope="all", maintenance_or_setup=True
)


def test_estimate_by_maintenance_context_hand_computed(pair_con):
    result = estimate_by(pair_con, FLT_MAINTENANCE_2024_ALL_AGES, "maintenance_context")
    by_group = {row["group_id"]: row["estimate"] for row in result.iter_rows(named=True)}
    assert by_group["blade_change"] == pytest.approx(10.0)
    assert by_group["cleaning"] == pytest.approx(20.0)
    assert "not_maintenance" not in by_group


def test_estimate_by_power_state_groups(pair_con):
    result = estimate_by(pair_con, FLT_MAINTENANCE_2024_ALL_AGES, "power_state")
    by_group = {row["group_id"]: row["estimate"] for row in result.iter_rows(named=True)}
    assert by_group["mentioned_unplugged"] == pytest.approx(10.0)
    assert by_group["not_mentioned"] == pytest.approx(20.0)
