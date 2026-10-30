"""3D body-map payload: schema, states, emphasis companding, hotspots, and 2D/3D parity.

Pure-function tests use synthetic `RegionValue`s; parity tests run against the committed processed
data through the same cached `wii.app.data` helpers the 2D page uses.
"""

from __future__ import annotations

import json
import math

import pytest

import wii.app.pages.body_map as body_map_page
from wii.analytics.insights import scan_guardrail_vocabulary
from wii.app import body3d_payload as p3d
from wii.app import theme
from wii.app.components.body_map import BodyScale, RegionValue, _region_title_text
from wii.app.components.stability_badge import (
    LIMITED_SAMPLE_LABEL,
    STATE_LIMITED,
    STATE_STABLE,
    STATE_SUPPRESSED,
    SUPPRESSED_LABEL,
)
from wii.app.data import body_map_frame, load_dims, region_share_by_tool_frame
from wii.metrics import CaseFilter

_REGION_KEYS = {"label", "value", "display", "state", "color", "emphasis", "tooltip", "raw_n"}
_TOP_KEYS = {
    "regions",
    "hotspots",
    "active_tool",
    "selected",
    "measure_label",
    "basis_label",
    "reduced_motion",
}
_BASIS = "annual_average"


def _rv(label, value, state, raw_n, display=None):
    return RegionValue(
        label=label,
        value=value,
        display_value=display
        if display is not None
        else ("Not reportable" if value is None else f"{value}"),
        state=state,
        raw_n=raw_n,
    )


# Synthetic: finger dominates (~52%), plus one of every state.
_SYN_VALUES = {
    "finger": _rv("Finger", 5200.0, STATE_STABLE, 900),
    "hand": _rv("Hand", 1300.0, STATE_STABLE, 300),
    "eye": _rv("Eye", 325.0, STATE_STABLE, 80),
    "wrist": _rv("Wrist", 900.0, STATE_LIMITED, 25),
    "ankle": _rv("Ankle", None, STATE_SUPPRESSED, 7),  # suppressed WITH cases
    "toe": _rv("Toe", None, STATE_SUPPRESSED, 0),  # no cases -> absent
    "knee": _rv("Knee", 0.0, STATE_STABLE, 0),  # zero estimate -> absent
}
_SYN_IDS = [*_SYN_VALUES, "ear"]  # "ear" missing from values entirely -> absent
_SYN_LABELS = {rid: rid.title() for rid in _SYN_IDS}


def _syn_regions():
    scale = p3d.figure_scale(_SYN_VALUES, _SYN_IDS)
    return p3d.build_regions(_SYN_IDS, _SYN_LABELS, _SYN_VALUES, scale), scale


def _default_payload(**kwargs):
    return p3d.build_body3d_payload(CaseFilter(), _BASIS, "estimated", **kwargs)


# ---------------------------------------------------------------- pure / synthetic


def test_every_state_reachable_and_absent_vs_suppressed_distinguished():
    regions, _ = _syn_regions()
    states = {rid: r["state"] for rid, r in regions.items()}
    assert states == {
        "finger": "stable",
        "hand": "stable",
        "eye": "stable",
        "wrist": "limited",
        "ankle": "suppressed",
        "toe": "absent",
        "knee": "absent",
        "ear": "absent",
    }
    assert regions["ankle"]["raw_n"] == 7
    assert SUPPRESSED_LABEL in regions["ankle"]["tooltip"]
    assert LIMITED_SAMPLE_LABEL in regions["wrist"]["tooltip"]


def test_emphasis_zero_for_non_stable_and_one_for_max_stable():
    regions, _ = _syn_regions()
    for r in regions.values():
        if r["state"] != "stable":
            assert r["emphasis"] == 0.0
    assert regions["finger"]["emphasis"] == 1.0
    assert all(0.0 <= r["emphasis"] <= 1.0 for r in regions.values())


def test_emphasis_is_sqrt_companded():
    regions, _ = _syn_regions()
    # hand = 1/4 of finger -> sqrt = 0.5 (linear would be 0.25); eye = 1/16 -> 0.25.
    assert regions["hand"]["emphasis"] == pytest.approx(0.5)
    assert regions["eye"]["emphasis"] == pytest.approx(0.25)
    assert regions["hand"]["emphasis"] == pytest.approx(math.sqrt(1300.0 / 5200.0))


