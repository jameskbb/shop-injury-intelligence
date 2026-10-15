"""PAGE-08 "Methodology & Data" (D-13..D-17): the last page in the brief's navigation order. It
is a transparent account of NEISS, weighting, stability and suppression, the coding taxonomy and
its 2019-era changes, the classifier and its MEASURED validation (with the D-16 labeler
disclosure directly beside the numbers), the D-15 Data Lineage diagram, and the D-14 live pipeline
metadata panel -- every count, percentage and version on this page is read at render time from
committed files (`wii.app.pipeline_metadata`), never a literal in this module or in the versioned
Markdown content files under `content/methodology/`. The no-denominator callout (D-13) is the
first element after the title and the page's single most prominent block: NEISS has no exposure
denominator, so an estimated injury count is never a probability of injury. Section order (D-13):
callout -> caption -> 17 prose sections (Task 2) -> Validation (holdout accuracy table, the D-16
provenance caption directly beside it, per-class precision/recall, coverage, VAL-01
reconciliation) -> Data freshness -> Data quality (the pipeline's own QA report, failing checks
first) -> Data Lineage (`st.graphviz_chart` with a text fallback) -> live pipeline metadata table.
"""

from __future__ import annotations

import html
import re

import polars as pl
import streamlit as st

from wii.app.components.filter_context import source_field, title_block_html
from wii.app.data import latest_data_year
from wii.app.pipeline_metadata import (
    LINEAGE_STAGES,
    QA_STATUS_BLOCKED,
    QA_STATUS_PASSED,
    UNKNOWN,
    coverage_fields,
    lineage_dot,
    pipeline_fields,
    qa_summary,
    qb_reconciliation,
    read_section,
    validation_summary,
)

PAGE_TITLE = "Methodology & Data"

#: The page is a long read and the numbers most people come for (validation, data quality) were
#: two thirds of the way down it. Anchors on the sections that are always open; the prose above
#: them is collapsed, so the scroll is short enough for the list to be worth having.
_SECTION_ANCHORS: tuple[tuple[str, str], ...] = (
    ("How the classifier was validated", "validation"),
    ("Data freshness", "data-freshness"),
    ("Data quality checks", "data-quality"),
    ("Coverage checks", "coverage"),
    ("Data lineage", "data-lineage"),
    ("Pipeline metadata", "pipeline-metadata"),
)

#: The three prose expanders. Seventeen stacked h2 sections became a ~10,000px scroll with no way
#: past it; the content is unchanged, only folded. These labels name the CONTAINER -- each file
#: keeps its own section headings inside, so a label must not restate the first of them.
_PROSE_EXPANDERS: tuple[tuple[str, str], ...] = (
    ("NEISS, weighting and how an estimate is made", "01_neiss_and_estimates.md"),
    ("Coding, taxonomy and scope", "02_coding_taxonomy_and_scope.md"),
    ("Limitations and narrative classification", "03_limitations_and_classification.md"),
)

COVERAGE_CAPTION = (
    "What the pipeline loaded and how complete it was, read from the run's own metadata: which "
    "annual files were processed, whether any period in that span is missing, how many raw rows "
    "were read, and how often the fields added in the 2019 schema are actually populated."
)

NO_DENOMINATOR_CALLOUT = (
    "The most important note on this page: NEISS contains no denominator, no count of tool "
    "owners and no hours of tool use. An estimated injury count is therefore not the probability "
    "of injury. A highly popular tool may generate many injuries partly because many people use "
    "it, and a lower-volume tool may appear less often simply because fewer people use it."
)

#: This page gets a title block like every other sheet, but its BASIS and SCOPE are FIXED rather
#: than read from the live sidebar: this page describes the whole dataset and the filters
#: deliberately never reach it, so a strip quoting the current filter state would be actively
#: misleading here. Stating that in the SCOPE field is also the clearest place to put a fact that
#: regularly surprises readers.
DRAWING = "How these numbers are made, and how far they can be trusted"
BASIS_FIELD = "Not filter-dependent"
SCOPE_FIELD = "Whole dataset; the sidebar filters do not apply on this page"

