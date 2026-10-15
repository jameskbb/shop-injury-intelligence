"""D-12 shared body-silhouette renderer tests (03-03 Task 2 `<behavior>`). `build_body_svg` /
`build_callout_chips_html` are pure string-builders -- no streamlit/AppTest needed. ID coverage is
checked against the real committed `dim_body_region` table (never a hard-coded id list) so the
assets always stay in sync with the taxonomy.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import pytest

from wii.app import theme
from wii.app.components import body_map
from wii.app.components.body_map import (
    BodyScale,
    RegionValue,
    _region_title_text,
    build_body_svg,
    build_callout_chips_html,
    top_region_chips_html,
)
from wii.app.data import get_connection
from wii.utils.paths import repo_root

_SVG_NS = "{http://www.w3.org/2000/svg}"
_TITLE_TAG = f"{_SVG_NS}title"


def _dim_body_region_rows():
    con = get_connection()
    return con.execute(
        "SELECT region_id, kind, svg_element_ids FROM dim_body_region ORDER BY sort_order"
    ).fetchall()


def _asset_ids(view: str) -> list[str]:
    path = repo_root() / "assets" / "body" / f"body_{view}.svg"
    tree = ET.parse(path)
    return [e.get("id") for e in tree.getroot().iter() if e.get("id") is not None]


def _full_values(value_fn=None, state="stable", raw_n=200) -> dict[str, RegionValue]:
    value_fn = value_fn or (lambda region_id: 0.1)
    values: dict[str, RegionValue] = {}
    for region_id, _kind, _svg_ids in _dim_body_region_rows():
        values[region_id] = RegionValue(
            label=region_id.replace("_", " ").title(),
            value=value_fn(region_id),
            display_value="10.0%",
            state=state,
            raw_n=raw_n,
        )
    return values


# ---------------------------------------------------------------------------
# ID completeness against dim_body_region (T-03-XX / D-12)
# ---------------------------------------------------------------------------


def test_every_region_svg_element_id_exists_exactly_once_across_the_two_files():
    front_ids = _asset_ids("front")
    back_ids = _asset_ids("back")
    all_ids = front_ids + back_ids
    assert len(all_ids) == len(set(all_ids)), "duplicate id within or across the two SVG files"

    expected_ids: set[str] = set()
    for _region_id, kind, svg_ids in _dim_body_region_rows():
        if kind == "region":
            expected_ids.update(svg_ids)
    assert set(all_ids) == expected_ids


def test_no_other_element_carries_a_region_style_id():
    """Every id present in either asset is a genuine dim_body_region svg_element_id -- no stray
    "-front"/"-back"-suffixed id sneaks in that isn't backed by the taxonomy."""
    expected_ids: set[str] = set()
    for _region_id, kind, svg_ids in _dim_body_region_rows():
        if kind == "region":
            expected_ids.update(svg_ids)
    for view in ("front", "back"):
        for element_id in _asset_ids(view):
            assert element_id in expected_ids, f"unexpected id {element_id!r} in body_{view}.svg"


# ---------------------------------------------------------------------------
# build_body_svg fills
# ---------------------------------------------------------------------------


def test_build_body_svg_shared_scale_fill_not_autoscaled():
    values = _full_values(value_fn=lambda rid: 0.5 if rid == "hand" else 0.1)
    scale = BodyScale(vmin=0.0, vmax=0.5)
    svg = build_body_svg(values, scale, "inst-a", "front")
    root = ET.fromstring(svg)
    hand = next(e for e in root.iter() if e.get("id") == "hand-front")
    assert hand.get("fill") == scale.color_for(0.5)
    torso = next(e for e in root.iter() if e.get("id") == "upper_trunk-front")
    assert torso.get("fill") == scale.color_for(0.1)


def test_absent_region_is_bare_sheet_inside_the_quietest_solid_hairline():
    """A zero estimate is ABSENT: no fill, and a SOLID rule-weight hairline.

    Solid, not dashed, on purpose. "Nothing was recorded here" is a settled fact; the dashed
    construction line means "provisional", which is the opposite of what is known. Drawn at rule
    strength so it recedes behind every region that actually carries a value, while still keeping
    the silhouette readable as one body -- an unstroked region would simply dissolve into the
    sheet.
    """
    values = _full_values(value_fn=lambda rid: 0.0 if rid == "toe" else 0.2)
    del values["ear"]  # absent entirely -- ear-front must stay untouched (no crash, no fill set)
    scale = BodyScale(vmin=0.0, vmax=0.2)
    svg = build_body_svg(values, scale, "inst-a", "front")
    root = ET.fromstring(svg)
    toe = next(e for e in root.iter() if e.get("id") == "toe-front")
    assert toe.get("fill") == theme.VELLUM
    assert toe.get("stroke") == theme.RULE
    assert toe.get("stroke-dasharray") is None
    ear = next(e for e in root.iter() if e.get("id") == "ear-front")
    assert ear.get("fill") == "#1B2029" and ear.get("stroke") is None


def test_suppressed_region_is_dashed_and_unfilled_while_a_caseless_one_is_solid():
    """A suppressed estimate has cases behind it that failed the stability rules; a region with
    zero sample cases has nothing behind it at all. Both draw as bare sheet, and the DASH is the
    only thing that separates them -- provisional geometry versus settled absence."""
    values = _full_values()
    values["wrist"] = RegionValue(
        label="Wrist", value=None, display_value="—", state="suppressed", raw_n=12
    )
    values["toe"] = RegionValue(
        label="Toe", value=None, display_value="—", state="suppressed", raw_n=0
    )
    scale = BodyScale(vmin=0.0, vmax=1.0)
    root = ET.fromstring(build_body_svg(values, scale, "inst-a", "front"))
    wrist = next(e for e in root.iter() if e.get("id") == "wrist-front")
    toe = next(e for e in root.iter() if e.get("id") == "toe-front")
    assert wrist.get("fill") == theme.VELLUM
    assert wrist.get("stroke-dasharray") == theme.DASH_SUPPRESSED
    assert toe.get("fill") == theme.VELLUM
    assert toe.get("stroke-dasharray") is None


