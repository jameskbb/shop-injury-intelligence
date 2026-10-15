"""Shared SVG body-silhouette renderer -- used by Tool Profiles' anatomy snapshot, Comparison's
small-multiple bodies (sharing ONE `BodyScale` instance across every rendered tool, per the
"never autoscale independently" rule), My Shop, and the Body Map page's click-through view. Two
original, non-graphic anatomical silhouettes (`assets/body/body_front.svg` / `body_back.svg`): one
element per `dim_body_region.svg_element_ids` value, a bilateral region being ONE `<path>` whose
`d` holds a left and a right subpath. Parsed and filled via `xml.etree.ElementTree` -- never
regex/string interpolation into markup. Rendered via `st.markdown(unsafe_allow_html=True)`, never
`st.html` (which silently strips `<svg>` content in Streamlit 1.63) and never a legacy
iframe-isolated custom-component helper.

THE TWO ENCODINGS (`docs/design-system.md`). This figure is where they matter most:

  * INK DENSITY = MAGNITUDE. A region's fill is its value on the shared `SEQUENTIAL_SCALE`,
    bare vellum through to saturated blueprint.
  * LINE TREATMENT = CONFIDENCE. Solid hairline = a finished, stable estimate. Dashed
    construction line = provisional. Suppressed additionally drops the fill to bare sheet.

Confidence used to be a saturated amber diagonal hatch. On a tool whose cases concentrate in the
hand -- which is most of them -- 16 of 22 regions are limited or suppressed, so the figure rendered
as a body bandaged head to toe in the app's loudest colour: the most saturated thing on four of
nine pages meant "we do not trust this", and the two hands, the only real data, were the quietest
marks on it. The channels are now orthogonal: a region can read "large but provisional" without
either statement borrowing the other's channel, and unstable regions recede instead of shouting.
Quiet is not hidden -- every state still carries its `<title>`/`help` text with the stability
label, the legend names all three treatments, and `stability_floor_note` prints how many regions
fell below the floor.
"""

from __future__ import annotations

import html
import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass, field

import streamlit as st

from wii.app import theme
from wii.app.components.stability_badge import (
    LIMITED_SAMPLE_LABEL,
    STATE_LIMITED,
    STATE_STABLE,
    STATE_SUPPRESSED,
    SUPPRESSED_LABEL,
)
from wii.utils.paths import repo_root

_SVG_NS = "http://www.w3.org/2000/svg"
_SVG_NS_XLINK = "http://www.w3.org/1999/xlink"
ET.register_namespace("", _SVG_NS)
ET.register_namespace("xlink", _SVG_NS_XLINK)

_CALLOUT_IDS: tuple[str, ...] = ("internal", "burn_25_50", "all_body", "not_recorded")

# Stroke width applied to the selected region's accent outline (theme.BLUEPRINT), layered on top
# of its existing fill -- the fill logic itself is unchanged for the selected region.
SELECTED_STROKE_WIDTH = "3"

# THE RULE, and why this file's answer to it differs from the 3D figure's.
#
# A region must be DISTINGUISHABLE FROM BARE SHEET. That is the requirement; a contour is only
# one way of meeting it. The 3D figure is lit and shaded, so its fill meets the rule on its own
# and adding a contour to every region made it read as a paint-by-numbers grid -- so the 3D path
# draws no contour on a stable region. This is a flat SVG with no lighting, and the lower half of
# `SEQUENTIAL_SCALE` sits between 1.07:1 and 2.48:1 against `VELLUM` (all under the 3:1 non-text
# contrast floor). A stable region holding a small value, drawn with no contour, would therefore
# be indistinguishable from a suppressed one: the fill would mean "reportable" and the eye would
# read "nothing here" -- the figure stating the opposite of the truth.
#
# So the hairline here supplies what shading supplies there. Same rule, two implementations,
# because the media differ. Do not "harmonise" this with the 3D path by deleting it.
REGION_STROKE_WIDTH = "0.8"
#: "No recorded cases" is the quietest line on the plate: a determinate fact, so SOLID (a dashed
#: line would claim the region is provisional, which is the opposite of what is known), but drawn
#: at rule strength so it recedes behind every region that carries a value.
ABSENT_STROKE_WIDTH = "0.6"

#: How far a limited region's fill is lightened toward the sheet -- "the value's ink density,
#: one step lighter". The dash on its outline, not this, is what says "provisional"; the lift
#: only keeps a provisional region from reading as heavily as a finished one beside it.
LIMITED_FILL_LIGHTEN = 0.45

_LEGEND_KEYS: tuple[tuple[str, str], ...] = (
    ("stable", "Reportable estimate: darker is more injuries"),
    ("limited", "Limited sample: interpret cautiously"),
    ("suppressed", "Below the reporting floor: no estimate shown"),
)


@dataclass(frozen=True)
class RegionValue:
    """One region's (or callout's) resolved display value -- already basis/stability-resolved by
    the caller via `stability_badge` (T-03-07: this module makes no suppression decisions of its
    own, it only chooses HOW to render an already-resolved state)."""

    label: str
    value: float | None
    display_value: str
    state: str  # stability_badge.STATE_STABLE | STATE_LIMITED | STATE_SUPPRESSED
    raw_n: int
    # D-04 tooltip extensions (Body Map only) -- None means "the caller didn't supply this field"
    # (Phase 3 callers/output stay byte-identical); the Body Map page always passes "—" instead
    # of None when a field has no stable value, per D-04.
    share_display: str | None = None
    # What the share is OF. None keeps the tool-scoped wording; the all-tools view passes
    # "Share of all workshop injuries" so the tooltip never implies a tool filter that isn't set.
    share_label: str | None = None
    top_diagnosis: str | None = None
    top_mechanism: str | None = None


@dataclass(frozen=True)
class BodyScale:
    """Maps a value to a color on a shared sequential colorscale. Callers pass ONE `BodyScale`
    instance across every silhouette they render together (Comparison's small multiples rely on
    this for a single shared color-scale domain, D-12/UI-SPEC "never autoscale independently") --
    this class never autoscales itself from the values it's given.
    """

    vmin: float
    vmax: float
    colors: tuple[str, ...] = field(default_factory=lambda: tuple(theme.SEQUENTIAL_SCALE))

    def color_for(self, value: float) -> str:
        span = self.vmax - self.vmin
        t = 0.0 if span <= 0 else (value - self.vmin) / span
        t = min(1.0, max(0.0, t))
        return _interpolate(self.colors, t)


def _interpolate(colors: tuple[str, ...], t: float) -> str:
    if len(colors) == 1:
        return colors[0]
    scaled = t * (len(colors) - 1)
    idx = min(int(scaled), len(colors) - 2)
    local_t = scaled - idx
    return _blend(colors[idx], colors[idx + 1], local_t)


def _blend(color_a: str, color_b: str, t: float) -> str:
    a = _hex_to_rgb(color_a)
    b = _hex_to_rgb(color_b)
    r, g, bl = (round(a[i] + (b[i] - a[i]) * t) for i in range(3))
    return f"#{r:02x}{g:02x}{bl:02x}"


def _hex_to_rgb(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))


def _asset_path(view: str):
    return repo_root() / "assets" / "body" / f"body_{view}.svg"


