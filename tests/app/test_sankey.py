"""D-10 INJURY PATHWAYS Sankey readability-rule tests: top-N/"Other" collapse, unstable-
individual-link redirect, `resolve_aggregate` hard-floor gating, column conservation, node cap,
single-tool focus, node colors, real-"other"-category relabeling (B3), and D-16 performance
budgets on committed data. `charts/sankey.py` is a pure module (no Streamlit/wii.metrics calls) --
every `SankeyInput` here is hand-built or comes straight from `wii.app.data.sankey_data`.
"""

from __future__ import annotations

import time

import pytest

import wii.app.data as data_module
import wii.app.pages.mechanisms as mechanisms_page
from wii.analytics.insights import scan_guardrail_vocabulary
from wii.app import filters as filters_module
from wii.app import theme
from wii.app.charts import sankey
from wii.app.components import stability_badge
from wii.app.components.stability_badge import STATE_LIMITED, STATE_STABLE, STATE_SUPPRESSED

_DEFAULT_POINT = 100_000.0
_DEFAULT_RAW_N = 500


def _cat(id_: str, point: float, state: str = STATE_STABLE, label: str | None = None):
    return sankey.StageCategory(id=id_, label=label or id_, point=point, state=state)


def _link(
    src_id: str,
    dst_id: str,
    point: float,
    raw_n: int,
    state: str = STATE_STABLE,
    ci_low: float | None = None,
    ci_high: float | None = None,
):
    return sankey.PairLink(
        src_id=src_id,
        src_label=src_id,
        dst_id=dst_id,
        dst_label=dst_id,
        point=point,
        raw_n=raw_n,
        state=state,
        ci_low=ci_low,
        ci_high=ci_high,
    )


def _default_marginal(stage_idx: int):
    return [_cat(f"s{stage_idx}", _DEFAULT_POINT, STATE_STABLE)]


def _default_link(col: int):
    return [_link(f"s{col}", f"s{col + 1}", _DEFAULT_POINT, _DEFAULT_RAW_N, STATE_STABLE)]


def _sankey_input(
    categories: dict[int, list] | None = None,
    links: dict[int, list] | None = None,
    severe_ids: frozenset[str] = frozenset(),
    focus_label: str | None = None,
) -> sankey.SankeyInput:
    categories = categories or {}
    links = links or {}
    marginals = tuple(tuple(categories.get(s, _default_marginal(s))) for s in range(5))
    stage_pairs = tuple(tuple(links.get(c, _default_link(c))) for c in range(4))
    return sankey.SankeyInput(
        stage_pairs=stage_pairs,
        marginals=marginals,
        severe_disposition_ids=severe_ids,
        excluded_display="12,000",
        focus_tool_label=focus_label,
    )


def test_topn_keeps_six_stable_and_buckets_rest():
    region_cats = [_cat(f"r{i}", 800.0 - i * 100.0, STATE_STABLE) for i in range(1, 8)]
    region_cats.append(_cat("r8", 50.0, STATE_LIMITED))
    inp = _sankey_input(categories={2: region_cats})

    graph = sankey.collapse_pathways(inp)

    stage2 = {n["id"]: n["label"] for n in graph.nodes if n["stage"] == 2}
    kept_ids = {f"2:r{i}" for i in range(1, 7)}
    assert set(stage2) == kept_ids | {"2:__other__"}
    assert stage2["2:__other__"] == "Other"


def test_unstable_individual_link_repointed_and_counted():
    inp = _sankey_input(
        categories={
            0: [_cat("toolA", _DEFAULT_POINT, STATE_STABLE)],
            1: [_cat("mechB", 3000.0, STATE_STABLE)],
        },
        links={0: [_link("toolA", "mechB", 3000.0, 100, state=STATE_LIMITED)]},
    )

    graph = sankey.collapse_pathways(inp)

    assert graph.merged_volume == 3000.0
    node_index = {n["id"]: i for i, n in enumerate(graph.nodes)}
    bucket_link = next(
        link
        for link in graph.links
        if link["source"] == node_index["0:toolA"] and link["target"] == node_index["1:__other__"]
    )
    assert bucket_link["value"] == 3000.0
    assert bucket_link["state"] == sankey.STATE_AGGREGATE
    mech_b_idx = node_index["1:mechB"]
    assert not any(link["target"] == mech_b_idx for link in graph.links)