def test_limited_region_keeps_its_value_ink_and_takes_a_dashed_outline():
    """Magnitude and confidence are ORTHOGONAL: a limited region still shows the value's ink
    density (lightened one step so it does not read as heavily as a finished estimate), and the
    dash on its outline -- not a colour change -- is what says "provisional". This is what lets a
    region say "large but provisional" at all, which the old amber hatch could not."""
    values = _full_values(value_fn=lambda rid: 0.3)
    values["wrist"] = RegionValue(
        label="Wrist", value=0.5, display_value="50.0%", state="limited", raw_n=25
    )
    scale = BodyScale(vmin=0.0, vmax=0.5)
    root = ET.fromstring(build_body_svg(values, scale, "inst-a", "front"))
    wrist = next(e for e in root.iter() if e.get("id") == "wrist-front")
    stable_top = scale.color_for(0.5)
    assert wrist.get("fill") not in (theme.VELLUM, stable_top)
    assert wrist.get("stroke-dasharray") == theme.DASH_LIMITED
    # ...and it is still lighter than the same value drawn as a finished estimate.
    assert sum(body_map._hex_to_rgb(wrist.get("fill"))) > sum(body_map._hex_to_rgb(stable_top))


def test_no_amber_and_no_hatch_pattern_survives_anywhere_in_the_figure():
    """The regression this whole rewrite exists to prevent. Amber at 0.55 alpha across 16 of 22
    regions made "we do not trust this" the loudest statement on four of nine pages."""
    values = _full_values(value_fn=lambda rid: 0.3)
    values["wrist"] = RegionValue(
        label="Wrist", value=0.3, display_value="30.0%", state="limited", raw_n=25
    )
    svg = build_body_svg(values, BodyScale(vmin=0.0, vmax=0.5), "inst-a", "front")
    assert "pattern" not in svg and "hatch" not in svg
    assert theme.REDLINE not in svg and theme.CAUTION not in svg


def test_build_body_svg_title_per_region_is_escaped():
    values = _full_values()
    values["hand"] = RegionValue(
        label='Hand & <wrist> "area"', value=0.2, display_value="20.0%", state="stable", raw_n=200
    )
    scale = BodyScale(vmin=0.0, vmax=1.0)
    svg = build_body_svg(values, scale, "inst-a", "front")
    root = ET.fromstring(svg)
    hand = next(e for e in root.iter() if e.get("id") == "hand-front")
    titles = [c for c in hand if c.tag == _TITLE_TAG]
    assert len(titles) == 1
    assert titles[0].text.startswith('Hand & <wrist> "area"')
    # Serialized form is XML-escaped -- raw "&"/"<" never reach the markup unescaped (T-03-10).
    assert "&amp;" in svg
    assert "&lt;wrist&gt;" in svg
    assert "Hand & <wrist>" not in svg


def test_build_body_svg_each_touched_region_has_exactly_one_title():
    values = _full_values()
    svg = build_body_svg(values, BodyScale(vmin=0.0, vmax=1.0), "inst-a", "front")
    root = ET.fromstring(svg)
    for element_id in _asset_ids("front"):
        elem = next(e for e in root.iter() if e.get("id") == element_id)
        titles = [c for c in elem if c.tag == _TITLE_TAG]
        assert len(titles) == 1, f"{element_id} has {len(titles)} title children"


def test_build_body_svg_viewbox_and_full_width():
    values = _full_values()
    svg = build_body_svg(values, BodyScale(vmin=0.0, vmax=1.0), "inst-a", "front")
    root = ET.fromstring(svg)
    assert root.get("viewBox")
    assert root.get("width") == "100%"


# ---------------------------------------------------------------------------
# Callout chips (rendered outside the SVG, never as silhouette regions)
# ---------------------------------------------------------------------------


def test_callout_values_render_as_labelled_chips_outside_the_svg():
    values = _full_values()
    values["internal"] = RegionValue(
        label="Internal", value=0.05, display_value="5.0%", state="stable", raw_n=40
    )
    chips_html = build_callout_chips_html(values)
    assert "wii-body-chip" in chips_html
    assert "Internal" in chips_html
    assert "5.0%" in chips_html

    front_svg = build_body_svg(values, BodyScale(vmin=0.0, vmax=1.0), "inst-a", "front")
    assert 'id="internal' not in front_svg


def test_callout_chips_empty_when_no_callouts_present():
    values = {k: v for k, v in _full_values().items() if k not in ("internal", "burn_25_50")}
    values.pop("all_body", None)
    values.pop("not_recorded", None)
    assert build_callout_chips_html(values) == ""


# ---------------------------------------------------------------------------
# D-05 click-to-filter link wrapping / selected-region accent stroke
# ---------------------------------------------------------------------------


def test_link_regions_wraps_each_valued_region_in_anchor_with_region_href():
    values = _full_values()
    svg = build_body_svg(
        values, BodyScale(vmin=0.0, vmax=1.0), "inst-a", "front", link_regions=True
    )
    root = ET.fromstring(svg)
    for region_id, kind, _svg_ids in _dim_body_region_rows():
        if kind != "region":
            continue
        elem = next(e for e in root.iter() if e.get("id") == f"{region_id}-front")
        parent = next(p for p in root.iter() for c in p if c is elem)
        assert parent.tag == f"{_SVG_NS}a"
        assert parent.get("href") == f"?region={region_id}"
        assert parent.get("target") == "_self"


