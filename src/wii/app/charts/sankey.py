"""D-09/D-10 INJURY PATHWAYS Sankey (Tool -> Primary mechanism -> Primary body region ->
Diagnosis category -> Disposition). Pure figure-builder module -- no Streamlit/wii.metrics calls
happen here; every input is an already-resolved `SankeyInput` built by `wii.app.data.sankey_data`.

D-10 readability rules, applied by `collapse_pathways`: keep the top TOP_N STABLE categories per
stage, collapse the rest into `BUCKET_ID`; an individual kept-to-kept link that isn't itself
stable is re-pointed into the destination stage's bucket (its point added to `merged_volume`);
every remaining aggregate/bucket link is drawn only when `stability_badge.resolve_aggregate`
clears the CPSC hard floors (raw N >= 20 AND estimate >= 1,200) -- a sub-floor aggregate is not
drawn, its point added to `hidden_volume` instead. `stability_badge.py` stays the single
suppression call site; this module never re-derives the CPSC rule itself.
"""

from __future__ import annotations

from dataclasses import dataclass

import plotly.graph_objects as go

from wii.app.components.stability_badge import STATE_AGGREGATE, STATE_STABLE, resolve_aggregate
from wii.app.theme import (
    DIAGNOSIS_COLORS,
    FONT_SANS,
    GRAPHITE,
    GRAPHITE_SOFT,
    MECHANISM_COLORS,
    RULE,
    SEVERE,
    diagnosis_palette_key,
    style_chart,
)

STAGES: tuple[str, ...] = (
    "tool_primary",
    "mechanism",
    "body_region_primary",
    "diagnosis_category_primary",
    "disposition",
)
TOP_N = 6
NODE_CAP = 40
BUCKET_ID = "__other__"

#: Links are a hairline wash of the page's own ink, never a semantic colour -- a link's job is to
#: carry width (volume), and any hue on it would compete with the node palettes.
_LINK_COLOR = RULE
_NODE_FONT_SIZE = 13

# A five-stage diagram is a lot of text in narrow columns, so each node gets a SHORT DISPLAY NAME
# rather than an ellipsis. "Bench or Table…", "Direct Cutting…", "Treated & relea…" and "Left
# without tr…" were the shipped labels at 1440px, where the columns had room -- a 390px fix
# applied at every width. The full label always still reaches hover via `customdata`.
_SHORT_NAMES: dict[str, str] = {
    # Tools
    "Bench or Table Saw": "Table saw",
    "Portable Circular Power Saw": "Circular saw",
    "Reciprocating Saw (Sabre Saw)": "Recip. saw",
    "Nail Gun / Stud Driver": "Nail gun",
    "Manual Sanding Tool": "Hand sanding",
    "Chisel / Plane": "Chisel/plane",
    "Clamp / Vise": "Clamp/vise",
    # Mechanisms
    "Direct Cutting Contact": "Direct contact",
    "Kickback Ejection": "Kickback",
    "Flying Debris Fb": "Flying debris",
    "Loss Of Tool Control": "Lost control",
    "Workpiece Slip": "Workpiece slip",
    "Pinch Crush": "Pinch/crush",
    "Maintenance Setup": "Maintenance",
    "Fastener Discharge": "Fastener",
    "Fall Trip": "Fall/trip",
    "Electrical Burn": "Electrical",
    # Dispositions (the NEISS labels run to ~160 characters)
    "Treated and released, or examined and released without treatment (also includes transfers "
    "for treatment to another department of the same facility without admission)": "Released",
    "Treated and transferred to another hospital": "Transferred",
    "Treated and admitted for hospitalization (within same facility)": "Admitted",
    "Held for observation (includes admitted for observation)": "Observation",
    "Left without being seen, Left against medical advice, Left without treatment, "
    "Eloped": "Left untreated",
    'Fatality, includes brain death, dead on arrival ("DOA"), died in the ED, and died after '
    "admission": "Fatality",
}

#: Generic tails that carry no information in a five-word column.
_STRIPPED_TAILS = (", Not Specified", ", not specified")

