"""Tests for the 3D body map component wrapper (wii.app.components.body3d).

WebGL cannot run under AppTest, so these cover the Python side of the contract: payload
validation, result mapping, registration/mounting without error, and static guards on the
frontend source for the honesty rules (one shared finger value, pinned Three.js).
"""

from __future__ import annotations

import copy
import re
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from wii.app.components import body3d as b3d
from wii.app.components.body3d import (
    REGION_IDS,
    Body3DPayloadError,
    interpret_result,
    validate_payload,
)
from wii.app.components.body3d.fixture import fixture_payload

FRONTEND = Path(b3d.__file__).parent / "frontend"


# --- payload validation --------------------------------------------------------------------


def test_fixture_is_valid_and_covers_every_state():
    payload = fixture_payload()
    assert validate_payload(payload) is payload
    assert set(payload["regions"]) == set(REGION_IDS)
    assert len(REGION_IDS) == 22
    states = {r["state"] for r in payload["regions"].values()}
    assert states == {"stable", "limited", "suppressed", "absent"}


def test_missing_region_rejected():
    payload = fixture_payload()
    del payload["regions"]["toe"]
    with pytest.raises(Body3DPayloadError, match="toe"):
        validate_payload(payload)


def test_per_finger_region_ids_rejected():
    """NEISS has one finger category; a payload inventing per-finger regions is refused."""
    payload = fixture_payload()
    payload["regions"]["index_finger"] = copy.deepcopy(payload["regions"]["finger"])
    with pytest.raises(Body3DPayloadError, match="index_finger"):
        validate_payload(payload)


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("state", "shaky", "unknown state"),
        ("color", "blue", "#rrggbb"),
        ("color", "#12345", "#rrggbb"),
        ("emphasis", 1.5, "0..1"),
        ("emphasis", -0.1, "0..1"),
        ("raw_n", -1, "raw_n"),
        ("raw_n", 3.5, "wrong type"),
        ("tooltip", None, "wrong type"),
        ("value", "12", "number or None"),
        ("value", True, "number or None"),
    ],
)
def test_bad_region_fields_rejected(field, value, match):
    payload = fixture_payload()
    payload["regions"]["hand"][field] = value
    with pytest.raises(Body3DPayloadError, match=match):
        validate_payload(payload)


def test_missing_region_field_rejected():
    payload = fixture_payload()
    del payload["regions"]["hand"]["tooltip"]
    with pytest.raises(Body3DPayloadError, match="tooltip"):
        validate_payload(payload)


def test_none_value_allowed_for_suppressed():
    payload = fixture_payload()
    assert payload["regions"]["neck"]["value"] is None
    validate_payload(payload)


@pytest.mark.parametrize(
    ("override", "match"),
    [
        ({"selected": "pinky"}, "not a region id"),
        ({"active_tool": 3}, "active_tool"),
        ({"reduced_motion": "yes"}, "reduced_motion"),
        ({"measure_label": 5}, "measure_label"),
        ({"hotspots": {"a": 1}}, "hotspots"),
        ({"hotspots": [{"tool_id": "x", "tool_label": "X", "regions": ["thumb"]}]}, "unknown"),
        ({"hotspots": [{"tool_id": "x", "regions": []}]}, "tool_label"),
    ],
)
def test_bad_top_level_fields_rejected(override, match):
    with pytest.raises(Body3DPayloadError, match=match):
        validate_payload(fixture_payload(**override))


def test_non_dict_payload_rejected():
    with pytest.raises(Body3DPayloadError):
        validate_payload([])  # type: ignore[arg-type]
    with pytest.raises(Body3DPayloadError):
        validate_payload({"regions": []})


# --- result mapping ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ({}, None),
        ({"selected": None}, None),
        ({"selected": "finger"}, {"selected": "finger"}),
        ({"selected": "not_a_region"}, None),
        ({"webgl": False}, {"selected": None, "webgl": False}),
        ({"webgl": False, "selected": "hand"}, {"selected": None, "webgl": False}),
        ({"webgl": True, "selected": "knee"}, {"selected": "knee"}),
    ],
)
def test_interpret_result(raw, expected):
    assert interpret_result(raw) == expected


