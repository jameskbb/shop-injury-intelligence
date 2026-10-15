"""D-11 pair-wise (two-key) grouped estimate tests. Hand-computed variance against the
synthetic fixture (see conftest.py's `pair_processed_dir` docstring for the derivation), plus
composite-key equivalence and marginal-sum (flow conservation) checks.
"""

from __future__ import annotations

import math

import pytest

from wii.metrics import CaseFilter, estimate_by, estimate_by_pair, estimate_total

FLT_ALL_AGES_2024 = CaseFilter(years=(2024, 2024), age_scope="all")


def test_pair_tool_mechanism_matches_hand_computed_variance(pair_con):
    result = estimate_by_pair(pair_con, FLT_ALL_AGES_2024, "tool", "mechanism")

    direct = result.filter(
        (result["group_id_1"] == "table_saw") & (result["group_id_2"] == "direct_cutting_contact")
    ).row(0, named=True)
    assert direct["estimate"] == pytest.approx(95.0)
    assert direct["raw_n"] == 4
    assert direct["se"] == pytest.approx(math.sqrt(1475.0))
    assert direct["df"] == 4
    assert direct["display_label_1"] == "Table Saw"
    assert direct["display_label_2"] == "direct_cutting_contact"

    kickback = result.filter(
        (result["group_id_1"] == "table_saw") & (result["group_id_2"] == "kickback_ejection")
    ).row(0, named=True)
    assert kickback["estimate"] == pytest.approx(45.0)
    assert kickback["raw_n"] == 2
    assert kickback["se"] == pytest.approx(math.sqrt(1025.0))


def test_pair_multi_membership_counts_case_once_per_tool(pair_con):
    """c8 carries two in-scope tools (other_power_saw seq1, circular_saw seq2); both tool groups
    must show c8's mechanism (flying_debris_fb) once each -- multi-membership per tool, not a
    double-count within either group."""
    result = estimate_by_pair(pair_con, FLT_ALL_AGES_2024, "tool", "mechanism")

    other_saw = result.filter(
        (result["group_id_1"] == "other_power_saw") & (result["group_id_2"] == "flying_debris_fb")
    ).row(0, named=True)
    assert other_saw["estimate"] == pytest.approx(12.0)
    assert other_saw["raw_n"] == 1

    circular = result.filter(
        (result["group_id_1"] == "circular_saw") & (result["group_id_2"] == "flying_debris_fb")
    ).row(0, named=True)
    assert circular["estimate"] == pytest.approx(12.0)
    assert circular["raw_n"] == 1

    circular_unknown = result.filter(
        (result["group_id_1"] == "circular_saw") & (result["group_id_2"] == "unknown")
    ).row(0, named=True)
    assert circular_unknown["estimate"] == pytest.approx(5.0)
    assert circular_unknown["raw_n"] == 1


def test_pair_cell_equals_single_key_filtered_estimate(pair_con):
    """The composite-key trick changes no variance math: a pair cell's se/cv equal
    estimate_by(...) run on the equivalent single-key filter."""
    pairs = estimate_by_pair(pair_con, FLT_ALL_AGES_2024, "tool", "mechanism")
    pair_row = pairs.filter(
        (pairs["group_id_1"] == "table_saw") & (pairs["group_id_2"] == "direct_cutting_contact")
    ).row(0, named=True)

    from dataclasses import replace

    single_key_flt = replace(FLT_ALL_AGES_2024, mechanisms=("direct_cutting_contact",))
    single = estimate_by(pair_con, single_key_flt, "tool")
    single_row = single.filter(single["group_id"] == "table_saw").row(0, named=True)

    assert pair_row["se"] == pytest.approx(single_row["se"], rel=1e-9)
    assert pair_row["cv"] == pytest.approx(single_row["cv"], rel=1e-9)
    assert pair_row["estimate"] == pytest.approx(single_row["estimate"], rel=1e-9)
    assert pair_row["raw_n"] == single_row["raw_n"]


def test_pair_sums_to_marginal_for_table_saw(pair_con):
    pairs = estimate_by_pair(pair_con, FLT_ALL_AGES_2024, "tool", "mechanism")
    table_saw_sum = pairs.filter(pairs["group_id_1"] == "table_saw")["estimate"].sum()

    marginal = estimate_by(pair_con, FLT_ALL_AGES_2024, "tool")
    table_saw_marginal = marginal.filter(marginal["group_id"] == "table_saw").row(0, named=True)

    assert table_saw_sum == pytest.approx(table_saw_marginal["estimate"], rel=1e-9)
    assert table_saw_sum == pytest.approx(140.0)