def test_limited_value_larger_than_max_stable_does_not_steal_emphasis():
    values = {
        "finger": _rv("Finger", 100.0, STATE_STABLE, 50),
        "hand": _rv("Hand", 400.0, STATE_LIMITED, 21),
    }
    scale = p3d.figure_scale(values, values)
    regions = p3d.build_regions(list(values), {}, values, scale)
    assert regions["finger"]["emphasis"] == 1.0
    assert regions["hand"]["emphasis"] == 0.0


def test_colors_follow_the_figure_ramp_and_never_fake_unstable_values():
    """INK DENSITY = MAGNITUDE on the figure's own ramp, which starts ONE STOP ABOVE the sheet.

    The flat map's floor stop is nearly the page colour; on a 3D figure that made the low end
    invisible and the two hands read as pale gloves in front of an unlit body (BODY-01). The
    figure therefore maps stable values across `SEQUENTIAL_SCALE[1:]`. Confidence is never a
    colour: a limited estimate keeps its own fill one step lighter, and a suppressed or absent
    region takes NO fill at all.
    """
    regions, scale = _syn_regions()
    assert scale == BodyScale(vmin=0.0, vmax=5200.0, colors=p3d.FIGURE_COLORS)
    assert tuple(p3d.FIGURE_COLORS) == tuple(theme.SEQUENTIAL_SCALE[1:])
    assert scale.colors[0] != theme.SEQUENTIAL_SCALE[0]
    assert regions["finger"]["color"] == scale.color_for(5200.0)
    assert regions["hand"]["color"] == scale.color_for(1300.0)
    # Limited: the value's own fill, lightened one step -- not a caution colour.
    assert regions["wrist"]["color"] == p3d._blend(
        scale.color_for(900.0), theme.SEQUENTIAL_SCALE[0], p3d.LIMITED_LIGHTEN
    )
    # Suppressed and absent: bare vellum. Amber is retired; nothing on the figure is CAUTION.
    for rid in ("ankle", "toe", "knee", "ear"):
        assert regions[rid]["color"] == p3d.NO_FILL_COLOR
    assert p3d.NO_FILL_COLOR == theme.VELLUM
    for r in regions.values():
        assert r["color"].startswith("#") and len(r["color"]) == 7
        assert r["color"].lower() != theme.CAUTION.lower()


def test_tooltip_reuses_d04_builder():
    regions, _ = _syn_regions()
    for rid, rv in _SYN_VALUES.items():
        assert regions[rid]["tooltip"] == _region_title_text(rv)


def test_hotspots_stable_only_ranked_and_never_padded():
    rows = [
        {"tool_id": "a", "region_id": "hand", "value": 0.30, "state": STATE_STABLE},
        {"tool_id": "a", "region_id": "finger", "value": 0.55, "state": STATE_STABLE},
        {"tool_id": "a", "region_id": "eye", "value": 0.90, "state": STATE_LIMITED},
        {"tool_id": "a", "region_id": "wrist", "value": None, "state": STATE_SUPPRESSED},
        {"tool_id": "a", "region_id": "internal", "value": 0.8, "state": STATE_STABLE},
        {"tool_id": "b", "region_id": "eye", "value": 0.4, "state": STATE_LIMITED},
        {"tool_id": "c", "region_id": "eye", "value": 0.2, "state": STATE_STABLE},
    ]
    hot = p3d.build_hotspots(
        ["c", "a", "b"], {"a": "Tool A"}, rows, ["finger", "hand", "eye", "wrist"]
    )
    assert hot == [
        {"tool_id": "c", "tool_label": "c", "regions": ["eye"]},
        {"tool_id": "a", "tool_label": "Tool A", "regions": ["finger", "hand"]},
    ]
    assert p3d.build_hotspots(["b"], {}, rows, ["eye"]) == []


def test_hotspots_cut_to_figure_stable_regions_and_tool_dropped_when_emptied():
    rows = [
        {"tool_id": "a", "region_id": "finger", "value": 0.55, "state": STATE_STABLE},
        {"tool_id": "a", "region_id": "hand", "value": 0.30, "state": STATE_STABLE},
        {"tool_id": "a", "region_id": "eye", "value": 0.10, "state": STATE_STABLE},
        {"tool_id": "b", "region_id": "wrist", "value": 0.60, "state": STATE_STABLE},
        {"tool_id": "b", "region_id": "hand", "value": 0.20, "state": STATE_STABLE},
    ]
    ids = ["finger", "hand", "eye", "wrist"]
    # The figure draws wrist and hand hatched: tool b has no figure-stable region left -> dropped.
    hot = p3d.build_hotspots(
        ["b", "a"], {}, rows, ids, region_limit=2, figure_stable=["finger", "eye"]
    )
    # a's top-2 is [finger, hand]; hand is cut, and eye (rank 3) is NOT back-filled.
    assert hot == [{"tool_id": "a", "tool_label": "a", "regions": ["finger"]}]
    assert p3d.build_hotspots(["b"], {}, rows, ids, figure_stable=["finger"]) == []


