"""Opt-in real-data reconciliation for wii.metrics against the official CPSC Query Builder 2024
anchor. Never run by default; run explicitly with:
    uv run pytest -m realdata tests/realdata/test_metrics_realdata.py
"""

from __future__ import annotations

import pytest

from wii.metrics import (
    CaseFilter,
    connect,
    estimate_by,
    estimate_by_pair,
    estimate_total,
    severity_profile,
)

pytestmark = pytest.mark.realdata

ALL_AGES_2024 = CaseFilter(years=(2024, 2024), age_scope="all")

# product_code -> (estimate, raw_n, cv rounded to 4dp, df) per RESEARCH.md Pattern 1 / official
# CPSC Query Builder.
_QUERY_BUILDER_2024_ANCHORS = {
    841: (33052, 560, 0.1009, 72),
    832: (9539, 172, 0.1495, 72),
    895: (15914, 276, 0.1008, 72),
    882: (9057, 154, 0.1402, 72),
    899: (27932, 445, 0.1265, 72),
    897: (10176, 168, 0.1425, 72),
    845: (20980, 370, 0.1126, 72),
    847: (14381, 251, 0.1220, 72),
    803: (1456, 23, 0.2640, 72),
    804: (1733, 29, 0.1911, 72),
}


def test_2024_table_saw_anchor_reproduces_official_query_builder():
    con = connect()
    result = estimate_by(con, ALL_AGES_2024, by="product_code")
    row = result.filter(result["group_id"] == 841).row(0, named=True)

    assert round(row["estimate"]) == 33052
    assert row["raw_n"] == 560
    assert round(row["cv"], 4) == 0.1009
    assert row["df"] == 72
    assert abs(row["ci_low"] - 26402.5) <= 1.0
    assert row["ci_high"] == pytest.approx(39701, abs=1.0)
    assert row["stable"] is True


def test_2024_ten_anchor_codes_match_official_query_builder():
    con = connect()
    result = estimate_by(con, ALL_AGES_2024, by="product_code")

    for code, (
        expected_estimate,
        expected_n,
        _expected_cv,
        _expected_df,
    ) in _QUERY_BUILDER_2024_ANCHORS.items():
        row = result.filter(result["group_id"] == code).row(0, named=True)
        assert round(row["estimate"]) == expected_estimate, f"code {code}: estimate mismatch"
        assert row["raw_n"] == expected_n, f"code {code}: raw_n mismatch"


def test_2020_2024_pooled_841_df_and_estimate_equal_sum_of_per_year():
    """D-03 multi-year pooling: df = sum of per-year (PSUs - strata); the pooled estimate equals
    the sum of the five per-year estimates within 1e-6 relative (weight sums are additive across
    independent years by construction)."""
    con = connect()
    pooled = estimate_by(con, CaseFilter(years=(2020, 2024), age_scope="all"), by="product_code")
    pooled_row = pooled.filter(pooled["group_id"] == 841).row(0, named=True)

    per_year_sum = 0.0
    per_year_df_sum = 0
    for year in range(2020, 2025):
        yearly = estimate_by(
            con, CaseFilter(years=(year, year), age_scope="all"), by="product_code"
        )
        yrow = yearly.filter(yearly["group_id"] == 841).row(0, named=True)
        per_year_sum += yrow["estimate"]
        per_year_df_sum += yrow["df"]

    assert pooled_row["df"] == per_year_df_sum
    assert pooled_row["estimate"] == pytest.approx(per_year_sum, rel=1e-6)
    assert pooled_row["n_years"] == 5
    assert pooled_row["annual_average"] == pytest.approx(pooled_row["estimate"] / 5)


def test_2024_severity_profile_for_table_saw_laceration_is_largest_diagnosis_share():
    con = connect()
    flt = CaseFilter(years=(2024, 2024), age_scope="all", tools=("table_saw",))
    profile = severity_profile(con, flt, by="tool")
    assert profile.height > 0

    diagnosis_metric_ids = {
        "amputation",
        "laceration",
        "fracture",
        "puncture",
        "foreign_body",
        "contusion",
        "avulsion",
        "crushing",
    }
    diag_rows = profile.filter(profile["metric_id"].is_in(diagnosis_metric_ids))
    assert diag_rows.height == len(diagnosis_metric_ids)
    for row in diag_rows.iter_rows(named=True):
        assert row["share"] is None or (0.0 <= row["share"] <= 1.0)

    laceration_share = diag_rows.filter(diag_rows["metric_id"] == "laceration").row(0, named=True)[
        "share"
    ]
    max_share = diag_rows["share"].max()
    assert laceration_share == pytest.approx(max_share)