def test_pair_rejects_unknown_or_duplicate_by(pair_con):
    with pytest.raises(ValueError):
        estimate_by_pair(pair_con, FLT_ALL_AGES_2024, "tool", "not_a_dimension")
    with pytest.raises(ValueError):
        estimate_by_pair(pair_con, FLT_ALL_AGES_2024, "tool", "tool")


def test_pair_empty_result_has_expected_columns(pair_con):
    flt = CaseFilter(years=(2024, 2024), age_scope="all", tools=("nonexistent_tool",))
    result = estimate_by_pair(pair_con, flt, "tool", "mechanism")
    assert result.height == 0
    for col in ("group_id_1", "group_id_2", "display_label_1", "display_label_2", "estimate"):
        assert col in result.columns


def test_pair_multi_membership_total_exceeds_estimate_total(pair_con):
    """ "tool" is a multi-membership group (D-07): c8 carries two in-scope tools, so it is
    double-counted across the pair table exactly as `estimate_by(flt, "tool")`'s own rows do not
    sum to `estimate_total` -- the pair table inherits that, not a bug."""
    pairs = estimate_by_pair(pair_con, FLT_ALL_AGES_2024, "tool", "mechanism")
    total = estimate_total(pair_con, FLT_ALL_AGES_2024)
    # c8 (weight 12) counted once under other_power_saw and once under circular_saw.
    assert pairs["estimate"].sum() == pytest.approx(total.estimate + 12.0, rel=1e-9)


# --- D-09/D-15 primary-value Sankey-stage specs + flow conservation (Task 2) ---------------


def test_tool_primary_counts_multi_tool_case_once(pair_con):
    """c8's tool_primary is its seq-1 in-scope slot (product 895 -> other_power_saw) -- unlike
    the any-basis "tool" spec, c8 does NOT also appear under circular_saw."""
    by_tool = estimate_by(pair_con, FLT_ALL_AGES_2024, "tool")
    by_tool_primary = estimate_by(pair_con, FLT_ALL_AGES_2024, "tool_primary")

    circular_any = by_tool.filter(by_tool["group_id"] == "circular_saw").row(0, named=True)
    assert circular_any["raw_n"] == 2  # c4 + c8

    circular_primary = by_tool_primary.filter(by_tool_primary["group_id"] == "circular_saw")
    assert circular_primary.height == 1
    assert circular_primary.row(0, named=True)["raw_n"] == 1  # c4 only
    assert circular_primary.row(0, named=True)["estimate"] == pytest.approx(5.0)

    other_primary = by_tool_primary.filter(by_tool_primary["group_id"] == "other_power_saw").row(
        0, named=True
    )
    assert other_primary["raw_n"] == 1  # c8, primary
    assert other_primary["estimate"] == pytest.approx(12.0)

    table_saw_primary = by_tool_primary.filter(by_tool_primary["group_id"] == "table_saw").row(
        0, named=True
    )
    assert table_saw_primary["estimate"] == pytest.approx(140.0)

    assert by_tool_primary["estimate"].sum() == pytest.approx(157.0)


def test_body_region_primary_ignores_secondary_slot(pair_con):
    """c7 carries a SECOND bridge_case_body_parts slot (82 hand); the any-basis "body_region"
    spec counts it in both finger and hand, but "body_region_primary" (joined on
    fc.primary_body_part_code, never the bridge table) counts it only in finger."""
    any_basis = estimate_by(pair_con, FLT_ALL_AGES_2024, "body_region")
    primary_basis = estimate_by(pair_con, FLT_ALL_AGES_2024, "body_region_primary")

    hand_any = any_basis.filter(any_basis["group_id"] == "hand").row(0, named=True)
    hand_primary = primary_basis.filter(primary_basis["group_id"] == "hand").row(0, named=True)

    assert hand_any["raw_n"] == 3  # c2, c6, c7 (c7 via its secondary slot)
    assert hand_any["estimate"] == pytest.approx(85.0)  # 20 + 25 + 40
    assert hand_primary["raw_n"] == 2  # c2, c6 only
    assert hand_primary["estimate"] == pytest.approx(45.0)  # 20 + 25


