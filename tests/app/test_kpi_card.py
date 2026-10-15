"""KPI period-over-period comparison tests.

Two layers:

* `KpiCard`/`KpiDelta` rendering -- formatting, the colour-independent direction treatment,
  escaping, and the guarantee that a delta-less card's markup is byte-identical to what pre-delta
  call sites produced.
* `wii.app.data.gated_delta` -- the statistical gate. Synthetic `ToolCell`s pin the behaviour that
  matters most: a delta appears ONLY when both windows are stable and their 95% CIs are disjoint;
  every other case renders nothing at all.

The gate tests are synthetic on purpose (the committed dataset can change on a refresh); the few
tests that do touch real data assert the INVARIANT (delta present <=> intervals disjoint) rather
than a hard-coded number.
"""

from __future__ import annotations

import html

import pytest

import wii.app.data as data_module
from wii.analytics.insights import ToolCell, load_thresholds
from wii.app import filters
from wii.app.charts import trend
from wii.app.components.kpi_card import (
    DIRECTION_DOWN,
    DIRECTION_FLAT,
    UNIT_PERCENT,
    UNIT_PP,
    KpiCard,
    KpiDelta,
    render_kpi_strip,
    strip_columns,
)
from wii.metrics import CaseFilter

_LABEL = "2021–2025 vs 2016–2020"


