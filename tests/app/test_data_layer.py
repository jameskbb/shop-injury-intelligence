"""D-02/APP-05 data-layer tests: agg-path == query-path equality, cache-hit behavior, timing
(D-14: < 2 s cold default bundle, < 1 s typical filtered query).
"""

from __future__ import annotations

import time

import polars as pl
import pytest

import wii.app.data as data_module
from wii.app import filters as filters_module
from wii.metrics import CaseFilter

#: What every budget below is actually guarding: that these frames are CACHED and read from the
#: pre-aggregated tables, rather than scanning raw rows per request. The failure this catches is
#: two orders of magnitude (a per-request table scan), not a 300ms drift -- so the bound is
#: deliberately loose enough to survive a shared, noisy CI runner. A perf test that flakes gets
#: re-run on reflex, which is worse than not having one.
COLD_BUDGET_SECONDS = 3.0
#: A warm call is a cache hit and does no query work at all; anything near this bound means the
#: cache key is not matching.
WARM_BUDGET_SECONDS = 1.5


def test_default_overall_estimate_matches_query_path():
    """default_overall_estimate's annual_average must equal
    estimate_total(CaseFilter(years=latest-5 range)).annual_average within 1e-6 relative."""
    record = data_module.default_overall_estimate()
    assert record is not None

    con = data_module.get_connection()
    flt = CaseFilter(years=(record.start_year, record.end_year))
    query_path = data_module.estimate_total(con, flt)

    rel_diff = abs(record.annual_average - query_path.annual_average) / query_path.annual_average
    assert rel_diff < 1e-6


def test_cached_estimate_total_hits_cache(monkeypatch):
    """A second call with an equal CaseFilter must not re-query wii.metrics.estimate_total."""
    data_module.cached_estimate_total.clear()
    call_count = {"n": 0}
    original = data_module.estimate_total

    def spy(con, flt):
        call_count["n"] += 1
        return original(con, flt)

    monkeypatch.setattr(data_module, "estimate_total", spy)

    flt = CaseFilter(years=(2021, 2021))
    r1 = data_module.cached_estimate_total(flt)
    r2 = data_module.cached_estimate_total(flt)

    assert call_count["n"] == 1
    assert r1.annual_average == r2.annual_average


def test_default_overall_estimate_is_fast():
    data_module.default_overall_estimate.clear()
    start = time.perf_counter()
    data_module.default_overall_estimate()
    elapsed = time.perf_counter() - start
    assert elapsed < COLD_BUDGET_SECONDS


def test_cached_estimate_by_tool_is_fast():
    data_module.cached_estimate_by.clear()
    flt = CaseFilter(years=(2021, 2025))
    start = time.perf_counter()
    data_module.cached_estimate_by(flt, "tool")
    elapsed = time.perf_counter() - start
    assert elapsed < COLD_BUDGET_SECONDS


def test_mechanism_dims_query_count_is_bounded(monkeypatch):
    """A per-tool loop would need >= 31 calls per mechanism; `_mechanism_disproportionate_dims`
    must make exactly 2 `share_by` calls per mechanism (one by="tool", one by="all"), never a
    per-tool loop, independent of tool count (D-14/T-03-16)."""
    data_module.cached_share_by.clear()
    calls: list[str] = []
    original = data_module.share_by

    def spy(con, flt, numerator, by):
        calls.append(by)
        return original(con, flt, numerator, by=by)

    monkeypatch.setattr(data_module, "share_by", spy)

    flt = CaseFilter(years=(2021, 2025))
    data_module._mechanism_disproportionate_dims(flt)

    assert len(calls) == 2 * len(data_module._MECHANISM_INSIGHT_IDS)
    assert set(calls) == {"tool", "all"}


def test_mechanism_dims_cold_build_is_fast():
    """D-14 first-paint budget for the mechanism dimension -- measured ~0.82-0.94s at planning
    time for all 11 mechanisms x (by="tool" + by="all")."""
    data_module.cached_share_by.clear()
    flt = CaseFilter(years=(2021, 2025))
    start = time.perf_counter()
    data_module._mechanism_disproportionate_dims(flt)
    elapsed = time.perf_counter() - start
    assert elapsed < COLD_BUDGET_SECONDS


