"""D-14 AppTest smoke tests. Plan 03-01 Task 2 covers the default-filter tracer path; Task 3
adds a non-default filter and a zero-case filter once the sidebar exists. Plan 06-01 Task 1 adds
test_every_registered_page_renders_with_default_filters, parametrized over PAGE_SPECS (D-03).
"""

from __future__ import annotations

import re

import pytest

import wii.app.data as data_module
from wii.app import filters as filters_module
from wii.app.components.body_map import BodyScale, RegionValue
from wii.app.components.footer import source_line
from wii.app.components.stability_badge import STATE_STABLE, resolve
from wii.app.data import latest_data_year
from wii.app.pages.comparison import _shared_anatomy_scale
from wii.app.pages_registry import PAGE_SPECS
from wii.metrics import CaseFilter

from .conftest import APP_TIMEOUT

_TOOL_PROFILES_PATH = next(s.file for s in PAGE_SPECS if s.url_path == "tool-profiles")
_COMPARISON_PATH = next(s.file for s in PAGE_SPECS if s.url_path == "comparison")

# Four cards, all of them estimates. "Tools analyzed" (a count of sampling coverage with no
# interval) and "Latest data year" (dataset metadata, already in the footer) are scope facts and
# now live in the title block instead of occupying a third of the strip.
_D09_KPI_LABELS = (
    "Estimated injuries",
    "Hand/finger share",
    "Laceration share",
    "Escalated-care share",
)

_D09_SECTION_HEADINGS = (
    "Tool injury landscape",
    "Top tools by estimated ER burden",
    "Injury type mix",
    "What the data is saying",
)


def _assert_section_headings_present(at):
    headers = [h.value for h in at.header]
    for heading in _D09_SECTION_HEADINGS:
        assert any(heading in h for h in headers), f"missing section heading {heading!r}"


def test_overview_default(app_test):
    at = app_test
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    titles = [t.value for t in at.title]
    assert any("What sends workshop users to the ER?" in t for t in titles)

    md_values = [m.value for m in at.markdown]
    expected_source = source_line(latest_data_year())
    matches = [v for v in md_values if expected_source in v]
    assert len(matches) == 1, f"expected exactly one source line, found {len(matches)}: {matches}"

    kpi_html = "".join(v for v in md_values if "wii-kpi-strip" in v)
    for label in _D09_KPI_LABELS:
        assert label in kpi_html, f"missing KPI label {label!r} in rendered KPI strip"

    _assert_section_headings_present(at)


def test_overview_nondefault_filter(app_test):
    """A 2-year span + All ages is NOT the default (latest-5, Adults 18+) view -- exercises the
    cached_estimate_total query path instead of the agg-table default-view path.
    """
    at = app_test
    at.session_state["year_range"] = (2024, 2025)
    at.session_state["age_scope"] = "all"
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    md_values = [m.value for m in at.markdown]
    expected_source = source_line(latest_data_year())
    matches = [v for v in md_values if expected_source in v]
    assert len(matches) == 1

    _assert_section_headings_present(at)


def _switch_to_tool_profiles(at):
    at.run(timeout=APP_TIMEOUT)
    at.switch_page(_TOOL_PROFILES_PATH)


