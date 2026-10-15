"""PAGE-08 Methodology & Data tests (D-13..D-17). Task 1: live pipeline metadata + callout +
navigation order. Task 2 extends with the 17-section prose order/content tests. Task 3 extends
with Validation/Data freshness/Data Lineage tests. The final section surfaces the pipeline's own
D-19/D-20 QA report (20 prose sections total).
"""

from __future__ import annotations

import json

import polars as pl

import wii.app.pipeline_metadata as pipeline_metadata
from wii.app.pages import methodology as methodology_module
from wii.app.pipeline_metadata import CONTENT_DIR, FIELD_LABELS, UNKNOWN, pipeline_fields
from wii.utils.paths import repo_root

from .conftest import APP_TIMEOUT

_METHODOLOGY_PATH = "src/wii/app/pages/methodology.py"

_EXPECTED_SECTION_HEADINGS_17 = [
    "What NEISS is",
    "Hospital probability sampling",
    "Weights",
    "National estimates",
    "Raw sample N",
    "Confidence and statistical stability",
    "Estimate suppression",
    "Product coding",
    "Narrative coding",
    "Tool taxonomy",
    "Multi-product cases",
    "Body-part coding changes around 2019",
    "Diagnosis coding changes around 2019",
    "Data coverage",
    "Consumer and non-work-related scope",
    "Limitations",
    "Narrative-classification methodology",
]

_EXPECTED_NAV_TITLES = [
    "Injury Overview",
    "Tool Profiles",
    "Body Map",
    "Mechanisms",
    "Tool Comparison",
    "My Shop",
    "Case Explorer",
    # PAGE-09 Signals sits with the analytical pages; Methodology & Data stays last because it is
    # the transparency backstop a reader reaches for after seeing a claim, not a peer of the
    # analytical lenses.
    "Signals",
    "Methodology & Data",
]


def _real_metadata() -> dict:
    path = pipeline_metadata.METADATA_DIR / "data_metadata.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_pipeline_fields_labels_in_order():
    fields = pipeline_fields()
    assert [label for label, _ in fields] == list(FIELD_LABELS)


def test_pipeline_fields_workshop_cases_retained_matches_committed_total():
    meta = _real_metadata()
    fields = dict(pipeline_fields())
    expected = f"{meta['workshop_case_count_raw_total']:,}"
    assert fields["Workshop cases retained"] == expected


def test_pipeline_fields_unknown_narrative_pct_one_decimal():
    meta = _real_metadata()
    fields = dict(pipeline_fields())
    expected = f"{meta['enrichment']['unknown_mechanism_pct']:.1f}%"
    assert fields["Unknown narrative %"] == expected


def test_pipeline_fields_latest_year_shows_year_and_method():
    meta = _real_metadata()
    fields = dict(pipeline_fields())
    detection = meta["latest_year_detection"]
    expected = f"{detection['year']} (detected via {detection['method']})"
    assert fields["Latest year"] == expected


def test_pipeline_fields_all_unknown_with_empty_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_metadata, "METADATA_DIR", tmp_path)
    fields = pipeline_fields()
    assert all(value == UNKNOWN for _, value in fields)


def test_raw_cases_processed_sums_two_years(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_metadata, "METADATA_DIR", tmp_path)
    fixture = {
        "years_processed": [2020, 2021],
        "years": {
            "2020": {"raw_row_count": 10},
            "2021": {"raw_row_count": 32},
        },
        "workshop_case_count_raw_total": 1,
        "enrichment": {"cases": 1, "unknown_mechanism_pct": 0.0, "classifier_version": "x"},
        "latest_year_detection": {"year": 2021, "method": "dropdown"},
        "taxonomy_version": 1,
    }
    (tmp_path / "data_metadata.json").write_text(json.dumps(fixture), encoding="utf-8")
    fields = dict(pipeline_fields())
    assert fields["Raw NEISS cases processed"] == "42"