def test_interpret_result_reads_attribute_style_results():
    class Result:
        selected = "eye"
        webgl = None

    assert interpret_result(Result()) == {"selected": "eye"}


# --- mounting under AppTest ----------------------------------------------------------------


def _mount_script():
    import streamlit as st

    from wii.app.components.body3d import body3d
    from wii.app.components.body3d.fixture import fixture_payload

    out = body3d(fixture_payload(active_tool="grinder"), key="body3d")
    st.write(f"result={out!r}")


def test_component_mounts_without_error_and_returns_none_before_any_click():
    at = AppTest.from_function(_mount_script, default_timeout=30)
    at.run()
    assert not at.exception
    assert any("result=None" in m.value for m in at.markdown)


def test_invalid_payload_surfaces_as_an_app_exception():
    def script():
        from wii.app.components.body3d import body3d

        body3d({"regions": {}}, key="bad")

    at = AppTest.from_function(script, default_timeout=30)
    at.run()
    assert at.exception
    assert "regions missing" in at.exception[0].value


def test_body3d_requires_keyword_key():
    with pytest.raises(TypeError):
        b3d.body3d(fixture_payload(), "body3d")  # type: ignore[misc]


# --- frontend source guards ----------------------------------------------------------------


def _js() -> str:
    return (FRONTEND / "body3d.js").read_text(encoding="utf-8")


def test_frontend_assets_present():
    for name in ("body3d.js", "body3d.css", "body3d.html"):
        assert (FRONTEND / name).is_file(), name


def test_every_contract_region_is_modelled_in_3d():
    js = _js()
    for rid in REGION_IDS:
        assert f'"{rid}"' in js, f"region {rid} has no mesh in body3d.js"


def test_fingers_share_one_region_and_no_per_finger_ids():
    """The five modelled digits must all use the single NEISS 'finger' region."""
    js = _js()
    figure = js[js.index("const HAND_PARTS") : js.index("function makeRegionMaterial")]
    parts = re.search(r"const HAND_PARTS = \[([^\]]+)\];", js)
    assert parts, "the hand surface must name its parts' regions in one list"
    hand_regions = re.findall(r'"([a-z_]+)"', parts.group(1))
    assert hand_regions[:2] == ["wrist", "hand"]
    assert hand_regions[2:] == ["finger"] * 5
    # Every region literal in the figure builder is a contract region id.
    literals = set(re.findall(r'add\(\s*"([a-z_]+)"', figure)) | set(hand_regions)
    assert literals <= set(REGION_IDS), literals - set(REGION_IDS)
    for bogus in ("thumb", "index", "middle_finger", "ring_finger", "pinky", "little_finger"):
        assert f'"{bogus}"' not in js
    # Every digit part is built from the one list, so no digit can take its own material.
    assert "addPiece(piece, HAND_PARTS[g]" in figure


def test_finger_caption_matches_python_constant():
    html = (FRONTEND / "body3d.html").read_text(encoding="utf-8")
    assert " ".join(b3d.FINGER_NOTE.split()) in " ".join(html.split())


def test_three_is_pinned_to_an_exact_version_on_an_allowed_cdn():
    js = _js()
    urls = re.findall(r'"(https://[^"]+)"', js)
    assert urls, "expected pinned Three.js URLs"
    for url in urls:
        assert url.startswith("https://cdn.jsdelivr.net/npm/three@0.170.0/"), url


def _code(js: str) -> str:
    """`js` with line comments stripped, for assertions about what the code DOES -- the prose
    above a function legitimately names the things the code no longer contains."""
    return "\n".join(line.split("//")[0] for line in js.splitlines())


def _fn(js: str, name: str) -> str:
    """Source of one top-level JS function (up to the next top-level `function`)."""
    start = js.index(f"function {name}(")
    nxt = js.find("\nfunction ", start + 1)
    return js[start : nxt if nxt != -1 else len(js)]


