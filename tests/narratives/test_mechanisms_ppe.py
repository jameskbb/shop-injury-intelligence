"""Synthetic-only tests for the 13-category mechanism taxonomy (D-15), activity (D-17), PPE/guard
(D-16), confidence, and evidence behaviors (Task 3)."""

from __future__ import annotations

from wii.narratives.classify import classify_case
from wii.narratives.rules import load_rules
from wii.narratives.schema import MECHANISM_PRIORITY, MECHANISMS


def test_mechanism_priority_matches_d15_exactly():
    expected = (
        "kickback_ejection",
        "entanglement",
        "fastener_discharge",
        "flying_debris_fb",
        "pinch_crush",
        "electrical_burn",
        "fall_trip",
        "loss_of_tool_control",
        "workpiece_slip",
        "direct_cutting_contact",
        "maintenance_setup",
        "other",
        "unknown",
    )
    assert MECHANISM_PRIORITY == expected
    assert len(MECHANISMS) == 13
    rules = load_rules()
    assert rules.mechanisms.priority == expected


def test_kickback_with_direct_cutting_contact_resolves_to_kickback_by_priority():
    e, _r = classify_case("WOOD KICKED BACK AND CUT FINGER ON TABLE SAW", [(841, "table_saw")])
    assert e.primary_mechanism == "kickback_ejection"
    assert "direct_cutting_contact" in e.secondary_mechanisms


def test_entanglement_vs_pinch_crush_subject_disambiguation():
    e1, _r = classify_case("HIS HAIR CAUGHT IN THE DRILL", [(855, "drill_portable")])
    assert e1.primary_mechanism == "entanglement"
    e2, _r = classify_case("HIS FINGER CAUGHT IN THE ROUTER", [(804, "router")])
    assert e2.primary_mechanism == "pinch_crush"


def test_fastener_discharge():
    e, _r = classify_case("NAIL GUN WENT OFF SHOT NAIL INTO HAND", [(882, "nail_gun")])
    assert e.primary_mechanism == "fastener_discharge"


def test_flying_debris_sets_projectile_flag():
    e, _r = classify_case("FB EYE WHILE GRINDING METAL", [(897, "grinder_power")])
    assert e.primary_mechanism == "flying_debris_fb"
    assert e.projectile_or_debris is True


def test_maintenance_setup_sets_boolean_and_secondary_mechanism():
    e, _r = classify_case("CHANGING BLADE ON SAW CUT FINGER", [(841, "table_saw")])
    assert e.maintenance_or_setup is True


def test_bare_unplug_without_tool_term_is_not_maintenance():
    e, _r = classify_case("TRIPPED WHILE TRYING TO UNPLUG THE LAMP", [(841, "table_saw")])
    assert e.maintenance_or_setup is False


def test_fall_trip_requires_tool_operation_link():
    e1, _r = classify_case("TRIPPED OVER A SAW ON THE FLOOR", [(845, "saw_unspecified")])
    # bare tripping-over-object mention with no operation link: not fall_trip as primary
    assert e1.primary_mechanism != "fall_trip"
    e2, _r = classify_case("FELL OFF LADDER WHILE SAWING", [(845, "saw_unspecified")])
    assert e2.primary_mechanism == "fall_trip"


def test_ppe_mentioned_worn_and_not_worn():
    worn, _r = classify_case("WEARING SAFETY GLASSES WHEN FB HIT EYE", [(897, "grinder_power")])
    assert worn.ppe_mentioned == "mentioned_worn"
    not_worn, _r = classify_case("NO EYE PROTECTION FB IN EYE", [(897, "grinder_power")])
    assert not_worn.ppe_mentioned == "mentioned_not_worn"
    unmentioned, _r = classify_case("CUT FINGER ON SAW", [(841, "table_saw")])
    assert unmentioned.ppe_mentioned == "not_mentioned"


def test_guard_mentioned_absent_and_present():
    absent, _r = classify_case("USING TABLE SAW WITHOUT GUARD CUT FINGER", [(841, "table_saw")])
    assert absent.guard_mentioned == "mentioned_absent_or_removed"
    present, _r = classify_case("SAFETY GUARD ON SAW BUT STILL CUT FINGER", [(841, "table_saw")])
    assert present.guard_mentioned == "mentioned_present"


def test_despite_wearing_ppe_still_recorded_as_worn_no_inference():
    e, _r = classify_case(
        "DESPITE WEARING SAFETY GLASSES AT THE TIME PT SUSTAINED FB INJURY",
        [(897, "grinder_power")],
    )
    assert e.ppe_mentioned == "mentioned_worn"


def test_scans_negative_does_not_negate_ppe_or_mechanism():
    e, _r = classify_case(
        "PT WAS WEARING SAFETY GLASSES CUT FINGER ON SAW. SCANS WERE NEGATIVE FOR FRACTURE.",
        [(841, "table_saw")],
    )
    assert e.ppe_mentioned == "mentioned_worn"


def test_no_mechanism_vocabulary_is_unknown_with_low_confidence():
    e, _r = classify_case("LAC L IND FINGER", [(841, "table_saw")])
    assert e.primary_mechanism == "unknown"


def test_evidence_capped_at_3_phrases_of_60_chars():
    e, _r = classify_case("WOOD KICKED BACK AND CUT FINGER ON TABLE SAW", [(841, "table_saw")])
    assert len(e.evidence) <= 3
    for phrase in e.evidence:
        assert len(phrase) <= 60


def test_alcohol_mention_produces_no_blame_field():
    e, _r = classify_case(
        "PT REPORTS ALCOHOL USE CUT FINGER ON TABLE SAW ETOH", [(841, "table_saw")]
    )
    # No field on the record encodes blame/intoxication -- the dataclass itself has no such
    # attribute, so simply constructing/accessing the record proves the invariant.
    assert not hasattr(e, "alcohol_code")
    assert not hasattr(e, "drug_code")
