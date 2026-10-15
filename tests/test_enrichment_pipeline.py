"""D-22 pipeline-integration tests: sync_neiss enriches every case, fills bridge provenance,
extends dim_tool, records classifier metadata in data_metadata.json, and the two new structural
QA checks (enrichment_one_row_per_case, specific_codes_never_refined) run and pass -- or abort
the pipeline when refinement provenance is corrupted."""

from __future__ import annotations

import dataclasses
import json

import polars as pl
import pytest

from tests.conftest import write_neiss_tsv
from wii.data.sync import main
from wii.narratives import CLASSIFIER_VERSION


def _run(tmp_path, records) -> tuple[dict, dict]:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    data_root = tmp_path / "data_root"
    write_neiss_tsv(raw_dir / "neiss2024.tsv", records)
    exit_code = main(
        [
            "--years",
            "2024",
            "--offline",
            "--raw-dir",
            str(raw_dir),
            "--data-root",
            str(data_root),
        ]
    )
    return exit_code, data_root


@pytest.fixture
def enrichment_records(neiss_record):
    miter_saw_case = neiss_record(
        Product_1="895",
        Narrative_1="45YOM USING A MITER SAW WHEN HAND SLIPPED INTO BLADE",
    )
    table_saw_misleading_case = neiss_record(
        Product_1="841",
        Narrative_1="50YOM CUTTING WOOD WITH MITER SAW LACERATED THUMB",
    )
    angle_grinder_case = neiss_record(
        Product_1="899",
        Narrative_1="38YOM USING ANGLE GRINDER SPARKS FLEW INTO EYE",
    )
    empty_narrative_case = neiss_record(Product_1="841", Narrative_1="")
    return {
        "miter_saw": miter_saw_case,
        "table_saw_misleading": table_saw_misleading_case,
        "angle_grinder": angle_grinder_case,
        "empty_narrative": empty_narrative_case,
    }


def test_sync_enriches_all_cases_fills_bridge_and_extends_dim_tool(tmp_path, enrichment_records):
    records = list(enrichment_records.values())
    exit_code, data_root = _run(tmp_path, records)
    assert exit_code == 0

    fact_cases = pl.read_parquet(data_root / "processed" / "fact_cases.parquet")
    enrichment = pl.read_parquet(data_root / "processed" / "narrative_enrichment.parquet")
    bridge = pl.read_parquet(data_root / "processed" / "bridge_case_products.parquet")
    dim_tool = pl.read_parquet(data_root / "processed" / "dim_tool.parquet")

    assert fact_cases.height == 4
    assert enrichment.height == 4
    assert set(enrichment["case_id"].to_list()) == set(fact_cases["case_id"].to_list())
    assert (enrichment["classification_source"] == "rules").all()

    miter_case_id = enrichment_records["miter_saw"][0]
    angle_case_id = enrichment_records["angle_grinder"][0]
    misleading_case_id = enrichment_records["table_saw_misleading"][0]
    empty_case_id = enrichment_records["empty_narrative"][0]

    miter_enr = enrichment.filter(pl.col("case_id") == miter_case_id).row(0, named=True)
    assert miter_enr["tool_subtype"] == "miter_saw"

    angle_enr = enrichment.filter(pl.col("case_id") == angle_case_id).row(0, named=True)
    assert angle_enr["tool_subtype"] == "angle_grinder"

    empty_enr = enrichment.filter(pl.col("case_id") == empty_case_id).row(0, named=True)
    assert empty_enr["tool_subtype"] == "unknown"
    assert empty_enr["primary_mechanism"] == "unknown"
    assert empty_enr["classification_source"] == "rules"

    miter_bridge = bridge.filter(pl.col("case_id") == miter_case_id).row(0, named=True)
    assert miter_bridge["narrative_refined_tool"] == "miter_saw"
    assert miter_bridge["final_tool_id"] == "miter_saw"
    assert miter_bridge["classification_confidence"] >= 0.70

    angle_bridge = bridge.filter(pl.col("case_id") == angle_case_id).row(0, named=True)
    assert angle_bridge["narrative_refined_tool"] == "angle_grinder"
    assert angle_bridge["final_tool_id"] == "angle_grinder"

    # 841 is NOT a broad code -- specific codes are never overridden by narrative text, even
    # when the narrative names a different specific tool (NARR-02).
    misleading_bridge = bridge.filter(pl.col("case_id") == misleading_case_id).row(0, named=True)
    assert misleading_bridge["narrative_refined_tool"] is None
    assert misleading_bridge["final_tool_id"] == "table_saw"

    assert dim_tool.height == 31
    refined = dim_tool.filter(pl.col("source") == "narrative_refined")
    assert refined.height == 4
    assert set(refined["tool_id"].to_list()) == {
        "miter_saw",
        "planer",
        "angle_grinder",
        "bench_grinder",
    }

    data_metadata = json.loads((data_root / "metadata" / "data_metadata.json").read_text())
    assert data_metadata["enrichment"]["classifier_version"] == CLASSIFIER_VERSION
    assert data_metadata["enrichment"]["cases"] == 4

    qa_report = json.loads((data_root / "metadata" / "qa_report.json").read_text())
    results_by_id = {r["check_id"]: r for r in qa_report["results"]}
    assert results_by_id["enrichment_one_row_per_case"]["passed"] is True
    assert results_by_id["specific_codes_never_refined"]["passed"] is True
    assert qa_report["aborted"] is False


def test_corrupted_specific_code_refinement_aborts_pipeline(
    tmp_path, enrichment_records, monkeypatch
):
    """A bad refinement on a non-broad code (841) must be caught by
    specific_codes_never_refined and abort the pipeline -- proven by monkeypatching the
    pipeline's enrich_cases seam to corrupt one row's provenance after real classification."""
    import wii.data.pipeline as pipeline_module

    real_enrich_cases = pipeline_module.enrich_cases

    def _corrupting_enrich_cases(fact_cases, bridge_products, dim_product, provider=None):
        result = real_enrich_cases(fact_cases, bridge_products, dim_product, provider=provider)
        bad_bridge = result.bridge_products.with_columns(
            final_tool_id=pl.when(pl.col("product_code") == 841)
            .then(pl.lit("miter_saw"))
            .otherwise(pl.col("final_tool_id"))
        )
        return dataclasses.replace(result, bridge_products=bad_bridge)

    monkeypatch.setattr(pipeline_module, "enrich_cases", _corrupting_enrich_cases)

    records = list(enrichment_records.values())
    exit_code, data_root = _run(tmp_path, records)
    assert exit_code == 1

    qa_report = json.loads((data_root / "metadata" / "qa_report.json").read_text())
    assert qa_report["aborted"] is True
    results_by_id = {r["check_id"]: r for r in qa_report["results"]}
    assert results_by_id["specific_codes_never_refined"]["passed"] is False
    assert not (data_root / "processed" / "fact_cases.parquet").exists()
