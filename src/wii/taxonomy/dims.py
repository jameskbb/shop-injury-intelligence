"""Build dim_tool_family / dim_tool / dim_product / dim_body_* Polars frames from loaded config."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from wii.taxonomy.loader import AgeBrackets, BodyRegions, CodeDictionary, ProductTaxonomy
from wii.utils.atomic import write_parquet_atomic


def build_product_dims(tax: ProductTaxonomy) -> dict[str, pl.DataFrame]:
    families_sorted = sorted(tax.families.values(), key=lambda f: f.sort_order)
    dim_tool_family = pl.DataFrame(
        {
            "family_id": [f.family_id for f in families_sorted],
            "label": [f.label for f in families_sorted],
            "sort_order": [f.sort_order for f in families_sorted],
        }
    )

    tools_sorted = sorted(
        tax.tools.values(),
        key=lambda t: (tax.families[t.family_id].sort_order, t.tool_id),
    )
    dim_tool = pl.DataFrame(
        {
            "tool_id": [t.tool_id for t in tools_sorted],
            "label": [t.label for t in tools_sorted],
            "family_id": [t.family_id for t in tools_sorted],
            "family_sort_order": [tax.families[t.family_id].sort_order for t in tools_sorted],
        }
    )

    codes_sorted = sorted(
        tax.codes.values(),
        key=lambda c: (tax.families[tax.tools[c.tool_id].family_id].sort_order, c.tool_id, c.code),
    )
    adjacent_sorted = sorted(tax.adjacent.values(), key=lambda a: a.code)

    product_rows = {
        "product_code": [],
        "code_label": [],
        "official_name": [],
        "tool_id": [],
        "family_id": [],
        "start_year": [],
        "in_scope": [],
        "is_adjacent": [],
        "broad": [],
        "validated_against": [],
    }
    for c in codes_sorted:
        family_id = tax.tools[c.tool_id].family_id
        product_rows["product_code"].append(c.code)
        product_rows["code_label"].append(c.code_label)
        product_rows["official_name"].append(c.official_name)
        product_rows["tool_id"].append(c.tool_id)
        product_rows["family_id"].append(family_id)
        product_rows["start_year"].append(c.start_year)
        product_rows["in_scope"].append(c.in_scope)
        product_rows["is_adjacent"].append(False)
        product_rows["broad"].append(c.broad)
        product_rows["validated_against"].append(c.validated_against)
    for a in adjacent_sorted:
        product_rows["product_code"].append(a.code)
        product_rows["code_label"].append(str(a.code).zfill(4))
        product_rows["official_name"].append(a.label)
        product_rows["tool_id"].append(None)
        product_rows["family_id"].append(None)
        product_rows["start_year"].append(None)
        product_rows["in_scope"].append(False)
        product_rows["is_adjacent"].append(True)
        product_rows["broad"].append(False)
        product_rows["validated_against"].append(tax.validated_against)

    dim_product = pl.DataFrame(
        product_rows,
        schema={
            "product_code": pl.Int16,
            "code_label": pl.Utf8,
            "official_name": pl.Utf8,
            "tool_id": pl.Utf8,
            "family_id": pl.Utf8,
            "start_year": pl.Int32,
            "in_scope": pl.Boolean,
            "is_adjacent": pl.Boolean,
            "broad": pl.Boolean,
            "validated_against": pl.Utf8,
        },
    )

    return {
        "dim_tool_family": dim_tool_family,
        "dim_tool": dim_tool,
        "dim_product": dim_product,
    }


def write_product_dims(dims: dict[str, pl.DataFrame], processed_dir: Path) -> None:
    processed_dir = Path(processed_dir)
    for name, df in dims.items():
        write_parquet_atomic(df, processed_dir / f"{name}.parquet")


def build_body_dims(
    body_parts: CodeDictionary, body_regions: BodyRegions
) -> dict[str, pl.DataFrame]:
    """dim_body_part, dim_body_region, dim_body_rollup (D-15, D-18)."""
    region_of_code: dict[int, str] = {}
    for region in body_regions.regions.values():
        for code in region.body_part_codes:
            region_of_code[code] = region.region_id

    entries_sorted = sorted(body_parts.entries.values(), key=lambda e: e.code)
    regions_by_id = body_regions.regions
    dim_body_part = pl.DataFrame(
        {
            "code": [e.code for e in entries_sorted],
            "label": [e.label for e in entries_sorted],
            "region_id": [region_of_code[e.code] for e in entries_sorted],
            "region_label": [regions_by_id[region_of_code[e.code]].label for e in entries_sorted],
            "is_callout": [
                regions_by_id[region_of_code[e.code]].kind == "callout" for e in entries_sorted
            ],
            "view": [regions_by_id[region_of_code[e.code]].view for e in entries_sorted],
            "svg_element_ids": [
                regions_by_id[region_of_code[e.code]].svg_element_ids for e in entries_sorted
            ],
            "manual_page": [e.manual_page for e in entries_sorted],
        },
        schema={
            "code": pl.Int16,
            "label": pl.Utf8,
            "region_id": pl.Utf8,
            "region_label": pl.Utf8,
            "is_callout": pl.Boolean,
            "view": pl.Utf8,
            "svg_element_ids": pl.List(pl.Utf8),
            "manual_page": pl.Int16,
        },
    )

    regions_sorted = sorted(body_regions.regions.values(), key=lambda r: r.sort_order)
    dim_body_region = pl.DataFrame(
        {
            "region_id": [r.region_id for r in regions_sorted],
            "label": [r.label for r in regions_sorted],
            "kind": [r.kind for r in regions_sorted],
            "view": [r.view for r in regions_sorted],
            "svg_element_ids": [r.svg_element_ids for r in regions_sorted],
            "body_part_codes": [r.body_part_codes for r in regions_sorted],
            "sort_order": [r.sort_order for r in regions_sorted],
        },
        schema={
            "region_id": pl.Utf8,
            "label": pl.Utf8,
            "kind": pl.Utf8,
            "view": pl.Utf8,
            "svg_element_ids": pl.List(pl.Utf8),
            "body_part_codes": pl.List(pl.Int16),
            "sort_order": pl.Int32,
        },
    )

    rollup_pairs = sorted(
        (rollup_id, code) for rollup_id, codes in body_regions.rollups.items() for code in codes
    )
    dim_body_rollup = pl.DataFrame(
        {
            "rollup_id": [r for r, _ in rollup_pairs],
            "body_part_code": [c for _, c in rollup_pairs],
        },
        schema={"rollup_id": pl.Utf8, "body_part_code": pl.Int16},
    )

    return {
        "dim_body_part": dim_body_part,
        "dim_body_region": dim_body_region,
        "dim_body_rollup": dim_body_rollup,
    }


def build_diagnosis_dim(diagnoses: CodeDictionary) -> pl.DataFrame:
    """dim_diagnosis: code, label, injury_category, manual_page. Sorted by code."""
    entries = sorted(diagnoses.entries.values(), key=lambda e: e.code)
    return pl.DataFrame(
        {
            "code": [e.code for e in entries],
            "label": [e.label for e in entries],
            "injury_category": [e.attrs["injury_category"] for e in entries],
            "manual_page": [e.manual_page for e in entries],
        },
        schema={
            "code": pl.Int16,
            "label": pl.Utf8,
            "injury_category": pl.Utf8,
            "manual_page": pl.Int16,
        },
    )


def build_disposition_dim(dispositions: CodeDictionary) -> pl.DataFrame:
    """dim_disposition: code, label, severity_bucket, escalated_care, sort_order. Sorted by code."""
    entries = sorted(dispositions.entries.values(), key=lambda e: e.code)
    return pl.DataFrame(
        {
            "code": [e.code for e in entries],
            "label": [e.label for e in entries],
            "severity_bucket": [e.attrs["severity_bucket"] for e in entries],
            "escalated_care": [bool(e.attrs["escalated_care"]) for e in entries],
            "sort_order": [e.attrs["sort_order"] for e in entries],
        },
        schema={
            "code": pl.Int8,
            "label": pl.Utf8,
            "severity_bucket": pl.Utf8,
            "escalated_care": pl.Boolean,
            "sort_order": pl.Int32,
        },
    )


def build_location_dim(locations: CodeDictionary) -> pl.DataFrame:
    """dim_location: code, label, manual_page, sort_order. Sorted by code."""
    entries = sorted(locations.entries.values(), key=lambda e: e.code)
    return pl.DataFrame(
        {
            "code": [e.code for e in entries],
            "label": [e.label for e in entries],
            "manual_page": [e.manual_page for e in entries],
            "sort_order": [e.attrs["sort_order"] for e in entries],
        },
        schema={
            "code": pl.Int8,
            "label": pl.Utf8,
            "manual_page": pl.Int16,
            "sort_order": pl.Int32,
        },
    )


def build_sex_dim(sex: CodeDictionary) -> pl.DataFrame:
    """dim_sex: code, label, manual_page. Sorted by code."""
    entries = sorted(sex.entries.values(), key=lambda e: e.code)
    return pl.DataFrame(
        {
            "code": [e.code for e in entries],
            "label": [e.label for e in entries],
            "manual_page": [e.manual_page for e in entries],
        },
        schema={"code": pl.Int8, "label": pl.Utf8, "manual_page": pl.Int16},
    )


def build_age_bracket_dim(brackets: AgeBrackets) -> pl.DataFrame:
    """dim_age_bracket: id, label, min_years, max_years, sort_order. Sorted by sort_order."""
    entries = sorted(brackets.brackets.values(), key=lambda b: b.sort_order)
    return pl.DataFrame(
        {
            "id": [b.id for b in entries],
            "label": [b.label for b in entries],
            "min_years": [b.min_years for b in entries],
            "max_years": [b.max_years for b in entries],
            "sort_order": [b.sort_order for b in entries],
        },
        schema={
            "id": pl.Utf8,
            "label": pl.Utf8,
            "min_years": pl.Float32,
            "max_years": pl.Float32,
            "sort_order": pl.Int32,
        },
    )


def build_all_dims(
    taxonomy: ProductTaxonomy,
    body_parts: CodeDictionary,
    body_regions: BodyRegions,
    diagnoses: CodeDictionary,
    dispositions: CodeDictionary,
    locations: CodeDictionary,
    sex: CodeDictionary,
    age_brackets: AgeBrackets,
) -> dict[str, pl.DataFrame]:
    """Every dim_* frame, so the pipeline writes them all from one place."""
    dims = build_product_dims(taxonomy)
    dims.update(build_body_dims(body_parts, body_regions))
    dims["dim_diagnosis"] = build_diagnosis_dim(diagnoses)
    dims["dim_disposition"] = build_disposition_dim(dispositions)
    dims["dim_location"] = build_location_dim(locations)
    dims["dim_sex"] = build_sex_dim(sex)
    dims["dim_age_bracket"] = build_age_bracket_dim(age_brackets)
    return dims
