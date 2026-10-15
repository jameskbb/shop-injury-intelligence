"""Interactive 3D body injury map, as an ``st.components.v2`` custom component.

Public API (the contract shared with the payload builder and the page integration)::

    from wii.app.components.body3d import body3d
    result = body3d(payload, key="body3d")

``body3d`` returns:

* ``{"selected": "<region_id>"}`` on the rerun right after the user clicks a region;
* ``{"selected": None, "webgl": False}`` on every rerun once the browser has reported that it
  cannot draw the 3D view (no WebGL, the renderer failed, or Three.js could not be loaded from
  the CDN). The page should then render the existing 2D body map instead. This is persistent
  component state, not a one-shot trigger, so the fallback stays in place across reruns. When
  the frontend has also reported why, the dict carries ``"reason"`` -- one of
  ``FALLBACK_REASONS`` (``"no-webgl"``, ``"renderer-failed"``, ``"three-load-failed"``) -- so the
  page can say what actually went wrong. The frontend must set its ``reason`` state before its
  ``webgl`` state, so the reason is already present on the rerun that reports the fallback;
* ``None`` otherwise.

Why ``st.components.v2``: it is bidirectional and needs no build step. It renders in the app's
own DOM (inside a shadow root for style isolation), not an iframe. The JS/CSS/HTML live in
``frontend/`` as real files and are passed inline, because v2's path-based assets require an
``asset_dir`` declared in an installed package's ``pyproject.toml``.

Three.js (pinned r170) is loaded by the frontend from ``cdn.jsdelivr.net`` with a dynamic
``import()``. v2 resends the inline JS on every rerun, so vendoring the ~690 KB Three.js build
would push it over the websocket on every filter change; the CDN copy is fetched once and then
served from the browser cache. If the CDN is unreachable the component reports ``webgl: False``
and the page falls back to 2D.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path
from typing import Any

REGION_IDS: tuple[str, ...] = (
    "head",
    "face",
    "eye",
    "mouth",
    "ear",
    "neck",
    "shoulder",
    "upper_arm",
    "elbow",
    "lower_arm",
    "wrist",
    "hand",
    "finger",
    "upper_trunk",
    "lower_trunk",
    "pubic_region",
    "upper_leg",
    "knee",
    "lower_leg",
    "ankle",
    "foot",
    "toe",
)
STATES: frozenset[str] = frozenset({"stable", "limited", "suppressed", "absent"})
FALLBACK_REASONS: frozenset[str] = frozenset({"no-webgl", "renderer-failed", "three-load-failed"})
FINGER_NOTE = "NEISS records fingers as a single category, so all fingers share one value."
#: Desktop height of the plate. The component is mounted with ``height="content"`` so the
#: frontend stylesheet owns it and can drop to 460px under 640px viewport width; this stays
#: as the documented desktop figure.
HEIGHT_PX = 620

_FRONTEND = Path(__file__).parent / "frontend"
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
_REGION_FIELDS: dict[str, type | tuple[type, ...]] = {
    "label": str,
    "display": str,
    "state": str,
    "color": str,
    "emphasis": (int, float),
    "tooltip": str,
    "raw_n": int,
}


class Body3DPayloadError(ValueError):
    """The payload does not match the body3d contract."""


def validate_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Check ``payload`` against the contract and return it unchanged.

    Raises ``Body3DPayloadError`` naming the first problem found. Validation is strict because
    the frontend trusts the payload: a wrong colour or a missing region would silently misdraw
    an estimate.
    """
    if not isinstance(payload, dict):
        raise Body3DPayloadError("payload must be a dict")
    regions = payload.get("regions")
    if not isinstance(regions, dict):
        raise Body3DPayloadError("payload['regions'] must be a dict keyed by region id")
    missing = [r for r in REGION_IDS if r not in regions]
    unknown = sorted(set(regions) - set(REGION_IDS))
    if missing:
        raise Body3DPayloadError(f"regions missing: {', '.join(missing)}")
    if unknown:
        raise Body3DPayloadError(f"unknown region ids: {', '.join(unknown)}")

    for rid, info in regions.items():
        if not isinstance(info, dict):
            raise Body3DPayloadError(f"region {rid!r} must be a dict")
        for field, typ in _REGION_FIELDS.items():
            if field not in info:
                raise Body3DPayloadError(f"region {rid!r} is missing {field!r}")
            val = info[field]
            if isinstance(val, bool) or not isinstance(val, typ):
                raise Body3DPayloadError(f"region {rid!r} field {field!r} has the wrong type")
        value = info.get("value")
        if value is not None and (isinstance(value, bool) or not isinstance(value, int | float)):
            raise Body3DPayloadError(f"region {rid!r} value must be a number or None")
        if info["state"] not in STATES:
            raise Body3DPayloadError(f"region {rid!r} has unknown state {info['state']!r}")
        if not _HEX.match(info["color"]):
            raise Body3DPayloadError(f"region {rid!r} color must be #rrggbb")
        if not 0.0 <= float(info["emphasis"]) <= 1.0:
            raise Body3DPayloadError(f"region {rid!r} emphasis must be within 0..1")
        if info["raw_n"] < 0:
            raise Body3DPayloadError(f"region {rid!r} raw_n must be >= 0")

    hotspots = payload.get("hotspots", [])
    if not isinstance(hotspots, list):
        raise Body3DPayloadError("payload['hotspots'] must be a list")
    for spot in hotspots:
        if not isinstance(spot, dict):
            raise Body3DPayloadError("each hotspot must be a dict")
        if not isinstance(spot.get("tool_id"), str) or not isinstance(spot.get("tool_label"), str):
            raise Body3DPayloadError("hotspot needs string tool_id and tool_label")
        spot_regions = spot.get("regions")
        if not isinstance(spot_regions, list) or any(r not in REGION_IDS for r in spot_regions):
            raise Body3DPayloadError(f"hotspot {spot['tool_id']!r} has unknown region ids")

    active = payload.get("active_tool")
    if active is not None and not isinstance(active, str):
        raise Body3DPayloadError("active_tool must be a string or None")
    selected = payload.get("selected")
    if selected is not None and selected not in REGION_IDS:
        raise Body3DPayloadError(f"selected {selected!r} is not a region id")
    if not isinstance(payload.get("measure_label", ""), str):
        raise Body3DPayloadError("measure_label must be a string")
    if not isinstance(payload.get("basis_label", ""), str):
        raise Body3DPayloadError("basis_label must be a string")
    if not isinstance(payload.get("reduced_motion", False), bool):
        raise Body3DPayloadError("reduced_motion must be a bool")
    return payload