def test_estimate_by_pair_tool_primary_mechanism_sums_to_estimate_total(pair_con):
    pairs = estimate_by_pair(pair_con, FLT_ALL_AGES_2024, "tool_primary", "mechanism")
    total = estimate_total(pair_con, FLT_ALL_AGES_2024)
    assert pairs["estimate"].sum() == pytest.approx(total.estimate, rel=1e-9)

    marginal = estimate_by(pair_con, FLT_ALL_AGES_2024, "mechanism")
    for row in marginal.iter_rows(named=True):
        incoming = pairs.filter(pairs["group_id_2"] == row["group_id"])["estimate"].sum()
        assert incoming == pytest.approx(row["estimate"], rel=1e-9)


def _assert_stage_conserves(pair_con, by1: str, by2: str) -> None:
    """Shared conservation assertion (D-09/D-15): the full pair table sums to estimate_total,
    and each side's own marginal (estimate_by(by1)/estimate_by(by2)) equals the sum of the pair
    rows carrying that category as group_id_1/group_id_2 respectively."""
    pairs = estimate_by_pair(pair_con, FLT_ALL_AGES_2024, by1, by2)
    total = estimate_total(pair_con, FLT_ALL_AGES_2024)
    assert pairs["estimate"].sum() == pytest.approx(total.estimate, rel=1e-9)

    marginal1 = estimate_by(pair_con, FLT_ALL_AGES_2024, by1)
    for row in marginal1.iter_rows(named=True):
        outgoing = pairs.filter(pairs["group_id_1"] == row["group_id"])["estimate"].sum()
        assert outgoing == pytest.approx(row["estimate"], rel=1e-9)

    marginal2 = estimate_by(pair_con, FLT_ALL_AGES_2024, by2)
    for row in marginal2.iter_rows(named=True):
        incoming = pairs.filter(pairs["group_id_2"] == row["group_id"])["estimate"].sum()
        assert incoming == pytest.approx(row["estimate"], rel=1e-9)


def test_conservation_tool_primary_to_mechanism(pair_con):
    _assert_stage_conserves(pair_con, "tool_primary", "mechanism")


def test_conservation_mechanism_to_body_region_primary(pair_con):
    _assert_stage_conserves(pair_con, "mechanism", "body_region_primary")

    pairs = estimate_by_pair(pair_con, FLT_ALL_AGES_2024, "mechanism", "body_region_primary")
    direct_finger = pairs.filter(
        (pairs["group_id_1"] == "direct_cutting_contact") & (pairs["group_id_2"] == "finger")
    ).row(0, named=True)
    assert direct_finger["estimate"] == pytest.approx(95.0)
    kickback_hand = pairs.filter(
        (pairs["group_id_1"] == "kickback_ejection") & (pairs["group_id_2"] == "hand")
    ).row(0, named=True)
    assert kickback_hand["estimate"] == pytest.approx(45.0)


def test_conservation_body_region_primary_to_diagnosis_category_primary(pair_con):
    _assert_stage_conserves(pair_con, "body_region_primary", "diagnosis_category_primary")

    pairs = estimate_by_pair(
        pair_con, FLT_ALL_AGES_2024, "body_region_primary", "diagnosis_category_primary"
    )
    finger_amputation = pairs.filter(
        (pairs["group_id_1"] == "finger") & (pairs["group_id_2"] == "amputation")
    ).row(0, named=True)
    assert finger_amputation["estimate"] == pytest.approx(55.0)


def test_conservation_diagnosis_category_primary_to_disposition(pair_con):
    _assert_stage_conserves(pair_con, "diagnosis_category_primary", "disposition")

    pairs = estimate_by_pair(
        pair_con, FLT_ALL_AGES_2024, "diagnosis_category_primary", "disposition"
    )
    foreign_body_not_recorded = pairs.filter(
        (pairs["group_id_1"] == "foreign_body") & (pairs["group_id_2"] == 9)
    ).row(0, named=True)
    assert foreign_body_not_recorded["estimate"] == pytest.approx(12.0)
    assert isinstance(foreign_body_not_recorded["group_id_2"], int)


def test_maintenance_or_setup_share_matches_hand_computed(pair_con):
    """D-08/D-14: maintenance_or_setup=True flags c1 (weight 10) and c2 (weight 20), both stratum
    A PSU 1 (04-08 extends the fixture so the maintenance-context breakdown has 2 populated
    categories); share_by over the "all" group gives the hand-computed ratio 30/157."""
    from wii.metrics import share_by

    result = share_by(pair_con, FLT_ALL_AGES_2024, CaseFilter(maintenance_or_setup=True), by="all")
    row = result.filter(result["group_id"] == "all").row(0, named=True)
    assert row["num_estimate"] == pytest.approx(30.0)
    assert row["den_estimate"] == pytest.approx(157.0)
    assert row["share"] == pytest.approx(30.0 / 157.0)
