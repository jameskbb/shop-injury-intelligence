"""Build an all-typed snake_case Polars frame from parsed raw rows, with strict casts.

Race/Other_Race/Hispanic are dropped per D-09. Blank handling and target dtypes follow the
RAW_TO_SNAKE interfaces table exactly. Any cast failure raises TypeCoercionError naming year,
column and the first offending raw value -- a bad value is never silently coerced to null.
"""

from __future__ import annotations

import polars as pl

from wii.data.parse import ParsedYear
from wii.data.schema import DROPPED_RAW_COLUMNS, RAW_TO_SNAKE, TypeCoercionError
from wii.taxonomy.loader import AgeBrackets

# (snake_name, dtype, blank_handling) -- blank_handling in {"error", "null", "zero"}.
# "error": blank or unparseable -> TypeCoercionError.
# "null": blank -> null; unparseable non-blank -> TypeCoercionError.
# "zero": blank -> 0; unparseable non-blank -> TypeCoercionError.
COLUMN_SPECS: list[tuple[str, pl.DataType, str]] = [
    ("case_id", pl.Utf8, "error"),
    ("treatment_date", pl.Date, "error"),
    ("age_raw", pl.Int16, "error"),
    ("sex_code", pl.Int8, "error"),
    ("body_part_code", pl.Int16, "error"),
    ("diagnosis_code", pl.Int16, "error"),
    ("other_diagnosis", pl.Utf8, "null"),
    ("body_part_2_code", pl.Int16, "null"),
    ("diagnosis_2_code", pl.Int16, "null"),
    ("other_diagnosis_2", pl.Utf8, "null"),
    ("disposition_code", pl.Int8, "error"),
    ("location_code", pl.Int8, "error"),
    ("fire_involvement_code", pl.Int8, "error"),
    ("product_1", pl.Int16, "zero"),
    ("product_2", pl.Int16, "zero"),
    ("product_3", pl.Int16, "zero"),
    ("alcohol_code", pl.Int8, "null"),
    ("drug_code", pl.Int8, "null"),
    ("narrative", pl.Utf8, "null"),
    ("stratum", pl.Utf8, "error"),
    ("psu", pl.Int32, "error"),
    # weight: unlike the other "strict" columns, a blank/non-positive weight is NOT a parse-time
    # structural error -- it is its own dedicated QA check (weights_positive, D-19) so the report
    # names every offending case rather than aborting on the first one found.
    ("weight", pl.Float64, "null"),
]


def normalize_rows(parsed: ParsedYear) -> pl.DataFrame:
    """Build a fully-typed, snake_case Polars frame over ALL rows of the parsed year."""
    header = list(parsed.header)
    if parsed.rows:
        raw_df = pl.DataFrame(parsed.rows, schema={c: pl.Utf8 for c in header}, orient="row")
    else:
        raw_df = pl.DataFrame(schema={c: pl.Utf8 for c in header})

    raw_df = raw_df.with_columns([pl.col(c).str.strip_chars() for c in header])
    raw_df = raw_df.drop(list(DROPPED_RAW_COLUMNS))
    raw_df = raw_df.rename(RAW_TO_SNAKE)

    final_exprs: list[pl.Expr] = []
    check_exprs: dict[str, pl.Expr] = {}

    for snake_name, dtype, blank_handling in COLUMN_SPECS:
        col_expr = pl.col(snake_name)
        is_blank_expr = col_expr == ""

        if dtype == pl.Utf8:
            if blank_handling == "error":
                final_exprs.append(col_expr.alias(snake_name))
                check_exprs[snake_name] = is_blank_expr
            else:
                final_exprs.append(
                    pl.when(is_blank_expr).then(None).otherwise(col_expr).alias(snake_name)
                )
            continue

        casted_expr = (
            col_expr.str.strptime(pl.Date, format="%m/%d/%Y", strict=False)
            if dtype == pl.Date
            else col_expr.cast(dtype, strict=False)
        )
        invalid_expr = casted_expr.is_null()

        if blank_handling == "error":
            final_exprs.append(casted_expr.alias(snake_name))
            check_exprs[snake_name] = invalid_expr
        elif blank_handling == "zero":
            final_exprs.append(casted_expr.fill_null(0).alias(snake_name))
            check_exprs[snake_name] = invalid_expr & ~is_blank_expr
        else:  # "null"
            final_exprs.append(casted_expr.alias(snake_name))
            check_exprs[snake_name] = invalid_expr & ~is_blank_expr

    if check_exprs and raw_df.height:
        mask_df = raw_df.select([expr.alias(name) for name, expr in check_exprs.items()])
        for name in check_exprs:
            mask_series = mask_df[name]
            if mask_series.any():
                idx = int(mask_series.arg_true()[0])
                bad_value = raw_df[name][idx]
                raise TypeCoercionError(parsed.year, name, bad_value)

    df = raw_df.select(final_exprs)
    df = df.with_columns(
        year=pl.lit(parsed.year, dtype=pl.Int16),
        raw_file_sha256=pl.lit(parsed.raw_file_sha256, dtype=pl.Utf8),
    )
    return df