def test_no_tool_means_whole_body_not_the_first_hotspot():
    """With "All workshop tools" the intro must not drill into hotspots[0]'s tool."""
    apply = _fn(_js(), "applyPayload")
    assert "hotspots[0]" not in apply
    assert "payload.active_tool || null" in apply


# --- the drafted plate: light ground, no hatch, no sculpted face, no joint sleeves -----------


def test_the_figure_is_drawn_on_vellum_with_graphite_ink_only():
    """The scene's palette is the shop-drawing token set. Anything darker than the ink or
    anything warm would mean a dark-theme leftover or a retired amber."""
    js = _js()
    consts = dict(re.findall(r"^const ([A-Z_]+) = (0x[0-9a-f]{6});", js, re.M))
    assert consts["VELLUM"] == "0xe8eae5"
    assert consts["GRAPHITE"] == "0x22272a"
    assert consts["BLUEPRINT"] == "0x1c5b7d"
    # Graphite at theme.CONSTRUCTION (55%) and theme.RULE (22%), flattened onto vellum.
    assert consts["CONSTRUCTION"] == "0x7b7f7e"
    assert consts["RULE"] == "0xbcbfbc"
    assert "const PAPER = VELLUM;" in js
    # No amber (CAUTION #f2a93b), and no near-black ground.
    for retired in ("0xf2a93b", "0x0e1116", "0x12161c", "0x6e7b92"):
        assert retired not in js.lower(), retired
    # Every other colour literal in the file must be one of the named tokens.
    literals = set(re.findall(r"0x[0-9a-fA-F]{6}", js)) - set(consts.values())
    assert literals <= {"0xffffff", "0x000000"}, literals


def test_the_hatch_is_gone_entirely():
    """XC-03: the world-space stripe field helixed on every cylinder, so limbs read as
    candy-cane bandaging. Nothing may bring it back -- confidence is line treatment now."""
    js = _js()
    code = _code(js)
    for token in (
        "HATCH",
        "uHatch",
        "hatchMix",
        "hatchFreq",
        "HATCH_FAR_BOOST",
        "HATCH_LINE",
        "uHatchColor",
        "fract((vWPos",
    ):
        assert token not in code, token
    assert "CAUTION" not in code


def test_confidence_is_carried_by_line_treatment_only():
    """Three treatments, three meanings, and NO line on a stable region.

    Outlining all 22 regions made the figure a paint-by-numbers grid -- the same "visible seams"
    the deleted joint sleeves used to produce -- and, because most regions fall below the floor
    with no tool selected, it also made the dashed construction line the loudest thing on the
    plate: the original P0 in a new medium. A stable region's fill is what says "region", so it
    gets no contour; a limited or suppressed one gets the dashed line, which is then genuinely
    exceptional; and an absent one keeps the faintest SOLID hairline, because "nothing recorded"
    is settled and must not look provisional.
    """
    js = _js()
    style = _fn(js, "styleRegion")
    assert "lm.userData.baseColor.setHex(provisional ? CONSTRUCTION : RULE)" in style
    assert 'const provisional = state === "limited" || state === "suppressed";' in style
    assert "lu.uDash.value = provisional ? 1 : 0;" in style
    assert 'lm.visible = state !== "stable";' in style
    assert "lm.userData.baseOpacity = provisional ? 0.95 : 0.6;" in style
    # A suppressed or absent region is forced to bare sheet whatever the payload said.
    assert "m.color.setHex(PAPER)" in style
    # The dash is a screen-consistent break along the contour's own arc length, not a colour.
    line = _fn(js, "makeLineMaterial")
    assert "uDash" in line and "fract(vArc * uDashScale) > 0.58) discard" in line
    assert "aArc" in line


def test_every_region_has_a_contour_available_even_though_stable_hides_it():
    """The ribbon is built for every region -- a region that is stable under one filter is
    provisional under the next -- and `styleRegion` decides whether it is drawn."""
    js = _js()
    fig = _fn(js, "buildFigure")
    assert "contourGeometry(THREE, pos, nrm, idx)" in fig
    assert "lineFor(region)" in fig
    contour = _fn(js, "contourGeometry")
    # Boundary edges are welded and chained into loops so the dash runs ALONG the line.
    assert "loops.push(loop)" in contour
    assert "arc" in contour