CONTENT_UNAVAILABLE = "This section's content is unavailable right now."

# Brief order: NEISS/estimates -> coding/taxonomy/scope -> limitations/classification. Kept as
# the canonical list; `_PROSE_EXPANDERS` pairs each file with the heading it is folded under.
SECTION_FILES = tuple(filename for _, filename in _PROSE_EXPANDERS)
VALIDATION_FILE = "04_validation.md"
DATA_FRESHNESS_FILE = "05_data_freshness.md"
DATA_QUALITY_FILE = "06_data_quality.md"

# Data-quality panel copy. Every number in it is substituted from `qa_summary()` at render time
# -- no check count, failure count or check name is written down here or in the Markdown section,
# so a later pipeline run that fixes (or adds) a failing check changes this page by itself.
QA_UNAVAILABLE = (
    "The committed pipeline QA report could not be read, so the check results cannot be shown "
    "here. That is a fault in this page, not a statement that the checks passed."
)

QA_ALL_PASSED = "Every check in the latest pipeline run passed."

QA_FAILING_HEADLINE = (
    "{failed} of {total} checks did not pass in the latest pipeline run. Each one is named below, "
    "with the message the check itself recorded. An advisory check reports and never stops the "
    "run; a blocking check that does not pass stops the run before any new data is published, so "
    "a blocking failure listed here means the tables from that run were withheld."
)

QA_STATUS_TEXT = {
    QA_STATUS_PASSED: "no blocking check failed, so this run's tables were published",
    QA_STATUS_BLOCKED: (
        "a blocking check failed, so this run's tables were withheld and the app is still "
        "serving the last set that passed"
    ),
}
QA_STATUS_UNKNOWN_TEXT = "the run's overall status could not be read"

QA_ALL_CHECKS_LABEL = "Every check in this run, in the order the pipeline ran them"

QA_PROVENANCE = (
    "Read at render time from the QA report the pipeline writes next to the data; the check names "
    "above are the report's own check ids."
)

# Kept adjacent to the measured accuracy/precision-recall numbers. Wording matches the same
# disclosure in `docs/classifier_validation.md` and `docs/labeling_guide.md`, so the app and the
# repo say the same thing about who produced the labels.
VALIDATION_PROVENANCE = (
    "The labeled validation set was produced during development with an AI assistant, blind to "
    "the classifier's output, not by a clinician, epidemiologist or trained NEISS coder."
)


_LEADING_HEADING = re.compile(r"\A\s*#{1,6}[^\n]*\n+")


def _render_section(filename: str, *, page_owns_heading: bool = False) -> None:
    """Renders a versioned Markdown section.

    `page_owns_heading=True` drops the file's FIRST heading line, for the sections this page
    titles itself with `st.header` (and hangs a jump-list anchor on). Without it each of those
    sections printed its title twice -- once from the page, once from the Markdown one level
    down. The page's wording is the one kept on purpose: "How the classifier was validated" says
    what the section answers, where the file's own "Validation" only names it.

    The three prose files are NOT stripped: they are multi-section, their first heading is real
    content rather than a repeat, and the expander label above them names the container.
    """
    content = read_section(filename)
    if content is None:
        st.caption(CONTENT_UNAVAILABLE)
        return
    if page_owns_heading:
        content = _LEADING_HEADING.sub("", content, count=1)
    st.markdown(content)


