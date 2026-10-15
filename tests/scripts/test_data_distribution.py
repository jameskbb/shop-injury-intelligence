"""Processed-data distribution guards (OPS-04/D-06, 06-03 Task 2).

Enforces the docs/data-distribution.md decision: `data/processed/` stays under the ~50 MB
budget, no `data/raw` file is ever tracked by git, and the distribution decision itself is
recorded with the budget stated.
"""

from __future__ import annotations

import subprocess

from wii.utils.paths import repo_root

BUDGET_BYTES = 50 * 1024 * 1024  # ~50 MB, per D-06
DOC_PATH = repo_root() / "docs" / "data-distribution.md"


def directory_byte_size(path) -> int:
    """Sum of file sizes under `path` (the same measurement docs/data-distribution.md cites)."""
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def check_budget(total_bytes: int, budget_bytes: int) -> None:
    """Raise AssertionError if total_bytes exceeds budget_bytes."""
    if total_bytes > budget_bytes:
        raise AssertionError(
            f"{total_bytes} bytes exceeds the {budget_bytes} byte budget "
            f"({total_bytes / 1024 / 1024:.2f} MB > {budget_bytes / 1024 / 1024:.2f} MB)"
        )


def test_budget_checker_fails_first_with_a_tiny_budget():
    total_bytes = directory_byte_size(repo_root() / "data" / "processed")
    tiny_budget = 10  # bytes -- any non-empty processed/ directory must exceed this
    assert total_bytes > tiny_budget, "test fixture assumption broken: processed/ is empty"
    try:
        check_budget(total_bytes, tiny_budget)
    except AssertionError:
        pass
    else:
        raise AssertionError("check_budget did not fail against a tiny budget")


def test_processed_directory_is_under_the_50mb_budget():
    total_bytes = directory_byte_size(repo_root() / "data" / "processed")
    check_budget(total_bytes, BUDGET_BYTES)


def test_no_data_raw_file_is_tracked_by_git():
    root = repo_root()
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or result.stdout.strip() != "true":
        import pytest

        pytest.skip("not inside a git work tree -- cannot check tracked files")

    ls_result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "data/raw"],
        capture_output=True,
        text=True,
        check=True,
    )
    tracked = [line for line in ls_result.stdout.splitlines() if line.strip()]
    assert tracked == [], f"data/raw files are tracked by git: {tracked}"


def test_gitignore_contains_data_raw():
    gitignore_text = (repo_root() / ".gitignore").read_text(encoding="utf-8")
    assert "data/raw/" in gitignore_text


def test_data_distribution_doc_exists_and_states_the_50mb_budget():
    assert DOC_PATH.is_file()
    text = DOC_PATH.read_text(encoding="utf-8")
    assert "50 MB" in text
    assert "Public Domain" in text