def test_selected_region_gets_accent_stroke_and_unchanged_fill():
    values = _full_values()
    scale = BodyScale(vmin=0.0, vmax=1.0)
    svg_selected = build_body_svg(values, scale, "inst-a", "front", selected_region_id="hand")
    svg_plain = build_body_svg(values, scale, "inst-a", "front")
    root_selected = ET.fromstring(svg_selected)
    root_plain = ET.fromstring(svg_plain)
    hand_selected = next(e for e in root_selected.iter() if e.get("id") == "hand-front")
    hand_plain = next(e for e in root_plain.iter() if e.get("id") == "hand-front")
    assert hand_selected.get("stroke") == theme.BLUEPRINT
    assert hand_selected.get("fill") == hand_plain.get("fill")
    # Selecting a region must never change what it SAYS -- only a stable region's own solid
    # hairline is replaced by the accent.
    assert hand_plain.get("stroke") == theme.GRAPHITE
    assert hand_selected.get("stroke-dasharray") is None


def test_defaults_emit_no_anchor_and_no_stroke():
    values = _full_values()
    scale = BodyScale(vmin=0.0, vmax=1.0)
    svg_defaults = build_body_svg(values, scale, "inst-a", "front")
    svg_explicit_defaults = build_body_svg(
        values, scale, "inst-a", "front", selected_region_id=None, link_regions=False
    )
    assert svg_defaults == svg_explicit_defaults
    root = ET.fromstring(svg_defaults)
    assert not [e for e in root.iter() if e.tag == f"{_SVG_NS}a"]
    # Every painted region now carries a hairline -- the confidence channel IS the outline -- but
    # none of them is the selection accent.
    assert not any(e.get("stroke") == theme.BLUEPRINT for e in root.iter())


def test_link_region_output_has_no_blank_lines():
    values = _full_values()
    svg = build_body_svg(
        values,
        BodyScale(vmin=0.0, vmax=1.0),
        "inst-a",
        "front",
        selected_region_id="hand",
        link_regions=True,
    )
    assert "\n\n" not in svg


# ---------------------------------------------------------------------------
# D-04 tooltip fields: share of tool injuries, most common diagnosis/mechanism
# ---------------------------------------------------------------------------


def test_title_text_includes_d04_fields_in_order():
    rv = RegionValue(
        label="Finger",
        value=5200,
        display_value="5,200",
        state="stable",
        raw_n=120,
        share_display="40.0%",
        top_diagnosis="Laceration",
        top_mechanism="Direct Cutting Contact",
    )
    text = _region_title_text(rv)
    for fragment in (
        "Finger",
        "5,200",
        "Share of tool injuries: 40.0%",
        "Most common diagnosis: Laceration",
        "Most common mechanism: Direct Cutting Contact",
        "120 sample cases (not a national count)",
    ):
        assert fragment in text
    assert (
        text.index("Finger")
        < text.index("5,200")
        < text.index("Share of tool injuries")
        < text.index("Most common diagnosis")
        < text.index("Most common mechanism")
        < text.index("sample cases")
    )


def test_title_text_unchanged_without_new_fields():
    rv = RegionValue(label="Finger", value=0.4, display_value="40.0%", state="stable", raw_n=120)
    assert _region_title_text(rv) == "; ".join(
        ["Finger", "40.0%", "120 sample cases (not a national count)"]
    )


def test_callout_chip_labels_are_escaped():
    values = _full_values()
    values["not_recorded"] = RegionValue(
        label="Not <recorded> & unknown", value=0.02, display_value="2.0%", state="stable", raw_n=10
    )
    chips_html = build_callout_chips_html(values)
    assert "&lt;recorded&gt;" in chips_html
    assert "&amp;" in chips_html
    assert "Not <recorded> & unknown" not in chips_html


# ---------------------------------------------------------------------------
# top_region_chips_html (05-02 Task 3, D-04: My Shop PROTECT THIS)
# ---------------------------------------------------------------------------


def _rv(label: str, value: float | None, state: str, raw_n: int = 200) -> RegionValue:
    display = f"{value * 100:.1f}%" if value is not None else "—"
    return RegionValue(label=label, value=value, display_value=display, state=state, raw_n=raw_n)


def test_top_region_chips_html_picks_top_3_stable_by_value_desc_ties_by_label():
    values = {
        "hand": _rv("Hand", 0.5, "stable"),
        "arm": _rv("Arm", 0.3, "stable"),
        "leg": _rv("Leg", 0.3, "stable"),
        "eye": _rv("Eye", 0.9, "limited"),
        "foot": _rv("Foot", 0.99, "suppressed"),
    }
    region_ids = set(values.keys())
    out = top_region_chips_html(values, region_ids, limit=3)
    assert "\n\n" not in out
    assert "Eye" not in out
    assert "Foot" not in out
    assert out.index("Hand") < out.index("Arm") < out.index("Leg")


def test_top_region_chips_html_escapes_label_with_angle_bracket():
    values = {"x": _rv("<script>", 0.5, "stable")}
    out = top_region_chips_html(values, {"x"})
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_top_region_chips_html_returns_empty_string_when_nothing_stable():
    values = {"x": _rv("Hand", 0.5, "limited")}
    out = top_region_chips_html(values, {"x"})
    assert out == ""


def test_top_region_chips_html_ignores_ids_outside_region_ids():
    values = {
        "hand": _rv("Hand", 0.5, "stable"),
        "internal": _rv("Internal", 0.9, "stable"),
    }
    out = top_region_chips_html(values, {"hand"})
    assert "Hand" in out
    assert "Internal" not in out


# ---------------------------------------------------------------------------
# Artwork shape (the redrawn bilateral silhouette)
# ---------------------------------------------------------------------------

_SINGLE_PIECE = {"head", "face", "mouth", "neck", "shoulder", "upper_trunk", "lower_trunk"}
_SINGLE_PIECE_FRONT_ONLY = {"pubic_region"}


