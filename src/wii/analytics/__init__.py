"""wii.analytics: the deterministic insight engine (D-13, INS-01).

Pure Python over metric-layer OUTPUT FRAMES only -- no streamlit import, no wii.narratives
import, no SQL (enforced by tests/app/test_app_boundaries.py and a dedicated grep in
tests/analytics/test_insights.py). Callers (src/wii/app/data.py) are responsible for resolving
raw wii.metrics rows into this module's plain `ToolCell`/`DimensionCategory` shapes via
stability_badge.resolve/resolve_share BEFORE handing them to `generate_insights`/`compare_tools` --
this module never computes suppression itself.
"""

from __future__ import annotations