class _MarkdownSpy:
    """Captures the single `st.markdown(...)` call `render_kpi_strip` makes."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []

    def __call__(self, body: str, unsafe_allow_html: bool = False) -> None:
        self.calls.append((body, unsafe_allow_html))

    @property
    def html(self) -> str:
        assert len(self.calls) == 1
        return self.calls[0][0]


def _render(cards: list[KpiCard], monkeypatch) -> str:
    spy = _MarkdownSpy()
    monkeypatch.setattr("wii.app.components.kpi_card.st.markdown", spy)
    render_kpi_strip(cards)
    assert spy.calls[0][1] is True
    return spy.html


def _cell(value, *, raw_n=500, stable=True, ci_low=None, ci_high=None) -> ToolCell:
    return ToolCell(
        tool_id="all",
        label="Workshop-wide",
        value=value,
        raw_n=raw_n,
        stable=stable,
        ci_low=ci_low,
        ci_high=ci_high,
    )


# ---------------------------------------------------------------------------
# Existing call sites keep working unchanged
# ---------------------------------------------------------------------------


def test_kpi_card_delta_defaults_to_none():
    """Every pre-existing construction form still works and opts into no comparison."""
    assert KpiCard(label="Estimated injuries", value="148,300").delta is None
    assert KpiCard("Estimated injuries", "148,300", "caption").delta is None
    assert KpiCard("Escalated-care share", "5.7%", "caption", True).delta is None


def test_delta_less_card_markup_is_unchanged(monkeypatch):
    """A card with no defensible comparison gains no element, no placeholder and no whitespace:
    the card markup is byte-for-byte what the component emitted before deltas existed. The strip
    wrapper carries only its column-count custom properties."""
    rendered = _render(
        [KpiCard(label="Tools analyzed", value="12", caption="in filter")], monkeypatch
    )
    assert rendered == (
        '<div class="wii-kpi-strip" style="--wii-kpi-cols: 1; --wii-kpi-cols-md: 1;">'
        '<div class="wii-kpi-card">'
        '<div class="wii-kpi-label">Tools analyzed</div>'
        '<div class="wii-kpi-value">12</div>'
        '<div class="wii-kpi-caption">in filter</div>'
        "</div>"
        "</div>"
    )
    assert "wii-kpi-delta" not in rendered


@pytest.mark.parametrize(
    ("n_cards", "expected"),
    [
        (1, (1, 1)),
        (3, (3, 3)),
        (4, (4, 2)),
        (5, (3, 3)),
        (6, (3, 3)),
        (7, (4, 2)),
        (8, (4, 2)),
    ],
)
def test_strip_columns_are_balanced(n_cards, expected):
    assert strip_columns(n_cards) == expected


@pytest.mark.parametrize("n_cards", range(1, 13))
def test_strip_never_orphans_one_card_when_a_balanced_split_exists(n_cards):
    """The Overview's 6 cards used to wrap 5 + 1 -- one card alone beside four empty slots. For
    any count, the chosen widths leave the last row at least half full."""
    desktop, tablet = strip_columns(n_cards)
    for cols in (desktop, tablet):
        remainder = n_cards % cols
        assert remainder == 0 or remainder * 2 >= cols, (n_cards, cols)
    assert desktop <= 4
    assert tablet <= 3


def test_strip_emits_its_column_counts(monkeypatch):
    rendered = _render([KpiCard(str(i), "1", "c") for i in range(6)], monkeypatch)
    assert rendered.startswith(
        '<div class="wii-kpi-strip" style="--wii-kpi-cols: 3; --wii-kpi-cols-md: 3;">'
    )


def test_caption_year_range_cannot_break_at_its_dash(monkeypatch):
    """ "2021–2025" wrapped as "2021–" / "2025" in a narrow card; word joiners forbid that break
    without changing the visible text."""
    rendered = _render([KpiCard("Share", "5%", "share of injuries, 2021–2025")], monkeypatch)
    assert "2021\u2060–\u20602025" in rendered
    assert rendered.replace("\u2060", "").count("2021–2025") == 1


def test_severe_card_without_delta_keeps_its_accent(monkeypatch):
    rendered = _render([KpiCard("Escalated-care share", "5.7%", "c", severe=True)], monkeypatch)
    # The severe accent rides the LABEL, not the digits: a coloured 34px number with no
    # comparator reads as an alarm rather than as an outcome class.
    assert (
        '<div class="wii-kpi-label" style="color: var(--wii-severe)">Escalated-care share</div>'
        in rendered
    )
    assert '<div class="wii-kpi-value">5.7%</div>' in rendered
    assert 'wii-kpi-value" style=' not in rendered
    assert "wii-kpi-delta" not in rendered


# ---------------------------------------------------------------------------
# Rendering the comparison indicator
# ---------------------------------------------------------------------------


def test_percentage_point_formatting():
    """A rate compares in PERCENTAGE POINTS -- never as a percent-change of a percent."""
    delta = KpiDelta(magnitude=4.629361921798825, unit=UNIT_PP, comparison_label=_LABEL)
    assert delta.formatted_magnitude == "+4.6 pts"
    assert delta.text == f"+4.6 pts, {_LABEL}"
    assert delta.aria_label == f"4.6 percentage points higher, {_LABEL}"


def test_negative_percentage_point_formatting():
    delta = KpiDelta(magnitude=-0.84, unit=UNIT_PP, comparison_label=_LABEL)
    assert delta.formatted_magnitude == "-0.8 pts"
    assert delta.direction == DIRECTION_DOWN
    assert delta.aria_label == f"0.8 percentage points lower, {_LABEL}"


def test_percent_formatting_for_count_estimates():
    delta = KpiDelta(magnitude=-12.37, unit=UNIT_PERCENT, comparison_label=_LABEL)
    assert delta.formatted_magnitude == "-12.4%"
    assert delta.text == f"-12.4%, {_LABEL}"
    assert delta.aria_label == f"12.4 percent lower, {_LABEL}"


def test_sub_display_precision_change_is_flat_not_signed():
    """A change that rounds to zero at display precision must not claim a direction the printed
    number does not show."""
    delta = KpiDelta(magnitude=0.02, unit=UNIT_PP, comparison_label=_LABEL)
    assert delta.formatted_magnitude == "0.0 pts"
    assert delta.direction == DIRECTION_FLAT
    assert delta.aria_label == f"unchanged, {_LABEL}"


def test_unknown_unit_is_rejected():
    with pytest.raises(ValueError):
        KpiDelta(magnitude=1.0, unit="ratio", comparison_label=_LABEL)


def test_delta_renders_glyph_sign_and_both_periods(monkeypatch):
    card = KpiCard(
        label="Laceration share",
        value="57.6%",
        caption="share of estimated U.S. ER-treated injuries",
        delta=KpiDelta(magnitude=4.63, unit=UNIT_PP, comparison_label=_LABEL),
    )
    rendered = _render([card], monkeypatch)
    assert 'class="wii-kpi-delta"' in rendered
    assert "▲" in rendered  # up arrow
    assert html.escape(f"+4.6 pts, {_LABEL}") in rendered
    # Sits between the value and the caption, never after it.
    assert rendered.index("wii-kpi-value") < rendered.index("wii-kpi-delta")
    assert rendered.index("wii-kpi-delta") < rendered.index("wii-kpi-caption")


def test_direction_never_relies_on_colour(monkeypatch):
    """Up and down deltas must render with IDENTICAL styling: the app may not imply that rising
    or falling estimated burden is good or bad news (a tool being "safer"/"more dangerous" is
    exactly the claim NEISS cannot support). Direction travels on the glyph, the sign and the
    spoken label instead."""
    up = _render([KpiCard("x", "y", delta=KpiDelta(4.6, UNIT_PP, _LABEL))], monkeypatch)
    down = _render([KpiCard("x", "y", delta=KpiDelta(-4.6, UNIT_PP, _LABEL))], monkeypatch)

    def style_of(markup: str) -> str:
        start = markup.index('class="wii-kpi-delta" style="') + len('class="wii-kpi-delta" style="')
        return markup[start : markup.index('"', start)]

    assert style_of(up) == style_of(down)
    for markup in (up, down):
        assert "green" not in markup.lower()
        assert "red" not in markup.lower()
        assert "--wii-severe" not in markup

    # The up/down markup differs ONLY by the three colour-independent direction channels:
    # the glyph, the sign, and the spoken "higher"/"lower" word. Nothing else -- no class, no
    # style, no accent -- changes with direction.
    def normalise(markup: str, glyph: str, signed: str, word: str) -> str:
        return markup.replace(glyph, "@").replace(signed, "#").replace(word, "~")

    assert normalise(up, "▲", "+4.6", "higher") == normalise(down, "▼", "-4.6", "lower")


def test_arrow_glyph_is_hidden_from_assistive_tech(monkeypatch):
    rendered = _render([KpiCard("x", "y", delta=KpiDelta(4.6, UNIT_PP, _LABEL))], monkeypatch)
    assert '<span aria-hidden="true">▲</span>' in rendered
    assert f'aria-label="{html.escape(f"4.6 percentage points higher, {_LABEL}")}"' in rendered


def test_delta_label_is_html_escaped(monkeypatch):
    rendered = _render(
        [KpiCard("x", "y", delta=KpiDelta(1.0, UNIT_PP, '<img src=x onerror="a">'))], monkeypatch
    )
    assert "<img" not in rendered
    assert "&lt;img" in rendered


# ---------------------------------------------------------------------------
# The statistical gate
# ---------------------------------------------------------------------------


def _gate(recent, prior, unit=UNIT_PP):
    return data_module.gated_delta(recent, prior, unit=unit, comparison_label=_LABEL)


def test_delta_renders_when_intervals_separate():
    recent = _cell(0.5764, ci_low=0.5642, ci_high=0.5885)
    prior = _cell(0.5301, ci_low=0.5168, ci_high=0.5433)
    delta = _gate(recent, prior)
    assert delta is not None
    assert delta.unit == UNIT_PP
    assert delta.magnitude == pytest.approx((0.5764 - 0.5301) * 100)
    assert delta.formatted_magnitude == "+4.6 pts"


def test_no_delta_when_intervals_overlap():
    """The single most important case: hand/finger share moved 0.8pp between the two real
    windows, but the intervals overlap, so the app says nothing."""
    recent = _cell(0.6398, ci_low=0.6272, ci_high=0.6525)
    prior = _cell(0.6482, ci_low=0.6376, ci_high=0.6587)
    assert _gate(recent, prior) is None


def test_no_delta_when_intervals_merely_touch():
    """Adjacent-but-touching intervals are an overlap, matching
    `insights._trend_candidates`' strict `>` test exactly."""
    recent = _cell(0.60, ci_low=0.55, ci_high=0.65)
    prior = _cell(0.50, ci_low=0.45, ci_high=0.55)
    assert _gate(recent, prior) is None
    # Nudging them apart is all it takes.
    assert _gate(_cell(0.60, ci_low=0.5501, ci_high=0.65), prior) is not None


