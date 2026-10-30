"""Design system: the "shop drawing" token set, the injected CSS block (fonts, rules, spacing,
measured-value grid, chrome hiding), the semantic diagnosis/mechanism/family palettes and the
registered Plotly template. This module is the ONLY place any color literal is allowed to live --
charts and components look colors up here by name, never via a rotating Plotly `colorway`.

The contract behind these tokens is `docs/design-system.md`. Two rules carry the product:

  * INK DENSITY = MAGNITUDE. More ink, more injuries. `SEQUENTIAL_SCALE` is one hue family
    travelling from nearly-bare vellum to saturated blueprint.
  * LINE TREATMENT = CONFIDENCE. Solid outline = stable; dashed construction outline = limited;
    dashed outline with NO fill = suppressed. Confidence is never color-coded and magnitude is
    never line-coded, so "large but provisional" is sayable without ambiguity.

Amber is retired. It previously meant "limited sample" and was drawn at 0.55-0.6 alpha, which made
the loudest element on four of nine pages the one meaning "we don't trust this". `CAUTION` is kept
as a name so nothing breaks, but it now resolves to REDLINE and is reserved for genuine
warnings -- never magnitude, never a category.

CSS is injected via `st.markdown(..., unsafe_allow_html=True)`, NOT `st.html` -- `st.html`
silently strips SVG content in Streamlit 1.63.0, and the SVG body map goes through this same
mechanism.
"""

from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

# ---------------------------------------------------------------------------
# Core tokens. Six named values; everything else is derived from them.
# ---------------------------------------------------------------------------

VELLUM = "#E8EAE5"  # the sheet: cool grey-green drafting film, deliberately NOT warm cream
VELLUM_DEEP = "#DADDD5"  # secondary surface: sidebar, table banding, expander bodies
GRAPHITE = "#22272A"  # primary ink: body text, linework
GRAPHITE_SOFT = "#6B736F"  # secondary ink: axis labels, captions, quiet data
BLUEPRINT = "#1C5B7D"  # the one data accent: drafting blue pencil
REDLINE = "#A83E27"  # correction ink: genuine cautions only

# Legacy names, kept so no call site breaks. New code should prefer the names above.
BACKGROUND = VELLUM
SURFACE = VELLUM_DEEP
TEXT = GRAPHITE
MUTED = GRAPHITE_SOFT
PRIMARY = BLUEPRINT
CAUTION = REDLINE  # amber is retired; this is annotation ink now, never a magnitude
SEVERE = "#7D2B3E"  # deep maroon, the most severe outcome; distinct from REDLINE
BORDER = "#C3C8C0"  # solid equivalent of RULE, for contexts that cannot take rgba

FONT_SANS = '"Archivo", -apple-system, "Segoe UI", sans-serif'
FONT_NARROW = '"Archivo Narrow", "Archivo", -apple-system, "Segoe UI", sans-serif'
# Measured values are set in Archivo Narrow, not a typewriter face: narrow gothic is what drafters
# lettered dimensions in, and a monospace face on small data labels is a generic-AI tell.
FONT_MONO = FONT_NARROW

_GOOGLE_FONTS_LINK = (
    "https://fonts.googleapis.com/css2"
    "?family=Archivo:wght@400;500;600;700"
    "&family=Archivo+Narrow:wght@400;500;600;700&display=swap"
)


