"""Self-verifying check of docs/testing.md's traceability table (D-01, 06-03 Task 1).

Parses the Markdown table, extracts every cited `path::test_name` node id, and resolves each
one against the live `tests/` tree: the file must exist and define a module-level or
class-level `def`/`async def` with that name (any trailing `[param]` parametrize suffix is
stripped before matching). A renamed or deleted test therefore fails this check rather than
letting the doc go silently stale.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from wii.utils.paths import repo_root

DOC_PATH = repo_root() / "docs" / "testing.md"

# The product brief's TESTING list, verbatim (the brief lives in the author's local notes).
BRIEF_ITEMS = [
    "Weighted injury estimates",
    "Unique-case deduplication",
    "Multi-product cases",
    "Body-part bridge handling",
    "Diagnosis bridge handling",
    "Date filtering",
    "Tool taxonomy",
    "Broad-code narrative refinement",
    "Mechanism rules",
    "Severity metrics",
    "Estimate suppression",
    "Age decoding",
    "Pre/post-2019 schemas",
    "Insight thresholds",
]

_NODE_ID_RE = re.compile(r"`([^`]+\.py::[A-Za-z0-9_:\[\].\-]+)`")
_TABLE_ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|\s*(.+?)\s*\|\s*(.*?)\s*\|\s*$")


def _parse_table_rows(doc_text: str) -> list[tuple[str, str, str]]:
    """Return (item, covering_tests_cell, notes_cell) for each data row after the header."""
    rows: list[tuple[str, str, str]] = []
    lines = doc_text.splitlines()
    in_table = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("| Brief TESTING item"):
            in_table = True
            continue
        if in_table and re.match(r"^\|[\s\-|]+\|$", stripped):
            continue
        if in_table and stripped.startswith("|"):
            m = _TABLE_ROW_RE.match(stripped)
            if m:
                rows.append((m.group(1), m.group(2), m.group(3)))
            continue
        if in_table and not stripped.startswith("|"):
            break
    return rows


def _extract_node_ids(cell_text: str) -> list[str]:
    return _NODE_ID_RE.findall(cell_text)


def _strip_param_suffix(func_name: str) -> str:
    idx = func_name.find("[")
    return func_name[:idx] if idx != -1 else func_name


def unresolved_node_ids(doc_text: str, tests_root: Path) -> list[str]:
    """Return every cited node id in doc_text's table that does not resolve under tests_root.

    A node id resolves when its file exists (relative to the repo root that tests_root's
    parent represents) and an ast.FunctionDef/AsyncFunctionDef with the (param-stripped) name
    exists at module level or inside the named class of that file.
    """
    repo = tests_root.parent
    unresolved: list[str] = []
    for _item, covering_cell, _notes in _parse_table_rows(doc_text):
        for node_id in _extract_node_ids(covering_cell):
            if "::" not in node_id:
                unresolved.append(node_id)
                continue
            file_part, rest = node_id.split("::", 1)
            parts = rest.split("::")
            func_name = _strip_param_suffix(parts[-1])
            class_name = parts[0] if len(parts) > 1 else None

            file_path = repo / file_part
            if not file_path.is_file():
                unresolved.append(node_id)
                continue

            try:
                tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
            except SyntaxError:
                unresolved.append(node_id)
                continue

            if class_name is not None:
                class_node = next(
                    (
                        n
                        for n in ast.walk(tree)
                        if isinstance(n, ast.ClassDef) and n.name == class_name
                    ),
                    None,
                )
                search_scope: list[ast.AST] = list(class_node.body) if class_node else []
            else:
                search_scope = tree.body

            found = any(
                isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func_name
                for n in search_scope
            )
            if not found:
                unresolved.append(node_id)
    return unresolved


def test_real_testing_doc_has_zero_unresolved_ids():
    doc_text = DOC_PATH.read_text(encoding="utf-8")
    unresolved = unresolved_node_ids(doc_text, repo_root() / "tests")
    assert unresolved == [], f"docs/testing.md cites unresolved node ids: {unresolved}"


def test_table_has_exactly_the_14_brief_items_each_citing_at_least_one_node_id():
    doc_text = DOC_PATH.read_text(encoding="utf-8")
    rows = _parse_table_rows(doc_text)
    items = [item for item, _covering, _notes in rows]
    assert items == BRIEF_ITEMS, f"docs/testing.md items {items} != brief items {BRIEF_ITEMS}"
    for item, covering_cell, _notes in rows:
        node_ids = _extract_node_ids(covering_cell)
        assert node_ids, f"brief item {item!r} cites no node id"


def test_fail_first_synthetic_doc_and_missing_function_are_both_reported(tmp_path):
    real_file = tmp_path / "tests" / "test_real.py"
    real_file.parent.mkdir(parents=True)
    real_file.write_text("def test_present():\n    pass\n", encoding="utf-8")

    synthetic_doc = (
        "| Brief TESTING item | Covering tests | Notes |\n"
        "|---|---|---|\n"
        "| Fake item | `tests/test_does_not_exist.py::test_nope` |  |\n"
        "| Fake item 2 | `tests/test_real.py::test_missing_function` |  |\n"
    )
    unresolved = unresolved_node_ids(synthetic_doc, tmp_path / "tests")
    assert "tests/test_does_not_exist.py::test_nope" in unresolved
    assert "tests/test_real.py::test_missing_function" in unresolved