def test_region_share_by_tool_frame_cold_is_fast():
    """Body Map matrix budget: cold BODY PART x TOOL MATRIX frame for the default filter."""
    data_module.cached_share_by.clear()
    data_module.region_share_by_tool_frame.clear()
    flt = filters_module.default_case_filter()
    start = time.perf_counter()
    data_module.region_share_by_tool_frame(flt)
    elapsed = time.perf_counter() - start
    assert elapsed < COLD_BUDGET_SECONDS


def test_body_map_frame_cold_is_fast():
    """Body Map silhouette budget: cold body_map_frame for the default filter/measure."""
    data_module.cached_estimate_by.clear()
    data_module.cached_share_by.clear()
    data_module.body_map_frame.clear()
    data_module.region_share_frame.clear()
    data_module.region_top_categories.clear()
    data_module.cached_estimate_by_pair.clear()
    flt = filters_module.default_case_filter()
    start = time.perf_counter()
    data_module.body_map_frame(flt, "annual_average", "estimated")
    elapsed = time.perf_counter() - start
    assert elapsed < COLD_BUDGET_SECONDS


def test_body_map_frame_warm_is_a_cache_hit():
    """A second (warm-cache) body_map_frame call for the SAME filter/measure/basis."""
    flt = filters_module.default_case_filter()
    data_module.body_map_frame(flt, "annual_average", "estimated")
    start = time.perf_counter()
    data_module.body_map_frame(flt, "annual_average", "estimated")
    elapsed = time.perf_counter() - start
    assert elapsed < WARM_BUDGET_SECONDS


# ---------------------------------------------------------------------------
# Schema inference on frames whose leading rows are suppressed
# ---------------------------------------------------------------------------


def test_every_row_frame_in_the_data_layer_pins_its_schema_inference():
    """Polars types a column from the first 100 rows only.

    A suppressed row carries `None` in `value`, `ci_low` and `ci_high`, so a frame whose leading
    rows are all suppressed gets those columns typed `Null` -- and the first real float appended
    afterwards raises `ComputeError: could not append value: ... of type: f64`. This killed the
    Body Map page at RUNTIME for every narrow measure (amputation, fracture, escalated care),
    because those suppress most regions.

    Every `pl.DataFrame(...)` built from a list of dicts in this module must therefore pass
    `infer_schema_length=None`. The frames are tens of rows, so scanning all of them costs
    nothing, and the failure is invisible until a reader picks one of those measures -- which is
    exactly why this is asserted structurally rather than left to per-site judgement.
    """
    import ast

    from wii.utils.paths import repo_root

    source = (repo_root() / "src/wii/app/data.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_df = (
            isinstance(func, ast.Attribute)
            and func.attr == "DataFrame"
            and isinstance(func.value, ast.Name)
            and func.value.id == "pl"
        )
        if not is_df or not node.args:
            continue  # `pl.DataFrame()` / `pl.DataFrame(schema=...)` infer nothing
        kwargs = {kw.arg for kw in node.keywords}
        if "schema" in kwargs or "infer_schema_length" in kwargs:
            continue
        offenders.append(f"data.py:{node.lineno}")
    assert not offenders, (
        "these frames infer their schema from the first 100 rows only, so a leading run of "
        f"suppressed rows types a float column as Null: {', '.join(offenders)}"
    )


def test_a_frame_whose_leading_rows_are_suppressed_keeps_a_float_column():
    """The failure mode itself, in miniature -- so the reason for the rule above stays legible
    even if the data layer is rewritten."""
    rows = [{"value": None} for _ in range(120)] + [{"value": 0.019132}]
    with pytest.raises(pl.exceptions.ComputeError):
        pl.DataFrame(rows)
    assert pl.DataFrame(rows, infer_schema_length=None)["value"].dtype == pl.Float64
