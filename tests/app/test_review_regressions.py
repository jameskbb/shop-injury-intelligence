"""Regressions for the presentation review's Lane C findings.

One test per defect the review actually found in the shipped build, written so the defect cannot
come back quietly:

  * an all-suppressed ranked chart was drawn with a zero-width value axis, which Plotly autoranged
    and labelled in SI milli-units ("−500m … 500m") beside a column of dashes;
  * the same "share by category" fact was drawn on five pages in two orientations;
  * amber, at 0.55-0.6 alpha, was the loudest mark on four of nine pages and meant "we don't
    trust this";
  * several builders bypassed the shared chart treatment and shipped Plotly's default chrome;
  * internal plan ids ("(D-13)") leaked into user-facing captions.
"""

from __future__ import annotations

import re

import polars as pl
import pytest

from wii.app import theme
from wii.app.charts import heatmap, injury_mix, marks, profile_bars, ranked_bars
from wii.app.pages import case_explorer as case_explorer_page
from wii.app.pages import comparison as comparison_page
from wii.app.pages import mechanisms as mechanisms_page
from wii.app.pages import methodology as methodology_page

from .conftest import APP_TIMEOUT

# ---------------------------------------------------------------------------
# Fixtures (hand-built frames -- no database, no AppTest)
# ---------------------------------------------------------------------------


def _estimate_row(group_id, label, estimate, raw_n=200, cv=0.10, stable=True, reasons=()):
    se = estimate * cv
    return {
        "group_id": group_id,
        "display_label": label,
        "estimate": estimate,
        "raw_n": raw_n,
        "se": se,
        "cv": cv,
        "ci_low": estimate - 1.96 * se,
        "ci_high": estimate + 1.96 * se,
        "df": 20,
        "stable": stable,
        "suppression_reason": list(reasons),
        "period_total": estimate,
        "annual_average": estimate,
        "se_annual": se,
        "ci_low_annual": estimate - 1.96 * se,
        "ci_high_annual": estimate + 1.96 * se,
        "n_years": 1,
        "basis": "period",
        "stable_annual": stable,
        "suppression_reason_annual": list(reasons),
    }


def _suppressed_row(group_id, label):
    return _estimate_row(
        group_id,
        label,
        500.0,
        raw_n=5,
        cv=0.6,
        stable=False,
        reasons=("est_lt_1200", "n_lt_20"),
    )


def _share_row(category, label, value, state="stable", raw_n=200, baseline=None):
    row = {
        "category": category,
        "label": label,
        "value": value,
        "ci_low": value * 0.9 if value is not None else None,
        "ci_high": value * 1.1 if value is not None else None,
        "raw_n": raw_n,
        "state": state,
    }
    if baseline is not None:
        row["baseline"] = baseline
    return row


# ---------------------------------------------------------------------------
# MECH-10 (P0): never draw an all-suppressed ranked chart
# ---------------------------------------------------------------------------


def test_all_suppressed_ranked_frame_returns_no_figure():
    df = pl.DataFrame([_suppressed_row("a", "Blade change"), _suppressed_row("b", "Guard removal")])
    assert ranked_bars.build_ranked_bars(df, "period_total", "Estimated injuries") is None


def test_all_suppressed_note_names_every_row_and_its_sample_count():
    df = pl.DataFrame([_suppressed_row("a", "Blade change"), _suppressed_row("b", "Guard removal")])
    note = ranked_bars.all_suppressed_note(df, "period_total", "activities")
    assert "2 activities" in note
    assert "Blade change" in note and "Guard removal" in note
    assert "10 sample cases" in note
    assert "not a national count" in note


def test_ranked_bars_value_axis_range_is_explicit_and_non_negative():
    """The milli-unit axis came from Plotly autoranging an empty domain. With the range pinned to
    [0, longest whisker + headroom] there is no empty domain to autorange."""
    df = pl.DataFrame([_estimate_row("a", "Table Saw", 50_000.0), _suppressed_row("b", "Lathe")])
    fig = ranked_bars.build_ranked_bars(df, "period_total", "Estimated injuries")
    low, high = fig.layout.xaxis.range
    assert low == 0
    assert high > 50_000


def test_ranked_bars_suppressed_footer_mode_drops_the_row_but_still_names_it():
    df = pl.DataFrame([_estimate_row("a", "Table Saw", 50_000.0), _suppressed_row("b", "Lathe")])
    fig = ranked_bars.build_ranked_bars(
        df, "period_total", "Estimated injuries", suppressed_footer=True
    )
    assert "Lathe" not in list(fig.data[0].y)
    assert ranked_bars.suppressed_labels(df, "period_total") == ("Lathe",)
    note = ranked_bars.suppressed_footer_note(df, "period_total")
    assert note == "1 below the reporting floor: Lathe."


def test_ranked_bars_suppressed_footer_note_is_none_when_nothing_is_suppressed():
    df = pl.DataFrame([_estimate_row("a", "Table Saw", 50_000.0)])
    assert ranked_bars.suppressed_footer_note(df, "period_total") is None


# ---------------------------------------------------------------------------
# The confidence channel: line treatment, never colour
# ---------------------------------------------------------------------------