def _region_title_text(rv: RegionValue) -> str:
    """D-04: label, value, then (only when supplied) share-of-tool-injuries / most-common
    diagnosis / most-common mechanism, then raw N, then the suppression/limited label. With the
    three new fields left at their default `None` this produces BYTE-IDENTICAL output to Phase 3
    (`"; ".join([label, display_value, raw_n_text])` (+ suppression label if unstable))."""
    parts = [rv.label, rv.display_value]
    if rv.share_display is not None:
        parts.append(f"{rv.share_label or 'Share of tool injuries'}: {rv.share_display}")
    if rv.top_diagnosis is not None:
        parts.append(f"Most common diagnosis: {rv.top_diagnosis}")
    if rv.top_mechanism is not None:
        parts.append(f"Most common mechanism: {rv.top_mechanism}")
    parts.append(f"{rv.raw_n:,} sample cases (not a national count)")
    if rv.state != STATE_STABLE:
        parts.append(SUPPRESSED_LABEL if rv.state == "suppressed" else LIMITED_SAMPLE_LABEL)
    return "; ".join(p for p in parts if p)


def region_is_absent(rv: RegionValue) -> bool:
    """True when there is nothing to estimate: no sample cases at all, or a zero estimate.

    Kept distinct from SUPPRESSED on purpose. A suppressed region has cases behind it whose
    estimate failed the stability rules -- it draws as bare sheet inside a DASHED construction
    outline, the "provisional" treatment. An absent region draws as bare sheet inside a solid
    rule hairline: nothing to report is a settled fact, not a provisional one, so the two states
    are told apart by line treatment without either borrowing a colour."""
    if rv.value is not None:
        return rv.value == 0
    return rv.raw_n == 0 or rv.state not in (STATE_SUPPRESSED, STATE_LIMITED)


def region_paint(
    rv: RegionValue, scale: BodyScale, *, confidence_outlines: bool = True
) -> dict[str, str]:
    """The SVG paint attributes for one already-resolved region -- the single place the two
    encodings are turned into ink, shared by the flat figure and the click-through layers so they
    can never disagree.

    | state              | fill                                   | outline                      |
    |--------------------|----------------------------------------|------------------------------|
    | stable             | value's ink density on the shared scale| solid graphite hairline      |
    | limited            | same, lightened toward the sheet       | dashed construction hairline |
    | suppressed         | bare sheet (no fill)                   | dashed construction hairline |
    | no recorded cases  | bare sheet (no fill)                   | solid rule hairline, quietest|

    `theme.stability_line` owns the stroke colour/dash for the three measured states, so a mark
    here and a mark in a Plotly chart carry the same confidence treatment.

    THE DASH DOES NOT SURVIVE BEING SHRUNK. `confidence_outlines=False` drops the per-region
    dashes and draws every region on one quiet rule hairline, so the plate is a silhouette whose
    only variable is fill.

    Above is written for a figure ~460px tall, where 22 regions have room to be read one at a
    time. In a small multiple at ~140px the dashes stop being a line treatment and become
    texture: on a typical tool 19 of 22 regions fall below the floor, so four such figures
    rendered as ghostly dashed skeletons and the loudest thing in the section was, once again,
    the marking that means "we have no number for this". That is the same inversion the amber
    hatch caused, in a different medium -- the channel is right, its scale-dependence was not.

    Nothing is lost at the small size: the caption still counts the regions below the floor, and
    every region keeps its `<title>`/`help` text naming its own stability.
    """
    absent = region_is_absent(rv)
    state = STATE_SUPPRESSED if rv.value is None and not absent else rv.state
    quiet_line = {"stroke": theme.RULE, "stroke-width": ABSENT_STROKE_WIDTH}
    if absent:
        return {"fill": theme.VELLUM, **quiet_line}
    if state == STATE_STABLE:
        fill = scale.color_for(rv.value)
    elif state == STATE_LIMITED:
        fill = _blend(scale.color_for(rv.value), theme.VELLUM, LIMITED_FILL_LIGHTEN)
    else:
        fill = theme.VELLUM
    if not confidence_outlines:
        return {"fill": fill, **quiet_line}
    line = theme.stability_line(state if state in _LINE_STATES else STATE_SUPPRESSED)
    paint = {
        "fill": fill,
        "stroke": str(line["color"]),
        "stroke-width": REGION_STROKE_WIDTH,
    }
    if state == STATE_LIMITED:
        paint["stroke-dasharray"] = theme.DASH_LIMITED
    elif state != STATE_STABLE:
        paint["stroke-dasharray"] = theme.DASH_SUPPRESSED
    return paint


_LINE_STATES = frozenset({STATE_STABLE, STATE_LIMITED, STATE_SUPPRESSED})
_PAINT_KEYS = ("fill", "stroke", "stroke-width", "stroke-dasharray")


def build_body_svg(
    values: Mapping[str, RegionValue],
    scale: BodyScale,
    instance_id: str,
    view: str,
    selected_region_id: str | None = None,
    link_regions: bool = False,
    confidence_outlines: bool = True,
) -> str:
    """Parses `assets/body/body_{view}.svg` and paints every element whose `id` ends in "-front"/
    "-back" per the region_id (the part before that suffix) found in `values`, using
    `region_paint` -- fill carries magnitude on the SHARED scale (never autoscaled per element),
    outline carries confidence. Inserts exactly one `<title>` child per touched region with the
    resolved display text -- ElementTree text nodes are XML-escaped on serialization, so this
    never string-concatenates untrusted text into markup.

    When `region_id == selected_region_id` that region's outline is replaced by a
    `theme.BLUEPRINT` accent of width `SELECTED_STROKE_WIDTH` -- the fill is untouched, so
    selecting a region never changes what it says. When `link_regions=True`, every touched
    (valued) region element is re-parented under a new `<a href="?region={region_id}">` element
    (ElementTree re-parenting, never string concatenation/regex) so a click navigates to
    `?region={region_id}`; `region_id` always derives from the asset's own (trusted, committed)
    element id, never from unvalidated caller input.

    `confidence_outlines=False` drops the per-region dashes for a small multiple -- see
    `region_paint`.
    """
    tree = ET.parse(_asset_path(view))
    root = tree.getroot()
    root.set("width", "100%")

    # Snapshot child->parent before any re-parenting below -- every region element's TRUE
    # original parent is captured here once, so later wrapping of one region never invalidates
    # the lookup for a not-yet-processed region (re-parenting via ElementTree, not string
    # concatenation).
    parent_map = {child: parent for parent in root.iter() for child in parent}

    for elem in list(root.iter()):
        svg_id = elem.get("id")
        if svg_id is None or not (svg_id.endswith("-front") or svg_id.endswith("-back")):
            continue
        region_id = svg_id.rsplit("-", 1)[0]
        rv = values.get(region_id)
        if rv is None:
            continue
        paint = region_paint(rv, scale, confidence_outlines=confidence_outlines)
        if region_id == selected_region_id:
            paint = {
                **paint,
                "stroke": theme.BLUEPRINT,
                "stroke-width": SELECTED_STROKE_WIDTH,
            }
            paint.pop("stroke-dasharray", None)
        for key in _PAINT_KEYS:
            if key in paint:
                elem.set(key, paint[key])
        title = ET.SubElement(elem, f"{{{_SVG_NS}}}title")
        title.text = _region_title_text(rv)

        if link_regions:
            parent = parent_map[elem]
            idx = list(parent).index(elem)
            parent.remove(elem)
            href = f"?region={region_id}"
            a_elem = ET.Element(f"{{{_SVG_NS}}}a")
            a_elem.set(f"{{{_SVG_NS_XLINK}}}href", href)
            a_elem.set("href", href)
            a_elem.set("target", "_self")
            a_elem.set("style", "cursor:pointer")
            a_elem.append(elem)
            parent.insert(idx, a_elem)

    return ET.tostring(root, encoding="unicode")


Box = tuple[float, float, float, float]
Segment = tuple[str, tuple[float, ...]]

