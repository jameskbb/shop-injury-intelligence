"""D-11: default-population (adults) aggregate Parquet, precomputed via the public
`wii.metrics` API only (estimate_by / estimate_total / severity_profile) -- no new variance
code lives here. This module is a first-paint CACHE builder; the query-time path stays primary
and these tables must equal it (tested in `tests/metrics/test_aggregates.py` and the realdata
equality spot-check in `tests/realdata/test_phase2_outputs_realdata.py`).

Every table carries a `window` column in {"all_years", "latest_5"} and `age_scope="adults"`.
The 5 `agg_tool_*` tables (body_region, diagnosis, mechanism, disposition, severity) group by
`final_tool_id` (`by="tool"`) -- the OTHER dimension (region/category/mechanism/disposition) is
looped over as a `CaseFilter` value, not a second `by` grouping. `agg_tool_year` and
`agg_overall_year` are the two trend tables, grouped by individual year within each window.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import duckdb
import polars as pl

from wii.metrics.db import require_view
from wii.metrics.estimator import estimate_by, estimate_total
from wii.metrics.filters import CaseFilter
from wii.metrics.severity import severity_profile
from wii.metrics.variance import InsufficientPSUsError

AGGREGATE_NAMES: tuple[str, ...] = (
    "agg_tool_year",
    "agg_tool_body_region",
    "agg_tool_diagnosis",
    "agg_tool_mechanism",
    "agg_tool_disposition",
    "agg_tool_severity",
    "agg_overall_year",
)

WINDOW_ALL_YEARS = "all_years"
WINDOW_LATEST_5 = "latest_5"

Windows = list[tuple[str, tuple[int, int]]]


def _windows(con: duckdb.DuckDBPyConnection, latest_year: int) -> Windows:
    """The two D-11 windows: all_years (min..max year present in fact_cases) and latest_5
    (latest_year-4..latest_year, from data -- not necessarily equal to the max year present)."""
    lo, hi = con.execute("SELECT MIN(year), MAX(year) FROM fact_cases").fetchone()
    all_years_range = (int(lo), int(hi))
    latest5_range = (latest_year - 4, latest_year)
    return [(WINDOW_ALL_YEARS, all_years_range), (WINDOW_LATEST_5, latest5_range)]


def _distinct(con: duckdb.DuckDBPyConnection, sql: str) -> list:
    return [r[0] for r in con.execute(sql).fetchall() if r[0] is not None]


def _tagged(df: pl.DataFrame, **extra) -> pl.DataFrame:
    if df.height == 0:
        return df
    return df.with_columns(**{k: pl.lit(v) for k, v in extra.items()})


def _concat_or_empty(frames: list[pl.DataFrame]) -> pl.DataFrame:
    frames = [f for f in frames if f.height > 0]
    return pl.concat(frames, how="vertical") if frames else pl.DataFrame()


def _record_omission(
    omitted: list[dict], *, table: str, window: str, group: dict, exc: InsufficientPSUsError
) -> None:
    omitted.append({"table": table, "window": window, "group": group, "reason": str(exc)})


def _safe_estimate_by(
    con: duckdb.DuckDBPyConnection,
    flt: CaseFilter,
    by: str,
    *,
    omitted: list[dict],
    table: str,
    window: str,
    group: dict,
) -> pl.DataFrame:
    """`estimate_by` raises `InsufficientPSUsError` (finding 3: a dedicated `ValueError`
    subclass, never a bare `ValueError`) when a stratum active in the requested years has fewer
    than 2 PSUs -- a real, expected condition for a sparse aggregate cell (e.g. a tiny/degenerate
    synthetic fixture, or a genuinely thin year/dimension combination). This cache omits that
    cell (recording it in `omitted`) rather than aborting the whole aggregate build; any OTHER
    `ValueError` propagates uncaught. The live query path (`estimate_by` itself) still raises for
    a caller that hits the same cell directly."""
    try:
        return estimate_by(con, flt, by=by)
    except InsufficientPSUsError as exc:
        _record_omission(omitted, table=table, window=window, group=group, exc=exc)
        return pl.DataFrame()


def _safe_estimate_total(
    con: duckdb.DuckDBPyConnection,
    flt: CaseFilter,
    *,
    omitted: list[dict],
    table: str,
    window: str,
    group: dict,
):
    try:
        return estimate_total(con, flt)
    except InsufficientPSUsError as exc:
        _record_omission(omitted, table=table, window=window, group=group, exc=exc)
        return None


def _safe_severity_profile(
    con: duckdb.DuckDBPyConnection,
    flt: CaseFilter,
    by: str,
    *,
    omitted: list[dict],
    table: str,
    window: str,
    group: dict,
) -> pl.DataFrame:
    try:
        return severity_profile(con, flt, by=by)
    except InsufficientPSUsError as exc:
        _record_omission(omitted, table=table, window=window, group=group, exc=exc)
        return pl.DataFrame()


def _build_agg_tool_year(
    con: duckdb.DuckDBPyConnection, windows: Windows, omitted: list[dict]
) -> pl.DataFrame:
    frames = []
    for window_label, (lo, hi) in windows:
        for year in range(lo, hi + 1):
            result = _safe_estimate_by(
                con,
                CaseFilter(years=(year, year)),
                by="tool",
                omitted=omitted,
                table="agg_tool_year",
                window=window_label,
                group={"year": year},
            )
            frames.append(_tagged(result, window=window_label, age_scope="adults", year=year))
    return _concat_or_empty(frames)


def _build_agg_overall_year(
    con: duckdb.DuckDBPyConnection, windows: Windows, omitted: list[dict]
) -> pl.DataFrame:
    rows: list[dict] = []
    for window_label, (lo, hi) in windows:
        for year in range(lo, hi + 1):
            record = _safe_estimate_total(
                con,
                CaseFilter(years=(year, year)),
                omitted=omitted,
                table="agg_overall_year",
                window=window_label,
                group={"year": year},
            )
            if record is None:
                continue
            rows.append(
                {"year": year, "window": window_label, "age_scope": "adults", **asdict(record)}
            )
    return pl.DataFrame(rows) if rows else pl.DataFrame()


def _build_agg_tool_body_region(
    con: duckdb.DuckDBPyConnection, windows: Windows, omitted: list[dict]
) -> pl.DataFrame:
    regions = sorted(
        {
            *_distinct(con, "SELECT DISTINCT region_id FROM dim_body_part"),
            *_distinct(con, "SELECT DISTINCT rollup_id FROM dim_body_rollup"),
        }
    )
    frames = []
    for window_label, years_range in windows:
        for basis in ("any", "primary"):
            for region in regions:
                flt = CaseFilter(years=years_range, body_regions=(region,), body_basis=basis)
                result = _safe_estimate_by(
                    con,
                    flt,
                    by="tool",
                    omitted=omitted,
                    table="agg_tool_body_region",
                    window=window_label,
                    group={"basis": basis, "body_region": region},
                )
                frames.append(
                    _tagged(
                        result,
                        window=window_label,
                        age_scope="adults",
                        basis=basis,
                        body_region=region,
                    )
                )
    return _concat_or_empty(frames)


def _build_agg_tool_diagnosis(
    con: duckdb.DuckDBPyConnection, windows: Windows, omitted: list[dict]
) -> pl.DataFrame:
    categories = sorted(_distinct(con, "SELECT DISTINCT injury_category FROM dim_diagnosis"))
    frames = []
    for window_label, years_range in windows:
        for basis in ("any", "primary"):
            for category in categories:
                flt = CaseFilter(
                    years=years_range, diagnosis_categories=(category,), diagnosis_basis=basis
                )
                result = _safe_estimate_by(
                    con,
                    flt,
                    by="tool",
                    omitted=omitted,
                    table="agg_tool_diagnosis",
                    window=window_label,
                    group={"basis": basis, "diagnosis_category": category},
                )
                frames.append(
                    _tagged(
                        result,
                        window=window_label,
                        age_scope="adults",
                        basis=basis,
                        diagnosis_category=category,
                    )
                )
    return _concat_or_empty(frames)


def _build_agg_tool_mechanism(
    con: duckdb.DuckDBPyConnection, windows: Windows, omitted: list[dict]
) -> pl.DataFrame:
    # narrative_enrichment is a required view here (D-22 pipeline order: enrichment is always
    # staged before aggregates are built) -- raise the clear MissingTableError, not a raw one.
    require_view(con, "narrative_enrichment")
    mechanisms = sorted(
        _distinct(con, "SELECT DISTINCT primary_mechanism FROM narrative_enrichment")
    )
    frames = []
    for window_label, years_range in windows:
        for mechanism in mechanisms:
            flt = CaseFilter(years=years_range, mechanisms=(mechanism,))
            result = _safe_estimate_by(
                con,
                flt,
                by="tool",
                omitted=omitted,
                table="agg_tool_mechanism",
                window=window_label,
                group={"mechanism": mechanism},
            )
            frames.append(
                _tagged(result, window=window_label, age_scope="adults", mechanism=mechanism)
            )
    return _concat_or_empty(frames)


def _build_agg_tool_disposition(
    con: duckdb.DuckDBPyConnection, windows: Windows, omitted: list[dict]
) -> pl.DataFrame:
    codes = sorted(_distinct(con, "SELECT DISTINCT code FROM dim_disposition"))
    frames = []
    for window_label, years_range in windows:
        for code in codes:
            flt = CaseFilter(years=years_range, dispositions=(code,))
            result = _safe_estimate_by(
                con,
                flt,
                by="tool",
                omitted=omitted,
                table="agg_tool_disposition",
                window=window_label,
                group={"disposition_code": code},
            )
            frames.append(
                _tagged(result, window=window_label, age_scope="adults", disposition_code=code)
            )
    return _concat_or_empty(frames)


def _build_agg_tool_severity(
    con: duckdb.DuckDBPyConnection, windows: Windows, omitted: list[dict]
) -> pl.DataFrame:
    """severity_profile's own share-record shape (share/share_se/...), not the burden-total D-02
    shape -- these ARE shares (D-09/D-10), not weighted case counts."""
    frames = []
    for window_label, years_range in windows:
        result = _safe_severity_profile(
            con,
            CaseFilter(years=years_range),
            by="tool",
            omitted=omitted,
            table="agg_tool_severity",
            window=window_label,
            group={},
        )
        frames.append(_tagged(result, window=window_label, age_scope="adults"))
    return _concat_or_empty(frames)


@dataclass(frozen=True)
class AggregatesResult:
    """D-11/finding-3: the 7 aggregate tables, plus every cell omitted because its domain had
    fewer than 2 PSUs in some active stratum (`InsufficientPSUsError`) -- 0 expected on real
    data; each entry names the table, window, and group so a nonzero count is actionable."""

    tables: dict[str, pl.DataFrame]
    omitted_cells: list[dict] = field(default_factory=list)


def build_aggregates(con: duckdb.DuckDBPyConnection, latest_year: int) -> AggregatesResult:
    """D-11: the 7 default-population (adults) aggregate tables. Built exclusively through the
    public wii.metrics API (`estimate_by`, `estimate_total`, `severity_profile`) -- no new
    variance SQL. `latest_year` should be `max(fact_cases.year)` from the run being staged."""
    windows = _windows(con, latest_year)
    omitted: list[dict] = []
    tables = {
        "agg_tool_year": _build_agg_tool_year(con, windows, omitted),
        "agg_tool_body_region": _build_agg_tool_body_region(con, windows, omitted),
        "agg_tool_diagnosis": _build_agg_tool_diagnosis(con, windows, omitted),
        "agg_tool_mechanism": _build_agg_tool_mechanism(con, windows, omitted),
        "agg_tool_disposition": _build_agg_tool_disposition(con, windows, omitted),
        "agg_tool_severity": _build_agg_tool_severity(con, windows, omitted),
        "agg_overall_year": _build_agg_overall_year(con, windows, omitted),
    }
    return AggregatesResult(tables=tables, omitted_cells=omitted)