#: The retired amber family. None of it may appear in any figure this lane builds.
_RETIRED_AMBER = ("#D9A441", "#E8A33D", "#F2B544", "#E8735C")


def _figure_colors(fig) -> str:
    return fig.to_json()


def test_no_chart_carries_a_retired_amber():
    df = pl.DataFrame(
        [
            _share_row("laceration", "Laceration", 0.5),
            _share_row("amputation", "Amputation", 0.1, state="limited"),
        ]
    )
    figures = [
        profile_bars.build_diagnosis_bars(df),
        profile_bars.build_mechanism_bars(df),
        injury_mix.build_injury_mix(df),
        ranked_bars.build_ranked_bars(
            pl.DataFrame([_estimate_row("a", "Table Saw", 50_000.0)]),
            "period_total",
            "Estimated injuries",
        ),
    ]
    for fig in figures:
        payload = _figure_colors(fig)
        for amber in _RETIRED_AMBER:
            assert amber.lower() not in payload.lower()


def _luminance(hex_color: str) -> float:
    """Relative luminance of an `#rrggbb` colour, for asserting a fill got lighter."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def test_suppressed_marks_never_carry_a_fill():
    assert marks.fill_for(theme.BLUEPRINT, "suppressed") == marks.NO_FILL
    assert marks.fill_for(theme.BLUEPRINT, "stable") == theme.BLUEPRINT


def test_a_limited_mark_keeps_its_category_hue_and_only_loses_density():
    """Confidence and category are separate channels. Lightening a limited mark must not collapse
    it onto one shared colour -- if it did, a limited laceration bar and a limited fracture bar
    would become indistinguishable and confidence would be silently eating the category channel."""
    limited = {
        key: marks.fill_for(color, "limited")
        for key, color in theme.DIAGNOSIS_COLORS.items()
        if key != "other"
    }
    assert len(set(limited.values())) == len(limited), "limited fills collapsed onto one colour"
    for key, fill in limited.items():
        stable = theme.DIAGNOSIS_COLORS[key]
        assert fill != stable, key
        assert _luminance(fill) > _luminance(stable), f"{key} did not lighten toward the sheet"


def test_stability_outlines_are_dashed_and_graphite_never_a_hue():
    for state in ("limited", "suppressed"):
        line = theme.stability_line(state)
        assert line["dash"] != "solid"
        assert line["color"] == theme.CONSTRUCTION
    assert theme.stability_line("stable")["dash"] == "solid"


# ---------------------------------------------------------------------------
# One horizontal share-bar builder for every "share by category" view
# ---------------------------------------------------------------------------


def test_injury_mix_is_the_shared_horizontal_builder():
    """The Overview injury mix was the app's only VERTICAL copy of a chart drawn horizontally on
    four other pages, which forced rotated labels at 390px and made the reader re-learn it."""
    df = pl.DataFrame([_share_row("laceration", "Laceration", 0.5)])
    mix = injury_mix.build_injury_mix(df)
    profile = profile_bars.build_diagnosis_bars(df)
    assert mix.data[0].orientation == "h"
    assert list(mix.data[0].y) == list(profile.data[0].y)
    assert list(mix.data[0].x) == list(profile.data[0].x)


def test_share_bars_order_stable_before_limited_regardless_of_value():
    """Minimum-volume logic: a large but unstable share never outranks a smaller stable one."""
    df = pl.DataFrame(
        [
            _share_row("laceration", "Laceration", 0.2),
            _share_row("amputation", "Amputation", 0.8, state="limited"),
        ]
    )
    fig = profile_bars.build_diagnosis_bars(df)
    assert list(fig.data[0].y) == ["Laceration", "Amputation"]


# ---------------------------------------------------------------------------
# XC-11: every builder routes through the shared chart treatment
# ---------------------------------------------------------------------------


def test_every_lane_c_figure_carries_the_shared_chart_treatment():
    share_df = pl.DataFrame([_share_row("laceration", "Laceration", 0.5)])
    figures = [
        profile_bars.build_diagnosis_bars(share_df),
        profile_bars.build_mechanism_bars(share_df),
        profile_bars.build_disposition_stack(
            [
                {
                    "metric_id": "released",
                    "label": "Released",
                    "value": 0.9,
                    "state": "stable",
                    "raw_n": 500,
                }
            ]
        ),
        injury_mix.build_injury_mix(share_df),
        ranked_bars.build_ranked_bars(
            pl.DataFrame([_estimate_row("a", "Table Saw", 50_000.0)]),
            "period_total",
            "Estimated injuries",
        ),
    ]
    for fig in figures:
        assert fig.layout.paper_bgcolor == theme.VELLUM
        assert fig.layout.plot_bgcolor == theme.VELLUM
        assert fig.layout.font.family == theme.FONT_SANS
        assert fig.layout.height, "every chart sets its own height, never Plotly's default 450"
        # Plotly's default top margin is 100px of dead band above the plot.
        assert fig.layout.margin.t is not None and fig.layout.margin.t <= 48


# ---------------------------------------------------------------------------
# One number-formatting layer
# ---------------------------------------------------------------------------


def test_numbers_format_the_same_way_everywhere():
    assert marks.fmt_share(0.784) == "78.4%"
    assert marks.fmt_estimate(2_418_391) == "2,418,400"
    assert marks.fmt_points(0.032) == "+3.2 pts"
    assert marks.fmt_points(-0.008) == "-0.8 pts"
    assert marks.fmt_share(None) == marks.EM_DASH
    assert marks.fmt_estimate(None) == marks.EM_DASH


# ---------------------------------------------------------------------------
# Leak: internal plan ids in user-facing strings
# ---------------------------------------------------------------------------

_PLAN_ID = re.compile(r"\((?:D|T)-\d+\)|\bINS-\d+\b|\bWave-0\b")


def test_no_internal_plan_id_reaches_a_user_facing_string():
    for module in (comparison_page, mechanisms_page, case_explorer_page, methodology_page):
        for name in dir(module):
            if name.startswith("__"):
                continue
            value = getattr(module, name)
            if isinstance(value, str):
                assert not _PLAN_ID.search(value), f"{module.__name__}.{name}: {value!r}"


# ---------------------------------------------------------------------------
# Dense matrices: legible at 390px, and suppression carried by absence not by dashes
# ---------------------------------------------------------------------------


def _matrix_row(tool_id, category, value, state="stable"):
    return {
        "tool_id": tool_id,
        "tool_label": "Portable Circular Power Saw",
        "category": category,
        "label": category.replace("_", " ").title(),
        "value": value,
        "ci_low": None,
        "ci_high": None,
        "raw_n": 50 if value is not None else 0,
        "state": state,
    }


def test_matrix_outlines_only_limited_cells_and_counts_the_blanks():
    """283 of 330 cells are suppressed at the default filter. A dashed rectangle around each is
    graph paper -- the amber-hatch failure in a different ink -- so absence carries suppression
    and the figure states the count instead."""
    df = pl.DataFrame(
        [
            _matrix_row("saw", "laceration", 0.4),
            _matrix_row("saw", "amputation", 0.1, state="limited"),
            _matrix_row("saw", "fracture", None, state="suppressed"),
        ]
    )
    fig = heatmap.build_share_matrix(
        df, ["laceration", "amputation", "fracture"], drop_empty_categories=False
    )
    dashed = [s for s in fig.layout.shapes if s.line.dash in ("dash", "dot")]
    assert len(dashed) == 1, "only the limited cell takes the construction outline"
    note = "".join(a.text for a in fig.layout.annotations)
    assert "below the reporting floor" in note


def test_matrix_labels_are_rotated_and_truncated_for_a_390px_sheet():
    df = pl.DataFrame([_matrix_row("saw", "laceration", 0.4)])
    fig = heatmap.build_share_matrix(df, ["laceration"])
    assert fig.layout.xaxis.tickangle == -45
    assert all(len(y) <= heatmap.MAX_ROW_LABEL_CHARS for y in fig.data[0].y)
    # The full row name is never lost -- it moves to hover.
    assert fig.data[0].customdata[0][0][4] == "Portable Circular Power Saw"


def test_matrix_colorbar_sits_above_the_plot_not_across_it():
    """Vertical, the colorbar took the width the grid needs and its ticks landed on the cells at
    390px."""
    df = pl.DataFrame([_matrix_row("saw", "laceration", 0.4)])
    bar = heatmap.build_share_matrix(df, ["laceration"]).data[0].colorbar
    assert bar.orientation == "h"
    assert bar.y == 1.0 and bar.yanchor == "bottom"


# ---------------------------------------------------------------------------
# Every sheet opens with a title block, and two of them must not quote live filters
# ---------------------------------------------------------------------------

_LANE_C_PAGES = (
    "src/wii/app/pages/mechanisms.py",
    "src/wii/app/pages/comparison.py",
    "src/wii/app/pages/case_explorer.py",
    "src/wii/app/pages/methodology.py",
)


def _page_markdown(app_test, path: str) -> str:
    app_test.run(timeout=APP_TIMEOUT)
    app_test.switch_page(path)
    app_test.run(timeout=APP_TIMEOUT)
    assert not app_test.exception
    return "".join(m.value for m in app_test.markdown)


@pytest.mark.parametrize("path", _LANE_C_PAGES)
def test_every_page_opens_with_exactly_one_title_block(app_test, path):
    markdown = _page_markdown(app_test, path)
    assert markdown.count('class="wii-titleblock"') == 1


def test_pages_without_live_filters_do_not_quote_a_basis_they_do_not_use(app_test):
    """Methodology describes the whole dataset and Case Explorer shows no weighted estimate at
    all, so a strip quoting the sidebar's basis would be false on both."""
    markdown = _page_markdown(app_test, "src/wii/app/pages/methodology.py")
    assert methodology_page.SCOPE_FIELD in markdown
    assert "Annual average" not in markdown

    markdown = _page_markdown(app_test, "src/wii/app/pages/case_explorer.py")
    assert case_explorer_page.BASIS_FIELD in markdown
    assert "Annual average" not in markdown
