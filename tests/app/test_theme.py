"""D-06 theme palette coverage tests: every committed diagnosis/mechanism/family category maps to
a color, no palette color collides with the reserved primary/severe hues, and the registered
Plotly template is the active default.
"""

from __future__ import annotations

import plotly.graph_objects as go
import plotly.io as pio
import pytest

from wii.app import theme
from wii.metrics import connect


def test_diagnosis_palette_covers_every_committed_injury_category():
    con = connect()
    categories = [
        r[0] for r in con.execute("SELECT DISTINCT injury_category FROM dim_diagnosis").fetchall()
    ]
    assert categories, "expected committed dim_diagnosis rows"
    for category in categories:
        assert theme.diagnosis_palette_key(category) in theme.DIAGNOSIS_COLORS


def test_mechanism_palette_covers_every_committed_mechanism():
    con = connect()
    mechanisms = [
        r[0]
        for r in con.execute(
            "SELECT DISTINCT primary_mechanism FROM narrative_enrichment"
        ).fetchall()
    ]
    assert mechanisms, "expected committed narrative_enrichment rows"
    for mechanism in mechanisms:
        assert mechanism in theme.MECHANISM_COLORS


def test_family_palette_covers_every_committed_family():
    con = connect()
    families = [
        r[0] for r in con.execute("SELECT DISTINCT family_id FROM dim_tool_family").fetchall()
    ]
    assert families, "expected committed dim_tool_family rows"
    for family in families:
        assert family in theme.FAMILY_COLORS


def test_no_palette_color_equals_a_reserved_accent():
    """BLUEPRINT is the one data accent and REDLINE is correction ink. Neither may also stand for
    a category, or a reader could not tell "this is the selection" from "this is lacerations".

    SEVERE is deliberately NOT reserved: `DIAGNOSIS_COLORS["amputation"]` IS the severe token, on
    purpose -- amputation is the most severe outcome the diagnosis palette names, and giving it a
    second, near-identical maroon would be a distinction with no meaning.
    """
    reserved = {theme.BLUEPRINT, theme.REDLINE}
    for palette in (theme.DIAGNOSIS_COLORS, theme.MECHANISM_COLORS, theme.FAMILY_COLORS):
        for color in palette.values():
            assert color not in reserved


#: The residual buckets. A neutral grey meaning "everything else" in BOTH the diagnosis and the
#: mechanism palette is not a collision -- it is the same statement about two dimensions, and
#: giving each its own grey would invent a distinction the data does not make.
_RESIDUAL_KEYS = frozenset({"other", "unknown"})


def _named_colors(palette: dict[str, str]) -> set[str]:
    return {c for k, c in palette.items() if k not in _RESIDUAL_KEYS}


def test_no_named_category_hex_appears_in_two_palettes():
    """The review found `laceration`, `direct_cutting_contact` and the `saws` family all resolving
    to one hex, and those co-occur on a single screen -- a red laceration bar above a red
    direct-contact bar, a red Sankey node feeding a red node. Each dimension now has its own ink
    family, so "same colour" can only ever mean "same category"."""
    palettes = (theme.DIAGNOSIS_COLORS, theme.MECHANISM_COLORS, theme.FAMILY_COLORS)
    for i, first in enumerate(palettes):
        for second in palettes[i + 1 :]:
            assert not _named_colors(first) & _named_colors(second)


def test_diagnosis_colors_are_distinct():
    assert len(set(theme.DIAGNOSIS_COLORS.values())) == len(theme.DIAGNOSIS_COLORS)


def test_mechanism_colors_are_distinct():
    assert len(set(theme.MECHANISM_COLORS.values())) == len(theme.MECHANISM_COLORS)


def test_family_colors_are_distinct():
    assert len(set(theme.FAMILY_COLORS.values())) == len(theme.FAMILY_COLORS)


def test_draft_template_registered_as_default_with_the_legacy_alias_intact():
    assert pio.templates.default == "wii_draft"
    # Kept so any call site still naming the retired dark template resolves rather than raising.
    assert "wii_dark" in pio.templates