_PATH_COMMANDS = "MmLlHhVvCcSsQqTtAaZz"
_PATH_TOKEN = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_PATH_ARITY = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "Z": 0}


def parse_path_subpaths(d: str) -> list[list[Segment]]:
    """Splits SVG path data into subpaths of ABSOLUTE segments: ("M", (x, y)), ("L", (x, y)),
    ("C", (x1, y1, x2, y2, x, y)), ("Q", (x1, y1, x, y)) and ("Z", ()).

    Relative commands are resolved against the current point, H/V become L, and S/T become C/Q
    with the reflected control point, so every consumer (bounding boxes, the per-subpath 3D
    layers) handles one small vocabulary. Elliptical arcs are rejected rather than approximated:
    the committed artwork never uses them, and a silently-wrong hit box is worse than a loud
    failure (`test_every_region_shape_is_box_computable` would catch it first anyway).
    """
    tokens = _PATH_TOKEN.findall(d)
    subpaths: list[list[Segment]] = []
    current: list[Segment] | None = None
    x = y = start_x = start_y = 0.0
    prev_cmd = ""
    prev_ctrl: tuple[float, float] | None = None
    cmd = ""
    i = 0

    def take(n: int) -> list[float]:
        nonlocal i
        vals = tokens[i : i + n]
        if len(vals) != n or any(v in _PATH_COMMANDS for v in vals):
            raise ValueError(f"truncated path data near token {i}: {d[:60]!r}")
        i += n
        return [float(v) for v in vals]

    while i < len(tokens):
        if tokens[i] in _PATH_COMMANDS:
            cmd = tokens[i]
            i += 1
        elif not cmd:
            raise ValueError(f"path data must start with a command: {d[:60]!r}")
        upper, rel = cmd.upper(), cmd.islower()
        if upper == "A":
            raise ValueError("elliptical-arc path commands are not supported")
        if upper == "Z":
            if current is not None:
                current.append(("Z", ()))
            x, y = start_x, start_y
            prev_cmd, prev_ctrl = "Z", None
            continue
        vals = take(_PATH_ARITY[upper])
        ox, oy = (x, y) if rel else (0.0, 0.0)
        if upper == "M":
            x, y = vals[0] + ox, vals[1] + oy
            start_x, start_y = x, y
            current = [("M", (x, y))]
            subpaths.append(current)
            # Further coordinate pairs after a moveto are implicit linetos (SVG 1.1 §8.3.2).
            cmd = "l" if rel else "L"
            prev_cmd, prev_ctrl = "M", None
            continue
        if current is None or (current and current[-1][0] == "Z"):
            current = [("M", (x, y))]
            subpaths.append(current)
        if upper in ("L", "H", "V"):
            if upper == "H":
                x = vals[0] + ox
            elif upper == "V":
                y = vals[0] + oy
            else:
                x, y = vals[0] + ox, vals[1] + oy
            current.append(("L", (x, y)))
            prev_ctrl = None
        elif upper in ("C", "S"):
            if upper == "C":
                x1, y1 = vals[0] + ox, vals[1] + oy
                rest = vals[2:]
            else:
                x1, y1 = (
                    (2 * x - prev_ctrl[0], 2 * y - prev_ctrl[1])
                    if (prev_ctrl and prev_cmd in "CS")
                    else (x, y)
                )
                rest = vals
            x2, y2 = rest[0] + ox, rest[1] + oy
            x, y = rest[2] + ox, rest[3] + oy
            current.append(("C", (x1, y1, x2, y2, x, y)))
            prev_ctrl = (x2, y2)
        else:  # Q / T
            if upper == "Q":
                x1, y1 = vals[0] + ox, vals[1] + oy
                x, y = vals[2] + ox, vals[3] + oy
            else:
                x1, y1 = (
                    (2 * x - prev_ctrl[0], 2 * y - prev_ctrl[1])
                    if (prev_ctrl and prev_cmd in "QT")
                    else (x, y)
                )
                x, y = vals[0] + ox, vals[1] + oy
            current.append(("Q", (x1, y1, x, y)))
            prev_ctrl = (x1, y1)
        prev_cmd = upper
    return subpaths


def _bezier_extrema(p0: float, p1: float, p2: float, p3: float) -> list[float]:
    """Parameter values in (0, 1) where one axis of a cubic Bezier turns around."""
    a = -p0 + 3 * p1 - 3 * p2 + p3
    b = 2 * (p0 - 2 * p1 + p2)
    c = p1 - p0
    if abs(a) < 1e-12:
        return [-c / b] if abs(b) > 1e-12 and 0 < -c / b < 1 else []
    disc = b * b - 4 * a * c
    if disc < 0:
        return []
    root = math.sqrt(disc)
    return [t for t in ((-b + root) / (2 * a), (-b - root) / (2 * a)) if 0 < t < 1]


def _cubic_at(p0: float, p1: float, p2: float, p3: float, t: float) -> float:
    u = 1 - t
    return u * u * u * p0 + 3 * u * u * t * p1 + 3 * u * t * t * p2 + t * t * t * p3


def subpath_box(segments: list[Segment]) -> Box:
    """Exact axis-aligned bounding box (x, y, w, h) of one absolute subpath. Curve extrema are
    solved analytically, not sampled, so a bulging calf or thumb is never clipped."""
    xs: list[float] = []
    ys: list[float] = []
    cx = cy = 0.0
    for kind, v in segments:
        if kind in ("M", "L"):
            cx, cy = v
            xs.append(cx)
            ys.append(cy)
        elif kind == "C":
            px = (cx, v[0], v[2], v[4])
            py = (cy, v[1], v[3], v[5])
            for pts, out in ((px, xs), (py, ys)):
                out.append(pts[3])
                out.extend(_cubic_at(*pts, t) for t in _bezier_extrema(*pts))
            cx, cy = v[4], v[5]
        elif kind == "Q":
            # Degree-elevate to a cubic so one extremum solver serves both.
            c1 = (cx + 2 / 3 * (v[0] - cx), cy + 2 / 3 * (v[1] - cy))
            c2 = (v[2] + 2 / 3 * (v[0] - v[2]), v[3] + 2 / 3 * (v[1] - v[3]))
            px = (cx, c1[0], c2[0], v[2])
            py = (cy, c1[1], c2[1], v[3])
            for pts, out in ((px, xs), (py, ys)):
                out.append(pts[3])
                out.extend(_cubic_at(*pts, t) for t in _bezier_extrema(*pts))
            cx, cy = v[2], v[3]
    return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


_CLIP_MAX_POINTS = 40


def subpath_outline(segments: list[Segment], per_curve: int = 6) -> list[tuple[float, float]]:
    """The subpath flattened to a polygon (curves sampled `per_curve` times), then thinned to at
    most `_CLIP_MAX_POINTS` vertices -- enough to follow a head or a thumb to well under a pixel
    at the 260px figure width, small enough to keep the emitted CSS compact."""
    pts: list[tuple[float, float]] = []
    cx = cy = 0.0
    for kind, v in segments:
        if kind in ("M", "L"):
            cx, cy = v
            pts.append((cx, cy))
        elif kind in ("C", "Q"):
            if kind == "Q":
                c1 = (cx + 2 / 3 * (v[0] - cx), cy + 2 / 3 * (v[1] - cy))
                c2 = (v[2] + 2 / 3 * (v[0] - v[2]), v[3] + 2 / 3 * (v[1] - v[3]))
                v = (*c1, *c2, v[2], v[3])
            for step in range(1, per_curve + 1):
                t = step / per_curve
                pts.append((_cubic_at(cx, v[0], v[2], v[4], t), _cubic_at(cy, v[1], v[3], v[5], t)))
            cx, cy = v[4], v[5]
    if len(pts) > _CLIP_MAX_POINTS:
        stride = len(pts) / _CLIP_MAX_POINTS
        pts = [pts[int(i * stride)] for i in range(_CLIP_MAX_POINTS)]
    return pts


