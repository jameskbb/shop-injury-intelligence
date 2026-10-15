"""Wave-0 spike D-05: does clicking the PRODUCTION body-map renderer's region
`<a href="?region=...">` link inside `st.markdown(unsafe_allow_html=True)` navigate in the same
browser tab, keep `st.session_state`, and populate `st.query_params` in Streamlit 1.63? This page
renders the REAL `body_map.build_body_svg(..., link_regions=True)` output -- the verdict measured
against it applies to what ships, not a simplified stand-in. Driven headlessly by
`spike_st_query_params_click.py` (mirrors `scripts/spikes/spike_st_html_svg.py`'s harness).
Throwaway page -- not a permanent app surface.
"""

from __future__ import annotations

import uuid

import streamlit as st

from wii.app import theme
from wii.app.components.body_map import BodyScale, RegionValue, build_body_svg

st.set_page_config(layout="wide")
theme.inject()

# Seeded once per session -- if a click causes a full page reload that resets session_state, a
# BRAND-NEW session would also read this same starting value, so PROBE_TOKEN alone (unlike a
# counter starting from a fixed seed) unambiguously distinguishes "same session, second run" from
# "brand-new session": a fresh session mints a DIFFERENT random token.
st.session_state.setdefault("probe_token", uuid.uuid4().hex)
token = st.session_state["probe_token"]

region = st.query_params.get("region")

values = {
    "hand": RegionValue(label="Hand", value=0.5, display_value="50.0%", state="stable", raw_n=100),
    "finger": RegionValue(
        label="Finger", value=0.5, display_value="50.0%", state="stable", raw_n=100
    ),
}
scale = BodyScale(vmin=0.0, vmax=1.0)
svg = build_body_svg(values, scale, "spike", "front", selected_region_id=region, link_regions=True)
st.markdown(svg, unsafe_allow_html=True)

# Raw control anchor -- plain href, no target/xlink, no production code path -- for diagnostics
# only, to separate "any anchor click in st.markdown content" behavior from anything specific to
# build_body_svg's own wrapping.
st.markdown(
    '<svg id="control-front" width="10" height="10" xmlns="http://www.w3.org/2000/svg">'
    '<a href="?region=finger"><rect width="10" height="10" fill="#5B8DEF"/></a>'
    "</svg>",
    unsafe_allow_html=True,
)

st.write(f"PROBE_REGION={region!r} PROBE_TOKEN={token}")
