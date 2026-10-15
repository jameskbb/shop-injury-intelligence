"""Streamlit-free page registry (D-03). This module never imports the Streamlit package, so it is
safe to import from anywhere (app.py, tests, tooling scripts) without pulling in a ScriptRunContext.
`app.py` builds its `st.navigation([...])` list by iterating PAGE_SPECS; the AppTest smoke sweep
in tests/app/test_pages_smoke.py is parametrized over the same tuple, so a page added here is
automatically wired into both navigation and the every-page smoke test (D-03).

Order is REQUIREMENTS.md PAGE-01..PAGE-08 order (Overview, Tool Profiles, Body Map, Mechanisms,
Tool Comparison, My Shop, Case Explorer, Methodology & Data per 06-UI-SPEC) -- this is the order
the nav sidebar, the README screenshot gallery, and the screenshot-capture script all share.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PageSpec:
    """One `st.Page(...)` registration's worth of data, minus the Streamlit import."""

    title: str
    file: str
    url_path: str
    hide_global_tool_filter: bool = False

    @property
    def is_default(self) -> bool:
        return self.url_path == ""

    @property
    def slug(self) -> str:
        return self.url_path or "overview"


PAGE_SPECS: tuple[PageSpec, ...] = (
    PageSpec("Injury Overview", "src/wii/app/pages/overview.py", "", hide_global_tool_filter=False),
    PageSpec(
        "Tool Profiles",
        "src/wii/app/pages/tool_profiles.py",
        "tool-profiles",
        hide_global_tool_filter=True,
    ),
    PageSpec("Body Map", "src/wii/app/pages/body_map.py", "body-map", hide_global_tool_filter=True),
    PageSpec(
        "Mechanisms", "src/wii/app/pages/mechanisms.py", "mechanisms", hide_global_tool_filter=False
    ),
    PageSpec(
        "Tool Comparison",
        "src/wii/app/pages/comparison.py",
        "comparison",
        hide_global_tool_filter=True,
    ),
    PageSpec("My Shop", "src/wii/app/pages/my_shop.py", "my-shop", hide_global_tool_filter=True),
    PageSpec(
        "Case Explorer",
        "src/wii/app/pages/case_explorer.py",
        "case-explorer",
        hide_global_tool_filter=True,
    ),
    # PAGE-09 Signals: the cross-cutting ranked feed over the SAME INS-01 engine the other pages
    # use. It carries no tool selector of its own -- the feed is deliberately workshop-wide, and
    # the global sidebar tool filter is the only thing that narrows it -- so
    # hide_global_tool_filter stays False. It sits with the analytical pages rather than after
    # Methodology & Data: Methodology is the transparency backstop a reader reaches for AFTER
    # seeing a claim, so it stays the last entry in the nav.
    PageSpec("Signals", "src/wii/app/pages/signals.py", "signals", hide_global_tool_filter=False),
    PageSpec(
        "Methodology & Data",
        "src/wii/app/pages/methodology.py",
        "methodology",
        hide_global_tool_filter=True,
    ),
)