def subpath_d(segments: list[Segment]) -> str:
    """Re-serialises one absolute subpath as standalone path data."""
    return "".join(kind + " ".join(f"{n:g}" for n in v) for kind, v in segments)


def _element_subpaths(elem: ET.Element) -> list[tuple[Box, str | None]]:
    """(box, standalone path data or None) per drawable piece of one region element. A `<path>`
    yields one entry per subpath; the legacy primitives yield exactly one, with no path data."""
    tag = elem.tag.rsplit("}", 1)[-1]
    if tag == "path":
        return [(subpath_box(sp), subpath_d(sp)) for sp in parse_path_subpaths(elem.get("d", ""))]
    if tag == "rect":
        box = tuple(float(elem.get(k, 0)) for k in ("x", "y", "width", "height"))
        return [(box, None)]
    if tag == "circle":
        cx, cy, r = (float(elem.get(k, 0)) for k in ("cx", "cy", "r"))
        return [((cx - r, cy - r, 2 * r, 2 * r), None)]
    if tag == "ellipse":
        cx, cy, rx, ry = (float(elem.get(k, 0)) for k in ("cx", "cy", "rx", "ry"))
        return [((cx - rx, cy - ry, 2 * rx, 2 * ry), None)]
    raise ValueError(f"unsupported region element <{tag}> (id={elem.get('id')!r})")


def _is_region_id(svg_id: str | None) -> bool:
    return svg_id is not None and (svg_id.endswith("-front") or svg_id.endswith("-back"))


def viewbox_size(view: str) -> tuple[float, float]:
    """The asset's own `viewBox` width/height. Read from the file rather than hard-coded so hit
    zones stay correct if the artwork is ever re-drawn at a different scale."""
    root = ET.parse(_asset_path(view)).getroot()
    parts = (root.get("viewBox") or "0 0 200 460").split()
    return (float(parts[2]), float(parts[3]))


def region_hit_outlines(view: str) -> dict[str, list[list[tuple[float, float]]]]:
    """Per-subpath outline polygons (viewBox units), parallel to `region_hit_boxes`. Used to clip
    each rectangular hit zone to its actual shape -- see `build_hit_zone_css`."""
    root = ET.parse(_asset_path(view)).getroot()
    outlines: dict[str, list[list[tuple[float, float]]]] = {}
    for elem in root.iter():
        svg_id = elem.get("id")
        if not _is_region_id(svg_id):
            continue
        if elem.tag.rsplit("}", 1)[-1] == "path":
            subpaths = parse_path_subpaths(elem.get("d", ""))
            outlines[svg_id.rsplit("-", 1)[0]] = [subpath_outline(sp) for sp in subpaths]
        else:
            outlines[svg_id.rsplit("-", 1)[0]] = [[]]
    return outlines


def region_hit_boxes(view: str) -> dict[str, list[Box]]:
    """Per-SUBPATH axis-aligned bounding boxes, in viewBox units, of every region in
    `assets/body/body_{view}.svg`, keyed by region_id (the element id minus its "-front"/"-back"
    suffix) in document order.

    A bilateral region is one `<path>` with a left and a right subpath, so it yields TWO boxes --
    one hit zone per hand, not a single rectangle spanning the whole torso between them. Boxes are
    computed from the path data itself (`parse_path_subpaths` / `subpath_box`), so there are no
    hand-tuned pixel coordinates to drift out of sync when the artwork changes. Callers turn them
    into percentage offsets against `viewbox_size(view)`, which keeps each zone locked to its
    shape at every rendered width.
    """
    root = ET.parse(_asset_path(view)).getroot()
    boxes: dict[str, list[Box]] = {}
    for elem in root.iter():
        svg_id = elem.get("id")
        if not _is_region_id(svg_id):
            continue
        boxes[svg_id.rsplit("-", 1)[0]] = [box for box, _d in _element_subpaths(elem)]
    return boxes


def callout_row_note(frames) -> str:
    """One line standing in for a ROW of identical callout chip lists, or "" when the chips carry
    something worth drawing per figure.

    The four callout categories (internal, 25-50% of body, all parts of body, not recorded) have
    no `<path>` on the silhouette, so they are printed beside it as chips. On a small-multiple row
    they are nearly always suppressed for every tool at once, which rendered the SAME four-item
    list of em dashes under all four figures -- more repeated ink than the figures themselves.

    `frames` is one `values` mapping per figure. Returns "" the moment ANY callout in ANY frame
    has a value, because then the chips differ between tools and are worth their space; only when
    every one of them is suppressed across the whole row is the list replaceable by this sentence.
    That keeps the disclosure (the categories are named, and the reader is told they are excluded)
    while printing it once.
    """
    present = [
        rv
        for values in frames
        for callout_id in _CALLOUT_IDS
        if (rv := values.get(callout_id)) is not None
    ]
    if not present or any(rv.state == STATE_STABLE for rv in present):
        return ""
    labels = []
    for values in frames:
        for callout_id in _CALLOUT_IDS:
            rv = values.get(callout_id)
            if rv is not None and rv.label not in labels:
                labels.append(rv.label)
    return (
        "No figure above draws these categories, and none of them clears the reporting floor for "
        f"any tool shown: {', '.join(labels)}."
    )


def build_callout_chips_html(values: Mapping[str, RegionValue]) -> str:
    """Pure builder (no `st.*` call) for the D-12 callout chips (internal, burn_25_50, all_body,
    not_recorded) -- rendered beside the figure, never forced onto the silhouette itself, since
    callouts carry no `svg_element_ids`. Returns "" when none of the 4 callout ids are present in
    `values` (nothing to render)."""
    chips: list[str] = []
    for callout_id in _CALLOUT_IDS:
        rv = values.get(callout_id)
        if rv is None:
            continue
        label = html.escape(rv.label)
        display = html.escape(rv.display_value)
        chips.append(
            '<span class="wii-body-chip">'
            f'<span class="wii-body-chip-label">{label}</span>'
            f'<span class="wii-body-chip-value">{display}</span>'
            "</span>"
        )
    if not chips:
        return ""
    return f'<div class="wii-body-chips">{"".join(chips)}</div>'


def top_region_chips_html(
    values: Mapping[str, RegionValue], region_ids: set[str], limit: int = 3
) -> str:
    """My Shop PROTECT THIS (D-04): chips for the `limit` highest-value STABLE regions among
    `region_ids` (ties broken by label), value-desc -- limited and suppressed regions are never
    chipped, and this is a presentation choice only (it makes no suppression decision of its own,
    T-03-07). Returns "" when nothing qualifies. Reuses the existing `.wii-body-chips`/
    `.wii-body-chip` markup shape (`build_callout_chips_html`'s escape-every-interpolated-string,
    single-string, no-blank-line pattern)."""
    candidates = [
        (rv.value, rv.label, rv.display_value)
        for region_id, rv in values.items()
        if region_id in region_ids and rv.state == STATE_STABLE and rv.value is not None
    ]
    candidates.sort(key=lambda item: (-item[0], item[1]))
    chips: list[str] = []
    for _value, label, display_value in candidates[:limit]:
        chips.append(
            '<span class="wii-body-chip">'
            f'<span class="wii-body-chip-label">{html.escape(label)}</span>'
            f'<span class="wii-body-chip-value">{html.escape(display_value)}</span>'
            "</span>"
        )
    if not chips:
        return ""
    return f'<div class="wii-body-chips">{"".join(chips)}</div>'