def _render_data_quality() -> None:
    """The D-19/D-20 QA report, surfaced. Failing checks lead the panel -- callout first, then the
    per-check table naming each one -- with the passed/failed totals underneath, so a reader meets
    the failures before the summary rather than after it.
    """
    _render_section(DATA_QUALITY_FILE, page_owns_heading=True)
    summary = qa_summary()
    if not summary["group_rows"]:
        st.caption(QA_UNAVAILABLE)
        return

    failing = summary["failing_checks"]
    headline = (
        QA_FAILING_HEADLINE.format(failed=summary["failed_checks"], total=summary["total_checks"])
        if failing
        else QA_ALL_PASSED
    )
    st.markdown(f'<div class="wii-callout">{html.escape(headline)}</div>', unsafe_allow_html=True)
    if failing:
        st.table(
            pl.DataFrame(failing, schema=["Check", "Type", "What the check recorded"], orient="row")
        )
    st.table(
        pl.DataFrame(
            summary["group_rows"], schema=["Check group", "Run", "Passed", "Failed"], orient="row"
        )
    )
    with st.expander(QA_ALL_CHECKS_LABEL):
        st.table(
            pl.DataFrame(summary["all_checks"], schema=["Check", "Type", "Result"], orient="row")
        )
    status_text = QA_STATUS_TEXT.get(summary["status"], QA_STATUS_UNKNOWN_TEXT)
    st.caption(f"QA report generated {summary['generated_at']}; {status_text}.")
    if summary["count_semantics"] != UNKNOWN:
        st.caption(summary["count_semantics"])
    st.caption(QA_PROVENANCE)


def _render_jump_list() -> None:
    links = " · ".join(f"[{label}](#{anchor})" for label, anchor in _SECTION_ANCHORS)
    st.markdown(f"**On this page:** {links}")


def render() -> None:
    st.title(PAGE_TITLE)
    escaped_callout = html.escape(NO_DENOMINATOR_CALLOUT)
    st.markdown(
        title_block_html(DRAWING, BASIS_FIELD, SCOPE_FIELD, source_field(latest_data_year())),
        unsafe_allow_html=True,
    )
    st.markdown(f'<div class="wii-callout">{escaped_callout}</div>', unsafe_allow_html=True)
    _render_jump_list()

    for label, filename in _PROSE_EXPANDERS:
        with st.expander(label, expanded=False):
            _render_section(filename)

    st.header("How the classifier was validated", anchor="validation")
    _render_section(VALIDATION_FILE, page_owns_heading=True)
    summary = validation_summary()
    if summary["per_field_accuracy"]:
        st.table(
            pl.DataFrame(
                summary["per_field_accuracy"], schema=["Field", "Holdout accuracy"], orient="row"
            )
        )
    else:
        st.caption(CONTENT_UNAVAILABLE)
    st.caption(VALIDATION_PROVENANCE)
    st.caption(
        f"Labeled cases: {summary['all_n']} "
        f"({summary['holdout_n']} holdout, {summary['dev_n']} development)"
    )
    if summary["per_class_primary_mechanism"]:
        st.table(
            pl.DataFrame(
                summary["per_class_primary_mechanism"],
                schema=["Class", "Precision", "Recall", "Support"],
                orient="row",
            )
        )
    else:
        st.caption(CONTENT_UNAVAILABLE)
    st.caption(
        f"Classified in-scope product rows: {summary['coverage_rate']}; product rows count a "
        "case once per workshop product, so this can exceed Workshop cases retained."
    )
    st.caption(f"Official CPSC Query Builder reconciliation: {qb_reconciliation()}")

    st.header("Data freshness", anchor="data-freshness")
    _render_section(DATA_FRESHNESS_FILE, page_owns_heading=True)

    st.header("Data quality checks", anchor="data-quality")
    _render_data_quality()

    st.header("Coverage checks", anchor="coverage")
    st.caption(COVERAGE_CAPTION)
    st.table(pl.DataFrame(coverage_fields(), schema=["Check", "Value"], orient="row"))

    st.header("Data lineage", anchor="data-lineage")
    try:
        st.graphviz_chart(lineage_dot())
    except Exception:
        st.markdown(" → ".join(LINEAGE_STAGES))

    st.header("Pipeline metadata", anchor="pipeline-metadata")
    st.table(pl.DataFrame(pipeline_fields(), schema=["Field", "Value"], orient="row"))


render()
