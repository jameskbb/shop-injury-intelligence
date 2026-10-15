"""Local CI-parity runner (D-02/06-01).

Usage: uv run python scripts/run_ci_locally.py [--keep]

Exports the working tree exactly as CI would see it -- every tracked or untracked-but-not-ignored
file that currently exists on disk (`git ls-files -co --exclude-standard`, so gitignored paths
like data/raw, data/interim, data/reference never leak in) -- into a fresh temporary directory,
`git init`s that export and stages every file (so git-aware tests behave like a real checkout),
parses `.github/workflows/ci.yml` with PyYAML, and runs each step's `run:` string in order with
the export as the working directory. Exits with the first failing step's exit code and prints
which step failed. Deletes the temp directory afterward unless --keep is passed.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from wii.utils.paths import repo_root

_CI_WORKFLOW = ".github/workflows/ci.yml"


def _tracked_and_untracked_files(root: Path) -> list[str]:
    """Every path git considers part of a checkout (tracked + untracked-not-ignored), filtered to
    paths that still exist on disk (a file deleted-but-not-yet-`git rm`'d shows up in `-o` output
    otherwise and would make the subsequent copy step fail)."""
    result = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if (root / line).exists()]


def _export_worktree(root: Path, dest: Path) -> None:
    for rel_path in _tracked_and_untracked_files(root):
        src = root / rel_path
        dst = dest / rel_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    subprocess.run(["git", "init", "-q"], cwd=dest, check=True)
    subprocess.run(["git", "add", "-A"], cwd=dest, check=True)


def _load_run_steps(workflow_path: Path) -> list[str]:
    """PyYAML loads the bare `on:` key as the boolean True (the YAML 1.1 spec treats `on`/`off` as
    booleans) -- irrelevant here since this function only reads `jobs.*.steps`, but noted per the
    plan so a future reader isn't surprised the parsed dict's `True` key holds the trigger config.
    """
    workflow = yaml.safe_load(workflow_path.read_text())
    steps: list[str] = []
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            if "run" in step:
                steps.append(step["run"])
    return steps


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep", action="store_true", help="Keep the temp export directory after running."
    )
    args = parser.parse_args()

    root = repo_root()
    workflow_path = root / _CI_WORKFLOW
    if not workflow_path.exists():
        print(f"ERROR: {_CI_WORKFLOW} not found", file=sys.stderr)
        return 1

    run_steps = _load_run_steps(workflow_path)
    if not run_steps:
        print(f"ERROR: no run: steps found in {_CI_WORKFLOW}", file=sys.stderr)
        return 1

    tmp_dir = Path(tempfile.mkdtemp(prefix="wii-ci-local-"))
    try:
        print(f"Exporting working tree to {tmp_dir} ...")
        _export_worktree(root, tmp_dir)

        env = dict(os.environ)
        env.pop("VIRTUAL_ENV", None)
        env.pop("UV_PROJECT_ENVIRONMENT", None)

        for i, run_str in enumerate(run_steps, start=1):
            print(f"\n=== Step {i}/{len(run_steps)}: {run_str} ===")
            proc = subprocess.run(run_str, shell=True, cwd=tmp_dir, env=env)
            if proc.returncode != 0:
                print(
                    f"\nFAILED at step {i}/{len(run_steps)}: {run_str!r} "
                    f"(exit code {proc.returncode})",
                    file=sys.stderr,
                )
                return proc.returncode

        print(f"\nAll {len(run_steps)} step(s) passed.")
        return 0
    finally:
        if args.keep:
            print(f"--keep passed: export left at {tmp_dir}")
        else:
            shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
