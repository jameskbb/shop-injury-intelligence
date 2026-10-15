"""Data payload for the Body Map page's 3D injury figure (a Three.js component consumes it).

Every number here comes from EXACTLY the path the 2D silhouette uses -- `body_map_frame` under the
page's own measure filter, coloured on the same shared `BodyScale` -- so the 2D and 3D figures can
never disagree. This module makes no suppression decisions and sums no weights: region states come
from `stability_badge` (via `body_map_frame` / `region_share_by_tool_frame`), and absent-vs-
suppressed is decided by the 2D renderer's own `region_is_absent`.

Schema (JSON-serialisable, plain Python scalars only)::

    {
      "regions": {region_id: {"label", "value", "display", "state", "color", "emphasis",
                              "tooltip", "raw_n"}},     # all kind='region' regions
      "hotspots": [{"tool_id", "tool_label", "regions": [region_id, ...]}],
      "active_tool": str | None,
      "selected": str | None,
      "measure_label": str,
      "basis_label": str,        # units + basis for the tooltip footer, formatted in Python
      "reduced_motion": bool,
    }

There is ONE finger region: NEISS records neither which finger nor which side, so the payload never
invents per-finger (or left/right) entries.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from typing import Any

from wii.app import theme
from wii.app.components import stability_badge
from wii.app.components.body_map import (
    BodyScale,
    RegionValue,
    _region_title_text,
    region_is_absent,
)
from wii.app.data import (
    body_map_frame,
    load_dims,
    region_share_by_tool_frame,
    region_top_tools,
)
from wii.metrics import CaseFilter

# Same keys/labels as the Body Map page's measure selector (parity is asserted in the tests).
MEASURES: dict[str, str] = {
    "estimated": "Estimated injuries",
    "share": "Share of selected tool injuries",
    "amputation": "Amputations",
    "laceration": "Lacerations",
    "fracture": "Fractures",
    "escalated": "Escalated-care injuries",
}

STATE_ABSENT = "absent"
HOTSPOT_TOOL_LIMIT = 12
HOTSPOT_REGION_LIMIT = 5

# INK DENSITY = MAGNITUDE, and the figure never uses the ramp's floor. `SEQUENTIAL_SCALE[0]` is
# nearly the sheet colour, which on a 3D figure turned the two hands into pale gloves floating in
# front of an unlit body; the plate therefore maps stable values across stops 1..4, so a region's
# "zero" is a visible construction grey rather than bare vellum.
FIGURE_COLORS: tuple[str, ...] = tuple(theme.SEQUENTIAL_SCALE[1:])

# LINE TREATMENT = CONFIDENCE, never colour. A limited estimate keeps its value's fill one step
# lighter; a suppressed or absent region takes NO fill at all and is told apart by its dashed
# contour alone (drawn by the component). Amber is retired: no caution colour reaches the figure.
NO_FILL_COLOR = theme.VELLUM
LIMITED_LIGHTEN = 0.55  # share of the ramp's lightest stop mixed into a limited region's fill


def _blend(a: str, b: str, t: float) -> str:
    """`a` mixed `t` of the way toward `b`; both "#rrggbb", result "#rrggbb"."""
    ca = tuple(int(a.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
    cb = tuple(int(b.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(ca, cb, strict=True))


def measure_filter(flt: CaseFilter, measure: str) -> CaseFilter:
    """Mirror of the Body Map page's `_measure_filter`: a diagnosis-subset measure's own category
    REPLACES the page diagnosis selector for the figure."""
    if measure in ("amputation", "laceration", "fracture"):
        return replace(flt, diagnosis_categories=(measure,), diagnosis_basis="any")
    return flt


def measure_population_filter(flt: CaseFilter, measure: str) -> CaseFilter:
    """The case population the figure is coloured by under `measure` -- `measure_filter` plus the
    escalated-care disposition restriction `body_map_frame` applies internally for "escalated"
    (codes from `dim_disposition.escalated_care`, never literals). "estimated"/"share" keep `flt`.
    The hotspot drill-down is computed on this, so it describes the same population as the fill."""
    eff = measure_filter(flt, measure)
    if measure == "escalated":
        dispositions = load_dims()["dispositions"]
        codes = tuple(
            int(row["code"]) for row in dispositions.iter_rows(named=True) if row["escalated_care"]
        )
        eff = replace(eff, dispositions=codes)
    return eff


def body_scale(values: Mapping[str, RegionValue], region_ids: Iterable[str]) -> BodyScale:
    """Mirror of the Body Map page's `_body_scale`: one shared scale, vmax = max non-None value
    over kind='region' regions (1.0 when there is none)."""
    ids = set(region_ids)
    candidates = [rv.value for rid, rv in values.items() if rid in ids and rv.value is not None]
    vmax = max(candidates) if candidates else 0.0
    return BodyScale(vmin=0.0, vmax=vmax if vmax > 0 else 1.0)


def region_state(rv: RegionValue) -> str:
    """stable | limited | suppressed | absent -- absent exactly when the 2D renderer draws the
    dashed "nothing here" outline; a suppressed region with sample cases stays suppressed."""
    if region_is_absent(rv):
        return STATE_ABSENT
    if rv.state == stability_badge.STATE_STABLE and rv.value is not None:
        return stability_badge.STATE_STABLE
    if rv.state == stability_badge.STATE_LIMITED and rv.value is not None:
        return stability_badge.STATE_LIMITED
    return stability_badge.STATE_SUPPRESSED


def emphasis_for(state: str, value: float | None, max_stable: float) -> float:
    """sqrt-companded position of a STABLE value against the max stable value (finger is roughly
    half of all injuries and would otherwise flatten every other region); 0 for anything else."""
    if state != stability_badge.STATE_STABLE or value is None or value <= 0 or max_stable <= 0:
        return 0.0
    return float(math.sqrt(min(1.0, value / max_stable)))


def figure_scale(values: Mapping[str, RegionValue], region_ids: Iterable[str]) -> BodyScale:
    """`body_scale`'s domain (so the figure and the flat map agree on every VALUE) carried on the
    figure's own ramp, which starts one stop above the sheet -- see `FIGURE_COLORS`."""
    return replace(body_scale(values, region_ids), colors=FIGURE_COLORS)