def test_estimate_total_smoke():
    # sanity: estimate_total still importable and callable end-to-end against committed data
    record = estimate_total(connect(), CaseFilter(years=(2024, 2024), age_scope="all"))
    assert record.raw_n > 0


def test_estimate_by_pair_tool_mechanism_sums_to_estimate_by_tool_realdata():
    """D-11/D-15: every case has exactly one primary_mechanism, so estimate_by_pair(flt, "tool",
    "mechanism") summed over mechanisms for a fixed tool must equal estimate_by(flt, "tool")'s
    own estimate for that tool, within a relative diff of 1e-9."""
    con = connect()
    flt = CaseFilter(years=(2021, 2025), age_scope="adults")
    pairs = estimate_by_pair(con, flt, "tool", "mechanism")
    marginal = estimate_by(con, flt, "tool")

    for row in marginal.iter_rows(named=True):
        incoming = pairs.filter(pairs["group_id_1"] == row["group_id"])["estimate"].sum()
        expected = row["estimate"]
        rel_diff = abs(incoming - expected) / expected if expected else abs(incoming - expected)
        assert rel_diff < 1e-9, f"tool {row['group_id']}: {incoming} vs {expected}"


def test_sankey_stage_conservation_realdata():
    """D-09/D-15: the full 5-stage Sankey chain (tool_primary -> mechanism ->
    body_region_primary -> diagnosis_category_primary -> disposition) conserves flow on
    committed data, 2021-2025 adults, dispositions restricted to every recorded (non
    'not_recorded') code. Every adjacent-stage pair table sums to estimate_total, and every
    category's incoming/outgoing sum matches its own estimate_by marginal, within 1e-9 relative."""
    con = connect()
    disp_rows = con.execute(
        "SELECT code FROM dim_disposition WHERE severity_bucket != 'not_recorded'"
    ).fetchall()
    dispositions = tuple(sorted(r[0] for r in disp_rows))
    flt = CaseFilter(years=(2021, 2025), age_scope="adults", dispositions=dispositions)

    total = estimate_total(con, flt).estimate
    stages = [
        ("tool_primary", "mechanism"),
        ("mechanism", "body_region_primary"),
        ("body_region_primary", "diagnosis_category_primary"),
        ("diagnosis_category_primary", "disposition"),
    ]
    for by1, by2 in stages:
        pairs = estimate_by_pair(con, flt, by1, by2)
        pair_total = pairs["estimate"].sum()
        rel_diff = abs(pair_total - total) / total
        assert rel_diff < 1e-9, f"{by1}x{by2}: pair total {pair_total} vs estimate_total {total}"

        marginal1 = estimate_by(con, flt, by1)
        for row in marginal1.iter_rows(named=True):
            outgoing = pairs.filter(pairs["group_id_1"] == row["group_id"])["estimate"].sum()
            expected = row["estimate"]
            rel = abs(outgoing - expected) / expected if expected else abs(outgoing - expected)
            assert rel < 1e-9, f"{by1}={row['group_id']!r}: {outgoing} vs {expected}"

        marginal2 = estimate_by(con, flt, by2)
        for row in marginal2.iter_rows(named=True):
            incoming = pairs.filter(pairs["group_id_2"] == row["group_id"])["estimate"].sum()
            expected = row["estimate"]
            rel = abs(incoming - expected) / expected if expected else abs(incoming - expected)
            assert rel < 1e-9, f"{by2}={row['group_id']!r}: {incoming} vs {expected}"


def test_val01_all_four_years_zero_mismatches():
    """VAL-01: the metric layer reconciles with zero mismatches against every committed official
    Query Builder table (2009/2014/2019/2024)."""
    from wii.utils.paths import repo_root
    from wii.validation.query_builder import parse_official_table, reconcile_year

    con = connect()
    root = repo_root()
    total_mismatches = 0
    total_rows = 0
    for year in (2009, 2014, 2019, 2024):
        official_path = root / "data" / "official_qb" / f"official_qb_{year}_by_product.txt"
        official = parse_official_table(official_path.read_text(encoding="utf-8"))
        rows = reconcile_year(con, year, official)
        total_rows += len(rows)
        for row in rows:
            if not row.match:
                total_mismatches += 1
        assert all(r.match for r in rows), (
            f"{year}: mismatches {[(r.code, r.mismatches) for r in rows if not r.match]}"
        )

    assert total_mismatches == 0
    assert total_rows > 0