def test_raw_cases_processed_unknown_when_year_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_metadata, "METADATA_DIR", tmp_path)
    fixture = {
        "years_processed": [2020, 2021],
        "years": {"2020": {"raw_row_count": 10}},
        "workshop_case_count_raw_total": 1,
        "enrichment": {"cases": 1, "unknown_mechanism_pct": 0.0, "classifier_version": "x"},
        "latest_year_detection": {"year": 2021, "method": "dropdown"},
        "taxonomy_version": 1,
    }
    (tmp_path / "data_metadata.json").write_text(json.dumps(fixture), encoding="utf-8")
    fields = dict(pipeline_fields())
    assert fields["Raw NEISS cases processed"] == UNKNOWN


def test_raw_cases_processed_unknown_when_non_int_or_bool(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_metadata, "METADATA_DIR", tmp_path)
    fixture = {
        "years_processed": [2020],
        "years": {"2020": {"raw_row_count": True}},
        "workshop_case_count_raw_total": 1,
        "enrichment": {"cases": 1, "unknown_mechanism_pct": 0.0, "classifier_version": "x"},
        "latest_year_detection": {"year": 2020, "method": "dropdown"},
        "taxonomy_version": 1,
    }
    (tmp_path / "data_metadata.json").write_text(json.dumps(fixture), encoding="utf-8")
    fields = dict(pipeline_fields())
    assert fields["Raw NEISS cases processed"] == UNKNOWN


def _switch_to_methodology(at):
    at.run(timeout=APP_TIMEOUT)
    at.switch_page(_METHODOLOGY_PATH)


