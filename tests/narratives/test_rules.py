"""Synthetic-only structural tests for the full broad-code subtype rule set (Task 3, D-14)."""

from __future__ import annotations

from wii.narratives.classify import classify_case
from wii.narratives.rules import load_rules


def test_all_8_broad_codes_have_pattern_tables():
    rules = load_rules()
    for code in (895, 809, 845, 847, 871, 899, 862, 870):
        assert code in rules.broad_codes
        assert len(rules.broad_codes[code].patterns) > 0


def test_899_angle_grinder_refines():
    _e, r = classify_case("USING AN ANGLE GRINDER CUT HAND", [(899, "grinder_unspecified")])
    assert r[0].final_tool_id == "angle_grinder"


def test_899_bench_grinder_refines():
    _e, r = classify_case("WAS USING A BENCH GRINDER LAC FINGER", [(899, "grinder_unspecified")])
    assert r[0].final_tool_id == "bench_grinder"


def test_899_bare_grinder_does_not_refine():
    e, r = classify_case("GRINDING METAL WITH A GRINDER FB IN EYE", [(899, "grinder_unspecified")])
    assert r[0].final_tool_id == "grinder_unspecified"
    assert e.tool_subtype == "grinder_generic"


def test_809_planer_refines():
    _e, r = classify_case("USING AN ELECTRIC PLANER CUT FINGER", [(809, "other_power_tool")])
    assert r[0].final_tool_id == "planer"


def test_870_power_planer_refines_but_hand_plane_does_not():
    _e, r = classify_case("USING A POWER PLANER CUT HAND", [(870, "chisel_plane")])
    assert r[0].final_tool_id == "planer"
    e2, r2 = classify_case("USING A HAND PLANE CUT HAND", [(870, "chisel_plane")])
    assert r2[0].final_tool_id == "chisel_plane"
    assert e2.tool_subtype == "hand_plane"


def test_845_table_saw_and_circular_saw_refine_to_named_tool():
    _e, r = classify_case("CUT FINGER ON A TABLE SAW", [(845, "saw_unspecified")])
    assert r[0].final_tool_id == "table_saw"
    _e2, r2 = classify_case("CUT FINGER ON A CIRCULAR SAW", [(845, "saw_unspecified")])
    assert r2[0].final_tool_id == "circular_saw"


def test_845_bare_saw_does_not_refine():
    e, r = classify_case("TRIPPED OVER A SAW", [(845, "saw_unspecified")])
    assert r[0].final_tool_id == "saw_unspecified"
    assert e.tool_subtype == "unknown"


def test_845_conflicting_saw_terms_do_not_refine():
    _e, r = classify_case(
        "PT WAS USING A TABLE SAW AND ALSO A CIRCULAR SAW", [(845, "saw_unspecified")]
    )
    assert r[0].final_tool_id == "saw_unspecified"
    assert r[0].narrative_refined_tool is None


def test_847_drill_press_and_portable_drill_refine():
    _e, r = classify_case("USING A DRILL PRESS CUT FINGER", [(847, "drill_unspecified")])
    assert r[0].final_tool_id == "drill_press"
    _e2, r2 = classify_case("USING A CORDLESS DRILL CUT FINGER", [(871, "drill_power_unspecified")])
    assert r2[0].final_tool_id == "drill_portable"


def test_862_belt_sander_subtype_only_no_refinement_target():
    e, r = classify_case("USING A BELT SANDER INJURED HAND", [(862, "manual_sanding")])
    assert r[0].final_tool_id == "manual_sanding"
    assert e.tool_subtype == "belt_sander"
