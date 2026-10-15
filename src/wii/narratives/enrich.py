"""D-22: pipeline-facing narrative enrichment. `enrich_cases` classifies every `fact_cases` row
exactly once (one `narrative_enrichment` row per case_id), fills `bridge_case_products`
provenance for in-scope rows, and returns the 4 D-14 refined-tool rows to append to `dim_tool`.

Classification is ALWAYS `classify_case` (rules) first, for every case -- rules are the sole
source of tool refinement/provenance decisions (NARR-02), never the optional `provider`. When
`provider` (an `AnthropicProvider`-shaped LLM consultant, D-18) is given AND the rules result's
confidence is below `LLM_CONFIDENCE_THRESHOLD`, `provider.classify_narrative(...)` is consulted;
a valid response REPLACES the enrichment record's narrative-derived fields only (never the
refinements/final_tool_id, which stay whatever rules decided). `provider=None` (the default, and
what `sync_neiss.py` always uses) means pure rules -- `classification_source='rules'` for 100% of
rows, matching the committed build's must-have. Only in-scope bridge slots are passed as
`(product_code, tool_id)` pairs; `alcohol_code`/`drug_code` are never read here (D-16).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol

import polars as pl

from wii.narratives.classify import classify_case
from wii.narratives.rules import load_refined_tool_defs
from wii.narratives.schema import NarrativeEnrichment
from wii.taxonomy.loader import load_product_taxonomy

# D-18: matches classify.py's MECHANISM_UNKNOWN_THRESHOLD -- an LLM consultant is worth calling
# only when rules classified this case at "unknown"-level confidence.
LLM_CONFIDENCE_THRESHOLD = 0.50


class LLMConsultant(Protocol):
    """The narrow shape `enrich_cases`' optional `provider` must have -- `AnthropicProvider`
    implements this. Deliberately NOT `NarrativeProvider`: this consultant never decides tool
    refinement, so it has no `classify()` returning `ProductRefinement`s."""

    def classify_narrative(
        self, narrative: str | None, official_product_codes: list[int]
    ) -> NarrativeEnrichment | None: ...


_ENRICHMENT_SCHEMA: dict[str, pl.DataType] = {
    "case_id": pl.Utf8,
    "tool_subtype": pl.Utf8,
    "activity": pl.Utf8,
    "primary_mechanism": pl.Utf8,
    "secondary_mechanisms": pl.List(pl.Utf8),
    "maintenance_or_setup": pl.Boolean,
    "workpiece_event": pl.Utf8,
    "projectile_or_debris": pl.Boolean,
    "direct_tool_contact": pl.Boolean,
    "body_contact_context": pl.Utf8,
    "ppe_mentioned": pl.Utf8,
    "guard_mentioned": pl.Utf8,
    "confidence": pl.Float64,
    "evidence": pl.List(pl.Utf8),
    "classification_source": pl.Utf8,
    "classifier_version": pl.Utf8,
    "maintenance_context": pl.Utf8,
    "power_state_mentioned": pl.Utf8,
}


@dataclass(frozen=True)
class EnrichmentResult:
    """`enrich_cases`' output: the committed enrichment table, the provenance-filled bridge
    (in-scope rows updated, everything else untouched), the 4 refined-tool `dim_tool` rows
    (no `source` column -- the pipeline adds it when concatenating with the official rows), and
    run stats for `data_metadata.json`."""

    enrichment: pl.DataFrame
    bridge_products: pl.DataFrame
    dim_tool_rows: pl.DataFrame
    stats: dict


def _dim_tool_rows_for_refined_tools() -> pl.DataFrame:
    """D-14: the 4 narrative-refined tools, `family_sort_order` looked up from the existing
    family (never hardcoded)."""
    tax = load_product_taxonomy()
    refined_defs = load_refined_tool_defs()
    tool_ids = sorted(refined_defs)
    labels = [refined_defs[t]["label"] for t in tool_ids]
    family_ids = [refined_defs[t]["family_id"] for t in tool_ids]
    family_sort_orders = [tax.families[fid].sort_order for fid in family_ids]
    return pl.DataFrame(
        {
            "tool_id": tool_ids,
            "label": labels,
            "family_id": family_ids,
            "family_sort_order": family_sort_orders,
        }
    )


def _empty_result(bridge_products: pl.DataFrame) -> EnrichmentResult:
    return EnrichmentResult(
        enrichment=pl.DataFrame(schema=_ENRICHMENT_SCHEMA),
        bridge_products=bridge_products,
        dim_tool_rows=_dim_tool_rows_for_refined_tools(),
        stats={
            "cases": 0,
            "coverage": 0.0,
            "unknown_mechanism_pct": 0.0,
            "refinement_by_broad_code": {},
            "source_counts": {},
        },
    )


def enrich_cases(
    fact_cases: pl.DataFrame,
    bridge_products: pl.DataFrame,
    dim_product: pl.DataFrame,
    provider: LLMConsultant | None = None,
) -> EnrichmentResult:
    """Classify every `fact_cases` row (one enrichment row each), fill `bridge_case_products`
    provenance for in-scope rows, and produce the D-14 refined-tool `dim_tool` rows.

    `product_codes` passed to `classify_case` come only from this case's in-scope bridge slots,
    in (case_id, seq) order -- `alcohol_code`/`drug_code` are never read (D-16). `provider`, if
    given, is consulted only for cases whose rules confidence is below
    `LLM_CONFIDENCE_THRESHOLD` (D-18); see the module docstring.
    """
    if fact_cases.is_empty() or "case_id" not in fact_cases.columns:
        return _empty_result(bridge_products)

    case_ids_all = fact_cases["case_id"].to_list()
    narratives_all = fact_cases["narrative"].to_list()

    in_scope = bridge_products.filter(pl.col("in_scope")).sort(["case_id", "seq"])
    not_in_scope = bridge_products.filter(~pl.col("in_scope"))

    row_case_ids = in_scope["case_id"].to_list()
    row_product_codes = in_scope["product_code"].to_list()
    row_tool_ids = in_scope["tool_id"].to_list()

    case_slots: dict[str, list[tuple[int, str]]] = {}
    for case_id, code, tool_id in zip(row_case_ids, row_product_codes, row_tool_ids, strict=True):
        case_slots.setdefault(case_id, []).append((code, tool_id))

    enrichment_rows: list[dict] = []
    per_case_refinements: dict[str, list] = {}
    for case_id, narrative in zip(case_ids_all, narratives_all, strict=True):
        slots = case_slots.get(case_id, [])
        # Rules ALWAYS run first and ALWAYS own the refinements (NARR-02) -- the optional
        # provider, if consulted below, can only replace narrative-derived enrichment fields.
        enrichment, refinements = classify_case(narrative, slots)
        per_case_refinements[case_id] = refinements

        if provider is not None and enrichment.confidence < LLM_CONFIDENCE_THRESHOLD:
            official_codes = [code for code, _tool_id in slots]
            llm_enrichment = provider.classify_narrative(narrative, official_codes)
            if llm_enrichment is not None:
                # D-14: maintenance_context/power_state_mentioned are rules-only fields -- an LLM
                # consultant may replace every other narrative-derived field, but never these two.
                enrichment = replace(
                    llm_enrichment,
                    maintenance_context=enrichment.maintenance_context,
                    power_state_mentioned=enrichment.power_state_mentioned,
                )
        enrichment_rows.append(
            {
                "case_id": case_id,
                "tool_subtype": enrichment.tool_subtype,
                "activity": enrichment.activity,
                "primary_mechanism": enrichment.primary_mechanism,
                "secondary_mechanisms": list(enrichment.secondary_mechanisms),
                "maintenance_or_setup": enrichment.maintenance_or_setup,
                "workpiece_event": enrichment.workpiece_event,
                "projectile_or_debris": enrichment.projectile_or_debris,
                "direct_tool_contact": enrichment.direct_tool_contact,
                "body_contact_context": enrichment.body_contact_context,
                "ppe_mentioned": enrichment.ppe_mentioned,
                "guard_mentioned": enrichment.guard_mentioned,
                "confidence": enrichment.confidence,
                "evidence": list(enrichment.evidence),
                "classification_source": enrichment.classification_source,
                "classifier_version": enrichment.classifier_version,
                "maintenance_context": enrichment.maintenance_context,
                "power_state_mentioned": enrichment.power_state_mentioned,
            }
        )

    case_cursor: dict[str, int] = {}
    refined_tool_by_row: list[str | None] = []
    final_tool_by_row: list[str] = []
    confidence_by_row: list[float | None] = []
    for case_id in row_case_ids:
        idx = case_cursor.get(case_id, 0)
        refinement = per_case_refinements[case_id][idx]
        case_cursor[case_id] = idx + 1
        refined_tool_by_row.append(refinement.narrative_refined_tool)
        final_tool_by_row.append(refinement.final_tool_id)
        confidence_by_row.append(refinement.refinement_confidence)

    if in_scope.height:
        in_scope = in_scope.with_columns(
            narrative_refined_tool=pl.Series(refined_tool_by_row, dtype=pl.Utf8),
            final_tool_id=pl.Series(final_tool_by_row, dtype=pl.Utf8),
            classification_confidence=pl.Series(confidence_by_row, dtype=pl.Float32),
        )

    bridge_products_out = pl.concat([in_scope, not_in_scope], how="vertical").sort(
        ["case_id", "seq"]
    )

    enrichment_df = (
        pl.DataFrame(enrichment_rows, schema=_ENRICHMENT_SCHEMA)
        if enrichment_rows
        else pl.DataFrame(schema=_ENRICHMENT_SCHEMA)
    )

    stats = _compute_stats(enrichment_rows, in_scope, dim_product)

    return EnrichmentResult(
        enrichment=enrichment_df,
        bridge_products=bridge_products_out,
        dim_tool_rows=_dim_tool_rows_for_refined_tools(),
        stats=stats,
    )


def _compute_stats(
    enrichment_rows: list[dict], in_scope_updated: pl.DataFrame, dim_product: pl.DataFrame
) -> dict:
    total_cases = len(enrichment_rows)
    unknown_count = sum(1 for r in enrichment_rows if r["primary_mechanism"] == "unknown")
    unknown_mechanism_pct = (unknown_count / total_cases * 100) if total_cases else 0.0

    source_counts: dict[str, int] = {}
    for r in enrichment_rows:
        source_counts[r["classification_source"]] = (
            source_counts.get(r["classification_source"], 0) + 1
        )
    coverage = (sum(source_counts.values()) / total_cases) if total_cases else 0.0

    refinement_by_broad_code: dict[int, int] = {}
    if in_scope_updated.height and "broad" in dim_product.columns:
        broad_codes = frozenset(dim_product.filter(pl.col("broad"))["product_code"].to_list())
        refined_rows = in_scope_updated.filter(
            pl.col("product_code").is_in(list(broad_codes))
            & pl.col("narrative_refined_tool").is_not_null()
        )
        if refined_rows.height:
            vc = refined_rows["product_code"].value_counts()
            codes = vc["product_code"].to_list()
            counts = vc["count"].to_list()
            for code, count in zip(codes, counts, strict=False):
                refinement_by_broad_code[int(code)] = int(count)

    return {
        "cases": total_cases,
        "coverage": coverage,
        "unknown_mechanism_pct": unknown_mechanism_pct,
        "refinement_by_broad_code": refinement_by_broad_code,
        "source_counts": source_counts,
    }