def _read(name: str) -> str:
    return (_FRONTEND / name).read_text(encoding="utf-8")


def _component():
    """Register once per component manager (i.e. per Streamlit runtime).

    Re-registering on every rerun logs duplicate-name warnings; caching per process would break
    when a new runtime (e.g. a fresh AppTest) comes up with an empty registry.
    """
    from streamlit.components.v2.get_bidi_component_manager import get_bidi_component_manager

    return _register(id(get_bidi_component_manager()))


@cache
def _register(_manager_id: int):
    from streamlit.components.v2 import component

    return component(
        "wii_body3d",
        html=_read("body3d.html"),
        css=_read("body3d.css"),
        js=_read("body3d.js"),
        isolate_styles=True,
    )


def _noop() -> None:
    """State/trigger callbacks must exist for v2 to track ``selected``, ``webgl``, ``reason``."""


def interpret_result(result: Any) -> dict[str, Any] | None:
    """Map the raw v2 ``ComponentResult`` onto the contract's return value."""
    if result is None:
        return None
    get = result.get if hasattr(result, "get") else (lambda k: getattr(result, k, None))
    if get("webgl") is False:
        out: dict[str, Any] = {"selected": None, "webgl": False}
        reason = get("reason")
        if isinstance(reason, str) and reason in FALLBACK_REASONS:
            out["reason"] = reason
        return out
    selected = get("selected")
    if isinstance(selected, str) and selected in REGION_IDS:
        return {"selected": selected}
    return None


def body3d(payload: dict[str, Any], *, key: str) -> dict[str, Any] | None:
    """Mount the 3D body map. See the module docstring for the return contract."""
    validate_payload(payload)
    result = _component()(
        key=key,
        data=payload,
        height="content",
        on_selected_change=_noop,
        on_webgl_change=_noop,
        on_reason_change=_noop,
    )
    return interpret_result(result)


__all__ = [
    "FALLBACK_REASONS",
    "FINGER_NOTE",
    "REGION_IDS",
    "STATES",
    "Body3DPayloadError",
    "body3d",
    "interpret_result",
    "validate_payload",
]
