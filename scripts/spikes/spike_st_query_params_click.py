"""D-05 Wave-0 spike driver: clicks the PRODUCTION body-map renderer's region link
(`#hand-front`, wrapped in `<a href="?region=hand">` by `body_map.build_body_svg`'s
`link_regions=True` path) in headless Chromium and measures whether Streamlit 1.63's
`st.markdown`-rendered anchor click (a) navigates same-tab, (b) preserves `st.session_state`,
and (c) populates `st.query_params`. Mirrors `scripts/spikes/spike_st_html_svg.py`'s harness
exactly: PORT, health-check, `Popen` of `uv run streamlit run`, Playwright, one JSON verdict
line, `terminate` in `finally`.

Playwright's Chromium is already cached at ~/.cache/ms-playwright; playwright itself is
intentionally NOT added to pyproject.toml/uv.lock (T-03-SC -- ephemeral spike-only dependency).

Run: uv run --with playwright python scripts/spikes/spike_st_query_params_click.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PORT = 8598
HEALTH_URL = f"http://localhost:{PORT}/_stcore/health"
APP_URL = f"http://localhost:{PORT}"
REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).resolve().parent / "st_query_params_click_page.py"

_PROBE_RE = re.compile(r"PROBE_REGION=(\S+) PROBE_TOKEN=(\S+)")
_WAIT_SELECTOR = "#hand-front"


def _wait_for_health(timeout_s: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=1) as resp:  # noqa: S310
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.5)
    return False


def _read_probe(page) -> tuple[str | None, str | None]:
    """Reads the `PROBE_REGION=... PROBE_TOKEN=...` line from the page body. Returns
    `(None, None)` if the probe line isn't present (e.g. a navigated-away/blank page)."""
    try:
        text = page.inner_text("body")
    except Exception:  # noqa: BLE001 -- page may be closed/navigating; treat as unreadable
        return None, None
    m = _PROBE_RE.search(text)
    if not m:
        return None, None
    return m.group(1), m.group(2)


def _measure(browser, selector: str, expected_region: str) -> dict:
    """One fresh browser context: goto, read the probe before click, click `selector`, wait for
    load/network-idle plus 1.5s, then re-read the probe from every page in the context (a click
    that opens a new tab is itself a measured outcome, not a harness failure)."""
    context = browser.new_context()
    try:
        page = context.new_page()
        page.goto(APP_URL)
        try:
            page.wait_for_selector(_WAIT_SELECTOR, timeout=15_000)
        except Exception:  # noqa: BLE001
            return {"verdict": "harness_failure", "reason": f"{_WAIT_SELECTOR!r} missing on load"}
        region_before, token_before = _read_probe(page)

        try:
            page.click(selector, timeout=15_000)
        except Exception:  # noqa: BLE001
            return {"verdict": "harness_failure", "reason": f"click failed on {selector!r}"}

        try:
            page.wait_for_load_state("load", timeout=10_000)
        except Exception:  # noqa: BLE001
            pass
        try:
            page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:  # noqa: BLE001
            pass
        page.wait_for_timeout(1_500)

        pages = context.pages
        opens_new_tab = len(pages) > 1

        region_after: str | None = None
        token_after: str | None = None
        for pg in pages:
            r, t = _read_probe(pg)
            if r is not None:
                region_after, token_after = r, t
                break

        expected_repr = repr(expected_region)
        if opens_new_tab:
            verdict = "click_opens_new_tab"
        elif region_after != expected_repr:
            verdict = "click_query_param_missing"
        elif token_after != token_before:
            verdict = "click_ok_session_lost"
        else:
            verdict = "click_ok_session_preserved"

        return {
            "verdict": verdict,
            "region_before": region_before,
            "token_before": token_before,
            "region_after": region_after,
            "token_after": token_after,
            "page_count_after": len(pages),
        }
    finally:
        context.close()


def main() -> int:
    proc = subprocess.Popen(  # noqa: S603
        [
            "uv",
            "run",
            "streamlit",
            "run",
            str(SCRIPT),
            "--server.headless",
            "true",
            "--server.port",
            str(PORT),
        ],
        cwd=str(REPO_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if not _wait_for_health():
            print(
                json.dumps(
                    {"verdict": "harness_failure", "details": {"reason": "server never healthy"}}
                )
            )
            return 1

        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                production = _measure(browser, "#hand-front", "hand")
                if production["verdict"] == "harness_failure":
                    print(json.dumps({"verdict": "harness_failure", "details": production}))
                    return 1
                control = _measure(browser, "#control-front", "finger")
            finally:
                browser.close()

        details = {**production, "control": control}
        print(json.dumps({"verdict": production["verdict"], "details": details}))
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