def test_methodology_default(app_test):
    at = app_test
    _switch_to_methodology(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    titles = [t.value for t in at.title]
    assert any("Methodology & Data" in t for t in titles)

    md_values = [m.value for m in at.markdown]
    callout = "".join(v for v in md_values if "wii-callout" in v)
    assert "denominator" in callout
    assert "not the probability" in callout
    assert "fewer people use it" in callout

    tables = list(at.table)
    assert tables, "expected a pipeline-metadata table"
    field_columns = [
        pl.DataFrame(t.value)["Field"].to_list()
        for t in tables
        if "Field" in pl.DataFrame(t.value).columns
    ]
    assert list(FIELD_LABELS) in field_columns


def test_methodology_metadata_fixture_swap(app_test, tmp_path, monkeypatch):
    meta = _real_metadata()
    meta["workshop_case_count_raw_total"] = 12345
    meta["enrichment"]["classifier_version"] = "rules-test-9.9.9"
    del meta["taxonomy_version"]
    (tmp_path / "data_metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    monkeypatch.setattr(pipeline_metadata, "METADATA_DIR", tmp_path)

    at = app_test
    _switch_to_methodology(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    tables = list(at.table)
    pipeline_table = next(
        t
        for t in tables
        if "Field" in pl.DataFrame(t.value).columns
        and pl.DataFrame(t.value)["Field"].to_list() == list(FIELD_LABELS)
    )
    pipeline_df = pl.DataFrame(pipeline_table.value)
    field_map = dict(
        zip(pipeline_df["Field"].to_list(), pipeline_df["Value"].to_list(), strict=True)
    )
    assert field_map["Workshop cases retained"] == "12,345"
    assert field_map["Classifier version"] == "rules-test-9.9.9"
    assert field_map["Tool taxonomy version"] == UNKNOWN


def test_navigation_order_matches_brief():
    """06-01: app.py builds its `st.navigation([...])` list by iterating PAGE_SPECS (D-03), so
    PAGE_SPECS order IS the navigation order -- no more AST-parsing app.py for individually-named
    `st.Page(...)` assignments, which the registry refactor replaced with a list comprehension.
    """
    from wii.app.pages_registry import PAGE_SPECS

    assert [spec.title for spec in PAGE_SPECS] == _EXPECTED_NAV_TITLES


def _rendered_markdown_headings(at) -> list[str]:
    headings: list[str] = []
    for m in at.markdown:
        for line in m.value.splitlines():
            if line.startswith("## "):
                headings.append(line[3:].strip())
    return headings


def test_methodology_sections_in_brief_order(app_test):
    """The first 17 sections are a prefix of the final 19 (Task 3 appends Validation and Data
    freshness after these) -- `test_methodology_sections_19_headings_in_brief_order` below is
    the full-page assertion.
    """
    at = app_test
    _switch_to_methodology(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception
    assert _rendered_markdown_headings(at)[:17] == _EXPECTED_SECTION_HEADINGS_17


def test_suppression_numbers_match_stability_constants():
    content = (CONTENT_DIR / "01_neiss_and_estimates.md").read_text(encoding="utf-8")
    # wii.metrics.stability.apply_suppression: estimate < 1_200, raw_n < 20, cv > 0.33.
    assert "1,200" in content
    assert "20" in content
    assert "0.33" in content


def test_missing_section_shows_unavailable(app_test, tmp_path, monkeypatch):
    """Every content-file read (the 3 Task 2 prose files plus Task 3's Validation/Data freshness)
    independently falls back to `CONTENT_UNAVAILABLE`, never an exception.
    """
    monkeypatch.setattr(pipeline_metadata, "CONTENT_DIR", tmp_path)
    at = app_test
    _switch_to_methodology(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception
    caption_values = [c.value for c in at.caption]
    # + Validation + Data freshness (Task 3) + Data quality (QA panel).
    expected_unavailable_count = len(methodology_module.SECTION_FILES) + 3
    assert (
        caption_values.count(methodology_module.CONTENT_UNAVAILABLE) == expected_unavailable_count
    )


def test_content_has_no_live_pipeline_numbers():
    meta = _real_metadata()
    workshop_total = meta["workshop_case_count_raw_total"]
    raw_sum = sum(meta["years"][str(y)]["raw_row_count"] for y in meta["years_processed"])
    unknown_pct = meta["enrichment"]["unknown_mechanism_pct"]
    live_literals = [
        str(workshop_total),
        f"{workshop_total:,}",
        str(raw_sum),
        f"{raw_sum:,}",
        f"{unknown_pct:.1f}",
    ]
    sources = [
        (CONTENT_DIR / f"{n:02d}_{name}").read_text(encoding="utf-8")
        for n, name in enumerate(
            [
                "neiss_and_estimates.md",
                "coding_taxonomy_and_scope.md",
                "limitations_and_classification.md",
                "validation.md",
                "data_freshness.md",
                "data_quality.md",
            ],
            start=1,
        )
    ]
    sources.append((repo_root() / "src" / "wii" / "app" / "pages" / "methodology.py").read_text())
    for literal in live_literals:
        for source in sources:
            assert literal not in source, f"live value {literal!r} leaked into content"


# --- Task 3 (05-04, tdd="true"): Validation, Data freshness, Data Lineage. RED: these import
# `validation_summary` / `qb_reconciliation` / `LINEAGE_STAGES` / `lineage_dot`, which do not
# exist in `wii.app.pipeline_metadata` yet, and read `04_validation.md` / `05_data_freshness.md`,
# which are not yet committed -- every test below fails until Task 3's GREEN commit.

#: The last three sections are titled by the PAGE now (with a jump-list anchor each), so their
#: Markdown no longer repeats the title one level down: the capture showed "How the classifier
#: was validated" followed immediately by "Validation", and the same for freshness and quality.
#: The page's wording is the one kept -- it says what the section answers rather than naming it.
_PAGE_OWNED_HEADINGS = [
    "How the classifier was validated",
    "Data freshness",
    "Data quality checks",
]


def _real_classifier_validation() -> dict:
    path = pipeline_metadata.METADATA_DIR / "classifier_validation.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_lineage_dot_contains_stages_and_theme_tokens():
    from wii.app import theme
    from wii.app.pipeline_metadata import LINEAGE_STAGES, lineage_dot

    assert len(LINEAGE_STAGES) == 8
    assert all('"' not in stage for stage in LINEAGE_STAGES)
    dot = lineage_dot()
    chain = " -> ".join(f'"{stage}"' for stage in LINEAGE_STAGES)
    assert chain in dot
    assert theme.VELLUM in dot
    assert theme.VELLUM_DEEP in dot
    assert theme.GRAPHITE in dot
    # The flow IS left to right; Graphviz's top-to-bottom default stacked 8 boxes down ~700px.
    assert "rankdir=LR" in dot
    assert "rankdir=TB" not in dot


def test_qb_reconciliation_parses_totals_line():
    from wii.app.pipeline_metadata import qb_reconciliation

    result = qb_reconciliation()
    assert result == "0 mismatches across 108 checked estimates"


def test_qb_reconciliation_unknown_when_file_missing(tmp_path, monkeypatch):
    from wii.app.pipeline_metadata import qb_reconciliation

    monkeypatch.setattr(pipeline_metadata, "DOCS_DIR", tmp_path)
    assert qb_reconciliation() == UNKNOWN


def test_validation_summary_matches_committed_classifier_validation():
    from wii.app.pipeline_metadata import validation_summary

    data = _real_classifier_validation()
    summary = validation_summary()
    holdout = data["per_field_accuracy"]["holdout"]
    assert [f for f, _ in summary["per_field_accuracy"]] == [k.replace("_", " ") for k in holdout]
    expected_pm_accuracy = f"{holdout['primary_mechanism'] * 100:.1f}%"
    pm_row = dict(summary["per_field_accuracy"])["primary mechanism"]
    assert pm_row == expected_pm_accuracy
    assert summary["all_n"] == str(data["labeled"]["all_n"])
    assert summary["holdout_n"] == str(data["labeled"]["holdout_n"])
    assert summary["dev_n"] == str(data["labeled"]["dev_n"])
    assert summary["coverage_rate"] == f"{data['coverage']['rate'] * 100:.1f}%"
    pm_classes = data["per_class"]["primary_mechanism"]["holdout"]
    assert len(summary["per_class_primary_mechanism"]) == len(pm_classes)


def test_validation_summary_unknown_when_file_missing(tmp_path, monkeypatch):
    from wii.app.pipeline_metadata import validation_summary

    monkeypatch.setattr(pipeline_metadata, "METADATA_DIR", tmp_path)
    summary = validation_summary()
    assert summary["per_field_accuracy"] == []
    assert summary["per_class_primary_mechanism"] == []
    assert summary["all_n"] == UNKNOWN
    assert summary["holdout_n"] == UNKNOWN
    assert summary["dev_n"] == UNKNOWN
    assert summary["coverage_rate"] == UNKNOWN


def test_methodology_sections_20_headings_in_brief_order(app_test):
    """The 17 prose headings still render, in brief order, inside their three expanders -- and
    the three page-owned sections appear exactly ONCE, as page headers rather than twice."""
    at = app_test
    _switch_to_methodology(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception
    assert _rendered_markdown_headings(at) == _EXPECTED_SECTION_HEADINGS_17

    headers = [h.value for h in at.header]
    for heading in _PAGE_OWNED_HEADINGS:
        assert headers.count(heading) == 1
        assert heading not in _rendered_markdown_headings(at)


def test_no_heading_is_rendered_twice_on_the_page(app_test):
    """Every title on this page -- page header, Markdown heading or expander label -- appears
    once. The section restructure briefly printed three of them twice."""
    at = app_test
    _switch_to_methodology(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    titles = [h.value for h in at.header]
    titles += _rendered_markdown_headings(at)
    titles += [e.label for e in at.expander]
    lowered = [t.lower() for t in titles]
    assert len(lowered) == len(set(lowered)), sorted(t for t in lowered if lowered.count(t) > 1)


def test_methodology_provenance_adjacent_to_accuracy_table(app_test):
    at = app_test
    _switch_to_methodology(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    captions = [c.value for c in at.caption]
    provenance_idx = next(i for i, v in enumerate(captions) if "AI assistant" in v)
    assert "blind to the classifier's output" in captions[provenance_idx]
    assert "not by a clinician" in captions[provenance_idx]
    # Task 3's action places the sample-size caption directly after the provenance caption,
    # which itself must sit directly after the per-field accuracy table -- adjacency to the
    # measured numbers (D-16) is what this test protects.
    assert "Labeled cases:" in captions[provenance_idx + 1]


def test_methodology_graphviz_renders_by_default(app_test):
    at = app_test
    _switch_to_methodology(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception
    assert len(at.get("graphviz_chart")) == 1
    md_values = [m.value for m in at.markdown]
    assert not any("CPSC NEISS → Annual Public Files" in v for v in md_values)


def test_methodology_graphviz_fallback_on_exception(app_test, monkeypatch):
    import streamlit as st

    def _raise(*_args, **_kwargs):
        raise RuntimeError("graphviz renderer unavailable")

    monkeypatch.setattr(st, "graphviz_chart", _raise)
    at = app_test
    _switch_to_methodology(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception
    md_values = [m.value for m in at.markdown]
    assert any("CPSC NEISS → Annual Public Files" in v for v in md_values)


# --- Data quality panel: the pipeline's own QA report (src/wii/data/quality.py), surfaced.
# `structural` checks abort the run (staged tables are never promoted); `advisory` checks report
# only. The panel must show BOTH, and must not hide a check that did not pass.

_QA_REPORT_NAME = "qa_report.json"


def _real_qa_report() -> dict:
    path = pipeline_metadata.METADATA_DIR / _QA_REPORT_NAME
    return json.loads(path.read_text(encoding="utf-8"))


_FAILING_COLUMNS = ["Check", "Type", "What the check recorded"]
_ROSTER_COLUMNS = ["Check", "Type", "Result"]
_GROUP_COLUMNS = ["Check group", "Run", "Passed", "Failed"]


def _qa_table(at, columns: list[str]) -> pl.DataFrame | None:
    """The one rendered table with exactly `columns`, found by its schema rather than by position,
    so the rest of the page can add or reorder tables freely."""
    for t in at.table:
        df = pl.DataFrame(t.value)
        if df.columns == columns:
            return df
    return None


def test_qa_summary_matches_committed_report():
    from wii.app.pipeline_metadata import qa_summary

    report = _real_qa_report()
    results = report["results"]
    summary = qa_summary()

    passed = [r for r in results if r["passed"]]
    assert summary["total_checks"] == str(len(results))
    assert summary["passed_checks"] == str(len(passed))
    assert summary["failed_checks"] == str(len(results) - len(passed))
    assert summary["status"] == ("blocked" if report["aborted"] else "passed")
    assert summary["generated_at"] == report["generated_at"].split("T", 1)[0]
    assert summary["count_semantics"] == report["count_semantics"]


def test_qa_summary_group_rows_split_blocking_and_advisory():
    from wii.app.pipeline_metadata import qa_summary

    results = _real_qa_report()["results"]
    rows = {row[0]: row[1:] for row in qa_summary()["group_rows"]}
    assert set(rows) == {"Blocking", "Advisory", "All checks"}
    for label, category in (("Blocking", "structural"), ("Advisory", "advisory")):
        group = [r for r in results if r["category"] == category]
        group_passed = [r for r in group if r["passed"]]
        assert rows[label] == (
            str(len(group)),
            str(len(group_passed)),
            str(len(group) - len(group_passed)),
        )
    assert rows["All checks"][0] == str(len(results))


def test_qa_summary_lists_every_failing_check_with_its_message():
    from wii.app.pipeline_metadata import qa_summary

    results = _real_qa_report()["results"]
    expected = [
        (
            r["check_id"],
            "Blocking" if r["category"] == "structural" else "Advisory",
            r["detail"],
        )
        for r in results
        if not r["passed"]
    ]
    assert qa_summary()["failing_checks"] == expected


def test_qa_summary_surfaces_the_committed_advisory_failures():
    """Regression guard for the reason this panel exists: the committed report has advisory checks
    that did NOT pass, and nothing in the app used to show them. If a future pipeline run clears
    them, this test's premise is read from the report itself, so it stays honest either way.
    """
    from wii.app.pipeline_metadata import qa_summary

    results = _real_qa_report()["results"]
    failing_ids = {r["check_id"] for r in results if not r["passed"]}
    surfaced_ids = {check_id for check_id, _, _ in qa_summary()["failing_checks"]}
    assert surfaced_ids == failing_ids
    assert qa_summary()["failed_checks"] == str(len(failing_ids))


def test_qa_summary_unknown_when_report_missing(tmp_path, monkeypatch):
    from wii.app.pipeline_metadata import qa_summary

    monkeypatch.setattr(pipeline_metadata, "METADATA_DIR", tmp_path)
    summary = qa_summary()
    assert summary["status"] == UNKNOWN
    assert summary["generated_at"] == UNKNOWN
    assert summary["count_semantics"] == UNKNOWN
    assert summary["total_checks"] == UNKNOWN
    assert summary["passed_checks"] == UNKNOWN
    assert summary["failed_checks"] == UNKNOWN
    assert summary["group_rows"] == []
    assert summary["failing_checks"] == []
    assert summary["all_checks"] == []


def test_qa_summary_unknown_when_report_unparseable(tmp_path, monkeypatch):
    from wii.app.pipeline_metadata import qa_summary

    monkeypatch.setattr(pipeline_metadata, "METADATA_DIR", tmp_path)
    (tmp_path / _QA_REPORT_NAME).write_text("{not json", encoding="utf-8")
    assert qa_summary()["total_checks"] == UNKNOWN


def test_qa_summary_unknown_when_one_result_is_malformed(tmp_path, monkeypatch):
    """A single result with a non-bool `passed` (or an unknown category) unknowns the whole set --
    a half-parsed report must never be shown as a smaller, cleaner-looking one."""
    from wii.app.pipeline_metadata import qa_summary

    monkeypatch.setattr(pipeline_metadata, "METADATA_DIR", tmp_path)
    payload = {
        "aborted": False,
        "generated_at": "2026-01-02T03:04:05+00:00",
        "count_semantics": "counts are raw sample records",
        "results": [
            {
                "check_id": "ok_check",
                "category": "structural",
                "passed": True,
                "detail": "fine",
            },
            {
                "check_id": "bad_check",
                "category": "advisory",
                "passed": "yes",
                "detail": "not a bool",
            },
        ],
    }
    (tmp_path / _QA_REPORT_NAME).write_text(json.dumps(payload), encoding="utf-8")
    summary = qa_summary()
    assert summary["failing_checks"] == []
    assert summary["group_rows"] == []
    assert summary["all_checks"] == []
    assert summary["total_checks"] == UNKNOWN
    # Per-field fallback: the scalars that ARE well-formed still read through.
    assert summary["status"] == "passed"
    assert summary["generated_at"] == "2026-01-02"


def test_qa_summary_blocked_status_when_run_aborted(tmp_path, monkeypatch):
    from wii.app.pipeline_metadata import qa_summary

    monkeypatch.setattr(pipeline_metadata, "METADATA_DIR", tmp_path)
    payload = {
        "aborted": True,
        "generated_at": "2026-01-02T03:04:05+00:00",
        "count_semantics": "counts are raw sample records",
        "results": [
            {
                "check_id": "header_contract",
                "category": "structural",
                "passed": False,
                "detail": "year 2020: 24 columns",
            }
        ],
    }
    (tmp_path / _QA_REPORT_NAME).write_text(json.dumps(payload), encoding="utf-8")
    summary = qa_summary()
    assert summary["status"] == "blocked"
    assert summary["failing_checks"] == [("header_contract", "Blocking", "year 2020: 24 columns")]


def test_methodology_data_quality_panel_shows_failing_checks(app_test):
    at = app_test
    _switch_to_methodology(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    report = _real_qa_report()
    failing = [r for r in report["results"] if not r["passed"]]

    group_table = _qa_table(at, _GROUP_COLUMNS)
    assert group_table is not None, "expected the QA totals table"
    groups = dict(
        zip(
            group_table["Check group"].to_list(),
            group_table["Failed"].to_list(),
            strict=True,
        )
    )
    assert groups["All checks"] == str(len(failing))

    failing_table = _qa_table(at, _FAILING_COLUMNS)
    assert failing_table is not None, "expected the failing-check table"
    assert failing_table["Check"].to_list() == [r["check_id"] for r in failing]
    assert failing_table["What the check recorded"].to_list() == [r["detail"] for r in failing]


def test_methodology_data_quality_failure_headline_is_a_callout(app_test):
    """Not buried: the failure count is rendered in the same `wii-callout` block the page uses for
    its single most prominent statement, ABOVE the totals table."""
    at = app_test
    _switch_to_methodology(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    report = _real_qa_report()
    failing = [r for r in report["results"] if not r["passed"]]
    callouts = [m.value for m in at.markdown if "wii-callout" in m.value]
    expected = methodology_module.QA_FAILING_HEADLINE.format(
        failed=len(failing), total=len(report["results"])
    )
    assert any(expected[:60] in c for c in callouts)
    assert any("advisory check reports and never stops the run" in c for c in callouts)


def test_methodology_data_quality_all_passing_report(app_test, tmp_path, monkeypatch):
    report = _real_qa_report()
    for result in report["results"]:
        result["passed"] = True
    report["aborted"] = False
    (tmp_path / _QA_REPORT_NAME).write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(pipeline_metadata, "METADATA_DIR", tmp_path)

    at = app_test
    _switch_to_methodology(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    callouts = [m.value for m in at.markdown if "wii-callout" in m.value]
    assert any(methodology_module.QA_ALL_PASSED in c for c in callouts)
    assert _qa_table(at, _FAILING_COLUMNS) is None, "no failing-check table when nothing failed"
    roster = _qa_table(at, _ROSTER_COLUMNS)
    assert roster is not None
    assert set(roster["Result"].to_list()) == {"Passed"}


def test_methodology_data_quality_unavailable_when_report_missing(app_test, tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_metadata, "METADATA_DIR", tmp_path)
    at = app_test
    _switch_to_methodology(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception
    captions = [c.value for c in at.caption]
    assert methodology_module.QA_UNAVAILABLE in captions
    assert _qa_table(at, _GROUP_COLUMNS) is None
    assert _qa_table(at, _ROSTER_COLUMNS) is None


def test_data_quality_section_explains_advisory_versus_blocking():
    content = (CONTENT_DIR / methodology_module.DATA_QUALITY_FILE).read_text(encoding="utf-8")
    lowered = content.lower()
    assert "blocking check" in lowered
    assert "advisory check" in lowered
    # The distinction that makes an advisory failure not-silent-corruption.
    assert "the run stops" in lowered
    assert "never published" in lowered
    assert "never abort" in lowered


def test_data_quality_copy_has_no_live_qa_numbers():
    """The QA panel's prose and page module must not restate anything the committed report says --
    a later pipeline run changes the panel by itself, with no doc to keep in sync."""
    report = _real_qa_report()
    sources = [
        (CONTENT_DIR / methodology_module.DATA_QUALITY_FILE).read_text(encoding="utf-8"),
        (repo_root() / "src" / "wii" / "app" / "pages" / "methodology.py").read_text(),
    ]
    live_literals = [str(len(report["results"])), report["generated_at"]]
    live_literals += [r["detail"] for r in report["results"] if not r["passed"]]
    live_literals += [r["check_id"] for r in report["results"] if not r["passed"]]
    for literal in live_literals:
        for source in sources:
            assert literal not in source, f"live QA value {literal!r} leaked into page copy"


def test_methodology_copy_passes_guardrails():
    from wii.analytics.insights import scan_guardrail_vocabulary

    for name in dir(methodology_module):
        if name.startswith("_"):
            continue
        value = getattr(methodology_module, name)
        if isinstance(value, str):
            assert scan_guardrail_vocabulary(value) == [], f"{name} failed guardrail scan"
        elif isinstance(value, dict):
            for key, item in value.items():
                if isinstance(item, str):
                    assert scan_guardrail_vocabulary(item) == [], (
                        f"{name}[{key!r}] failed guardrail scan"
                    )


def test_methodology_content_passes_guardrails():
    from wii.analytics.insights import scan_guardrail_vocabulary

    content = (CONTENT_DIR / methodology_module.DATA_QUALITY_FILE).read_text(encoding="utf-8")
    assert scan_guardrail_vocabulary(content) == []


def test_qa_summary_roster_covers_every_check_in_run_order():
    from wii.app.pipeline_metadata import qa_summary

    results = _real_qa_report()["results"]
    expected = [
        (
            r["check_id"],
            "Blocking" if r["category"] == "structural" else "Advisory",
            "Passed" if r["passed"] else "Did not pass",
        )
        for r in results
    ]
    assert qa_summary()["all_checks"] == expected


def test_methodology_data_quality_panel_lists_every_check(app_test):
    """The failing checks lead the panel; the complete roster sits behind an expander below it, so
    a reader can confirm which checks ran at all -- not just the ones that failed."""
    at = app_test
    _switch_to_methodology(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    results = _real_qa_report()["results"]
    roster = _qa_table(at, _ROSTER_COLUMNS)
    assert roster is not None, "expected the full check roster"
    assert roster["Check"].to_list() == [r["check_id"] for r in results]
    assert roster["Result"].to_list() == [
        "Passed" if r["passed"] else "Did not pass" for r in results
    ]