def test_the_head_is_a_smooth_ellipsoid_with_flush_patches():
    """BODY-01: no relief(), no eye/mouth facePatch, no ear sockets. `eye` is one shallow visor
    band, `mouth` a lower-front band and `ear` a flush oval -- labels on a smooth surface, never
    modelled anatomy."""
    js = _js()
    code = _code(js)
    for gone in ("function relief(", "facePatch(", "onHead(", "gauss(", "rimGeometry("):
        assert gone not in code, gone
    for word in ("socket", "eyelid", "nostril", "lipgeo"):
        assert word not in code.lower(), word
    field = _fn(js, "buildBodyField")
    assert 'add("eye", 0, boxEye)' in field
    assert 'add("mouth", 0, boxMouth)' in field
    assert 'add("ear", sx,' in field
    # HR up, neck radius up ~15% on the old 0.05/0.048/0.045 stem.
    assert float(re.search(r"const HR = ([\d.]+);", js).group(1)) == pytest.approx(0.115)
    neck = re.search(r"solid\(\[0, 1\.35, -0\.004\], \[0, 1\.5, 0\.0\], ([\d.]+), ([\d.]+)", field)
    assert float(neck.group(1)) >= 0.05 * 1.14


def test_the_body_is_one_continuous_surface_with_no_joint_sleeves():
    """BODY-01: elbow/knee sleeves, the shoulder spheres and the seam feathering are gone. Trunk,
    head and limbs are one SDF meshed with surface nets and cut into regions, exactly as the hand
    already was, so a joint is a blend and a region is a named stretch of one surface."""
    js = _js()
    code = _code(js)
    for gone in (
        "JOINTS",
        "JOINT_FEATHER",
        "JOINT_CAP",
        "uFeather",
        "limbGeo",
        "bumpGeo",
        "shadeSeams",
        "LatheGeometry",
        "SphereGeometry",
    ):
        assert gone not in code, gone
    fig = _fn(js, "buildFigure")
    assert "surfaceNets(body.sdf" in fig
    assert "splitByPart(bodySurf, body.parts" in fig
    field = _fn(js, "buildBodyField")
    # shoulder / elbow / knee / ankle are LABEL bands on the limb axis, not solids of their own.
    for joint in ("shoulder", "elbow", "knee", "ankle"):
        assert f'add("{joint}", sx, axisBand(' in field, joint


def test_the_depth_channel_is_real_and_capped():
    """BODY-01 / README: the catalog claims colour AND depth. `emphasis` now displaces a stable
    region along its own normals, capped, and faded to nothing at the seam so nothing tears."""
    js = _js()
    cap = float(re.search(r"const RELIEF_MAX = ([\d.]+);", js).group(1))
    assert 0 < cap <= 0.03
    style = _fn(js, "styleRegion")
    assert 'state === "stable" ? RELIEF_MAX * emphasis : 0' in style
    mat = _fn(js, "makeRegionMaterial")
    assert "transformed += normalize(objectNormal) * (uRelief * aRelief)" in mat
    # The per-vertex weight is zero at a region seam (a cut point is ON the boundary).
    split = _fn(js, "splitByPart")
    assert "w: 0" in split
    assert "RELIEF_SPAN" in split


def test_the_lighting_is_flat_and_matte():
    """A drawing is lit flat: no environment map, no clearcoat, no sheen, no rim light, no
    specular highlight -- a glossy highlight is what made the old figure read as a mannequin."""
    js = _js()
    code = _code(js)
    for gone in (
        "RoomEnvironment",
        "PMREMGenerator",
        "clearcoat",
        "sheen",
        "MeshPhysicalMaterial",
        "uRimColor",
        "RIM_STRENGTH",
        "scene.environment",
    ):
        assert gone not in code, gone
    assert "MeshLambertMaterial" in js
    assert "NoToneMapping" in js
    # Form comes from an ink contour at the silhouette, not a light one.
    assert "smoothstep(0.55, 0.99, wiiF) * uEdge" in _fn(js, "makeRegionMaterial")
    # The ground is a contact occlusion only: no plinth, no grid floor, no bevel.
    create = _fn(js, "createInstance")
    assert "ShadowMaterial" in create
    assert "RingGeometry" not in code and "GridHelper" not in code


