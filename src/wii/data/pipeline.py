"""Per-year orchestration: parse -> normalize -> in-scope select -> build -> QA -> promote.

D-10: each year's normalized workshop rows, design_psu slice and QA stats are cached in
`<interim>/years/{year}/`, keyed by the raw file's sha256 plus a digest of the taxonomy config.
A matching cache key skips re-parsing entirely on rerun.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

import polars as pl

from wii.data.build import (
    build_bridge_case_body_parts,
    build_bridge_case_diagnoses,
    build_bridge_case_products,
    build_fact_cases,
    select_in_scope,
)
from wii.data.design import design_psu_for_year
from wii.data.metadata import build_run_metadata, write_data_metadata
from wii.data.metadata import year_metadata as compute_year_metadata
from wii.data.normalize import decode_age, detect_schema_era, normalize_rows
from wii.data.parse import parse_year_file
from wii.data.quality import QAContext, QAReport, YearQAStats, run_all, write_reports
from wii.data.schema import HeaderDriftError, MalformedRecordError
from wii.metrics import connect as metrics_connect
from wii.metrics.aggregates import build_aggregates
from wii.narratives import CLASSIFIER_VERSION
from wii.narratives.enrich import enrich_cases
from wii.narratives.rules import CONFIG_DIR as NARRATIVES_CONFIG_DIR
from wii.taxonomy.dims import build_all_dims
from wii.taxonomy.loader import (
    ProductTaxonomy,
    in_scope_codes,
    load_age_brackets,
    load_body_regions,
    load_code_dictionary,
    load_product_taxonomy,
)
from wii.utils.atomic import atomic_write_text, write_parquet_atomic
from wii.utils.hashing import sha256_file
from wii.utils.paths import CONFIG_DIR, DataPaths

CACHE_VERSION = 4

# D-15: field name (as used in YearQAStats.unknown_codes / known_*_codes checks) -> the raw
# normalized columns that feed it.
_UNKNOWN_CODE_FIELD_COLUMNS: dict[str, list[str]] = {
    "body_part": ["body_part_code", "body_part_2_code"],
    "diagnosis": ["diagnosis_code", "diagnosis_2_code"],
    "disposition": ["disposition_code"],
    "location": ["location_code"],
    "sex": ["sex_code"],
}


@dataclass
class PipelineResult:
    report: QAReport
    years_processed: list[int]
    workshop_case_count: int
    promoted: bool
    cache_hits: list[int] = field(default_factory=list)


def _duplicate_case_id_stats(df: pl.DataFrame) -> tuple[int, list[str]]:
    if df.is_empty():
        return 0, []
    dupes = df.group_by("case_id").agg(pl.len().alias("n")).filter(pl.col("n") > 1)
    return dupes.height, dupes["case_id"].to_list()[:10]


def _config_digest() -> str:
    """sha256 of the sorted (filename, sha256) pairs of every *.yaml in CONFIG_DIR."""
    yaml_files = sorted(CONFIG_DIR.glob("*.yaml"))
    pairs = [[f.name, sha256_file(f)] for f in yaml_files]
    payload = json.dumps(pairs, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _config_sha256_map() -> dict[str, str]:
    """D-22: taxonomy config sha256s plus the narratives config YAMLs (prefixed to avoid any
    filename collision), so a rule-table edit is visible in the committed metadata."""
    taxonomy_map = {f.name: sha256_file(f) for f in sorted(CONFIG_DIR.glob("*.yaml"))}
    narratives_map = {
        f"narratives/{f.name}": sha256_file(f) for f in sorted(NARRATIVES_CONFIG_DIR.glob("*.yaml"))
    }
    return {**taxonomy_map, **narratives_map}


def _unknown_code_counts(
    df_full: pl.DataFrame, columns: list[str], known: frozenset[int]
) -> dict[int, int]:
    """Non-null values in any of `columns` absent from `known` (D-15/D-19)."""
    counts: dict[int, int] = {}
    if df_full.is_empty():
        return counts
    known_list = list(known)
    for col in columns:
        series = df_full[col]
        mask = series.is_not_null() & (~series.is_in(known_list))
        if not mask.any():
            continue
        vc = df_full.filter(mask)[col].value_counts()
        for code, count in zip(vc[col].to_list(), vc["count"].to_list(), strict=False):
            counts[int(code)] = counts.get(int(code), 0) + int(count)
    return counts


def _invalid_age_counts(df_full: pl.DataFrame) -> dict[int, int]:
    """D-14: age_raw value -> count, for rows decode_age classified as age_unit == 'invalid'."""
    if df_full.is_empty() or "age_unit" not in df_full.columns:
        return {}
    invalid_mask = df_full["age_unit"] == "invalid"
    if not invalid_mask.any():
        return {}
    vc = df_full.filter(invalid_mask)["age_raw"].value_counts()
    return {
        int(v): int(c) for v, c in zip(vc["age_raw"].to_list(), vc["count"].to_list(), strict=False)
    }


def _compute_year_qa_stats(
    df_full: pl.DataFrame,
    year: int,
    parsed_rejoined: int,
    parsed_cp1252: bool,
    workshop_count: int,
    known_code_sets: dict[str, frozenset[int]] | None = None,
    body_part_2_fill_rate: float = 0.0,
    secondary_fill_rates: dict[str, float] | None = None,
) -> YearQAStats:
    dup_count, dup_examples = _duplicate_case_id_stats(df_full)

    weight_col = df_full["weight"]
    bad_weight_mask = weight_col.is_null() | (weight_col <= 0)
    nonpos_weight_count = int(bad_weight_mask.sum())
    weight_examples = df_full.filter(bad_weight_mask)["case_id"].to_list()[:10]

    date_col = df_full["treatment_date"]
    out_of_year_mask = date_col.dt.year() != year
    out_of_year_count = int(out_of_year_mask.sum())
    date_examples = df_full.filter(out_of_year_mask)["case_id"].to_list()[:10]

    unknown_codes: dict[str, dict[int, int]] = {}
    for field_name, known in (known_code_sets or {}).items():
        counts = _unknown_code_counts(df_full, _UNKNOWN_CODE_FIELD_COLUMNS[field_name], known)
        if counts:
            unknown_codes[field_name] = counts

    return YearQAStats(
        total_rows=df_full.height,
        duplicate_case_id_count=dup_count,
        duplicate_examples=dup_examples,
        rejoined_record_count=parsed_rejoined,
        cp1252_fallback=parsed_cp1252,
        nonpositive_or_null_weight_count=nonpos_weight_count,
        weight_examples=[str(v) for v in weight_examples],
        out_of_year_date_count=out_of_year_count,
        date_examples=[str(v) for v in date_examples],
        workshop_case_count_raw=workshop_count,
        unknown_codes=unknown_codes,
        invalid_age_counts=_invalid_age_counts(df_full),
        body_part_2_fill_rate=body_part_2_fill_rate,
        secondary_fill_rates=secondary_fill_rates or {},
    )


def _read_cache_key(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def run_pipeline(
    year_files: dict[int, Path],
    paths: DataPaths,
    *,
    taxonomy: ProductTaxonomy | None = None,
    run_context: dict | None = None,
) -> PipelineResult:
    paths.ensure_dirs()
    taxonomy = taxonomy or load_product_taxonomy()
    in_scope = in_scope_codes(taxonomy)
    config_digest = _config_digest()

    # D-15: dictionaries loaded once per run, passed to both year stats and build.
    body_parts = load_code_dictionary("body_parts")
    body_regions = load_body_regions(body_parts)
    diagnoses = load_code_dictionary("diagnoses", required_attrs=("injury_category",))
    dispositions = load_code_dictionary(
        "dispositions", required_attrs=("severity_bucket", "escalated_care", "sort_order")
    )
    locations = load_code_dictionary("locations", required_attrs=("sort_order",))
    sex = load_code_dictionary("sex")
    age_brackets = load_age_brackets()
    known_code_sets: dict[str, frozenset[int]] = {
        "body_part": body_parts.codes(),
        "diagnosis": diagnoses.codes(),
        "disposition": dispositions.codes(),
        "location": locations.codes(),
        "sex": sex.codes(),
    }

    parse_failures: dict[int, dict] = {}
    year_stats: dict[int, YearQAStats] = {}
    year_metas: dict[int, dict] = {}
    workshop_frames: list[pl.DataFrame] = []
    design_psu_frames: list[pl.DataFrame] = []
    cache_hits: list[int] = []

    for year in sorted(year_files):
        path = year_files[year]
        year_cache_dir = paths.interim / "years" / str(year)
        cache_key_path = year_cache_dir / "cache_key.json"

        raw_sha256 = sha256_file(path)
        current_key = {
            "raw_sha256": raw_sha256,
            "cache_version": CACHE_VERSION,
            "config_digest": config_digest,
        }

        existing_key = _read_cache_key(cache_key_path)
        if existing_key == current_key:
            workshop_df = pl.read_parquet(year_cache_dir / "workshop.parquet")
            design_psu_year = pl.read_parquet(year_cache_dir / "design_psu.parquet")
            cached = json.loads((year_cache_dir / "year_stats.json").read_text())
            cached_qa_stats = cached["qa_stats"]
            cached_qa_stats["unknown_codes"] = {
                field: {int(code): count for code, count in counts.items()}
                for field, counts in cached_qa_stats.get("unknown_codes", {}).items()
            }
            cached_qa_stats["invalid_age_counts"] = {
                int(v): count for v, count in cached_qa_stats.get("invalid_age_counts", {}).items()
            }
            year_stats[year] = YearQAStats(**cached_qa_stats)
            year_metas[year] = cached["year_metadata"]
            workshop_frames.append(workshop_df)
            design_psu_frames.append(design_psu_year)
            cache_hits.append(year)
            continue

        try:
            parsed = parse_year_file(path, year)
            df_full = normalize_rows(parsed)
        except HeaderDriftError as e:
            parse_failures[year] = {
                "kind": "header",
                "detail": str(e),
                "exception": type(e).__name__,
            }
            continue
        except MalformedRecordError as e:
            parse_failures[year] = {
                "kind": "record",
                "detail": str(e),
                "exception": type(e).__name__,
            }
            continue

        # D-14: decode age on the FULL file before any in-scope filtering -- never drops rows.
        df_full = decode_age(df_full, age_brackets)

        workshop_count = select_in_scope(df_full, in_scope).height
        year_meta = compute_year_metadata(parsed, df_full, workshop_count)
        body_part_2_fill_rate = year_meta["fill_rate"]["body_part_2"]
        schema_era = detect_schema_era(body_part_2_fill_rate)

        # D-16: schema_era is a per-year constant, attached here so it survives to fact_cases
        # without a later join, and is baked into the cached workshop.parquet below.
        workshop_df = select_in_scope(df_full, in_scope).with_columns(
            schema_era=pl.lit(schema_era, dtype=pl.Utf8)
        )
        design_psu_year = design_psu_for_year(df_full, year)
        qa_stats = _compute_year_qa_stats(
            df_full,
            year,
            parsed.rejoined_record_count,
            parsed.cp1252_fallback,
            workshop_df.height,
            known_code_sets,
            body_part_2_fill_rate,
            secondary_fill_rates={
                "body_part_2": year_meta["fill_rate"]["body_part_2"],
                "diagnosis_2": year_meta["fill_rate"]["diagnosis_2"],
                "product_3": year_meta["fill_rate"]["product_3"],
            },
        )

        year_stats[year] = qa_stats
        year_metas[year] = year_meta
        workshop_frames.append(workshop_df)
        design_psu_frames.append(design_psu_year)

        year_cache_dir.mkdir(parents=True, exist_ok=True)
        write_parquet_atomic(workshop_df, year_cache_dir / "workshop.parquet")
        write_parquet_atomic(design_psu_year, year_cache_dir / "design_psu.parquet")
        atomic_write_text(
            year_cache_dir / "year_stats.json",
            json.dumps({"qa_stats": asdict(qa_stats), "year_metadata": year_meta}, indent=2),
        )
        atomic_write_text(cache_key_path, json.dumps(current_key, indent=2))

    if workshop_frames:
        workshop_all = pl.concat(workshop_frames, how="vertical")
    else:
        workshop_all = pl.DataFrame()

    dims = build_all_dims(
        taxonomy, body_parts, body_regions, diagnoses, dispositions, locations, sex, age_brackets
    )

    fact_cases = (
        build_fact_cases(workshop_all, dims["dim_disposition"])
        if not workshop_all.is_empty()
        else pl.DataFrame()
    )
    bridge_products = (
        build_bridge_case_products(workshop_all, taxonomy)
        if not workshop_all.is_empty()
        else pl.DataFrame()
    )
    bridge_body_parts = (
        build_bridge_case_body_parts(workshop_all)
        if not workshop_all.is_empty()
        else pl.DataFrame()
    )
    bridge_diagnoses = (
        build_bridge_case_diagnoses(workshop_all) if not workshop_all.is_empty() else pl.DataFrame()
    )
    design_psu_all = (
        pl.concat(design_psu_frames, how="vertical").sort(["year", "stratum", "psu"])
        if design_psu_frames
        else pl.DataFrame()
    )

    # D-22: enrich every fact_cases row, fill bridge_case_products provenance for in-scope rows,
    # and extend dim_tool with the 4 D-14 refined tools (source='official' | 'narrative_refined').
    enrichment_result = enrich_cases(fact_cases, bridge_products, dims["dim_product"])
    narrative_enrichment = enrichment_result.enrichment
    bridge_products = enrichment_result.bridge_products
    dims["dim_tool"] = pl.concat(
        [
            dims["dim_tool"].with_columns(source=pl.lit("official", dtype=pl.Utf8)),
            enrichment_result.dim_tool_rows.with_columns(
                source=pl.lit("narrative_refined", dtype=pl.Utf8)
            ),
        ],
        how="vertical",
    ).sort(["family_sort_order", "tool_id"])

    staging_dir = paths.interim / "_staging"
    staging_dir.mkdir(parents=True, exist_ok=True)
    write_parquet_atomic(fact_cases, staging_dir / "fact_cases.parquet")
    write_parquet_atomic(bridge_products, staging_dir / "bridge_case_products.parquet")
    write_parquet_atomic(bridge_body_parts, staging_dir / "bridge_case_body_parts.parquet")
    write_parquet_atomic(bridge_diagnoses, staging_dir / "bridge_case_diagnoses.parquet")
    write_parquet_atomic(design_psu_all, staging_dir / "design_psu.parquet")
    write_parquet_atomic(narrative_enrichment, staging_dir / "narrative_enrichment.parquet")
    for name, df in dims.items():
        write_parquet_atomic(df, staging_dir / f"{name}.parquet")

    # D-11: once the enrichment + all dims/bridges are staged, open a read connection over the
    # STAGED parquet (not yet promoted) and build the 7 default-population aggregates through the
    # public wii.metrics API. They stage alongside everything else and are promoted only if QA
    # passes below -- never built from a partial/aborted run.
    if not fact_cases.is_empty():
        latest_year = int(fact_cases["year"].max())
        metrics_con = metrics_connect(staging_dir)
        try:
            aggregates_result = build_aggregates(metrics_con, latest_year)
        finally:
            metrics_con.close()
        for name, df in aggregates_result.tables.items():
            write_parquet_atomic(df, staging_dir / f"{name}.parquet")
    else:
        aggregates_result = None

    ctx = QAContext(
        years=sorted(year_files),
        parse_failures=parse_failures,
        tables={
            "fact_cases": fact_cases,
            "bridge_case_products": bridge_products,
            "bridge_case_body_parts": bridge_body_parts,
            "bridge_case_diagnoses": bridge_diagnoses,
            "design_psu": design_psu_all,
            "narrative_enrichment": narrative_enrichment,
            **dims,
        },
        taxonomy=taxonomy,
        year_stats=year_stats,
        aggregates_omitted_cells=(
            aggregates_result.omitted_cells if aggregates_result is not None else []
        ),
    )
    report = run_all(ctx)

    promoted = False
    if not report.aborted:
        for staged_file in sorted(staging_dir.glob("*.parquet")):
            os.replace(staged_file, paths.processed / staged_file.name)
        promoted = True

        run_metadata = build_run_metadata(
            year_metas,
            taxonomy_version=taxonomy.version,
            config_sha256=_config_sha256_map(),
            cache_version=CACHE_VERSION,
            latest_year_detection=(run_context or {}).get("latest_year_detection"),
            enrichment={"classifier_version": CLASSIFIER_VERSION, **enrichment_result.stats},
            aggregates=(
                {
                    "cells_omitted_count": len(aggregates_result.omitted_cells),
                    "omitted_cells": aggregates_result.omitted_cells,
                }
                if aggregates_result is not None
                else None
            ),
        )
        write_data_metadata(paths.metadata / "data_metadata.json", run_metadata)

    write_reports(report, paths.metadata, ctx)

    return PipelineResult(
        report=report,
        years_processed=sorted(year_files),
        workshop_case_count=fact_cases.height,
        promoted=promoted,
        cache_hits=sorted(cache_hits),
    )
