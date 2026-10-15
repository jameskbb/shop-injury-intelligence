"""PAGE-07 Case Explorer tests (D-07..D-12, D-17): search-term parsing, the safe highlight
algorithm, LIKE-wildcard escaping, committed-data count/page correctness, filter predicates,
pagination boundaries, the high-confidence toggle, the zero-results state, and the "no weighted
estimate reaches this page" boundary (T-05-01..T-05-09).
"""

from __future__ import annotations

import ast
import time

import wii.app.data as data_module
from wii.app.components.pagination import PageWindow, page_window
from wii.app.components.search_highlight import (
    MAX_QUERY_CHARS,
    MAX_TERMS,
    highlight_narrative,
    parse_search_terms,
)
from wii.metrics import CaseFilter
from wii.utils.paths import repo_root

from .conftest import APP_TIMEOUT

_CASE_EXPLORER_PATH = "src/wii/app/pages/case_explorer.py"


def _switch_to_case_explorer(at):
    at.run(timeout=APP_TIMEOUT)
    at.switch_page(_CASE_EXPLORER_PATH)


# ---------------------------------------------------------------------------
# parse_search_terms
# ---------------------------------------------------------------------------


def test_parse_search_terms_whitespace_split_and_case_insensitive_dedupe():
    assert parse_search_terms("  Blade   GUARD blade ") == ("Blade", "GUARD")


def test_parse_search_terms_truncates_chars_and_caps_terms():
    words = " ".join(f"w{i}" for i in range(7))
    long_text = (words + "x" * 200)[: MAX_QUERY_CHARS + 50]
    terms = parse_search_terms(long_text)
    assert len(terms) <= MAX_TERMS
    # A 150-char string uses only its first 100 characters.
    raw_150 = ("word " * 40)[:150]
    assert parse_search_terms(raw_150) == parse_search_terms(raw_150[:MAX_QUERY_CHARS])


def test_parse_search_terms_seven_words_yields_first_five():
    raw = " ".join(f"term{i}" for i in range(7))
    terms = parse_search_terms(raw)
    assert terms == tuple(f"term{i}" for i in range(5))


def test_parse_search_terms_empty_and_whitespace_only_yield_empty_tuple():
    assert parse_search_terms("") == ()
    assert parse_search_terms("   ") == ()
    assert parse_search_terms(None) == ()


# ---------------------------------------------------------------------------
# highlight_narrative -- D-09 corrected order, XSS/entity safety
# ---------------------------------------------------------------------------


def test_highlight_narrative_escapes_script_tag_in_narrative():
    out = highlight_narrative("PT <script>alert(1)</script> CUT FINGER", ("finger",))
    assert "&lt;script&gt;" in out
    assert "<mark>FINGER</mark>" in out
    assert "<script" not in out


def test_highlight_narrative_amp_term_does_not_split_entity():
    out = highlight_narrative("CUT ON R&D SAW", ("amp",))
    assert out == "CUT ON R&amp;D SAW"


def test_highlight_narrative_lt_and_amp_terms_escaped_and_marked():
    out = highlight_narrative("A<B & C", ("<b", "&"))
    assert out == "A<mark>&lt;B</mark> <mark>&amp;</mark> C"


def test_highlight_narrative_merges_overlapping_spans():
    out = highlight_narrative("SAW BLADE", ("saw", "aw b"))
    assert out.count("<mark>") == 1


def test_highlight_narrative_terms_are_literal_never_regex():
    out = highlight_narrative("some text with a plus sign + here", ("(a+)+$", ".*"))
    assert "<mark>" not in out


def test_highlight_narrative_empty_terms_returns_escaped_narrative():
    assert highlight_narrative("A & B", ()) == "A &amp; B"


def test_highlight_narrative_none_narrative_returns_empty_string():
    assert highlight_narrative(None, ("x",)) == ""


# ---------------------------------------------------------------------------
# _like_term / _search_clause
# ---------------------------------------------------------------------------


def test_like_term_escapes_backslash_percent_underscore():
    assert data_module._like_term("50%_off\\x") == "%50\\%\\_off\\\\x%"


def test_search_clause_joins_n_copies_with_and():
    clause = data_module._search_clause(3)
    assert clause.count("ILIKE ? ESCAPE '\\'") == 3
    assert clause.count(" AND ") == 2


