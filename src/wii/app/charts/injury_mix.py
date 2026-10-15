"""INJURY TYPE MIX (Overview): within-selection shares of the primary diagnosis.

The vertical form is retired. The same "share by diagnosis" fact was drawn vertically here and
horizontally on four other pages, so a reader had to re-learn the chart per page (and the vertical
axis forced rotated, cramped category labels at 390px). This module is now a thin delegate to
`charts.profile_bars.build_share_bars` -- one horizontal builder, one look, the diagnosis ink
looked up by category name from `theme.DIAGNOSIS_COLORS`.
"""

from __future__ import annotations

import plotly.graph_objects as go
import polars as pl

from wii.app.charts.profile_bars import build_diagnosis_bars

SUBTITLE = (
    "Within-selection shares of the primary diagnosis; the same diagnosis keeps the same "
    "color on every page."
)

# Input frame columns (one row per palette category, built by wii.app.data.injury_mix_frame):
# category (one of theme.DIAGNOSIS_COLORS' 9 keys), label, value (share, 0-1, unrounded),
# ci_low, ci_high, raw_n, state ("stable" | "limited" | "suppressed"), optional baseline.


def build_injury_mix(df: pl.DataFrame) -> go.Figure:
    """Horizontal diagnosis-share bars. Suppressed categories are dropped (the page names them),
    limited ones carry the dashed construction outline, and every share is lettered past the end
    of its bar."""
    return build_diagnosis_bars(df)
