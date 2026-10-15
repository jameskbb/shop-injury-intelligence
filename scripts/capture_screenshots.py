"""D-08 screenshot capture (dev-only, never imported by app code or CI). Launches the Streamlit
app headless (same subprocess/health-poll/try-finally harness proven in
`scripts/spikes/spike_st_html_svg.py`) and drives it with Playwright's sync API to capture every
`PAGE_SPECS` page (`src/wii/app/pages_registry.py`) at desktop (1440x900) and mobile (390x844)
into `docs/screenshots/`, plus one `hero.png` (the Body Map's 3D figure after a tool drill-down).

Run (playwright is intentionally absent from pyproject.toml/uv.lock -- ephemeral, dev-only):
    uv run --with playwright==1.62.0 python scripts/capture_screenshots.py

One-time Chromium install (if the precondition check below fails):
    uv run --with playwright==1.62.0 python -m playwright install chromium

CLI:
    --set desktop|mobile|all   (default: all)
    --pages slug,slug,...      (default: all registered slugs)
    --out DIR                  (default: docs/screenshots)
    --port PORT                (default: 8611)
    --no-hero                  (skip the hero.png capture)

`playwright` is imported lazily inside the capture functions so this module (and its pure
helpers) can be imported/tested without the package installed (T-06-SC).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Literal

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "docs" / "screenshots"
DEFAULT_PORT = 8611

VIEWPORTS: dict[str, dict[str, int]] = {
    "desktop": {"width": 1440, "height": 900},
    "mobile": {"width": 390, "height": 844},
}

# Budgets per Screenshot Capture Contract (06-UI-SPEC.md).
BUDGET_BYTES: dict[str, int] = {
    "desktop": 500 * 1024,
    "mobile": 250 * 1024,
}
TOTAL_BUDGET_BYTES = 8 * 1024 * 1024

# My Shop capture-only tool pre-selection (D-01 unchanged -- app default stays empty). Labels
# below are substrings matched against the live rendered multiselect option text so the script
# never silently selects nothing if a taxonomy label changes; see `_select_my_shop_tools`.
MY_SHOP_CAPTURE_TOOLS: tuple[str, ...] = (
    "Bench or Table Saw",
    "Portable Circular Power Saw",
    "Router",
    "Sander",
)

# Body Map hero: the real per-tool drill-down. Picking a tool in the page's own "Tool" selector
# (the same click/type/click a user performs) makes the 3D figure fly to where that tool's
# injuries concentrate. For the table saw that is the hand close-up, which also shows the
# "fingers share one value" note -- the most informative single frame of the figure.
HERO_TOOL_LABEL = "Bench or Table Saw"
HERO_TOOL_SELECT_LABEL = "Tool"
# Space kept below the 3D figure in the cropped hero.
HERO_BOTTOM_PAD_PX = 12
#: The whole-body view sits ~4.0 from its target; a region close-up is under 1.6. The hero must
#: be the former, so a regression back to the hand close-up fails the capture instead of
#: shipping an unreadable first image.
HERO_MIN_DISTANCE = 3.0
BODY_MAP_SLUG = "body-map"


def _load_page_specs():
    """Imported lazily so `--help`/pure-helper use never requires the `wii` package's full
    dependency chain to succeed before argparse even runs (fast failure feedback)."""
    from wii.app.pages_registry import PAGE_SPECS

    return PAGE_SPECS


def nn_for(index: int) -> str:
    """1-based index -> zero-padded 2-digit string. `index` is the position in `PAGE_SPECS`."""
    return f"{index:02d}"


def slug_for(spec: Any) -> str:
    """`spec` is any object exposing a `.slug` property (PageSpec's own definition: `url_path or
    'overview'`) -- reused here rather than reimplemented so the two never drift."""
    return spec.slug


def gallery_filename(nn: str, slug: str, viewport_set: str) -> str:
    return f"{nn}-{slug}-{viewport_set}.png"


def expected_filenames(page_specs, sets: tuple[str, ...] = ("desktop", "mobile")) -> list[str]:
    """2 x len(page_specs) gallery filenames in registry order (all of a page's sets before the
    next page) plus `hero.png` last. `page_specs` is the `PAGE_SPECS` tuple or an equivalent
    ordered sequence of PageSpec-like objects (each need only `.slug`)."""
    names: list[str] = []
    for i, spec in enumerate(page_specs, start=1):
        nn = nn_for(i)
        slug = slug_for(spec)
        for viewport_set in sets:
            names.append(gallery_filename(nn, slug, viewport_set))
    names.append("hero.png")
    return names


def budget_ok(size_bytes: int, kind: Literal["desktop", "mobile"]) -> bool:
    """True if `size_bytes` is within the per-file budget for `kind` (desktop 500 KB, mobile
    250 KB). `kind` must be exactly "desktop" or "mobile" -- the hero image uses the desktop
    budget (it is captured at the desktop viewport)."""
    return size_bytes <= BUDGET_BYTES[kind]


def _quantize_and_save(img, png_path: Path, colors: int) -> None:
    """Saves `img` as an adaptive-palette PNG (still the lossless PNG format -- no JPEG-style
    blur of text/thin chart lines -- but limited to `colors` distinct colors, which is what
    actually shrinks these dark, mostly-flat-region UI screenshots; plain `optimize=True` alone
    barely touches full-24-bit-RGB PNGs of a scrollable app page). `dither=NONE` matters: dithering
    scatters noise-like pixel variation across flat regions specifically to fake more colors,
    which works against PNG's flat-region compression and can make the file LARGER, not smaller."""
    from PIL import Image

    quantized = img.convert("RGB").quantize(
        colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE
    )
    quantized.save(png_path, format="PNG", optimize=True, compress_level=9)


def _optimize_and_maybe_downscale(
    png_path: Path, kind: Literal["desktop", "mobile"]
) -> dict[str, Any] | None:
    """Re-saves `png_path` quantized to progressively smaller adaptive palettes until it fits
    `kind`'s budget (see `_quantize_and_save`). Returns a budget-exception dict (never raises) if
    still over budget at the smallest palette, else None.

    It NEVER resizes. The previous version downscaled the longest edge in 5% steps up to 25% to
    make budget, which is how `05-comparison-mobile.png` was committed at 292px wide (390 x 0.75)
    while the README described it as a 390px phone capture -- a caption that no longer matched
    the file. Colour depth is the right thing to spend here; the declared viewport width is not."""
    from PIL import Image

    for colors in (256, 128, 64, 32):
        with Image.open(png_path) as img:
            _quantize_and_save(img, png_path, colors=colors)
        if budget_ok(png_path.stat().st_size, kind):
            return None

    return {
        "file": png_path.name,
        "size_bytes": png_path.stat().st_size,
        "budget_bytes": BUDGET_BYTES[kind],
    }


def _wait_for_health(health_url: str, timeout_s: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=1) as resp:  # noqa: S310
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.5)
    return False


def _wait_for_render_complete(page) -> None:
    """Render-completion wait (never a fixed sleep, E3): waits for the app container and its
    `st.title` heading (every page renders one), then -- if `stStatusWidget` (Streamlit's
    running/rerun indicator) ever attaches -- waits for it to detach again (a plain
    `state="detached"` wait resolves immediately for an element that never attached at all, which
    would race ahead of the actual page script run, so attach-then-detach is checked explicitly),
    then waits for the network to go idle and for every Plotly container already in the DOM to
    have produced its inner SVG."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    page.wait_for_selector('[data-testid="stAppViewContainer"]', timeout=30_000)
    page.wait_for_selector('[data-testid="stAppViewContainer"] h1', timeout=30_000)
    try:
        page.wait_for_selector('[data-testid="stStatusWidget"]', state="attached", timeout=2_000)
        page.wait_for_selector('[data-testid="stStatusWidget"]', state="detached", timeout=30_000)
    except PlaywrightTimeoutError:
        pass
    page.wait_for_load_state("networkidle", timeout=30_000)
    # `st.spinner` is NOT `stStatusWidget`: a section that does its own cached work inside a
    # spinner context (Overview's "What the data is saying" wraps `insight_frames`) keeps
    # rendering after the status widget has already detached. Without this wait the capture
    # freezes that section mid-flight -- the committed 01-overview-desktop.png showed a live
    # "Running insight_frames(...)" spinner where the insight cards belong.
    page.wait_for_function(
        """
        () => document.querySelectorAll('[data-testid="stSpinner"]').length === 0
        """,
        timeout=60_000,
    )
    page.wait_for_function(
        """
        () => {
          const plots = document.querySelectorAll('.js-plotly-plot');
          if (plots.length === 0) return true;
          return Array.from(plots).every((p) => p.querySelector('svg.main-svg'));
        }
        """,
        timeout=30_000,
    )
    _wait_for_body3d_settled(page, required=False)


def _wait_for_body3d_settled(page, *, required: bool, timeout_ms: int = 45_000) -> None:
    """Waits until the Body Map's 3D figure has finished its intro animation.

    The figure flies in from a wide shot (1.6 s), pauses 250 ms, then flies to the active tool's
    hotspots (1.3 s) and pulses them (1.1 s). A screenshot taken during any of that is a blurred
    mid-flight frame. The component exposes a read-only `window.__wiiBody3d.state()` hook
    (`tweening`, `flashing`); the figure counts as settled once neither is true on every poll
    across a window longer than that 250 ms gap between the two flights.

    `required=True` (Body Map, hero) raises if the figure never renders -- e.g. the browser fell
    back to the flat 2D map because WebGL or the Three.js CDN was unavailable -- since a capture
    of the fallback would misrepresent the page. `required=False` returns at once when the page
    has no 3D figure at all.
    """
    root = page.locator(".b3d")
    if not required and root.count() == 0:
        return
    root.first.wait_for(state="attached", timeout=timeout_ms)
    page.wait_for_function(
        """
        () => {
          const hook = window.__wiiBody3d;
          if (hook) return true;
          const host = Array.from(document.querySelectorAll('*')).find((e) => e.shadowRoot
            && e.shadowRoot.querySelector('.b3d[data-state="nowebgl"]'));
          return Boolean(host);
        }
        """,
        timeout=timeout_ms,
    )
    if not page.evaluate("() => Boolean(window.__wiiBody3d)"):
        raise RuntimeError("3D body map fell back to the flat map (no WebGL or Three.js)")
    page.wait_for_function(
        """
        () => {
          const s = window.__wiiBody3d && window.__wiiBody3d.state();
          const now = performance.now();
          if (!s || s.tweening || s.flashing) {
            window.__wiiCaptureCalmSince = null;
            return false;
          }
          if (window.__wiiCaptureCalmSince == null) window.__wiiCaptureCalmSince = now;
          return now - window.__wiiCaptureCalmSince > 900;
        }
        """,
        polling=100,
        timeout=timeout_ms,
    )
    # One more frame so the final camera pose (and outline width) is actually painted.
    page.wait_for_timeout(200)


def _select_my_shop_tools(page, base_url: str) -> None:
    """Capture-only override (never the app default, D-01): opens the My Shop multiselect and
    picks each label in `MY_SHOP_CAPTURE_TOOLS` that actually appears among the live rendered
    options, via the same click-input/type/click-option interaction a real user performs --
    never `st.session_state` injection, since this must exercise the real widget."""
    from wii.app.pages.my_shop import SELECT_LABEL

    page.goto(f"{base_url}/my-shop")
    _wait_for_render_complete(page)
    # `.first` on a generic `stMultiSelect` locator is wrong here -- the sidebar's own "Tool
    # family" advanced-filter multiselect (src/wii/app/filters.py) is NOT gated by
    # `hide_global_tool_filter` (only the tool-name filter is) and renders before this page's
    # widget in DOM order, so `.first` silently grabs the wrong control. The page's own selector
    # (SELECT_LABEL) sets that exact text as the widget's accessible name/aria-label.
    input_el = page.get_by_label(SELECT_LABEL, exact=True)
    for label in MY_SHOP_CAPTURE_TOOLS:
        input_el.click()
        page.wait_for_timeout(150)
        # `.fill()` sets the DOM value directly and does NOT reliably trigger this react-aria
        # combobox's type-ahead filtering (confirmed empirically: the option list stayed
        # unfiltered and the input showed no typed text). `press_sequentially` dispatches real
        # per-character keyboard events, which the widget's onKeyDown-driven filter needs.
        # Options render as `div[role="option"]` (not `li`) -- confirmed via a live DOM probe.
        input_el.press_sequentially(label, delay=30)
        page.wait_for_timeout(400)
        option = page.locator('[role="option"]', has_text=label).first
        try:
            option.wait_for(state="visible", timeout=5_000)
            option.click()
            page.wait_for_timeout(200)
        except Exception:  # noqa: BLE001 -- a missing label is skipped, not fatal (best-effort)
            continue
    page.keyboard.press("Escape")
    _wait_for_render_complete(page)


def _capture_hero(page, base_url: str, out_path: Path) -> None:
    """Hero: the Body Map after picking `HERO_TOOL_LABEL`, framed on the WHOLE FIGURE with that
    tool's focus still applied -- the rest of the body receded toward the sheet and only the
    hands carrying ink. That is the app's thesis in one frame and it is what the README's alt
    text already describes.

    It is deliberately NOT the hand close-up the drill-down ends on: at that framing the hand
    fills the frame as unreadable blocks of ink and nothing in the image says "body injury map".
    So the capture lets the drill-down run (which is what applies the focus), then takes the
    figure's own "Whole body" control -- which moves the camera and changes no data -- and shoots
    from there. Cropped from the top of the page to just below the figure, so the image carries
    the app chrome (sidebar, the chosen tool, the page title) and the whole figure, rather than
    the long detail sections beneath it."""
    width = VIEWPORTS["desktop"]["width"]
    page.goto(f"{base_url}/{BODY_MAP_SLUG}")
    _wait_for_render_complete(page)
    _wait_for_body3d_settled(page, required=True)

    select = page.get_by_label(HERO_TOOL_SELECT_LABEL, exact=True)
    select.click()
    page.wait_for_timeout(300)
    select.press_sequentially(HERO_TOOL_LABEL, delay=30)
    page.wait_for_timeout(400)
    option = page.locator('[role="option"]', has_text=HERO_TOOL_LABEL).first
    option.wait_for(state="visible", timeout=5_000)
    option.click()
    page.wait_for_timeout(500)
    # Drop the selector's focus ring so the hero shows the page at rest, not mid-edit.
    page.evaluate("() => document.activeElement && document.activeElement.blur()")
    _wait_for_render_complete(page)
    _wait_for_body3d_settled(page, required=True)
    if page.evaluate("() => window.__wiiBody3d.state().distance") > 1.5:
        raise RuntimeError(f"3D figure did not drill down after selecting {HERO_TOOL_LABEL!r}")

    # Back out to the whole figure, keeping the tool's focus treatment.
    page.locator('[data-view="body"]').first.click()
    _wait_for_body3d_settled(page, required=True)
    distance = page.evaluate("() => window.__wiiBody3d.state().distance")
    if distance < HERO_MIN_DISTANCE:
        raise RuntimeError(
            f"hero framing is a close-up (camera distance {distance:.2f}, "
            f"need >= {HERO_MIN_DISTANCE}) -- the whole figure must be in frame"
        )

    box = page.locator(".b3d").first.bounding_box()
    if box is None:
        raise RuntimeError("3D figure has no bounding box")
    bottom = int(box["y"] + box["height"]) + HERO_BOTTOM_PAD_PX
    # Grow the viewport to fit the whole figure (a clip outside the viewport is blank), then
    # re-check the camera is still at rest before shooting.
    page.set_viewport_size({"width": width, "height": max(bottom, VIEWPORTS["desktop"]["height"])})
    _wait_for_body3d_settled(page, required=True)
    page.screenshot(path=str(out_path), clip={"x": 0, "y": 0, "width": width, "height": bottom})


def _full_page_screenshot(page, out_path: Path, width: int) -> None:
    """Streamlit's real scroll container is `[data-testid="stMain"]` (an inner `overflow-y:auto`
    div sized to the viewport, not `document.body`), so Playwright's `full_page=True` alone only
    captures the first viewport-height of content. Resizing the browser viewport to the
    container's actual `scrollHeight` first (its CSS is viewport-height-relative, so it grows to
    fill the new viewport with no overflow) makes a full, real page-length capture possible."""
    scroll_height = _settled_scroll_height(page)
    page.set_viewport_size({"width": width, "height": max(int(scroll_height), 1)})
    _wait_for_render_complete(page)
    # Growing the viewport can itself reveal lazily-rendered content (Plotly sizes to its
    # container), so re-measure and grow once more if the page got taller.
    grown = _settled_scroll_height(page)
    height = max(int(scroll_height), 1)
    if grown > scroll_height:
        height = max(int(grown), 1)
        page.set_viewport_size({"width": width, "height": height})
        _wait_for_render_complete(page)
    # Capture an explicit clip of exactly the viewport, NOT `full_page=True`. Once the viewport
    # has been grown to the settled scroll height there is nothing left to scroll, and
    # `full_page=True` then stitches a second pass over a Streamlit container that re-renders
    # between passes -- which is how `05-comparison-mobile` and `06-my-shop-mobile` ended up
    # with the title, the multiselect and the KPI strip drawn twice down one image.
    page.screenshot(path=str(out_path), clip={"x": 0, "y": 0, "width": width, "height": height})
    _assert_capture(page, out_path, width)


def _assert_capture(page, out_path: Path, width: int) -> None:
    """Two guards on every gallery capture, because both failures are silent in the PNG:
    the image must be exactly the viewport width, and the page's title must appear once."""
    from PIL import Image

    with Image.open(out_path) as img:
        got = img.size[0]
    if got != width:
        raise RuntimeError(f"{out_path.name}: captured at {got}px, expected {width}px")
    titles = page.locator('[data-testid="stMain"] h1').count()
    if titles > 1:
        raise RuntimeError(f"{out_path.name}: page title appears {titles} times (double render)")


def _settled_scroll_height(page, *, stable_polls: int = 3, interval_ms: int = 350) -> int:
    """`stMain`'s scrollHeight once it has stopped changing.

    Reading it once races the page: Streamlit streams elements in, and Plotly figures resize after
    their container settles, so an early read returns the viewport height and the capture silently
    truncates to one screen. That is exactly what happened on a run where overview and body-map
    came out 1440x900 while methodology, on the same run, captured 1440x6894 -- a race, not a
    broken selector, which is why it hits different pages on different runs.
    """
    js = """
        () => {
          const main = document.querySelector('[data-testid="stMain"]')
            || document.querySelector('.main');
          return main ? main.scrollHeight : document.documentElement.scrollHeight;
        }
    """
    last = -1
    stable = 0
    for _ in range(40):
        current = int(page.evaluate(js))
        if current == last:
            stable += 1
            if stable >= stable_polls:
                return current
        else:
            stable = 0
            last = current
        page.wait_for_timeout(interval_ms)
    return max(last, 1)


def _capture_page(
    page, base_url: str, url_path: str, out_path: Path, width: int, *, require_3d: bool = False
) -> None:
    page.goto(f"{base_url}/{url_path}" if url_path else base_url)
    _wait_for_render_complete(page)
    if require_3d:
        _wait_for_body3d_settled(page, required=True)
    _full_page_screenshot(page, out_path, width)


# The Body Map's 3D figure is WebGL (Three.js). Headless Chromium has no GPU here, so it renders
# through SwiftShader (software GL); without these flags it reports no WebGL and the page falls
# back to the flat 2D map -- which would make every Body Map capture misrepresent the product.
WEBGL_ARGS: tuple[str, ...] = ("--use-angle=swiftshader", "--enable-unsafe-swiftshader")


def _browser_env() -> dict[str, str]:
    """Chromium's environment for the capture run: this process's own env minus `WAYLAND_DISPLAY`.

    Under WSLg that variable is always set (`wayland-0`), and its mere presence makes headless
    Chromium's `Page.captureScreenshot` hang forever -- the call logs "fonts loaded" and then never
    returns a frame, so every `page.screenshot()` dies on its timeout no matter how high the
    timeout is. Bisected empirically against a live app page AND `about:blank` (so it is not the
    app's content, fonts, or any animation): unsetting `WAYLAND_DISPLAY` alone captures in ~0.1s,
    while unsetting `DISPLAY` alone, `--ozone-platform=headless`, `--disable-gpu`,
    `--disable-gpu-compositing`, `screenshot(animations="disabled")` and the
    `chromium-headless-shell` channel all still hang. Playwright's `env=` REPLACES the browser's
    environment rather than merging into it, hence the full `os.environ` copy."""
    return {k: v for k, v in os.environ.items() if k != "WAYLAND_DISPLAY"}


def _launch_server(port: int) -> subprocess.Popen:
    return subprocess.Popen(  # noqa: S603
        [
            "uv",
            "run",
            "streamlit",
            "run",
            "app.py",
            "--server.headless",
            "true",
            "--server.port",
            str(port),
            "--server.address",
            "127.0.0.1",
        ],
        cwd=str(REPO_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def run_capture(
    sets: tuple[str, ...],
    page_slugs: set[str] | None,
    out_dir: Path,
    port: int,
    include_hero: bool,
) -> dict[str, Any]:
    page_specs = _load_page_specs()
    out_dir.mkdir(parents=True, exist_ok=True)
    health_url = f"http://127.0.0.1:{port}/_stcore/health"
    base_url = f"http://127.0.0.1:{port}"

    captured: list[str] = []
    failed: list[dict[str, str]] = []
    budget_exceptions: list[dict[str, Any]] = []

    proc = _launch_server(port)
    try:
        if not _wait_for_health(health_url):
            return {
                "captured": captured,
                "failed": [{"page": "*", "error": "server never became healthy"}],
                "budget_exceptions": budget_exceptions,
                "total_size_bytes": 0,
            }

        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(env=_browser_env(), args=list(WEBGL_ARGS))
            try:
                for viewport_set in sets:
                    context = browser.new_context(
                        viewport=VIEWPORTS[viewport_set], device_scale_factor=1
                    )
                    page = context.new_page()
                    for i, spec in enumerate(page_specs, start=1):
                        slug = slug_for(spec)
                        if page_slugs is not None and slug not in page_slugs:
                            continue
                        nn = nn_for(i)
                        filename = gallery_filename(nn, slug, viewport_set)
                        out_path = out_dir / filename
                        try:
                            # Reset to the base viewport before each page: a prior page's
                            # content-height resize (below) must never leak into this page's own
                            # scrollHeight measurement (a shorter page under a taller leftover
                            # viewport would otherwise report the stale, larger height).
                            page.set_viewport_size(VIEWPORTS[viewport_set])
                            if slug == "my-shop":
                                _select_my_shop_tools(page, base_url)
                                _full_page_screenshot(
                                    page, out_path, VIEWPORTS[viewport_set]["width"]
                                )
                            else:
                                _capture_page(
                                    page,
                                    base_url,
                                    spec.url_path,
                                    out_path,
                                    VIEWPORTS[viewport_set]["width"],
                                    require_3d=(slug == BODY_MAP_SLUG),
                                )
                            exc = _optimize_and_maybe_downscale(out_path, viewport_set)
                            if exc is not None:
                                budget_exceptions.append(exc)
                            captured.append(filename)
                        except Exception as e:  # noqa: BLE001
                            failed.append({"page": slug, "error": str(e)})
                    context.close()

                if include_hero and (page_slugs is None or BODY_MAP_SLUG in page_slugs):
                    context = browser.new_context(
                        viewport=VIEWPORTS["desktop"], device_scale_factor=1
                    )
                    page = context.new_page()
                    try:
                        hero_path = out_dir / "hero.png"
                        _capture_hero(page, base_url, hero_path)
                        exc = _optimize_and_maybe_downscale(hero_path, "desktop")
                        if exc is not None:
                            budget_exceptions.append(exc)
                        captured.append("hero.png")
                    except Exception as e:  # noqa: BLE001
                        failed.append({"page": "hero", "error": str(e)})
                    context.close()
            finally:
                browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    if page_slugs is None and sets == ("desktop", "mobile"):
        keep = set(expected_filenames(page_specs))
        for existing in out_dir.glob("*.png"):
            if existing.name not in keep:
                existing.unlink()

    total_size = sum(p.stat().st_size for p in out_dir.glob("*.png"))
    return {
        "captured": captured,
        "failed": failed,
        "budget_exceptions": budget_exceptions,
        "total_size_bytes": total_size,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--set", choices=["desktop", "mobile", "all"], default="all")
    parser.add_argument("--pages", default=None, help="comma-separated slugs (default: all)")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-hero", action="store_true")
    args = parser.parse_args(argv)

    sets: tuple[str, ...] = ("desktop", "mobile") if args.set == "all" else (args.set,)
    page_slugs = set(args.pages.split(",")) if args.pages else None
    out_dir = Path(args.out)

    result = run_capture(
        sets=sets,
        page_slugs=page_slugs,
        out_dir=out_dir,
        port=args.port,
        include_hero=not args.no_hero,
    )
    print(json.dumps(result, indent=2))
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
