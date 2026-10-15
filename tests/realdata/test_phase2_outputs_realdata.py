"""Opt-in real-data smoke tests for Phase 2 pipeline integration (D-22, must_haves 1-4) and the
D-11 aggregates-equal-query-path spot-check. Reads the already-committed data/processed +
data/metadata release; never re-runs the pipeline. Run explicitly with:
    uv run pytest -m realdata tests/realdata/test_phase2_outputs_realdata.py
"""

from __future__ import annotations

import json
import os

import polars as pl
import pytest

from wii.metrics.aggregates import _windows
from wii.metrics.db import connect
from wii.metrics.estimator import estimate_by, estimate_total
from wii.metrics.filters import CaseFilter
from wii.metrics.severity import severity_profile
from wii.narratives import CLASSIFIER_VERSION
from wii.narratives.schema import MAINTENANCE_CONTEXTS, POWER_STATES
from wii.utils.paths import repo_root

pytestmark = pytest.mark.realdata

EXPECTED_CASE_COUNT = 63_425
SAMPLE_SEED = 20260915
SAMPLE_SIZE = 20
_TOL = 1e-6


def _processed_dir():
    return repo_root() / "data" / "processed"


def _metadata_dir():
    return repo_root() / "data" / "metadata"


def test_narrative_enrichment_one_row_per_case_all_rules():
    fact_cases = pl.read_parquet(_processed_dir() / "fact_cases.parquet")
    enrichment = pl.read_parquet(_processed_dir() / "narrative_enrichment.parquet")

    assert fact_cases.height == EXPECTED_CASE_COUNT
    assert enrichment.height == EXPECTED_CASE_COUNT
    assert enrichment["case_id"].n_unique() == EXPECTED_CASE_COUNT
    assert set(enrichment["case_id"].to_list()) == set(fact_cases["case_id"].to_list())
    assert (enrichment["classification_source"] == "rules").all()


def test_bridge_case_products_provenance_committed():
    bridge = pl.read_parquet(_processed_dir() / "bridge_case_products.parquet")
    dim_product = pl.read_parquet(_processed_dir() / "dim_product.parquet")

    in_scope = bridge.filter(pl.col("in_scope"))
    assert in_scope.height > 0
    assert in_scope["classification_confidence"].null_count() == 0

    broad_codes = frozenset(dim_product.filter(pl.col("broad"))["product_code"].to_list())
    non_broad_in_scope = in_scope.filter(~pl.col("product_code").is_in(list(broad_codes)))
    assert non_broad_in_scope["narrative_refined_tool"].null_count() == non_broad_in_scope.height
    assert (non_broad_in_scope["final_tool_id"] == non_broad_in_scope["tool_id"]).all()

    refined_rows = in_scope.filter(pl.col("narrative_refined_tool").is_not_null())
    refined_codes = frozenset(refined_rows["product_code"].to_list())
    assert refined_codes <= broad_codes


def test_dim_tool_has_31_rows_27_official_4_refined():
    dim_tool = pl.read_parquet(_processed_dir() / "dim_tool.parquet")
    assert dim_tool.height == 31
    assert dim_tool.filter(pl.col("source") == "official").height == 27
    refined = dim_tool.filter(pl.col("source") == "narrative_refined")
    assert refined.height == 4
    assert set(refined["tool_id"].to_list()) == {
        "miter_saw",
        "planer",
        "angle_grinder",
        "bench_grinder",
    }


def test_data_metadata_and_qa_report_record_real_classifier_numbers():
    data_metadata = json.loads((_metadata_dir() / "data_metadata.json").read_text())
    assert data_metadata["enrichment"]["classifier_version"] == CLASSIFIER_VERSION
    assert data_metadata["enrichment"]["cases"] == EXPECTED_CASE_COUNT

    qa_report = json.loads((_metadata_dir() / "qa_report.json").read_text())
    results_by_id = {r["check_id"]: r for r in qa_report["results"]}

    assert results_by_id["enrichment_one_row_per_case"]["passed"] is True
    assert results_by_id["specific_codes_never_refined"]["passed"] is True

    coverage_detail = results_by_id["classifier_coverage"]["detail"]
    assert "(100.0%)" in coverage_detail

    refinement_detail = results_by_id["broad_code_refinement_rate"]["detail"]
    assert "(0.0%)" not in refinement_detail