def test_subfloor_aggregate_not_drawn_and_counted():
    inp = _sankey_input(
        categories={
            1: [
                _cat("mechC", _DEFAULT_POINT, STATE_STABLE),
                _cat("mechD", 500.0, STATE_SUPPRESSED),
            ],
            2: [
                _cat("regionZ", _DEFAULT_POINT, STATE_STABLE),
                _cat("regionB", 100.0, STATE_SUPPRESSED),
            ],
            3: [_cat("diagX", _DEFAULT_POINT, STATE_STABLE)],
        },
        links={
            1: [_link("mechD", "regionZ", 5000.0, 40, state=STATE_SUPPRESSED)],
            2: [_link("regionB", "diagX", 800.0, 12, state=STATE_LIMITED)],
        },
    )

    graph = sankey.collapse_pathways(inp)
    node_index = {n["id"]: i for i, n in enumerate(graph.nodes)}

    drawn_agg = next(
        link
        for link in graph.links
        if link["source"] == node_index["1:__other__"] and link["target"] == node_index["2:regionZ"]
    )
    assert drawn_agg["value"] == 5000.0
    assert drawn_agg["state"] == sankey.STATE_AGGREGATE

    assert not any(
        link["source"] == node_index["2:__other__"] and link["target"] == node_index["3:diagX"]
        for link in graph.links
    )
    assert graph.hidden_volume == 800.0


def _isolated_column_input(col: int) -> sankey.SankeyInput:
    src, dst = col, col + 1
    src_cats = [_cat("A", 100_000.0, STATE_STABLE), _cat("A2", 90_000.0, STATE_STABLE)]
    dst_cats = [
        _cat("B", 9_000.0, STATE_STABLE),
        _cat("C", 100.0, STATE_SUPPRESSED),
        _cat("D", 50.0, STATE_LIMITED),
    ]
    categories: dict[int, list] = {s: [] for s in range(5)}
    categories[src] = src_cats
    categories[dst] = dst_cats
    links: dict[int, list] = {c: [] for c in range(4)}
    links[col] = [
        _link("A", "B", 5000.0, 100, state=STATE_STABLE),
        _link("A", "C", 3000.0, 30, state=STATE_STABLE),
        _link("A", "D", 100.0, 5, state=STATE_STABLE),
        _link("A2", "C", 500.0, 10, state=STATE_STABLE),
    ]
    return sankey.SankeyInput(
        stage_pairs=tuple(tuple(links[c]) for c in range(4)),
        marginals=tuple(tuple(categories[s]) for s in range(5)),
        severe_disposition_ids=frozenset(),
        excluded_display="0",
    )


def test_column_conservation_including_hidden():
    for col in range(4):
        inp = _isolated_column_input(col)
        graph = sankey.collapse_pathways(inp)
        drawn_total = sum(link["value"] for link in graph.links)
        expected = sum(inp.stage_pairs[col][i].point for i in range(len(inp.stage_pairs[col])))
        assert drawn_total + graph.hidden_volume == pytest.approx(expected, rel=1e-9)


def _many_cats(prefix: str, n: int) -> list:
    return [_cat(f"{prefix}{i}", float(1000 - i), STATE_STABLE) for i in range(n)]