def test_tool_profiles_default(app_test):
    at = app_test
    _switch_to_tool_profiles(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    titles = [t.value for t in at.title]
    assert any("Injury Profile" in t for t in titles)

    sb = at.selectbox(key="profile_selected_tool")
    assert len(sb.options) == 31

    # D-10 backstop: the default selection is computed (highest-burden stable tool under the
    # active workshop-wide filter), never hard-coded.
    years = filters_module._default_year_range()
    default_flt = CaseFilter(years=years, age_scope="adults")
    expected_default = None
    best_value = None
    for row in data_module.cached_estimate_by(default_flt, "tool").iter_rows(named=True):
        resolved = resolve(row, "annual_average")
        if resolved.state != STATE_STABLE or resolved.value is None:
            continue
        if best_value is None or resolved.value > best_value:
            best_value = resolved.value
            expected_default = row["group_id"]
    assert sb.value == expected_default

    # D-03: the global tool multiselect is hidden on Tool Profiles (this page owns its own).
    assert all(ms.key != "widget_global_tool_filter" for ms in at.multiselect)

    headers = [h.value for h in at.header]
    assert any("Anatomy snapshot" in h for h in headers)
    md_values = [m.value for m in at.markdown]
    assert any("wii-body-map" in v for v in md_values)


def test_tool_profiles_nondefault(app_test):
    """Selects a narrative-refined tool with age_scope "all" -- exercises the non-default query
    path and the "(narrative-refined)" selector suffix (D-10).

    The tool must be one with a REPORTABLE estimate, or the page correctly renders the
    unreportable state instead of these sections (see
    `test_tool_profiles_unreportable_tool_states_the_reason`). Angle Grinder, which this test used
    to select, has 84 sample cases and no publishable estimate.
    """
    at = app_test
    _switch_to_tool_profiles(at)
    at.session_state["profile_selected_tool"] = "miter_saw"
    at.session_state["age_scope"] = "all"
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    titles = [t.value for t in at.title]
    assert any("Miter Saw Injury Profile" in t for t in titles)

    sb = at.selectbox(key="profile_selected_tool")
    assert sb.value == "miter_saw"

    # Task 3: INJURY TREND, ANATOMY SNAPSHOT and REPRESENTATIVE CASE PATTERNS all render.
    headers = [h.value for h in at.header]
    for heading in ("Injury trend", "Anatomy snapshot", "Representative case patterns"):
        assert any(heading in h for h in headers), f"missing section heading {heading!r}"


def test_tool_profiles_unreportable_tool_states_the_reason(app_test):
    """A tool whose own estimate is suppressed gets one explanation, not a dashboard of "Not
    reportable".

    Shaper (9 sample cases in 20 years) used to draw eight blank KPIs, four empty charts and a
    body plate captioned "22 of 22 body regions are below the reporting floor".
    """
    at = app_test
    _switch_to_tool_profiles(at)
    at.session_state["profile_selected_tool"] = "shaper"
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    subheaders = [s.value for s in at.subheader]
    assert any("No reportable estimate" in s for s in subheaders), subheaders

    # None of the sections that would have been all-"Not reportable" are drawn.
    headers = [h.value for h in at.header]
    for heading in ("Injury trend", "Anatomy snapshot", "Representative case patterns"):
        assert not any(heading in h for h in headers), f"unreportable page still drew {heading!r}"

    body = " ".join(m.value for m in at.markdown)
    assert "sample cases" in body
    # The absence is never allowed to read as a safety claim.
    assert "not** a finding that this tool is safer" in body


def test_tool_profiles_selector_marks_tools_with_no_estimate(app_test):
    """All 31 tools stay selectable; the ones with no reportable estimate say so (D-10)."""
    at = app_test
    _switch_to_tool_profiles(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    sb = at.selectbox(key="profile_selected_tool")
    assert len(sb.options) == 31
    labels = [sb.format_func(tid) for tid in sb.options]
    assert any("no reportable estimate" in label for label in labels), labels
    # A tool that does clear the floor is never marked.
    assert "no reportable estimate" not in sb.format_func("table_saw")


def _switch_to_comparison(at):
    at.run(timeout=APP_TIMEOUT)
    at.switch_page(_COMPARISON_PATH)


def _expected_default_comparison_tools() -> list[str]:
    years = filters_module._default_year_range()
    default_flt = CaseFilter(years=years, age_scope="adults")
    scored = []
    for row in data_module.cached_estimate_by(default_flt, "tool").iter_rows(named=True):
        resolved = resolve(row, "annual_average")
        if resolved.state != STATE_STABLE or resolved.value is None:
            continue
        scored.append((resolved.value, row["group_id"]))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [tool_id for _, tool_id in scored[:4]]


def test_comparison_default(app_test):
    at = app_test
    _switch_to_comparison(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    titles = [t.value for t in at.title]
    assert any("Every tool has an injury fingerprint" in t for t in titles)

    ms = at.multiselect(key="comparison_selected_tools")
    assert list(ms.value) == _expected_default_comparison_tools()
    assert len(ms.value) == 4

    # The comparison strip is gone: its nine numbers per tool were printed again, verbatim, as
    # in-cell text in the fingerprint directly below it -- the same 36 values twice within one
    # screen.
    headers = [h.value for h in at.header]
    for heading in (
        "Injury fingerprint",
        "Anatomy signature",
        "How they differ",
    ):
        assert any(heading in h for h in headers), f"missing section heading {heading!r}"


def test_comparison_one_tool(app_test):
    """Fewer than 2 selected tools -- D-11 bound: renders the exact empty-state copy, no
    comparison sections."""
    at = app_test
    _switch_to_comparison(at)
    tools = data_module.load_dims()["tools"]["tool_id"].to_list()
    at.session_state["comparison_selected_tools"] = [tools[0]]
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    info_values = [i.value for i in at.info]
    assert any(
        "Select at least 2 tools to compare." in v
        and "Choose 2 to 6 tools from the list above" in v
        for v in info_values
    )
    headers = [h.value for h in at.header]
    assert all("Comparison strip" not in h for h in headers)


def test_comparison_seven_tools(app_test):
    """More than 6 selected tools -- D-11 bound: renders the exact over-limit copy with the
    correct removal count, no comparison sections."""
    at = app_test
    _switch_to_comparison(at)
    tools = data_module.load_dims()["tools"]["tool_id"].to_list()[:7]
    at.session_state["comparison_selected_tools"] = tools
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    info_values = [i.value for i in at.info]
    assert any(
        "Select up to 6 tools" in v and "Remove 1 tool(s) to continue." in v for v in info_values
    )
    headers = [h.value for h in at.header]
    assert all("Comparison strip" not in h for h in headers)


def test_comparison_shared_anatomy_scale_uses_max_across_all_tools():
    """D-11/D-12 hard requirement: the small-multiple bodies' shared `BodyScale` domain is
    computed from the MAX region share across ALL selected tools, not autoscaled per tool."""
    region_ids = {"hand", "finger"}
    values_by_tool = {
        "table_saw": {
            "hand": RegionValue(
                label="Hand", value=0.5, display_value="50.0%", state="stable", raw_n=200
            ),
            "finger": RegionValue(
                label="Finger", value=0.1, display_value="10.0%", state="stable", raw_n=200
            ),
        },
        "hand_plane": {
            "hand": RegionValue(
                label="Hand", value=0.2, display_value="20.0%", state="stable", raw_n=200
            ),
            "finger": RegionValue(
                label="Finger", value=0.9, display_value="90.0%", state="stable", raw_n=200
            ),
        },
    }
    scale = _shared_anatomy_scale(values_by_tool, region_ids)
    assert scale.vmax == 0.9  # driven by hand_plane's finger value, not table_saw's own max (0.5)

    # A per-tool-autoscaled implementation would build table_saw's own BodyScale from ITS max
    # (0.5), mapping table_saw's hand value (0.5) to the scale's top color -- the shared scale
    # must NOT do this, proving the domain genuinely comes from all tools, not just this one.
    independent_table_saw_scale = BodyScale(vmin=0.0, vmax=0.5)
    assert scale.color_for(0.5) != independent_table_saw_scale.color_for(0.5)


def test_overview_zero_case_filter(app_test):
    """poisoning + toe is a real, valid advanced-filter combination with zero matching cases in
    the committed data (verified live against fact_cases this session) -- must show the
    empty-state text, never an exception, with the footer still present exactly once. A year
    range outside the slider's min/max cannot be used here: the sidebar slider itself raises
    StreamlitValueBelowMinError for an out-of-bounds pre-seeded value, so the zero-match
    condition must come from a within-bounds advanced-filter combination instead.
    """
    at = app_test
    at.session_state["f_diagnosis_categories"] = ("poisoning",)
    at.session_state["f_body_regions"] = ("toe",)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    info_values = [i.value for i in at.info]
    assert any("No cases match the current filters" in v for v in info_values)

    md_values = [m.value for m in at.markdown]
    expected_source = source_line(latest_data_year())
    matches = [v for v in md_values if expected_source in v]
    assert len(matches) == 1


@pytest.mark.parametrize("spec", PAGE_SPECS, ids=lambda s: s.slug)
def test_every_registered_page_renders_with_default_filters(app_test, spec):
    """D-03: driven from PAGE_SPECS, not a hand-maintained list -- adding a page to the registry
    automatically adds it here. Runs against the committed data/processed/, no data setup needed.
    """
    at = app_test
    at.run(timeout=APP_TIMEOUT)
    if not spec.is_default:
        at.switch_page(spec.file)
        at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    titles = [t.value for t in at.title]
    assert len(titles) >= 1, f"page {spec.slug!r} rendered no title element"


_GLOBAL_TOOL_WIDGET_KEY = "widget_global_tool_filter"


@pytest.mark.parametrize("spec", PAGE_SPECS, ids=lambda s: s.slug)
def test_no_filter_duplication_for_global_tool_widget(app_test, spec):
    """UX checklist #6 (filter duplication): the global sidebar tool multiselect is absent when
    the page's own spec sets hide_global_tool_filter=True, and present otherwise -- a page must
    never show both its own tool selector and the global one at the same time.
    """
    at = app_test
    at.run(timeout=APP_TIMEOUT)
    if not spec.is_default:
        at.switch_page(spec.file)
        at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    widget_keys = [ms.key for ms in at.multiselect]
    if spec.hide_global_tool_filter:
        assert _GLOBAL_TOOL_WIDGET_KEY not in widget_keys, (
            f"page {spec.slug!r} sets hide_global_tool_filter=True but the global tool widget "
            "is still rendered"
        )
    else:
        assert _GLOBAL_TOOL_WIDGET_KEY in widget_keys, (
            f"page {spec.slug!r} sets hide_global_tool_filter=False but the global tool widget "
            "is missing"
        )


# ---------------------------------------------------------------------------
# Internal-process leakage
# ---------------------------------------------------------------------------

#: Plan and requirement identifiers. These are cross-references into the planning directory with
#: no meaning whatsoever to a visitor, and three page captions shipped ending "...never padded
#: with weaker statements (D-13)." -- visible in the committed desktop captures for Overview,
#: Tool Profiles and Comparison. This is the regression guard for the whole class, not that one
#: string: any future caption that quotes a decision id fails here before it reaches a reader.
PLAN_ID_PATTERNS = (
    re.compile(r"\(D-\d+\)"),
    re.compile(r"\bINS-\d+\b"),
    re.compile(r"\bVAL-\d+\b"),
    re.compile(r"\bWave-0\b"),
    re.compile(r"\bT-\d{2}-\d{2}\b"),
    re.compile(r"\.planning/"),
)


def _rendered_strings(at):
    """Every string this page actually puts in front of a reader: headings, body copy, captions,
    raw-HTML blocks, widget labels and their `help=` tooltips, alert boxes and table text."""
    out: list[str] = []
    for group in (
        at.title,
        at.header,
        at.subheader,
        at.markdown,
        at.caption,
        at.info,
        at.warning,
        at.error,
        at.success,
    ):
        out.extend(el.value for el in group if isinstance(el.value, str))
    for group in (at.selectbox, at.multiselect, at.radio, at.slider, at.button, at.toggle):
        for el in group:
            out.append(getattr(el, "label", "") or "")
            out.append(getattr(el, "help", "") or "")
    return out


@pytest.mark.parametrize("spec", PAGE_SPECS, ids=lambda s: s.url_path)
def test_no_page_renders_a_planning_identifier(app_test, spec):
    at = app_test
    at.run(timeout=APP_TIMEOUT)
    at.switch_page(spec.file)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception
    for text in _rendered_strings(at):
        for pattern in PLAN_ID_PATTERNS:
            assert not pattern.search(text), (
                f"{spec.url_path} renders the planning identifier "
                f"{pattern.search(text).group(0)!r} in: {text[:160]!r}"
            )


# ---------------------------------------------------------------------------
# Sentence case
# ---------------------------------------------------------------------------

#: The title block is the ONE deliberate caps exception: it appears once per page and quotes a
#: real drafting artifact. Everything else is sentence case: a tracked-out all-caps label
#: repeated above every card or section is the commonest tell of a generated page, and three caps
#: headings on My Shop plus fifteen caps tier eyebrows on Signals were exactly that.
_TITLE_BLOCK_KEYS = frozenset({"DRAWING", "BASIS", "SCOPE", "SOURCE"})
_CAPS_RUN = re.compile(r"\b[A-Z][A-Z]+(?:[ -][A-Z]+)*\b")
#: Acronyms and dataset names are not shouting.
_ALLOWED_CAPS = frozenset(
    {"NEISS", "CPSC", "ER", "US", "USA", "AI", "CV", "CI", "QA", "SVG", "WEBGL", "PSU", "PSUS"}
)


@pytest.mark.parametrize("spec", PAGE_SPECS, ids=lambda s: s.url_path)
def test_no_page_heading_is_written_in_all_caps(app_test, spec):
    at = app_test
    at.run(timeout=APP_TIMEOUT)
    at.switch_page(spec.file)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception
    for element in [*at.title, *at.header, *at.subheader]:
        for run in _CAPS_RUN.findall(element.value):
            words = [w for w in run.replace("-", " ").split() if w]
            if all(w in _ALLOWED_CAPS or w in _TITLE_BLOCK_KEYS for w in words):
                continue
            raise AssertionError(
                f"{spec.url_path or 'overview'} renders an all-caps heading: "
                f"{run!r} in {element.value!r}"
            )