def decode_age(frame: pl.DataFrame, brackets: AgeBrackets) -> pl.DataFrame:
    """D-14: age_unit, age_years, age_top_coded, age_bracket, is_adult. Never filters rows.

    age_unit: "years" for 2..115, "months" for 201..223, "unknown" for 0, "invalid" otherwise.
    age_years: raw for years, (raw-200)/12 for months, null otherwise.
    age_bracket: months fall in the bracket containing 0 (age_0_17); unknown gets "unknown";
    invalid gets null; years resolve via the bracket ranges.
    """
    age_raw = pl.col("age_raw")

    frame = frame.with_columns(
        age_unit=(
            pl.when(age_raw == 0)
            .then(pl.lit("unknown"))
            .when((age_raw >= 2) & (age_raw <= 115))
            .then(pl.lit("years"))
            .when((age_raw >= 201) & (age_raw <= 223))
            .then(pl.lit("months"))
            .otherwise(pl.lit("invalid"))
        ),
        age_years=(
            pl.when((age_raw >= 2) & (age_raw <= 115))
            .then(age_raw.cast(pl.Float32))
            .when((age_raw >= 201) & (age_raw <= 223))
            .then((age_raw.cast(pl.Float32) - 200.0) / 12.0)
            .otherwise(None)
        ),
        age_top_coded=(age_raw == 115),
    )

    ranged = sorted(
        (b for b in brackets.brackets.values() if b.min_years is not None),
        key=lambda b: b.min_years,
    )
    years_bracket_expr = pl.lit(None, dtype=pl.Utf8)
    for b in ranged:  # ascending min_years -- each wrap makes the higher threshold outermost
        years_bracket_expr = (
            pl.when(pl.col("age_years") >= b.min_years)
            .then(pl.lit(b.id))
            .otherwise(years_bracket_expr)
        )

    unknown_bracket_id = next(b.id for b in brackets.brackets.values() if b.id == "unknown")
    months_bracket_id = next(b.id for b in ranged if b.min_years == 0)

    frame = frame.with_columns(
        age_bracket=(
            pl.when(pl.col("age_unit") == "months")
            .then(pl.lit(months_bracket_id))
            .when(pl.col("age_unit") == "unknown")
            .then(pl.lit(unknown_bracket_id))
            .when(pl.col("age_unit") == "years")
            .then(years_bracket_expr)
            .otherwise(None)
        ),
        is_adult=((pl.col("age_unit") == "years") & (pl.col("age_years") >= 18)),
    )

    return frame


def detect_schema_era(fill_rate: float) -> str:
    """D-16: era detected by Body_Part_2 non-null fill rate, never column presence."""
    return "post_2019" if fill_rate > 0.05 else "pre_2019"