def test_every_region_is_one_path_and_bilateral_regions_have_two_subpaths():
    """The artwork used to draw ONE arm, ONE leg and ONE foot as stacked primitives -- anatomically
    half a person. Every region is now a single `<path>` (the exactly-once id test forbids a second
    element per region), and a bilateral region carries its left and right side as two subpaths
    of that one element. This pins both halves of that contract: the element count AND that the
    other arm, leg, hand and foot actually exist."""
    for view in ("front", "back"):
        root = ET.parse(body_map._asset_path(view)).getroot()
        ided = [e for e in root.iter() if (e.get("id") or "").endswith(f"-{view}")]
        assert ided
        boxes = body_map.region_hit_boxes(view)
        assert len(boxes) == len(ided)
        for elem in ided:
            assert elem.tag == f"{_SVG_NS}path", elem.get("id")
            assert elem.get("fill"), elem.get("id")
            region_id = elem.get("id").rsplit("-", 1)[0]
            expected = 1 if region_id in _SINGLE_PIECE | _SINGLE_PIECE_FRONT_ONLY else 2
            assert len(boxes[region_id]) == expected, (view, region_id)


def test_bilateral_pieces_mirror_about_the_midline():
    vb_w, _vb_h = body_map.viewbox_size("front")
    for view in ("front", "back"):
        for region_id, pieces in body_map.region_hit_boxes(view).items():
            if len(pieces) != 2:
                continue
            (lx, ly, lw, lh), (rx, ry, rw, rh) = pieces
            assert lx + lw <= vb_w / 2 + 0.5 <= rx + 1, (view, region_id)  # left then right
            assert abs((lx + lw / 2) + (rx + rw / 2) - vb_w) < 0.05, (view, region_id)
            assert abs(ly - ry) < 0.05 and abs(lw - rw) < 0.05 and abs(lh - rh) < 0.05


def test_root_keeps_role_and_aria_label():
    for view in ("front", "back"):
        root = ET.parse(body_map._asset_path(view)).getroot()
        assert root.get("role") == "img"
        assert root.get("aria-label")
        assert root.get("viewBox") == "0 0 200 460"


def test_artwork_carries_no_gradient_filter_or_stroke():
    """Honesty: the caution hatch is the only thing separating a max-value region from a
    suppressed one, so nothing in the asset may paint over a fill -- no gradient, no filter, no
    baked-in stroke (the renderer owns every stroke)."""
    for view in ("front", "back"):
        text = body_map._asset_path(view).read_text(encoding="utf-8")
        for banned in ("Gradient", "<filter", "stroke", "opacity"):
            assert banned not in text, (view, banned)


# ---------------------------------------------------------------------------
# Path parsing (region_hit_boxes reads `d` itself -- no new dependency)
# ---------------------------------------------------------------------------


def test_parse_path_resolves_relative_and_shorthand_commands():
    subpaths = body_map.parse_path_subpaths("m10 10 h5 v5 l-5 0 z M20 20 L30 20 30 30 Z")
    assert len(subpaths) == 2
    assert body_map.subpath_box(subpaths[0]) == (10.0, 10.0, 5.0, 5.0)
    # Implicit lineto after a moveto's first pair.
    assert body_map.subpath_box(subpaths[1]) == (20.0, 20.0, 10.0, 10.0)


def test_parse_path_reflects_smooth_curve_control_points():
    (sub,) = body_map.parse_path_subpaths("M0 0 C0 10 10 10 10 0 S20 -10 20 0")
    assert sub[2] == ("C", (10.0, -10.0, 20.0, -10.0, 20.0, 0.0))
    (quad,) = body_map.parse_path_subpaths("M0 0 Q5 10 10 0 T20 0")
    assert quad[2] == ("Q", (15.0, -10.0, 20.0, 0.0))


def test_subpath_box_includes_curve_extrema_not_just_endpoints():
    """A bulging calf or thumb lives entirely in a curve's interior; an endpoint-only box would
    clip it and leave part of the visible shape outside its own hit zone."""
    (sub,) = body_map.parse_path_subpaths("M0 0 C0 40 20 40 20 0 Z")
    x, y, w, h = body_map.subpath_box(sub)
    assert (x, y, w) == (0.0, 0.0, 20.0)
    assert h == pytest.approx(30.0)  # the true apex at t=0.5, not the control points' 40


def test_parse_path_rejects_arcs_loudly():
    with pytest.raises(ValueError):
        body_map.parse_path_subpaths("M0 0 A5 5 0 0 1 10 0")


# ---------------------------------------------------------------------------
# Hit-zone geometry (04-09 click-through body map)
# ---------------------------------------------------------------------------


def test_hit_boxes_are_inside_the_viewbox_and_non_degenerate():
    for view in ("front", "back"):
        vb_w, vb_h = body_map.viewbox_size(view)
        for region_id, pieces in body_map.region_hit_boxes(view).items():
            for x, y, w, h in pieces:
                assert w > 0 and h > 0, region_id
                assert -1 <= x and x + w <= vb_w + 1, region_id
                assert -1 <= y and y + h <= vb_h + 1, region_id


def test_hit_zone_css_has_no_blank_lines_and_covers_every_requested_piece():
    """The blank-line rule is load bearing: CommonMark ends a raw-HTML block at the first blank
    line and re-parses the rest as Markdown, which would leak raw CSS into the page as text. Every
    piece (both hands, both feet) gets its own zone, and every region its hover outline."""
    boxes = body_map.region_hit_boxes("front")
    css = body_map.build_hit_zone_css("body-map-front", "front", set(boxes))
    assert "\n\n" not in css
    assert css.startswith("<style>") and css.endswith("</style>")
    for region_id, pieces in boxes.items():
        for i in range(len(pieces)):
            assert f".st-key-body-map-front__{region_id}__{i}{{" in css
        assert f".wii-o-{region_id}{{stroke:" in css