# ---------------------------------------------------------------------------------------------
# Per-region depth (Body Map's click-through view)
# ---------------------------------------------------------------------------------------------
#
# Depth ENCODES the measure: a region with a higher stable value sits closer to the viewer. It is
# a second channel for the same number the fill colour carries, never decoration, so:
#   * only STABLE values lift. Limited, suppressed and absent regions sit on the floor -- raising
#     an unreliable estimate toward the viewer would give it exactly the prominence its dashed
#     outline is there to withhold;
#   * the value is square-root companded before it becomes depth, so the finger (roughly half of
#     all workshop injuries) does not flatten every other region into the floor.
#
# Every length below is in viewBox units and is emitted as `cqw` against the keyed container
# (`container-type: inline-size`), so depth scales with the figure instead of getting relatively
# stronger as the figure shrinks. DEPTH_PERSPECTIVE is the ONE perspective constant: the CSS
# `perspective()` and the Python counter-translate are both derived from it, and if they ever
# disagreed the magnification drift would no longer cancel.
#
# There is deliberately no rotateX/rotateY. A tilted flat figure is a skewed flat figure (measured:
# the old 11deg tilt turned the shoulder bar into a parallelogram and changed nothing else), and
# with a tiling silhouette any rotation-induced parallax opens visible gaps between neighbouring
# regions. Depth here is real translateZ under a real perspective, read through magnification,
# occlusion (nearer regions paint over farther ones). There is no cast shadow: a drawing does not
# cast one, and the depth-proportional drop-shadow this used to carry was built on an
# `rgba(0,0,0,...)` literal that only read at all against the retired dark ground.

DEPTH_PERSPECTIVE = 600.0
DEPTH_FLOOR = -6.0
DEPTH_MAX = 60.0


def region_depth(rv: RegionValue | None, scale: BodyScale) -> float:
    """viewBox-unit translateZ for one region: sqrt-companded position of its STABLE value on the
    shared scale, mapped onto [0, DEPTH_MAX]; DEPTH_FLOOR for anything that is not a stable,
    positive estimate."""
    if rv is None or rv.state != STATE_STABLE or rv.value is None or rv.value <= 0:
        return DEPTH_FLOOR
    span = scale.vmax - scale.vmin
    t = 1.0 if span <= 0 else (rv.value - scale.vmin) / span
    return math.sqrt(min(1.0, max(0.0, t))) * DEPTH_MAX


def region_depths(values: Mapping[str, RegionValue], scale: BodyScale) -> dict[str, float]:
    return {region_id: region_depth(rv, scale) for region_id, rv in values.items()}


def depth_transform(z: float, box: Box, vb_w: float) -> str:
    """The CSS transform shared, character for character, by a region-piece's visual layer and
    its hit-zone button.

    Under `perspective(P)`, `translateZ(z)` magnifies by `mag = P / (P - z)` about the transform
    origin, which also SLIDES an off-origin piece outward by `(c - origin) * (mag - 1)`. The
    trailing `translate()` cancels that exactly: with `k = (mag - 1) / mag = z / P`, translating
    by `-(c - origin) * k` before projection lands the piece's own centre `c` back where it was.
    Both elements set `transform-origin` to the same page point (see `build_hit_zone_css`), so
    the net effect for every point is `c + (p - c) * mag`: each piece swells or recedes in place,
    and the layer and its button undergo the identical mapping. Lengths are `cqw` of the keyed
    container, which every element inside it resolves identically.
    """
    x, y, w, h = box
    cq = 100.0 / vb_w
    k = z / DEPTH_PERSPECTIVE
    cx, cy = x + w / 2, y + h / 2
    ox, oy = _depth_origin(vb_w)
    tx, ty = -(cx - ox) * k, -(cy - oy) * k
    return (
        f"perspective({DEPTH_PERSPECTIVE * cq:.4f}cqw) translateZ({z * cq:.4f}cqw) "
        f"translate({tx * cq:.4f}cqw,{ty * cq:.4f}cqw)"
    )


def depth_zone_transform(z: float, box: Box, vb_w: float) -> str:
    """The hit zone's transform: the SAME point mapping as `depth_transform`, written as a 2D
    affine matrix.

    With no rotation, `perspective(P) translateZ(z) translate(t)` about origin `o` maps every
    in-plane point `p` to `o + mag * (p - o + t)` -- exactly `translate(mag * t) scale(mag)` about
    the same origin. The two strings differ; the geometry they produce does not
    (`test_zone_and_layer_transforms_produce_the_identical_mapping` checks it numerically, and the
    browser measurement checks it to <0.5px).

    Why not reuse the projective string on the button: Chromium HIT-TESTS a projective transform
    (matrix3d with m34 != 0) over a different footprint than it paints. Measured on this page: the
    raised upper-leg zone reported a 119px-tall rect ending at y=904 yet captured clicks down to
    y=930, swallowing the entire knee -- and every floor-level region adjacent to a raised one
    (wrist, knee, ankle, foot, toe, neck, shoulder, pubic region, ear) lost 100% of its real clicks.
    The visual layers keep true `translateZ`: they are `pointer-events:none`, so only their
    painting matters, and painting is correct.
    """
    x, y, w, h = box
    cq = 100.0 / vb_w
    mag = DEPTH_PERSPECTIVE / (DEPTH_PERSPECTIVE - z)
    k = z / DEPTH_PERSPECTIVE
    ox, oy = _depth_origin(vb_w)
    tx, ty = -(x + w / 2 - ox) * k, -(y + h / 2 - oy) * k
    return f"translate({mag * tx * cq:.4f}cqw,{mag * ty * cq:.4f}cqw) scale({mag:.6f})"


def _depth_origin(vb_w: float) -> tuple[float, float]:
    """The shared perspective origin: the figure's horizontal centre, at the top of the viewBox.
    With the counter-translate above the result is independent of where this sits, so a fixed,
    easily-expressed point is used; `transform-origin` on each element encodes it in that
    element's own percentage space."""
    return (vb_w / 2.0, 0.0)


def _box_contains(outer: Box, inner: Box) -> bool:
    ox, oy, ow, oh = outer
    ix, iy, iw, ih = inner
    return ox <= ix and oy <= iy and ix + iw <= ox + ow and iy + ih <= oy + oh and ow * oh > iw * ih


def hit_zone_order(
    view: str, region_ids, depths: Mapping[str, float] | None = None
) -> list[tuple[str, int]]:
    """(region_id, subpath_index) pieces in PAINT order -- the order both the visual layers and
    the hit-zone buttons are emitted in, so a zone's stacking always matches what the eye sees.

    Sort key: (nesting level, depth, document order, subpath index).

    * Nesting first: a piece whose box sits entirely inside another region's box (face in head,
      eye and mouth in face) always paints above its container, whatever the two depths are --
      otherwise a raised head would cover the face it contains.
    * Depth next: nearer pieces paint over farther ones, which is what makes depth read as depth
      where a magnified region overlaps its neighbour.
    * Document order breaks ties, preserving the artwork's own paint order. That tie-break is what
      fixed the wrist once stealing 14.9% of the hand: ordering by descending AREA put the larger
      wrist shape on top of the hand it is painted under.
    """
    boxes = region_hit_boxes(view)
    depths = depths or {}
    everything = [(rid, box) for rid, sub_boxes in boxes.items() for box in sub_boxes]
    pieces = []
    for doc_index, (region_id, sub_boxes) in enumerate(boxes.items()):
        if region_id not in region_ids:
            continue
        for sub_index, box in enumerate(sub_boxes):
            nesting = sum(
                1
                for other_id, other in everything
                if other_id != region_id and _box_contains(other, box)
            )
            pieces.append((nesting, depths.get(region_id, 0.0), doc_index, sub_index, region_id))
    pieces.sort()
    return [(region_id, sub_index) for _n, _z, _d, sub_index, region_id in pieces]


