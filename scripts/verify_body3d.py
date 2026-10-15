"""Browser verification harness for the Body Map page's 3D injury figure (dev-only, never
imported by app code or CI -- AppTest cannot render WebGL, so the pixel-level checks live here).

Launches the REAL app headless, opens `/body-map` in headless Chromium with software WebGL
(SwiftShader), and checks, printing one PASS/FAIL line each:

  canvas     the 3D canvas exists and actually renders (sampled pixels are not all background)
  drag       a mouse drag on the canvas changes the rendered view (pixel diff before/after)
  click      a click on a region updates "Showing supporting data for: <Region>." and ?region=
  reclick    clicking the same spot again clears the selection (the pick_region toggle)
  hand       the "Hand" view control zooms the view (pixel diff vs. the whole-body view)
  drilldown  picking a tool plays the choreography: exactly ONE camera flight, of about the
             length it claims, with the camera still afterwards while the arrival pulse runs

Design-review fixes, each measured rather than claimed:

  wholebody  no tool picked: settled camera distance (hook) is whole-body, not hand (~0.59);
             the silhouette spans the canvas unclipped; the `.b3d-tool` banner names no tool
  drilltop   picking the drill tool lands the camera on its #1 region (anchor projects near the
             canvas centre, closer than the chest)
  focus      clicking a region dims the non-selected regions (median patch luminance ratio)
             and moves the camera toward the selection
  linework   every region's contour matches its stability: NO contour when stable (the fill is
             what says "region"), a dashed construction contour when limited or suppressed, and
             the faintest solid hairline when there are no recorded cases
  luma       mean figure luminance vs background luminance
  keyboard   ArrowLeft on the focused canvas turns the view; Escape clears a selection
  fingers    all five finger meshes share one colour (via the hook)
  fallback   with cdn.jsdelivr.net blocked: flat 2D map renders, copy says the viewer could not
             load (no WebGL claim), and no empty 3D panel remains

Screenshots land in --out (default: the system temp dir's `wii-verify-body3d/`):
  body3d-default.png, body3d-after-drag.png, body3d-hand-zoom.png, body3d-drilldown.png,
  body3d-default-settled.png, body3d-drilltop.png, body3d-focus-{before,after,same-view}.png,
  body3d-linework.png, body3d-keyboard-{left,escape}.png, body3d-fingers-hand.png,
  page-fallback.png, plus full-viewport page captures (page-*.png) for context.

Run (playwright is intentionally absent from pyproject.toml/uv.lock -- ephemeral, dev-only):
    uv run --with playwright==1.62.0 python scripts/verify_body3d.py [--port 8860] [--out DIR]

One-time Chromium install:
    uv run --with playwright==1.62.0 python -m playwright install chromium

Process hygiene: the only process this script ever stops is the Streamlit server it started
itself, by the PID (and that PID's own process group) it recorded at launch.

Exit status: 0 when every check passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import io
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qs, urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORT = 8860
ALLOWED_PORTS = range(8860, 8870)
VIEWPORT = {"width": 1440, "height": 1000}
# Not the workshop-wide top tool (with no tool picked, the figure's intro already drills into the
# top tool's hotspots), and one whose hotspots reach past the hands (eye first) with enough cases
# for stable estimates, so the fly-to has somewhere visibly different to go.
DEFAULT_TOOL_LABEL = "Power Grinder"

# A view "changed" when at least this fraction of canvas pixels moved by more than
# PIXEL_DELTA (0-255 per channel, max over RGB). SwiftShader output is deterministic for a static
# scene, so a still frame diffs at ~0 and any real camera move clears this easily.
CHANGED_FRACTION = 0.01
PIXEL_DELTA = 24
# "Non-blank": at least this fraction of pixels differs from the dominant (background) colour.
NONBLANK_FRACTION = 0.02

ALL_CHECKS = (
    "canvas",
    "drag",
    "click",
    "reclick",
    "hand",
    "drilldown",
    "wholebody",
    "drilltop",
    "focus",
    "linework",
    "luma",
    "keyboard",
    "fingers",
    "fallback",
)

BROWSER_ARGS = ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"]


# ---------------------------------------------------------------------------
# Pure image helpers
# ---------------------------------------------------------------------------


def _to_array(png: bytes):
    import numpy as np
    from PIL import Image

    return np.asarray(Image.open(io.BytesIO(png)).convert("RGB")).astype("int16")


def nonblank_fraction(png: bytes) -> float:
    """Fraction of pixels that differ (by > PIXEL_DELTA) from the image's most common colour --
    an all-background canvas (WebGL context lost, nothing drawn) scores ~0."""
    import numpy as np

    arr = _to_array(png)
    flat = arr.reshape(-1, 3)
    colors, counts = np.unique(flat, axis=0, return_counts=True)
    background = colors[counts.argmax()]
    delta = np.abs(flat - background).max(axis=1)
    return float((delta > PIXEL_DELTA).mean())


def _figure_mask(png: bytes):
    import numpy as np

    flat = _to_array(png)
    colors, counts = np.unique(flat.reshape(-1, 3), axis=0, return_counts=True)
    background = colors[counts.argmax()]
    return np.abs(flat - background).max(axis=2) > PIXEL_DELTA


def silhouette_change(before: bytes, after: bytes) -> float:
    """Fraction of pixels that switched between figure and background -- a camera move changes
    it, a recolour of the regions alone (a new filter's values) does not."""
    a, b = _figure_mask(before), _figure_mask(after)
    if a.shape != b.shape:
        return 1.0
    return float((a ^ b).mean())


def changed_fraction(before: bytes, after: bytes) -> float:
    import numpy as np

    a, b = _to_array(before), _to_array(after)
    if a.shape != b.shape:
        return 1.0
    return float((np.abs(a - b).max(axis=2) > PIXEL_DELTA).mean())


# ---------------------------------------------------------------------------
# Server lifecycle (only ever touches the process this script started)
# ---------------------------------------------------------------------------


def _launch_server(port: int) -> subprocess.Popen:
    # The interpreter running this script already has the project env (uv run --with layers
    # playwright on top of it), so streamlit runs as ONE child process whose PID we own. A new
    # session makes that PID its own process-group leader, so stopping the group reaches only
    # processes this server spawned.
    return subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            "app.py",
            "--server.headless",
            "true",
            "--server.port",
            str(port),
            "--server.address",
            "127.0.0.1",
            "--browser.gatherUsageStats",
            "false",
        ],
        cwd=str(REPO_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _stop_server(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)  # group led by OUR recorded PID only
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=5)


