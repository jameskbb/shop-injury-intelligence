"""D-10 Tool Profiles chart-builder + narrative-query tests (03-03 Task 3). `trend.py`/
`profile_bars.py` builders are exercised with hand-built Polars frames -- no database, no
AppTest, mirroring 03-02's `test_charts_overview.py` convention. `representative_narratives` and
the median-age gate are tested against the real committed processed Parquet (same convention as
`test_data_layer.py` -- no `realdata` marker, since this reads already-committed data/processed,
never downloads).
"""

from __future__ import annotations

import polars as pl

import wii.app.data as data_module
from wii.app.charts import marks, profile_bars, trend
from wii.app.components.narrative_card import narrative_card_html
from wii.app.pages.tool_profiles import _median_age_card
from wii.app.theme import DIAGNOSIS_COLORS, MECHANISM_COLORS, SEVERE
from wii.metrics import CaseFilter

# ---------------------------------------------------------------------------
# trend
# ---------------------------------------------------------------------------


def _trend_row(year, value, state="stable", raw_n=200):
    ci_low = value * 0.9 if value else None
    ci_high = value * 1.1 if value else None
    return {
        "year": year,
        "value": value,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "state": state,
        "raw_n": raw_n,
    }


def _trend_df():
    return pl.DataFrame(
        [
            _trend_row(2017, 1000.0),
            _trend_row(2018, 1100.0),
            _trend_row(2019, 900.0, state="limited"),
            _trend_row(2020, None, state="suppressed", raw_n=5),
            _trend_row(2021, 1200.0),
        ]
    )


def test_trend_gaps_at_suppressed_years():
    fig = trend.build_trend(_trend_df(), "annual_average")
    main_trace = fig.data[-1]
    x_values = list(main_trace.x)
    y_values = list(main_trace.y)
    assert y_values[x_values.index(2020)] is None
    assert main_trace.connectgaps is False


def test_trend_hollow_marker_on_limited_year_solid_on_stable():
    fig = trend.build_trend(_trend_df(), "annual_average")
    main_trace = fig.data[-1]
    x_values = list(main_trace.x)
    symbols = list(main_trace.marker.symbol)
    assert symbols[x_values.index(2019)] == "circle-open"
    assert symbols[x_values.index(2017)] == "circle"


def test_trend_2019_annotation_when_range_crosses_it():
    fig = trend.build_trend(_trend_df(), "annual_average")
    shapes = fig.layout.shapes or ()
    assert len(shapes) == 1
    annotations = [a.text for a in (fig.layout.annotations or ())]
    assert any("2019" in a for a in annotations)


def test_trend_no_2019_annotation_when_range_does_not_cross_it():
    df = pl.DataFrame([_trend_row(2021, 1000.0), _trend_row(2022, 1100.0)])
    fig = trend.build_trend(df, "annual_average")
    shapes = fig.layout.shapes or ()
    assert len(shapes) == 0


def test_trend_ci_band_from_basis_matched_ci():
    fig = trend.build_trend(_trend_df(), "annual_average")
    # traces[0]/[1] are the CI band (upper bound, then lower/fill); traces[-1] is the main line.
    assert fig.data[1].fill == "tonexty"
    assert len(fig.data) == 3


# ---------------------------------------------------------------------------
# profile_bars (diagnosis / mechanism / disposition)
# ---------------------------------------------------------------------------


def _profile_row(category, label, value, state="stable", raw_n=200):
    ci_low = value * 0.9 if value else None
    ci_high = value * 1.1 if value else None
    return {
        "category": category,
        "label": label,
        "value": value,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "raw_n": raw_n,
        "state": state,
    }


def test_diagnosis_bars_colored_by_name_lookup():
    df = pl.DataFrame(
        [
            _profile_row("laceration", "Laceration", 0.5),
            _profile_row("amputation", "Amputation", 0.2),
        ]
    )
    fig = profile_bars.build_diagnosis_bars(df)
    colors = list(fig.data[0].marker.color)
    labels = list(fig.data[0].y)
    expected = {
        "Laceration": DIAGNOSIS_COLORS["laceration"],
        "Amputation": DIAGNOSIS_COLORS["amputation"],
    }
    for label, color in zip(labels, colors, strict=True):
        assert color == expected[label]


def test_mechanism_bars_colored_by_name_lookup():
    df = pl.DataFrame(
        [
            _profile_row("direct_cutting_contact", "Direct Cutting Contact", 0.6),
            _profile_row("kickback_ejection", "Kickback Ejection", 0.1),
        ]
    )
    fig = profile_bars.build_mechanism_bars(df)
    colors = list(fig.data[0].marker.color)
    assert MECHANISM_COLORS["direct_cutting_contact"] in colors
    assert MECHANISM_COLORS["kickback_ejection"] in colors