#: Desktop keeps a readable name; `compact=True` (under 640px, where five columns leave ~70px
#: each) is the only place an ellipsis is still used.
_MAX_NODE_LABEL_CHARS = 22
_COMPACT_NODE_LABEL_CHARS = 14

# B3: a real category id whose OWN (non-bucket) label must be relabelled so it never collides
# with its stage's "Other" bucket label -- keyed by (stage index, category id). The real category
# keeps its own node id ("other"), distinct from BUCKET_ID, and is ranked/kept like any other
# category.
_RELABEL: dict[tuple[int, str], str] = {
    (1, "other"): "Other (classified)",
    (3, "other"): "Other diagnosis",
}


@dataclass(frozen=True)
class PairLink:
    """One already-estimated adjacent-stage-pair row (`wii.metrics.estimate_by_pair`, basis-
    resolved by `wii.app.data.sankey_data`). `point` is the row's basis-matched point value
    (never gated to None by suppression -- that's a display concern, not an aggregation input);
    `state` is `stability_badge`'s STABLE/LIMITED/SUPPRESSED classification for THIS pair alone.
    """

    src_id: str
    src_label: str
    dst_id: str
    dst_label: str
    point: float
    raw_n: int
    state: str
    ci_low: float | None = None
    ci_high: float | None = None


@dataclass(frozen=True)
class StageCategory:
    """One stage's marginal category (`wii.metrics.estimate_by`, basis-resolved)."""

    id: str
    label: str
    point: float
    state: str


@dataclass(frozen=True)
class SankeyInput:
    """`stage_pairs` has 4 tuples (one per adjacent-stage column, in `STAGES` order);
    `marginals` has 5 tuples (one per stage, in `STAGES` order) -- used only for per-stage top-N
    ranking, never for link values."""

    stage_pairs: tuple[tuple[PairLink, ...], ...]
    marginals: tuple[tuple[StageCategory, ...], ...]
    severe_disposition_ids: frozenset[str]
    excluded_display: str
    focus_tool_label: str | None = None


@dataclass(frozen=True)
class SankeyGraph:
    nodes: tuple[dict, ...]
    links: tuple[dict, ...]
    merged_volume: float
    hidden_volume: float
    column_totals: tuple[float, ...]


def node_short_label(label: str, *, compact: bool = False) -> str:
    """The on-chart name for a node: a hand-written short name where one exists, otherwise the
    text before the first " (" with any ", Not Specified" tail dropped. Only `compact=True` (the
    narrow-viewport path) truncates with an ellipsis. The full label always still reaches hover
    via `customdata`."""
    named = _SHORT_NAMES.get(label)
    if named is not None:
        short = named
    else:
        idx = label.find(" (")
        short = label[:idx] if idx != -1 else label
        for tail in _STRIPPED_TAILS:
            if short.endswith(tail):
                short = short[: -len(tail)]
    limit = _COMPACT_NODE_LABEL_CHARS if compact else _MAX_NODE_LABEL_CHARS
    if len(short) <= limit:
        return short
    return short[: limit - 1].rstrip() + "…"


def _bucket_label(stage_idx: int) -> str:
    return "Other tools" if stage_idx == 0 else "Other"


def _stage_label(stage_idx: int, cat_id: str, cat_label: str) -> str:
    return _RELABEL.get((stage_idx, cat_id), cat_label)


def _kept_ids(categories: tuple[StageCategory, ...], top_n: int) -> set[str]:
    """Top `top_n` STABLE categories by point. A stage with 0 or 1 category has nothing to rank
    against (this is the single-tool Sankey focus mode's Tool stage, D-10) -- its sole category is
    always kept rather than being bucketed away."""
    if len(categories) <= 1:
        return {c.id for c in categories}
    stable = [c for c in categories if c.state == STATE_STABLE]
    stable.sort(key=lambda c: c.point, reverse=True)
    return {c.id for c in stable[:top_n]}


