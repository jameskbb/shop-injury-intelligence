"""Per-year full-file metadata (D-08) and the run-level data_metadata.json payload."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from importlib import metadata as _importlib_metadata
from pathlib import Path

import polars as pl

from wii.data.parse import ParsedYear
from wii.utils.atomic import atomic_write_text

COUNT_SEMANTICS = (
    "raw NEISS sample counts and weight sums for sanity checks; not national estimates"
)

try:
    _PACKAGE_VERSION = _importlib_metadata.version("workshop-injury-intelligence")
except _importlib_metadata.PackageNotFoundError:
    _PACKAGE_VERSION = "0.0.0+unknown"


def _non_null_share(df: pl.DataFrame, column: str) -> float:
    if df.height == 0:
        return 0.0
    return 1.0 - (df[column].null_count() / df.height)


def _non_zero_share(df: pl.DataFrame, column: str) -> float:
    if df.height == 0:
        return 0.0
    return float((df[column] != 0).sum()) / df.height


def year_metadata(parsed: ParsedYear, df_full: pl.DataFrame, workshop_count: int) -> dict:
    """D-08: per-year full-file metadata (not gated by in-scope filtering)."""
    total = df_full.height

    if total:
        # Body part 0 ("Internal") is a real slot-2 code; only null means "no second body part".
        body_part_2_populated = int(df_full["body_part_2_code"].is_not_null().sum())
        narrative_max_length = int((df_full["narrative"].fill_null("").str.len_chars().max()) or 0)
        weight_sum_full_file = float(df_full["weight"].fill_null(0.0).sum())
    else:
        body_part_2_populated = 0
        narrative_max_length = 0
        weight_sum_full_file = 0.0

    return {
        "raw_row_count": total,
        "weight_sum_full_file": weight_sum_full_file,
        "n_strata": df_full["stratum"].n_unique() if total else 0,
        "n_psu": df_full["psu"].n_unique() if total else 0,
        "fill_rate": {
            "body_part_2": _non_null_share(df_full, "body_part_2_code"),
            "diagnosis_2": _non_null_share(df_full, "diagnosis_2_code"),
            "product_3": _non_zero_share(df_full, "product_3"),
            "alcohol": _non_null_share(df_full, "alcohol_code"),
            "drug": _non_null_share(df_full, "drug_code"),
        },
        "body_part_2_populated_rate": (body_part_2_populated / total) if total else 0.0,
        "narrative_max_length": narrative_max_length,
        "rejoined_record_count": parsed.rejoined_record_count,
        "blank_line_count": parsed.blank_line_count,
        "cp1252_fallback": parsed.cp1252_fallback,
        "encoding": parsed.encoding,
        "raw_file_sha256": parsed.raw_file_sha256,
        "workshop_case_count_raw": workshop_count,
    }


def build_run_metadata(
    year_meta: dict[int, dict],
    *,
    taxonomy_version: int,
    config_sha256: dict[str, str],
    cache_version: int,
    latest_year_detection: dict | None = None,
    enrichment: dict | None = None,
    aggregates: dict | None = None,
) -> dict:
    years_sorted = sorted(year_meta.keys())
    return {
        "pipeline_run_at": datetime.now(UTC).isoformat(),
        "package_version": _PACKAGE_VERSION,
        "taxonomy_version": taxonomy_version,
        "latest_year_detection": latest_year_detection,
        "years_processed": years_sorted,
        "years": {str(y): year_meta[y] for y in years_sorted},
        "workshop_case_count_raw_total": sum(
            m["workshop_case_count_raw"] for m in year_meta.values()
        ),
        "cache_version": cache_version,
        "config_sha256": config_sha256,
        "count_semantics": COUNT_SEMANTICS,
        # D-22: classifier_version + enrich_cases' run stats (Phase 2). None for a pipeline run
        # made before Phase 2 integration or a build with zero years processed.
        "enrichment": enrichment,
        # Finding 3: D-11 aggregate build stats -- cells_omitted_count/omitted_cells (0 expected
        # on real data). None for a build with zero years processed.
        "aggregates": aggregates,
    }


def write_data_metadata(path: Path, payload: dict) -> None:
    atomic_write_text(Path(path), json.dumps(payload, indent=2))