def _rgba(hex_color: str, alpha: float) -> str:
    """A token at reduced opacity, so derived shades (rules, grid, hover wash) never introduce a
    new color literal -- they stay the named token they are derived from."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r}, {g}, {b}, {alpha})"


#: Hairline rules -- the primary separator now that nothing has a box around it.
RULE = _rgba(GRAPHITE, 0.22)
#: Faint graph-paper grid, behind drawing areas only.
GRID = _rgba(GRAPHITE, 0.10)
#: Dashed provisional geometry: the "limited sample" / "suppressed" outline.
#:
#: 0.55, not the 0.38 this started at. Once the hatch was retired, this dashed line became the SOLE
#: visual carrier of "limited" and "suppressed" on the body map and in every chart -- and at 0.38 it
#: composites to 2.18:1 against vellum, under WCAG 1.4.11's 3:1 for a meaningful graphical
#: boundary. At 0.55 it clears at 3.35:1. `RULE` and `GRID` stay where they are: they are decorative
#: separators carrying no meaning, which 1.4.11 exempts.
CONSTRUCTION = _rgba(GRAPHITE, 0.55)

#: Dash patterns for the confidence channel. SVG stroke-dasharray / Plotly dash strings.
DASH_LIMITED = "4 3"
DASH_SUPPRESSED = "2 3"

# Type scale: major third (1.25) on a 16px base. Archivo throughout; Archivo Narrow for measured
# numerics, axis labels and title-block metadata.
TYPE_MICRO = 12
TYPE_LABEL = 13
TYPE_BODY = 16
TYPE_SUBHEAD = 19
TYPE_HEADING = 24
TYPE_DISPLAY = 36
TYPE_METRIC = 34  # a measured value; the review asked for a hero figure at >=36px on Overview

# NOTE: no blank lines are allowed anywhere inside the <style> block below. Streamlit's markdown
# renderer follows CommonMark's raw-HTML-block rule: a <style> block is passed through verbatim
# only until the first blank line, after which the REST of the string is re-parsed as ordinary
# Markdown (curly braces and a stray closing tag leak through as visible text).
# `tests/app/test_theme.py` asserts this.
#
# Specificity: Streamlit styles its elements with single emotion classes (0,1,0). The rules below
# use `[data-testid]` + element selectors (>= 0,1,1) so they win WITHOUT `!important`. The one
# `!important` is on Plotly's SVG text, where Streamlit's chart theme writes `font-family` as an
# INLINE style that no selector can outrank.
_CSS = f"""
<link rel="stylesheet" href="{_GOOGLE_FONTS_LINK}">
<style>
:root {{
  --wii-vellum: {VELLUM};
  --wii-vellum-deep: {VELLUM_DEEP};
  --wii-graphite: {GRAPHITE};
  --wii-graphite-soft: {GRAPHITE_SOFT};
  --wii-blueprint: {BLUEPRINT};
  --wii-redline: {REDLINE};
  --wii-severe: {SEVERE};
  --wii-rule: {RULE};
  --wii-grid: {GRID};
  --wii-construction: {CONSTRUCTION};
  --wii-bg: {VELLUM};
  --wii-surface: {VELLUM_DEEP};
  --wii-border: {RULE};
  --wii-text: {GRAPHITE};
  --wii-muted: {GRAPHITE_SOFT};
  --wii-primary: {BLUEPRINT};
  --wii-caution: {REDLINE};
  --wii-font-sans: {FONT_SANS};
  --wii-font-narrow: {FONT_NARROW};
  --wii-font-mono: {FONT_NARROW};
  --wii-text-display: {TYPE_DISPLAY}px;
  --wii-text-heading: {TYPE_HEADING}px;
  --wii-text-subhead: {TYPE_SUBHEAD}px;
  --wii-text-body: {TYPE_BODY}px;
  --wii-text-label: {TYPE_LABEL}px;
  --wii-text-micro: {TYPE_MICRO}px;
  --wii-text-metric: {TYPE_METRIC}px;
  --wii-weight-regular: 400;
  --wii-weight-medium: 500;
  --wii-weight-semibold: 600;
  --wii-weight-bold: 700;
  --wii-space-xs: 4px;
  --wii-space-sm: 8px;
  --wii-space-md: 16px;
  --wii-space-lg: 24px;
  --wii-space-xl: 32px;
  --wii-space-2xl: 48px;
  --wii-space-3xl: 64px;
  /* A drawing has square corners and casts no shadow. */
  --wii-radius: 0px;
}}
html, body, [data-testid="stAppViewContainer"] {{
  background-color: var(--wii-vellum);
  color: var(--wii-graphite);
  font-family: var(--wii-font-sans);
  font-size: var(--wii-text-body);
  -webkit-font-smoothing: antialiased;
}}
[data-testid="stAppViewContainer"], [data-testid="stSidebar"],
[data-testid="stMarkdownContainer"], [data-testid="stMarkdownContainer"] p,
[data-testid="stMarkdownContainer"] li, [data-testid="stCaptionContainer"] p,
[data-testid="stWidgetLabel"] p, [data-testid="stSidebarNav"] span,
[data-testid="stExpander"] summary, [data-testid="stRadioOption"] p,
[data-testid="stMain"] h1, [data-testid="stMain"] h2, [data-testid="stMain"] h3,
[data-testid="stMain"] h4, h1 span, h2 span, h3 span, h4 span,
button, input, select, textarea, [role="option"], [role="combobox"],
[data-testid^="stMetric"], [data-testid="stDataFrame"],
[class^="wii-"], [class*=" wii-"] {{
  font-family: var(--wii-font-sans);
}}
[data-testid="stPlotlyChart"] text {{
  font-family: var(--wii-font-sans) !important;
}}
[data-testid="stSidebar"] {{
  background-color: var(--wii-vellum-deep);
  border-right: 1px solid var(--wii-rule);
}}
[data-testid="stToolbar"] {{ visibility: hidden; height: 0; }}
[data-testid="stDecoration"] {{ display: none; }}
body {{ overflow-x: hidden; }}
img, svg {{ max-width: 100%; height: auto; }}
/* Clear Streamlit's fixed 60px header: hosted deployments paint it opaque over the title. */
[data-testid="stMainBlockContainer"] {{
  padding-top: 80px;
  padding-bottom: var(--wii-space-3xl);
}}
[data-testid="stMain"] [data-testid="stVerticalBlock"] {{ gap: 12px; }}
/* Sheet title: the drawing's name, set large and tight against the datum edge. */
[data-testid="stMain"] h1 {{
  font-size: var(--wii-text-display);
  font-weight: var(--wii-weight-bold);
  letter-spacing: -0.02em;
  line-height: 1.08;
  color: var(--wii-graphite);
  padding: 0;
  margin: 0;
}}
[data-testid="stMain"] h2 {{
  font-size: var(--wii-text-heading);
  font-weight: var(--wii-weight-semibold);
  letter-spacing: -0.01em;
  line-height: 1.2;
  color: var(--wii-graphite);
  padding: 0;
  margin: 32px 0 0;
}}
[data-testid="stMain"] h3 {{
  font-size: var(--wii-text-subhead);
  font-weight: var(--wii-weight-semibold);
  line-height: 1.3;
  color: var(--wii-graphite);
  padding: 0;
  margin: 20px 0 0;
}}
[data-testid="stMain"] h4 {{
  font-size: var(--wii-text-body);
  font-weight: var(--wii-weight-semibold);
  padding: 0;
  margin: 8px 0 0;
}}
@media (max-width: 768px) {{
  [data-testid="stMainBlockContainer"] {{ padding-top: 64px; }}
  [data-testid="stMain"] h1 {{ font-size: 26px; }}
  :root {{ --wii-text-metric: 27px; }}
}}
[data-testid="stMain"] h1 span, [data-testid="stMain"] h2 span,
[data-testid="stMain"] h3 span, [data-testid="stMain"] h4 span {{
  font-size: inherit;
  font-weight: inherit;
  line-height: inherit;
}}
[data-testid="stHeading"] [data-testid="stMarkdownContainer"] {{ margin-bottom: 0; }}
[data-testid="stElementContainer"]:has(h1, h2, h3) + [data-testid="stElementContainer"]
  [data-testid="stCaptionContainer"] {{
  margin-top: -8px;
}}
[data-testid="stMain"] [data-testid="stMarkdownContainer"] > p,
[data-testid="stMain"] [data-testid="stMarkdownContainer"] > ul {{
  font-size: var(--wii-text-body);
  line-height: 1.55;
  max-width: 74ch;
}}
[data-testid="stCaptionContainer"] p {{
  font-size: var(--wii-text-label);
  line-height: 1.5;
  color: var(--wii-graphite-soft);
  max-width: 92ch;
  margin: 0;
  text-wrap: pretty;
}}
[data-testid="stWidgetLabel"] p {{
  font-size: var(--wii-text-label);
  font-weight: var(--wii-weight-medium);
}}
[data-testid="stPlotlyChart"] {{ margin-top: 4px; }}
:focus-visible {{ outline: 2px solid var(--wii-blueprint); outline-offset: 2px; }}
/* ---- Title block: what is drawn, on what basis, from which data. Once per page. ---- */
.wii-titleblock {{
  display: flex;
  flex-wrap: wrap;
  gap: 0;
  border-top: 2px solid var(--wii-graphite);
  border-bottom: 1px solid var(--wii-rule);
  margin: var(--wii-space-md) 0 var(--wii-space-lg);
}}
.wii-titleblock .wii-tb-field {{
  flex: 1 1 auto;
  min-width: 0;
  padding: 6px 14px 7px 0;
  margin-right: 14px;
  border-right: 1px solid var(--wii-rule);
}}
.wii-titleblock .wii-tb-field:last-child {{ border-right: none; margin-right: 0; }}
/* Phone: the four fields are flex items with a max-content basis, so they each take their own row
   and the strip costs ~184px -- enough to push a body figure off the first screen. A 2x2 grid
   halves that to ~92px on every page that carries the block. The value is allowed to WRAP here
   rather than keeping its desktop ellipsis: scope is a load-bearing fact on a product whose whole
   argument is stating scope honestly, so a taller row beats a truncated one. */