def test_hit_zone_css_emits_pieces_in_paint_order():
    """Zones stack in the same order the figure paints, so a zone's depth matches what the eye
    sees. With no depths supplied that is the artwork's own document order, lifted only where one
    piece nests inside another region (face in head).

    Ordering by descending area instead looks right for nested shapes but is wrong wherever paint
    order disagrees with area: `wrist` was once a larger shape painted UNDER the smaller `hand`,
    so an area-ordered stack put wrist on top and it stole 14.9% of the visible hand.
    """
    regions = set(body_map.region_hit_boxes("front"))
    css = body_map.build_hit_zone_css("body-map-front", "front", regions)
    order = body_map.hit_zone_order("front", regions)
    emitted = sorted(order, key=lambda p: css.index(f".st-key-body-map-front__{p[0]}__{p[1]}{{"))
    assert emitted == order
    ids = [region_id for region_id, _i in order]
    assert ids.index("wrist") < ids.index("hand")
    assert ids.index("head") < ids.index("face") < ids.index("eye")


def test_nested_pieces_stay_above_their_container_even_when_the_container_is_nearer():
    """A raised head must not paint over the face it contains: nesting outranks depth."""
    regions = set(body_map.region_hit_boxes("front"))
    depths = {r: 0.0 for r in regions} | {"head": body_map.DEPTH_MAX}
    ids = [region_id for region_id, _i in body_map.hit_zone_order("front", regions, depths)]
    assert ids.index("head") < ids.index("face") < ids.index("mouth")


def test_nearer_pieces_paint_and_stack_above_farther_ones():
    regions = set(body_map.region_hit_boxes("front"))
    depths = {r: 0.0 for r in regions} | {"wrist": body_map.DEPTH_MAX}
    ids = [region_id for region_id, _i in body_map.hit_zone_order("front", regions, depths)]
    assert ids.index("hand") < ids.index("wrist")


def test_hit_zone_buttons_carry_the_d04_tooltip_text_as_help():
    """The figure layer is pointer-events:none, so its native <title> tooltips can never be
    hovered. The same text must ride on the button's `help`, or the page loses every per-region
    number and every "Estimate suppressed" / "Limited sample" label -- silently, with a green
    suite."""
    src = (repo_root() / "src/wii/app/components/body_map.py").read_text(encoding="utf-8")
    assert "help=help_text" in src
    assert "_region_title_text(rv)" in src


# ---------------------------------------------------------------------------
# Per-region 3D depth
# ---------------------------------------------------------------------------


def _rule_body(css: str, selector: str) -> str:
    start = css.index(selector + "{") + len(selector) + 1
    return css[start : css.index("}", start)]


def _transform_of(css: str, selector: str) -> str:
    body = _rule_body(css, selector)
    return re.search(r"(?:^|;)transform:([^;]+)", body).group(1)


def _apply_layer(t: str, px: float, py: float, vb_w: float) -> tuple[float, float]:
    """Evaluates an emitted `perspective() translateZ() translate()` string (origin-relative)."""
    persp, z, tx, ty = (float(n) * vb_w / 100 for n in re.findall(r"(-?[\d.]+)cqw", t))
    mag = persp / (persp - z)
    return ((px + tx) * mag, (py + ty) * mag)


def _apply_zone(t: str, px: float, py: float, vb_w: float) -> tuple[float, float]:
    """Evaluates an emitted `translate() scale()` string (origin-relative)."""
    tx, ty = (float(n) * vb_w / 100 for n in re.findall(r"(-?[\d.]+)cqw", t))
    mag = float(re.search(r"scale\(([\d.]+)\)", t).group(1))
    return (px * mag + tx, py * mag + ty)


def test_every_hit_zone_carries_its_visual_layers_transform():
    """THE trap. Each piece's button is its own absolutely-positioned element, so it has to be
    given its layer's transform explicitly. When the old 9deg tilt moved the shapes but not the
    buttons, `toe` sat 5.27px (27% of its width) off its own zone.

    This replaces `test_hit_zone_layer_svg_is_never_independently_transformed`, whose rule
    (transform only the shared container) cannot express per-region depth. The invariant is now
    "zone mapping == layer mapping", evaluated numerically at every piece's corners and centre at
    non-trivial depths. It is a MAPPING check rather than a string check on purpose: the zone
    carries the affine form of the layer's projective transform, because Chromium hit-tests a
    projective (m34 != 0) transform over a larger footprint than it paints -- measured, a raised
    upper-leg zone swallowed every click on the knee below it."""
    vb_w, _vb_h = body_map.viewbox_size("front")
    ox, oy = vb_w / 2.0, 0.0
    boxes = body_map.region_hit_boxes("front")
    depths = {r: (i * 7.0) % body_map.DEPTH_MAX - 6.0 for i, r in enumerate(boxes)}
    css = body_map.build_hit_zone_css("body-map-front", "front", set(boxes), depths)
    for region_id, pieces in boxes.items():
        for i, (x, y, w, h) in enumerate(pieces):
            zone = _transform_of(
                css, f".st-key-body-map-front .st-key-body-map-front__{region_id}__{i}"
            )
            layer = _transform_of(css, f".st-key-body-map-front .wii-l-{region_id}-{i}")
            assert "translateZ" in layer and "perspective" in layer
            assert "matrix3d" not in zone and "perspective" not in zone and "translateZ" not in zone
            for px, py in ((x, y), (x + w, y + h), (x + w / 2, y + h / 2), (x, y + h)):
                lx, ly = _apply_layer(layer, px - ox, py - oy, vb_w)
                zx, zy = _apply_zone(zone, px - ox, py - oy, vb_w)
                assert zx == pytest.approx(lx, abs=2e-3), (region_id, i)
                assert zy == pytest.approx(ly, abs=2e-3), (region_id, i)


