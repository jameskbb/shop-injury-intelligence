"""Full-file design_psu builder (D-07): one row per (year, stratum, psu) observed in the FULL
normalized file, before any in-scope/taxonomy filtering. PSUs with zero workshop cases still get
a row here -- required for correct variance in Phase 2's Taylor linearization."""

from __future__ import annotations

import polars as pl


def design_psu_for_year(df_full: pl.DataFrame, year: int) -> pl.DataFrame:
    if df_full.is_empty():
        return pl.DataFrame(
            schema={
                "year": pl.Int16,
                "stratum": pl.Utf8,
                "psu": pl.Int32,
                "full_file_case_count": pl.Int64,
                "full_file_weight_sum": pl.Float64,
            }
        )

    grouped = (
        df_full.group_by(["stratum", "psu"])
        .agg(
            full_file_case_count=pl.len().cast(pl.Int64),
            full_file_weight_sum=pl.col("weight").fill_null(0.0).sum().cast(pl.Float64),
        )
        .with_columns(
            year=pl.lit(year, dtype=pl.Int16),
            psu=pl.col("psu").cast(pl.Int32),
        )
        .select(["year", "stratum", "psu", "full_file_case_count", "full_file_weight_sum"])
        .sort(["year", "stratum", "psu"])
    )
    return grouped