@media (max-width: 640px) {{
  .wii-titleblock {{ display: grid; grid-template-columns: 1fr 1fr; }}
  .wii-titleblock .wii-tb-field:nth-child(2n) {{ border-right: none; margin-right: 0; }}
  .wii-titleblock .wii-tb-value {{ white-space: normal; overflow: visible; text-overflow: clip; }}
}}
.wii-titleblock .wii-tb-key {{
  display: block;
  font-family: var(--wii-font-narrow);
  font-size: var(--wii-text-micro);
  font-weight: var(--wii-weight-semibold);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--wii-graphite-soft);
  line-height: 1.3;
}}
.wii-titleblock .wii-tb-value {{
  display: block;
  font-size: var(--wii-text-label);
  color: var(--wii-graphite);
  line-height: 1.35;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}
/* ---- Measured values. No card, no box: a datum rule and a dimension line. ---- */
.wii-kpi-strip {{
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0;
  margin: var(--wii-space-md) 0 var(--wii-space-sm);
  border-top: 1px solid var(--wii-rule);
}}
@media (min-width: 760px) {{
  .wii-kpi-strip {{
    grid-template-columns: repeat(var(--wii-kpi-cols-md, 3), minmax(0, 1fr));
  }}
}}
@media (min-width: 1200px) {{
  .wii-kpi-strip {{
    grid-template-columns: repeat(var(--wii-kpi-cols, 3), minmax(0, 1fr));
  }}
}}
.wii-kpi-card {{
  display: flex;
  flex-direction: column;
  min-width: 0;
  background: none;
  border: none;
  border-left: 1px solid var(--wii-rule);
  border-bottom: 1px solid var(--wii-rule);
  border-radius: 0;
  padding: 10px 16px 12px;
}}
.wii-kpi-card:first-child {{ border-left: none; padding-left: 0; }}
.wii-kpi-card .wii-kpi-label {{
  font-size: var(--wii-text-label);
  font-weight: var(--wii-weight-medium);
  line-height: 1.3;
  color: var(--wii-graphite-soft);
}}
.wii-kpi-card .wii-kpi-value {{
  font-family: var(--wii-font-narrow);
  font-size: var(--wii-text-metric);
  font-weight: var(--wii-weight-semibold);
  font-variant-numeric: tabular-nums lining-nums;
  letter-spacing: -0.015em;
  line-height: 1.05;
  color: var(--wii-graphite);
  margin-top: 6px;
}}
/* The hero measured value on a page's first screen. */
.wii-kpi-card.wii-kpi-hero .wii-kpi-value {{
  font-size: calc(var(--wii-text-metric) * 1.35);
  font-weight: var(--wii-weight-bold);
}}
.wii-kpi-card .wii-kpi-delta {{ margin-top: 3px; font-size: var(--wii-text-label); }}
.wii-kpi-card .wii-kpi-caption {{
  font-size: var(--wii-text-micro);
  line-height: 1.4;
  color: var(--wii-graphite-soft);
  margin-top: auto;
  padding-top: 8px;
  text-wrap: pretty;
}}
/* A dimension line under a measured value: |<-- span -->| */
.wii-dim {{
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 7px;
  font-family: var(--wii-font-narrow);
  font-size: var(--wii-text-micro);
  color: var(--wii-graphite-soft);
  line-height: 1;
}}
.wii-dim::before, .wii-dim::after {{
  content: "";
  flex: 1 1 auto;
  height: 1px;
  background: var(--wii-rule);
}}
.wii-dim .wii-dim-label {{ flex: 0 0 auto; white-space: nowrap; }}
/* ---- Confidence channel. Line treatment, never colour. ---- */
.wii-stable {{ color: var(--wii-graphite); }}
.wii-limited {{
  color: var(--wii-graphite-soft);
  border-bottom: 1px dashed var(--wii-construction);
}}
.wii-suppressed {{
  color: var(--wii-graphite-soft);
  font-style: italic;
}}
.wii-footer {{
  margin-top: var(--wii-space-3xl);
  padding-top: var(--wii-space-md);
  border-top: 1px solid var(--wii-rule);
  font-size: var(--wii-text-label);
  color: var(--wii-graphite-soft);
}}
/* ---- Content blocks: rule-separated, never boxed. ---- */
.wii-insight-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 0;
  margin: var(--wii-space-md) 0;
  border-top: 1px solid var(--wii-rule);
}}
.wii-insight-card {{
  background: none;
  border: none;
  border-bottom: 1px solid var(--wii-rule);
  border-left: 1px solid var(--wii-rule);
  border-radius: 0;
  padding: 12px 16px 14px;
}}
.wii-insight-card:first-child {{ border-left: none; padding-left: 0; }}
.wii-insight-card .wii-insight-text {{
  font-size: var(--wii-text-body);
  color: var(--wii-graphite);
  margin-bottom: var(--wii-space-sm);
  line-height: 1.45;
}}
.wii-insight-card .wii-insight-numbers {{
  display: flex;
  flex-wrap: wrap;
  gap: var(--wii-space-sm);
  margin-bottom: var(--wii-space-xs);
}}
.wii-insight-card .wii-insight-number {{
  font-size: var(--wii-text-label);
  color: var(--wii-graphite-soft);
}}
.wii-insight-card .wii-insight-number-value {{
  font-family: var(--wii-font-narrow);
  font-variant-numeric: tabular-nums lining-nums;
  color: var(--wii-graphite);
}}
.wii-insight-card .wii-insight-raw-n {{
  font-size: var(--wii-text-label);
  color: var(--wii-graphite-soft);
}}
.wii-body-map {{
  display: flex;
  flex-wrap: wrap;
  gap: var(--wii-space-lg);
  margin: var(--wii-space-md) 0;
}}
.wii-body-map-view {{
  flex: 1 1 200px;
  max-width: 260px;
}}
.wii-body-chips {{
  display: flex;
  flex-wrap: wrap;
  gap: var(--wii-space-sm) var(--wii-space-md);
  margin: var(--wii-space-sm) 0 var(--wii-space-md);
}}
.wii-body-chip {{
  display: inline-flex;
  align-items: baseline;
  gap: var(--wii-space-xs);
  background: none;
  border: none;
  border-bottom: 1px solid var(--wii-rule);
  border-radius: 0;
  padding: 2px 0 3px;
  font-size: var(--wii-text-label);
}}
.wii-body-chip .wii-body-chip-label {{ color: var(--wii-graphite-soft); }}
.wii-body-chip .wii-body-chip-value {{
  font-family: var(--wii-font-narrow);
  font-variant-numeric: tabular-nums lining-nums;
  color: var(--wii-graphite);
}}
.wii-narrative-list {{
  display: flex;
  flex-direction: column;
  gap: 0;
  margin: var(--wii-space-md) 0;
  border-top: 1px solid var(--wii-rule);
}}
.wii-narrative-card {{
  background: none;
  border: none;
  border-bottom: 1px solid var(--wii-rule);
  border-radius: 0;
  padding: 12px 0 13px;
}}
.wii-narrative-card .wii-narrative-text {{
  font-size: var(--wii-text-body);
  color: var(--wii-graphite);
  margin-bottom: var(--wii-space-xs);
  line-height: 1.5;
  max-width: 74ch;
}}
.wii-narrative-card .wii-narrative-meta {{
  font-family: var(--wii-font-narrow);
  font-size: var(--wii-text-label);
  color: var(--wii-graphite-soft);
}}
.wii-comparison-strip {{
  width: 100%;
  border-collapse: collapse;
  margin: var(--wii-space-md) 0;
  font-size: var(--wii-text-label);
}}
.wii-comparison-strip th, .wii-comparison-strip td {{
  border: none;
  border-bottom: 1px solid var(--wii-rule);
  padding: 7px 10px 7px 0;
  text-align: left;
}}
.wii-comparison-strip th {{
  font-family: var(--wii-font-narrow);
  font-size: var(--wii-text-micro);
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: var(--wii-graphite-soft);
  font-weight: var(--wii-weight-semibold);
  border-bottom: 1px solid var(--wii-graphite);
}}
.wii-comparison-strip td {{
  font-family: var(--wii-font-narrow);
  font-variant-numeric: tabular-nums lining-nums;
  color: var(--wii-graphite);
}}
.wii-comparison-suppressed {{ color: var(--wii-graphite-soft); font-style: italic; }}
.wii-comparison-flag {{
  font-family: var(--wii-font-sans);
  color: var(--wii-graphite-soft);
  font-size: var(--wii-text-label);
  margin-left: var(--wii-space-xs);
}}
.wii-case-list {{
  display: flex;
  flex-direction: column;
  gap: 0;
  margin: var(--wii-space-md) 0;
  border-top: 1px solid var(--wii-rule);
}}
.wii-case-card {{
  background: none;
  border: none;
  border-bottom: 1px solid var(--wii-rule);
  border-radius: 0;
  padding: 13px 0 14px;
}}
.wii-case-card .wii-case-narrative {{
  font-size: var(--wii-text-body);
  color: var(--wii-graphite);
  margin-bottom: var(--wii-space-sm);
  line-height: 1.5;
  max-width: 74ch;
}}
.wii-case-card mark {{
  background-color: {_rgba(BLUEPRINT, 0.16)};
  color: var(--wii-graphite);
  border-bottom: 1px solid {_rgba(BLUEPRINT, 0.5)};
  font-weight: inherit;
  padding: 0 1px;
}}
.wii-case-meta {{
  display: flex;
  flex-wrap: wrap;
  gap: var(--wii-space-sm) var(--wii-space-lg);
  font-family: var(--wii-font-narrow);
  font-size: var(--wii-text-label);
  color: var(--wii-graphite-soft);
}}
.wii-case-meta .wii-case-meta-label {{ color: var(--wii-graphite-soft); }}
.wii-case-meta .wii-case-meta-value {{ color: var(--wii-graphite); }}
.wii-confidence-high {{ display: inline-block; color: var(--wii-graphite); }}
.wii-confidence-low {{
  display: inline-block;
  color: var(--wii-graphite-soft);
  border-bottom: 1px dashed var(--wii-construction);
}}
.wii-disclaimer {{
  background: none;
  border: none;
  border-left: 2px solid var(--wii-graphite);
  border-radius: 0;
  padding: 2px 0 2px var(--wii-space-md);
  font-size: var(--wii-text-body);
  color: var(--wii-graphite);
  margin: var(--wii-space-md) 0;
  max-width: 74ch;
}}
.wii-shop-list {{
  display: flex;
  flex-direction: column;
  gap: 0;
  margin: var(--wii-space-sm) 0 var(--wii-space-md);
}}
.wii-shop-list .wii-shop-list-row {{
  font-size: var(--wii-text-body);
  color: var(--wii-graphite);
  padding: 5px 0;
  border-bottom: 1px solid var(--wii-rule);
}}
.wii-limited-divider {{
  font-size: var(--wii-text-label);
  color: var(--wii-graphite-soft);
  border-top: 1px dashed var(--wii-construction);
  padding-top: var(--wii-space-xs);
  margin-top: var(--wii-space-xs);
}}
.wii-watchout-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 0;
  margin: var(--wii-space-md) 0;
  border-top: 1px solid var(--wii-rule);
}}
@media (max-width: 640px) {{
  .wii-watchout-grid {{ grid-template-columns: 1fr; }}
}}
.wii-watchout-card {{
  background: none;
  border: none;
  border-bottom: 1px solid var(--wii-rule);
  border-left: 1px solid var(--wii-rule);
  border-radius: 0;
  padding: 12px 16px 14px;
}}
.wii-watchout-card:first-child {{ border-left: none; padding-left: 0; }}
.wii-watchout-card .wii-watchout-title {{
  font-size: var(--wii-text-subhead);
  font-weight: var(--wii-weight-semibold);
  color: var(--wii-graphite);
  margin-bottom: var(--wii-space-sm);
}}
.wii-watchout-card .wii-watchout-field {{ margin-bottom: var(--wii-space-xs); }}
.wii-watchout-card .wii-watchout-label {{
  font-size: var(--wii-text-label);
  color: var(--wii-graphite-soft);
}}
.wii-watchout-card .wii-watchout-value {{
  font-size: var(--wii-text-body);
  color: var(--wii-graphite);
}}
.wii-watchout-card .wii-watchout-note {{
  font-size: var(--wii-text-label);
  color: var(--wii-graphite-soft);
}}
/* The one place redline appears at size: a genuine caution, marked as a correction. */
.wii-callout {{
  background: none;
  border: none;
  border-left: 3px solid var(--wii-redline);
  border-radius: 0;
  padding: 2px 0 2px var(--wii-space-md);
  font-size: var(--wii-text-body);
  color: var(--wii-graphite);
  margin-bottom: var(--wii-space-xl);
  max-width: 74ch;
}}
@media (prefers-reduced-motion: reduce) {{
  * {{ transition: none !important; animation: none !important; }}
}}
</style>
"""


def inject() -> None:
    """Inject fonts, tokens, the spacing scale, the measured-value grid and chrome hiding once,
    near the top of `app.py`. Uses `st.markdown(unsafe_allow_html=True)`, not `st.html`."""
    st.markdown(_CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Semantic palettes. Fixed dictionaries, looked up by category name -- NEVER a rotating Plotly
# `colorway` (a colorway breaks "same category, same color everywhere" the moment trace category
# order differs between charts).
#
# Three NON-OVERLAPPING ink families, one per dimension. The previous palettes resolved
# `laceration`, `direct_cutting_contact` and the `saws` family all to #E8735C, and those co-occur
# on one screen (a red laceration bar above a red direct-contact bar; a red Sankey node feeding a
# red node). No hex below appears in two palettes, and none equals BLUEPRINT or REDLINE.
# ---------------------------------------------------------------------------

# What the injury IS -- warm inks.
DIAGNOSIS_COLORS: dict[str, str] = {
    "laceration": "#C1553D",
    "amputation": SEVERE,
    "fracture": "#B07A2E",
    "puncture": "#6B4E9B",
    "foreign_body": "#2E7D74",
    "contusion_abrasion": "#5C7FA8",
    "avulsion": "#9B4F6E",
    "crushing": "#556370",
    "other": "#8A918C",
}

# `dim_diagnosis.injury_category` has 24 distinct values; only the 8 named directly above have a
# dedicated hue -- everything else maps to "other".
_DIAGNOSIS_DIRECT_KEYS = frozenset(DIAGNOSIS_COLORS) - {"other"}


def diagnosis_palette_key(injury_category: str) -> str:
    """Maps any `dim_diagnosis.injury_category` value to one of the 9 `DIAGNOSIS_COLORS` keys."""
    return injury_category if injury_category in _DIAGNOSIS_DIRECT_KEYS else "other"


# HOW it happened -- a cool teal-to-indigo family, entirely disjoint from the diagnosis inks so a
# mechanism mark and a diagnosis mark can never be confused on a shared screen.
MECHANISM_COLORS: dict[str, str] = {
    "direct_cutting_contact": "#1F5E5B",
    "kickback_ejection": "#2C7A6F",
    "flying_debris_fb": "#3E8F7C",
    "loss_of_tool_control": "#4E7C8C",
    "workpiece_slip": "#3A6B92",
    "pinch_crush": "#4A5E96",
    "entanglement": "#5B5490",
    "maintenance_setup": "#6B7F5E",
    "fastener_discharge": "#2F6E88",
    "fall_trip": "#607A7E",
    "electrical_burn": "#7A6E4E",
    "other": "#8A918C",
    "unknown": "#A8AEA8",
}

#: Display labels for the mechanism keys, wherever a reader sees one: axis ticks, chart legends,
#: filter option lists. `key.replace("_", " ").title()` is not a label -- it rendered
#: `flying_debris_fb` as "Flying Debris Fb", and title-casing a category turns a description into
#: something that reads like a proper noun. Sentence case, and the compounds keep their slash.
#:
#: These are the LABEL twin of `MECHANISM_COLORS` and must keep its keys; `data._MECHANISM_LABELS`
#: is a different thing -- lowercase fragments for mid-sentence prose ("...associated with flying
#: debris/foreign body") -- and neither can stand in for the other.
#:
#: "other" is a real classifier-assigned category, distinct from "unknown" narrative coverage, so
#: it is labelled "Other (classified)" everywhere rather than reading as a vague catch-all.
#: Every label here fits `charts.heatmap`'s two-line rotated axis tick; a longer one would be
#: truncated there, so check it against that budget before adding one.
MECHANISM_DISPLAY_LABELS: dict[str, str] = {
    "direct_cutting_contact": "Direct cutting contact",
    "kickback_ejection": "Kickback/ejection",
    "flying_debris_fb": "Flying debris/foreign body",
    "loss_of_tool_control": "Loss of tool control",
    "workpiece_slip": "Workpiece slip",
    "pinch_crush": "Pinch/crush",
    "entanglement": "Entanglement",
    "maintenance_setup": "Maintenance/setup",
    "fastener_discharge": "Fastener discharge",
    "fall_trip": "Fall/trip",
    "electrical_burn": "Electrical/burn",
    "other": "Other (classified)",
    "unknown": "Unknown",
}


def mechanism_label(key: str) -> str:
    """The one way to turn a mechanism key into display text. Falls back to sentence case rather
    than `.title()` so an unmapped key never reintroduces the "Flying Debris Fb" shape."""
    return MECHANISM_DISPLAY_LABELS.get(key, key.replace("_", " ").capitalize())


# Tool families -- neutral earth/graphite, so a family mark never competes with either set above.
FAMILY_COLORS: dict[str, str] = {
    "saws": "#3B4348",
    "abrasive": "#6E6257",
    "cutting_shaping": "#4F5B52",
    "drilling_fastening": "#52606E",
    "manual": "#7A7268",
}

# INK DENSITY = MAGNITUDE. One hue family, from nearly-bare vellum to saturated blueprint;
# lightness descends in roughly even steps. No amber anywhere: amber is retired from the product.
# A suppressed cell is told apart from a low real value by having NO fill and a dashed outline --
# never by being a different colour.
SEQUENTIAL_SCALE: list[str] = ["#DCE3E4", "#A8C2CE", "#6D9BB4", "#3A7192", "#134A66"]

# Ratio vs. a baseline: blueprint (under) -> neutral vellum (at) -> redline (over). Both poles are
# non-amber, so a "2x baseline" cell can no longer be read as the caution marking, and the warm end
# no longer shares a hue with SEQUENTIAL_SCALE's top.
DIVERGING_SCALE: list[str] = ["#1C5B7D", "#7FA3B5", "#CFD3CC", "#C98A72", REDLINE]

#: displayModeBar off for every `st.plotly_chart` call.
PLOTLY_CONFIG: dict[str, bool] = {"displayModeBar": False}

# ---------------------------------------------------------------------------
# Shared chart treatment.
#
# `st.plotly_chart` defaults to `theme="streamlit"`, which REPLACES the figure's template with
# Streamlit's own -- none of a registered template survives. Properties set directly on
# `fig.layout` DO survive, so `style_chart` writes the treatment explicitly on each figure instead
# of trusting the template. Every chart must route through it.
# ---------------------------------------------------------------------------

CHART_TEXT_SIZE = TYPE_MICRO  # tick labels, axis titles, legend, notes
CHART_DATA_LABEL_SIZE = TYPE_MICRO  # values printed on marks
CHART_HOVER_SIZE = TYPE_LABEL

_CHART_GRIDS = frozenset({"x", "y", "xy", "none"})


def stability_line(stability: str) -> dict[str, object]:
    """The confidence channel as Plotly/SVG line properties.

    Line treatment carries confidence; fill carries magnitude. Returns the outline for a mark of
    the given stability -- `"stable"`, `"limited"` or `"suppressed"`. A suppressed mark additionally
    takes NO fill (the caller drops it to the page colour), which is what makes it unmistakable
    without giving it a colour of its own.
    """
    if stability == "stable":
        return {"color": GRAPHITE, "width": 1, "dash": "solid"}
    if stability == "limited":
        return {"color": CONSTRUCTION, "width": 1, "dash": "dash"}
    if stability == "suppressed":
        return {"color": CONSTRUCTION, "width": 1, "dash": "dot"}
    raise ValueError(f"unknown stability {stability!r}")


def chart_note(text: str, **position) -> dict:
    """Annotation kwargs for a quiet in-chart note (orientation captions, group dividers), so every
    chart's secondary text is the same size and colour. `position` passes straight through."""
    return {
        "text": text,
        "showarrow": False,
        "font": {"family": FONT_SANS, "size": CHART_TEXT_SIZE, "color": GRAPHITE_SOFT},
        **position,
    }


