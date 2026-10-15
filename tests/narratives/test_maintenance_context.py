"""D-14 tests for maintenance_context / power_state_mentioned: negation, explicit-only power
state, primary_mechanism invariance, and the maintenance_or_setup <-> context invariant.
Synthetic-only for Task 1's tracer scope (matches test_classify.py's own convention); Task 3
adds real-committed-data invariance tests separately.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from wii.narratives.classify import classify_case
from wii.narratives.rules import CONFIG_DIR, RuleConfigError, load_rules
from wii.narratives.schema import NarrativeEnrichment, validate


def _base_record(**overrides) -> NarrativeEnrichment:
    fields = dict(
        tool_subtype="unknown",
        activity="unknown",
        primary_mechanism="unknown",
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
        classifier_version="rules-1.1.0",
    )
    fields.update(overrides)
    return NarrativeEnrichment(**fields)


def test_tracer_blade_change_unplugged():
    enrichment, _refinements = classify_case(
        "CHANGING THE BLADE ON TABLE SAW, SAW WAS UNPLUGGED, LACERATED FINGER",
        [(841, "table_saw")],
    )
    assert enrichment.maintenance_context == "blade_change"
    assert enrichment.power_state_mentioned == "mentioned_unplugged"
    assert enrichment.maintenance_or_setup is True


def test_empty_narrative_defaults():
    enrichment, _refinements = classify_case("", [(841, "table_saw")])
    assert enrichment.maintenance_context == "not_maintenance"
    assert enrichment.power_state_mentioned == "not_mentioned"


def test_validate_rejects_unknown_maintenance_context():
    bad = replace(_base_record(), maintenance_context="oiling")
    with pytest.raises(ValueError, match="maintenance_context"):
        validate(bad)


def test_validate_rejects_unknown_power_state():
    bad = replace(_base_record(), power_state_mentioned="off")
    with pytest.raises(ValueError, match="power_state_mentioned"):
        validate(bad)


def test_enrich_cases_emits_new_columns():
    import polars as pl

    from wii.narratives.enrich import enrich_cases

    fact_cases = pl.DataFrame(
        {
            "case_id": ["c1"],
            "narrative": ["CHANGING THE BLADE ON TABLE SAW, SAW WAS UNPLUGGED, LACERATED FINGER"],
        }
    )
    bridge_products = pl.DataFrame(
        {
            "case_id": ["c1"],
            "product_code": [841],
            "seq": [1],
            "tool_id": ["table_saw"],
            "in_scope": [True],
            "narrative_refined_tool": [None],
            "final_tool_id": ["table_saw"],
            "classification_confidence": [None],
        },
        schema_overrides={"classification_confidence": pl.Float32},
    )
    dim_product = pl.DataFrame({"product_code": [841], "broad": [False]})

    result = enrich_cases(fact_cases, bridge_products, dim_product)

    assert "maintenance_context" in result.enrichment.columns
    assert "power_state_mentioned" in result.enrichment.columns
    row = result.enrichment.row(0, named=True)
    assert row["maintenance_context"] == "blade_change"
    assert row["power_state_mentioned"] == "mentioned_unplugged"


def test_existing_narrative_enrichment_construction_without_new_fields_still_works():
    record = _base_record()
    assert record.maintenance_context == "not_maintenance"
    assert record.power_state_mentioned == "not_mentioned"


def test_rules_loader_rejects_unknown_context_category(tmp_path):
    import shutil

    tmp_config = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, tmp_config)

    mech_path = tmp_config / "mechanisms.yaml"
    doc = yaml.safe_load(mech_path.read_text(encoding="utf-8"))
    doc["maintenance_context"]["phrases"]["not_a_real_category"] = ["FOO"]
    mech_path.write_text(yaml.safe_dump(doc), encoding="utf-8")

    with pytest.raises(RuleConfigError, match="not_a_real_category"):
        load_rules(config_dir=tmp_config)


def test_rules_loader_accepts_real_config():
    # Sanity check the real committed config loads cleanly (guards against a copy/paste error
    # in mechanisms.yaml going unnoticed because the tmp-config test above only exercises a
    # mutated copy).
    rules = load_rules()
    assert "blade_change" in rules.mechanisms.maintenance_context_phrases


# --- Task 3: negation / explicit-only / invariance (D-14, D-15) --------------------------------


def test_negated_unplug_never_sets_power_state():
    # NOTE: this narrative's 4-token negation window (normalize.py NEGATION_WINDOW) also spans
    # the start of "CLEARING JAM", so `maintenance_context` resolves to "not_maintenance" here
    # rather than "jam_clearing" -- a same-clause negation cue conservatively suppresses any
    # phrase match whose span overlaps it, which is the correct safety-conserving behavior for
    # this generic window (never inferring positive evidence from a negated span). The
    # `power_state_mentioned` contract this test names is unaffected either way.
    enrichment, _ = classify_case("DID NOT UNPLUG SAW BEFORE CLEARING JAM", [(841, "table_saw")])
    assert enrichment.power_state_mentioned == "not_mentioned"


def test_failed_to_unplug_never_sets_power_state():
    enrichment, _ = classify_case(
        "FAILED TO UNPLUG DRILL, CHANGING BIT", [(871, "drill_power_unspecified")]
    )
    assert enrichment.power_state_mentioned == "not_mentioned"
    assert enrichment.maintenance_context == "bit_change"


def test_plugged_in_explicit():
    enrichment, _ = classify_case(
        "SAW WAS STILL PLUGGED IN WHILE CHANGING BLADE", [(841, "table_saw")]
    )
    assert enrichment.power_state_mentioned == "mentioned_plugged_in"
    assert enrichment.maintenance_context == "blade_change"


def test_conflicting_power_mentions_not_mentioned():
    enrichment, _ = classify_case(
        "UNPLUGGED THE SAW THEN LATER PLUGGED IN AND CUT FINGER", [(841, "table_saw")]
    )
    assert enrichment.power_state_mentioned == "not_mentioned"


def test_no_tool_context_is_not_maintenance():
    enrichment, _ = classify_case("CLEANING GUTTERS FELL OFF LADDER", [(841, "table_saw")])
    assert enrichment.maintenance_context == "not_maintenance"
    assert enrichment.maintenance_or_setup is False


def test_jammed_finger_is_not_jam_clearing():
    enrichment, _ = classify_case("JAMMED FINGER IN DOOR", [(841, "table_saw")])
    assert enrichment.maintenance_context == "not_maintenance"


def test_cleaning_drill_is_cleaning():
    enrichment, _ = classify_case(
        "PT CLEANING HIS DRILL AND CUT HAND", [(871, "drill_power_unspecified")]
    )
    assert enrichment.maintenance_context == "cleaning"


def test_primary_mechanism_unchanged_on_fixture_cases():
    """The new maintenance_context/power_state phrase tables are a separate config section
    never read by mechanism scoring -- classify_case's primary_mechanism for every labeled-
    fixture narrative must equal the committed narrative_enrichment's primary_mechanism for
    that case_id (T-04-09)."""
    import csv

    from wii.metrics import connect
    from wii.utils.paths import repo_root

    fixture_path = repo_root() / "tests" / "fixtures" / "narratives" / "labeled_validation.csv"
    with open(fixture_path, encoding="utf-8") as f:
        fixture_rows = list(csv.DictReader(f))

    con = connect()
    rows = con.execute("SELECT case_id, primary_mechanism FROM narrative_enrichment").fetchall()
    committed = {case_id: mechanism for case_id, mechanism in rows}

    checked = 0
    for row in fixture_rows:
        case_id = row["case_id"]
        committed_mechanism = committed.get(case_id)
        if committed_mechanism is None:
            continue
        code = int(row["official_product_code"])
        enrichment, _ = classify_case(row["narrative"], [(code, row["label_final_tool_id"])])
        assert enrichment.primary_mechanism == committed_mechanism, case_id
        checked += 1
    assert checked > 0


def test_maintenance_flag_invariant_on_committed_sample():
    """For a deterministic 200-case sample of committed narratives (by case_id order),
    maintenance_or_setup == (maintenance_context != 'not_maintenance') always holds."""
    from wii.metrics import connect

    con = connect()
    rows = con.execute(
        "SELECT case_id, narrative FROM fact_cases ORDER BY case_id LIMIT 200"
    ).fetchall()
    assert len(rows) == 200
    for _case_id, narrative in rows:
        enrichment, _ = classify_case(narrative, [])
        assert enrichment.maintenance_or_setup == (
            enrichment.maintenance_context != "not_maintenance"
        )


def _config_dir() -> Path:
    return CONFIG_DIR
