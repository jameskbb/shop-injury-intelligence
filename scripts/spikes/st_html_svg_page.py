"""Wave-0 spike A1: does `st.html` preserve inline <style>/<pattern>/<title> SVG children, and
which Google-Fonts loading form (@import vs <link>) survives? Also tests the `st.markdown(...,
unsafe_allow_html=True)` alternative side by side, since D-12's body-map renderer needs SOME
surviving mechanism for hover-carrying SVG. Throwaway page driven headlessly by
`spike_st_html_svg.py` (scripts/spikes/spike_st_html_svg.py). Not a permanent app surface -- the
real body-map renderer is `src/wii/app/components/body_map.py`, built in a later phase.
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(layout="wide")


def _body_svg(prefix: str) -> str:
    """Two path regions, an instance-prefixed hatch pattern, and native <title> tooltips (one
    with an escaped ampersand + en dash to prove encoding survives). Rendered under two different
    prefixes (once via st.html, once via st.markdown) to prove two instances coexist in one DOM
    without pattern-id collision, and to compare which rendering mechanism preserves SVG content.
    """
    return f"""
<svg class="{prefix}-body" viewBox="0 0 100 60" xmlns="http://www.w3.org/2000/svg"
     style="max-width:100%">
  <style>.{prefix}-fill-test {{ fill: #5B8DEF; }}</style>
  <defs>
    <pattern id="{prefix}-hatch" width="4" height="4" patternTransform="rotate(45)"
             patternUnits="userSpaceOnUse">
      <line x1="0" y1="0" x2="0" y2="4" stroke="#F2A93B" stroke-width="2" />
    </pattern>
  </defs>
  <path id="{prefix}-hand-front" class="{prefix}-fill-test" d="M5 5 L40 5 L40 30 L5 30 Z">
    <title>Hand &amp; wrist – estimated burden</title>
  </path>
  <path id="{prefix}-finger-front" fill="url(#{prefix}-hatch)" d="M55 5 L95 5 L95 30 L55 30 Z">
    <title>Finger region</title>
  </path>
</svg>
"""


# Mechanism 1: st.html (STACK.md's assumed default -- verify it actually preserves SVG).
st.html(_body_svg("stHtml"))

# Mechanism 2: st.markdown(unsafe_allow_html=True) -- the fallback candidate if st.html strips
# SVG content. Both are rendered so the driver can compare which one actually survives.
st.markdown(_body_svg("stMarkdown"), unsafe_allow_html=True)

_FONT_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;600&display=swap');
.wii-font-probe { font-family: "IBM Plex Sans", sans-serif; }
</style>
<link rel="stylesheet"
      href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&display=swap">
"""

st.html(_FONT_CSS)
st.markdown(_FONT_CSS, unsafe_allow_html=True)
