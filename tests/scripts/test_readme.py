"""Refresh-stable structure guard for README.md (D-07, 06-06 Task 1).

Asserts the README's shape -- H1, hero placement, brief section order, gallery wiring to real
screenshot files, Mermaid diagram contract, and safety-language guardrails -- without asserting
any specific number (numbers are checked separately, at authoring time, by
`scripts/check_doc_numbers.py`). This test must keep passing across a data refresh: it never
reads `data/metadata/*.json`.
"""

from __future__ import annotations

import re

from wii.app.pages_registry import PAGE_SPECS
from wii.utils.paths import repo_root

README_PATH = repo_root() / "README.md"
SCREENSHOTS_DIR = repo_root() / "docs" / "screenshots"

# Brief section order, verbatim (06-UI-SPEC.md Copywriting Contract row "README section order"),
# plus two sections the brief's list does not name. "Technology" and "Why this exists" come from the
# Master Dashboard Builder README contract; they are additive and sit either side of the gallery so
# the brief's own relative order is unchanged. The assertion stays exact equality -- a new section
# must be added here deliberately, it cannot drift in.
BRIEF_SECTION_ORDER = [
    "What problem this solves",
    "Why NEISS is unusual",
    "Key capabilities",
    "Architecture",
    "Narrative intelligence",
    "Survey-weighted methodology",
    "Data source",
    "What this does NOT measure",
    "Limitations",
    "Running locally",
    "Refreshing data",
    "Testing",
    "Technology",
    "Screenshots",
    "Why this exists",
]

# 05-UI-SPEC.md D-15's 8-node Data Lineage labels, verbatim.
LINEAGE_LABELS = [
    "CPSC NEISS",
    "Annual Public Files",
    "Schema Normalization",
    "Workshop Tool Filter",
    "Narrative Enrichment",
    "Survey-Weighted Metric Layer",
    "DuckDB / Parquet",
    "Streamlit",
]

# Brief SAFETY / COMMUNICATION GUARDRAILS -- language never allowed anywhere in the README.
GUARDRAIL_PHRASES = [
    "caused by",
    "times more dangerous",
    "most dangerous",
    "safest tool",
    "careless",
]

_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_H1_RE = re.compile(r"^# (.+)$", re.MULTILINE)
_H2_RE = re.compile(r"^## (.+)$", re.MULTILINE)
_MERMAID_RE = re.compile(r"```mermaid\n(.*?)```", re.DOTALL)


def _text() -> str:
    return README_PATH.read_text(encoding="utf-8")


def test_first_line_is_the_h1():
    first_line = _text().splitlines()[0]
    assert first_line == "# Workshop Injury Intelligence"


def test_h2_headings_appear_in_exactly_the_brief_order():
    text = _text()
    headings = [m.strip() for m in _H2_RE.findall(text)]
    assert headings == BRIEF_SECTION_ORDER, headings


def test_what_this_does_not_measure_heading_is_verbatim():
    assert "## What this does NOT measure" in _text()


def test_hero_image_appears_before_the_first_h2():
    text = _text()
    hero_match = re.search(r"!\[[^\]]*\]\(docs/screenshots/hero\.png\)", text)
    first_h2_match = _H2_RE.search(text)
    assert hero_match is not None, "hero.png not referenced in README.md"
    assert first_h2_match is not None
    assert hero_match.start() < first_h2_match.start()


def test_every_image_path_resolves_to_a_committed_file():
    for _alt, path in _IMAGE_RE.findall(_text()):
        if path.startswith(("http://", "https://")):
            continue
        resolved = repo_root() / path
        assert resolved.is_file(), f"README image path does not exist: {path}"


def test_no_image_alt_text_is_empty_generic_or_a_bare_filename():
    for alt, path in _IMAGE_RE.findall(_text()):
        stripped = alt.strip()
        assert stripped != "", f"empty alt text for {path}"
        assert stripped.lower() != "screenshot", f"generic alt text for {path}"
        filename = path.rsplit("/", 1)[-1]
        assert stripped != filename, f"bare-filename alt text for {path}"


def test_gallery_has_one_entry_per_page_spec_in_registry_order_referencing_its_desktop_file():
    text = _text()
    heading_positions = [
        (int(n), title.strip(), m.start())
        for m in re.finditer(r"^### (\d+)\. (.+)$", text, re.MULTILINE)
        for n, title in [m.group(1, 2)]
    ]
    assert len(heading_positions) == len(PAGE_SPECS), heading_positions
    for idx, spec in enumerate(PAGE_SPECS, start=1):
        n, title, pos = heading_positions[idx - 1]
        assert n == idx, f"gallery entry {idx} has heading number {n}"
        assert title == spec.title, f"gallery entry {idx} title {title!r} != {spec.title!r}"
        nn = f"{idx:02d}"
        expected_file = f"docs/screenshots/{nn}-{spec.slug}-desktop.png"
        # Search the section between this heading and the next for the expected image path.
        next_pos = heading_positions[idx][2] if idx < len(heading_positions) else len(text)
        section = text[pos:next_pos]
        assert expected_file in section, f"{expected_file} not referenced in section {idx}"


def test_exactly_one_mermaid_block_with_all_lineage_labels_and_no_style_overrides():
    blocks = _MERMAID_RE.findall(_text())
    assert len(blocks) == 1, f"expected exactly one mermaid block, found {len(blocks)}"
    block = blocks[0]
    for label in LINEAGE_LABELS:
        assert label in block, f"missing lineage label: {label}"
    assert "classDef" not in block
    assert "style " not in block


def test_readme_contains_none_of_the_guardrail_phrases():
    lowered = _text().lower()
    for phrase in GUARDRAIL_PHRASES:
        assert phrase not in lowered, f"guardrail phrase present: {phrase!r}"


def test_readme_links_key_docs_and_license():
    text = _text()
    assert "docs/testing.md" in text
    assert "docs/data-distribution.md" in text
    assert "LICENSE" in text