def test_plotly_config_disables_mode_bar():
    assert theme.PLOTLY_CONFIG == {"displayModeBar": False}


# ---------------------------------------------------------------------------
# Injected CSS block
# ---------------------------------------------------------------------------


def test_css_block_has_no_blank_lines():
    """CommonMark ends a raw-HTML block at the first blank line: everything after it is re-parsed
    as Markdown and leaks onto the page as visible text (curly braces, a stray `</style>`). The
    block is injected through `st.markdown`, so one empty line anywhere breaks every page."""
    lines = theme._CSS.strip("\n").split("\n")
    blank = [i for i, line in enumerate(lines, start=1) if not line.strip()]
    assert blank == [], f"blank line(s) inside theme._CSS at block line(s) {blank}"


def test_css_block_is_one_style_element():
    css = theme._CSS
    assert css.count("<style>") == 1
    assert css.count("</style>") == 1
    assert css.rstrip().endswith("</style>")


def test_css_applies_the_type_scale_to_streamlit_headings():
    """The scale tokens used to be declared and consumed by nothing -- headings rendered at
    Streamlit's stock 41/34/26px in Source Sans."""
    css = theme._CSS
    for selector, token in (
        ('[data-testid="stMain"] h1', "--wii-text-display"),
        ('[data-testid="stMain"] h2', "--wii-text-heading"),
        ('[data-testid="stMain"] h3', "--wii-text-subhead"),
    ):
        block = css[css.index(selector + " {") :]
        assert f"font-size: var({token})" in block[: block.index("}")]
    assert f"--wii-text-display: {theme.TYPE_DISPLAY}px" in css
    assert theme.TYPE_DISPLAY > theme.TYPE_HEADING > theme.TYPE_SUBHEAD > theme.TYPE_BODY
    assert theme.TYPE_BODY > theme.TYPE_LABEL > theme.TYPE_MICRO


def test_plotly_svg_text_is_forced_to_the_product_typeface():
    """Streamlit's chart theme writes font-family as an inline style on every SVG <text>; only an
    `!important` rule reaches it."""
    assert '[data-testid="stPlotlyChart"] text' in theme._CSS
    assert "font-family: var(--wii-font-sans) !important" in theme._CSS


# ---------------------------------------------------------------------------
# Scales
# ---------------------------------------------------------------------------


def _luminance(hex_color: str) -> float:
    def channel(c: int) -> float:
        v = c / 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _is_warm(hex_color: str) -> bool:
    h = hex_color.lstrip("#")
    r, _, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return r > b


def test_sequential_scale_is_monotonic_in_lightness():
    """INK DENSITY = MAGNITUDE: the ramp runs from nearly-bare sheet to saturated blueprint, so
    luminance DESCENDS as the value rises. More ink, more injuries."""
    lum = [_luminance(c) for c in theme.SEQUENTIAL_SCALE]
    assert lum == sorted(lum, reverse=True)
    assert len(set(lum)) == len(lum)


def test_sequential_scale_starts_at_the_sheet_and_ends_in_blueprint():
    assert _luminance(theme.SEQUENTIAL_SCALE[0]) > _luminance(theme.VELLUM_DEEP)
    assert _luminance(theme.SEQUENTIAL_SCALE[-1]) < _luminance(theme.SEQUENTIAL_SCALE[0]) / 5


def test_sequential_scale_carries_no_caution_ink():
    """Magnitude is never line-coded and confidence is never colour-coded. The ramp must therefore
    contain no correction ink at all: when the ramp's top stop and the limited-sample marking
    shared a hue, the highest real value and "we do not trust this" looked the same."""
    assert theme.REDLINE not in theme.SEQUENTIAL_SCALE
    assert theme.CAUTION not in theme.SEQUENTIAL_SCALE
    assert not any(_is_warm(c) for c in theme.SEQUENTIAL_SCALE)


