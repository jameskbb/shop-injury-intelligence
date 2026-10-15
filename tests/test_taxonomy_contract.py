"""Taxonomy config contract (TAX-01 traceability gap, D-01, 06-03 Task 2).

Asserts the shipped `product_taxonomy.yaml`, loaded via the project's own loader (no CPSC
data), satisfies the structural TAX-01 requirements: every in-scope code maps to exactly one
tool in one of the defined families, no code is both in-scope and excluded, the excluded
categories named in TAX-01 (chain saws, yard tools) stay out of scope, and the config carries a
version identifier. The one non-machine-readable requirement -- every in-scope code validated
against the January 2025 NEISS Product Code Comparability Table -- is a documented manual
verification (see docs/testing.md's Tool taxonomy row), not an automated assertion against a
PDF.
"""

from __future__ import annotations

from wii.taxonomy.loader import CONFIG_DIR, load_product_taxonomy, load_yaml

EXPECTED_FAMILY_LABELS = {
    "Saws",
    "Cutting / Shaping",
    "Abrasive",
    "Drilling / Fastening",
    "Manual",
}


def test_every_in_scope_code_maps_to_exactly_one_tool_in_a_defined_family():
    tax = load_product_taxonomy()
    family_labels = {f.label for f in tax.families.values()}
    assert family_labels == EXPECTED_FAMILY_LABELS

    for code, product_code in tax.codes.items():
        if not product_code.in_scope:
            continue
        tool_id = product_code.tool_id
        assert tool_id in tax.tools, f"code {code} maps to undefined tool {tool_id!r}"
        tool = tax.tools[tool_id]
        assert tool.family_id in tax.families, (
            f"tool {tool_id!r} (code {code}) belongs to undefined family {tool.family_id!r}"
        )


def test_no_code_is_both_in_scope_and_excluded():
    tax = load_product_taxonomy()
    in_scope_codes = {c for c, pc in tax.codes.items() if pc.in_scope}
    adjacent_codes = set(tax.adjacent.keys())
    overlap = in_scope_codes & adjacent_codes
    assert overlap == set(), f"code(s) {overlap} are both in-scope and adjacent/excluded"


def test_excluded_categories_named_in_tax_01_are_not_in_scope():
    tax = load_product_taxonomy()
    in_scope_codes = {c for c, pc in tax.codes.items() if pc.in_scope}

    # Chain saws (1411) are named explicitly as an adjacent/excluded code.
    chain_saw_codes = {a.code for a in tax.adjacent.values() if "chain saw" in a.label.lower()}
    assert chain_saw_codes, "no adjacent entry names chain saws"
    assert chain_saw_codes.isdisjoint(in_scope_codes)

    # Yard/garden power equipment is excluded by construction: membership in `codes` (the only
    # source of in-scope product codes) is an explicit allow-list, so no yard-tool code can ever
    # appear there. The raw config documents this rationale in `excluded_categories.yard_tools`.
    raw_doc = load_yaml(CONFIG_DIR / "product_taxonomy.yaml")
    yard_tools_note = raw_doc["excluded_categories"]["yard_tools"]["note"]
    assert "allow-list" in yard_tools_note
    assert not any("yard" in pc.official_name.lower() for pc in tax.codes.values() if pc.in_scope)


def test_config_carries_a_version_identifier():
    tax = load_product_taxonomy()
    assert isinstance(tax.version, int)
    assert tax.version >= 1
    assert isinstance(tax.validated_against, str) and tax.validated_against
