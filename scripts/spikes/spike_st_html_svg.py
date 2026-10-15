"""Wave-0 spike A1 driver: launches `st_html_svg_page.py` headlessly, inspects the rendered DOM
with Playwright to determine whether `st.html` (or, as a compared alternative,
`st.markdown(..., unsafe_allow_html=True)`) preserves inline <style>/<pattern>/<title> SVG
children, and whether Google-Fonts @import/<link> forms survive under each mechanism.

Playwright's Chromium is already cached at ~/.cache/ms-playwright; playwright itself is
intentionally NOT added to pyproject.toml/uv.lock (T-03-SC -- ephemeral spike-only dependency).

Run: uv run --with playwright python scripts/spikes/spike_st_html_svg.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PORT = 8599
HEALTH_URL = f"http://localhost:{PORT}/_stcore/health"
APP_URL = f"http://localhost:{PORT}"
REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).resolve().parent / "st_html_svg_page.py"

_DOM_PROBE_JS = """
() => {
  function probe(prefix) {
    const hand = document.getElementById(prefix + '-hand-front');
    const finger = document.getElementById(prefix + '-finger-front');
    const titles = hand && finger
      ? [hand.querySelector('title'), finger.querySelector('title')].filter(Boolean)
      : [];
    return {
      hand_present: !!hand,
      finger_present: !!finger,
      pattern_present: !!document.getElementById(prefix + '-hatch'),
      computed_fill: hand ? getComputedStyle(hand).fill : null,
      hatch_fill_attribute: finger ? finger.getAttribute('fill') : null,
      title_count: titles.length,
      title_texts: titles.map((t) => t.textContent),
    };
  }
  const styleEls = Array.from(document.querySelectorAll('style'));
  const linkEls = Array.from(document.querySelectorAll('link[rel="stylesheet"]'));
  return {
    st_html: probe('stHtml'),
    st_markdown: probe('stMarkdown'),
    at_import_survived: styleEls.some((s) => s.textContent.includes('@import')),
    font_link_survived: linkEls.some(
      (l) => (l.getAttribute('href') || '').includes('fonts.googleapis.com')
    ),
    iframe_count: document.querySelectorAll('iframe').length,
  };
}
"""


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


def _mechanism_ok(m: dict) -> bool:
    fill_ok = m["computed_fill"] in ("rgb(91, 141, 239)", "#5b8def", "#5B8DEF")
    return bool(
        m["hand_present"]
        and m["finger_present"]
        and m["pattern_present"]
        and fill_ok
        and m["title_count"] >= 2
        and (m["hatch_fill_attribute"] or "").startswith("url(#")
    )


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
            page = browser.new_page()
            page.goto(APP_URL)
            page.wait_for_selector("svg", timeout=15_000)
            page.wait_for_timeout(1_000)
            result = page.evaluate(_DOM_PROBE_JS)
            browser.close()

        st_html_ok = _mechanism_ok(result["st_html"])
        st_markdown_ok = _mechanism_ok(result["st_markdown"])
        result["st_html_svg_ok"] = st_html_ok
        result["st_markdown_svg_ok"] = st_markdown_ok

        if st_html_ok:
            verdict = "svg_ok"
        elif st_markdown_ok:
            verdict = "svg_ok_via_markdown"
        else:
            verdict = "svg_stripped"
        print(json.dumps({"verdict": verdict, "details": result}))
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
