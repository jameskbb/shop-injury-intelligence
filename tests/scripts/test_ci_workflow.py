"""Local contract tests for .github/workflows/ci.yml (D-02, 06-01 Task 2). Loads the workflow with
PyYAML (handling the `on:`/`True` boolean-key quirk -- YAML 1.1 treats the bare `on` key as the
boolean `True`, so PyYAML's `safe_load` stores the trigger config under `workflow[True]`, not
`workflow["on"]`) and also reads the raw text lines for the pin-comment regex, since a `uses:` SHA
pin's trailing version comment isn't part of YAML's data model.
"""

from __future__ import annotations

import re

import yaml

from wii.utils.paths import repo_root

_CI_PATH = repo_root() / ".github" / "workflows" / "ci.yml"
_EXPECTED_RUN_STEPS = [
    "uv sync --locked",
    "uv run ruff check .",
    "uv run ruff format --check .",
    "uv run pytest -q",
]
_USES_PIN_RE = re.compile(
    r"^\s*-?\s*uses:\s*([\w.-]+/[\w.-]+(?:/[\w./-]+)?)@([0-9a-f]{40})\s*#\s*(v\S+)"
)


def _load() -> dict:
    return yaml.safe_load(_CI_PATH.read_text())


def _raw_lines() -> list[str]:
    return _CI_PATH.read_text().splitlines()


def test_ci_yml_exists():
    assert _CI_PATH.is_file()


def test_triggers_are_exactly_push_and_pull_request_on_main_plus_dispatch():
    workflow = _load()
    # PyYAML boolean-key quirk: the bare `on:` key loads as the Python boolean True.
    triggers = workflow.get("on", workflow.get(True))
    assert triggers is not None, "no `on:` trigger block found"
    assert set(triggers.keys()) == {"push", "pull_request", "workflow_dispatch"}, (
        f"unexpected trigger set: {set(triggers.keys())}"
    )
    assert triggers["push"]["branches"] == ["main"]
    assert triggers["pull_request"]["branches"] == ["main"]
    assert "pull_request_target" not in triggers


def test_workflow_permissions_is_contents_read_only():
    workflow = _load()
    assert workflow["permissions"] == {"contents": "read"}


def test_no_job_level_permissions_grants_write():
    workflow = _load()
    for job in workflow["jobs"].values():
        job_perms = job.get("permissions")
        if job_perms is None:
            continue
        for scope, level in job_perms.items():
            assert level != "write", f"job-level permission {scope!r} grants write"


def test_concurrency_cancels_in_progress():
    workflow = _load()
    assert workflow["concurrency"]["cancel-in-progress"] is True


def test_setup_uv_python_version_and_cache():
    workflow = _load()
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            if str(step.get("uses", "")).startswith("astral-sh/setup-uv"):
                with_block = step.get("with", {})
                assert with_block.get("python-version") == "3.13"
                assert with_block.get("enable-cache") is True
                return
    raise AssertionError("no astral-sh/setup-uv step found")


def test_every_uses_line_is_pinned_to_a_sha_with_version_comment():
    uses_lines = [line for line in _raw_lines() if re.match(r"^\s*-?\s*uses:\s*\S", line)]
    assert uses_lines, "no uses: lines found in ci.yml"
    for line in uses_lines:
        assert _USES_PIN_RE.match(line), (
            f"uses: line not pinned to a 40-hex-char SHA with a trailing # vX.Y.Z comment: {line!r}"
        )


def test_run_steps_are_exactly_the_four_d02_commands_in_order():
    workflow = _load()
    run_steps = [
        step["run"]
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if "run" in step
    ]
    assert run_steps == _EXPECTED_RUN_STEPS


def test_no_step_mentions_sync_neiss_realdata_or_secrets():
    banned = ("sync_neiss", "realdata", "secrets.")
    full_text = _CI_PATH.read_text()
    for term in banned:
        assert term not in full_text, f"ci.yml must not reference {term!r}"
