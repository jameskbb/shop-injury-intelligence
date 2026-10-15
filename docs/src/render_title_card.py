"""Render docs/images/readme-title-shop-injury-intelligence.png from docs/src/title-card.html.

Run (playwright is intentionally absent from pyproject.toml/uv.lock; ephemeral, dev-only):
    uv run --with playwright==1.62.0 python docs/src/render_title_card.py

One-time Chromium install if the launch fails:
    uv run --with playwright==1.62.0 python -m playwright install chromium

1200x440 at a device scale factor of 2, the title-card convention shared by the author's public
repositories. The bars on the card quote the committed release; refresh them with:

    select t.label, round(sum(a.estimate) / count(distinct a.year)) as annual_avg
    from 'data/processed/agg_tool_year.parquet' a
    join 'data/processed/dim_tool.parquet' t on a.group_id = t.tool_id
    where a."window" = 'all_years' and a.age_scope = 'adults'
    group by 1 order by 2 desc limit 5
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "title-card.html"
OUT = HERE.parent / "images" / "readme-title-shop-injury-intelligence.png"
WIDTH, HEIGHT = 1200, 440


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=2)
        page.goto(SOURCE.as_uri(), wait_until="networkidle")
        page.evaluate("document.fonts.ready")
        page.wait_for_timeout(250)
        page.screenshot(
            path=str(OUT),
            clip={"x": 0, "y": 0, "width": WIDTH, "height": HEIGHT},
            animations="disabled",
        )
        browser.close()
    print(OUT)


if __name__ == "__main__":
    main()