def _port_in_use(port: int) -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=1)  # noqa: S310
    except urllib.error.URLError:
        return False
    except Exception:  # noqa: BLE001 -- anything answering means the port is taken
        return True
    return True


def _wait_healthy(port: int, proc: subprocess.Popen, timeout_s: float = 90.0) -> None:
    url = f"http://127.0.0.1:{port}/_stcore/health"
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"streamlit exited early with code {proc.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310
                if resp.status == 200:
                    return
        except Exception:  # noqa: BLE001 -- not up yet
            pass
        time.sleep(0.5)
    raise RuntimeError(f"streamlit did not become healthy on port {port}")


# ---------------------------------------------------------------------------
# Browser helpers
# ---------------------------------------------------------------------------


def _browser_env() -> dict[str, str]:
    """This process's env minus WAYLAND_DISPLAY: under WSLg its mere presence makes headless
    Chromium's screenshot call hang forever (see scripts/capture_screenshots.py)."""
    return {k: v for k, v in os.environ.items() if k != "WAYLAND_DISPLAY"}


def _is_base_path_probe(url: str) -> bool:
    path = urlparse(url).path
    return path.endswith(("/_stcore/health", "/_stcore/host-config")) and not path.startswith(
        "/_stcore/"
    )


def _wait_render_complete(page) -> None:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    page.wait_for_selector('[data-testid="stMain"] h1', timeout=60_000)
    try:
        page.wait_for_selector('[data-testid="stStatusWidget"]', state="attached", timeout=2_000)
        page.wait_for_selector('[data-testid="stStatusWidget"]', state="detached", timeout=60_000)
    except PlaywrightTimeoutError:
        pass
    page.wait_for_function(
        "() => document.querySelectorAll('[data-testid=\"stSpinner\"]').length === 0",
        timeout=90_000,
    )