def region_color(state: str, value: float | None, scale: BodyScale) -> str:
    """The fill only. Confidence is carried by the contour, which the component draws."""
    if state == stability_badge.STATE_STABLE and value is not None:
        return scale.color_for(value)
    if state == stability_badge.STATE_LIMITED and value is not None:
        return _blend(scale.color_for(value), theme.SEQUENTIAL_SCALE[0], LIMITED_LIGHTEN)
    return NO_FILL_COLOR


def _missing_region(label: str) -> RegionValue:
    # Same fallback `body_map_frame` uses for a region with no estimate row at all.
    return RegionValue(
        label=label,
        value=None,
        display_value="—",
        state=stability_badge.STATE_SUPPRESSED,
        raw_n=0,
    )


def build_regions(
    region_ids: Sequence[str],
    labels: Mapping[str, str],
    values: Mapping[str, RegionValue],
    scale: BodyScale,
) -> dict[str, dict[str, Any]]:
    """Pure: one entry per atomic region, in `region_ids` order."""
    resolved = {rid: values.get(rid) or _missing_region(labels.get(rid, rid)) for rid in region_ids}
    states = {rid: region_state(rv) for rid, rv in resolved.items()}
    stable_values = [
        float(rv.value)
        for rid, rv in resolved.items()
        if states[rid] == stability_badge.STATE_STABLE and rv.value is not None
    ]
    max_stable = max(stable_values) if stable_values else 0.0

    regions: dict[str, dict[str, Any]] = {}
    for rid in region_ids:
        rv = resolved[rid]
        state = states[rid]
        value = None if rv.value is None else float(rv.value)
        regions[rid] = {
            "label": str(rv.label),
            "value": value,
            "display": str(rv.display_value),
            "state": state,
            "color": region_color(state, value, scale),
            "emphasis": emphasis_for(state, value, max_stable),
            "tooltip": _region_title_text(rv),
            "raw_n": int(rv.raw_n or 0),
        }
    return regions


