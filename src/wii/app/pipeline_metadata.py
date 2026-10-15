"""PAGE-08 D-14 live metadata reader: every number Methodology shows is read from committed
files on EVERY render (no caching -- the files are tiny and tests monkeypatch the directories),
with a per-field "unknown" fallback following the same reject-bool/wrong-type pattern as
`wii.app.data.latest_data_year`. This module is framework-light (no streamlit import for the
data-reading half; `wii.app.theme` is only pulled in for `lineage_dot`'s color tokens) and
SQL-free -- it reads `data_metadata.json`, `classifier_validation.json`, `qa_report.json`
(the D-19/D-20 pipeline QA report), `docs/validation.md` (VAL-01), and the versioned Markdown
content files under `content/methodology/`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from wii.app import theme
from wii.utils.paths import repo_root

METADATA_DIR = repo_root() / "data" / "metadata"
DOCS_DIR = repo_root() / "docs"
CONTENT_DIR = Path(__file__).resolve().parent / "content" / "methodology"

UNKNOWN = "unknown"

# D-14 UI-SPEC order.
FIELD_LABELS: tuple[str, ...] = (
    "Data years loaded",
    "Raw NEISS cases processed",
    "Workshop cases retained",
    "Narratives classified",
    "Unknown narrative %",
    "Latest year",
    "Tool taxonomy version",
    "Classifier version",
)

# D-15 8-stage linear flow, verbatim brief order.
LINEAGE_STAGES: tuple[str, ...] = (
    "CPSC NEISS",
    "Annual Public Files",
    "Schema Normalization",
    "Workshop Tool Filter",
    "Narrative Enrichment",
    "Survey-Weighted Metric Layer",
    "DuckDB / Parquet",
    "Streamlit",
)

_QB_TOTALS_RE = re.compile(
    r"Totals:\s*(\d+)\s*mismatch\(es\)\s*across\s*(\d+)\s*rows", re.IGNORECASE
)


def _load_json(name: str) -> dict | None:
    """Reads `METADATA_DIR / name` on EVERY call -- never cached, since tests monkeypatch
    `METADATA_DIR` to point at fixture copies. Returns None on any read/parse failure.
    """
    path = METADATA_DIR / name
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _is_plain_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _years_loaded(meta: dict | None) -> str:
    if not isinstance(meta, dict):
        return UNKNOWN
    years = meta.get("years_processed")
    if not isinstance(years, list) or not years:
        return UNKNOWN
    if not all(_is_plain_int(y) for y in years):
        return UNKNOWN
    return f"{min(years)}–{max(years)} ({len(years)} years)"


def _raw_cases_processed(meta: dict | None) -> str:
    if not isinstance(meta, dict):
        return UNKNOWN
    years_processed = meta.get("years_processed")
    years = meta.get("years")
    if not isinstance(years_processed, list) or not isinstance(years, dict):
        return UNKNOWN
    total = 0
    for year in years_processed:
        entry = years.get(str(year))
        if not isinstance(entry, dict):
            return UNKNOWN
        count = entry.get("raw_row_count")
        if not _is_plain_int(count):
            return UNKNOWN
        total += count
    return f"{total:,}"


def _top_level_int(meta: dict | None, key: str) -> str:
    if not isinstance(meta, dict):
        return UNKNOWN
    value = meta.get(key)
    if not _is_plain_int(value):
        return UNKNOWN
    return f"{value:,}"


def _enrichment(meta: dict | None) -> dict | None:
    if not isinstance(meta, dict):
        return None
    enrichment = meta.get("enrichment")
    return enrichment if isinstance(enrichment, dict) else None


def _narratives_classified(meta: dict | None) -> str:
    enrichment = _enrichment(meta)
    if enrichment is None:
        return UNKNOWN
    value = enrichment.get("cases")
    if not _is_plain_int(value):
        return UNKNOWN
    return f"{value:,}"


def _unknown_narrative_pct(meta: dict | None) -> str:
    enrichment = _enrichment(meta)
    if enrichment is None:
        return UNKNOWN
    value = enrichment.get("unknown_mechanism_pct")
    if not _is_number(value):
        return UNKNOWN
    return f"{value:.1f}%"


def _classifier_version(meta: dict | None) -> str:
    enrichment = _enrichment(meta)
    if enrichment is None:
        return UNKNOWN
    value = enrichment.get("classifier_version")
    if not isinstance(value, str) or not value:
        return UNKNOWN
    return value


def _latest_year(meta: dict | None) -> str:
    if not isinstance(meta, dict):
        return UNKNOWN
    detection = meta.get("latest_year_detection")
    if not isinstance(detection, dict):
        return UNKNOWN
    year = detection.get("year")
    method = detection.get("method")
    if not _is_plain_int(year) or not isinstance(method, str) or not method:
        return UNKNOWN
    return f"{year} (detected via {method})"


def _taxonomy_version(meta: dict | None) -> str:
    if not isinstance(meta, dict):
        return UNKNOWN
    value = meta.get("taxonomy_version")
    if value is None or isinstance(value, bool):
        return UNKNOWN
    if isinstance(value, (int, str)):
        return str(value)
    return UNKNOWN


def pipeline_fields() -> list[tuple[str, str]]:
    """The 8 (label, value) pairs in `FIELD_LABELS` order, each independently falling back to
    `UNKNOWN` (D-14). "Raw NEISS cases processed" is the sum of `years[y].raw_row_count` over
    `years_processed`, never a pre-aggregated field.
    """
    meta = _load_json("data_metadata.json")
    values = (
        _years_loaded(meta),
        _raw_cases_processed(meta),
        _top_level_int(meta, "workshop_case_count_raw_total"),
        _narratives_classified(meta),
        _unknown_narrative_pct(meta),
        _latest_year(meta),
        _taxonomy_version(meta),
        _classifier_version(meta),
    )
    return list(zip(FIELD_LABELS, values, strict=True))


def read_section(filename: str) -> str | None:
    """The text of `CONTENT_DIR / filename`, or None on any OSError (missing/unreadable file)."""
    try:
        return (CONTENT_DIR / filename).read_text(encoding="utf-8")
    except OSError:
        return None


def _per_field_accuracy(data: dict | None) -> list[tuple[str, str]]:
    if not isinstance(data, dict):
        return []
    holdout = data.get("per_field_accuracy")
    if not isinstance(holdout, dict):
        return []
    holdout = holdout.get("holdout")
    if not isinstance(holdout, dict):
        return []
    rows = []
    for field, value in holdout.items():
        display = f"{value * 100:.1f}%" if _is_number(value) else UNKNOWN
        rows.append((field.replace("_", " "), display))
    return rows


def _per_class_primary_mechanism(data: dict | None) -> list[tuple[str, str, str, str]]:
    if not isinstance(data, dict):
        return []
    per_class = data.get("per_class")
    if not isinstance(per_class, dict):
        return []
    mechanism = per_class.get("primary_mechanism")
    if not isinstance(mechanism, dict):
        return []
    holdout = mechanism.get("holdout")
    if not isinstance(holdout, dict):
        return []
    rows = []
    for class_name, stats in holdout.items():
        if not isinstance(stats, dict):
            rows.append((class_name, UNKNOWN, UNKNOWN, UNKNOWN))
            continue
        precision = stats.get("precision")
        recall = stats.get("recall")
        support = stats.get("support")
        precision_str = f"{precision * 100:.1f}%" if _is_number(precision) else UNKNOWN
        recall_str = f"{recall * 100:.1f}%" if _is_number(recall) else UNKNOWN
        support_str = str(support) if _is_plain_int(support) else UNKNOWN
        rows.append((class_name, precision_str, recall_str, support_str))
    return rows


def _labeled_count(data: dict | None, key: str) -> str:
    if not isinstance(data, dict):
        return UNKNOWN
    labeled = data.get("labeled")
    if not isinstance(labeled, dict):
        return UNKNOWN
    value = labeled.get(key)
    if not _is_plain_int(value):
        return UNKNOWN
    return str(value)


def _coverage_rate(data: dict | None) -> str:
    if not isinstance(data, dict):
        return UNKNOWN
    coverage = data.get("coverage")
    if not isinstance(coverage, dict):
        return UNKNOWN
    rate = coverage.get("rate")
    if not _is_number(rate):
        return UNKNOWN
    return f"{rate * 100:.1f}%"


def validation_summary() -> dict:
    """Holdout per-field accuracy, primary-mechanism holdout per-class precision/recall, labeled
    sample sizes, and classifier coverage rate from `classifier_validation.json` (D-14, D-16).
    Every piece independently falls back to `UNKNOWN`/an empty list -- a missing file empties
    every list and unknowns every scalar, never raising.
    """
    data = _load_json("classifier_validation.json")
    return {
        "per_field_accuracy": _per_field_accuracy(data),
        "per_class_primary_mechanism": _per_class_primary_mechanism(data),
        "all_n": _labeled_count(data, "all_n"),
        "holdout_n": _labeled_count(data, "holdout_n"),
        "dev_n": _labeled_count(data, "dev_n"),
        "coverage_rate": _coverage_rate(data),
    }


def qb_reconciliation() -> str:
    """Parses the final "Totals: N mismatch(es) across M rows." line from `DOCS_DIR/validation.md`
    (VAL-01) into "N mismatches across M checked estimates". A missing file or unmatched line
    falls back to `UNKNOWN`.
    """
    path = DOCS_DIR / "validation.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return UNKNOWN
    match = _QB_TOTALS_RE.search(text)
    if not match:
        return UNKNOWN
    mismatches, rows = match.group(1), match.group(2)
    return f"{mismatches} mismatches across {rows} checked estimates"


def lineage_dot() -> str:
    """A plain DOT string (no `graphviz` package) for the 8 `LINEAGE_STAGES`, chained with "->"
    and styled with theme tokens. Stage names are the DOT node identifiers directly -- no separate
    label attribute is needed since none of the 8 stage strings contains a double quote.

    `rankdir=LR`: the flow IS left to right, and Graphviz's top-to-bottom default stacked eight
    boxes down ~700px of page for what is a single linear chain. Square corners and a hairline
    border match the rest of the drawing -- a stage is a box on a sheet, not a rounded card.
    """
    chain = " -> ".join(f'"{stage}"' for stage in LINEAGE_STAGES)
    return (
        "digraph lineage {\n"
        f'  bgcolor="{theme.VELLUM}";\n'
        "  rankdir=LR;\n"
        "  nodesep=0.18;\n"
        "  ranksep=0.32;\n"
        f'  node [shape=box, style="filled", fillcolor="{theme.VELLUM_DEEP}", '
        f'color="{theme.BORDER}", fontcolor="{theme.GRAPHITE}", fontname="Archivo", '
        "fontsize=11];\n"
        f'  edge [color="{theme.GRAPHITE_SOFT}", arrowsize=0.6];\n'
        f"  {chain};\n"
        "}\n"
    )


# ---------------------------------------------------------------------------
# Coverage and completeness. The pipeline already records everything below per year; this is the
# page-facing read of it, so "what periods loaded, what is missing, how complete are the key
# fields" is legible on the Methodology sheet instead of only inside `data_metadata.json`.
# ---------------------------------------------------------------------------

COVERAGE_LABELS: tuple[str, ...] = (
    "Periods loaded",
    "Missing periods in that span",
    "Latest available period",
    "Raw rows read from the source files",
    "Secondary body-part field populated (latest year)",
    "Narrative length limit (latest year)",
    "Years needing a cp1252 encoding fallback",
)


def _year_entries(meta: dict | None) -> tuple[list[int], dict]:
    if not isinstance(meta, dict):
        return [], {}
    years = meta.get("years_processed")
    entries = meta.get("years")
    if not isinstance(years, list) or not all(_is_plain_int(y) for y in years):
        return [], entries if isinstance(entries, dict) else {}
    return sorted(years), entries if isinstance(entries, dict) else {}


def _missing_years(years: list[int]) -> str:
    """Gaps inside the loaded span -- the "missing periods" check. A contiguous run says so
    explicitly rather than rendering an empty cell."""
    if not years:
        return UNKNOWN
    missing = [y for y in range(years[0], years[-1] + 1) if y not in set(years)]
    if not missing:
        return f"None ({years[0]}–{years[-1]} is complete)"
    return ", ".join(str(y) for y in missing)


def _latest_entry(meta: dict | None) -> tuple[int | None, dict]:
    years, entries = _year_entries(meta)
    if not years:
        return None, {}
    entry = entries.get(str(years[-1]))
    return years[-1], entry if isinstance(entry, dict) else {}


def _rate(entry: dict, key: str) -> str:
    value = entry.get(key)
    if not _is_number(value):
        return UNKNOWN
    return f"{value * 100:.1f}%"


def _encoding_fallbacks(meta: dict | None) -> str:
    years, entries = _year_entries(meta)
    if not years:
        return UNKNOWN
    flagged = [y for y in years if bool(entries.get(str(y), {}).get("cp1252_fallback"))]
    if not flagged:
        return "None (every file read as UTF-8)"
    return ", ".join(str(y) for y in flagged)


def coverage_fields() -> list[tuple[str, str]]:
    """The (label, value) rows behind the Methodology page's coverage table, each independently
    falling back to `UNKNOWN` -- never a guessed number."""
    meta = _load_json("data_metadata.json")
    years, _ = _year_entries(meta)
    latest_year, latest_entry = _latest_entry(meta)
    narrative_max = latest_entry.get("narrative_max_length")
    values = (
        f"{years[0]}–{years[-1]} ({len(years)} annual files)" if years else UNKNOWN,
        _missing_years(years),
        str(latest_year) if latest_year is not None else UNKNOWN,
        _raw_cases_processed(meta),
        _rate(latest_entry, "body_part_2_populated_rate"),
        f"{narrative_max:,} characters" if _is_plain_int(narrative_max) else UNKNOWN,
        _encoding_fallbacks(meta),
    )
    return list(zip(COVERAGE_LABELS, values, strict=True))


# `wii.data.quality` categories -> the word this app uses for them. "structural" checks gate the
# run (a failure aborts before the staged tables are promoted, so the app keeps serving the last
# set that passed); "advisory" checks are reported and never enforced.
QA_CATEGORY_LABELS: dict[str, str] = {"structural": "Blocking", "advisory": "Advisory"}

QA_STATUS_PASSED = "passed"
QA_STATUS_BLOCKED = "blocked"

QA_RESULT_PASSED = "Passed"
QA_RESULT_FAILED = "Did not pass"


def _qa_generated_at(data: dict | None) -> str:
    if not isinstance(data, dict):
        return UNKNOWN
    value = data.get("generated_at")
    if not isinstance(value, str) or not value:
        return UNKNOWN
    return value.split("T", 1)[0]


def _qa_count_semantics(data: dict | None) -> str:
    if not isinstance(data, dict):
        return UNKNOWN
    value = data.get("count_semantics")
    if not isinstance(value, str) or not value:
        return UNKNOWN
    return value


def _qa_status(data: dict | None) -> str:
    if not isinstance(data, dict):
        return UNKNOWN
    aborted = data.get("aborted")
    if not isinstance(aborted, bool):
        return UNKNOWN
    return QA_STATUS_BLOCKED if aborted else QA_STATUS_PASSED


def _qa_results(data: dict | None) -> list[tuple[str, str, bool, str]] | None:
    """`(check_id, category, passed, detail)` for every check in the committed report, or None if
    the payload is absent or does not match `wii.data.quality`'s serialized `CheckResult` shape.
    One malformed entry unknowns the whole set rather than silently under-reporting failures.
    """
    if not isinstance(data, dict):
        return None
    results = data.get("results")
    if not isinstance(results, list) or not results:
        return None
    clean: list[tuple[str, str, bool, str]] = []
    for item in results:
        if not isinstance(item, dict):
            return None
        check_id = item.get("check_id")
        category = item.get("category")
        passed = item.get("passed")
        detail = item.get("detail")
        if not isinstance(check_id, str) or not check_id:
            return None
        if not isinstance(category, str) or category not in QA_CATEGORY_LABELS:
            return None
        if not isinstance(passed, bool) or not isinstance(detail, str):
            return None
        clean.append((check_id, category, passed, detail))
    return clean


def _qa_group_rows(results: list[tuple[str, str, bool, str]] | None) -> list[tuple[str, ...]]:
    if results is None:
        return []
    rows: list[tuple[str, ...]] = []
    for category, label in QA_CATEGORY_LABELS.items():
        group = [r for r in results if r[1] == category]
        passed = sum(1 for r in group if r[2])
        rows.append((label, str(len(group)), str(passed), str(len(group) - passed)))
    total_passed = sum(1 for r in results if r[2])
    rows.append(
        ("All checks", str(len(results)), str(total_passed), str(len(results) - total_passed))
    )
    return rows


def qa_summary() -> dict:
    """The committed pipeline QA report (`qa_report.json`), read on EVERY call like every other
    reader here, reduced to what the Methodology page shows: run status, generation date, the
    counts-semantics sentence the pipeline itself wrote, total/passed/failed check counts, a
    per-category breakdown, the pass/fail roster of every check that ran, and every FAILING check
    with its name, category and the message the check itself recorded. A missing, unreadable or
    malformed report unknowns every scalar and empties every list -- it never raises and never
    guesses a count.
    """
    data = _load_json("qa_report.json")
    results = _qa_results(data)
    if results is None:
        total = passed = failed = UNKNOWN
        failing: list[tuple[str, str, str]] = []
        roster: list[tuple[str, str, str]] = []
    else:
        passed_count = sum(1 for r in results if r[2])
        total, passed = str(len(results)), str(passed_count)
        failed = str(len(results) - passed_count)
        failing = [
            (check_id, QA_CATEGORY_LABELS[category], detail)
            for check_id, category, is_passed, detail in results
            if not is_passed
        ]
        roster = [
            (
                check_id,
                QA_CATEGORY_LABELS[category],
                QA_RESULT_PASSED if is_passed else QA_RESULT_FAILED,
            )
            for check_id, category, is_passed, _ in results
        ]
    return {
        "status": _qa_status(data),
        "generated_at": _qa_generated_at(data),
        "count_semantics": _qa_count_semantics(data),
        "total_checks": total,
        "passed_checks": passed,
        "failed_checks": failed,
        "group_rows": _qa_group_rows(results),
        "failing_checks": failing,
        "all_checks": roster,
    }