def test_no_delta_when_recent_window_is_unstable():
    recent = _cell(0.5764, stable=False, ci_low=0.5642, ci_high=0.5885)
    prior = _cell(0.5301, ci_low=0.5168, ci_high=0.5433)
    assert _gate(recent, prior) is None


def test_no_delta_when_prior_window_is_unstable():
    recent = _cell(0.5764, ci_low=0.5642, ci_high=0.5885)
    prior = _cell(0.5301, stable=False, ci_low=0.5168, ci_high=0.5433)
    assert _gate(recent, prior) is None


def test_no_delta_below_min_raw_n():
    floor = load_thresholds().min_raw_n
    recent = _cell(0.5764, raw_n=floor - 1, ci_low=0.5642, ci_high=0.5885)
    prior = _cell(0.5301, raw_n=floor, ci_low=0.5168, ci_high=0.5433)
    assert _gate(recent, prior) is None
    assert _gate(_cell(0.5764, raw_n=floor, ci_low=0.5642, ci_high=0.5885), prior) is not None


def test_no_delta_when_a_confidence_interval_is_missing():
    recent = _cell(0.5764, ci_low=None, ci_high=None)
    prior = _cell(0.5301, ci_low=0.5168, ci_high=0.5433)
    assert _gate(recent, prior) is None