def build_depth_layers_html(
    values: Mapping[str, RegionValue],
    scale: BodyScale,
    instance_id: str,
    view: str,
    depths: Mapping[str, float],
    selected_region_id: str | None = None,
) -> str:
    """The Body Map's 3D figure: one absolutely-positioned HTML layer per region PIECE (subpath),
    each holding its own full-viewBox `<svg>`, all superimposed.

    Per-piece HTML layers are not an implementation detail. An `<svg>` is a flattening context --
    `transform-style: preserve-3d` is a no-op inside it and `translateZ` on a child shape does
    nothing (measured: a 40px shape at translateZ(120px) renders 40.00px inside an SVG, 47.06px in
    its own HTML div). And each PIECE, not each region, gets a layer because a two-handed region
    magnified about a single centre on the body's midline would slide both hands outward, off
    their wrists.

    Fill and `<title>` come from `build_body_svg` (via `region_paint`) -- this function only
    redistributes its output, so the flat and click-through renderers can never disagree about
    what a region looks like. Fills are left untouched: no gradient, gloss or lighting is laid
    over them, because ink density IS the value. Strokes (the confidence dash, the selection
    accent) move onto a separate fill-less OUTLINE layer painted above every fill layer with the
    same transform, so a dashed construction outline is never half-covered by a neighbour that
    paints later -- which would silently downgrade a "provisional" mark to an unmarked one.

    Any `<defs>` the artwork carries is hoisted into one shared, zero-size
    `<svg class="wii-body-defs">`; `url(#...)` paint references resolve across SVG roots in the
    same document. Returned as a single line -- no newline at all, so no blank line can end the
    raw-HTML block early.
    """
    vb_w, vb_h = viewbox_size(view)
    root = ET.fromstring(
        build_body_svg(values, scale, instance_id, view, selected_region_id=selected_region_id)
    )
    defs = root.find(f"{{{_SVG_NS}}}defs")
    elements = {
        elem.get("id").rsplit("-", 1)[0]: elem
        for elem in root.iter()
        if _is_region_id(elem.get("id"))
    }
    viewbox = root.get("viewBox") or f"0 0 {vb_w:g} {vb_h:g}"

    def layer_svg(child: ET.Element) -> str:
        svg = ET.Element(
            f"{{{_SVG_NS}}}svg",
            {"viewBox": viewbox, "aria-hidden": "true", "focusable": "false"},
        )
        svg.append(child)
        return ET.tostring(svg, encoding="unicode")

    fills: list[str] = []
    outlines: list[str] = []
    for region_id, sub_index in hit_zone_order(view, set(elements), depths):
        elem = elements[region_id]
        pieces = _element_subpaths(elem)
        _box, piece_d = pieces[sub_index]
        shape = ET.Element(elem.tag, {"fill": elem.get("fill", "none")})
        if piece_d is None:  # legacy primitive: one piece, copy its geometry verbatim
            for key, val in elem.attrib.items():
                if key not in ("id", "fill", "stroke", "stroke-width", "stroke-dasharray"):
                    shape.set(key, val)
        else:
            shape.tag = f"{{{_SVG_NS}}}path"
            shape.set("d", piece_d)
        if sub_index == 0:
            shape.set("id", elem.get("id"))
        shape.set("class", f"wii-r-{region_id}")
        title = elem.find(f"{{{_SVG_NS}}}title")
        if title is not None:
            shape.append(title if len(pieces) == 1 else _copy_title(title))
        outline = ET.Element(shape.tag, {"fill": "none", "class": f"wii-o-{region_id}"})
        for key in ("d", "x", "y", "width", "height", "rx", "ry", "cx", "cy", "r"):
            if shape.get(key) is not None:
                outline.set(key, shape.get(key))
        for key in ("stroke", "stroke-width", "stroke-dasharray"):
            if elem.get(key) is not None:
                outline.set(key, elem.get(key))
        if region_id == selected_region_id:
            outline.set("class", f"wii-o-{region_id} wii-body-selected")
        piece_class = f"wii-l-{region_id}-{sub_index}"
        data = f'data-region="{region_id}" data-piece="{sub_index}"'
        fills.append(f'<div class="wii-body-layer {piece_class}" {data}>{layer_svg(shape)}</div>')
        outlines.append(
            f'<div class="wii-body-layer wii-body-outline {piece_class}">{layer_svg(outline)}</div>'
        )

    defs_svg = ""
    if defs is not None:
        holder = ET.Element(
            f"{{{_SVG_NS}}}svg",
            {"class": "wii-body-defs", "width": "0", "height": "0", "aria-hidden": "true"},
        )
        holder.append(defs)
        defs_svg = ET.tostring(holder, encoding="unicode")
    aria = html.escape(root.get("aria-label") or f"{view.title()} body silhouette")
    return (
        f'<div class="wii-body-3d" role="img" aria-label="{aria}">'
        + defs_svg
        + "".join(fills)
        + "".join(outlines)
        + "</div>"
    ).replace("\n", " ")


def _copy_title(title: ET.Element) -> ET.Element:
    clone = ET.Element(title.tag)
    clone.text = title.text
    return clone