# ---------------------------------------------------------------------------
# Committed-data correctness (Pitfall 2: no is_primary-only join loses cases)
# ---------------------------------------------------------------------------


def test_case_explorer_count_unfiltered_equals_fact_cases_live_count():
    con = data_module.get_connection()
    live_total = con.execute("SELECT COUNT(*) FROM fact_cases").fetchone()[0]
    flt = CaseFilter(years=None, age_scope="all")
    assert data_module.case_explorer_count(flt, ()) == live_total


def test_case_explorer_count_percent_search_matches_literal_percent_only():
    con = data_module.get_connection()
    direct = con.execute(
        "SELECT COUNT(*) FROM fact_cases WHERE instr(narrative, '%') > 0"
    ).fetchone()[0]
    flt = CaseFilter(years=None, age_scope="all")
    percent_count = data_module.case_explorer_count(flt, ("%",))
    unfiltered_count = data_module.case_explorer_count(flt, ())
    assert percent_count == direct
    assert percent_count < unfiltered_count


def test_case_explorer_page_shape_no_age_columns_unique_case_ids_ordered():
    flt = CaseFilter(years=None, age_scope="all")
    df = data_module.case_explorer_page(flt, (), page=1)
    assert df.height <= data_module.CASE_EXPLORER_PAGE_SIZE
    assert df["case_id"].n_unique() == df.height
    assert "age_years" not in df.columns
    assert "age_raw" not in df.columns
    years = df["year"].to_list()
    assert years == sorted(years, reverse=True)


def test_case_explorer_page_multi_tool_case_lists_both_tool_labels():
    con = data_module.get_connection()
    multi = con.execute(
        "SELECT case_id FROM bridge_case_products WHERE in_scope "
        "GROUP BY case_id HAVING COUNT(DISTINCT final_tool_id) > 1 LIMIT 1"
    ).fetchone()
    assert multi is not None, "expected at least one multi-tool case in committed data"
    case_id = multi[0]
    tool_labels = con.execute(
        "SELECT DISTINCT dt.label FROM bridge_case_products bcp "
        "JOIN dim_tool dt ON dt.tool_id = bcp.final_tool_id "
        "WHERE bcp.case_id = ? AND bcp.in_scope",
        [case_id],
    ).fetchall()
    assert len(tool_labels) > 1
    flt = CaseFilter(years=None, age_scope="all")
    df = data_module.case_explorer_page(flt, (), page=1)
    # Find the row for this case via a direct single-case query (avoids depending on page order).
    row = con.execute(data_module._CASE_EXPLORER_PAGE_SQL_PREFIX + "fc.case_id = ?", [case_id]).pl()
    del df
    assert row.height == 1
    label = row["tool_label"][0]
    for (expected_label,) in tool_labels:
        assert expected_label in label


# ---------------------------------------------------------------------------
# pagination
# ---------------------------------------------------------------------------


def test_page_window_boundaries():
    assert page_window(0, 1, 25) == PageWindow(page=1, n_pages=1, offset=0)
    assert page_window(25, 1, 25).n_pages == 1
    assert page_window(26, 2, 25) == PageWindow(2, 2, 25)
    assert page_window(26, 99, 25).page == 2
    assert page_window(26, 0, 25).page == 1


# ---------------------------------------------------------------------------
# AppTest smoke
# ---------------------------------------------------------------------------