def test_stability_line_is_the_only_confidence_channel():
    """Line treatment carries confidence: solid for a finished estimate, dashed for provisional.
    No state may differ from another by COLOUR alone, and none may use a palette accent."""
    lines = {s: theme.stability_line(s) for s in ("stable", "limited", "suppressed")}
    assert lines["stable"]["dash"] == "solid"
    assert lines["limited"]["dash"] != "solid"
    assert lines["suppressed"]["dash"] != "solid"
    assert lines["limited"]["dash"] != lines["suppressed"]["dash"]
    for line in lines.values():
        assert line["color"] not in {theme.BLUEPRINT, theme.REDLINE}


def test_stability_line_rejects_an_unknown_state():
    with pytest.raises(ValueError):
        theme.stability_line("probably_fine")


def test_diverging_warm_end_does_not_share_a_hue_with_the_sequential_top():
    assert _is_warm(theme.DIVERGING_SCALE[-1])
    assert not _is_warm(theme.SEQUENTIAL_SCALE[-1])


# ---------------------------------------------------------------------------
# Shared chart treatment
# ---------------------------------------------------------------------------


def test_style_chart_writes_the_treatment_onto_the_figure_not_the_template():
    """Streamlit's `theme="streamlit"` swaps the figure's template out; only properties written
    directly on `fig.layout` survive, so the treatment must live there."""
    fig = theme.style_chart(go.Figure(go.Bar(x=[1], y=["a"])), height=300, grid="x")
    layout = fig.layout
    assert layout.height == 300
    assert layout.font.family == theme.FONT_SANS
    assert layout.font.size == theme.CHART_TEXT_SIZE
    assert layout.xaxis.tickfont.size == theme.CHART_TEXT_SIZE
    assert layout.xaxis.showgrid is True
    assert layout.yaxis.showgrid is False
    assert layout.xaxis.gridcolor == theme.GRID
    assert layout.margin.t <= 16
    assert layout.showlegend is False


def test_style_chart_legend_sits_in_a_band_above_the_plot():
    fig = theme.style_chart(go.Figure(), height=300, legend=True)
    assert fig.layout.showlegend is True
    assert fig.layout.legend.orientation == "h"
    assert fig.layout.legend.yanchor == "bottom"
    assert fig.layout.legend.y == 1.0


def test_style_chart_rejects_unknown_grid():
    with pytest.raises(ValueError):
        theme.style_chart(go.Figure(), height=300, grid="diagonal")


def test_rule_grid_and_construction_are_all_derived_from_graphite():
    """Derived shades never introduce a new literal: a hairline, the graph-paper grid and a
    construction line are all the ink token at reduced opacity, in ascending strength."""
    h = theme.GRAPHITE.lstrip("#")
    rgb = ", ".join(str(int(h[i : i + 2], 16)) for i in (0, 2, 4))
    for derived in (theme.RULE, theme.GRID, theme.CONSTRUCTION):
        assert derived.startswith(f"rgba({rgb}, ")
    alphas = [
        float(d.rsplit(", ", 1)[1].rstrip(")"))
        for d in (theme.GRID, theme.RULE, theme.CONSTRUCTION)
    ]
    assert alphas == sorted(alphas)


def test_the_ground_is_light_and_the_ink_is_dark():
    """The single consequential swing in the rebuild: the sheet is vellum and graphite is ink ON
    it, never a background. A regression here silently un-does every contrast decision below."""
    assert _luminance(theme.VELLUM) > 0.6
    assert _luminance(theme.GRAPHITE) < 0.1
    assert theme.BACKGROUND == theme.VELLUM
    assert theme.TEXT == theme.GRAPHITE


def test_body_text_clears_wcag_aa_on_the_sheet():
    def contrast(a: str, b: str) -> float:
        la, lb = _luminance(a), _luminance(b)
        hi, lo = max(la, lb), min(la, lb)
        return (hi + 0.05) / (lo + 0.05)

    assert contrast(theme.GRAPHITE, theme.VELLUM) >= 4.5
    assert contrast(theme.GRAPHITE_SOFT, theme.VELLUM) >= 3.0
    assert contrast(theme.BLUEPRINT, theme.VELLUM) >= 4.5
    assert contrast(theme.REDLINE, theme.VELLUM) >= 4.5