def build_hit_zone_css(
    wrap_key: str, view: str, region_ids, depths: Mapping[str, float] | None = None
) -> str:
    """The `<style>` block that turns one keyed `st.container` into the click-through 3D body map.

    Streamlit gives a keyed container the class `st-key-{key}`, which is the documented hook for
    per-element CSS; every other selector here (`stElementContainer`, `stButton`) is the same
    class of test-id selector the app already uses for chrome. Several rules are load bearing,
    each one an empirically-confirmed failure:

    * `display:block; line-height:0` on `[data-testid='stButton']` is mandatory. Without it the
      `<button>` is an inline-flex box on a ~25px text baseline and renders 6-13px BELOW its own
      hit zone, which makes small regions (eye, mouth, toe) look unclickable.
    * The wrapper takes its height from `aspect-ratio`, never `height:<px>`: an explicit height is
      overridden, the block collapses to 0px, and the following (relatively positioned) element
      containers then paint over the absolutely positioned buttons and swallow every click.
    * The figure layer is `pointer-events:none` so clicks fall through to the buttons beneath it,
      and the buttons are emitted BEFORE it so the `~` sibling combinator can reach the real
      outline and highlight it on hover/focus with no JavaScript at all.
    * THE HIT ZONE CARRIES ITS LAYER'S TRANSFORM. Each piece's button gets the same point
      mapping as its visual layer (`depth_zone_transform`, the affine form of `depth_transform`
      -- see there for why the string differs), plus a `transform-origin` that names the same
      page point in the button's own percentage space. A transform that moved the shapes but not
      the buttons once knocked `toe` 5.27px (27% of its width) off its own zone.
    * `container-type: inline-size` on the wrapper is what every `cqw` length resolves against.
      It also overrides theme.py's whole-figure `rotateY` tilt (and its hover un-tilt) on this
      container: stacked on the per-piece transforms it would double-transform the figure.

    Returned as one line with no blank lines anywhere: a blank line would end the raw-HTML block
    per CommonMark and leak the remaining CSS into the page as visible text.
    """
    vb_w, vb_h = viewbox_size(view)
    boxes = region_hit_boxes(view)
    outlines = region_hit_outlines(view)
    depths = depths or {}
    ox, oy = _depth_origin(vb_w)
    k = f".st-key-{wrap_key}"
    ec = "[data-testid='stElementContainer']"
    rules = [
        f"{k}{{position:relative;width:100%;max-width:260px;aspect-ratio:{vb_w:g}/{vb_h:g};"
        "gap:0 !important;container-type:inline-size;transform:none;transition:none;}",
        f"{k}:hover{{transform:none;}}",
        # Streamlit gives every element container `min-width:15px`; measured, that silently
        # widened the ear and eye zones by 3-4px past their shapes. Zones must be exactly their box.
        f"{k} {ec}{{position:absolute;margin:0;padding:0;min-width:0;min-height:0;}}",
        # The figure's own element container is sized to the whole figure so the layers can
        # `inset:0` against it -- which also makes it cover every button. Measured: without
        # `pointer-events:none` HERE (not only on the child) it swallowed 100% of real clicks.
        f"{k} {ec}:has(.wii-body-hit-svg){{left:0;top:0;width:100%;height:100%;"
        "pointer-events:none;}",
        f"{k} .wii-body-hit-svg{{position:absolute;inset:0;pointer-events:none;z-index:3;}}",
        f"{k} .wii-body-3d{{position:absolute;inset:0;}}",
        f"{k} .wii-body-defs{{position:absolute;width:0;height:0;overflow:hidden;}}",
        f"{k} .wii-body-layer{{position:absolute;inset:0;"
        f"transform-origin:{ox / vb_w * 100:.4f}% {oy / vb_h * 100:.4f}%;}}",
        f"{k} .wii-body-layer svg{{display:block;width:100%;height:100%;max-width:none;"
        "overflow:visible;}",
        f"{k} [data-testid='stButton']{{width:100%;height:100%;display:block;line-height:0;}}",
        # A `help=`-bearing button is wrapped by Streamlit as
        # stButton > div > stTooltipIcon > stTooltipHoverTarget > button. Every one of those is
        # auto-height by default, so `button{height:100%}` resolves against a collapsed parent and
        # the button renders ~2px tall: measured 3.0% of the silhouette's visible pixels landing
        # on the correct button, and a real click at a region's centre selecting nothing at all.
        # ...but NEVER `display` on stButton's direct children: Streamlit 1.63 renders a second,
        # natively `display:none` copy of every help-bearing button beside the tooltip-wrapped
        # one. Forcing `display:block` here un-hid it one full zone-height BELOW its own zone --
        # measured in Chromium, every region then stole the clicks of the region beneath it
        # (upper leg took the knee, lower leg took ankle, foot and toe): 24 of 65 pieces.
        f"{k} [data-testid='stButton'] > div{{width:100%;height:100%;}}",
        f"{k} [data-testid='stTooltipIcon']{{width:100%;height:100%;display:block;}}",
        f"{k} [data-testid='stTooltipHoverTarget']{{width:100%;height:100%;display:block;}}",
        f"{k} [data-testid='stButton'] button{{display:block;width:100%;height:100%;min-height:0;"
        "min-width:0;padding:0;border:1px solid transparent;border-radius:3px;"
        "background:transparent;color:transparent;font-size:0;overflow:hidden;}",
        f"{k} [data-testid='stButton'] button *{{font-size:0 !important;line-height:0 !important;"
        "margin:0 !important;}",
        f"{k} [data-testid='stButton'] button:focus-visible{{border-color:{theme.PRIMARY};"
        "outline:2px solid " + theme.PRIMARY + ";outline-offset:1px;}",
        f"{k} .wii-body-selected{{animation:wii-body-pulse 650ms ease-out 1;}}",
        "@keyframes wii-body-pulse{0%{stroke-width:7;stroke-opacity:.35}"
        "100%{stroke-width:" + SELECTED_STROKE_WIDTH + ";stroke-opacity:1}}",
    ]
    for region_id, sub_index in hit_zone_order(view, region_ids, depths):
        box = boxes[region_id][sub_index]
        x, y, w, h = box
        z = depths.get(region_id, 0.0)
        transform = depth_transform(z, box, vb_w)
        piece = f"{region_id}-{sub_index}"
        zone = f"{k} .st-key-{wrap_key}__{region_id}__{sub_index}"
        rules.append(f"{k} .wii-l-{piece}{{transform:{transform};}}")
        rules.append(
            f"{zone}{{left:{x / vb_w * 100:.4f}%;top:{y / vb_h * 100:.4f}%;"
            f"width:{w / vb_w * 100:.4f}%;height:{h / vb_h * 100:.4f}%;"
            f"transform-origin:{(ox - x) / w * 100:.4f}% {(oy - y) / h * 100:.4f}%;"
            f"transform:{depth_zone_transform(z, box, vb_w)};"
            f"{_clip_polygon(outlines, region_id, sub_index, box)}}}"
        )
        # Hover AND keyboard focus both mark the region's outline layer through the `~`
        # combinator. The focus case is not decoration: the figure paints over the buttons, so a
        # ring drawn on the button itself is hidden behind opaque fills -- measured at zero
        # visible pixels for eye, mouth and face, a WCAG 2.4.7 failure.
        rules.append(
            f"{zone}:hover ~ {ec} .wii-o-{region_id}{{stroke:{theme.PRIMARY};"
            f"stroke-width:{SELECTED_STROKE_WIDTH};stroke-dasharray:none;}}"
        )
        rules.append(
            f"{zone}:focus-within ~ {ec} .wii-o-{region_id}"
            f"{{stroke:{theme.TEXT};stroke-width:{SELECTED_STROKE_WIDTH};stroke-dasharray:4 2;}}"
        )
    rules.append(
        "@supports not (transform-style:preserve-3d){"
        f"{k} .wii-body-layer,{k} {ec}{{transform:none !important;}}}}"
    )
    rules.append(
        "@media (prefers-reduced-motion:reduce){"
        f"{k} .wii-body-selected{{animation:none !important;}}}}"
    )
    return "<style>" + "".join(rules) + "</style>"


def _clip_polygon(outlines, region_id: str, sub_index: int, box: Box) -> str:
    """`clip-path:polygon(...)` in percentages of the zone's own box, so the zone scales with the
    figure and is exactly its shape. Clipped-away area is not hit-tested, which is what matters:
    a rectangle cannot describe a head, and without this the raised head's box took every click
    on the visible part of the ear beside it (measured). Empty for a legacy primitive."""
    pts = outlines.get(region_id, [[]])[sub_index] if region_id in outlines else []
    if len(pts) < 3:
        return ""
    x, y, w, h = box
    coords = ",".join(f"{(px - x) / w * 100:.1f}% {(py - y) / h * 100:.1f}%" for px, py in pts)
    return f"clip-path:polygon({coords});"