def test_case_explorer_default(app_test):
    at = app_test
    _switch_to_case_explorer(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    titles = [t.value for t in at.title]
    assert any("Read what happened" in t for t in titles)

    con = data_module.get_connection()
    from wii.app import filters as filters_module

    years = filters_module._default_year_range()
    live_total = con.execute(
        "SELECT COUNT(*) FROM fact_cases WHERE year BETWEEN ? AND ? AND is_adult = TRUE",
        list(years),
    ).fetchone()[0]
    subheaders = [s.value for s in at.subheader]
    expected = f"{live_total:,} matching sample cases (not a national estimate)"
    assert any(expected in s for s in subheaders)

    assert all(ms.key != "widget_global_tool_filter" for ms in at.multiselect)


def test_case_explorer_search_highlights(app_test):
    at = app_test
    _switch_to_case_explorer(at)
    at.run(timeout=APP_TIMEOUT)
    at.session_state["ce_search"] = "kickback"
    at.session_state["ce_page"] = 1
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    con = data_module.get_connection()
    from wii.app import filters as filters_module

    years = filters_module._default_year_range()
    default_total = con.execute(
        "SELECT COUNT(*) FROM fact_cases WHERE year BETWEEN ? AND ? AND is_adult = TRUE",
        list(years),
    ).fetchone()[0]

    subheaders = [s.value for s in at.subheader]
    matching = [s for s in subheaders if "matching sample cases" in s]
    assert len(matching) == 1
    searched_total = int(matching[0].split(" ")[0].replace(",", ""))
    assert searched_total < default_total

    md_values = [m.value for m in at.markdown]
    case_list_html = "".join(v for v in md_values if v.startswith('<div class="wii-case-list">'))
    if searched_total > 0:
        assert "<mark>" in case_list_html


def test_case_explorer_pagination_boundaries(app_test):
    at = app_test
    _switch_to_case_explorer(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception
    prev_buttons = [b for b in at.button if b.key == "ce_prev"]
    if prev_buttons:
        assert prev_buttons[0].disabled

    at.session_state["ce_page"] = 999999
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception
    next_buttons = [b for b in at.button if b.key == "ce_next"]
    captions = [c.value for c in at.caption]
    page_captions = [c for c in captions if c.startswith("Page ")]
    if next_buttons:
        assert next_buttons[0].disabled
        assert len(page_captions) >= 1
        n = page_captions[0].split(" of ")[-1]
        assert page_captions[0] == f"Page {n} of {n}"


def test_case_explorer_zero_results_shows_zero_results_copy(app_test):
    at = app_test
    _switch_to_case_explorer(at)
    at.run(timeout=APP_TIMEOUT)
    at.session_state["ce_search"] = "zzqxqzzqnonexistentterm"
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    subheaders = [s.value for s in at.subheader]
    assert any(s.startswith("0 matching sample cases") for s in subheaders)

    info_values = [i.value for i in at.info]
    from wii.app.pages.case_explorer import ZERO_RESULTS_COPY

    assert any(ZERO_RESULTS_COPY in v for v in info_values)

    md_values = [m.value for m in at.markdown]
    assert not any(v.startswith('<div class="wii-case-list">') for v in md_values)
    assert not any(b.key in ("ce_prev", "ce_next") for b in at.button)


def test_case_explorer_high_confidence_toggle_restricts_and_badges(app_test):
    at = app_test
    _switch_to_case_explorer(at)
    at.run(timeout=APP_TIMEOUT)
    subheaders = [s.value for s in at.subheader]
    default_total = int(
        next(s for s in subheaders if "matching sample cases" in s).split(" ")[0].replace(",", "")
    )

    at.session_state["ce_high_conf"] = True
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception
    subheaders = [s.value for s in at.subheader]
    toggled_total = int(
        next(s for s in subheaders if "matching sample cases" in s).split(" ")[0].replace(",", "")
    )
    assert toggled_total <= default_total

    from wii.analytics.insights import load_thresholds

    threshold = load_thresholds().narrative_min_confidence
    captions = [c.value for c in at.caption]
    assert any(f"{threshold:.2f}" in c for c in captions)

    md_values = [m.value for m in at.markdown]
    case_list_html = "".join(v for v in md_values if v.startswith('<div class="wii-case-list">'))
    if toggled_total > 0:
        assert "wii-confidence-low" not in case_list_html


def test_case_explorer_filter_by_location_and_age_bracket_matches_direct_sql(app_test):
    at = app_test
    _switch_to_case_explorer(at)
    at.run(timeout=APP_TIMEOUT)

    options = data_module.case_explorer_filter_options()
    location_code = options["locations"][0][0]
    age_bracket_id = options["age_brackets"][0][0]

    at.session_state["ce_f_locations"] = (location_code,)
    at.session_state["ce_f_age_brackets"] = (age_bracket_id,)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    from wii.app import filters as filters_module

    years = filters_module._default_year_range()
    con = data_module.get_connection()
    direct = con.execute(
        "SELECT COUNT(*) FROM fact_cases WHERE year BETWEEN ? AND ? AND is_adult = TRUE "
        "AND location_code = ? AND age_bracket = ?",
        [*years, location_code, age_bracket_id],
    ).fetchone()[0]
    subheaders = [s.value for s in at.subheader]
    matching = next(s for s in subheaders if "matching sample cases" in s)
    assert int(matching.split(" ")[0].replace(",", "")) == direct


def test_case_explorer_stale_persisted_filter_value_is_dropped_silently(app_test):
    at = app_test
    _switch_to_case_explorer(at)
    at.run(timeout=APP_TIMEOUT)
    at.session_state["ce_f_tools"] = ("not_a_tool",)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    from wii.app import filters as filters_module

    years = filters_module._default_year_range()
    con = data_module.get_connection()
    unfiltered = con.execute(
        "SELECT COUNT(*) FROM fact_cases WHERE year BETWEEN ? AND ? AND is_adult = TRUE",
        list(years),
    ).fetchone()[0]
    subheaders = [s.value for s in at.subheader]
    matching = next(s for s in subheaders if "matching sample cases" in s)
    assert int(matching.split(" ")[0].replace(",", "")) == unfiltered


# ---------------------------------------------------------------------------
# Verbatim narrative round-trip (D-10)
# ---------------------------------------------------------------------------


def test_case_explorer_page_narrative_is_verbatim_source():
    flt = CaseFilter(years=None, age_scope="all")
    df = data_module.case_explorer_page(flt, (), page=1)
    if df.height == 0:
        return
    row = df.row(0, named=True)
    from wii.app.pages.case_explorer import _case_card_html

    html_out = _case_card_html(row, (), 1.0)
    narrative_start = html_out.index('<div class="wii-case-narrative">') + len(
        '<div class="wii-case-narrative">'
    )
    narrative_end = html_out.index("</div>", narrative_start)
    import html as html_mod

    rendered = html_mod.unescape(html_out[narrative_start:narrative_end])
    assert rendered == (row["narrative"] or "")


# ---------------------------------------------------------------------------
# Performance (P3 D-14 / D-12: cold query < 1.0s for a 5-term search)
# ---------------------------------------------------------------------------


def test_case_explorer_count_and_page_are_fast_for_five_term_search():
    data_module.case_explorer_count.clear()
    data_module.case_explorer_page.clear()
    flt = CaseFilter(years=None, age_scope="all")
    terms = ("saw", "finger", "cut", "blade", "hand")

    start = time.perf_counter()
    data_module.case_explorer_count(flt, terms)
    assert time.perf_counter() - start < 1.0

    start = time.perf_counter()
    data_module.case_explorer_page(flt, terms, page=1)
    assert time.perf_counter() - start < 1.0


# ---------------------------------------------------------------------------
# D-10 honesty boundary: no weighted-estimate helper is importable on this page
# ---------------------------------------------------------------------------

_FORBIDDEN_ESTIMATE_NAMES = frozenset(
    {
        "resolve",
        "resolve_share",
        "cached_estimate_total",
        "cached_estimate_by",
        "cached_share_by",
        "cached_severity_profile",
    }
)


def test_case_explorer_page_imports_no_weighted_estimate_helper():
    path = repo_root() / "src" / "wii" / "app" / "pages" / "case_explorer.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.update(alias.asname or alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported.update(alias.asname or alias.name for alias in node.names)
    offenders = imported & _FORBIDDEN_ESTIMATE_NAMES
    assert not offenders, f"case_explorer.py imports weighted-estimate helpers: {offenders}"


def test_case_explorer_page_never_shows_basis_caption_text():
    path = repo_root() / "src" / "wii" / "app" / "pages" / "case_explorer.py"
    text = path.read_text(encoding="utf-8")
    assert "Estimated U.S. ER-treated injuries" not in text


def test_highlight_narrative_single_call_site_in_app():
    import subprocess

    result = subprocess.run(
        ["grep", "-rn", "highlight_narrative(", str(repo_root() / "src" / "wii" / "app")],
        capture_output=True,
        text=True,
    )
    lines = [line for line in result.stdout.splitlines() if "def highlight_narrative" not in line]
    assert len(lines) == 1, lines


# ---------------------------------------------------------------------------
# theme
# ---------------------------------------------------------------------------


def test_theme_css_contains_case_card_mark_rule():
    from wii.app import theme

    assert ".wii-case-card mark" in theme._CSS