def test_hit_zone_transform_origin_names_the_same_page_point_as_the_layer():
    """Identical transform strings only produce identical mappings if both elements pivot about
    the same page point. The layer's origin is expressed against the full figure box, the zone's
    against its own box -- converting both back to viewBox units must land on one point."""
    vb_w, vb_h = body_map.viewbox_size("front")
    boxes = body_map.region_hit_boxes("front")
    css = body_map.build_hit_zone_css("body-map-front", "front", set(boxes))
    layer_origin = re.search(r"\.wii-body-layer\{[^}]*transform-origin:([-\d.]+)% ([-\d.]+)%", css)
    lx, ly = float(layer_origin.group(1)) / 100 * vb_w, float(layer_origin.group(2)) / 100 * vb_h
    for region_id, pieces in boxes.items():
        for i, (x, y, w, h) in enumerate(pieces):
            body = _rule_body(
                css, f".st-key-body-map-front .st-key-body-map-front__{region_id}__{i}"
            )
            ox, oy = re.search(r"transform-origin:([-\d.]+)% ([-\d.]+)%", body).groups()
            assert x + float(ox) / 100 * w == pytest.approx(lx, abs=1e-3)
            assert y + float(oy) / 100 * h == pytest.approx(ly, abs=1e-3)


def test_counter_translate_keeps_each_piece_centred_where_it_was():
    """Depth magnifies about the perspective origin, which would also slide an off-axis piece
    outward by (c - origin) * (mag - 1) -- ~65px uncorrected. Re-derive the projected position of
    a piece's centre and corner from the EMITTED transform string (perspective, translateZ and
    translate, in cqw) and require the centre to stay put and the corner to scale about it."""
    vb_w, _vb_h = body_map.viewbox_size("front")
    ox, oy = vb_w / 2.0, 0.0
    to_vb = vb_w / 100.0
    for box in [(35.0, 252.0, 22.0, 30.0), (140.0, 420.0, 18.0, 12.0), (90.0, 11.0, 20.0, 57.0)]:
        z_vb = 48.0
        t = body_map.depth_transform(z_vb, box, vb_w)
        nums = [float(n) for n in re.findall(r"(-?[\d.]+)cqw", t)]
        persp, z, tx, ty = (n * to_vb for n in nums)
        mag = persp / (persp - z)
        x, y, w, h = box
        cx, cy = x + w / 2, y + h / 2

        def project(px, py, tx=tx, ty=ty, mag=mag):
            return (ox + (px - ox + tx) * mag, oy + (py - oy + ty) * mag)

        assert project(cx, cy)[0] == pytest.approx(cx, abs=1e-3)
        assert project(cx, cy)[1] == pytest.approx(cy, abs=1e-3)
        corner = project(x, y)
        assert corner[0] == pytest.approx(cx + (x - cx) * mag, abs=1e-3)
        assert corner[1] == pytest.approx(cy + (y - cy) * mag, abs=1e-3)


def test_depth_encodes_stable_burden_with_sqrt_companding():
    scale = BodyScale(vmin=0.0, vmax=100.0)
    stable = lambda v: RegionValue(label="r", value=v, display_value="", state="stable", raw_n=99)  # noqa: E731
    assert body_map.region_depth(stable(100.0), scale) == pytest.approx(body_map.DEPTH_MAX)
    assert body_map.region_depth(stable(25.0), scale) == pytest.approx(0.5 * body_map.DEPTH_MAX)
    assert body_map.region_depth(stable(1.0), scale) == pytest.approx(0.1 * body_map.DEPTH_MAX)
    assert body_map.region_depth(stable(1.0), scale) < body_map.region_depth(stable(4.0), scale)


def test_unstable_or_absent_regions_never_rise_toward_the_viewer():
    """Depth is a second channel for the value, so an estimate the product flags as unreliable
    must not get the prominence of being nearest -- limited, suppressed and absent all sit on the
    floor, below every stable region."""
    scale = BodyScale(vmin=0.0, vmax=100.0)
    floor = body_map.DEPTH_FLOOR
    for rv in (
        RegionValue(label="r", value=100.0, display_value="", state="limited", raw_n=30),
        RegionValue(label="r", value=None, display_value="—", state="suppressed", raw_n=12),
        RegionValue(label="r", value=0.0, display_value="0", state="stable", raw_n=0),
        None,
    ):
        assert body_map.region_depth(rv, scale) == floor
    assert floor < body_map.region_depth(
        RegionValue(label="r", value=0.01, display_value="", state="stable", raw_n=99), scale
    )


def test_perspective_constant_is_single_sourced():
    """The CSS perspective and the Python counter-translate must agree or the drift stops
    cancelling. Both derive from DEPTH_PERSPECTIVE -- pin that the emitted value is it."""
    vb_w, _ = body_map.viewbox_size("front")
    t = body_map.depth_transform(10.0, (0.0, 0.0, 10.0, 10.0), vb_w)
    persp = float(re.search(r"perspective\(([\d.]+)cqw\)", t).group(1))
    assert persp == pytest.approx(body_map.DEPTH_PERSPECTIVE * 100.0 / vb_w)
    src = (repo_root() / "src/wii/app/components/body_map.py").read_text(encoding="utf-8")
    assert "perspective(900px)" not in src


def test_depth_css_uses_container_units_fallbacks_and_no_rotation():
    boxes = body_map.region_hit_boxes("front")
    css = body_map.build_hit_zone_css("body-map-front", "front", set(boxes), {"hand": 30.0})
    assert "container-type:inline-size" in css
    assert "translateZ(" in css and "px)" not in _transform_of(
        css, ".st-key-body-map-front .wii-l-hand-0"
    )
    assert "rotateX" not in css and "rotateY" not in css
    assert "@supports not (transform-style:preserve-3d)" in css
    assert "@media (prefers-reduced-motion:reduce)" in css
    # No cast shadow anywhere: a drawing does not cast one, and the drop-shadow this used to
    # emit was an `rgba(0,0,0,...)` literal that only read against the retired dark ground.
    assert "drop-shadow" not in css
    assert "rgba(0,0,0" not in css.replace("rgba(0,0,0,0)", "")


