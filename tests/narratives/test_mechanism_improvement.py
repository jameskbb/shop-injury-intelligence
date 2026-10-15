"""rules-1.1.0 mechanism-detection improvement: recognize cutting-injury verbs (LACERAT*, CUT,
SLICED, AMPUTAT*, NICKED, CAUGHT BY/ON BLADE) as direct_cutting_contact at MODERATE confidence,
restricted to narratives that also mention a cutting-tool context term (SAW, BLADE, CHISEL,
KNIFE, ...) -- never a bare verb match with no tool context at all."""

from __future__ import annotations

from wii.narratives import CLASSIFIER_VERSION
from wii.narratives.classify import classify_case


def test_classifier_version_is_rules_1_2_0():
    assert CLASSIFIER_VERSION == "rules-1.2.0"


def test_laceration_verb_on_saw_is_direct_cutting_contact():
    e, _r = classify_case("LACERATED FINGER ON TABLE SAW", [(841, "table_saw")])
    assert e.primary_mechanism == "direct_cutting_contact"


def test_amputation_verb_with_chisel_is_direct_cutting_contact():
    e, _r = classify_case("AMPUTATED THUMB WHILE USING CHISEL", [(870, "chisel_plane")])
    assert e.primary_mechanism == "direct_cutting_contact"


def test_nicked_verb_on_blade_is_direct_cutting_contact():
    e, _r = classify_case("NICKED HAND ON SAW BLADE WHILE CUTTING", [(841, "table_saw")])
    assert e.primary_mechanism == "direct_cutting_contact"


def test_caught_on_blade_is_direct_cutting_contact():
    e, _r = classify_case("FINGER CAUGHT ON BLADE OF SAW", [(841, "table_saw")])
    assert e.primary_mechanism == "direct_cutting_contact"


def test_cutting_verb_requires_cutting_tool_context():
    """A bare cutting verb with NO cutting-tool context word anywhere in the narrative must not
    be classified as direct_cutting_contact -- this is the "on cutting-tool cases" restriction."""
    e, _r = classify_case("LACERATED FOOT ON BROKEN GLASS IN GARAGE", [(841, "table_saw")])
    assert e.primary_mechanism != "direct_cutting_contact"


def test_negated_cutting_verb_is_not_direct_cutting_contact():
    e, _r = classify_case(
        "PT DENIES LACERATION, TWISTED ANKLE NEAR TABLE SAW", [(841, "table_saw")]
    )
    assert e.primary_mechanism != "direct_cutting_contact"
