"""Guards the public surface of the repository against internal-process leakage.

This repo is going public. Everything under `docs/` and the README is read by a visitor who has
never seen the planning directory, so a decision id, a requirement tag, a phase number or a path
into `.planning/` is not shorthand to them -- it is a dangling reference that makes the project
look like an artefact of its own process rather than a product. The same class of leak reached the
UI (three page captions shipped ending "(D-13)"); `tests/app/test_pages_smoke.py` guards the
rendered strings, this guards the prose.

An obsolete completion report stays outside the public docs. The publication runbook is now a
reader-facing operational guide, covered by the same prose guards as every other document.
"""

from __future__ import annotations

import re

import pytest

from wii.utils.paths import repo_root

#: Cross-references into the planning directory. Each has a word boundary or literal parenthesis
#: so ordinary prose ("D-shaped", a 2019-nn date) cannot trip it.
LEAK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("decision id", re.compile(r"\bD-\d+\b")),
    ("requirement id", re.compile(r"\b(?:INS|VAL|OPS|TAX|APP|WR)-\d+\b")),
    ("task id", re.compile(r"\bT-\d{2}-\d{2}\b")),
    ("spike reference", re.compile(r"\bWave-0\b")),
    ("planning path", re.compile(r"\.planning/")),
    ("phase reference", re.compile(r"\bPhase \d+\b")),
    ("gsd command", re.compile(r"/gsd-[a-z-]+")),
)

#: Reviewer-of-the-author framing. The quality bar stays and a short, honest "Why this exists"
#: section stays; copy that addresses a hiring manager rather than a woodworker does not.
PORTFOLIO_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bportfolio\b", re.IGNORECASE),
    re.compile(r"\bhiring\b", re.IGNORECASE),
    re.compile(r"\brecruiter\b", re.IGNORECASE),
)

MOVED_OUT_OF_DOCS = ("development-summary.md",)


def _public_docs() -> list:
    docs = sorted((repo_root() / "docs").glob("*.md"))
    assert docs, "expected committed docs/*.md"
    return [repo_root() / "README.md", *docs]


@pytest.mark.parametrize("path", _public_docs(), ids=lambda p: p.name)
def test_public_doc_carries_no_planning_identifier(path):
    text = path.read_text(encoding="utf-8")
    for name, pattern in LEAK_PATTERNS:
        match = pattern.search(text)
        assert match is None, (
            f"{path.name} leaks a {name}: {match.group(0)!r} in "
            f"{text[max(0, match.start() - 60) : match.end() + 60]!r}"
        )


@pytest.mark.parametrize("path", _public_docs(), ids=lambda p: p.name)
def test_public_doc_addresses_a_reader_not_a_reviewer(path):
    text = path.read_text(encoding="utf-8")
    for pattern in PORTFOLIO_PATTERNS:
        match = pattern.search(text)
        assert match is None, (
            f"{path.name} addresses a reviewer of the author: {match.group(0)!r} in "
            f"{text[max(0, match.start() - 80) : match.end() + 80]!r}"
        )


@pytest.mark.parametrize("name", MOVED_OUT_OF_DOCS)
def test_internal_report_is_not_shipped_under_docs(name):
    assert not (repo_root() / "docs" / name).exists()


def test_no_public_doc_links_to_a_document_that_moved_out():
    for path in _public_docs():
        text = path.read_text(encoding="utf-8")
        for name in MOVED_OUT_OF_DOCS:
            assert name not in text, f"{path.name} still links to {name}"
