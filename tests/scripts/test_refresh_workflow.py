"""Local contract tests for .github/workflows/refresh-data.yml (D-04/D-05, OPS-03).

Loads the workflow with PyYAML (handling the `on:`/`True` boolean-key quirk, same as
test_ci_workflow.py) and also reads the raw text lines for pin-comment/injection checks that
aren't part of YAML's data model.
"""

from __future__ import annotations

import re

import yaml

from wii.data.sync import _WINDOW_SIZE
from wii.utils.paths import repo_root

_PATH = repo_root() / ".github" / "workflows" / "refresh-data.yml"
_USES_PIN_RE = re.compile(
    r"^\s*-?\s*uses:\s*([\w.-]+/[\w.-]+(?:/[\w./-]+)?)@([0-9a-f]{40})\s*#\s*(v\S+)"
)
_EXPR_OPEN = "$" + "{{"  # avoid a literal ${{ in this file's own source


def _load() -> dict:
    return yaml.safe_load(_PATH.read_text())


def _raw_lines() -> list[str]:
    return _PATH.read_text().splitlines()


def test_refresh_workflow_exists():
    assert _PATH.is_file()


def test_triggers_are_exactly_workflow_dispatch_and_schedule():
    workflow = _load()
    triggers = workflow.get("on", workflow.get(True))
    assert triggers is not None, "no `on:` trigger block found"
    assert set(triggers.keys()) == {"workflow_dispatch", "schedule"}


def test_cron_is_monthly_not_daily():
    workflow = _load()
    triggers = workflow.get("on", workflow.get(True))
    schedule = triggers["schedule"]
    assert len(schedule) == 1
    cron = schedule[0]["cron"]
    fields = cron.split()
    assert len(fields) == 5, f"unexpected cron field count: {cron!r}"
    _minute, _hour, day_of_month, _month, day_of_week = fields
    assert day_of_month.isdigit(), f"cron day-of-month is not a single number: {cron!r}"
    assert day_of_week == "*", f"cron day-of-week is not a wildcard (would be daily-ish): {cron!r}"


def test_workflow_permissions_is_contents_read_only():
    workflow = _load()
    assert workflow["permissions"] == {"contents": "read"}


def test_only_open_pr_job_has_any_write_permission():
    workflow = _load()
    for name, job in workflow["jobs"].items():
        job_perms = job.get("permissions") or {}
        write_scopes = {scope for scope, level in job_perms.items() if level == "write"}
        if name == "open-pr":
            assert write_scopes == {"contents", "pull-requests"}, (
                f"open-pr permissions were {job_perms!r}"
            )
        else:
            assert not write_scopes, f"job {name!r} unexpectedly grants write: {job_perms!r}"


def test_every_uses_line_is_pinned_to_a_sha_with_version_comment():
    uses_lines = [line for line in _raw_lines() if re.match(r"^\s*-?\s*uses:\s*\S", line)]
    assert uses_lines, "no uses: lines found in refresh-data.yml"
    for line in uses_lines:
        assert _USES_PIN_RE.match(line), (
            f"uses: line not pinned to a 40-hex-char SHA with a trailing # vX.Y.Z comment: {line!r}"
        )


def test_no_secrets_reference():
    full_text = _PATH.read_text()
    assert "secrets." not in full_text


def test_no_continue_on_error_anywhere():
    full_text = _PATH.read_text()
    assert "continue-on-error" not in full_text


def test_no_run_step_contains_git_push():
    workflow = _load()
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            run_text = step.get("run", "")
            assert "git push" not in run_text


def test_no_run_step_interpolates_an_expression():
    """D-09/T-06-09: expression values must pass through env:, never be inlined in run: text."""
    workflow = _load()
    for job_name, job in workflow["jobs"].items():
        for step in job.get("steps", []):
            run_text = step.get("run", "")
            assert _EXPR_OPEN not in run_text, (
                f"job {job_name!r} step has an inline expression in run: text: {run_text!r}"
            )


def test_restore_and_save_step_counts_equal_window_size_with_zero_padded_ids():
    workflow = _load()
    steps = workflow["jobs"]["refresh"]["steps"]
    restore_ids = sorted(s["id"] for s in steps if str(s.get("id", "")).startswith("restore_"))
    save_ids = sorted(s["id"] for s in steps if str(s.get("id", "")).startswith("save_"))
    expected_restore = [f"restore_{i:02d}" for i in range(1, _WINDOW_SIZE + 1)]
    expected_save = [f"save_{i:02d}" for i in range(1, _WINDOW_SIZE + 1)]
    assert restore_ids == expected_restore
    assert save_ids == expected_save


def test_refresh_job_gates_on_new_year_status():
    workflow = _load()
    refresh = workflow["jobs"]["refresh"]
    assert refresh["needs"] == "check"
    assert "new_year" in refresh["if"]


def test_open_pr_job_needs_refresh():
    workflow = _load()
    open_pr = workflow["jobs"]["open-pr"]
    needs = open_pr["needs"]
    assert "refresh" in needs
    assert "check" in needs


def test_sync_step_uses_latest_and_not_offline():
    workflow = _load()
    steps = workflow["jobs"]["refresh"]["steps"]
    sync_steps = [s for s in steps if "sync_neiss.py" in s.get("run", "")]
    assert len(sync_steps) == 1
    run_text = sync_steps[0]["run"]
    assert "--latest" in run_text
    assert "--offline" not in run_text


def test_realdata_and_validate_classifications_steps_exist_and_precede_upload():
    workflow = _load()
    steps = workflow["jobs"]["refresh"]["steps"]
    run_texts = [s.get("run", "") for s in steps]
    validate_idx = next(i for i, t in enumerate(run_texts) if "validate_classifications.py" in t)
    realdata_idx = next(i for i, t in enumerate(run_texts) if "-m realdata" in t)
    upload_idx = next(i for i, s in enumerate(steps) if "upload-artifact" in str(s.get("uses", "")))
    assert validate_idx < upload_idx
    assert realdata_idx < upload_idx


def test_create_pull_request_base_is_main_and_branch_starts_with_data_refresh():
    workflow = _load()
    steps = workflow["jobs"]["open-pr"]["steps"]
    pr_steps = [s for s in steps if "create-pull-request" in str(s.get("uses", ""))]
    assert len(pr_steps) == 1
    with_block = pr_steps[0]["with"]
    assert with_block["base"] == "main"
    assert with_block["branch"].startswith("data-refresh/")


def test_check_job_outputs_status_and_latest_year():
    workflow = _load()
    outputs = workflow["jobs"]["check"]["outputs"]
    assert set(outputs.keys()) == {"status", "latest_year"}
