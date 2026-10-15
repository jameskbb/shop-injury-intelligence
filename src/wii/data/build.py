"""In-scope case selection, fact_cases and bridge_case_products construction (D-03, D-17)."""

from __future__ import annotations

import polars as pl

from wii.taxonomy.loader import ProductTaxonomy, in_scope_codes

_PRODUCT_SLOTS = ("product_1", "product_2", "product_3")


def select_in_scope(df: pl.DataFrame, codes: frozenset[int]) -> pl.DataFrame:
    """D-03: a case is in scope if ANY of product_1/2/3 is an in-scope code."""
    codes_list = list(codes)
    mask = pl.lit(False)
    for slot in _PRODUCT_SLOTS:
        mask = mask | pl.col(slot).is_in(codes_list)
    return df.filter(mask)


def build_fact_cases(df: pl.DataFrame, dim_disposition: pl.DataFrame) -> pl.DataFrame:
    """One row per case_id. escalated_care is joined from dim_disposition (D-19). Sorted by
    (year, case_id). `df` must already carry decode_age's age_years/age_unit/age_top_coded/
    age_bracket/is_adult and a per-year schema_era column."""
    selected = df.select(
        pl.col("case_id"),
        pl.col("year"),
        pl.col("treatment_date"),
        pl.col("age_raw"),
        pl.col("age_years"),
        pl.col("age_unit"),
        pl.col("age_top_coded"),
        pl.col("age_bracket"),
        pl.col("is_adult"),
        pl.col("schema_era"),
        pl.col("sex_code"),
        pl.col("body_part_code").alias("primary_body_part_code"),
        pl.col("diagnosis_code").alias("primary_diagnosis_code"),
        pl.col("disposition_code"),
        pl.col("location_code"),
        pl.col("fire_involvement_code"),
        pl.col("alcohol_code"),
        pl.col("drug_code"),
        pl.col("narrative"),
        pl.col("stratum"),
        pl.col("psu"),
        pl.col("weight"),
        pl.col("raw_file_sha256"),
    )
    joined = selected.join(
        dim_disposition.select(pl.col("code").alias("disposition_code"), pl.col("escalated_care")),
        on="disposition_code",
        how="left",
    )
    return joined.sort(["year", "case_id"])


def build_bridge_case_products(df: pl.DataFrame, tax: ProductTaxonomy) -> pl.DataFrame:
    """One row per case x product-slot (1-3). Drop empty (0) slots. Sorted by (case_id, seq)."""
    in_scope = in_scope_codes(tax)
    adjacent_codes = frozenset(tax.adjacent.keys())
    code_to_tool: dict[int, str] = {c: pc.tool_id for c, pc in tax.codes.items()}
    code_to_family: dict[int, str] = {
        c: tax.tools[pc.tool_id].family_id for c, pc in tax.codes.items()
    }

    long = (
        df.select(["case_id", *_PRODUCT_SLOTS])
        .unpivot(
            index="case_id",
            on=list(_PRODUCT_SLOTS),
            variable_name="slot",
            value_name="product_code",
        )
        .filter(pl.col("product_code") != 0)
        .with_columns(seq=pl.col("slot").str.extract(r"(\d)$").cast(pl.Int8))
        .with_columns(is_primary=pl.col("seq") == 1)
        .drop("slot")
    )

    long = long.with_columns(
        tool_id=pl.col("product_code").replace_strict(
            code_to_tool, default=None, return_dtype=pl.Utf8
        ),
        family_id=pl.col("product_code").replace_strict(
            code_to_family, default=None, return_dtype=pl.Utf8
        ),
        in_scope=pl.col("product_code").is_in(list(in_scope)),
        is_adjacent=pl.col("product_code").is_in(list(adjacent_codes)),
    )

    # D-06: reserved Phase 2 provenance columns. final_tool_id defaults to the official tool_id
    # (non-null exactly for in-scope rows today); Phase 2 populates the other two without a
    # schema change.
    long = long.with_columns(
        narrative_refined_tool=pl.lit(None, dtype=pl.Utf8),
        final_tool_id=pl.col("tool_id"),
        classification_confidence=pl.lit(None, dtype=pl.Float32),
    )

    return long.sort(["case_id", "seq"])


def build_bridge_case_body_parts(df: pl.DataFrame) -> pl.DataFrame:
    """D-17: one row per case x body-part-slot (1-2). Slot 1 is always included; slot 2 whenever
    body_part_2_code is non-null. Body part 0 ("Internal", used with aspiration/ingestion) is a
    real code in either slot -- a blank Body_Part_2 is already null after normalization. Sorted by
    (case_id, seq)."""
    slot1 = df.select(
        pl.col("case_id"),
        pl.col("year"),
        pl.col("body_part_code"),
        pl.lit(1, dtype=pl.Int8).alias("seq"),
        pl.lit(True).alias("is_primary"),
    )
    slot2 = df.filter(pl.col("body_part_2_code").is_not_null()).select(
        pl.col("case_id"),
        pl.col("year"),
        pl.col("body_part_2_code").alias("body_part_code"),
        pl.lit(2, dtype=pl.Int8).alias("seq"),
        pl.lit(False).alias("is_primary"),
    )
    return pl.concat([slot1, slot2], how="vertical").sort(["case_id", "seq"])


def build_bridge_case_diagnoses(df: pl.DataFrame) -> pl.DataFrame:
    """D-17: one row per case x diagnosis-slot (1-2). other_diagnosis_text comes from
    other_diagnosis for seq 1 and other_diagnosis_2 for seq 2. Slot 2 only when
    diagnosis_2_code is non-null and non-zero. Sorted by (case_id, seq)."""
    slot1 = df.select(
        pl.col("case_id"),
        pl.col("year"),
        pl.col("diagnosis_code"),
        pl.col("other_diagnosis").alias("other_diagnosis_text"),
        pl.lit(1, dtype=pl.Int8).alias("seq"),
        pl.lit(True).alias("is_primary"),
    )
    slot2 = df.filter(
        pl.col("diagnosis_2_code").is_not_null() & (pl.col("diagnosis_2_code") != 0)
    ).select(
        pl.col("case_id"),
        pl.col("year"),
        pl.col("diagnosis_2_code").alias("diagnosis_code"),
        pl.col("other_diagnosis_2").alias("other_diagnosis_text"),
        pl.lit(2, dtype=pl.Int8).alias("seq"),
        pl.lit(False).alias("is_primary"),
    )
    return pl.concat([slot1, slot2], how="vertical").sort(["case_id", "seq"])