def _node_color(stage_idx: int, cat_id: str, severe_ids: frozenset[str]) -> str:
    """D-10/UI-SPEC Color: mechanism nodes MECHANISM_COLORS, diagnosis nodes DIAGNOSIS_COLORS via
    diagnosis_palette_key, disposition nodes in `severe_ids` get SEVERE -- every other node
    (including every bucket node, and every Tool/body-region node) stays neutral. No node ever
    gets theme.PRIMARY (that's reserved for the focus selector's own UI state, D-10)."""
    if cat_id == BUCKET_ID:
        return GRAPHITE_SOFT
    if stage_idx == 1:
        return MECHANISM_COLORS.get(cat_id, GRAPHITE_SOFT)
    if stage_idx == 3:
        return DIAGNOSIS_COLORS.get(diagnosis_palette_key(cat_id), GRAPHITE_SOFT)
    if stage_idx == 4:
        return SEVERE if cat_id in severe_ids else GRAPHITE_SOFT
    return GRAPHITE_SOFT


def collapse_pathways(inp: SankeyInput, top_n: int = TOP_N) -> SankeyGraph:
    """D-10 full readability/stability rules. Per column: map each `PairLink`'s endpoints to
    kept-or-bucket; a link between two still-kept nodes ("individual") whose OWN state isn't
    stable is re-pointed to (src, bucket-of-dst) and its point counted in `merged_volume`; the
    remaining truly-individual-stable links draw with their own CI; every other (multi-member or
    bucket-touching) group is gated by `stability_badge.resolve_aggregate` on the SUM of its
    member points/raw_n -- drawn as STATE_AGGREGATE when it clears the CPSC hard floors, else its
    point is added to `hidden_volume` and it is not drawn. `collapse_pathways` issues no queries
    of its own -- it only classifies/sums already-estimated `PairLink` points (B2)."""
    n_stages = len(STAGES)
    kept = [_kept_ids(inp.marginals[s], top_n) for s in range(n_stages)]

    def mapped(stage_idx: int, cat_id: str) -> str:
        return cat_id if cat_id in kept[stage_idx] else BUCKET_ID

    node_label: dict[tuple[int, str], str] = {}
    node_point: dict[tuple[int, str], float] = {}

    def _touch(stage_idx: int, cat_id: str, label: str, point: float = 0.0) -> None:
        key = (stage_idx, cat_id)
        if key not in node_label:
            node_label[key] = (
                _bucket_label(stage_idx)
                if cat_id == BUCKET_ID
                else _stage_label(stage_idx, cat_id, label)
            )
            node_point[key] = 0.0
        if cat_id != BUCKET_ID:
            node_point[key] = point

    for s in range(n_stages):
        for cat in inp.marginals[s]:
            if cat.id in kept[s]:
                _touch(s, cat.id, cat.label, cat.point)

    column_totals: list[float] = []
    raw_links: list[dict] = []
    merged_volume = 0.0
    hidden_volume = 0.0
    for col in range(n_stages - 1):
        pairs = inp.stage_pairs[col]
        column_totals.append(sum(link.point for link in pairs))
        groups: dict[tuple[str, str], list[PairLink]] = {}
        individual_keys: set[tuple[str, str]] = set()
        for link in pairs:
            m_src = mapped(col, link.src_id)
            m_dst = mapped(col + 1, link.dst_id)
            is_candidate = m_src == link.src_id and m_dst == link.dst_id
            if is_candidate and link.state != STATE_STABLE:
                merged_volume += link.point
                m_dst = BUCKET_ID
                is_candidate = False
            if m_src == BUCKET_ID:
                _touch(col, m_src, "")
            if m_dst == BUCKET_ID:
                _touch(col + 1, m_dst, "")
            key = (m_src, m_dst)
            groups.setdefault(key, []).append(link)
            if is_candidate:
                individual_keys.add(key)
            else:
                individual_keys.discard(key)
        for key, members in groups.items():
            m_src, m_dst = key
            if len(members) == 1 and key in individual_keys:
                link = members[0]
                raw_links.append(
                    {
                        "src": (col, m_src),
                        "dst": (col + 1, m_dst),
                        "value": link.point,
                        "state": STATE_STABLE,
                        "raw_n": link.raw_n,
                        "ci_low": link.ci_low,
                        "ci_high": link.ci_high,
                    }
                )
                continue
            sum_point = sum(m.point for m in members)
            sum_raw_n = sum(m.raw_n for m in members)
            resolved = resolve_aggregate(sum_point, sum_raw_n)
            if resolved.value is None:
                hidden_volume += sum_point
                continue
            raw_links.append(
                {
                    "src": (col, m_src),
                    "dst": (col + 1, m_dst),
                    "value": sum_point,
                    "state": STATE_AGGREGATE,
                    "raw_n": sum_raw_n,
                    "ci_low": None,
                    "ci_high": None,
                }
            )

    node_order: list[tuple[int, str]] = []
    for s in range(n_stages):
        keys = [k for k in node_label if k[0] == s and k[1] != BUCKET_ID]
        keys.sort(key=lambda k: node_point.get(k, 0.0), reverse=True)
        node_order.extend(keys)
        bucket_key = (s, BUCKET_ID)
        if bucket_key in node_label:
            node_order.append(bucket_key)

    if len(node_order) > NODE_CAP:
        raise ValueError(f"sankey node count {len(node_order)} exceeds cap {NODE_CAP}")

    node_index = {key: i for i, key in enumerate(node_order)}
    nodes = tuple(
        {
            "id": f"{s}:{cid}",
            "stage": s,
            "label": node_label[(s, cid)],
            "color": _node_color(s, cid, inp.severe_disposition_ids),
        }
        for s, cid in node_order
    )
    links = tuple(
        {
            "source": node_index[link["src"]],
            "target": node_index[link["dst"]],
            "value": link["value"],
            "state": link["state"],
            "raw_n": link["raw_n"],
            "ci_low": link["ci_low"],
            "ci_high": link["ci_high"],
        }
        for link in raw_links
    )

    return SankeyGraph(
        nodes=nodes,
        links=links,
        merged_volume=merged_volume,
        hidden_volume=hidden_volume,
        column_totals=tuple(column_totals),
    )


