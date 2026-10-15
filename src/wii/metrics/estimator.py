"""wii.metrics estimator: the public API for survey-weighted totals (D-02, D-06, D-07).

Per-tool and any-recorded rows do NOT sum to `estimate_total` -- a case associated with two
in-scope tools counts once for each tool via `estimate_by`, and once in the total via
`estimate_total` (D-07). Floats stay float64 throughout; rounding happens only at display time,
never in this layer.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, fields, replace

import duckdb
import polars as pl

from wii.metrics.filters import CaseFilter
from wii.metrics.stability import apply_suppression
from wii.metrics.variance import run_grouped_ratio, run_grouped_total, t_quantile

# by -> {grp: SQL expr, join: extra FROM-clause joins, label: "product"|"simple"|"identity",
#        label_table/label_key: dim lookup for "simple"/"product"}
# Multi-membership groups (tool, product_code, body_region "any", diagnosis "any", mechanism
# secondary) rely on run_grouped_total's DISTINCT(grp, case_id) dedup -- a case counts once per
# group it belongs to (D-07); per-group rows therefore do NOT sum to estimate_total.
_BY_SPECS: dict[str, dict[str, str | None]] = {
    "all": {
        "grp": "'all'",
        "join": "",
        "where_extra": "TRUE",
        "label": "identity",
        "label_table": None,
        "label_key": None,
    },
    "product_code": {
        "grp": "bcp.product_code",
        "join": "JOIN bridge_case_products bcp ON bcp.case_id = fc.case_id",
        "where_extra": "bcp.in_scope",
        "label": "product",
        "label_table": "dim_product",
        "label_key": "product_code",
    },
    "tool": {
        "grp": "bcp.final_tool_id",
        "join": "JOIN bridge_case_products bcp ON bcp.case_id = fc.case_id",
        "where_extra": "bcp.in_scope",
        "label": "simple",
        "label_table": "dim_tool",
        "label_key": "tool_id",
    },
    "family": {
        "grp": "bcp.family_id",
        "join": "JOIN bridge_case_products bcp ON bcp.case_id = fc.case_id",
        "where_extra": "bcp.in_scope",
        "label": "identity",
        "label_table": None,
        "label_key": None,
    },
    "year": {
        "grp": "fc.year",
        "join": "",
        "where_extra": "TRUE",
        "label": "identity",
        "label_table": None,
        "label_key": None,
    },
    "body_region": {
        "grp": "dbp.region_id",
        "join": (
            "JOIN bridge_case_body_parts bbp ON bbp.case_id = fc.case_id "
            "JOIN dim_body_part dbp ON dbp.code = bbp.body_part_code"
        ),
        "where_extra": "TRUE",
        "label": "identity",
        "label_table": None,
        "label_key": None,
    },
    "body_rollup": {
        "grp": "dbr.rollup_id",
        "join": (
            "JOIN bridge_case_body_parts bbp ON bbp.case_id = fc.case_id "
            "JOIN dim_body_rollup dbr ON dbr.body_part_code = bbp.body_part_code"
        ),
        "where_extra": "TRUE",
        "label": "identity",
        "label_table": None,
        "label_key": None,
    },
    "diagnosis": {
        "grp": "bcd.diagnosis_code",
        "join": "JOIN bridge_case_diagnoses bcd ON bcd.case_id = fc.case_id",
        "where_extra": "TRUE",
        "label": "simple",
        "label_table": "dim_diagnosis",
        "label_key": "code",
    },
    "diagnosis_category": {
        "grp": "dd.injury_category",
        "join": (
            "JOIN bridge_case_diagnoses bcd ON bcd.case_id = fc.case_id "
            "JOIN dim_diagnosis dd ON dd.code = bcd.diagnosis_code"
        ),
        "where_extra": "TRUE",
        "label": "identity",
        "label_table": None,
        "label_key": None,
    },
    "disposition": {
        "grp": "fc.disposition_code",
        "join": "",
        "where_extra": "TRUE",
        "label": "simple",
        "label_table": "dim_disposition",
        "label_key": "code",
    },
    "mechanism": {
        "grp": "ne.primary_mechanism",
        "join": "JOIN narrative_enrichment ne ON ne.case_id = fc.case_id",
        "where_extra": "TRUE",
        "label": "identity",
        "label_table": None,
        "label_key": None,
    },
    # D-14 (04-08): narrative-derived SETUP & MAINTENANCE deep dive -- same `ne` alias/join shape
    # as "mechanism" above, so both are added to `_PAIR_INCOMPATIBLE` alongside "mechanism".
    "maintenance_context": {
        "grp": "ne.maintenance_context",
        "join": "JOIN narrative_enrichment ne ON ne.case_id = fc.case_id",
        "where_extra": "TRUE",
        "label": "identity",
        "label_table": None,
        "label_key": None,
    },
    "power_state": {
        "grp": "ne.power_state_mentioned",
        "join": "JOIN narrative_enrichment ne ON ne.case_id = fc.case_id",
        "where_extra": "TRUE",
        "label": "identity",
        "label_table": None,
        "label_key": None,
    },
    # D-09/D-11: "primary" variants of tool/body_region/diagnosis_category -- unlike the
    # multi-membership "any" specs above, each case contributes to EXACTLY ONE group under these
    # three, so a Sankey stage built from them conserves flow across adjacent stages (D-09's
    # "each case contributes exactly once per stage" rule).
    "tool_primary": {
        "grp": "bcpp.final_tool_id",
        "join": (
            "JOIN (SELECT case_id, arg_min(final_tool_id, seq) AS final_tool_id "
            "FROM bridge_case_products WHERE in_scope GROUP BY case_id) bcpp "
            "ON bcpp.case_id = fc.case_id"
        ),
        "where_extra": "TRUE",
        "label": "simple",
        "label_table": "dim_tool",
        "label_key": "tool_id",
    },
    "body_region_primary": {
        "grp": "dbpp.region_id",
        "join": "JOIN dim_body_part dbpp ON dbpp.code = fc.primary_body_part_code",
        "where_extra": "TRUE",
        "label": "identity",
        "label_table": None,
        "label_key": None,
    },
    "diagnosis_category_primary": {
        "grp": "ddp.injury_category",
        "join": "JOIN dim_diagnosis ddp ON ddp.code = fc.primary_diagnosis_code",
        "where_extra": "TRUE",
        "label": "identity",
        "label_table": None,
        "label_key": None,
    },
}

_SUPPORTED_BY = set(_BY_SPECS)

# D-11: grp expressions whose underlying column is numeric (not VARCHAR) -- their composite-key
# string half must be cast back to int after `estimate_by_pair` splits the pair key.
_INT_GRP_BY = frozenset({"product_code", "year", "diagnosis", "disposition"})

_PAIR_SEP = "||"  # never appears in a tool_id/mechanism/region_id/diagnosis_category/disposition
# label (all snake_case identifiers or small ints) -- verified no collision risk (RESEARCH.md).

# by1/by2 combinations that would join the SAME table alias twice in one FROM clause (a DuckDB
# syntax error) -- rejected with a clear ValueError before that error can occur.
_PAIR_INCOMPATIBLE: frozenset[frozenset[str]] = frozenset(
    {
        frozenset({"tool", "product_code"}),
        frozenset({"tool", "family"}),
        frozenset({"product_code", "family"}),
        frozenset({"body_region", "body_rollup"}),
        frozenset({"diagnosis", "diagnosis_category"}),
        frozenset({"mechanism", "maintenance_context"}),
        frozenset({"mechanism", "power_state"}),
        frozenset({"maintenance_context", "power_state"}),
    }
)


@dataclass(frozen=True)
class EstimateRecord:
    """One D-02/D-06 estimate row. Stability is evaluated PER BASIS: `stable`/`suppression_reason`
    apply the CPSC rule to `period_total`; `stable_annual`/`suppression_reason_annual` apply it to
    `annual_average`. A rare/low-volume group can be stable pooled across years while its annual
    average is suppressed -- this is intended (pooling is how low-volume groups become reportable).
    """

    estimate: float
    raw_n: int
    se: float
    cv: float | None
    ci_low: float | None
    ci_high: float | None
    df: int
    stable: bool
    suppression_reason: list[str]
    display_label: str
    period_total: float
    annual_average: float
    se_annual: float
    ci_low_annual: float | None
    ci_high_annual: float | None
    n_years: int
    basis: str
    stable_annual: bool
    suppression_reason_annual: list[str]


def _resolve_years(con: duckdb.DuckDBPyConnection, flt: CaseFilter) -> tuple[int, int]:
    if flt.years is not None:
        return flt.years
    row = con.execute("SELECT MIN(year), MAX(year) FROM fact_cases").fetchone()
    return (int(row[0]), int(row[1]))


def _finish_record(row: dict, n_years: int, display_label: str) -> EstimateRecord:
    estimate = float(row["estimate"] or 0.0)
    raw_n = int(row["raw_n"] or 0)
    variance = float(row["variance"] or 0.0)
    se = math.sqrt(variance) if variance > 0 else 0.0
    cv = (se / estimate) if estimate else None
    total_psus = int(row["total_psus"] or 0)
    n_strata = int(row["n_strata"] or 0)
    df = total_psus - n_strata

    if df > 0:
        tq = t_quantile(df)
        ci_low, ci_high = estimate - tq * se, estimate + tq * se
    else:
        tq = None
        ci_low = ci_high = None

    stable, reasons = apply_suppression(estimate, raw_n, cv)

    annual_average = estimate / n_years
    se_annual = se / n_years
    if tq is not None:
        ci_low_annual = annual_average - tq * se_annual
        ci_high_annual = annual_average + tq * se_annual
    else:
        ci_low_annual = ci_high_annual = None
    stable_annual, reasons_annual = apply_suppression(annual_average, raw_n, cv)

    return EstimateRecord(
        estimate=estimate,
        raw_n=raw_n,
        se=se,
        cv=cv,
        ci_low=ci_low,
        ci_high=ci_high,
        df=df,
        stable=stable,
        suppression_reason=reasons,
        display_label=display_label,
        period_total=estimate,
        annual_average=annual_average,
        se_annual=se_annual,
        ci_low_annual=ci_low_annual,
        ci_high_annual=ci_high_annual,
        n_years=n_years,
        basis="period",
        stable_annual=stable_annual,
        suppression_reason_annual=reasons_annual,
    )


def estimate_total(con: duckdb.DuckDBPyConnection, flt: CaseFilter | None = None) -> EstimateRecord:
    """D-07 total burden: distinct case_id in fact_cases after filters."""
    flt = flt if flt is not None else CaseFilter()
    lo, hi = _resolve_years(con, flt)
    n_years = hi - lo + 1
    eff = replace(flt, years=(lo, hi))
    where_sql, where_params = eff.to_sql(alias="fc")
    domain_cte = f"""
        SELECT 'total' AS grp, fc.case_id, fc.year, fc.stratum, fc.psu, fc.weight
        FROM fact_cases fc
        WHERE {where_sql}
    """
    result = run_grouped_total(con, domain_cte, where_params, (lo, hi))
    if result.height == 0:
        row = {"estimate": 0.0, "raw_n": 0, "variance": 0.0, "total_psus": 0, "n_strata": 0}
    else:
        row = result.row(0, named=True)
    return _finish_record(row, n_years, display_label="Total")


def _labels_for(con: duckdb.DuckDBPyConnection, by: str, group_ids: list) -> dict:
    spec = _BY_SPECS[by]
    if spec["label"] == "identity" or not group_ids:
        return {}
    dim_table, key_col = spec["label_table"], spec["label_key"]
    placeholders = ",".join("?" for _ in group_ids)
    if spec["label"] == "product":
        rows = con.execute(
            f"SELECT {key_col}, official_name FROM {dim_table} WHERE {key_col} IN ({placeholders})",
            group_ids,
        ).fetchall()
        return {r[0]: f"{r[0]} - {r[1]}" for r in rows}
    rows = con.execute(
        f"SELECT {key_col}, label FROM {dim_table} WHERE {key_col} IN ({placeholders})",
        group_ids,
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def _domain_cte(spec: dict, where_sql: str) -> str:
    return f"""
        SELECT {spec["grp"]} AS grp, fc.case_id, fc.year, fc.stratum, fc.psu, fc.weight
        FROM fact_cases fc
        {spec["join"]}
        WHERE {spec["where_extra"]} AND {where_sql}
    """


def estimate_by(con: duckdb.DuckDBPyConnection, flt: CaseFilter, by: str) -> pl.DataFrame:
    """D-07 grouped burden. `by` in {"all", "product_code", "tool", "family", "year",
    "body_region", "body_rollup", "diagnosis", "diagnosis_category", "disposition", "mechanism",
    "tool_primary", "body_region_primary", "diagnosis_category_primary"}. The "_primary" variants
    (D-09) join on the case's own primary_tool/primary_body_part_code/primary_diagnosis_code
    slot, so (unlike their any-basis counterparts) each case contributes to exactly one group --
    the basis a Sankey stage needs to conserve flow. `by="all"` groups the entire (post-filter)
    population into a single row with a constant
    `group_id` of `"all"` -- for a single-group, weighted, CI-bearing total/share over the whole
    filtered population (e.g. workshop-wide severity shares), never a per-tool/per-year split. A
    multi-membership case (two in-scope tools, two any-recorded body regions, ...) counts once per
    group (DISTINCT grp, case_id) -- per-group rows therefore do NOT sum to `estimate_total`'s
    single total (documented, not a bug).

    Returns a Polars frame, one row per group, group key column named `group_id`.
    """
    if by not in _SUPPORTED_BY:
        raise ValueError(f"unsupported by={by!r}, expected one of {sorted(_SUPPORTED_BY)}")
    spec = _BY_SPECS[by]

    lo, hi = _resolve_years(con, flt)
    n_years = hi - lo + 1
    eff = replace(flt, years=(lo, hi))
    where_sql, where_params = eff.to_sql(alias="fc")
    domain_cte = _domain_cte(spec, where_sql)
    result = run_grouped_total(con, domain_cte, where_params, (lo, hi))
    labels = _labels_for(con, by, result["grp"].to_list())

    records = []
    for row in result.iter_rows(named=True):
        rec = _finish_record(row, n_years, display_label=labels.get(row["grp"], str(row["grp"])))
        records.append({"group_id": row["grp"], **asdict(rec)})
    return pl.DataFrame(records)


def share_by(
    con: duckdb.DuckDBPyConnection, flt: CaseFilter, numerator: CaseFilter, by: str
) -> pl.DataFrame:
    """D-05 ratio estimator: share = numerator total / denominator total per group. The
    denominator domain is `flt` (grouped by `by`); the numerator domain is `flt AND numerator`.
    `stable` requires BOTH the numerator's own total AND the denominator's own total to pass D-04
    independently (a share can look "stable" from its own small SE while resting on a suppressed
    denominator, which CPSC does not consider reportable).
    """
    if by not in _SUPPORTED_BY:
        raise ValueError(f"unsupported by={by!r}, expected one of {sorted(_SUPPORTED_BY)}")
    spec = _BY_SPECS[by]

    # D-05/finding-2: the numerator is a SUBSET condition of the denominator, never an
    # independent filter -- its age_scope must match the denominator's (`flt`'s), or an
    # all-ages denominator (age_scope="all") would silently get an adults-only numerator
    # via CaseFilter's default age_scope="adults".
    numerator = replace(numerator, age_scope=flt.age_scope)

    lo, hi = _resolve_years(con, flt)
    eff_den = replace(flt, years=(lo, hi))
    den_where_sql, den_params = eff_den.to_sql(alias="fc")
    num_where_sql, num_params = numerator.to_sql(alias="fc")

    den_cte = _domain_cte(spec, den_where_sql)
    num_cte = _domain_cte(spec, f"{den_where_sql} AND ({num_where_sql})")
    num_cte_params = [*den_params, *num_params]

    result = run_grouped_ratio(con, num_cte, num_cte_params, den_cte, den_params, (lo, hi))
    labels = _labels_for(con, by, result["grp"].to_list())

    records = []
    for row in result.iter_rows(named=True):
        records.append(_finish_share_record(row, labels.get(row["grp"], str(row["grp"]))))
    return pl.DataFrame(records)


def _finish_share_record(row: dict, display_label: str) -> dict:
    den_estimate = float(row["den_estimate"] or 0.0)
    num_estimate = float(row["num_estimate"] or 0.0)
    share = float(row["share"]) if row["share"] is not None else None
    z_variance_sum = float(row["z_variance_sum"] or 0.0)
    share_variance = (
        z_variance_sum / (den_estimate**2) if den_estimate and z_variance_sum >= 0 else 0.0
    )
    share_se = math.sqrt(share_variance) if share_variance > 0 else 0.0

    total_psus = int(row["total_psus"] or 0)
    n_strata = int(row["n_strata"] or 0)
    df = total_psus - n_strata
    if df > 0 and share is not None:
        tq = t_quantile(df)
        share_ci_low = max(0.0, share - tq * share_se)
        share_ci_high = min(1.0, share + tq * share_se)
    else:
        share_ci_low = share_ci_high = None

    num_raw_n = int(row["num_raw_n"] or 0)
    den_raw_n = int(row["den_raw_n"] or 0)
    num_cv = (math.sqrt(row["num_variance"]) / num_estimate) if num_estimate else None
    den_cv = (math.sqrt(row["den_variance"]) / den_estimate) if den_estimate else None
    num_stable, num_reasons = apply_suppression(num_estimate, num_raw_n, num_cv)
    den_stable, den_reasons = apply_suppression(den_estimate, den_raw_n, den_cv)
    stable = num_stable and den_stable
    reasons = [f"num_{r}" for r in num_reasons] + [f"den_{r}" for r in den_reasons]

    return {
        "group_id": row["grp"],
        "display_label": display_label,
        "share": share,
        "share_se": share_se,
        "share_ci_low": share_ci_low,
        "share_ci_high": share_ci_high,
        "num_estimate": num_estimate,
        "num_raw_n": num_raw_n,
        "den_estimate": den_estimate,
        "den_raw_n": den_raw_n,
        "df": df,
        "stable": stable,
        "suppression_reason": reasons,
    }


def _pair_domain_cte(spec1: dict, spec2: dict, where_sql: str) -> str:
    """D-11: a composite `grp = spec1_grp || _PAIR_SEP || spec2_grp` domain CTE. Both sides are
    joined in FROM as usual; both specs' grp expressions are CAST to VARCHAR first so a numeric
    grp (year, disposition, ...) concatenates cleanly. Both specs' FROM/WHERE are inner joins
    (never LEFT JOIN), so a case with no matching row on either side is simply absent from the
    domain -- never a NULL half breaking the concatenation."""
    return f"""
        SELECT CAST({spec1["grp"]} AS VARCHAR) || '{_PAIR_SEP}' ||
               CAST({spec2["grp"]} AS VARCHAR) AS grp,
               fc.case_id, fc.year, fc.stratum, fc.psu, fc.weight
        FROM fact_cases fc
        {spec1["join"]}
        {spec2["join"]}
        WHERE {spec1["where_extra"]} AND {spec2["where_extra"]} AND {where_sql}
    """


_EMPTY_PAIR_EXTRA_COLUMNS = [
    "group_id_1",
    "group_id_2",
    "display_label_1",
    "display_label_2",
]


def estimate_by_pair(
    con: duckdb.DuckDBPyConnection, flt: CaseFilter, by1: str, by2: str
) -> pl.DataFrame:
    """D-11: one row per (group_id_1, group_id_2) pair actually present in the filtered domain,
    with the exact same `EstimateRecord` numeric fields (`estimate`/`raw_n`/`se`/`cv`/`ci_low`/
    `ci_high`/`stable`/`suppression_reason`/...) every other estimate in this module carries,
    plus `display_label_1`/`display_label_2`. Reuses `run_grouped_total` UNCHANGED via a
    composite-string `grp` domain CTE (`_pair_domain_cte`) -- zero new variance code (RESEARCH.md
    Pattern 2). `by1`/`by2` must both be in `_SUPPORTED_BY` and differ from each other;
    combinations that would join the same table alias twice (`_PAIR_INCOMPATIBLE`) also raise
    ValueError. An empty result returns an empty `pl.DataFrame` carrying the same column names.
    """
    if by1 not in _SUPPORTED_BY or by2 not in _SUPPORTED_BY:
        raise ValueError(
            f"unsupported by1={by1!r}/by2={by2!r}, expected both in {sorted(_SUPPORTED_BY)}"
        )
    if by1 == by2:
        raise ValueError(f"by1 and by2 must differ, got by1=by2={by1!r}")
    if frozenset((by1, by2)) in _PAIR_INCOMPATIBLE:
        raise ValueError(
            f"by1={by1!r} and by2={by2!r} join the same table alias and cannot be paired"
        )

    spec1, spec2 = _BY_SPECS[by1], _BY_SPECS[by2]
    lo, hi = _resolve_years(con, flt)
    n_years = hi - lo + 1
    eff = replace(flt, years=(lo, hi))
    where_sql, where_params = eff.to_sql(alias="fc")
    domain_cte = _pair_domain_cte(spec1, spec2, where_sql)
    result = run_grouped_total(con, domain_cte, where_params, (lo, hi))

    if result.height == 0:
        columns = [*_EMPTY_PAIR_EXTRA_COLUMNS, *(f.name for f in fields(EstimateRecord))]
        return pl.DataFrame({c: [] for c in columns})

    def _cast(value: str, by: str) -> int | str:
        return int(value) if by in _INT_GRP_BY else value

    ids1: list = []
    ids2: list = []
    for grp in result["grp"].to_list():
        g1, g2 = grp.split(_PAIR_SEP, 1)
        ids1.append(_cast(g1, by1))
        ids2.append(_cast(g2, by2))

    labels1 = _labels_for(con, by1, ids1)
    labels2 = _labels_for(con, by2, ids2)

    records = []
    for row, id1, id2 in zip(result.iter_rows(named=True), ids1, ids2, strict=True):
        l1 = labels1.get(id1, str(id1))
        l2 = labels2.get(id2, str(id2))
        rec = _finish_record(row, n_years, display_label=f"{l1} × {l2}")
        records.append(
            {
                "group_id_1": id1,
                "group_id_2": id2,
                "display_label_1": l1,
                "display_label_2": l2,
                **asdict(rec),
            }
        )
    return pl.DataFrame(records)