def _find_canvas(page, timeout_s: float = 60.0):
    """The 3D figure's <canvas>, wherever the component mounts it: inside a component iframe
    (st.components v1) or inline in the main document (v2; CSS locators pierce open shadow
    roots). Returns (locator, where) or (None, reason)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            loc = frame.locator("canvas")
            try:
                if loc.count() and loc.first.bounding_box():
                    return loc.first, f"iframe {frame.url[:60]}"
            except Exception:  # noqa: BLE001 -- frame detached mid-poll
                continue
        loc = page.locator('[data-testid="stMain"] canvas')
        # Plotly draws no <canvas> for the page's charts (SVG), but guard by size anyway.
        for i in range(loc.count()):
            box = loc.nth(i).bounding_box()
            if box and box["width"] >= 200 and box["height"] >= 200:
                return loc.nth(i), "main document"
        page.wait_for_timeout(500)
    return None, "no <canvas> found within timeout"


def _settle(page, canvas, *, quiet_ms: int = 600, timeout_s: float = 15.0) -> bytes:
    """Canvas screenshot once two consecutive frames `quiet_ms` apart are identical (camera
    animations and damping have finished)."""
    deadline = time.monotonic() + timeout_s
    last = canvas.screenshot()
    while time.monotonic() < deadline:
        page.wait_for_timeout(quiet_ms)
        current = canvas.screenshot()
        if changed_fraction(last, current) < 0.001:
            return current
        last = current
    return last


def _support_caption(page) -> str | None:
    loc = page.locator(
        '[data-testid="stMain"] [data-testid="stCaptionContainer"]',
        has_text=("Showing supporting data for:"),
    )
    if loc.count() == 0:
        return None
    return loc.first.inner_text().strip()


def _region_param(page) -> str | None:
    values = parse_qs(urlparse(page.url).query).get("region")
    return values[0] if values else None


def _wait_rerun(page) -> None:
    page.wait_for_timeout(400)
    _wait_render_complete(page)


def _control(page, canvas_where: str, selector: str):
    """A control inside the component (same frame as the canvas)."""
    for frame in page.frames:
        loc = frame.locator(selector)
        try:
            if loc.count():
                return loc.first
        except Exception:  # noqa: BLE001
            continue
    return None


def _whole_body(page, canvas) -> bytes:
    """Resets the camera to the whole-body view (the intro drills into a tool's hotspots) and
    returns the settled frame."""
    btn = _control(page, "", 'button[data-view="body"]')
    if btn is not None:
        btn.click()
    return _settle(page, canvas)


# ---------------------------------------------------------------------------
# Review-fix measurements (pure image maths + the read-only window.__wiiBody3d hook)
# ---------------------------------------------------------------------------

# Hand view sits ~0.59 from its target; the whole-body view ~4.0. Anything under this is a
# close-up, not a whole-figure framing.
WHOLE_BODY_MIN_DISTANCE = 1.5
# Whole-figure framing: the silhouette spans at least this fraction of the canvas height...
WHOLE_BODY_MIN_HEIGHT_FRAC = 0.45
# Drill-down: the #1 region's anchor lands within this fraction of the canvas' shorter side
# of the canvas centre (where the camera target projects).
DRILL_CENTER_FRAC = 0.12
# Focus treatment: on the vellum sheet a non-selected region recedes by fading TOWARD the page,
# so its patch luminance must RISE by at least this fraction (it used to fall toward black).
FOCUS_MIN_FADE = 0.10
# Figure vs background: the ink is darker than the sheet, so the margin runs background - figure.
FIGURE_MIN_LUMA_MARGIN = 24.0

FOCUS_PROBE_REGIONS = (
    "head",
    "face",
    "neck",
    "shoulder",
    "upper_arm",
    "elbow",
    "lower_arm",
    "lower_trunk",
    "pubic_region",
    "upper_leg",
    "knee",
    "lower_leg",
    "ankle",
    "foot",
)


def _luma(arr):
    return 0.2126 * arr[..., 0] + 0.7152 * arr[..., 1] + 0.0722 * arr[..., 2]


def _background(arr):
    import numpy as np

    colors, counts = np.unique(arr.reshape(-1, 3), axis=0, return_counts=True)
    return colors[counts.argmax()]


def figure_vs_background_luma(png: bytes) -> tuple[float, float, float]:
    """(mean figure luminance, background luminance, figure pixel fraction)."""
    import numpy as np

    arr = _to_array(png)
    bg = _background(arr)
    mask = np.abs(arr - bg).max(axis=2) > PIXEL_DELTA
    if not mask.any():
        return 0.0, float(_luma(bg.astype(float))), 0.0
    return (
        float(_luma(arr[mask].astype(float)).mean()),
        float(_luma(bg.astype(float))),
        float(mask.mean()),
    )


def figure_extent(png: bytes) -> tuple[float, bool]:
    """(silhouette height as a fraction of the image height, clipped at top/bottom edge)."""
    import numpy as np

    mask = _figure_mask(png)
    rows = np.where(mask.mean(axis=1) > 0.004)[0]
    if rows.size == 0:
        return 0.0, False
    h = mask.shape[0]
    clipped = rows[0] <= 1 or rows[-1] >= h - 2
    return float((rows[-1] - rows[0] + 1) / h), bool(clipped)


def patch_luma(png: bytes, x: float, y: float, r: int = 4) -> float | None:
    """Median luminance of a (2r+1)^2 patch at canvas-local (x, y), or None off-canvas/bg."""
    import numpy as np

    arr = _to_array(png)
    h, w = arr.shape[:2]
    xi, yi = int(round(x)), int(round(y))
    if not (r <= xi < w - r and r <= yi < h - r):
        return None
    patch = arr[yi - r : yi + r + 1, xi - r : xi + r + 1].reshape(-1, 3)
    bg = _background(arr)
    fig = patch[np.abs(patch - bg).max(axis=1) > PIXEL_DELTA]
    if fig.shape[0] < patch.shape[0] // 2:
        return None
    return float(np.median(_luma(fig.astype(float))))


def _hook(page, body: str, arg=None):
    """Evaluates `body` with `api` bound to window.__wiiBody3d (null-safe)."""
    return page.evaluate(
        f"(arg) => {{ const api = window.__wiiBody3d; if (!api) return null; {body} }}", arg
    )


def _cam_state(page) -> dict | None:
    return _hook(page, "return api.state ? api.state() : null;")


def _project_local(page, canvas, region: str) -> tuple[float, float] | None:
    """A region anchor's position in canvas-local pixels (None if unknown or off-canvas)."""
    pt = _hook(page, "return api.project ? api.project(arg) : null;", region)
    box = canvas.bounding_box()
    if not pt or not box:
        return None
    x, y = pt["x"] - box["x"], pt["y"] - box["y"]
    if not (0 <= x < box["width"] and 0 <= y < box["height"]):
        return None
    return x, y


def _wait_camera_idle(page, timeout_s: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        st = _cam_state(page)
        if st is None or (not st.get("tweening") and not st.get("flashing")):
            return
        page.wait_for_timeout(150)


def _settled(page, canvas) -> bytes:
    _wait_camera_idle(page)
    return _settle(page, canvas)


def _pick_tool(page, label: str) -> None:
    page.locator('[data-testid="stMain"]').get_by_label("Tool", exact=True).click()
    page.wait_for_timeout(300)
    page.keyboard.type(label, delay=20)
    page.wait_for_timeout(300)
    page.locator('[role="option"]', has_text=label).first.click()
    _wait_rerun(page)


def _fresh(page, url: str):
    page.goto(url)
    _wait_render_complete(page)
    c, _ = _find_canvas(page)
    if c is None:
        raise RuntimeError("no canvas after reload")
    c.scroll_into_view_if_needed()
    return c


# Tallest rendered layer of the flat 2D body map (0 when it is absent).
_FLAT_MAP_JS = (
    "Math.max(0, ...Array.from(document.querySelectorAll('.wii-body-layer'),"
    " (e) => e.getBoundingClientRect().height))"
)


def _banner_text(page) -> tuple[bool, str]:
    """(visible, text) of the in-canvas `.b3d-tool` banner."""
    loc = page.locator(".b3d-tool")
    if loc.count() == 0:
        return False, ""
    el = loc.first
    visible = el.is_visible()
    return visible, (el.inner_text() if visible else el.text_content() or "").strip()


# The choreography is a behavioural contract (see the 3D body map review): selecting a tool
# dims, holds the whole body, flies ONCE to the #1 region, and pulses an outline that moves
# nothing. A rAF sampler in the page records the camera pose so the SHAPE of that can be
# asserted, rather than merely counting how many filmed frames happened to differ.
_POSE_SAMPLER_JS = """
(ms) => {
  window.__wiiPoses = [];
  const t0 = performance.now();
  const tick = () => {
    const api = window.__wiiBody3d;
    if (api && api.pose) {
      const p = api.pose();
      window.__wiiPoses.push([performance.now() - t0, ...p.position, ...p.target]);
    }
    if (performance.now() - t0 < ms) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
  return true;
}
"""
#: A sample counts as camera motion above this (world units between consecutive frames).
POSE_EPS = 2e-4
#: Samples this far apart with no motion between them are still one burst -- a dropped frame
#: mid-flight must not read as two flights.
BURST_GAP_MS = 180.0
#: The camera must be still for at least this long after the flight: the arrival pulse is an
#: outline, not a camera move.
POST_FLIGHT_STILL_MS = 500.0
#: `FLIGHT_MS` in body3d.js -- the turn-and-lean to the #1 hotspot.
FLIGHT_MS = 1050.0
#: Long enough to cover dim + hold + flight + pulse (MAX_SEQUENCE_MS) and the Streamlit rerun
#: that precedes them, with room left over to prove the camera then stays put.
SAMPLE_MS = 8000


def _motion_bursts(poses: list[list[float]]) -> list[tuple[float, float]]:
    """Contiguous (start_ms, end_ms) spans during which the camera pose changed."""
    bursts: list[list[float]] = []
    for (t0, *a), (t1, *b) in zip(poses, poses[1:], strict=False):
        if max(abs(x - y) for x, y in zip(a, b, strict=True)) <= POSE_EPS:
            continue
        if bursts and t0 - bursts[-1][1] <= BURST_GAP_MS:
            bursts[-1][1] = t1
        else:
            bursts.append([t0, t1])
    return [(a, b) for a, b in bursts]


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


class _AbortChecks(Exception):
    """Raised when a failed prerequisite makes the remaining checks meaningless."""


class Results:
    def __init__(self) -> None:
        self.rows: list[tuple[str, bool, str]] = []

    def record(self, name: str, ok: bool, detail: str) -> bool:
        self.rows.append((name, ok, detail))
        print(f"{'PASS' if ok else 'FAIL'}  {name:<10} {detail}", flush=True)
        return ok

    def guarded(self, name: str, fn: Callable[[], tuple[bool, str]]) -> bool:
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001 -- a crashed check is a FAIL, not an abort
            ok, detail = False, f"error: {type(exc).__name__}: {exc}"
        return self.record(name, ok, detail)

    @property
    def all_passed(self) -> bool:
        return bool(self.rows) and all(ok for _, ok, _ in self.rows)


def _click_candidates(box) -> list[tuple[float, float]]:
    """Points to try on the figure, torso first (the biggest target in any default framing),
    then down and up the centre line and out to the sides."""
    fx = [0.5, 0.46, 0.54, 0.42, 0.58, 0.35, 0.65]
    fy = [0.45, 0.4, 0.5, 0.35, 0.55, 0.3, 0.6, 0.25, 0.65, 0.7, 0.2, 0.75]
    pts = []
    for y in fy:
        for x in fx:
            pts.append((box["x"] + box["width"] * x, box["y"] + box["height"] * y))
    return pts


def run(port: int, out_dir: Path, tool_label: str, headed: bool) -> bool:
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    base_url = f"http://127.0.0.1:{port}"
    results = Results()

    if _port_in_use(port):
        print(f"FAIL  setup      port {port} is already serving something; pick another 8860-8869")
        return False

    proc = _launch_server(port)
    print(f"INFO  server     started streamlit pid={proc.pid} on port {port}", flush=True)
    try:
        _wait_healthy(port, proc)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not headed, args=BROWSER_ARGS, env=_browser_env())
            context = browser.new_context(viewport=VIEWPORT, device_scale_factor=1)
            page = context.new_page()
            console_errors: list[str] = []
            failed_urls: list[str] = []
            page.on(
                "response",
                lambda r: (
                    failed_urls.append(f"{r.status} {r.url}")
                    if r.status >= 400 and not _is_base_path_probe(r.url)
                    else None
                ),
            )
            page.on(
                "console",
                lambda m: console_errors.append(m.text) if m.type == "error" else None,
            )

            page.goto(f"{base_url}/body-map")
            _wait_render_complete(page)
            canvas, where = _find_canvas(page)
            page.screenshot(path=str(out_dir / "page-default.png"))

            # 1. canvas renders --------------------------------------------------------
            state: dict[str, bytes] = {}

            def check_canvas() -> tuple[bool, str]:
                if canvas is None:
                    return False, where
                canvas.scroll_into_view_if_needed()
                shot = _settle(page, canvas)
                state["default"] = shot
                (out_dir / "body3d-default.png").write_bytes(shot)
                frac = nonblank_fraction(shot)
                box = canvas.bounding_box()
                size = f"{int(box['width'])}x{int(box['height'])}" if box else "?"
                return (
                    frac >= NONBLANK_FRACTION,
                    f"{where}, {size}, {frac:.1%} non-background pixels",
                )

            if not results.guarded("canvas", check_canvas):
                for name in ALL_CHECKS[1:]:
                    results.record(name, False, "skipped: no rendered canvas")
                raise _AbortChecks

            # 2. drag rotates ----------------------------------------------------------
            def check_drag() -> tuple[bool, str]:
                box = canvas.bounding_box()
                cx, cy = box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.5
                page.mouse.move(cx - 120, cy)
                page.mouse.down()
                for i in range(1, 21):
                    page.mouse.move(cx - 120 + i * 12, cy + i * 2)
                    page.wait_for_timeout(16)
                page.mouse.up()
                after = _settle(page, canvas)
                (out_dir / "body3d-after-drag.png").write_bytes(after)
                frac = changed_fraction(state["default"], after)
                return frac >= CHANGED_FRACTION, f"{frac:.1%} of pixels changed after drag"

            results.guarded("drag", check_drag)

            # 3. click selects a region (fresh load so the camera is at its default) -----
            page.goto(f"{base_url}/body-map")
            _wait_render_complete(page)
            canvas, where = _find_canvas(page)
            canvas.scroll_into_view_if_needed()
            _settle(page, canvas)
            _whole_body(page, canvas)
            clicked: dict[str, tuple[float, float] | str] = {}

            def check_click() -> tuple[bool, str]:
                box = canvas.bounding_box()
                for x, y in _click_candidates(box):
                    page.mouse.click(x, y)
                    page.wait_for_timeout(300)
                    caption = None
                    for _ in range(20):
                        _wait_render_complete(page)
                        caption = _support_caption(page)
                        if caption:
                            break
                        page.wait_for_timeout(150)
                    if caption:
                        clicked["point"] = (x, y)
                        region = _region_param(page)
                        clicked["region"] = region or ""
                        page.screenshot(path=str(out_dir / "page-after-click.png"))
                        ok = region is not None
                        return ok, f"caption {caption!r}, ?region={region}"
                return False, "no click on the figure produced a 'Showing supporting data' caption"

            results.guarded("click", check_click)

            def check_reclick() -> tuple[bool, str]:
                if "point" not in clicked:
                    return False, "skipped: no region was selected"
                canvas_now, _ = _find_canvas(page, timeout_s=10)
                canvas_now.scroll_into_view_if_needed()
                _settle(page, canvas_now)
                x, y = clicked["point"]
                page.mouse.click(x, y)
                caption = "unset"
                for _ in range(20):
                    _wait_rerun(page)
                    caption = _support_caption(page)
                    if caption is None:
                        break
                region = _region_param(page)
                ok = caption is None and region is None
                return ok, f"after second click: caption={caption!r}, ?region={region}"

            results.guarded("reclick", check_reclick)

            # 4. hand zoom -------------------------------------------------------------
            def check_hand() -> tuple[bool, str]:
                page.goto(f"{base_url}/body-map")
                _wait_render_complete(page)
                c, _ = _find_canvas(page)
                c.scroll_into_view_if_needed()
                _settle(page, c)
                body = _whole_body(page, c)
                (out_dir / "body3d-whole-body.png").write_bytes(body)
                btn = _control(page, where, 'button[data-view="hand"]')
                if btn is not None:
                    btn.click()
                    how = "Hand view button"
                else:
                    page.goto(f"{base_url}/body-map?region=hand")
                    _wait_render_complete(page)
                    c, _ = _find_canvas(page)
                    c.scroll_into_view_if_needed()
                    how = "?region=hand deep link"
                zoomed = _settle(page, c)
                (out_dir / "body3d-hand-zoom.png").write_bytes(zoomed)
                page.screenshot(path=str(out_dir / "page-hand-zoom.png"))
                frac = changed_fraction(body, zoomed)
                return frac >= CHANGED_FRACTION, f"via {how}: {frac:.1%} of pixels changed"

            results.guarded("hand", check_hand)

            # 5. tool drill-down: the SHAPE of the choreography ------------------------
            def check_drilldown() -> tuple[bool, str]:
                """Selecting a tool is the one orchestrated moment in the product, and it is a
                contract, not a vibe: dim 250ms, hold the whole body 600ms, then ONE flight of
                FLIGHT_MS to the #1 region, then an outline pulse that moves nothing.

                So this films the camera POSE rather than counting how many screenshots happened
                to differ. Three things are asserted that a pixel diff cannot see: that there is
                exactly one flight (an establishing shot, or a queued second move, would show as
                two bursts), that it lasts about as long as it says it does (not a 2.5s tour),
                and that the camera is still afterwards while the arrival pulse plays.
                """
                page.goto(f"{base_url}/body-map")
                _wait_render_complete(page)
                c, _ = _find_canvas(page)
                c.scroll_into_view_if_needed()
                _settle(page, c)
                before = _whole_body(page, c)
                # Insurance against OrbitControls' damping tail being filmed as a first burst.
                _wait_camera_idle(page)
                page.evaluate(_POSE_SAMPLER_JS, SAMPLE_MS)
                _pick_tool(page, tool_label)
                # Dim 250ms + hold 600ms, so this lands mid-flight: one frame for the eye.
                page.wait_for_timeout(1200)
                c, _ = _find_canvas(page)
                try:
                    (out_dir / "body3d-drilldown.png").write_bytes(c.screenshot(timeout=3_000))
                except Exception:  # noqa: BLE001 -- the artefact is a nicety, not the check
                    pass
                # Wait for the sampler's own window to close rather than guessing a duration:
                # `_pick_tool` already spent an unknown slice of it typing and waiting on the
                # rerun, and the check needs the whole trace, tail included.
                page.wait_for_function(
                    "(ms) => { const p = window.__wiiPoses; "
                    "return Boolean(p && p.length && p[p.length - 1][0] >= ms - 120); }",
                    arg=SAMPLE_MS,
                    timeout=SAMPLE_MS + 15_000,
                )
                poses = page.evaluate("() => window.__wiiPoses || []")
                _wait_render_complete(page)
                c, _ = _find_canvas(page)
                after = _settle(page, c)
                (out_dir / "body3d-drilldown-settled.png").write_bytes(after)
                page.screenshot(path=str(out_dir / "page-drilldown.png"))

                bursts = _motion_bursts(poses)
                one_flight = len(bursts) == 1
                span = (bursts[0][1] - bursts[0][0]) if bursts else 0.0
                right_length = FLIGHT_MS * 0.55 <= span <= FLIGHT_MS * 1.7
                still_after = (
                    poses[-1][0] - bursts[-1][1] >= POST_FLIGHT_STILL_MS if bursts else False
                )
                moved = silhouette_change(before, after)
                dist = (_cam_state(page) or {}).get("distance")
                landed = dist is not None and dist < WHOLE_BODY_MIN_DISTANCE
                _, banner_text = _banner_text(page)
                ok = (
                    one_flight
                    and right_length
                    and still_after
                    and landed
                    and moved >= CHANGED_FRACTION
                    and tool_label in banner_text
                )
                shape = " + ".join(f"{a:.0f}-{b:.0f}ms" for a, b in bursts) or "none"
                return ok, (
                    f"tool {tool_label!r}: {len(bursts)} camera flight(s) [{shape}] over "
                    f"{len(poses)} pose samples (need exactly 1, {FLIGHT_MS * 0.55:.0f}-"
                    f"{FLIGHT_MS * 1.7:.0f}ms), still {'yes' if still_after else 'no'} for "
                    f"{POST_FLIGHT_STILL_MS:.0f}ms after it, landed at distance "
                    f"{dist if dist is None else round(dist, 2)}, silhouette {moved:.1%} changed, "
                    f"banner={banner_text[:60]!r}"
                )

            results.guarded("drilldown", check_drilldown)

            # ===== Review-fix checks ====================================================
            body_url = f"{base_url}/body-map"
            shots: dict[str, bytes] = {}

            # 6. default (no tool) frames the whole body -------------------------------
            def check_wholebody() -> tuple[bool, str]:
                c = _fresh(page, body_url)
                shot = _settled(page, c)
                shots["wholebody"] = shot
                (out_dir / "body3d-default-settled.png").write_bytes(shot)
                st_ = _cam_state(page) or {}
                dist = st_.get("distance")
                target = st_.get("target")
                height, clipped = figure_extent(shot)
                visible, text = _banner_text(page)
                # Keyed on the TOOL LABEL, not on the banner's sentence: the copy changed to
                # associative wording ("Areas most often recorded with X") and this check went
                # on passing vacuously, no longer able to catch a tool banner leaking onto the
                # no-tool view. A label is what the assertion is actually about.
                names_tool = visible and tool_label in text
                ok = (
                    dist is not None
                    and dist >= WHOLE_BODY_MIN_DISTANCE
                    and height >= WHOLE_BODY_MIN_HEIGHT_FRAC
                    and not clipped
                    and not names_tool
                )
                return ok, (
                    f"camera distance={dist if dist is None else round(dist, 3)} "
                    f"(need >= {WHOLE_BODY_MIN_DISTANCE}; hand ~0.59), target={target}, "
                    f"silhouette height={height:.0%} of canvas (need >= "
                    f"{WHOLE_BODY_MIN_HEIGHT_FRAC:.0%}), clipped={clipped}, "
                    f"banner visible={visible} text={text[:70]!r}"
                )

            results.guarded("wholebody", check_wholebody)

            # 7. drill-down lands on the tool's #1 region --------------------------------
            def check_drilltop() -> tuple[bool, str]:
                c = _fresh(page, body_url)
                _settled(page, c)
                _pick_tool(page, tool_label)
                c, _ = _find_canvas(page)
                shot = _settled(page, c)
                (out_dir / "body3d-drilltop.png").write_bytes(shot)
                box = c.bounding_box()
                centre = (box["width"] / 2, box["height"] / 2)
                _, text = _banner_text(page)
                # Banner: "Most-injured areas with <Tool>: Eye, Face, ..." -> first label.
                first = text.split(":", 1)[1].split(",")[0].strip() if ":" in text else ""
                guess = first.lower().replace(" ", "_")
                candidates = [guess, {"eyeball": "eye"}.get(guess, guess), "eye", "face"]
                top = next(
                    (r for r in candidates if r and _hook(page, "return api.project(arg);", r)),
                    None,
                )
                if top is None:
                    return False, f"could not resolve #1 region from banner {text[:70]!r}"
                pt = _project_local(page, c, top)
                chest = _project_local(page, c, "upper_trunk")
                short = min(box["width"], box["height"])
                d_top = (
                    ((pt[0] - centre[0]) ** 2 + (pt[1] - centre[1]) ** 2) ** 0.5 / short
                    if pt
                    else float("inf")
                )
                d_chest = (
                    ((chest[0] - centre[0]) ** 2 + (chest[1] - centre[1]) ** 2) ** 0.5 / short
                    if chest
                    else float("inf")
                )
                st_ = _cam_state(page) or {}
                ok = d_top <= DRILL_CENTER_FRAC and d_top < d_chest
                return ok, (
                    f"tool {tool_label!r}, #1 region {top!r} (banner first={first!r}): "
                    f"anchor {d_top:.3f} of canvas from centre (need <= {DRILL_CENTER_FRAC}), "
                    f"upper_trunk {d_chest:.3f}, distance={st_.get('distance')}"
                )

            results.guarded("drilltop", check_drilltop)

            # 8. selection is visible: others dim, camera moves toward the pick ---------
            def check_focus() -> tuple[bool, str]:
                c = _fresh(page, body_url)
                _settled(page, c)
                before = _whole_body(page, c)
                _wait_camera_idle(page)
                before = _settle(page, c)
                (out_dir / "body3d-focus-before.png").write_bytes(before)
                target = "upper_trunk"
                pt = _project_local(page, c, target)
                if pt is None:
                    return False, f"{target} anchor not projectable"
                box = c.bounding_box()
                centre = (box["width"] / 2, box["height"] / 2)
                off_before = ((pt[0] - centre[0]) ** 2 + (pt[1] - centre[1]) ** 2) ** 0.5
                dist_before = (_cam_state(page) or {}).get("distance")
                probes_before = {}
                for r in FOCUS_PROBE_REGIONS:
                    p_ = _project_local(page, c, r)
                    if p_:
                        v = patch_luma(before, *p_)
                        if v is not None:
                            probes_before[r] = v
                page.mouse.click(box["x"] + pt[0], box["y"] + pt[1])
                _wait_rerun(page)
                c, _ = _find_canvas(page)
                after = _settled(page, c)
                (out_dir / "body3d-focus-after.png").write_bytes(after)
                selected = (_cam_state(page) or {}).get("selected")
                pt2 = _project_local(page, c, target)
                off_after = (
                    ((pt2[0] - centre[0]) ** 2 + (pt2[1] - centre[1]) ** 2) ** 0.5
                    if pt2
                    else float("inf")
                )
                dist_after = (_cam_state(page) or {}).get("distance")
                # Focus treatment, measured at the SAME camera pose: fly back to the whole-body
                # view (the selection stays), then compare each non-selected region's patch with
                # the pre-selection frame pixel for pixel.
                _whole_body(page, c)
                same_view = _settled(page, c)
                (out_dir / "body3d-focus-same-view.png").write_bytes(same_view)
                still_selected = (_cam_state(page) or {}).get("selected")
                ratios = []
                for r, lb in probes_before.items():
                    if r == selected:
                        continue
                    p_ = _project_local(page, c, r)
                    la = patch_luma(same_view, *p_) if p_ else None
                    if la is not None and lb > 0:
                        ratios.append(la / lb)
                ratios.sort()
                med = ratios[len(ratios) // 2] if ratios else None
                sel_b = patch_luma(before, *pt)
                pt3 = _project_local(page, c, target)
                sel_a = patch_luma(same_view, *pt3) if pt3 else None
                sel_ratio = sel_a / sel_b if sel_a and sel_b else None
                faded = med is not None and med >= 1 + FOCUS_MIN_FADE
                moved = off_after < off_before - 5 or (
                    dist_before is not None
                    and dist_after is not None
                    and dist_after < dist_before - 0.1
                )
                ok = bool(selected) and still_selected == selected and faded and moved
                med_s = f"{med:.2f}" if med is not None else "n/a"
                sel_s = f"{sel_ratio:.2f}" if sel_ratio is not None else "n/a"
                return ok, (
                    f"selected={selected!r} (still {still_selected!r} back at whole-body view); "
                    f"non-selected luma after/before median={med_s} over {len(ratios)} regions "
                    f"(need >= {1 + FOCUS_MIN_FADE:.2f}), selected region {sel_s}; "
                    f"fly-to: anchor offset from centre {off_before:.0f}px -> {off_after:.0f}px, "
                    f"distance {dist_before:.2f} -> {dist_after:.2f}"
                )

            results.guarded("focus", check_focus)

            # 9. LINE TREATMENT = CONFIDENCE ------------------------------------------
            def check_linework() -> tuple[bool, str]:
                """The confidence channel is line treatment, never colour, and only the
                EXCEPTIONS are drawn: a stable region gets no contour (its fill says "region"),
                a limited or suppressed one a dashed construction contour, and a region with no
                recorded cases the faintest solid hairline -- "nothing here" is settled, not
                provisional. Read off the component's own `lineState` hook rather than sampled
                pixels: a hairline at whole-body distance is sub-pixel to measure, and the rule
                is exact."""
                c = _fresh(page, body_url)
                png = _whole_body(page, c)
                (out_dir / "body3d-linework.png").write_bytes(png)
                states = _hook(page, "return api.regionStates ? api.regionStates() : null;")
                bad = []
                seen = 0
                for region, state in (states or {}).items():
                    line = _hook(page, "return api.lineState ? api.lineState(arg) : null;", region)
                    if line is None:
                        continue
                    seen += 1
                    want_visible = state != "stable"
                    want_dashed = state in ("limited", "suppressed")
                    if line["visible"] != want_visible or (
                        want_visible and line["dashed"] != want_dashed
                    ):
                        bad.append(f"{region}={state}:{line}")
                if not seen:
                    return False, "lineState hook returned nothing"
                return not bad, (
                    f"{seen} regions checked, {len(bad)} mismatched"
                    + (f": {', '.join(bad[:4])}" if bad else "")
                )

            results.guarded("linework", check_linework)

            # 10. the figure is ink on the sheet, not a ghost -----------------------------
            def check_luma() -> tuple[bool, str]:
                png = shots.get("wholebody")
                if png is None:
                    c = _fresh(page, body_url)
                    png = _whole_body(page, c)
                fig, bg, frac = figure_vs_background_luma(png)
                # Ink on paper: the figure is DARKER than the sheet.
                margin = bg - fig
                return margin >= FIGURE_MIN_LUMA_MARGIN, (
                    f"figure mean luma {fig:.1f}, background {bg:.1f}, margin {margin:.1f} "
                    f"(need >= {FIGURE_MIN_LUMA_MARGIN}), figure covers {frac:.1%}"
                )

            results.guarded("luma", check_luma)

            # 11. keyboard: arrows turn, Escape clears -----------------------------------
            def check_keyboard() -> tuple[bool, str]:
                c = _fresh(page, body_url)
                _settled(page, c)
                before = _whole_body(page, c)
                focus_on = page.evaluate(
                    """() => {
                      const find = (root) => {
                        for (const el of root.querySelectorAll('*')) {
                          if (el.tagName === 'CANVAS' && el.width >= 200) return el;
                          if (el.shadowRoot) { const f = find(el.shadowRoot); if (f) return f; }
                        }
                        return null;
                      };
                      const cv = find(document);
                      if (!cv) return 'no canvas';
                      const target = cv.tabIndex >= 0 ? cv
                        : cv.closest('[tabindex]') || cv.parentElement;
                      target.focus();
                      let a = document.activeElement;
                      while (a && a.shadowRoot && a.shadowRoot.activeElement)
                        a = a.shadowRoot.activeElement;
                      return a ? `${a.tagName.toLowerCase()}.${a.className}` : 'none';
                    }"""
                )
                for _ in range(6):
                    page.keyboard.press("ArrowLeft")
                    page.wait_for_timeout(60)
                after = _settled(page, c)
                (out_dir / "body3d-keyboard-left.png").write_bytes(after)
                turned = changed_fraction(before, after)
                # Escape after a selection.
                pt = _project_local(page, c, "upper_trunk")
                box = c.bounding_box()
                if pt is None:
                    return False, f"arrow change {turned:.1%}; upper_trunk not projectable"
                page.mouse.click(box["x"] + pt[0], box["y"] + pt[1])
                _wait_rerun(page)
                sel_before = _support_caption(page)
                c, _ = _find_canvas(page)
                _settled(page, c)
                c.evaluate(
                    "(el) => { const t = el.tabIndex >= 0 ? el"
                    " : el.closest('[tabindex]') || el.parentElement; t.focus(); }"
                )
                page.keyboard.press("Escape")
                caption = "unset"
                for _ in range(20):
                    _wait_rerun(page)
                    caption = _support_caption(page)
                    if caption is None:
                        break
                region = _region_param(page)
                sel = (_cam_state(page) or {}).get("selected")
                c, _ = _find_canvas(page)
                (out_dir / "body3d-keyboard-escape.png").write_bytes(_settled(page, c))
                ok = turned >= CHANGED_FRACTION and bool(sel_before) and caption is None
                ok = ok and region is None and not sel
                return ok, (
                    f"focused {focus_on!r}; ArrowLeft x6 changed {turned:.1%} of pixels "
                    f"(need >= {CHANGED_FRACTION:.0%}); selected {sel_before!r} then Escape -> "
                    f"caption={caption!r}, ?region={region}, hook selected={sel!r}"
                )

            results.guarded("keyboard", check_keyboard)

            # 12. finger honesty: every finger mesh shares one colour ------------------
            def check_fingers() -> tuple[bool, str]:
                c = _fresh(page, body_url)
                _settled(page, c)
                btn = _control(page, "", 'button[data-view="hand"]')
                if btn is not None:
                    btn.click()
                    shot = _settled(page, c)
                    (out_dir / "body3d-fingers-hand.png").write_bytes(shot)
                probe = _hook(
                    page,
                    """
                    const out = {keys: Object.keys(api)};
                    for (const k of Object.keys(api)) {
                      if (typeof api[k] !== 'function' || !/color|colour|mesh|finger|material/i
                          .test(k)) continue;
                      try { out[k] = api[k]('finger'); } catch (e) { out[k] = 'err ' + e; }
                    }
                    const s = api.state ? api.state() : {};
                    for (const k of Object.keys(s))
                      if (/finger|color|colour/i.test(k)) out['state.' + k] = s[k];
                    return out;
                    """,
                )
                if not probe:
                    return False, "no window.__wiiBody3d hook"
                values = None
                source = None
                for k, v in probe.items():
                    if k == "keys":
                        continue
                    if isinstance(v, dict) and "finger" in v:
                        v = v["finger"]
                    if isinstance(v, list) and len(v) >= 2:
                        values, source = v, k
                        break
                if values is None:
                    return False, (
                        f"hook exposes no per-finger-mesh colours (keys={probe.get('keys')}); "
                        "not measurable"
                    )
                distinct = {str(v) for v in values}
                ok = len(values) >= 5 and len(distinct) == 1
                return ok, (
                    f"via api.{source}: {len(values)} finger meshes, "
                    f"{len(distinct)} distinct value(s) {sorted(distinct)[:5]}"
                )

            results.guarded("fingers", check_fingers)

            # 13. CDN blocked -> flat fallback, honest copy, no empty panel ------------
            def check_fallback() -> tuple[bool, str]:
                ctx = browser.new_context(viewport=VIEWPORT, device_scale_factor=1)
                try:
                    fp = ctx.new_page()
                    blocked: list[str] = []

                    def _abort(route):
                        blocked.append(route.request.url)
                        route.abort()

                    fp.route("https://cdn.jsdelivr.net/**", _abort)
                    fp.goto(body_url)
                    _wait_render_complete(fp)
                    try:
                        fp.wait_for_function(_FLAT_MAP_JS + " > 0", timeout=45_000)
                    except Exception:  # noqa: BLE001 -- measured below
                        pass
                    _wait_render_complete(fp)
                    fp.wait_for_timeout(1000)
                    fp.screenshot(path=str(out_dir / "page-fallback.png"), full_page=True)
                    flat_h = fp.evaluate(f"() => {_FLAT_MAP_JS}")
                    flat = flat_h >= 200
                    main_text = fp.locator('[data-testid="stMain"]').inner_text()
                    fb = fp.locator(".b3d-fallback")
                    fb_text = fb.first.inner_text() if fb.count() and fb.first.is_visible() else ""
                    copy = f"{main_text}\n{fb_text}".lower()
                    says_load = "couldn't load" in copy or "could not load" in copy
                    webgl_claim = any(
                        s in copy
                        for s in (
                            "needs webgl",
                            "does not provide",
                            "has turned off",
                            "webgl, which",
                        )
                    )
                    panel_h = fp.evaluate(
                        """() => {
                          let h = 0;
                          const walk = (root) => {
                            for (const el of root.querySelectorAll('*')) {
                              if (el.shadowRoot) {
                                if (el.shadowRoot.querySelector('.b3d'))
                                  h = Math.max(h, el.getBoundingClientRect().height);
                                walk(el.shadowRoot);
                              }
                              if (el.classList && el.classList.contains('b3d'))
                                h = Math.max(h, el.getBoundingClientRect().height);
                            }
                          };
                          walk(document);
                          return h;
                        }"""
                    )
                    ok = flat and says_load and not webgl_claim and panel_h < 150
                    return ok, (
                        f"{len(blocked)} jsdelivr request(s) aborted; "
                        f"flat map height={flat_h:.0f}px (need >= 200); "
                        f"'could not load' copy={says_load}; WebGL claim={webgl_claim}; "
                        f"leftover 3D panel height={panel_h:.0f}px (need < 150)"
                    )
                finally:
                    ctx.close()

            results.guarded("fallback", check_fallback)

            # Streamlit's frontend probes `<page path>/_stcore/*` to discover its base path and
            # logs a 404 console error for each miss; those are not failures of this page.
            console_errors = [m for m in console_errors if "status of 404" not in m or failed_urls]
            if console_errors:
                print(f"INFO  console    {len(console_errors)} browser console error(s):")
                for msg in console_errors[:10]:
                    print(f"        {msg[:200]}")
            for line in sorted(set(failed_urls))[:10]:
                print(f"INFO  http       {line[:200]}")
            browser.close()
    except _AbortChecks:
        pass
    finally:
        _stop_server(proc)
        print(f"INFO  server     stopped pid={proc.pid}", flush=True)

    print(f"INFO  output     screenshots in {out_dir}")
    passed = results.all_passed
    print("ALL PASS" if passed else "SOME CHECKS FAILED")
    return passed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--out", type=Path, default=Path(tempfile.gettempdir()) / "wii-verify-body3d"
    )
    parser.add_argument("--tool", default=DEFAULT_TOOL_LABEL, help="Tool label for drill-down")
    parser.add_argument("--headed", action="store_true", help="Show the browser window")
    args = parser.parse_args(argv)
    if args.port not in ALLOWED_PORTS:
        parser.error("--port must be in 8860-8869")
    return 0 if run(args.port, args.out, args.tool, args.headed) else 1


if __name__ == "__main__":
    sys.exit(main())