# --- motion: the one orchestrated moment ------------------------------------------------------


def test_motion_grammar_durations_and_easing():
    js = _js()
    ms = {k: float(v) for k, v in re.findall(r"^const ([A-Z_]+_MS) = (\d+);", js, re.M)}
    assert ms["DIM_MS"] == 250
    assert ms["HOLD_MS"] == 600
    assert ms["FLIGHT_MS"] == 1050
    assert ms["ZOOMED_FLIGHT_MS"] == 900
    assert ms["RETURN_MS"] == 700
    assert ms["PULSE_MS"] == 600
    assert ms["MAX_FLIGHT_MS"] == 1300
    assert ms["MAX_SEQUENCE_MS"] == 2600
    # The whole select-a-tool sequence fits inside the cap.
    assert ms["DIM_MS"] + ms["HOLD_MS"] + ms["FLIGHT_MS"] + ms["PULSE_MS"] <= ms["MAX_SEQUENCE_MS"]
    # Two curves: depart on ease-in-out-cubic, settle the last fifth on an ease-out-quint.
    assert "easeInOutCubic" in js and "easeOutQuint" in js
    settle = _fn(js, "flightEase")
    assert "easeOutQuint((t - SETTLE_AT)" in settle
    assert float(re.search(r"const SETTLE_AT = ([\d.]+);", js).group(1)) == pytest.approx(0.8)
    # Every flight is clamped, so no single move can become a tour.
    assert "Math.min(Math.max(0, ms || 0), MAX_FLIGHT_MS)" in _fn(js, "flyTo")


def test_orbit_and_dolly_run_on_separate_timelines():
    """The figure turns first and the camera then leans in, which is what makes a flight read as
    turning to look rather than a straight dive through the trunk."""
    js = _js()
    fly = _fn(js, "flyTo")
    assert "DOLLY_LAG_MS" in fly
    assert "Spherical()" in fly
    tick = _fn(js, "tick")
    assert "tw.lag" in tick
    assert "s.radius = tw.a.radius + (tw.b.radius - tw.a.radius) * kr" in tick


def test_tool_selection_dims_before_it_moves_and_pulses_only_the_first_hotspot():
    js = _js()
    drill = _fn(js, "drill")
    assert "startDim(inst, 1, DIM_MS)" in drill
    assert "later(inst, DIM_MS + HOLD_MS" in drill
    assert "FLIGHT_MS, { split: true, then: pulse }" in drill
    assert "startFlash(inst, [first], PULSE_MS)" in drill  # ONE region, not all three
    # Already zoomed in: cross-dim in place, one shorter flight, no establishing shot.
    assert "ZOOMED_FLIGHT_MS, { split: true, then: pulse }" in drill
    assert "alreadyFramed" in drill
    # The pulse is one cycle, not a repeating flash.
    assert "0.5 * (1 - Math.cos(k * Math.PI * 2))" in _fn(js, "tick")


def test_a_second_payload_cancels_the_flight_instead_of_queueing_it():
    js = _js()
    cancel = _fn(js, "cancelSequence")
    assert "clearTimeout(t)" in cancel
    assert "inst.tween = null" in cancel
    for caller in ("drill", "selectRegion"):
        assert "cancelSequence(inst)" in _fn(js, caller), caller


def test_clearing_returns_and_restores_the_tool_focus_at_the_end_of_the_flight():
    sel = _fn(_js(), "selectRegion")
    assert "startDim(inst, 0, RETURN_MS)" in sel
    assert "bodyView(), RETURN_MS" in sel
    assert "inst.focusRegions = new Set(spot.regions.slice(0, 3));" in sel


