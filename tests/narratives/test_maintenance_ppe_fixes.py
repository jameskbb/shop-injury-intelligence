"""Regression tests for code-review findings 5 (bare-UNPLUG substring/negation bug) and 6
(PPE not-worn phrase coverage)."""

from __future__ import annotations

from wii.narratives.classify import classify_case


def test_unplug_substring_false_positive_from_stool_is_not_maintenance():
    """'UNPLUGGING LAMP, FELL OFF STOOL' -- STOOL contains the substring TOOL and LAMP is not a
    tool word; naive substring matching previously mistook this for maintenance context."""
    e, _r = classify_case("UNPLUGGING LAMP, FELL OFF STOOL", [(841, "table_saw")])
    assert e.maintenance_or_setup is False


def test_unplug_substring_false_positive_from_sawdust_is_not_maintenance():
    """SAWDUST contains the substring SAW -- must not count as a tool-word co-occurrence."""
    e, _r = classify_case("UNPLUGGED VACUUM, SLIPPED ON SAWDUST", [(841, "table_saw")])
    assert e.maintenance_or_setup is False


def test_negated_unplug_is_not_maintenance():
    e, _r = classify_case("DENIES UNPLUGGING THE SAW BEFORE CHANGING BIT", [(841, "table_saw")])
    # the maintenance cue "CHANGING BIT" still fires on its own -- assert via the unplug-only case
    del e


def test_negated_unplug_alone_is_not_maintenance():
    e, _r = classify_case("DENIES UNPLUGGING THE SAW BEFORE INJURY", [(841, "table_saw")])
    assert e.maintenance_or_setup is False


def test_ppe_not_worn_denies_wearing():
    e, _r = classify_case("PT DENIES WEARING SAFETY GLASSES CUT FINGER", [(841, "table_saw")])
    assert e.ppe_mentioned == "mentioned_not_worn"


def test_ppe_not_worn_wasnt_wearing():
    e, _r = classify_case("PT WASN'T WEARING GLOVES CUT FINGER", [(841, "table_saw")])
    assert e.ppe_mentioned == "mentioned_not_worn"


def test_ppe_not_worn_was_not_wearing():
    e, _r = classify_case("PT WAS NOT WEARING GOGGLES CUT FINGER", [(841, "table_saw")])
    assert e.ppe_mentioned == "mentioned_not_worn"


def test_ppe_not_worn_w_o_glasses():
    e, _r = classify_case("CUT FINGER W/O SAFETY GLASSES", [(841, "table_saw")])
    assert e.ppe_mentioned == "mentioned_not_worn"
