"""D-19/NARR-06 structural checks on the blind-labeled validation fixture. This test reads the
already-committed fixture CSV; it never runs classify_case/RuleProvider against it (that would
defeat the blindness protocol the fixture exists to prove)."""

from __future__ import annotations

import csv
import re

from wii.narratives.rules import REFINED_TOOLS
from wii.narratives.schema import (
    ACTIVITIES,
    GUARD_STATES,
    MAINTENANCE_CONTEXTS,
    MECHANISMS,
    POWER_STATES,
    PPE_STATES,
)
from wii.taxonomy.loader import load_product_taxonomy
from wii.utils.paths import repo_root

FIXTURE_PATH = repo_root() / "tests" / "fixtures" / "narratives" / "labeled_validation.csv"
GUIDE_PATH = repo_root() / "docs" / "labeling_guide.md"

BROAD_CODES = {895, 809, 845, 847, 871, 899, 862, 870}


def _load_rows() -> list[dict]:
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_at_least_150_rows_at_least_110_holdout_exactly_40_dev():
    rows = _load_rows()
    assert len(rows) >= 150
    holdout = [r for r in rows if r["split"] == "holdout"]
    dev = [r for r in rows if r["split"] == "dev"]
    assert len(holdout) >= 110
    assert len(dev) == 40


def test_unique_case_ids():
    rows = _load_rows()
    case_ids = [r["case_id"] for r in rows]
    assert len(case_ids) == len(set(case_ids))


def test_label_values_inside_schema_vocabularies():
    rows = _load_rows()
    for r in rows:
        assert r["label_activity"] in ACTIVITIES, r["label_activity"]
        assert r["label_primary_mechanism"] in MECHANISMS, r["label_primary_mechanism"]
        assert r["label_ppe_mentioned"] in PPE_STATES, r["label_ppe_mentioned"]
        assert r["label_guard_mentioned"] in GUARD_STATES, r["label_guard_mentioned"]
        mc = r["label_maintenance_context"]
        assert mc in MAINTENANCE_CONTEXTS, mc
        ps = r["label_power_state_mentioned"]
        assert ps in POWER_STATES, ps
        for m in (r["label_secondary_mechanisms"] or "").split(";"):
            if m:
                assert m in MECHANISMS, m


def test_maintenance_context_label_consistent_with_maintenance_flag():
    rows = _load_rows()
    for r in rows:
        is_maintenance = r["label_maintenance_or_setup"].strip().upper() == "TRUE"
        context_set = r["label_maintenance_context"] != "not_maintenance"
        assert context_set == is_maintenance, (
            f"case {r['case_id']}: label_maintenance_or_setup={r['label_maintenance_or_setup']!r} "
            f"but label_maintenance_context={r['label_maintenance_context']!r}"
        )


def test_final_tool_id_in_official_or_refined_tools():
    tax = load_product_taxonomy()
    official = set(tax.tools.keys())
    valid = official | REFINED_TOOLS
    rows = _load_rows()
    for r in rows:
        assert r["label_final_tool_id"] in valid, r["label_final_tool_id"]


def test_final_tool_id_differs_from_official_only_for_broad_codes():
    tax = load_product_taxonomy()
    rows = _load_rows()
    for r in rows:
        code = int(r["official_product_code"])
        official_tool_id = tax.codes[code].tool_id
        if code not in BROAD_CODES:
            assert r["label_final_tool_id"] == official_tool_id, (
                f"case {r['case_id']}: non-broad code {code} was refined to "
                f"{r['label_final_tool_id']!r}"
            )


def _matches(rows: list[dict], predicate) -> int:
    return sum(1 for r in rows if predicate(r))


def test_stratification_minimums_met():
    rows = _load_rows()
    narratives = [r["narrative"].upper() for r in rows]
    codes = [int(r["official_product_code"]) for r in rows]

    broad_count = sum(1 for c in codes if c in BROAD_CODES)
    assert broad_count >= 60, broad_count

    for code in BROAD_CODES:
        count = sum(1 for c in codes if c == code)
        assert count >= 5, f"broad code {code} has only {count} cases (need >= 5)"

    chop_saw = sum(1 for n in narratives if "CHOP SAW" in n)
    assert chop_saw >= 12, chop_saw

    kickback = sum(1 for n in narratives if "KICKBACK" in n or "KICKED BACK" in n)
    assert kickback >= 12, kickback

    negation = sum(
        1
        for n in narratives
        if "DENIES" in n or re.search(r"\bNO\b", n) or "W/O" in n or "NEGATIVE" in n
    )
    assert negation >= 12, negation

    negative_imaging = sum(1 for n in narratives if "NEGATIVE" in n)
    assert negative_imaging >= 4, negative_imaging

    bare_saw_845 = sum(
        1
        for r, n in zip(rows, narratives, strict=True)
        if int(r["official_product_code"]) == 845 and "SAW" in n
    )
    assert bare_saw_845 >= 8, bare_saw_845

    ppe_guard = sum(
        1
        for n in narratives
        if any(k in n for k in ("GLASSES", "GOGGLES", "GLOVES", "GUARD", "PROTECTION"))
    )
    assert ppe_guard >= 12, ppe_guard

    maintenance = sum(
        1 for n in narratives if any(k in n for k in ("CHANGING", "CLEANING", "ADJUSTING"))
    )
    assert maintenance >= 8, maintenance

    nail_gun = sum(1 for n in narratives if "NAIL GUN" in n or "NAILER" in n)
    assert nail_gun >= 8, nail_gun

    entanglement = sum(1 for n in narratives if "CAUGHT" in n)
    assert entanglement >= 4, entanglement

    fall_trip = sum(1 for n in narratives if "FELL" in n or "TRIPPED" in n)
    assert fall_trip >= 5, fall_trip

    families = [r["official_tool_family"] for r in rows]
    for family in ("saws", "cutting_shaping", "abrasive", "drilling_fastening", "manual"):
        assert families.count(family) >= 10, (family, families.count(family))


def test_disclosure_sentence_present_in_labeling_guide():
    """The labels are not clinician-produced and the guide has to say so. The wording was revised
    for the public release; what matters is that the provenance and the blindness both survive."""
    text = GUIDE_PATH.read_text(encoding="utf-8")
    assert "with an AI assistant" in text
    assert "blind to the classifier's output" in text
    assert "not by a clinician, epidemiologist or trained NEISS coder" in text