def test_no_establishing_shot_when_a_question_has_already_been_asked():
    apply = _fn(_js(), "applyPayload")
    assert "if (dest || spot)" in apply
    wide = apply[apply.index("if (dest || spot)") :]
    assert "WIDE_OFFSET" not in wide.split("} else {")[0]
    assert "WIDE_OFFSET" in apply  # still used for the no-tool, no-selection first load


def test_the_view_buttons_do_not_change_the_dimming_and_hover_never_moves_the_camera():
    js = _js()
    bind = _fn(js, "bindDom")
    assert "VIEW_BTN_MS" in bind
    assert "startDim" not in bind and "startFlash" not in bind
    hover = _code(_fn(js, "setHover"))
    assert "flyTo" not in hover and "camera" not in hover


def test_reduced_motion_cuts_to_the_destination():
    js = _js()
    assert "prefersReducedMotion" in _fn(js, "flyTo")
    assert "prefersReducedMotion" in _fn(js, "startDim")
    assert "prefersReducedMotion" in _fn(js, "startFlash")
    drill = _fn(js, "drill")
    assert "flyTo(inst, target, 0)" in drill


def test_nothing_in_the_scene_moves_on_its_own():
    """The spec bans unnecessary animation: every frame must follow a gesture or new data."""
    js = _js()
    loop = js[js.index("const loop = (now)") : js.index("inst.dispose = () => {")]
    assert "if (inst.dirty)" in loop
    assert "autoRotate" not in _code(js)


# --- reading the plate -------------------------------------------------------------------------


def test_touch_peeks_before_it_selects_and_mirrors_the_reading_into_the_header():
    """BODY-04: there is no hover on a touch screen, so the first tap pins the tooltip and only
    the second tap filters the page."""
    js = _js()
    assert 'ev.pointerType === "touch"' in js
    create = _fn(js, "createInstance")
    assert "inst.peeked !== region" in create
    assert "inst.peeked = null;" in create
    label = _fn(js, "updateLabel")
    assert "out.textContent = String(info.tooltip" in label


def test_the_tooltip_carries_the_basis_and_is_formatted_in_python():
    js = _js()
    hover = _fn(js, "setHover")
    assert "inst.payload && inst.payload.basis_label" in hover
    # No number formatting in JS: every displayed figure comes from the payload as a string.
    assert "toLocaleString" not in js and "toFixed" not in js


def test_the_plate_has_an_empty_state():
    js = _js()
    apply = _fn(js, "applyPayload")
    assert 'regions[r].state === "suppressed"' in apply and 'regions[r].state === "absent"' in apply
    assert "inst.ui.empty.hidden = !nothingDrawn" in apply
    html = (FRONTEND / "body3d.html").read_text(encoding="utf-8")
    assert "b3d-empty" in html
    assert "within the NEISS reporting" in " ".join(html.split())


def test_the_key_is_page_html_and_is_never_hidden():
    """BODY-03: the in-canvas key was `display:none` under 640px, so a phone visitor never
    learned what the line treatments meant. It now lives on the page, under the figure."""
    import wii.app.pages.body_map as page

    css = (FRONTEND / "body3d.css").read_text(encoding="utf-8")
    assert "b3d-key" not in css
    assert "b3d-key" not in (FRONTEND / "body3d.html").read_text(encoding="utf-8")
    key = page.BODY3D_LEGEND_HTML
    assert "Fewer" in key and "more estimated injuries" in key
    assert "Limited sample" in key and "Suppressed" in key and "No recorded cases" in key
    # A stable region has no contour, so the key must not promise one.
    assert "Stable estimate" not in key
    assert "display:none" not in key
    for stop in page.theme.SEQUENTIAL_SCALE[1:]:
        assert stop in key


