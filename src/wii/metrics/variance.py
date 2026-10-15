"""D-03: stratified with-replacement (ultimate cluster) Taylor-linearization variance.

This is the one reusable pattern the whole metric layer is built on (RESEARCH.md Pattern 1,
numerically re-verified against the official 2024 CPSC anchor). Every grouped estimate goes
through `grouped_total_sql`/`run_grouped_total` -- never a per-group Python loop issuing N
queries. Nothing outside wii.metrics may sum `weight` (D-01).
"""

from __future__ import annotations

import re

import duckdb
import polars as pl
from scipy.stats import t as _t_dist

from wii.metrics.db import MissingTableError

_MISSING_VIEW_RE = re.compile(r'Table with name "?(\w+)"? does not exist')


class InsufficientPSUsError(ValueError):
    """Raised only by `_check_min_psus_per_stratum`: a stratum active in the requested year
    range has fewer than 2 PSUs, so the ultimate-cluster variance formula's `n_h - 1` divisor
    would be zero/undefined. A dedicated subclass of `ValueError` (finding 3) so callers that
    want to treat this one specific, expected condition differently (e.g. `wii.metrics.aggregates`
    omitting a too-sparse cell) never have to catch-and-swallow an unrelated `ValueError` too.
    """


def _execute_pl(con: duckdb.DuckDBPyConnection, sql: str, params: list) -> pl.DataFrame:
    """Run `sql`, translating a DuckDB "table does not exist" error into `MissingTableError`
    (e.g. a `mechanisms` filter referencing the not-yet-built `narrative_enrichment` view)."""
    try:
        return con.execute(sql, params).pl()
    except duckdb.Error as exc:
        match = _MISSING_VIEW_RE.search(str(exc))
        if match:
            raise MissingTableError(f"required view {match.group(1)!r} is not registered") from exc
        raise


def t_quantile(df: int) -> float:
    """Two-sided 95% CI Student-t quantile at the given degrees of freedom."""
    return float(_t_dist.ppf(0.975, df))


def _check_min_psus_per_stratum(con: duckdb.DuckDBPyConnection, years: tuple[int, int]) -> None:
    rows = con.execute(
        """
        SELECT year || '|' || stratum AS pooled_stratum, COUNT(*) AS n_h
        FROM design_psu
        WHERE year BETWEEN ? AND ?
        GROUP BY pooled_stratum
        HAVING COUNT(*) < 2
        """,
        [years[0], years[1]],
    ).fetchall()
    if rows:
        bad = ", ".join(f"{r[0]} (n_h={r[1]})" for r in rows)
        raise InsufficientPSUsError(
            f"stratum with fewer than 2 PSUs cannot compute variance: {bad}"
        )


def grouped_total_sql(domain_cte: str) -> str:
    """Wrap a caller-supplied domain CTE yielding (grp, case_id, year, stratum, psu, weight).

    The domain is selected as DISTINCT (grp, case_id, year, stratum, psu, weight) first -- this
    is how a case carrying the same code/tool in two bridge slots counts once per group (D-07).
    Pooled keys `year|stratum` / `year|stratum|psu` are used from day one, so single-year and
    multi-year queries share one code path (D-03). Every `design_psu` row for the requested years
    contributes a PSU total, COALESCEd to 0 when it has no matching domain case (zero-domain PSUs
    are part of the variance formula, not an omission).

    Output columns per `grp`: estimate, raw_n, variance, total_psus, n_strata.
    """
    return f"""
    WITH domain_raw AS (
        {domain_cte}
    ),
    domain AS (
        SELECT DISTINCT grp, case_id, year, stratum, psu, weight FROM domain_raw
    ),
    groups AS (
        SELECT DISTINCT grp FROM domain
    ),
    domain_pooled AS (
        SELECT grp, case_id,
               year || '|' || stratum AS pooled_stratum,
               year || '|' || stratum || '|' || psu AS pooled_psu,
               weight
        FROM domain
    ),
    design_pooled AS (
        SELECT year || '|' || stratum AS pooled_stratum,
               year || '|' || stratum || '|' || psu AS pooled_psu
        FROM design_psu
        WHERE year BETWEEN ? AND ?
    ),
    psu_totals AS (
        SELECT g.grp, dp.pooled_stratum, dp.pooled_psu,
               COALESCE(SUM(dm.weight), 0) AS y_hp,
               COUNT(dm.case_id) AS n_cases
        FROM groups g
        CROSS JOIN design_pooled dp
        LEFT JOIN domain_pooled dm
          ON dm.grp = g.grp AND dm.pooled_stratum = dp.pooled_stratum
             AND dm.pooled_psu = dp.pooled_psu
        GROUP BY g.grp, dp.pooled_stratum, dp.pooled_psu
    ),
    stratum_stats AS (
        SELECT grp, pooled_stratum, COUNT(*) AS n_h, SUM(y_hp) AS y_h_total,
               AVG(y_hp) AS y_h_bar, SUM(n_cases) AS raw_n_h
        FROM psu_totals
        GROUP BY grp, pooled_stratum
    ),
    stratum_var AS (
        SELECT p.grp, s.pooled_stratum, s.n_h, s.y_h_total, s.raw_n_h,
               s.n_h::DOUBLE / (s.n_h - 1) * SUM(POWER(p.y_hp - s.y_h_bar, 2)) AS v_h
        FROM psu_totals p
        JOIN stratum_stats s USING (grp, pooled_stratum)
        GROUP BY p.grp, s.pooled_stratum, s.n_h, s.y_h_total, s.raw_n_h
    )
    SELECT grp,
           SUM(y_h_total) AS estimate,
           SUM(raw_n_h)::BIGINT AS raw_n,
           SUM(v_h) AS variance,
           SUM(n_h)::BIGINT AS total_psus,
           COUNT(*)::BIGINT AS n_strata
    FROM stratum_var
    GROUP BY grp
    ORDER BY grp
    """