def _layers(html_text: str) -> ET.Element:
    return ET.fromstring(html_text)


def test_depth_layers_one_fill_layer_per_piece_with_titles_and_untouched_fills():
    values = _full_values(value_fn=lambda rid: 0.5 if rid == "hand" else 0.1)
    values["wrist"] = RegionValue(
        label="Wrist", value=0.3, display_value="30.0%", state="limited", raw_n=25
    )
    scale = BodyScale(vmin=0.0, vmax=0.5)
    depths = body_map.region_depths(values, scale)
    out = body_map.build_depth_layers_html(values, scale, "inst-a", "front", depths, "hand")
    assert "\n" not in out
    root = _layers(out)
    flat = ET.fromstring(
        build_body_svg(values, scale, "inst-a", "front", selected_region_id="hand")
    )
    boxes = body_map.region_hit_boxes("front")
    fill_layers = [
        d
        for d in root
        if "wii-body-layer" in (d.get("class") or "") and "wii-body-outline" not in d.get("class")
    ]
    assert len(fill_layers) == sum(len(p) for p in boxes.values())
    for region_id, pieces in boxes.items():
        flat_elem = next(e for e in flat.iter() if e.get("id") == f"{region_id}-front")
        shapes = [e for e in root.iter() if e.get("class") == f"wii-r-{region_id}"]
        assert len(shapes) == len(pieces), region_id
        for shape in shapes:
            assert shape.get("fill") == flat_elem.get("fill"), region_id
            assert shape.get("stroke") is None, region_id  # strokes live on the outline layer
            titles = [c for c in shape if c.tag == _TITLE_TAG]
            assert len(titles) == 1 and titles[0].text == _region_title_text(values[region_id])
    wrist = next(e for e in root.iter() if e.get("id") == "wrist-front")
    assert wrist.get("fill") == flat.find(".//*[@id='wrist-front']").get("fill")
    assert not [e for e in root.iter() if "Gradient" in e.tag or e.tag.endswith("filter")]
    selected = [e for e in root.iter() if "wii-body-selected" in (e.get("class") or "")]
    assert len(selected) == 2 and all(e.get("stroke") == theme.BLUEPRINT for e in selected)


def test_depth_layer_order_matches_hit_zone_order():
    values = _full_values(value_fn=lambda rid: 0.5 if rid in ("finger", "head") else 0.1)
    scale = BodyScale(vmin=0.0, vmax=0.5)
    depths = body_map.region_depths(values, scale)
    root = _layers(body_map.build_depth_layers_html(values, scale, "inst-a", "front", depths))
    layered = [
        (d.get("data-region"), int(d.get("data-piece"))) for d in root if d.get("data-region")
    ]
    regions = set(body_map.region_hit_boxes("front"))
    assert layered == body_map.hit_zone_order("front", regions, depths)


def test_the_full_size_figure_container_is_click_through():
    """The figure's element container is stretched over the whole figure (the layers inset
    against it), so it sits on top of every hit-zone button. Measured in Chromium: with
    `pointer-events:none` only on its child, a real click at every region's centre selected
    nothing -- the container took them all."""
    css = body_map.build_hit_zone_css("body-map-front", "front", {"hand"})
    rule = _rule_body(
        css, ".st-key-body-map-front [data-testid='stElementContainer']:has(.wii-body-hit-svg)"
    )
    assert "pointer-events:none" in rule
    assert "pointer-events:none" in _rule_body(css, ".st-key-body-map-front .wii-body-hit-svg")


def test_hit_zone_css_never_unhides_streamlits_hidden_button_copy():
    """Streamlit 1.63 renders a natively `display:none` duplicate of each help-bearing button as a
    direct child of `stButton`. A `> div{display:block}` rule revived it one zone-height below
    its own zone, and in a real browser 24 of 65 region pieces had every click taken by the
    region above them. The height chain may size those children; it must not display them."""
    css = body_map.build_hit_zone_css("body-map-front", "front", {"hand"})
    rule = _rule_body(css, ".st-key-body-map-front [data-testid='stButton'] > div")
    assert "height:100%" in rule
    assert "display" not in rule


def test_every_hit_zone_is_clipped_to_its_own_shape():
    """A rectangle cannot describe a head: in Chromium the raised head's box took every click on
    the visible part of the ear beside it. Each zone is clipped to its piece's outline, in
    percentages of its own box (so it scales with the figure), and clipped-away area is not
    hit-tested. Every polygon vertex must lie within its box, give or take rounding."""
    boxes = body_map.region_hit_boxes("front")
    css = body_map.build_hit_zone_css("body-map-front", "front", set(boxes))
    for region_id, pieces in boxes.items():
        for i in range(len(pieces)):
            body = _rule_body(
                css, f".st-key-body-map-front .st-key-body-map-front__{region_id}__{i}"
            )
            poly = re.search(r"clip-path:polygon\(([^)]*)\)", body).group(1)
            pts = [tuple(float(v.rstrip("%")) for v in pair.split()) for pair in poly.split(",")]
            assert len(pts) >= 3, region_id
            assert all(-0.1 <= px <= 100.1 and -0.1 <= py <= 100.1 for px, py in pts), region_id


# ---------------------------------------------------------------------------
# The confidence channel does not survive being shrunk
# ---------------------------------------------------------------------------


