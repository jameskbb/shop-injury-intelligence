"""Synthetic-only tests for classify_case / RuleProvider (Task 1 tracer scope). Never reads
real CPSC narrative data -- that would violate Task 2's blindness protocol for the labeled
validation fixture.
"""

from __future__ import annotations

import dataclasses

import pytest

from wii.narratives import CLASSIFIER_VERSION, classify
from wii.narratives.classify import classify_case
from wii.narratives.normalize import normalize
from wii.narratives.providers import RuleProvider
from wii.narratives.rules import load_rules, match_phrases
from wii.narratives.schema import NarrativeEnrichment, validate


def test_miter_saw_end_to_end_via_rule_provider():
    enrichment, refinements = RuleProvider().classify(
        "43YOM USING A MITER SAW CUT FINGER", [(895, "other_power_saw")]
    )
    assert enrichment.tool_subtype == "miter_saw"
    assert refinements[0].final_tool_id == "miter_saw"
    assert enrichment.confidence >= 0.70
    assert enrichment.classification_source == "rules"
    assert enrichment.classifier_version == CLASSIFIER_VERSION


def test_specific_code_is_never_overridden_by_narrative():
    enrichment, refinements = classify_case(
        "43YOM USING A MITER SAW CUT FINGER", [(841, "table_saw")]
    )
    assert refinements[0].final_tool_id == "table_saw"
    assert refinements[0].narrative_refined_tool is None
    assert enrichment.confidence >= 0.0


def test_chop_saw_wood_context_refines_to_miter_saw():
    _enrichment, refinements = classify_case("USED A CHOP SAW ON A 2X4", [(895, "other_power_saw")])
    assert refinements[0].final_tool_id == "miter_saw"
    assert refinements[0].narrative_refined_tool == "miter_saw"


def test_chop_saw_metal_context_does_not_refine():
    enrichment, refinements = classify_case(
        "CHOP SAW ON ALUMINUM KICKED BACK", [(895, "other_power_saw")]
    )
    assert refinements[0].final_tool_id == "other_power_saw"
    assert refinements[0].narrative_refined_tool is None
    assert enrichment.tool_subtype == "chop_saw_metal"


@pytest.mark.parametrize("narrative", [None, "", "   "])
def test_empty_narrative_returns_unknown_and_official_tool(narrative):
    enrichment, refinements = classify_case(narrative, [(895, "other_power_saw")])
    assert enrichment.tool_subtype == "unknown"
    assert enrichment.primary_mechanism == "unknown"
    assert enrichment.confidence == 0.0
    assert refinements[0].narrative_refined_tool is None
    assert refinements[0].final_tool_id == "other_power_saw"


def test_cp1252_narrative_classifies_without_error():
    narrative = "43YOM USING MITER SAW CUT FINGER ®, DX: LAC Æ"
    enrichment, _refinements = classify_case(narrative, [(895, "other_power_saw")])
    assert enrichment.tool_subtype == "miter_saw"


def test_redaction_placeholder_never_matches_surrounding_word():
    rules = load_rules()
    glued = normalize("USING A ***SAW TO CUT WOOD", rules.abbreviations)
    assert match_phrases(("SAW",), "saw_test", glued) == []

    spaced = normalize("USING A CIRCULAR *** SAW TO CUT WOOD", rules.abbreviations)
    assert len(match_phrases(("SAW",), "saw_test", spaced)) == 1


def test_negated_miter_saw_produces_no_refinement():
    _enrichment, refinements = classify_case("NO MITER SAW USE NOTED", [(895, "other_power_saw")])
    assert refinements[0].narrative_refined_tool is None
    assert refinements[0].final_tool_id == "other_power_saw"


def test_body_part_terms_unbalanced_paren_does_not_raise_re_error():
    """WR-03: a `body_part_terms` config value containing an unescaped regex metacharacter must
    not raise `re.error` -- prior to the fix, `"(FINGER"` compiled to `r"\b(FINGER\b"`, an
    unterminated group, raising `re.error: missing ), unterminated subpattern`."""
    rules = load_rules()
    synthetic_mechanisms = dataclasses.replace(rules.mechanisms, body_part_terms=("(FINGER",))
    synthetic_rules = dataclasses.replace(rules, mechanisms=synthetic_mechanisms)
    normalized = normalize("CUT NEAR (FINGER WITH SAW", rules.abbreviations)

    # Must not raise re.error -- this is the regression this test guards against.
    classify._workpiece_event_and_flags(normalized, "direct_cutting_contact", synthetic_rules)


def test_body_part_terms_metacharacter_matched_as_literal_text():
    """WR-03: a config term containing a regex metacharacter must be matched as a literal
    phrase, not interpreted as regex syntax -- prior to the fix, the `+` in `"TOE+NAIL"` was a
    quantifier (one-or-more of the preceding char), so it would never match the literal text
    `"TOE+NAIL"`."""
    rules = load_rules()
    synthetic_mechanisms = dataclasses.replace(rules.mechanisms, body_part_terms=("TOE+NAIL",))
    synthetic_rules = dataclasses.replace(rules, mechanisms=synthetic_mechanisms)
    normalized = normalize("INJURED TOE+NAIL WITH CHISEL", rules.abbreviations)

    _event, _projectile, _direct, body_contact_context = classify._workpiece_event_and_flags(
        normalized, "direct_cutting_contact", synthetic_rules
    )
    assert body_contact_context == "toe+nail"


def test_todays_body_part_terms_config_unaffected_by_escaping():
    """Confirms the WR-03 fix is behavior-preserving for today's real `mechanisms.yaml`
    `body_part_terms` config (no regex metacharacters), so CLASSIFIER_VERSION does not need a
    bump and no data regeneration is required."""
    enrichment, _refinements = classify_case(
        "43YOM CUT FINGER WITH TABLE SAW", [(841, "table_saw")]
    )
    assert enrichment.body_contact_context == "finger"


def test_schema_validate_rejects_out_of_vocabulary_mechanism():
    bad = NarrativeEnrichment(
        tool_subtype="unknown",
        activity="unknown",
        primary_mechanism="not_a_real_mechanism",
        secondary_mechanisms=(),
        maintenance_or_setup=False,
        workpiece_event=None,
        projectile_or_debris=False,
        direct_tool_contact=False,
        body_contact_context=None,
        ppe_mentioned="not_mentioned",
        guard_mentioned="not_mentioned",
        confidence=0.0,
        evidence=(),
        classification_source="rules",
        classifier_version="rules-1.0.0",
    )
    with pytest.raises(ValueError):
        validate(bad)