def test_profile_bars_drop_suppressed_and_mark_limited_with_a_dashed_outline():
    """Confidence is LINE TREATMENT: no hatch, no reduced-alpha amber. A limited bar keeps its
    length, is filled one step lighter, and is outlined with a dashed construction line."""
    df = pl.DataFrame(
        [
            _profile_row("laceration", "Laceration", 0.5),
            _profile_row("amputation", "Amputation", 0.1, state="limited"),
            _profile_row("fracture", "Fracture", 0.0, state="suppressed", raw_n=0),
        ]
    )
    fig = profile_bars.build_diagnosis_bars(df)
    labels = list(fig.data[0].y)
    assert "Fracture" not in labels
    assert fig.data[0].marker.pattern.shape in (None, "", ())
    colors = list(fig.data[0].marker.color)
    assert colors[labels.index("Laceration")] == DIAGNOSIS_COLORS["laceration"]
    # A limited bar keeps its own category hue and only loses density -- it must NOT collapse onto
    # a shared lightening stop, or confidence would overwrite the category channel.
    assert colors[labels.index("Amputation")] == marks.fill_for(
        DIAGNOSIS_COLORS["amputation"], "limited"
    )
    assert colors[labels.index("Amputation")] != DIAGNOSIS_COLORS["amputation"]
    dashed = [sh for sh in fig.layout.shapes if sh.line.dash in ("dash", "dot")]
    assert len(dashed) == 1


def test_profile_bars_print_values_and_pin_an_explicit_axis_range():
    df = pl.DataFrame([_profile_row("laceration", "Laceration", 0.5)])
    fig = profile_bars.build_diagnosis_bars(df)
    assert [t.strip() for t in fig.data[0].text] == ["50.0%"]
    assert fig.layout.xaxis.range == (0, 0.61)
    assert fig.layout.xaxis.tickformat == ".0%"


def test_profile_bars_draw_a_workshop_baseline_tick_when_the_frame_has_one():
    row = _profile_row("laceration", "Laceration", 0.74)
    row["baseline"] = 0.576
    fig = profile_bars.build_diagnosis_bars(pl.DataFrame([row]))
    ticks = [sh for sh in fig.layout.shapes if sh.type == "line"]
    assert len(ticks) == 1
    assert ticks[0].x0 == 0.576
    assert "All-workshop share" in fig.data[0].hovertext[0]


def test_profile_bars_return_none_when_nothing_is_drawable_and_asked_to():
    df = pl.DataFrame([_profile_row("fracture", "Fracture", None, state="suppressed", raw_n=0)])
    assert profile_bars.build_share_bars(df, DIAGNOSIS_COLORS, drop_when_empty=True) is None
    # The default stays total, so no existing page call site has to learn a new contract.
    assert profile_bars.build_diagnosis_bars(df) is not None


def test_disposition_caption_contains_not_a_sequential_process():
    assert "not a sequential process" in profile_bars.DISPOSITION_CAPTION


def test_disposition_stack_severe_red_only_on_admitted_and_fatality():
    rows = [
        {
            "metric_id": "released",
            "label": "Released",
            "value": 0.8,
            "state": "stable",
            "raw_n": 500,
        },
        {
            "metric_id": "admitted",
            "label": "Admitted",
            "value": 0.1,
            "state": "stable",
            "raw_n": 100,
        },
        {
            "metric_id": "fatality",
            "label": "Fatality",
            "value": 0.01,
            "state": "stable",
            "raw_n": 20,
        },
        {
            "metric_id": "transferred",
            "label": "Transferred",
            "value": 0.05,
            "state": "stable",
            "raw_n": 40,
        },
    ]
    fig = profile_bars.build_disposition_stack(rows)
    for tr in fig.data:
        if tr.name in ("Admitted", "Fatality"):
            assert tr.marker.color == SEVERE
        else:
            assert tr.marker.color != SEVERE


def test_disposition_stack_omits_suppressed_segment():
    rows = [
        {
            "metric_id": "released",
            "label": "Released",
            "value": 0.8,
            "state": "stable",
            "raw_n": 500,
        },
        {
            "metric_id": "fatality",
            "label": "Fatality",
            "value": None,
            "state": "suppressed",
            "raw_n": 0,
        },
    ]
    fig = profile_bars.build_disposition_stack(rows)
    names = [tr.name for tr in fig.data]
    assert "Fatality" not in names
    assert "Released" in names


def test_disposition_stack_legend_does_not_collide_with_axis_title():
    """TP-1: an unpositioned horizontal legend renders in the same vertical band as the x-axis
    title below the bar, and both become illegible where they collide. The legend must be
    explicitly anchored above the plot area (paper-coordinate y > 1)."""
    rows = [
        {
            "metric_id": "released",
            "label": "Released",
            "value": 0.9,
            "state": "stable",
            "raw_n": 500,
        },
        {
            "metric_id": "admitted",
            "label": "Admitted",
            "value": 0.1,
            "state": "stable",
            "raw_n": 50,
        },
    ]
    fig = profile_bars.build_disposition_stack(rows)
    # Every drawn segment here is >= 3%, so each is lettered directly under the strip and there is
    # no legend at all -- a legend earns its place only when it is doing work.
    assert fig.layout.showlegend is False
    labelled = [a.text for a in fig.layout.annotations]
    assert any(text.startswith("Released") for text in labelled)
    assert fig.layout.xaxis.title.text == "Share of known-disposition cases"


