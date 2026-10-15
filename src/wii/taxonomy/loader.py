"""Load and validate the versioned product_taxonomy.yaml config.

YAML is loaded exclusively via yaml.safe_load. Every validation failure names the offending
key path (e.g. "codes.841.tool_id") -- no silent defaults.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from wii.utils.paths import CONFIG_DIR

EXPECTED_IN_SCOPE_COUNT = 27


class TaxonomyConfigError(Exception):
    """Raised for any structural problem in product_taxonomy.yaml. Message names the key path."""


@dataclass(frozen=True)
class Family:
    family_id: str
    label: str
    sort_order: int


@dataclass(frozen=True)
class Tool:
    tool_id: str
    label: str
    family_id: str


@dataclass(frozen=True)
class ProductCode:
    code: int
    tool_id: str
    code_label: str
    official_name: str
    start_year: int
    comparability_note: str
    validated_against: str
    in_scope: bool
    broad: bool


@dataclass(frozen=True)
class AdjacentCode:
    code: int
    label: str
    reason: str


@dataclass(frozen=True)
class ProductTaxonomy:
    version: int
    validated_against: str
    families: dict[str, Family]
    tools: dict[str, Tool]
    codes: dict[int, ProductCode]
    adjacent: dict[int, AdjacentCode]


def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_product_taxonomy(path: Path | None = None) -> ProductTaxonomy:
    if path is None:
        path = CONFIG_DIR / "product_taxonomy.yaml"
    path = Path(path)
    if not path.is_file():
        raise TaxonomyConfigError(f"Taxonomy config file not found: {path}")

    doc = load_yaml(path)
    if not doc:
        raise TaxonomyConfigError(f"Taxonomy config file is empty: {path}")

    for key in ("version", "validated_against", "families", "tools", "codes", "adjacent"):
        if key not in doc:
            raise TaxonomyConfigError(f"Missing required key: {key}")

    families: dict[str, Family] = {}
    for family_id, fdoc in doc["families"].items():
        if not isinstance(fdoc, dict) or "label" not in fdoc:
            raise TaxonomyConfigError(f"families.{family_id}.label missing")
        if "sort_order" not in fdoc:
            raise TaxonomyConfigError(f"families.{family_id}.sort_order missing")
        families[family_id] = Family(
            family_id=family_id, label=fdoc["label"], sort_order=fdoc["sort_order"]
        )

    tools: dict[str, Tool] = {}
    for tool_id, tdoc in doc["tools"].items():
        if not isinstance(tdoc, dict) or "label" not in tdoc:
            raise TaxonomyConfigError(f"tools.{tool_id}.label missing")
        family_id = tdoc.get("family")
        if family_id is None:
            raise TaxonomyConfigError(f"tools.{tool_id}.family missing")
        if family_id not in families:
            raise TaxonomyConfigError(
                f"tools.{tool_id}.family references undefined family {family_id!r}"
            )
        tools[tool_id] = Tool(tool_id=tool_id, label=tdoc["label"], family_id=family_id)

    used_families = {t.family_id for t in tools.values()}
    for family_id in families:
        if family_id not in used_families:
            raise TaxonomyConfigError(f"families.{family_id} has no tools")

    codes: dict[int, ProductCode] = {}
    for code_key, cdoc in doc["codes"].items():
        code = int(code_key)
        if not isinstance(cdoc, dict):
            raise TaxonomyConfigError(f"codes.{code} must be a mapping")
        for required in ("tool_id", "code_label", "official_name", "start_year", "in_scope"):
            if required not in cdoc:
                raise TaxonomyConfigError(f"codes.{code}.{required} missing")
        tool_id = cdoc["tool_id"]
        if tool_id not in tools:
            raise TaxonomyConfigError(f"codes.{code}.tool_id references undefined tool {tool_id!r}")
        codes[code] = ProductCode(
            code=code,
            tool_id=tool_id,
            code_label=cdoc["code_label"],
            official_name=cdoc["official_name"],
            start_year=cdoc["start_year"],
            comparability_note=cdoc.get("comparability_note", ""),
            validated_against=cdoc.get("validated_against", doc["validated_against"]),
            in_scope=bool(cdoc["in_scope"]),
            broad=bool(cdoc.get("broad", False)),
        )

    adjacent: dict[int, AdjacentCode] = {}
    for code_key, adoc in doc["adjacent"].items():
        code = int(code_key)
        if not isinstance(adoc, dict) or "label" not in adoc:
            raise TaxonomyConfigError(f"adjacent.{code}.label missing")
        if "reason" not in adoc:
            raise TaxonomyConfigError(f"adjacent.{code}.reason missing")
        if code in codes:
            raise TaxonomyConfigError(f"code {code} present in both codes and adjacent")
        adjacent[code] = AdjacentCode(code=code, label=adoc["label"], reason=adoc["reason"])

    in_scope_set = {c for c, pc in codes.items() if pc.in_scope}
    if len(in_scope_set) != EXPECTED_IN_SCOPE_COUNT:
        raise TaxonomyConfigError(
            f"in-scope code set has {len(in_scope_set)} codes, expected {EXPECTED_IN_SCOPE_COUNT}"
        )

    return ProductTaxonomy(
        version=doc["version"],
        validated_against=doc["validated_against"],
        families=families,
        tools=tools,
        codes=codes,
        adjacent=adjacent,
    )


def in_scope_codes(tax: ProductTaxonomy) -> frozenset[int]:
    """D-03: the set of product codes that put a case in scope."""
    return frozenset(c for c, pc in tax.codes.items() if pc.in_scope)


@dataclass(frozen=True)
class CodeEntry:
    code: int
    label: str
    manual_page: int
    attrs: dict[str, object]


@dataclass(frozen=True)
class CodeDictionary:
    name: str
    version: int
    source: dict
    entries: dict[int, CodeEntry]

    def codes(self) -> frozenset[int]:
        return frozenset(self.entries.keys())


def load_code_dictionary(
    name: str,
    required_attrs: tuple[str, ...] = (),
    *,
    config_dir: Path | None = None,
) -> CodeDictionary:
    """Generic validated code-dictionary loader (D-15). Reads CONFIG_DIR/{name}.yaml.

    Every code entry requires `label` and `manual_page`; any name in `required_attrs` must
    also be present on every code. Validation errors name the full key path, e.g.
    "diagnoses.codes.59.injury_category".
    """
    path = (config_dir or CONFIG_DIR) / f"{name}.yaml"
    if not path.is_file():
        raise TaxonomyConfigError(f"{name}: config file not found: {path}")

    doc = load_yaml(path)
    if not doc:
        raise TaxonomyConfigError(f"{name}: config file is empty: {path}")

    for key in ("version", "source", "codes"):
        if key not in doc:
            raise TaxonomyConfigError(f"{name}.{key} missing")

    codes_doc = doc["codes"]
    if not codes_doc:
        raise TaxonomyConfigError(f"{name}.codes missing or empty")

    entries: dict[int, CodeEntry] = {}
    for code_key, cdoc in codes_doc.items():
        try:
            code = int(code_key)
        except (TypeError, ValueError) as e:
            raise TaxonomyConfigError(f"{name}.codes.{code_key} is not an integer code") from e
        if not isinstance(cdoc, dict):
            raise TaxonomyConfigError(f"{name}.codes.{code} must be a mapping")
        if not cdoc.get("label"):
            raise TaxonomyConfigError(f"{name}.codes.{code}.label missing")
        if "manual_page" not in cdoc or cdoc["manual_page"] is None:
            raise TaxonomyConfigError(f"{name}.codes.{code}.manual_page missing")

        attrs: dict[str, object] = {}
        for required in required_attrs:
            if required not in cdoc or cdoc[required] is None:
                raise TaxonomyConfigError(f"{name}.codes.{code}.{required} missing")
            attrs[required] = cdoc[required]

        entries[code] = CodeEntry(
            code=code,
            label=cdoc["label"],
            manual_page=int(cdoc["manual_page"]),
            attrs=attrs,
        )

    return CodeDictionary(name=name, version=doc["version"], source=doc["source"], entries=entries)


@dataclass(frozen=True)
class BodyRegion:
    region_id: str
    label: str
    kind: str  # "region" | "callout"
    view: str
    svg_element_ids: list[str]
    body_part_codes: list[int]
    sort_order: int


@dataclass(frozen=True)
class BodyRegions:
    version: int
    regions: dict[str, BodyRegion]  # keyed by region_id; includes both regions and callouts
    rollups: dict[str, list[int]]


def _parse_body_region_group(
    group_doc: dict, kind: str, seen_codes: dict[int, list[str]]
) -> dict[str, BodyRegion]:
    out: dict[str, BodyRegion] = {}
    for region_id, rdoc in group_doc.items():
        if not isinstance(rdoc, dict) or not rdoc.get("label"):
            raise TaxonomyConfigError(f"body_regions.{kind}s.{region_id}.label missing")
        codes = rdoc.get("body_part_codes")
        if not codes:
            raise TaxonomyConfigError(
                f"body_regions.{kind}s.{region_id}.body_part_codes missing or empty"
            )
        view = rdoc.get("view")
        if not view:
            raise TaxonomyConfigError(f"body_regions.{kind}s.{region_id}.view missing")
        if "sort_order" not in rdoc:
            raise TaxonomyConfigError(f"body_regions.{kind}s.{region_id}.sort_order missing")
        svg_ids = rdoc.get("svg_element_ids")
        if svg_ids is None:
            raise TaxonomyConfigError(f"body_regions.{kind}s.{region_id}.svg_element_ids missing")

        for code in codes:
            seen_codes.setdefault(int(code), []).append(region_id)

        out[region_id] = BodyRegion(
            region_id=region_id,
            label=rdoc["label"],
            kind=kind,
            view=view,
            svg_element_ids=list(svg_ids),
            body_part_codes=[int(c) for c in codes],
            sort_order=int(rdoc["sort_order"]),
        )
    return out


def load_body_regions(body_parts: CodeDictionary, *, config_dir: Path | None = None) -> BodyRegions:
    """D-18: validate every body_parts.yaml code maps to exactly one region or callout."""
    path = (config_dir or CONFIG_DIR) / "body_regions.yaml"
    if not path.is_file():
        raise TaxonomyConfigError(f"body_regions: config file not found: {path}")

    doc = load_yaml(path)
    if not doc:
        raise TaxonomyConfigError("body_regions: config file is empty")

    for key in ("version", "regions", "callouts", "rollups"):
        if key not in doc:
            raise TaxonomyConfigError(f"body_regions.{key} missing")

    seen_codes: dict[int, list[str]] = {}
    regions = _parse_body_region_group(doc["regions"], "region", seen_codes)
    callouts = _parse_body_region_group(doc["callouts"], "callout", seen_codes)
    all_regions = {**regions, **callouts}

    expected_codes = body_parts.codes()
    for code, mapped_ids in seen_codes.items():
        if len(mapped_ids) > 1:
            raise TaxonomyConfigError(
                f"body_regions: code {code} mapped in more than one region/callout: {mapped_ids}"
            )
        if code not in expected_codes:
            raise TaxonomyConfigError(f"body_regions: code {code} is not a known body_parts code")
    missing = sorted(expected_codes - set(seen_codes))
    if missing:
        raise TaxonomyConfigError(
            f"body_regions: code(s) {missing} not mapped to any region or callout"
        )

    rollups_doc = doc["rollups"]
    rollups: dict[str, list[int]] = {}
    for rollup_id, codes in rollups_doc.items():
        if not codes:
            raise TaxonomyConfigError(f"body_regions.rollups.{rollup_id} missing or empty")
        codes_int = [int(c) for c in codes]
        for c in codes_int:
            if c not in expected_codes:
                raise TaxonomyConfigError(
                    f"body_regions.rollups.{rollup_id} references unknown code {c}"
                )
        rollups[rollup_id] = codes_int

    return BodyRegions(version=doc["version"], regions=all_regions, rollups=rollups)


@dataclass(frozen=True)
class AgeBracket:
    id: str
    label: str
    min_years: float | None  # inclusive; None only for the "unknown" bracket
    max_years: float | None  # exclusive; None means open-ended (75+)
    sort_order: int


@dataclass(frozen=True)
class AgeBrackets:
    version: int
    brackets: dict[str, AgeBracket]  # keyed by bracket id


def load_age_brackets(*, config_dir: Path | None = None) -> AgeBrackets:
    """D-14: validated age brackets -- contiguous, non-overlapping ranges; unique sort_order;
    exactly one 'unknown' bracket."""
    path = (config_dir or CONFIG_DIR) / "age_brackets.yaml"
    if not path.is_file():
        raise TaxonomyConfigError(f"age_brackets: config file not found: {path}")

    doc = load_yaml(path)
    if not doc:
        raise TaxonomyConfigError("age_brackets: config file is empty")

    for key in ("version", "brackets"):
        if key not in doc:
            raise TaxonomyConfigError(f"age_brackets.{key} missing")

    brackets_doc = doc["brackets"]
    if not brackets_doc:
        raise TaxonomyConfigError("age_brackets.brackets missing or empty")

    brackets: dict[str, AgeBracket] = {}
    sort_orders_seen: dict[int, str] = {}
    for bracket_id, bdoc in brackets_doc.items():
        if not isinstance(bdoc, dict) or not bdoc.get("label"):
            raise TaxonomyConfigError(f"age_brackets.brackets.{bracket_id}.label missing")
        for required in ("min_years", "max_years", "sort_order"):
            if required not in bdoc:
                raise TaxonomyConfigError(f"age_brackets.brackets.{bracket_id}.{required} missing")

        sort_order = int(bdoc["sort_order"])
        if sort_order in sort_orders_seen:
            raise TaxonomyConfigError(
                f"age_brackets: sort_order {sort_order} duplicated between "
                f"{sort_orders_seen[sort_order]!r} and {bracket_id!r}"
            )
        sort_orders_seen[sort_order] = bracket_id

        min_years = bdoc["min_years"]
        max_years = bdoc["max_years"]
        brackets[bracket_id] = AgeBracket(
            id=bracket_id,
            label=bdoc["label"],
            min_years=float(min_years) if min_years is not None else None,
            max_years=float(max_years) if max_years is not None else None,
            sort_order=sort_order,
        )

    unknown_brackets = [b for b in brackets.values() if b.id == "unknown"]
    if len(unknown_brackets) != 1:
        raise TaxonomyConfigError(
            f"age_brackets: expected exactly one bracket with id 'unknown', found "
            f"{len(unknown_brackets)}"
        )

    ranged = sorted(
        (b for b in brackets.values() if b.min_years is not None), key=lambda b: b.min_years
    )
    for prev, curr in zip(ranged, ranged[1:], strict=False):
        if prev.max_years is None:
            raise TaxonomyConfigError(
                f"age_brackets: {prev.id!r} has no upper bound but is followed by {curr.id!r}"
            )
        if prev.max_years != curr.min_years:
            raise TaxonomyConfigError(
                f"age_brackets: {prev.id!r} (max {prev.max_years}) and {curr.id!r} "
                f"(min {curr.min_years}) are not contiguous/non-overlapping"
            )
    if ranged and ranged[-1].max_years is not None:
        raise TaxonomyConfigError(
            f"age_brackets: last ranged bracket {ranged[-1].id!r} must have max_years null "
            "(open-ended, e.g. 75+)"
        )

    return AgeBrackets(version=doc["version"], brackets=brackets)