def test_a_small_multiple_drops_the_per_region_dashes_entirely():
    """At ~140px a side the dashes stop being a line treatment and become texture.

    On a typical tool 19 of 22 regions fall below the reporting floor, so a row of small figures
    drawn with per-region dashes reads as four ghostly dashed skeletons, and the loudest thing
    in the section is once again the marking that means "we have no number for this". That is the
    amber-hatch inversion in a new medium. At small size the plate becomes a plain silhouette
    whose only variable is fill.
    """
    values = _full_values(value_fn=lambda rid: 0.3)
    values["wrist"] = RegionValue(
        label="Wrist", value=0.3, display_value="30.0%", state="limited", raw_n=25
    )
    values["ankle"] = RegionValue(
        label="Ankle", value=None, display_value="—", state="suppressed", raw_n=12
    )
    scale = BodyScale(vmin=0.0, vmax=0.5)
    small = ET.fromstring(
        build_body_svg(values, scale, "inst-a", "front", confidence_outlines=False)
    )
    assert not [e for e in small.iter() if e.get("stroke-dasharray")]
    # ...and every region is on ONE quiet hairline, so the figure still reads as a body.
    strokes = {e.get("stroke") for e in small.iter() if body_map._is_region_id(e.get("id"))}
    assert strokes == {theme.RULE}


def test_a_small_multiple_still_shows_which_regions_carry_a_value():
    """Dropping the dashes must not drop the DATA: ink density is untouched, so a region with a
    reportable estimate is still the only thing filled."""
    values = _full_values(value_fn=lambda rid: 0.3 if rid == "hand" else 0.0)
    values["ankle"] = RegionValue(
        label="Ankle", value=None, display_value="—", state="suppressed", raw_n=12
    )
    scale = BodyScale(vmin=0.0, vmax=0.3)
    root = ET.fromstring(
        build_body_svg(values, scale, "inst-a", "front", confidence_outlines=False)
    )
    hand = next(e for e in root.iter() if e.get("id") == "hand-front")
    ankle = next(e for e in root.iter() if e.get("id") == "ankle-front")
    assert hand.get("fill") == scale.color_for(0.3)
    assert ankle.get("fill") == theme.VELLUM


def test_a_small_multiple_keeps_every_regions_stability_in_its_tooltip():
    """Quiet is not hidden. The dash is gone from the small plate, so the per-region `<title>` is
    the only place a careful reader can still recover each region's own state. It must survive."""
    values = _full_values()
    values["ankle"] = RegionValue(
        label="Ankle", value=None, display_value="—", state="suppressed", raw_n=12
    )
    root = ET.fromstring(
        build_body_svg(values, BodyScale(0.0, 1.0), "inst-a", "front", confidence_outlines=False)
    )
    ankle = next(e for e in root.iter() if e.get("id") == "ankle-front")
    title = ankle.find(f"{_SVG_NS}title")
    assert title is not None
    from wii.app.components.stability_badge import SUPPRESSED_LABEL

    assert SUPPRESSED_LABEL in title.text


def test_the_full_size_figure_still_carries_the_dashes():
    """The flag is opt-in per call site: at 460px there IS room to read 22 outlines one at a
    time, and that is where "suppressed" versus "no cases recorded" does real work."""
    values = _full_values(value_fn=lambda rid: 0.3)
    values["wrist"] = RegionValue(
        label="Wrist", value=0.3, display_value="30.0%", state="limited", raw_n=25
    )
    root = ET.fromstring(build_body_svg(values, BodyScale(0.0, 0.5), "inst-a", "front"))
    assert [e for e in root.iter() if e.get("stroke-dasharray")]


def test_the_floor_note_states_the_count_without_naming_a_drawing_treatment():
    """The same sentence prints under a full-size figure (dashed unstable regions) and under a
    small multiple (no dashes at all), so naming the treatment would be false in one of them."""
    values = {
        "hand": RegionValue("Hand", 0.5, "50%", "stable", 80),
        "ankle": RegionValue("Ankle", None, "—", "suppressed", 9),
        "toe": RegionValue("Toe", None, "—", "suppressed", 4),
    }
    note = body_map.stability_floor_note(values, set(values))
    assert "2 of 3" in note and "carry no estimate" in note
    for treatment in ("dashed", "outline", "hatch"):
        assert treatment not in note


def test_a_row_of_figures_collapses_four_identical_callout_lists_into_one_line():
    """Four small figures each printed the same four-item list of em dashes beneath them, more
    repeated ink than the figures carried. The categories are still named, and the reader is
    still told they are excluded; it is said once for the row instead of once per tool."""
    suppressed = {
        callout_id: RegionValue(label, None, "—", "suppressed", 2)
        for callout_id, label in (
            ("internal", "Internal (use with aspiration and ingestion)"),
            ("burn_25_50", "25-50% of body"),
            ("all_body", "All parts of body"),
            ("not_recorded", "Not recorded"),
        )
    }
    note = body_map.callout_row_note([suppressed] * 4)
    assert "reporting floor" in note
    for label in ("Internal", "25-50% of body", "All parts of body", "Not recorded"):
        assert label in note
    # Named once, not once per figure.
    assert note.count("Not recorded") == 1


def test_the_row_note_defers_to_per_figure_chips_the_moment_one_carries_a_value():
    """The collapse is only honest while every callout is suppressed for every tool. As soon as
    one has a reportable value the chips differ between figures and are worth their space, so the
    component says nothing and leaves the page to draw them."""
    suppressed = {
        "internal": RegionValue("Internal", None, "—", "suppressed", 2),
        "not_recorded": RegionValue("Not recorded", None, "—", "suppressed", 0),
    }
    with_value = dict(suppressed)
    with_value["internal"] = RegionValue("Internal", 0.02, "2.0%", "stable", 40)
    assert body_map.callout_row_note([suppressed, with_value]) == ""
    assert body_map.callout_row_note([{}]) == ""