def style_chart(
    fig: go.Figure,
    *,
    height: int,
    margin: dict[str, int] | None = None,
    grid: str = "y",
    legend: bool = False,
) -> go.Figure:
    """Applies the product chart treatment to `fig` in place and returns it.

    - One text size (12px) and colour role (soft graphite) for every tick, axis title, legend and
      note; data values stay full-strength ink.
    - Gridlines only on the value axis (`grid`), at graph-paper strength; no zero line, no ticks.
    - Tight explicit margins with `automargin`, so long category labels grow the margin instead of
      being clipped and no chart carries Plotly's default 100px dead band.
    - Legend, when shown, sits in a fixed band above the plot, right-aligned.
    """
    if grid not in _CHART_GRIDS:
        raise ValueError(f"grid must be one of {sorted(_CHART_GRIDS)}, got {grid!r}")
    fig.update_layout(
        height=height,
        margin={"l": 8, "r": 16, "t": 8, "b": 8, "pad": 0, **(margin or {})},
        paper_bgcolor=VELLUM,
        plot_bgcolor=VELLUM,
        font={"family": FONT_SANS, "size": CHART_TEXT_SIZE, "color": GRAPHITE_SOFT},
        hoverlabel={
            "bgcolor": VELLUM,
            "bordercolor": GRAPHITE,
            "align": "left",
            "font": {"family": FONT_SANS, "size": CHART_HOVER_SIZE, "color": GRAPHITE},
        },
        showlegend=legend,
    )
    if legend:
        fig.update_layout(
            legend={
                "orientation": "h",
                "x": 1.0,
                "xanchor": "right",
                "y": 1.0,
                "yanchor": "bottom",
                "bgcolor": "rgba(0,0,0,0)",
                "font": {"size": CHART_TEXT_SIZE, "color": GRAPHITE_SOFT},
                "itemsizing": "constant",
            }
        )
    axis = {
        "tickfont": {"size": CHART_TEXT_SIZE, "color": GRAPHITE_SOFT},
        "title_font": {"size": CHART_TEXT_SIZE, "color": GRAPHITE_SOFT},
        "gridcolor": GRID,
        "gridwidth": 1,
        "linecolor": RULE,
        "zeroline": False,
        "ticks": "",
        "automargin": True,
    }
    fig.update_xaxes(**axis, showgrid=grid in {"x", "xy"})
    fig.update_yaxes(**axis, showgrid=grid in {"y", "xy"})
    return fig