def test_no_delta_when_a_window_is_missing_or_valueless():
    prior = _cell(0.5301, ci_low=0.5168, ci_high=0.5433)
    assert _gate(None, prior) is None
    assert _gate(prior, None) is None
    assert _gate(_cell(None, ci_low=0.1, ci_high=0.2), prior) is None


def test_percent_unit_uses_relative_change_and_refuses_zero_baseline():
    recent = _cell(120000.0, ci_low=115000.0, ci_high=125000.0)
    prior = _cell(100000.0, ci_low=95000.0, ci_high=105000.0)
    delta = _gate(recent, prior, unit=UNIT_PERCENT)
    assert delta is not None
    assert delta.magnitude == pytest.approx(20.0)
    assert delta.formatted_magnitude == "+20.0%"

    zero_prior = _cell(0.0, ci_low=-1.0, ci_high=0.0)
    assert _gate(recent, zero_prior, unit=UNIT_PERCENT) is None


# ---------------------------------------------------------------------------
# Windows + wiring against the committed dataset
# ---------------------------------------------------------------------------


def test_comparison_windows_are_two_adjacent_post_schema_change_spans():
    """Both windows must sit entirely on or after the 2019 schema change.

    NEISS gained a second diagnosis slot in 2019 (Diagnosis_2 fill rate 0.000 for 2014-2018,
    ~0.20-0.25 from 2019), so every `diagnosis_any` metric steps up at that boundary for coding
    reasons alone -- laceration share on the ANY basis goes 50.8% (2018) to 56.9% (2019) while the
    PRIMARY basis holds flat. A window pair straddling 2019 produced the page's only delta,
    "Laceration share +4.6 pts", which measured the arrival of a database column. Two separated
    confidence intervals cannot detect a systematic artifact, so the boundary is a hard constraint
    rather than something the CI gate can be trusted to catch.
    """
    windows = data_module.comparison_windows()
    span = data_module._KPI_DELTA_WINDOW_YEARS
    assert windows is not None
    assert windows.recent[1] == data_module.available_years()[-1]
    assert windows.recent[1] - windows.recent[0] == span - 1
    assert windows.prior[1] - windows.prior[0] == span - 1
    assert windows.prior[1] == windows.recent[0] - 1
    assert windows.prior[0] >= trend.SCHEMA_CHANGE_YEAR
    assert windows.label == (
        f"{windows.recent[0]}–{windows.recent[1]} vs {windows.prior[0]}–{windows.prior[1]}"
    )


def test_no_delta_when_the_cards_period_is_not_the_recent_window():
    """A delta under a number computed over a different period would be mislabelled by
    construction, so none is offered."""
    windows = data_module.comparison_windows()
    off_window = CaseFilter(years=(windows.recent[0] + 1, windows.recent[1]), age_scope="adults")
    assert data_module.burden_window_delta(off_window) is None
    assert data_module.share_window_delta(off_window, "laceration") is None


@pytest.mark.parametrize("metric_id", ["hand_finger", "laceration", "escalated_care"])
def test_share_delta_presence_matches_interval_separation(metric_id):
    """Invariant, not a hard-coded number: the delta exists exactly when the two windows' share
    intervals are disjoint (and both stable)."""
    windows = data_module.comparison_windows()
    flt = CaseFilter(years=windows.recent, age_scope="adults")
    recent = data_module._share_window_cell(flt, windows.recent, metric_id)
    prior = data_module._share_window_cell(flt, windows.prior, metric_id)
    expected = (
        recent.stable
        and prior.stable
        and None not in (recent.ci_low, recent.ci_high, prior.ci_low, prior.ci_high)
        and (recent.ci_low > prior.ci_high or prior.ci_low > recent.ci_high)
    )
    delta = data_module.share_window_delta(flt, metric_id)
    assert (delta is not None) == expected
    if delta is not None:
        assert delta.magnitude == pytest.approx((recent.value - prior.value) * 100)


