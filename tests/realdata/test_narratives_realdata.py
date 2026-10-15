"""Opt-in real-data smoke test for wii.narratives over the full committed fact_cases narrative
set (63,425 rows). Never run by default; run explicitly with:
    uv run pytest -m realdata tests/realdata/test_narratives_realdata.py
"""

from __future__ import annotations

import time

import duckdb
import pytest

from wii.narratives.classify import classify_case
from wii.taxonomy.loader import load_product_taxonomy
from wii.utils.paths import repo_root

pytestmark = pytest.mark.realdata

MAX_RUNTIME_SECONDS = 120
CP1252_YEARS = (2008, 2009, 2017)


def _connect() -> duckdb.DuckDBPyConnection:
    processed_dir = repo_root() / "data" / "processed"
    con = duckdb.connect(":memory:")
    for path in sorted(processed_dir.glob("*.parquet")):
        escaped = str(path).replace("'", "''")
        con.execute(f"CREATE VIEW \"{path.stem}\" AS SELECT * FROM read_parquet('{escaped}')")
    return con


def test_all_cases_classify_without_error_within_120s():
    con = _connect()
    tax = load_product_taxonomy()
    broad_codes = {c for c, pc in tax.codes.items() if pc.broad}

    rows = con.execute(
        """
        SELECT fc.case_id, fc.year, fc.narrative,
               list(bcp.product_code ORDER BY bcp.product_code) AS product_codes,
               list(dt.tool_id ORDER BY bcp.product_code) AS tool_ids
        FROM fact_cases fc
        JOIN bridge_case_products bcp USING (case_id)
        JOIN dim_tool dt ON dt.tool_id = bcp.tool_id
        WHERE bcp.in_scope
        GROUP BY fc.case_id, fc.year, fc.narrative
        """
    ).fetchall()

    assert len(rows) == 63_425, f"expected 63,425 cases, got {len(rows)}"

    total_refinements = 0
    total_broad_refinable_slots = 0
    unknown_mechanism = 0
    non_rules_source = 0
    cp1252_year_classified = 0
    illegal_refinement_on_non_broad: list[str] = []

    start = time.monotonic()
    for case_id, year, narrative, product_codes, tool_ids in rows:
        pairs = list(zip(product_codes, tool_ids, strict=True))
        enrichment, refinements = classify_case(narrative, pairs)

        if enrichment.classification_source != "rules":
            non_rules_source += 1
        if enrichment.primary_mechanism == "unknown":
            unknown_mechanism += 1
        if year in CP1252_YEARS and narrative:
            cp1252_year_classified += 1

        for r in refinements:
            if r.product_code not in broad_codes:
                if r.narrative_refined_tool is not None or r.final_tool_id != r.official_tool_id:
                    illegal_refinement_on_non_broad.append(str(case_id))
            else:
                total_broad_refinable_slots += 1
                if r.narrative_refined_tool is not None:
                    total_refinements += 1

    elapsed = time.monotonic() - start

    assert elapsed < MAX_RUNTIME_SECONDS, (
        f"classification took {elapsed:.1f}s, budget is {MAX_RUNTIME_SECONDS}s"
    )
    assert non_rules_source == 0
    assert illegal_refinement_on_non_broad == [], (
        f"{len(illegal_refinement_on_non_broad)} refinements on non-broad codes, "
        f"e.g. {illegal_refinement_on_non_broad[:5]}"
    )
    assert cp1252_year_classified > 0

    refinement_rate = (
        total_refinements / total_broad_refinable_slots if total_broad_refinable_slots else 0.0
    )
    unknown_pct = unknown_mechanism / len(rows) * 100

    print(f"\nrealdata: {len(rows)} cases classified in {elapsed:.1f}s")
    print(
        f"broad-code refinement rate: {refinement_rate:.1%} "
        f"({total_refinements}/{total_broad_refinable_slots})"
    )
    print(f"mechanism unknown %: {unknown_pct:.1f}%")