def _con():
    return connect(_processed_dir())


def _latest_year(con) -> int:
    return int(con.execute("SELECT MAX(year) FROM fact_cases").fetchone()[0])


_FILTER_BUILDERS = {
    "agg_tool_body_region": lambda row, years: CaseFilter(
        years=years, body_regions=(row["body_region"],), body_basis=row["basis"]
    ),
    "agg_tool_diagnosis": lambda row, years: CaseFilter(
        years=years,
        diagnosis_categories=(row["diagnosis_category"],),
        diagnosis_basis=row["basis"],
    ),
    "agg_tool_mechanism": lambda row, years: CaseFilter(
        years=years, mechanisms=(row["mechanism"],)
    ),
    "agg_tool_disposition": lambda row, years: CaseFilter(
        years=years, dispositions=(row["disposition_code"],)
    ),
}


@pytest.mark.parametrize(
    "table_name",
    ["agg_tool_body_region", "agg_tool_diagnosis", "agg_tool_mechanism", "agg_tool_disposition"],
)
def test_agg_tool_dimension_table_matches_query_path_on_random_sample(table_name):
    """D-11: 20 randomly seeded rows per committed tool-dimension agg table must equal the
    live wii.metrics query path for the equivalent CaseFilter."""
    con = _con()
    latest_year = _latest_year(con)
    windows = dict(_windows(con, latest_year))
    df = pl.read_parquet(_processed_dir() / f"{table_name}.parquet")
    sample = df.sample(n=min(SAMPLE_SIZE, df.height), seed=SAMPLE_SEED)
    builder = _FILTER_BUILDERS[table_name]
    for row in sample.iter_rows(named=True):
        years_range = windows[row["window"]]
        flt = builder(row, years_range)
        expected = estimate_by(con, flt, by="tool")
        match_rows = expected.filter(pl.col("group_id") == row["group_id"])
        assert match_rows.height == 1
        match = match_rows.row(0, named=True)
        assert row["estimate"] == pytest.approx(match["estimate"], rel=_TOL)
        assert row["raw_n"] == match["raw_n"]
        assert row["stable"] == match["stable"]


def test_agg_tool_year_matches_query_path_on_random_sample():
    con = _con()
    df = pl.read_parquet(_processed_dir() / "agg_tool_year.parquet")
    sample = df.sample(n=min(SAMPLE_SIZE, df.height), seed=SAMPLE_SEED)
    for row in sample.iter_rows(named=True):
        flt = CaseFilter(years=(row["year"], row["year"]))
        expected = estimate_by(con, flt, by="tool")
        match = expected.filter(pl.col("group_id") == row["group_id"]).row(0, named=True)
        assert row["estimate"] == pytest.approx(match["estimate"], rel=_TOL)
        assert row["raw_n"] == match["raw_n"]
        assert row["stable"] == match["stable"]


def test_agg_overall_year_matches_query_path_on_random_sample():
    con = _con()
    df = pl.read_parquet(_processed_dir() / "agg_overall_year.parquet")
    sample = df.sample(n=min(SAMPLE_SIZE, df.height), seed=SAMPLE_SEED)
    for row in sample.iter_rows(named=True):
        flt = CaseFilter(years=(row["year"], row["year"]))
        expected = estimate_total(con, flt)
        assert row["estimate"] == pytest.approx(expected.estimate, rel=_TOL)
        assert row["raw_n"] == expected.raw_n
        assert row["stable"] == expected.stable


