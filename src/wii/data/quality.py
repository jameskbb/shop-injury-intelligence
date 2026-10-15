"""Run-to-completion QA check registry: structural checks abort, advisory checks report only.

D-19/D-20: every check runs to completion (never fail-fast) so qa_report.md is always a full
picture. The run aborts (non-zero exit) only if any *structural* check failed. Reports are
written via atomic_write_text so an aborted run never replaces the last complete report.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import polars as pl

from wii.taxonomy.loader import ProductTaxonomy, in_scope_codes
from wii.utils.atomic import atomic_write_text

REPORT_VERSION = 1

_COUNT_SEMANTICS_SENTENCE = "All counts are raw NEISS sample records, not national estimates."


@dataclass
class CheckResult:
    check_id: str
    category: Literal["structural", "advisory"]
    passed: bool
    detail: str
    count: int = 0
    examples: list[str] = field(default_factory=list)


@dataclass
class YearQAStats:
    total_rows: int = 0
    duplicate_case_id_count: int = 0
    duplicate_examples: list[str] = field(default_factory=list)
    rejoined_record_count: int = 0
    cp1252_fallback: bool = False
    nonpositive_or_null_weight_count: int = 0
    weight_examples: list[str] = field(default_factory=list)
    out_of_year_date_count: int = 0
    date_examples: list[str] = field(default_factory=list)
    workshop_case_count_raw: int = 0
    # D-15/D-19: field name (e.g. "body_part") -> {code -> count} for values observed on the
    # FULL file that are absent from that field's dictionary. Populated by the pipeline from the
    # dictionaries loaded once per run.
    unknown_codes: dict[str, dict[int, int]] = field(default_factory=dict)
    # D-14: age_raw value -> count, for values outside {0} U [2,115] U [201,223] on the FULL file.
    invalid_age_counts: dict[int, int] = field(default_factory=dict)
    # D-16: this year's Body_Part_2 non-null fill rate on the FULL file, for
    # secondary_fill_rate_by_era.
    body_part_2_fill_rate: float = 0.0
    # QA-01: subset of D-08's per-year fill rates (body_part_2, diagnosis_2, product_3),
    # surfaced informationally by secondary_field_availability. Computed once by pipeline.py
    # from year_metadata's own fill_rate dict -- never re-derived here.
    secondary_fill_rates: dict[str, float] = field(default_factory=dict)


@dataclass
class QAContext:
    years: list[int]
    parse_failures: dict[int, dict]
    tables: dict[str, object]
    taxonomy: ProductTaxonomy
    year_stats: dict[int, YearQAStats] = field(default_factory=dict)
    # Finding 3: every D-11 aggregate cell omitted because its domain had fewer than 2 PSUs in
    # some active stratum (InsufficientPSUsError) -- 0 expected on real data.
    aggregates_omitted_cells: list[dict] = field(default_factory=list)


@dataclass
class QAReport:
    results: list[CheckResult]
    aborted: bool
    structural_failure_count: int
    advisory_flag_count: int
    generated_at: str
    report_version: int = REPORT_VERSION


CheckFn = Callable[[QAContext], CheckResult]


def check_header_contract(ctx: QAContext) -> CheckResult:
    header_failures = {y: pf for y, pf in ctx.parse_failures.items() if pf.get("kind") == "header"}
    if header_failures:
        detail = "; ".join(f"year {y}: {pf['detail']}" for y, pf in sorted(header_failures.items()))
        return CheckResult(
            "header_contract", "structural", False, detail, count=len(header_failures)
        )
    return CheckResult(
        "header_contract",
        "structural",
        True,
        "All years match the 25-column header contract",
        count=0,
    )


def check_unique_case_ids_within_year(ctx: QAContext) -> CheckResult:
    total = sum(s.duplicate_case_id_count for s in ctx.year_stats.values())
    examples: list[str] = []
    for stats in ctx.year_stats.values():
        examples.extend(stats.duplicate_examples)
    if total > 0:
        return CheckResult(
            "unique_case_ids_within_year",
            "structural",
            False,
            f"{total} duplicate case_id(s) found within a year",
            count=total,
            examples=examples[:10],
        )
    return CheckResult(
        "unique_case_ids_within_year",
        "structural",
        True,
        "No duplicate case_ids within any year",
        count=0,
    )


def check_no_unparseable_rows(ctx: QAContext) -> CheckResult:
    record_failures = {y: pf for y, pf in ctx.parse_failures.items() if pf.get("kind") == "record"}
    if record_failures:
        detail = "; ".join(f"year {y}: {pf['detail']}" for y, pf in sorted(record_failures.items()))
        return CheckResult(
            "no_unparseable_rows", "structural", False, detail, count=len(record_failures)
        )
    return CheckResult(
        "no_unparseable_rows",
        "structural",
        True,
        "Every year's rows parsed and cast cleanly after rejoin",
        count=0,
    )


def check_rejoined_records(ctx: QAContext) -> CheckResult:
    per_year = {
        y: s.rejoined_record_count for y, s in ctx.year_stats.items() if s.rejoined_record_count
    }
    total = sum(per_year.values())
    if per_year:
        detail = "; ".join(f"year {y}: {n} rejoined" for y, n in sorted(per_year.items()))
        return CheckResult("rejoined_records", "advisory", False, detail, count=total)
    return CheckResult("rejoined_records", "advisory", True, "No rejoined records", count=0)


def check_cp1252_fallback_years(ctx: QAContext) -> CheckResult:
    years = sorted(y for y, s in ctx.year_stats.items() if s.cp1252_fallback)
    if years:
        return CheckResult(
            "cp1252_fallback_years",
            "advisory",
            False,
            f"Years decoded via cp1252 fallback: {years}",
            count=len(years),
            examples=[str(y) for y in years],
        )
    return CheckResult(
        "cp1252_fallback_years", "advisory", True, "All years decoded as utf-8", count=0
    )


def check_weights_positive(ctx: QAContext) -> CheckResult:
    per_year = {y: s for y, s in ctx.year_stats.items() if s.nonpositive_or_null_weight_count}
    if per_year:
        total = sum(s.nonpositive_or_null_weight_count for s in per_year.values())
        detail = "; ".join(
            f"year {y}: {s.nonpositive_or_null_weight_count} nonpositive/null weight(s)"
            for y, s in sorted(per_year.items())
        )
        examples = [e for s in per_year.values() for e in s.weight_examples][:10]
        return CheckResult(
            "weights_positive", "structural", False, detail, count=total, examples=examples
        )
    return CheckResult(
        "weights_positive", "structural", True, "Every weight is positive and non-null", count=0
    )


def check_treatment_dates_in_file_year(ctx: QAContext) -> CheckResult:
    per_year = {y: s for y, s in ctx.year_stats.items() if s.out_of_year_date_count}
    if per_year:
        total = sum(s.out_of_year_date_count for s in per_year.values())
        detail = "; ".join(
            f"year {y}: {s.out_of_year_date_count} out-of-year treatment_date(s)"
            for y, s in sorted(per_year.items())
        )
        examples = [e for s in per_year.values() for e in s.date_examples][:10]
        return CheckResult(
            "treatment_dates_in_file_year",
            "structural",
            False,
            detail,
            count=total,
            examples=examples,
        )
    return CheckResult(
        "treatment_dates_in_file_year",
        "structural",
        True,
        "Every treatment_date falls within its file's year",
        count=0,
    )


def _unknown_codes_check(ctx: QAContext, field: str, check_id: str) -> CheckResult:
    """D-15/D-19: structural gate for a code field's known-code closure. `field` is the name
    used in YearQAStats.unknown_codes (e.g. "body_part"), covering every slot for that field."""
    per_year: dict[int, dict[int, int]] = {
        y: s.unknown_codes[field] for y, s in ctx.year_stats.items() if s.unknown_codes.get(field)
    }
    if per_year:
        total = sum(sum(counts.values()) for counts in per_year.values())
        detail_parts = []
        examples: list[str] = []
        for y in sorted(per_year):
            codes_str = ", ".join(f"{code}({count})" for code, count in sorted(per_year[y].items()))
            detail_parts.append(f"year {y} field {field}: {codes_str}")
            examples.extend(str(code) for code in sorted(per_year[y]))
        detail = "; ".join(detail_parts)
        return CheckResult(
            check_id, "structural", False, detail, count=total, examples=examples[:10]
        )
    return CheckResult(
        check_id,
        "structural",
        True,
        f"Every observed {field} code exists in its dictionary",
        count=0,
    )


def check_known_body_part_codes(ctx: QAContext) -> CheckResult:
    return _unknown_codes_check(ctx, "body_part", "known_body_part_codes")


def check_known_diagnosis_codes(ctx: QAContext) -> CheckResult:
    return _unknown_codes_check(ctx, "diagnosis", "known_diagnosis_codes")


def check_known_disposition_codes(ctx: QAContext) -> CheckResult:
    return _unknown_codes_check(ctx, "disposition", "known_disposition_codes")


def check_known_location_codes(ctx: QAContext) -> CheckResult:
    return _unknown_codes_check(ctx, "location", "known_location_codes")


def check_known_sex_codes(ctx: QAContext) -> CheckResult:
    return _unknown_codes_check(ctx, "sex", "known_sex_codes")


def check_in_scope_product_mapping(ctx: QAContext) -> CheckResult:
    """Every bridge_case_products row with in_scope=true has a non-null tool_id/family_id, and
    dim_product's in-scope codes equal in_scope_codes() (Common Pitfalls #5 / Open Question 1:
    scoped to the D-01 in-scope list, not arbitrary co-product codes)."""
    bridge = ctx.tables.get("bridge_case_products")
    dim_product = ctx.tables.get("dim_product")

    detail_parts: list[str] = []
    problems = 0
    examples: list[str] = []

    if bridge is not None and not bridge.is_empty():
        in_scope_rows = bridge.filter(pl.col("in_scope"))
        bad_mapping = in_scope_rows.filter(
            pl.col("tool_id").is_null() | pl.col("family_id").is_null()
        )
        problems = bad_mapping.height
        if problems:
            detail_parts.append(
                f"{problems} in-scope bridge row(s) with a null tool_id or family_id"
            )
            examples = bad_mapping["case_id"].to_list()[:10]

    expected = in_scope_codes(ctx.taxonomy)
    actual = (
        frozenset(dim_product.filter(pl.col("in_scope"))["product_code"].to_list())
        if dim_product is not None and not dim_product.is_empty()
        else frozenset()
    )
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        detail_parts.append(
            f"dim_product in-scope codes mismatch: missing {missing}, extra {extra}"
        )

    if detail_parts:
        return CheckResult(
            "in_scope_product_mapping",
            "structural",
            False,
            "; ".join(detail_parts),
            count=problems,
            examples=examples,
        )
    return CheckResult(
        "in_scope_product_mapping",
        "structural",
        True,
        "Every in-scope bridge row maps to a tool/family; dim_product matches the taxonomy",
        count=0,
    )


def check_non_taxonomy_coproduct_codes(ctx: QAContext) -> CheckResult:
    """Advisory only (Common Pitfalls #5): co-product codes recorded with in_scope=false and
    is_adjacent=false always report, never abort."""
    bridge = ctx.tables.get("bridge_case_products")
    if bridge is None or bridge.is_empty():
        return CheckResult(
            "non_taxonomy_coproduct_codes",
            "advisory",
            True,
            "No co-product codes observed",
            count=0,
        )

    non_taxonomy = bridge.filter(~pl.col("in_scope") & ~pl.col("is_adjacent"))
    distinct_count = non_taxonomy["product_code"].n_unique()
    if distinct_count == 0:
        return CheckResult(
            "non_taxonomy_coproduct_codes",
            "advisory",
            True,
            "No co-product codes observed",
            count=0,
        )

    top10 = (
        non_taxonomy.group_by("product_code")
        .agg(pl.len().alias("n"))
        .sort("n", descending=True)
        .head(10)
    )
    examples = [f"{row['product_code']}({row['n']})" for row in top10.iter_rows(named=True)]
    return CheckResult(
        "non_taxonomy_coproduct_codes",
        "advisory",
        False,
        f"{distinct_count} distinct non-taxonomy co-product code(s) observed",
        count=distinct_count,
        examples=examples,
    )


def check_unique_case_ids_global(ctx: QAContext) -> CheckResult:
    fact_cases = ctx.tables.get("fact_cases")
    if fact_cases is None or fact_cases.is_empty():
        return CheckResult("unique_case_ids_global", "structural", True, "No fact cases", count=0)
    dupes = fact_cases.group_by("case_id").agg(pl.len().alias("n")).filter(pl.col("n") > 1)
    if dupes.height:
        examples = dupes["case_id"].to_list()[:10]
        return CheckResult(
            "unique_case_ids_global",
            "structural",
            False,
            f"{dupes.height} case_id(s) duplicated across years",
            count=dupes.height,
            examples=examples,
        )
    return CheckResult(
        "unique_case_ids_global", "structural", True, "No case_id duplicated across years", count=0
    )


def check_design_psu_covers_fact_cases(ctx: QAContext) -> CheckResult:
    fact_cases = ctx.tables.get("fact_cases")
    design_psu = ctx.tables.get("design_psu")
    if fact_cases is None or fact_cases.is_empty():
        return CheckResult(
            "design_psu_covers_fact_cases", "structural", True, "No fact cases to check", count=0
        )
    fact_keys = fact_cases.select(["year", "stratum", "psu"]).unique()
    design_keys = (
        design_psu.select(["year", "stratum", "psu"]).unique()
        if design_psu is not None and not design_psu.is_empty()
        else fact_keys.clear()
    )
    missing = fact_keys.join(design_keys, on=["year", "stratum", "psu"], how="anti")
    if missing.height:
        examples = [str(r) for r in missing.head(10).rows()]
        return CheckResult(
            "design_psu_covers_fact_cases",
            "structural",
            False,
            f"{missing.height} fact (year,stratum,psu) combo(s) missing from design_psu",
            count=missing.height,
            examples=examples,
        )
    return CheckResult(
        "design_psu_covers_fact_cases",
        "structural",
        True,
        "Every fact (year,stratum,psu) exists in design_psu",
        count=0,
    )


def check_bridge_products_reference_fact(ctx: QAContext) -> CheckResult:
    fact_cases = ctx.tables.get("fact_cases")
    bridge = ctx.tables.get("bridge_case_products")
    if fact_cases is None or bridge is None or fact_cases.is_empty():
        return CheckResult(
            "bridge_products_reference_fact", "structural", True, "No data to check", count=0
        )
    fact_ids = fact_cases.select("case_id").unique()
    bridge_ids = bridge.select("case_id").unique()
    orphan_bridge = bridge_ids.join(fact_ids, on="case_id", how="anti")
    in_scope_case_ids = bridge.filter(pl.col("in_scope"))["case_id"].unique().to_frame("case_id")
    cases_without_in_scope_bridge = fact_ids.join(in_scope_case_ids, on="case_id", how="anti")
    problems = orphan_bridge.height + cases_without_in_scope_bridge.height
    if problems:
        detail = (
            f"{orphan_bridge.height} bridge case_id(s) not in fact_cases; "
            f"{cases_without_in_scope_bridge.height} fact case(s) with no in_scope bridge row"
        )
        examples = (
            (orphan_bridge["case_id"].to_list()[:5])
            + (cases_without_in_scope_bridge["case_id"].to_list()[:5])
        )
        return CheckResult(
            "bridge_products_reference_fact",
            "structural",
            False,
            detail,
            count=problems,
            examples=examples,
        )
    return CheckResult(
        "bridge_products_reference_fact",
        "structural",
        True,
        "Every bridge case_id references a fact case, and every fact case has an in-scope "
        "bridge row",
        count=0,
    )


def check_yoy_workshop_volume_within_40pct(ctx: QAContext) -> CheckResult:
    years_sorted = sorted(ctx.year_stats.keys())
    flags = []
    for a, b in zip(years_sorted, years_sorted[1:], strict=False):
        if b != a + 1:
            continue
        count_a = ctx.year_stats[a].workshop_case_count_raw
        count_b = ctx.year_stats[b].workshop_case_count_raw
        if count_a <= 0:
            continue
        pct_change = abs(count_b - count_a) / count_a
        if pct_change > 0.40:
            flags.append(f"{a}->{b}: {count_a} -> {count_b} ({pct_change:.0%})")
    if flags:
        return CheckResult(
            "yoy_workshop_volume_within_40pct",
            "advisory",
            False,
            "; ".join(flags),
            count=len(flags),
        )
    return CheckResult(
        "yoy_workshop_volume_within_40pct",
        "advisory",
        True,
        "No consecutive-year workshop volume swings exceed 40%",
        count=0,
    )


def check_ages_in_valid_domain(ctx: QAContext) -> CheckResult:
    """D-14: age_raw must fall in {0} U [2,115] U [201,223]. Never drops/nulls rows -- structural
    abort with every offending value and its count."""
    per_year = {y: s.invalid_age_counts for y, s in ctx.year_stats.items() if s.invalid_age_counts}
    if per_year:
        total = sum(sum(counts.values()) for counts in per_year.values())
        detail_parts = []
        examples: list[str] = []
        for y in sorted(per_year):
            vals = ", ".join(f"{v}({c})" for v, c in sorted(per_year[y].items()))
            detail_parts.append(f"year {y}: {vals}")
            examples.extend(str(v) for v in sorted(per_year[y]))
        return CheckResult(
            "ages_in_valid_domain",
            "structural",
            False,
            "; ".join(detail_parts),
            count=total,
            examples=examples[:10],
        )
    return CheckResult(
        "ages_in_valid_domain",
        "structural",
        True,
        "Every age_raw value is in {0} U [2,115] U [201,223]",
        count=0,
    )


def check_secondary_fill_rate_by_era(ctx: QAContext) -> CheckResult:
    """D-16: year<=2018 requires a 0.0 Body_Part_2 fill rate; year>=2019 requires >0.05. Detects
    the rich-detail era by fill rate, never column presence (PITFALLS.md Pitfall 6)."""
    problems: list[tuple[int, float, str]] = []
    for year, stats in sorted(ctx.year_stats.items()):
        rate = stats.body_part_2_fill_rate
        if year <= 2018 and rate != 0.0:
            problems.append((year, rate, "expected 0.0 for year<=2018"))
        elif year >= 2019 and not (rate > 0.05):
            problems.append((year, rate, "expected >0.05 for year>=2019"))
    if problems:
        detail = "; ".join(f"year {y}: rate {r} ({msg})" for y, r, msg in problems)
        return CheckResult(
            "secondary_fill_rate_by_era", "structural", False, detail, count=len(problems)
        )
    return CheckResult(
        "secondary_fill_rate_by_era",
        "structural",
        True,
        "Every year's Body_Part_2 fill rate matches its schema era",
        count=0,
    )


def check_missing_narratives(ctx: QAContext) -> CheckResult:
    """QA-01: advisory-only -- an in-scope case with a null/blank narrative is flagged, never
    aborts."""
    fact_cases = ctx.tables.get("fact_cases")
    if fact_cases is None or fact_cases.is_empty():
        return CheckResult("missing_narratives", "advisory", True, "No fact cases", count=0)
    narrative = fact_cases["narrative"]
    mask = narrative.is_null() | (narrative.fill_null("").str.strip_chars() == "")
    count = int(mask.sum())
    if count:
        examples = fact_cases.filter(mask)["case_id"].to_list()[:10]
        return CheckResult(
            "missing_narratives",
            "advisory",
            False,
            f"{count} case(s) with a missing or empty narrative",
            count=count,
            examples=examples,
        )
    return CheckResult(
        "missing_narratives", "advisory", True, "Every case has a narrative", count=0
    )


def check_secondary_field_availability(ctx: QAContext) -> CheckResult:
    """QA-01: per-year body_part_2/diagnosis_2/product_3 fill-rate table -- informational,
    always passes. Rendered as its own table in qa_report.md (see write_reports)."""
    rows = {
        y: s.secondary_fill_rates
        for y, s in sorted(ctx.year_stats.items())
        if s.secondary_fill_rates
    }
    if not rows:
        return CheckResult(
            "secondary_field_availability", "advisory", True, "No years processed", count=0
        )
    detail = "; ".join(
        f"year {y}: body_part_2={r.get('body_part_2', 0.0):.1%}, "
        f"diagnosis_2={r.get('diagnosis_2', 0.0):.1%}, product_3={r.get('product_3', 0.0):.1%}"
        for y, r in rows.items()
    )
    return CheckResult("secondary_field_availability", "advisory", True, detail, count=len(rows))


def check_workshop_case_volume_sanity(ctx: QAContext) -> CheckResult:
    """QA-01: flags any year whose raw in-scope case count falls outside [1500, 6000]. The
    verified 2006-2025 real range is 2,636-3,703/yr (63,425 total) -- see VARIANCE-VALIDATION.md
    and research/NEISS-REFERENCE.md."""
    problems = {
        y: s.workshop_case_count_raw
        for y, s in sorted(ctx.year_stats.items())
        if not (1_500 <= s.workshop_case_count_raw <= 6_000)
    }
    if problems:
        detail = "; ".join(f"year {y}: {n} raw in-scope case(s)" for y, n in problems.items())
        return CheckResult(
            "workshop_case_volume_sanity", "advisory", False, detail, count=len(problems)
        )
    return CheckResult(
        "workshop_case_volume_sanity",
        "advisory",
        True,
        "Every year's raw in-scope case count is within [1500, 6000]",
        count=0,
    )


def check_classifier_coverage(ctx: QAContext) -> CheckResult:
    """QA-01: share of in-scope bridge_case_products rows with a populated
    classification_confidence (D-06, Phase 2's classifier). Informational -- always passes."""
    bridge = ctx.tables.get("bridge_case_products")
    if bridge is None or bridge.is_empty():
        return CheckResult("classifier_coverage", "advisory", True, "No in-scope rows", count=0)
    in_scope_rows = bridge.filter(pl.col("in_scope"))
    total = in_scope_rows.height
    if total == 0:
        return CheckResult("classifier_coverage", "advisory", True, "No in-scope rows", count=0)
    covered = in_scope_rows.filter(pl.col("classification_confidence").is_not_null()).height
    rate = covered / total
    return CheckResult(
        "classifier_coverage",
        "advisory",
        True,
        f"{covered}/{total} in-scope rows classified ({rate:.1%})",
        count=total,
    )


def check_broad_code_refinement_rate(ctx: QAContext) -> CheckResult:
    """QA-01: share of in-scope, taxonomy-broad-flagged rows with a populated
    narrative_refined_tool (D-06). Only broad in-scope codes count toward the denominator.
    Informational -- always passes."""
    bridge = ctx.tables.get("bridge_case_products")
    dim_product = ctx.tables.get("dim_product")
    if (
        bridge is None
        or bridge.is_empty()
        or dim_product is None
        or dim_product.is_empty()
        or "broad" not in dim_product.columns
    ):
        return CheckResult(
            "broad_code_refinement_rate", "advisory", True, "No in-scope broad-code rows", count=0
        )
    broad_codes = dim_product.filter(pl.col("broad"))["product_code"].to_list()
    broad_rows = bridge.filter(pl.col("in_scope") & pl.col("product_code").is_in(broad_codes))
    total = broad_rows.height
    if total == 0:
        return CheckResult(
            "broad_code_refinement_rate", "advisory", True, "No in-scope broad-code rows", count=0
        )
    refined = broad_rows.filter(pl.col("narrative_refined_tool").is_not_null()).height
    rate = refined / total
    return CheckResult(
        "broad_code_refinement_rate",
        "advisory",
        True,
        f"{refined}/{total} in-scope broad-code rows refined ({rate:.1%})",
        count=total,
    )


def check_aggregate_cells_omitted(ctx: QAContext) -> CheckResult:
    """Finding 3: advisory count of D-11 aggregate cells omitted for InsufficientPSUsError.
    0 expected on real data -- a nonzero count is not a structural abort (a genuinely sparse
    stratum can be legitimate on a thin slice), but must be visible, not silently swallowed."""
    count = len(ctx.aggregates_omitted_cells)
    if count == 0:
        return CheckResult("aggregate_cells_omitted", "advisory", True, "0 cells omitted", count=0)
    examples = [
        f"{c['table']}/{c['window']}/{c['group']}" for c in ctx.aggregates_omitted_cells[:10]
    ]
    return CheckResult(
        "aggregate_cells_omitted",
        "advisory",
        True,
        f"{count} aggregate cell(s) omitted for insufficient PSUs",
        count=count,
        examples=examples,
    )


def check_enrichment_one_row_per_case(ctx: QAContext) -> CheckResult:
    """D-22: narrative_enrichment must carry exactly one row per fact_cases case_id -- no
    duplicates, none missing, none extra."""
    fact_cases = ctx.tables.get("fact_cases")
    if fact_cases is None or fact_cases.is_empty():
        return CheckResult(
            "enrichment_one_row_per_case", "structural", True, "No fact cases", count=0
        )
    fact_ids = frozenset(fact_cases["case_id"].to_list())
    enrichment = ctx.tables.get("narrative_enrichment")
    if enrichment is None or enrichment.is_empty():
        return CheckResult(
            "enrichment_one_row_per_case",
            "structural",
            False,
            f"narrative_enrichment has 0 rows, expected {len(fact_ids)}",
            count=len(fact_ids),
        )
    enr_ids = enrichment["case_id"].to_list()
    dup_count = len(enr_ids) - len(set(enr_ids))
    missing = fact_ids - set(enr_ids)
    extra = set(enr_ids) - fact_ids
    problems: list[str] = []
    if dup_count:
        problems.append(f"{dup_count} duplicate case_id(s) in narrative_enrichment")
    if missing:
        problems.append(f"{len(missing)} fact case(s) missing from narrative_enrichment")
    if extra:
        problems.append(f"{len(extra)} narrative_enrichment case_id(s) not in fact_cases")
    if problems:
        return CheckResult(
            "enrichment_one_row_per_case",
            "structural",
            False,
            "; ".join(problems),
            count=dup_count + len(missing) + len(extra),
            examples=(list(missing) + list(extra))[:10],
        )
    return CheckResult(
        "enrichment_one_row_per_case",
        "structural",
        True,
        f"narrative_enrichment has exactly one row per fact_cases case_id ({len(fact_ids)})",
        count=0,
    )


def check_specific_codes_never_refined(ctx: QAContext) -> CheckResult:
    """D-14/NARR-02: every in-scope bridge row whose product_code is NOT a broad code keeps
    final_tool_id == tool_id and a null narrative_refined_tool -- refinement never overrides a
    specific official product code."""
    bridge = ctx.tables.get("bridge_case_products")
    dim_product = ctx.tables.get("dim_product")
    if (
        bridge is None
        or bridge.is_empty()
        or dim_product is None
        or dim_product.is_empty()
        or "broad" not in dim_product.columns
    ):
        return CheckResult(
            "specific_codes_never_refined", "structural", True, "No in-scope rows", count=0
        )
    broad_codes = dim_product.filter(pl.col("broad"))["product_code"].to_list()
    non_broad_in_scope = bridge.filter(
        pl.col("in_scope") & ~pl.col("product_code").is_in(broad_codes)
    )
    bad = non_broad_in_scope.filter(
        (pl.col("final_tool_id") != pl.col("tool_id"))
        | pl.col("narrative_refined_tool").is_not_null()
    )
    if bad.height:
        examples = bad["case_id"].to_list()[:10]
        return CheckResult(
            "specific_codes_never_refined",
            "structural",
            False,
            f"{bad.height} non-broad-code in-scope row(s) refined "
            "(final_tool_id != tool_id or narrative_refined_tool set)",
            count=bad.height,
            examples=examples,
        )
    return CheckResult(
        "specific_codes_never_refined",
        "structural",
        True,
        "Every non-broad-code in-scope row keeps final_tool_id == tool_id, no refinement",
        count=0,
    )


def check_years_processed(ctx: QAContext) -> CheckResult:
    """At least one year must parse successfully; otherwise every other check passes vacuously
    and an empty build would replace the committed release."""
    processed = [y for y in ctx.years if y not in ctx.parse_failures]
    if not processed:
        detail = (
            "No NEISS year was processed"
            if not ctx.years
            else f"All {len(ctx.years)} requested year(s) failed to parse"
        )
        return CheckResult("years_processed", "structural", False, detail, count=0)
    return CheckResult(
        "years_processed",
        "structural",
        True,
        f"{len(processed)} year(s) processed",
        count=len(processed),
    )


STRUCTURAL_CHECKS: list[CheckFn] = [
    check_years_processed,
    check_header_contract,
    check_unique_case_ids_within_year,
    check_no_unparseable_rows,
    check_weights_positive,
    check_treatment_dates_in_file_year,
    check_unique_case_ids_global,
    check_design_psu_covers_fact_cases,
    check_bridge_products_reference_fact,
    check_known_body_part_codes,
    check_known_diagnosis_codes,
    check_known_disposition_codes,
    check_known_location_codes,
    check_known_sex_codes,
    check_in_scope_product_mapping,
    check_ages_in_valid_domain,
    check_secondary_fill_rate_by_era,
    check_enrichment_one_row_per_case,
    check_specific_codes_never_refined,
]

ADVISORY_CHECKS: list[CheckFn] = [
    check_rejoined_records,
    check_cp1252_fallback_years,
    check_yoy_workshop_volume_within_40pct,
    check_non_taxonomy_coproduct_codes,
    check_missing_narratives,
    check_secondary_field_availability,
    check_workshop_case_volume_sanity,
    check_classifier_coverage,
    check_broad_code_refinement_rate,
    check_aggregate_cells_omitted,
]


def run_all(ctx: QAContext) -> QAReport:
    """Run every registered check to completion. Exception -> failed structural-category result
    keyed by the raising check's own category; BaseException propagates uncaught."""
    results: list[CheckResult] = []
    for fn in (*STRUCTURAL_CHECKS, *ADVISORY_CHECKS):
        category: Literal["structural", "advisory"] = (
            "structural" if fn in STRUCTURAL_CHECKS else "advisory"
        )
        check_id = fn.__name__.removeprefix("check_")
        try:
            results.append(fn(ctx))
        except Exception as e:  # noqa: BLE001 - intentional: BaseException must propagate
            results.append(
                CheckResult(
                    check_id=check_id,
                    category=category,
                    passed=False,
                    detail=f"check raised {type(e).__name__}: {e}",
                )
            )

    structural_failures = [r for r in results if r.category == "structural" and not r.passed]
    advisory_flags = [r for r in results if r.category == "advisory" and not r.passed]
    return QAReport(
        results=results,
        aborted=bool(structural_failures),
        structural_failure_count=len(structural_failures),
        advisory_flag_count=len(advisory_flags),
        generated_at=datetime.now(UTC).isoformat(),
        report_version=REPORT_VERSION,
    )


def _report_to_dict(report: QAReport) -> dict:
    return {
        "aborted": report.aborted,
        "structural_failure_count": report.structural_failure_count,
        "advisory_flag_count": report.advisory_flag_count,
        "generated_at": report.generated_at,
        "report_version": report.report_version,
        "count_semantics": _COUNT_SEMANTICS_SENTENCE,
        "results": [
            {
                "check_id": r.check_id,
                "category": r.category,
                "passed": r.passed,
                "detail": r.detail,
                "count": r.count,
                "examples": [str(e) for e in r.examples],
            }
            for r in report.results
        ],
    }


def _check_table(results: list[CheckResult]) -> list[str]:
    lines = ["| check_id | result | count | detail |", "|---|---|---|---|"]
    for r in results:
        result_str = "PASS" if r.passed else "FAIL"
        detail = r.detail.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {r.check_id} | {result_str} | {r.count} | {detail} |")
    return lines


def _render_markdown(report: QAReport, ctx: QAContext | None = None) -> str:
    """D-20/QA-01: title, generated_at + processed years, STATUS, counts-semantics sentence,
    a Structural section then an Advisory section (each its own check_id|result|count|detail
    table), a per-year secondary-field-availability table, and fenced-code examples."""
    status = (
        "PASSED"
        if not report.aborted
        else f"ABORTED ({report.structural_failure_count} structural failures)"
    )
    processed_years = sorted(ctx.years) if ctx is not None else []
    years_str = ", ".join(str(y) for y in processed_years) if processed_years else "none"

    lines = [
        "# NEISS workshop pipeline: QA report",
        "",
        f"STATUS: {status}",
        "",
        _COUNT_SEMANTICS_SENTENCE,
        "",
        f"Generated at: {report.generated_at}",
        f"Processed years: {years_str}",
        "",
    ]

    structural = [r for r in report.results if r.category == "structural"]
    advisory = [r for r in report.results if r.category == "advisory"]

    lines.append("## Structural")
    lines.append("")
    lines.extend(_check_table(structural))
    lines.append("")
    lines.append("## Advisory")
    lines.append("")
    lines.extend(_check_table(advisory))

    if ctx is not None:
        secondary_rows = {
            y: s.secondary_fill_rates
            for y, s in sorted(ctx.year_stats.items())
            if s.secondary_fill_rates
        }
        if secondary_rows:
            lines.append("")
            lines.append("## Secondary-field availability by year")
            lines.append("")
            lines.append("| year | body_part_2 | diagnosis_2 | product_3 |")
            lines.append("|---|---|---|---|")
            for y, r in secondary_rows.items():
                lines.append(
                    f"| {y} | {r.get('body_part_2', 0.0):.1%} | "
                    f"{r.get('diagnosis_2', 0.0):.1%} | {r.get('product_3', 0.0):.1%} |"
                )

    examples_present = [r for r in report.results if r.examples]
    if examples_present:
        lines.append("")
        lines.append("## Examples")
        for r in examples_present:
            lines.append("")
            lines.append(f"### {r.check_id}")
            lines.append("```")
            for ex in r.examples:
                lines.append(str(ex))
            lines.append("```")

    return "\n".join(lines) + "\n"


def write_reports(report: QAReport, metadata_dir: Path, ctx: QAContext | None = None) -> None:
    metadata_dir = Path(metadata_dir)
    json_payload = json.dumps(_report_to_dict(report), indent=2)
    atomic_write_text(metadata_dir / "qa_report.json", json_payload)
    atomic_write_text(metadata_dir / "qa_report.md", _render_markdown(report, ctx))