def test_overview_kpi_strip_renders_only_defensible_deltas(app_test):
    """End-to-end: the Overview strip carries a delta for every share whose windows separate, and
    none for the cards where a comparison is not defensible."""
    from .conftest import APP_TIMEOUT

    at = app_test
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    # `<style>` blocks name these classes too, so the theme injection is excluded -- otherwise
    # "the hero class appears once" would be counting a CSS rule, not a card.
    kpi_html = "".join(
        v.value for v in at.markdown if "wii-kpi-strip" in v.value and "<style>" not in v.value
    )
    assert kpi_html

    # The strip is rendered at the app's DEFAULT filters, so the comparison is offered only when
    # the default year range happens to equal the comparison window. It does not: the default is
    # the latest five years, while the comparison window is the latest three years that sit after
    # the 2019 schema change. A card's headline number and its delta must describe the same
    # period, so the honest result at defaults is no delta at all -- the delta appears once a user
    # narrows to the comparable window. Asserting against the page's own filter (not a
    # hand-built one) is what keeps this test describing what a reader actually sees.
    default_flt = filters.current_filter()
    for label, metric_id in (
        ("Hand/finger share", "hand_finger"),
        ("Laceration share (any diagnosis)", "laceration"),
        ("Escalated-care share", "escalated_care"),
    ):
        card = kpi_html.split(label)[1].split("</div></div>")[0]
        delta = data_module.share_window_delta(default_flt, metric_id)
        if delta is None:
            assert "wii-kpi-delta" not in card, f"{label} rendered an indefensible delta"
        else:
            assert html.escape(delta.text) in card, f"{label} dropped a defensible delta"

    # Neither dataset metadata nor a raw category count belongs in a KPI slot at all. "Tools
    # analyzed" is a count of sampling coverage with no interval to compare against and "Latest
    # data year" duplicates the footer; both are now scope facts in the title block, which is why
    # the strip is four cards and neither label appears in it.
    for label in ("Tools analyzed", "Latest data year"):
        assert label not in kpi_html

    # Exactly one card is the hero, and it is the headline estimate.
    assert kpi_html.count("wii-kpi-hero") == 1
    hero = kpi_html.split("wii-kpi-hero")[1].split("</div></div>")[0]
    assert "Estimated injuries" in hero


# ---------------------------------------------------------------------------
# One formatting layer
# ---------------------------------------------------------------------------


def test_points_suffix_is_the_one_the_chart_layer_writes():
    """Consistent number formatting is a product requirement, not a detail: a points delta must
    be lettered identically whether it lands on a KPI card, a bar label or a table cell. This
    pins the card's suffix to `charts.marks.fmt_points` so the two can never drift apart."""
    from wii.app.charts import marks
    from wii.app.components.kpi_card import _POINTS_SUFFIX

    assert marks.fmt_points(0.032) == f"+3.2{_POINTS_SUFFIX}"
    assert KpiDelta(magnitude=3.2, unit=UNIT_PP, comparison_label=_LABEL).formatted_magnitude == (
        marks.fmt_points(0.032)
    )


def test_a_rate_is_never_compared_as_a_percent_change_of_a_percent():
    """The unit is points, and the spoken form says so. A share moving 5.7% -> 5.3% is "-0.4 pts",
    never "-7%": the latter reads as a percentage of the page's own percentage, and it turns a
    fractional move on a small base into a headline."""
    delta = KpiDelta(magnitude=-0.4, unit=UNIT_PP, comparison_label=_LABEL)
    assert delta.formatted_magnitude.endswith(" pts")
    assert "%" not in delta.formatted_magnitude
    assert "percentage points" in delta.aria_label


def test_direction_is_carried_without_colour(monkeypatch):
    """Accessibility floor and honesty rule at once: up and down render byte-identically apart
    from the glyph and the sign, so no reader depends on colour to know which way a figure moved
    -- and the product never asserts that a move is good or bad news, which NEISS cannot support
    without an exposure denominator."""
    up = _render([KpiCard(label="m", value="1", delta=KpiDelta(2.0, UNIT_PP, _LABEL))], monkeypatch)
    down = _render(
        [KpiCard(label="m", value="1", delta=KpiDelta(-2.0, UNIT_PP, _LABEL))], monkeypatch
    )

    def normalise(text: str) -> str:
        for direction, neutral in (
            ("▲", "G"),
            ("▼", "G"),
            ("+2.0", "N"),
            ("-2.0", "N"),
            ("higher", "W"),
            ("lower", "W"),
        ):
            text = text.replace(direction, neutral)
        return text

    # Identical once the glyph, the sign and the direction WORD are neutralised: those three
    # colour-independent channels are the only thing that differs. No style, class or colour does.
    assert normalise(up) == normalise(down)
    assert "higher" in up and "lower" in down
    # ...and the one colour the delta line does carry is the neutral caption ink, not a valence.
    style = up.split('wii-kpi-delta" style="')[1].split('"')[0]
    assert "var(--wii-muted)" in style
    for valence in ("green", "red", "--wii-severe", "--wii-redline", "--wii-blueprint"):
        assert valence not in style