def test_agg_tool_severity_matches_query_path_on_random_sample():
    con = _con()
    latest_year = _latest_year(con)
    windows = dict(_windows(con, latest_year))
    df = pl.read_parquet(_processed_dir() / "agg_tool_severity.parquet")
    sample = df.sample(n=min(SAMPLE_SIZE, df.height), seed=SAMPLE_SEED)
    cache: dict[str, pl.DataFrame] = {}
    for row in sample.iter_rows(named=True):
        window = row["window"]
        if window not in cache:
            cache[window] = severity_profile(con, CaseFilter(years=windows[window]), by="tool")
        expected = cache[window]
        match_rows = expected.filter(
            (pl.col("group_id") == row["group_id"]) & (pl.col("metric_id") == row["metric_id"])
        )
        assert match_rows.height == 1
        match = match_rows.row(0, named=True)
        if match["share"] is None:
            assert row["share"] is None
        else:
            assert row["share"] == pytest.approx(match["share"], rel=_TOL)
        assert row["stable"] == match["stable"]


def test_enrichment_has_maintenance_and_power_columns():
    """D-14 (04-05): committed narrative_enrichment carries the two rules-1.2.0 fields,
    schema-valid and classifier_version-stamped on every row."""
    enrichment = pl.read_parquet(_processed_dir() / "narrative_enrichment.parquet")

    assert "maintenance_context" in enrichment.columns
    assert "power_state_mentioned" in enrichment.columns
    assert enrichment.height == EXPECTED_CASE_COUNT
    assert enrichment["case_id"].n_unique() == EXPECTED_CASE_COUNT

    maintenance_values = set(enrichment["maintenance_context"].unique().to_list())
    assert maintenance_values <= MAINTENANCE_CONTEXTS

    power_values = set(enrichment["power_state_mentioned"].unique().to_list())
    assert power_values <= POWER_STATES

    assert (enrichment["classifier_version"] == "rules-1.2.0").all()


def test_primary_mechanism_unchanged_vs_previous_release():
    """D-14 (04-05): rules-1.2.0 regeneration must not change any case's primary_mechanism
    vs. the pre-regeneration release. Set WII_PREV_ENRICHMENT to the snapshot parquet path
    to run this check; skipped when unset (no baseline to compare against)."""
    prev_path = os.environ.get("WII_PREV_ENRICHMENT")
    if not prev_path:
        pytest.skip("WII_PREV_ENRICHMENT not set; no previous-release snapshot to compare")

    prev = pl.read_parquet(prev_path).select("case_id", "primary_mechanism")
    current = pl.read_parquet(_processed_dir() / "narrative_enrichment.parquet").select(
        "case_id", "primary_mechanism"
    )

    joined = prev.join(current, on="case_id", how="inner", suffix="_current")
    assert joined.height == prev.height == current.height
    mismatches = joined.filter(pl.col("primary_mechanism") != pl.col("primary_mechanism_current"))
    assert mismatches.height == 0, (
        f"{mismatches.height} case(s) changed primary_mechanism vs previous release: "
        f"{mismatches['case_id'].to_list()[:10]}"
    )


def test_maintenance_flag_invariant_and_recall_floor():
    """D-14 (04-05): maintenance_or_setup == (maintenance_context != 'not_maintenance') for
    every committed row, and recall never drops below the pre-regeneration TRUE count (38)."""
    enrichment = pl.read_parquet(_processed_dir() / "narrative_enrichment.parquet")

    expected_flag = enrichment["maintenance_context"] != "not_maintenance"
    mismatches = enrichment.filter(enrichment["maintenance_or_setup"] != expected_flag)
    assert mismatches.height == 0

    true_count = enrichment.filter(pl.col("maintenance_or_setup")).height
    assert true_count >= 38


def test_latest_year_detection_is_real():
    """T-04-12 (04-05): the committed release must never carry a failed or offline-skipped
    latest-year detection; that would blank the footer's data year."""
    data_metadata = json.loads((_metadata_dir() / "data_metadata.json").read_text())
    detection = data_metadata["latest_year_detection"]

    assert isinstance(detection["year"], int)
    assert detection["year"] == 2025
    assert detection["method"] not in {"failed", "skipped_offline"}
