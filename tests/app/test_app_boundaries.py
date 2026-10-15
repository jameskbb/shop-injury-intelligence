"""D-02 boundary test: AST-walks every module under src/wii/app, src/wii/analytics (if present),
and app.py. Forbids importing wii.narratives or wii.data.download, any string literal referencing
data/raw or data/interim, and any `.execute(...)` call whose first argument is an f-string
(JoinedStr) -- SQL values must always reach DuckDB via `?` parameters.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from wii.utils.paths import repo_root

_FORBIDDEN_IMPORTS = ("wii.narratives", "wii.data.download")
_FORBIDDEN_PATH_SUBSTRINGS = ("data/raw", "data/interim")


def _iter_target_files(root: Path) -> list[Path]:
    files: list[Path] = []
    app_py = root / "app.py"
    if app_py.is_file():
        files.append(app_py)
    for base in (root / "src" / "wii" / "app", root / "src" / "wii" / "analytics"):
        if base.is_dir():
            files.extend(sorted(base.rglob("*.py")))
    return files


def _is_forbidden_module(name: str) -> bool:
    return any(name == mod or name.startswith(mod + ".") for mod in _FORBIDDEN_IMPORTS)


_DOCSTRING_OWNER_TYPES = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _docstring_node_ids(tree: ast.AST) -> set[int]:
    """Module/class/function docstrings are documentation, not path references -- prose that
    happens to mention "data/interim" while explaining the boundary rule must not self-trigger.
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, _DOCSTRING_OWNER_TYPES) and node.body:
            first = node.body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                ids.add(id(first.value))
    return ids


def _check_file(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    docstring_ids = _docstring_node_ids(tree)
    offenses: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_forbidden_module(alias.name):
                    offenses.append(f"{path}: forbidden import {alias.name!r}")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if _is_forbidden_module(mod):
                offenses.append(f"{path}: forbidden import from {mod!r}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstring_ids:
                continue
            for bad in _FORBIDDEN_PATH_SUBSTRINGS:
                if bad in node.value:
                    offenses.append(f"{path}: forbidden path reference {node.value!r}")
        elif isinstance(node, ast.Call):
            is_execute = isinstance(node.func, ast.Attribute) and node.func.attr == "execute"
            if is_execute and node.args and isinstance(node.args[0], ast.JoinedStr):
                offenses.append(f"{path}: f-string SQL passed to .execute(...)")
    return offenses


def test_app_boundaries():
    root = repo_root()
    all_offenses: list[str] = []
    for path in _iter_target_files(root):
        all_offenses.extend(_check_file(path))
    assert not all_offenses, "\n".join(all_offenses)


_SUPPRESSION_OWNER = "stability_badge.py"
_SUPPRESSION_NAMES = ("apply_suppression", "stable_annual")


def _suppression_references(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _SUPPRESSION_NAMES:
            found.append(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in _SUPPRESSION_NAMES:
            found.append(node.attr)
        elif isinstance(node, ast.ImportFrom):
            found.extend(alias.name for alias in node.names if alias.name in _SUPPRESSION_NAMES)
        elif isinstance(node, ast.Constant) and node.value in _SUPPRESSION_NAMES:
            found.append(node.value)  # e.g. row["stable_annual"]
    return found


def test_single_suppression_call_site():
    """Only stability_badge.py may apply the CPSC suppression rule or read the precomputed
    per-basis `stable_annual` flag; every other app module goes through its resolve helpers."""
    offenses = [
        f"{path}: references {name!r}"
        for path in _iter_target_files(repo_root())
        if path.name != _SUPPRESSION_OWNER
        for name in _suppression_references(path)
    ]
    assert not offenses, "\n".join(offenses)


# D-11/T-04-20 weight-column boundary: app code must never sum survey weights itself. A string
# constant is flagged only when it BOTH looks like SQL (select/from/sum) AND contains the
# standalone column name "weight" (word-boundary-ish, hyphen-guarded so theme.py's legitimate
# `font-weight`/`--wii-weight-semibold` CSS strings and "weighted" prose never trip it).
_SQL_LOOKING = re.compile(r"\b(select|from|sum)\b", re.IGNORECASE)
_WEIGHT_COLUMN = re.compile(r"(?<![-\w])weight(?![-\w])")


def _weight_reference_offenses(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(path))
    docstring_ids = _docstring_node_ids(tree)
    offenses: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstring_ids:
                continue
            value = node.value
            if _SQL_LOOKING.search(value) and _WEIGHT_COLUMN.search(value):
                offenses.append(f"{path}: {value!r}")
    return offenses


def test_no_weight_column_reference_in_app():
    """Per P2 D-01, app code never sums weights -- pathway values come only from already-
    estimated `estimate_by_pair`/`estimate_by` rows (D-11)."""
    offenses: list[str] = []
    for path in _iter_target_files(repo_root()):
        offenses.extend(_weight_reference_offenses(path))
    assert not offenses, "\n".join(offenses)