def test_node_cap_all_tools():
    counts = [31, 13, 22, 24, 6]
    categories = {s: _many_cats(f"s{s}_", n) for s, n in enumerate(counts)}
    links: dict[int, list] = {}
    for col in range(4):
        src_first, src_last = categories[col][0].id, categories[col][-1].id
        dst_first, dst_last = categories[col + 1][0].id, categories[col + 1][-1].id
        links[col] = [
            _link(src_first, dst_last, 50.0, 25, state=STATE_STABLE),
            _link(src_last, dst_first, 50.0, 25, state=STATE_STABLE),
        ]
    inp = sankey.SankeyInput(
        stage_pairs=tuple(tuple(links[c]) for c in range(4)),
        marginals=tuple(tuple(categories[s]) for s in range(5)),
        severe_disposition_ids=frozenset(),
        excluded_display="0",
    )

    graph = sankey.collapse_pathways(inp)

    assert len(graph.nodes) <= sankey.NODE_CAP


def test_focus_mode_single_tool_node():
    inp = _sankey_input(
        categories={0: [_cat("table_saw", 50_000.0, STATE_STABLE, label="Table Saw")]},
        links={0: [_link("table_saw", "s1", 40_000.0, 300, state=STATE_STABLE)]},
        focus_label="Table Saw",
    )

    graph = sankey.collapse_pathways(inp)

    stage0 = [n for n in graph.nodes if n["stage"] == 0]
    assert len(stage0) == 1
    assert stage0[0]["label"] == "Table Saw"


def test_node_colors_semantic_and_severe_only_escalated():
    inp = _sankey_input(
        categories={
            0: [_cat("toolA", _DEFAULT_POINT, STATE_STABLE)],
            1: [_cat("kickback_ejection", 90_000.0, STATE_STABLE)],
            4: [_cat("2", 80_000.0, STATE_STABLE), _cat("1", 70_000.0, STATE_STABLE)],
        },
        severe_ids=frozenset({"2"}),
    )

    graph = sankey.collapse_pathways(inp)
    by_id = {n["id"]: n for n in graph.nodes}

    assert by_id["0:toolA"]["color"] == theme.MUTED
    assert by_id["1:kickback_ejection"]["color"] == theme.MECHANISM_COLORS["kickback_ejection"]
    assert by_id["4:2"]["color"] == theme.SEVERE
    assert by_id["4:1"]["color"] == theme.MUTED
    assert theme.PRIMARY not in {n["color"] for n in graph.nodes}


def test_resolve_aggregate_floors():
    ok = stability_badge.resolve_aggregate(5000.0, 40)
    assert ok.state == stability_badge.STATE_AGGREGATE
    assert ok.ci_low is None
    assert ok.ci_high is None
    assert ok.display_value == stability_badge.format_estimate(5000.0)

    low_estimate = stability_badge.resolve_aggregate(900.0, 40)
    assert low_estimate.state == stability_badge.STATE_SUPPRESSED

    low_n = stability_badge.resolve_aggregate(5000.0, 12)
    assert low_n.state == stability_badge.STATE_SUPPRESSED


def test_real_other_categories_relabeled():
    inp = _sankey_input(
        categories={
            1: [
                _cat("other", 90_000.0, STATE_STABLE, label="Other"),
                _cat("mechY", 100.0, STATE_SUPPRESSED),
            ],
            3: [
                _cat("other", 85_000.0, STATE_STABLE, label="Other"),
                _cat("diagY", 100.0, STATE_SUPPRESSED),
            ],
        }
    )

    graph = sankey.collapse_pathways(inp)

    stage1 = {n["id"]: n["label"] for n in graph.nodes if n["stage"] == 1}
    stage3 = {n["id"]: n["label"] for n in graph.nodes if n["stage"] == 3}
    assert stage1["1:other"] == "Other (classified)"
    assert stage1["1:__other__"] == "Other"
    assert stage3["3:other"] == "Other diagnosis"
    assert stage3["3:__other__"] == "Other"
    assert len(set(stage1.values())) == len(stage1)
    assert len(set(stage3.values())) == len(stage3)