def render_clickable_body_view(
    values: Mapping[str, RegionValue],
    scale: BodyScale,
    instance_id: str,
    view: str,
    region_ids,
    labels: Mapping[str, str],
    on_pick,
    selected_region_id: str | None = None,
) -> None:
    """One click-through 3D silhouette: invisible per-PIECE `st.button` hit zones under a
    `pointer-events:none` stack of per-piece depth layers, inside a single keyed container.

    Selection goes through real `<button>` widgets, so it is a normal Streamlit rerun -- session
    state survives, unlike the `<a href="?region=">` navigation the 2026-09-15 spike measured.
    They are also tab-focusable, Enter-activatable and carry the region label as their accessible
    name, which the anchor mechanism never provided. A bilateral region gets one button per side
    (keys `...__hand__0`, `...__hand__1`), both picking the same region, so each hand has its own
    tight hit zone instead of one rectangle spanning the torso between them.

    This never calls `build_body_svg` with `link_regions=True`: the anchors and the hit zones are
    two different mechanisms and stacking them would double-fire a pick.
    """
    wrap_key = f"{instance_id}-{view}".replace("_", "-")
    depths = region_depths(values, scale)
    layers = build_depth_layers_html(
        values, scale, instance_id, view, depths, selected_region_id=selected_region_id
    )
    st.markdown(build_hit_zone_css(wrap_key, view, region_ids, depths), unsafe_allow_html=True)
    view_label = "front" if view == "front" else "back"
    with st.container(key=wrap_key):
        for region_id, sub_index in hit_zone_order(view, region_ids, depths):
            label = labels.get(region_id, region_id)
            rv = values.get(region_id)
            # The figure is pointer-events:none so clicks reach the buttons, which means its
            # native <title> tooltips can never be hovered. Carrying the same D-04 text as the
            # button's `help` is what keeps the per-region value, share, top diagnosis/mechanism,
            # raw N and -- critically -- the "Estimate suppressed" / "Limited sample" labels
            # reachable at all. Without this the page has no textual channel for suppression,
            # which is the one thing this product must never drop silently.
            help_text = _region_title_text(rv) if rv is not None else None
            selected_mark = " (selected)" if region_id == selected_region_id else ""
            st.button(
                f"{label} ({view_label} view){selected_mark}",
                key=f"{wrap_key}__{region_id}__{sub_index}",
                help=help_text,
                on_click=on_pick,
                args=(region_id,),
            )
        st.markdown(f'<div class="wii-body-hit-svg">{layers}</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------------------------
# The key, and the count of regions that fell below the floor
# ---------------------------------------------------------------------------------------------
#
# The flat figure shipped with NO key at all, so the one visual channel carrying confidence was
# never named anywhere on four of nine pages. Both builders below are pure (no `st.*`) and both
# emit a single line with no blank line in it, so neither can end a raw-HTML block early.
#
# Styles are written inline from `theme` tokens rather than as new classes: this module does not
# own `theme.py`, and every value used here is a token, not a literal.

_KEY_SWATCH_BASE = (
    "display:inline-block;width:18px;height:11px;vertical-align:-1px;margin-right:6px;"
)
_KEY_ROW_STYLE = (
    "display:inline-flex;align-items:center;font-size:var(--wii-text-micro);"
    "color:var(--wii-graphite-soft);margin:0 18px 0 0;"
)
_KEY_WRAP_STYLE = "display:flex;flex-wrap:wrap;gap:4px 0;margin:6px 0 2px;"


def _key_swatch_style(state: str) -> str:
    """One legend swatch, painted by the SAME rules the figure uses: a stable key is a filled
    block at the scale's deep end behind a solid hairline, a limited key the same fill lightened
    behind a dashed one, a suppressed key bare sheet behind a dashed one."""
    line = theme.stability_line(state)
    dash = {"solid": "solid", "dash": "dashed", "dot": "dashed"}[str(line["dash"])]
    deep = theme.SEQUENTIAL_SCALE[-2]
    fill = {
        STATE_STABLE: deep,
        STATE_LIMITED: _blend(deep, theme.VELLUM, LIMITED_FILL_LIGHTEN),
        STATE_SUPPRESSED: theme.VELLUM,
    }[state]
    return f"{_KEY_SWATCH_BASE}background:{fill};border:1px {dash} {line['color']};"


def stability_legend_html() -> str:
    """The three-key legend naming every treatment the figure can draw. Rendered on every
    viewport and beside every flat figure -- the 3D component hid its own key under 640px, which
    left the phone visitor with no way to learn what an unfilled region meant."""
    rows = "".join(
        f'<span style="{_KEY_ROW_STYLE}">'
        f'<span style="{_key_swatch_style(state)}" aria-hidden="true"></span>'
        f"{html.escape(label)}</span>"
        for state, label in _LEGEND_KEYS
    )
    return f'<div style="{_KEY_WRAP_STYLE}">{rows}</div>'


def stability_floor_note(values: Mapping[str, RegionValue], region_ids) -> str:
    """ "N of M regions are below the reporting floor" -- the count the reader would otherwise
    have to tally off the silhouette. Returns "" when every region clears the floor, so a clean
    figure carries no needless line."""
    considered = [rv for region_id, rv in values.items() if region_id in region_ids]
    below = sum(1 for rv in considered if rv.state != STATE_STABLE)
    if not considered or not below:
        return ""
    # No drawing instruction in this sentence. The same line is printed under a full-size figure,
    # where unstable regions carry a dashed outline, and under a small multiple, where they do
    # not -- so naming the treatment would be wrong in one of the two places. The COUNT is the
    # information; how it is drawn is what the legend and the figure itself are for.
    return (
        f"{below} of {len(considered)} body regions are below the reporting floor in this "
        "selection and carry no estimate."
    )


VIEWS_BOTH: tuple[str, ...] = ("front", "back")
VIEWS_FRONT: tuple[str, ...] = ("front",)


def render_body_map(
    values: Mapping[str, RegionValue],
    scale: BodyScale,
    instance_id: str,
    caption: str = "",
    selected_region_id: str | None = None,
    link_regions: bool = False,
    views: tuple[str, ...] = VIEWS_BOTH,
    region_ids=None,
    legend: bool = True,
    confidence_outlines: bool = True,
    callouts: bool = True,
    floor_note: bool = True,
) -> None:
    """Renders the requested `views` side by side via `st.markdown(unsafe_allow_html=True)`
    (never `st.html`), then the key, the stability-floor note, the caption and the callout chips.
    Static + hover only unless `link_regions=True`.

    `views=VIEWS_FRONT` draws the front plate alone -- for pages (My Shop's PROTECT THIS, the
    Comparison small multiples) where the back view repeated the front's one finding down another
    460px of page. `region_ids` scopes the floor note to the silhouette's own regions, excluding
    the four callouts, which carry no `<path>`.

    FOUR FLAGS EXIST FOR THE SMALL-MULTIPLE CASE, and a row of figures should pass all four.
    Rendered at ~140px a side, everything this function draws around the plate repeats per
    figure: four legends saying the same thing, four identical callout lists, four floor-count
    lines. `legend=False`, `callouts=False` and `floor_note=False` let the calling page draw each
    once for the row; `confidence_outlines=False` drops the per-region dashes, which at that size
    read as texture rather than as a line treatment (see `region_paint`).

    The floor COUNT differs per tool, so a caller that drops it from the figures owes the reader
    the numbers somewhere -- `stability_floor_note` is public for exactly that.
    """
    plates = "".join(
        '<div class="wii-body-map-view">'
        + build_body_svg(
            values,
            scale,
            instance_id,
            view,
            selected_region_id=selected_region_id,
            link_regions=link_regions,
            confidence_outlines=confidence_outlines,
        )
        + "</div>"
        for view in views
    )
    st.markdown(f'<div class="wii-body-map">{plates}</div>', unsafe_allow_html=True)
    if legend:
        st.markdown(stability_legend_html(), unsafe_allow_html=True)
    if floor_note:
        note = stability_floor_note(values, region_ids if region_ids is not None else set(values))
        if note:
            st.caption(note)
    if caption:
        st.caption(caption)
    if callouts:
        chips_html = build_callout_chips_html(values)
        if chips_html:
            st.markdown(chips_html, unsafe_allow_html=True)
