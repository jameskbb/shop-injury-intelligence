"""Synthetic body3d payload for tests, local previews and browser verification.

Numbers are invented. They exist only to exercise every encoding state (stable at several
emphasis levels, limited, suppressed, absent) and the hotspot drill-down.
"""

from __future__ import annotations

from typing import Any

from wii.app.components.body3d import REGION_IDS

# Region -> (value, state, raw_n). None value means no stable estimate to show.
_VALUES: dict[str, tuple[float | None, str, int]] = {
    "finger": (412_000.0, "stable", 9_800),
    "hand": (158_000.0, "stable", 3_700),
    "eye": (96_000.0, "stable", 2_300),
    "face": (61_000.0, "stable", 1_450),
    "lower_arm": (44_000.0, "stable", 1_020),
    "wrist": (23_000.0, "stable", 560),
    "head": (31_000.0, "stable", 740),
    "lower_trunk": (38_000.0, "stable", 900),
    "upper_trunk": (19_000.0, "stable", 470),
    "foot": (27_000.0, "stable", 640),
    "knee": (14_000.0, "stable", 330),
    "lower_leg": (16_000.0, "stable", 390),
    "upper_leg": (9_500.0, "stable", 230),
    "shoulder": (8_200.0, "stable", 190),
    "toe": (6_100.0, "limited", 41),
    "upper_arm": (5_400.0, "limited", 38),
    "elbow": (4_900.0, "limited", 33),
    "mouth": (None, "suppressed", 12),
    "ear": (None, "suppressed", 7),
    "ankle": (3_900.0, "limited", 29),
    "neck": (None, "suppressed", 9),
    "pubic_region": (None, "absent", 0),
}

# The figure's own ramp: `theme.SEQUENTIAL_SCALE` without its floor stop, so a region's "zero"
# is a visible construction grey rather than the sheet colour (see body3d_payload.FIGURE_COLORS).
_RAMP = ["#A8C2CE", "#6D9BB4", "#3A7192", "#134A66"]
#: Bare vellum: a suppressed or absent region takes NO fill and is told apart by its contour.
_NO_FILL = "#E8EAE5"
#: A limited estimate keeps its fill, one step lighter.
_LIMITED = "#B7CBD4"


def _label(rid: str) -> str:
    return rid.replace("_", " ").capitalize()


def fixture_payload(**overrides: Any) -> dict[str, Any]:
    top = max(v for v, _, _ in _VALUES.values() if v is not None)
    regions: dict[str, dict[str, Any]] = {}
    for rid in REGION_IDS:
        value, state, raw_n = _VALUES[rid]
        emphasis = (value / top) if (value is not None and state == "stable") else 0.0
        if state == "stable":
            color = _RAMP[min(len(_RAMP) - 1, int(emphasis * (len(_RAMP) - 1) + 0.5))]
        elif state == "limited":
            color = _LIMITED
        else:
            color = _NO_FILL
        if state == "stable":
            display = f"{value:,.0f}"
            tip = f"{_label(rid)}: {display} estimated U.S. ER-treated injuries, 2006-2025"
        elif state == "limited":
            display = f"{value:,.0f}"
            tip = f"{_label(rid)}: {display} (Limited sample, n={raw_n})"
        elif state == "suppressed":
            display = "Suppressed"
            tip = f"{_label(rid)}: Estimate suppressed (n={raw_n})"
        else:
            display = "No cases"
            tip = f"{_label(rid)}: no cases in the current filters"
        regions[rid] = {
            "label": _label(rid),
            "value": value,
            "display": display,
            "state": state,
            "color": color,
            "emphasis": round(emphasis, 4),
            "tooltip": tip,
            "raw_n": raw_n,
        }
    payload: dict[str, Any] = {
        "regions": regions,
        "hotspots": [
            {
                "tool_id": "table_saw",
                "tool_label": "Table saw",
                "regions": ["finger", "hand", "wrist"],
            },
            {
                "tool_id": "grinder",
                "tool_label": "Angle grinder",
                "regions": ["eye", "face", "lower_arm"],
            },
            {
                "tool_id": "ladder",
                "tool_label": "Ladder",
                "regions": ["lower_trunk", "knee", "ankle"],
            },
        ],
        "active_tool": None,
        "selected": None,
        "measure_label": "Estimated U.S. ER-treated injuries, 2006–2025",
        "basis_label": "Estimated U.S. ER-treated injuries: annual average, 2006–2025",
        "reduced_motion": False,
    }
    payload.update(overrides)
    return payload