def test_the_mobile_canvas_is_shorter_and_the_reading_lines_sit_above_it():
    css = (FRONTEND / "body3d.css").read_text(encoding="utf-8")
    mobile = css[css.index("@media (max-width: 640px)") :]
    assert "height: 460px" in mobile
    assert "display: none" not in mobile
    html = (FRONTEND / "body3d.html").read_text(encoding="utf-8")
    # The hotspot/readout lines are siblings ABOVE the stage, never overlaying the head.
    assert html.index("b3d-head") < html.index("b3d-stage")
    assert "b3d-measure" not in html  # BODY-02: the measure line repeated the selectbox


def test_the_hand_view_frames_the_whole_hand_from_outboard():
    """The old Hand view looked from INBOARD at 0.42: the thigh sat at 0.31 -- nearer the camera
    than the hand -- so a faded limb cut across the frame full height while the hand itself
    overflowed it. From outboard the obstruction falls behind the hand and the whole hand fits."""
    js = _js()
    raw = re.search(r"const HAND_OFFSET = \[([^\]]+)\];", js).group(1)
    off = [float(v) for v in raw.split(",")]
    dist = float(re.search(r"const HAND_DIST = ([\d.]+);", js).group(1))
    # The figure's right hand is at -x, so an outboard camera has a NEGATIVE x offset.
    assert off[0] < 0, off
    assert dist >= 0.55
    view = _fn(js, "handView")
    assert "inst.handCenter.clone()" in view  # the whole hand, wrist included
    create = _fn(js, "createInstance")
    assert "HAND_REGIONS.has(m.userData.region) && m.userData.side < 0" in create
    assert "Math.max(HAND_DIST, 0.135 / halfW)" in _fn(js, "regionView")


def test_proportions_match_the_review():
    js = _js()
    assert float(re.search(r"const ARM_ABDUCT = ([\d.]+);", js).group(1)) == pytest.approx(0.06)
    field = _fn(js, "buildBodyField")
    # Forearm radii +10% on the old 0.039 at the elbow, but NOT at the wrist, where the hand's
    # own field must stay the wider of the two or the join shows as a ring.
    fore = re.search(r"solids\.push\(solid\(E, W, ([\d.]+), ([\d.]+),", field)
    assert float(fore.group(1)) >= 0.039 * 1.09
    assert float(fore.group(2)) == pytest.approx(0.026)


# --- unchanged guarantees ---------------------------------------------------------------------


def test_outline_hulls_are_created_lazily():
    js = _js()
    create = _fn(js, "createInstance")
    assert "m.add(hull)" not in create
    assert "ensureHull(" in _fn(js, "applyHighlights")


def test_keyboard_and_screen_reader_support():
    js = _js()
    keys = _fn(js, "handleKey")
    for key in ("ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", '"+"', '"-"', "Escape"):
        assert key in keys, key
    assert 'addEventListener("keydown"' in js
    assert '"aria-live", "polite"' in js
    assert "Selected: " in js


def test_hover_is_retested_when_a_camera_flight_lands():
    js = _js()
    fly = _fn(js, "flyTo")
    assert "setHover(inst, null)" in fly
    assert "inst.rehover = true" in fly
    assert "inst.rehover = true" in _fn(js, "tick")
    re_ = _fn(js, "rehover")
    assert "inst.pick(p)" in re_
    assert "setHover(inst, null)" in re_


def test_close_ups_back_off_to_fit_a_narrow_canvas():
    """Head/face/eye views (and every other close frame) back off with the canvas aspect, so a
    phone keeps the whole region in frame instead of clipping it at the edge."""
    view = _fn(_js(), "regionView")
    assert "inst.camera.aspect" in view
    assert "HEAD_FIT_RADIUS / halfW" in view
    for other in ("HAND_REGIONS.has(region)", "TRUNK_REGIONS.has(region)"):
        block = view[view.index(other) :]
        assert "/ halfW" in block[: block.index("} else") + 200]


def test_the_flat_map_fallback_survives():
    js = _js()
    assert 'root.querySelector(".b3d-fallback").hidden = false' in js
    for reason in b3d.FALLBACK_REASONS:
        assert f'"{reason}"' in js
    assert 'setStateValue("webgl", false)' in js
