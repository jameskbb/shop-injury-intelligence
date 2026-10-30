"""Regression: Streamlit runs each session on its own thread, and DuckDB connections are not
safe for concurrent queries. `get_connection()` must hand every caller its own cursor so
concurrent page loads cannot read each other's results (seen live as InsufficientPSUsError with
fractional n_h and ColumnNotFoundError "grp")."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from polars.testing import assert_frame_equal

from wii.app import data as data_module
from wii.metrics.estimator import share_by
from wii.metrics.filters import CaseFilter


def test_get_connection_returns_distinct_cursor_per_call():
    first = data_module.get_connection()
    second = data_module.get_connection()
    assert first is not second
    assert first.execute("SELECT COUNT(*) FROM fact_cases").fetchone()[0] > 0


def test_concurrent_share_queries_return_serial_result():
    years = data_module.available_years()
    flt = CaseFilter(years=(years[-5], years[-1]))
    numerator = CaseFilter(body_regions=("finger",), body_basis="any")
    expected = share_by(data_module.get_connection(), flt, numerator, by="all")

    barrier = threading.Barrier(8)

    def run(_: int):
        barrier.wait()
        out = []
        for _ in range(6):
            out.append(share_by(data_module.get_connection(), flt, numerator, by="all"))
        return out

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = [frame for batch in pool.map(run, range(8)) for frame in batch]

    # DuckDB sums in parallel, so serial runs already differ around 1e-16 relative; a crossed
    # result differs in shape or by whole orders of magnitude.
    for frame in results:
        assert_frame_equal(frame, expected, check_exact=False, rel_tol=1e-9)