_AGGREGATE_HOVER_LABEL = "Combined flow: not individually reportable"


def _link_customdata(link: dict) -> list[str]:
    if link["ci_low"] is not None and link["ci_high"] is not None:
        ci_text = f"{link['ci_low']:,.0f}–{link['ci_high']:,.0f}"
    else:
        ci_text = "—"
    value_text = f"{link['value']:,.0f} estimated injuries"
    if link["state"] != STATE_STABLE:
        value_text = f"{value_text} ({_AGGREGATE_HOVER_LABEL})"
    return [value_text, ci_text, f"{link['raw_n']:,}"]


_LINK_HOVERTEMPLATE = (
    "%{customdata[0]}<br>95% CI: %{customdata[1]}<br>"
    "%{customdata[2]} sample cases (not a national count)<extra></extra>"
)


def build_sankey(graph: SankeyGraph, *, compact: bool = False) -> go.Figure:
    """Node order/colors come pre-resolved from `graph.nodes`; node `customdata` carries the full
    (un-shortened) label for hover, the on-chart text uses `node_short_label`. Link color is a
    fixed hairline neutral (never a semantic color, so it never competes with node color).
    `compact=True` is the narrow-viewport path and is the only one that truncates a node name."""
    node_labels = [node_short_label(n["label"], compact=compact) for n in graph.nodes]
    node_customdata = [n["label"] for n in graph.nodes]
    node_colors = [n["color"] for n in graph.nodes]

    fig = go.Figure(
        go.Sankey(
            node={
                "label": node_labels,
                "color": node_colors,
                "customdata": node_customdata,
                "hovertemplate": "%{customdata}<extra></extra>",
                "pad": 12,
                "thickness": 14,
                "line": {"color": GRAPHITE, "width": 1},
            },
            link={
                "source": [link["source"] for link in graph.links],
                "target": [link["target"] for link in graph.links],
                "value": [link["value"] for link in graph.links],
                "color": _LINK_COLOR,
                "customdata": [_link_customdata(link) for link in graph.links],
                "hovertemplate": _LINK_HOVERTEMPLATE,
            },
        )
    )
    style_chart(
        fig,
        height=max(420, 26 * max(len(graph.nodes) // 4, 6) + 180),
        margin={"l": 8, "r": 8, "t": 8, "b": 8},
        grid="none",
    )
    fig.update_layout(font={"family": FONT_SANS, "size": _NODE_FONT_SIZE, "color": GRAPHITE})
    return fig