def test_measure_population_filter_mirrors_body_map_frame():
    flt = CaseFilter(years=(2024, 2024))
    assert p3d.measure_population_filter(flt, "estimated") == flt
    assert p3d.measure_population_filter(flt, "share") == flt
    for m in ("amputation", "laceration", "fracture"):
        assert p3d.measure_population_filter(flt, m) == body_map_page._measure_filter(flt, m)
    esc = p3d.measure_population_filter(flt, "escalated")
    disp = load_dims()["dispositions"]
    expected = {int(r["code"]) for r in disp.iter_rows(named=True) if r["escalated_care"]}
    assert esc.dispositions and set(esc.dispositions) == expected


def test_measures_match_page():
    assert p3d.MEASURES == body_map_page.MEASURES


# ---------------------------------------------------------------- real data (2D/3D parity)


def test_schema_shape_and_types():
    payload = _default_payload(selected="finger", reduced_motion=True, basis_label="Basis line")
    assert set(payload) == _TOP_KEYS
    assert payload["basis_label"] == "Basis line"
    assert payload["selected"] == "finger"
    assert payload["reduced_motion"] is True
    assert payload["measure_label"] == "Estimated injuries"
    for r in payload["regions"].values():
        assert set(r) == _REGION_KEYS
        assert type(r["label"]) is str and type(r["display"]) is str
        assert type(r["tooltip"]) is str and type(r["color"]) is str
        assert r["value"] is None or type(r["value"]) is float
        assert type(r["emphasis"]) is float and 0.0 <= r["emphasis"] <= 1.0
        assert type(r["raw_n"]) is int
        assert r["state"] in {"stable", "limited", "suppressed", "absent"}
    for h in payload["hotspots"]:
        assert set(h) == {"tool_id", "tool_label", "regions"}
        assert type(h["tool_id"]) is str and type(h["tool_label"]) is str
        assert h["regions"] and all(type(rid) is str for rid in h["regions"])


def test_all_atomic_regions_present_and_single_finger():
    payload = _default_payload()
    atomic = load_dims()["body_regions"]["region_id"].to_list()
    assert len(atomic) == 22
    assert list(payload["regions"]) == atomic
    assert [rid for rid in payload["regions"] if "finger" in rid] == ["finger"]
    for h in payload["hotspots"]:
        assert set(h["regions"]) <= set(atomic)


@pytest.mark.parametrize(
    ("flt", "measure"),
    [
        (CaseFilter(), "estimated"),
        (CaseFilter(), "fracture"),
        (CaseFilter(years=(2024, 2024), tools=("band_saw",)), "share"),
        (CaseFilter(years=(2024, 2024), tools=("band_saw",)), "amputation"),
        (CaseFilter(years=(2024, 2024), tools=("band_saw",)), "escalated"),
    ],
)
def test_values_match_2d_body_map_frame(flt, measure):
    payload = p3d.build_body3d_payload(flt, _BASIS, measure)
    values = body_map_frame(body_map_page._measure_filter(flt, measure), _BASIS, measure)
    atomic = load_dims()["body_regions"]["region_id"].to_list()
    scale = body_map_page._body_scale(values, set(atomic))
    assert p3d.body_scale(values, atomic) == scale
    for rid in atomic:
        rv, r = values[rid], payload["regions"][rid]
        assert r["value"] == rv.value
        assert r["display"] == rv.display_value
        assert r["raw_n"] == rv.raw_n
        assert r["tooltip"] == _region_title_text(rv)
        if r["state"] == "stable":
            # Same DOMAIN as the flat map (so the two figures can never disagree about a value),
            # carried on the figure's own ramp (so its low end is still visible ink).
            fig_scale = p3d.figure_scale(values, atomic)
            assert (fig_scale.vmin, fig_scale.vmax) == (scale.vmin, scale.vmax)
            assert r["color"] == fig_scale.color_for(rv.value)