def _register_plotly_template() -> None:
    """Registers `wii_draft` as the DEFAULT Plotly template at import time. Inside the app
    Streamlit's chart theme replaces it (see `style_chart`), so this is the fallback for figures
    rendered elsewhere -- notebooks, static export -- and for charts not yet routed through
    `style_chart`. `colorway` is reserved for charts with NO semantic category (a single-series bar
    or line); diagnosis/mechanism/family colours always come from the fixed dictionaries above.
    """
    axis = {
        "gridcolor": GRID,
        "zerolinecolor": RULE,
        "linecolor": RULE,
        "tickfont": {"size": CHART_TEXT_SIZE, "color": GRAPHITE_SOFT},
    }
    template = go.layout.Template(
        layout=go.Layout(
            paper_bgcolor=VELLUM,
            plot_bgcolor=VELLUM,
            font={"family": FONT_SANS, "size": CHART_TEXT_SIZE, "color": GRAPHITE_SOFT},
            xaxis=axis,
            yaxis=axis,
            colorway=[BLUEPRINT, GRAPHITE_SOFT, REDLINE],
        )
    )
    pio.templates["wii_draft"] = template
    # Kept as an alias so any call site still naming the old template resolves.
    pio.templates["wii_dark"] = template
    pio.templates.default = "wii_draft"


_register_plotly_template()