def build_hotspots(
    tool_ids: Sequence[str],
    tool_labels: Mapping[str, str],
    share_rows: Iterable[Mapping[str, Any]],
    region_ids: Iterable[str],
    region_limit: int = HOTSPOT_REGION_LIMIT,
    figure_stable: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Pure: for each tool (in the given burden order), its top regions by within-tool share --
    STABLE, positive shares on atomic regions only, value-desc (ties by region_id). When
    `figure_stable` is given, the top-`region_limit` list is then cut to regions the FIGURE draws
    as stable (a hotspot never names a region drawn hatched); the list is never back-filled with
    lower-ranked regions. A tool with no qualifying region is dropped; nothing is padded."""
    valid = set(region_ids)
    drawn_stable = None if figure_stable is None else set(figure_stable)
    by_tool: dict[str, list[tuple[float, str]]] = {}
    for row in share_rows:
        value = row.get("value")
        if (
            row.get("state") != stability_badge.STATE_STABLE
            or value is None
            or value <= 0
            or row.get("region_id") not in valid
        ):
            continue
        by_tool.setdefault(str(row["tool_id"]), []).append((float(value), str(row["region_id"])))

    hotspots: list[dict[str, Any]] = []
    for tool_id in tool_ids:
        ranked = sorted(by_tool.get(tool_id, []), key=lambda p: (-p[0], p[1]))
        top = [rid for _, rid in ranked[:region_limit]]
        if drawn_stable is not None:
            top = [rid for rid in top if rid in drawn_stable]
        if not top:
            continue
        hotspots.append(
            {
                "tool_id": str(tool_id),
                "tool_label": str(tool_labels.get(tool_id, tool_id)),
                "regions": top,
            }
        )
    return hotspots


def _stable_burden_tools(flt: CaseFilter, basis: str, limit: int) -> list[str]:
    """Top tools by estimated burden (stable-first ranking from `region_top_tools`), keeping only
    tools whose own burden estimate is stable."""
    df = region_top_tools(flt, basis, limit)
    return [
        str(row["group_id"])
        for row in df.iter_rows(named=True)
        if stability_badge.resolve(row, basis).state == stability_badge.STATE_STABLE
    ]


def build_body3d_payload(
    flt: CaseFilter,
    basis: str,
    measure: str,
    *,
    selected: str | None = None,
    active_tool: str | None = None,
    reduced_motion: bool = False,
    basis_label: str = "",
) -> dict[str, Any]:
    """The 3D figure's payload for the Body Map page's filter `flt` (the page's filter BEFORE the
    measure override -- this applies `measure_filter` itself, exactly like the page does)."""
    if measure not in MEASURES:
        raise ValueError(f"unsupported measure={measure!r}")

    dims = load_dims()
    region_dims = dims["body_regions"]
    region_ids = [str(r) for r in region_dims["region_id"].to_list()]
    labels = dict(zip(region_dims["region_id"], region_dims["label"], strict=True))
    tool_labels = dict(zip(dims["tools"]["tool_id"], dims["tools"]["label"], strict=True))

    values = body_map_frame(measure_filter(flt, measure), basis, measure)
    scale = figure_scale(values, region_ids)
    regions = build_regions(region_ids, labels, values, scale)

    # Hotspots describe the SAME population the figure is coloured by (the measure's own filter),
    # and rank tools against each other, so the page's single-tool selection is cleared.
    flt_all_tools = replace(measure_population_filter(flt, measure), tools=())
    tool_order = _stable_burden_tools(flt_all_tools, basis, HOTSPOT_TOOL_LIMIT)
    active = active_tool if active_tool in tool_labels else None
    if active is not None and active not in tool_order:
        tool_order.append(active)
    share_df = region_share_by_tool_frame(flt_all_tools)
    share_rows = share_df.iter_rows(named=True) if share_df.height else []
    stable_ids = [rid for rid, r in regions.items() if r["state"] == stability_badge.STATE_STABLE]
    hotspots = build_hotspots(
        tool_order, tool_labels, share_rows, region_ids, figure_stable=stable_ids
    )

    return {
        "regions": regions,
        "hotspots": hotspots,
        "active_tool": None if active is None else str(active),
        "selected": selected if selected in regions else None,
        "measure_label": MEASURES[measure],
        "basis_label": str(basis_label),
        "reduced_motion": bool(reduced_motion),
    }