def run_grouped_total(
    con: duckdb.DuckDBPyConnection,
    domain_cte: str,
    domain_params: list,
    years: tuple[int, int],
) -> pl.DataFrame:
    """Execute `grouped_total_sql`. Raises ValueError if any stratum active in `years` has fewer
    than 2 PSUs (the ultimate-cluster variance formula divides by n_h - 1).
    """
    _check_min_psus_per_stratum(con, years)
    sql = grouped_total_sql(domain_cte)
    params = [*domain_params, years[0], years[1]]
    return _execute_pl(con, sql, params)


def grouped_ratio_sql(numerator_domain_cte: str, denominator_domain_cte: str) -> str:
    """D-05 ratio estimator: share = numerator total / denominator total, linearized variance
    z_i = (y_num_i - R * y_den_i) / T_den, same stratified-WR variance sum applied to z (extends
    the verified Pattern-1 base). Both domain CTEs yield (grp, case_id, year, stratum, psu,
    weight); the denominator group set is authoritative (a case matching the numerator condition
    always also matches the (wider) denominator condition).

    Correlated per-cell subqueries (rather than a join-then-aggregate) keep the numerator and
    denominator PSU sums independent -- no risk of one side's row multiplying the other's.

    Returns one row per grp: share, z_variance_sum (raw stratified-WR sum of z_i, NOT yet divided
    by T_den^2 -- callers finish that division so a T_den of 0 never causes a SQL error), plus the
    numerator/denominator totals+raw_n+variance and total_psus/n_strata for each side's own D-04
    suppression check.
    """
    return f"""
    WITH num_raw AS (
        {numerator_domain_cte}
    ),
    den_raw AS (
        {denominator_domain_cte}
    ),
    num_domain AS (
        SELECT DISTINCT grp, case_id, year, stratum, psu, weight FROM num_raw
    ),
    den_domain AS (
        SELECT DISTINCT grp, case_id, year, stratum, psu, weight FROM den_raw
    ),
    groups AS (
        SELECT DISTINCT grp FROM den_domain
    ),
    num_pooled AS (
        SELECT grp, case_id,
               year || '|' || stratum AS pooled_stratum,
               year || '|' || stratum || '|' || psu AS pooled_psu,
               weight
        FROM num_domain
    ),
    den_pooled AS (
        SELECT grp, case_id,
               year || '|' || stratum AS pooled_stratum,
               year || '|' || stratum || '|' || psu AS pooled_psu,
               weight
        FROM den_domain
    ),
    design_pooled AS (
        SELECT year || '|' || stratum AS pooled_stratum,
               year || '|' || stratum || '|' || psu AS pooled_psu
        FROM design_psu
        WHERE year BETWEEN ? AND ?
    ),
    psu_totals AS (
        SELECT g.grp, dp.pooled_stratum, dp.pooled_psu,
               COALESCE(
                   (SELECT SUM(np.weight) FROM num_pooled np
                    WHERE np.grp = g.grp AND np.pooled_stratum = dp.pooled_stratum
                          AND np.pooled_psu = dp.pooled_psu), 0
               ) AS y_num_hp,
               COALESCE(
                   (SELECT COUNT(*) FROM num_pooled np
                    WHERE np.grp = g.grp AND np.pooled_stratum = dp.pooled_stratum
                          AND np.pooled_psu = dp.pooled_psu), 0
               ) AS n_num_cases,
               COALESCE(
                   (SELECT SUM(dd.weight) FROM den_pooled dd
                    WHERE dd.grp = g.grp AND dd.pooled_stratum = dp.pooled_stratum
                          AND dd.pooled_psu = dp.pooled_psu), 0
               ) AS y_den_hp,
               COALESCE(
                   (SELECT COUNT(*) FROM den_pooled dd
                    WHERE dd.grp = g.grp AND dd.pooled_stratum = dp.pooled_stratum
                          AND dd.pooled_psu = dp.pooled_psu), 0
               ) AS n_den_cases
        FROM groups g
        CROSS JOIN design_pooled dp
    ),
    totals AS (
        SELECT grp, SUM(y_num_hp) AS t_num, SUM(y_den_hp) AS t_den,
               SUM(n_num_cases)::BIGINT AS num_raw_n, SUM(n_den_cases)::BIGINT AS den_raw_n
        FROM psu_totals
        GROUP BY grp
    ),
    num_stratum_stats AS (
        SELECT grp, pooled_stratum, COUNT(*) AS n_h, AVG(y_num_hp) AS y_bar
        FROM psu_totals
        GROUP BY grp, pooled_stratum
    ),
    num_stratum_var AS (
        SELECT p.grp, s.pooled_stratum, s.n_h,
               s.n_h::DOUBLE / (s.n_h - 1) * SUM(POWER(p.y_num_hp - s.y_bar, 2)) AS v_h
        FROM psu_totals p
        JOIN num_stratum_stats s USING (grp, pooled_stratum)
        GROUP BY p.grp, s.pooled_stratum, s.n_h
    ),
    num_variance AS (
        SELECT grp, SUM(v_h) AS num_variance, SUM(n_h)::BIGINT AS total_psus,
               COUNT(*)::BIGINT AS n_strata
        FROM num_stratum_var
        GROUP BY grp
    ),
    den_stratum_stats AS (
        SELECT grp, pooled_stratum, COUNT(*) AS n_h, AVG(y_den_hp) AS y_bar
        FROM psu_totals
        GROUP BY grp, pooled_stratum
    ),
    den_stratum_var AS (
        SELECT p.grp, s.pooled_stratum, s.n_h,
               s.n_h::DOUBLE / (s.n_h - 1) * SUM(POWER(p.y_den_hp - s.y_bar, 2)) AS v_h
        FROM psu_totals p
        JOIN den_stratum_stats s USING (grp, pooled_stratum)
        GROUP BY p.grp, s.pooled_stratum, s.n_h
    ),
    den_variance AS (
        SELECT grp, SUM(v_h) AS den_variance
        FROM den_stratum_var
        GROUP BY grp
    ),
    z_stratum_stats AS (
        SELECT p.grp, p.pooled_stratum, COUNT(*) AS n_h,
               AVG(p.y_num_hp - (t.t_num / NULLIF(t.t_den, 0)) * p.y_den_hp) AS z_bar
        FROM psu_totals p
        JOIN totals t USING (grp)
        GROUP BY p.grp, p.pooled_stratum
    ),
    z_stratum_var AS (
        SELECT p.grp, s.pooled_stratum, s.n_h,
               s.n_h::DOUBLE / (s.n_h - 1) * SUM(
                   POWER(
                       (p.y_num_hp - (t.t_num / NULLIF(t.t_den, 0)) * p.y_den_hp) - s.z_bar, 2
                   )
               ) AS v_h
        FROM psu_totals p
        JOIN totals t USING (grp)
        JOIN z_stratum_stats s USING (grp, pooled_stratum)
        GROUP BY p.grp, s.pooled_stratum, s.n_h
    ),
    share_variance AS (
        SELECT grp, SUM(v_h) AS z_variance_sum
        FROM z_stratum_var
        GROUP BY grp
    )
    SELECT t.grp,
           t.t_num / NULLIF(t.t_den, 0) AS share,
           sv.z_variance_sum,
           t.t_num AS num_estimate,
           t.num_raw_n,
           nv.num_variance,
           t.t_den AS den_estimate,
           t.den_raw_n,
           dv.den_variance,
           nv.total_psus,
           nv.n_strata
    FROM totals t
    JOIN num_variance nv USING (grp)
    JOIN den_variance dv USING (grp)
    JOIN share_variance sv USING (grp)
    ORDER BY t.grp
    """


def run_grouped_ratio(
    con: duckdb.DuckDBPyConnection,
    numerator_domain_cte: str,
    numerator_params: list,
    denominator_domain_cte: str,
    denominator_params: list,
    years: tuple[int, int],
) -> pl.DataFrame:
    """Execute `grouped_ratio_sql` (D-05 share estimator). Parameter order matches the CTE
    text order: the numerator CTE's own placeholders, then the denominator CTE's, then years.
    """
    _check_min_psus_per_stratum(con, years)
    sql = grouped_ratio_sql(numerator_domain_cte, denominator_domain_cte)
    params = [*numerator_params, *denominator_params, years[0], years[1]]
    return _execute_pl(con, sql, params)