def test_sankey_conservation_on_committed_data():
    flt = filters_module.default_case_filter()
    inp = data_module.sankey_data(flt, "annual_average", None)

    totals: list[float] = []
    for col in range(4):
        marginals = tuple(inp.marginals[s] if s in (col, col + 1) else () for s in range(5))
        stage_pairs = tuple(inp.stage_pairs[c] if c == col else () for c in range(4))
        col_inp = sankey.SankeyInput(
            stage_pairs=stage_pairs,
            marginals=marginals,
            severe_disposition_ids=inp.severe_disposition_ids,
            excluded_display=inp.excluded_display,
        )
        graph = sankey.collapse_pathways(col_inp)
        drawn = sum(link["value"] for link in graph.links)
        total = sum(link.point for link in inp.stage_pairs[col])
        assert drawn + graph.hidden_volume == pytest.approx(total, rel=1e-9)
        totals.append(total)

    assert totals[0] == pytest.approx(totals[1], rel=1e-9)
    assert totals[1] == pytest.approx(totals[2], rel=1e-9)
    assert totals[2] == pytest.approx(totals[3], rel=1e-9)


def test_sankey_data_cold_under_2s():
    data_module.cached_estimate_by_pair.clear()
    data_module.cached_estimate_by.clear()
    data_module.sankey_data.clear()
    flt = filters_module.default_case_filter()
    start = time.perf_counter()
    data_module.sankey_data(flt, "annual_average", None)
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0


def test_sankey_data_warm_under_1s():
    flt = filters_module.default_case_filter()
    data_module.sankey_data(flt, "annual_average", None)
    start = time.perf_counter()
    data_module.sankey_data(flt, "annual_average", None)
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0


def test_sankey_copy_passes_guardrails():
    for module in (sankey, mechanisms_page):
        for name in dir(module):
            if name.startswith("_"):
                continue
            value = getattr(module, name)
            if isinstance(value, str):
                assert scan_guardrail_vocabulary(value) == [], f"{module.__name__}.{name} failed"


# ---------------------------------------------------------------------------
# On-chart node naming: a SHORT NAME on every viewport, an ellipsis only when compact
# ---------------------------------------------------------------------------


def test_node_short_label_uses_a_short_name_not_an_ellipsis():
    """The shipped chart read "Direct Cutting…", "Bench or Table…" and "Treated & relea…" at
    1440px, where the columns had room. A hand-written short name says the same thing in fewer
    characters without spending one of them on a "…"."""
    assert sankey.node_short_label("Direct Cutting Contact") == "Direct contact"
    assert sankey.node_short_label("Bench or Table Saw") == "Table saw"
    assert sankey.node_short_label("Portable Circular Power Saw") == "Circular saw"
    disposition = (
        "Left without being seen, Left against medical advice, Left without treatment, Eloped"
    )
    assert sankey.node_short_label(disposition) == "Left untreated"


def test_node_short_label_strips_not_specified_tails():
    assert sankey.node_short_label("Saw, Not Specified") == "Saw"


def test_node_short_label_only_truncates_in_compact_mode():
    long_label = "An Unusually Long Category Name That Has No Short Name"
    wide = sankey.node_short_label(long_label)
    compact = sankey.node_short_label(long_label, compact=True)
    assert len(wide) <= sankey._MAX_NODE_LABEL_CHARS + 1
    assert len(compact) <= sankey._COMPACT_NODE_LABEL_CHARS + 1
    assert len(compact) < len(wide)
    assert compact.endswith("…")


def test_node_short_label_keeps_short_labels_unchanged():
    assert sankey.node_short_label("Hand") == "Hand"


def test_node_short_label_strips_parenthetical_before_truncating():
    assert sankey.node_short_label("Other (classified)") == "Other"


def test_node_short_label_full_text_still_reaches_hover_via_customdata():
    """The on-chart label is shortened (above); `build_sankey` must still carry the full,
    untruncated label through to hover via node `customdata` -- MC-1's fix must never lose
    information, only the on-chart text rendering."""
    long_label = "Direct Cutting Contact"
    inp = _sankey_input(
        categories={1: [_cat("direct_cutting_contact", 50_000.0, STATE_STABLE, label=long_label)]},
    )
    graph = sankey.collapse_pathways(inp)
    fig = sankey.build_sankey(graph)
    assert long_label in list(fig.data[0].node.customdata)