def test_real_data_reaches_suppressed_with_cases_and_absent():
    payload = p3d.build_body3d_payload(
        CaseFilter(years=(2024, 2024), tools=("band_saw",)), _BASIS, "amputation"
    )
    states = {r["state"] for r in payload["regions"].values()}
    assert {"suppressed", "absent"} <= states
    for r in payload["regions"].values():
        if r["state"] == "suppressed":
            assert r["raw_n"] > 0
        if r["raw_n"] > 0:
            assert r["state"] != "absent" or r["value"] == 0


def test_real_data_emphasis_max_stable_is_one():
    regions = _default_payload()["regions"]
    stable = {rid: r for rid, r in regions.items() if r["state"] == "stable"}
    top = max(stable, key=lambda rid: stable[rid]["value"])
    assert stable[top]["emphasis"] == 1.0
    for r in stable.values():
        assert r["emphasis"] == pytest.approx(math.sqrt(r["value"] / stable[top]["value"]))
    assert all(r["emphasis"] == 0.0 for r in regions.values() if r["state"] != "stable")


def test_real_data_hotspots_stable_only_and_ranked():
    payload = _default_payload()
    hot = payload["hotspots"]
    assert 0 < len(hot) <= p3d.HOTSPOT_TOOL_LIMIT
    share = {
        (row["tool_id"], row["region_id"]): row
        for row in region_share_by_tool_frame(CaseFilter()).iter_rows(named=True)
    }
    for h in hot:
        vals = []
        for rid in h["regions"]:
            row = share[(h["tool_id"], rid)]
            assert row["state"] == STATE_STABLE
            vals.append(row["value"])
        assert vals == sorted(vals, reverse=True)
    assert len({h["tool_id"] for h in hot}) == len(hot)


@pytest.mark.parametrize(
    "flt",
    [CaseFilter(), CaseFilter(years=(2020, 2024)), CaseFilter(years=(2024, 2024))],
)
@pytest.mark.parametrize("measure", list(p3d.MEASURES))
def test_every_hotspot_region_is_stable_in_the_same_payload(flt, measure):
    payload = p3d.build_body3d_payload(flt, _BASIS, measure, active_tool="table_saw")
    for h in payload["hotspots"]:
        assert h["regions"]
        for rid in h["regions"]:
            assert payload["regions"][rid]["state"] == "stable", (measure, h["tool_id"], rid)


def test_hotspots_follow_the_selected_measure():
    flt = CaseFilter(years=(2020, 2024))
    estimated = p3d.build_body3d_payload(flt, _BASIS, "estimated")["hotspots"]
    amputation = p3d.build_body3d_payload(flt, _BASIS, "amputation")["hotspots"]
    assert estimated and amputation
    assert amputation != estimated
    # Amputation hotspots are ranked on the amputation population's own within-tool shares.
    amp_flt = p3d.measure_population_filter(flt, "amputation")
    share = {
        (row["tool_id"], row["region_id"]): row
        for row in region_share_by_tool_frame(amp_flt).iter_rows(named=True)
    }
    for h in amputation:
        vals = [share[(h["tool_id"], rid)]["value"] for rid in h["regions"]]
        assert all(share[(h["tool_id"], rid)]["state"] == STATE_STABLE for rid in h["regions"])
        assert vals == sorted(vals, reverse=True)


def test_active_tool_and_selected_validation():
    payload = _default_payload(selected="not_a_region", active_tool="not_a_tool")
    assert payload["selected"] is None and payload["active_tool"] is None
    payload = _default_payload(active_tool="table_saw")
    assert payload["active_tool"] == "table_saw"
    assert "table_saw" in {h["tool_id"] for h in payload["hotspots"]}


def test_unknown_measure_rejected():
    with pytest.raises(ValueError):
        p3d.build_body3d_payload(CaseFilter(), _BASIS, "per_use_risk")


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _strings(k)
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


@pytest.mark.parametrize("measure", list(p3d.MEASURES))
def test_guardrail_scan_and_json_round_trip(measure):
    payload = p3d.build_body3d_payload(
        CaseFilter(years=(2020, 2024)), _BASIS, measure, active_tool="table_saw"
    )
    for text in _strings(payload):
        assert scan_guardrail_vocabulary(text) == [], text
    assert json.loads(json.dumps(payload, allow_nan=False)) == payload