def test_disposition_stack_keeps_a_legend_key_only_for_segments_too_thin_to_letter():
    rows = [
        {
            "metric_id": "released",
            "label": "Released",
            "value": 0.98,
            "state": "stable",
            "raw_n": 500,
        },
        {
            "metric_id": "admitted",
            "label": "Admitted",
            "value": 0.02,
            "state": "stable",
            "raw_n": 50,
        },
    ]
    fig = profile_bars.build_disposition_stack(rows)
    assert fig.layout.showlegend is True
    legend_names = [tr.name for tr in fig.data if tr.showlegend]
    assert legend_names == ["Admitted"]


def test_disposition_summary_line_names_every_drawn_segment():
    rows = [
        {
            "metric_id": "released",
            "label": "Released",
            "value": 0.924,
            "state": "stable",
            "raw_n": 500,
        },
        {
            "metric_id": "fatality",
            "label": "Fatality",
            "value": None,
            "state": "suppressed",
            "raw_n": 1,
        },
    ]
    line = profile_bars.disposition_summary_line(rows)
    assert line == "Released 92.4%"


def test_disposition_stack_is_a_single_100_percent_stacked_bar():
    rows = [
        {
            "metric_id": "released",
            "label": "Released",
            "value": 0.9,
            "state": "stable",
            "raw_n": 500,
        },
        {
            "metric_id": "admitted",
            "label": "Admitted",
            "value": 0.1,
            "state": "stable",
            "raw_n": 50,
        },
    ]
    fig = profile_bars.build_disposition_stack(rows)
    ys = {y for tr in fig.data for y in tr.y}
    # One row, and the "Disposition" y label is dropped: the strip is 12px of bar, not a chart
    # with an axis category nobody needs to read.
    assert ys == {""}
    assert fig.layout.barmode == "stack"
    assert fig.layout.yaxis.showticklabels is False
    assert fig.layout.height <= 120


# ---------------------------------------------------------------------------
# representative_narratives (real committed data -- same convention as test_data_layer.py)
# ---------------------------------------------------------------------------


def test_representative_narratives_ordering_limit_confidence_and_spread():
    flt = CaseFilter(years=(2021, 2025), age_scope="adults", tools=("table_saw",))
    mechanisms = ("direct_cutting_contact", "kickback_ejection", "flying_debris_fb")
    df = data_module.representative_narratives(flt, "table_saw", mechanisms, 0.70, limit=5)
    assert 0 < df.height <= 5
    assert all(c >= 0.70 for c in df["confidence"].to_list())
    assert len(set(df["primary_mechanism"].to_list())) > 1

    rows = df.to_dicts()
    expected_order = sorted(rows, key=lambda r: (-r["confidence"], -r["year"], r["case_id"]))
    assert rows == expected_order


def test_representative_narratives_zero_mechanisms_returns_empty():
    flt = CaseFilter(years=(2021, 2025), age_scope="adults", tools=("table_saw",))
    df = data_module.representative_narratives(flt, "table_saw", (), 0.70, limit=5)
    assert df.height == 0


def test_representative_narratives_limit_respected():
    flt = CaseFilter(years=(2021, 2025), age_scope="adults", tools=("table_saw",))
    df = data_module.representative_narratives(
        flt, "table_saw", ("direct_cutting_contact",), 0.70, limit=2
    )
    assert df.height <= 2


# ---------------------------------------------------------------------------
# narrative card renderer: full text, no truncation, HTML-escaped
# ---------------------------------------------------------------------------


def test_narrative_card_full_text_not_truncated():
    long_narrative = "A" * 400
    row = {
        "narrative": long_narrative,
        "year": 2025,
        "age_bracket_label": "25-34",
        "diagnosis_label": "Laceration",
        "disposition_label": "Treated and released",
    }
    html_str = narrative_card_html(row)
    assert long_narrative in html_str


def test_narrative_card_escapes_html():
    row = {
        "narrative": "PT CUT <FINGER> & BLED",
        "year": 2025,
        "age_bracket_label": "25-34",
        "diagnosis_label": "Laceration",
        "disposition_label": "Treated and released",
    }
    html_str = narrative_card_html(row)
    assert "&lt;FINGER&gt;" in html_str
    assert "&amp;" in html_str
    assert "PT CUT <FINGER> & BLED" not in html_str


# ---------------------------------------------------------------------------
# median age KPI gate (raw N >= 100)
# ---------------------------------------------------------------------------


def test_median_age_card_omitted_below_raw_n_100():
    dummy_flt = CaseFilter(years=(2021, 2025), age_scope="adults", tools=("does_not_exist",))
    workshop_flt = CaseFilter(years=(2021, 2025), age_scope="adults")
    assert _median_age_card(dummy_flt, raw_n=99, workshop_flt=workshop_flt) is None


def test_median_age_card_present_at_raw_n_100():
    flt = CaseFilter(years=(2021, 2025), age_scope="adults", tools=("table_saw",))
    workshop_flt = CaseFilter(years=(2021, 2025), age_scope="adults")
    card = _median_age_card(flt, raw_n=100, workshop_flt=workshop_flt)
    assert card is not None
    assert card.label == "Median age"
    assert "median age of sample cases" in card.caption
