"""D-01 thin entrypoint: set_page_config -> theme -> sidebar filters -> st.navigation -> page ->
footer. Everything else lives under src/wii/app/ (pages, components, charts, theme, data) and
src/wii/analytics/ (insight engine). Pages are built from the single PAGE_SPECS registry
(src/wii/app/pages_registry.py, D-03) -- adding a page there automatically adds it here and to
the AppTest smoke sweep, so a page can't be silently left out of navigation or CI.
"""

from __future__ import annotations

import streamlit as st

from wii.app import filters, theme
from wii.app.components.footer import render_footer
from wii.app.pages_registry import PAGE_SPECS

st.set_page_config(layout="wide", page_title="Workshop Injury Intelligence")
theme.inject()

pages = [
    st.Page(spec.file, title=spec.title, default=True)
    if spec.is_default
    else st.Page(spec.file, title=spec.title, url_path=spec.url_path)
    for spec in PAGE_SPECS
]
nav = st.navigation(pages)

# Pages with their own tool selector/filter grid hide the global tool filter (D-03/D-01/D-07).
# nav.url_path is readable BEFORE .run() (Wave-0 spike A5, finding (d)) -- the default page's
# url_path resolves to "", not its declared url_path. Mechanisms is deliberately not in
# _HIDE_TOOL_PAGES: it uses the global tool filter normally, and owns only its own single-tool
# Sankey focus selector (04-CONTEXT.md "Mechanisms page" note). Methodology describes the whole
# dataset (D-13/D-17): sidebar filters never apply, but the page still hides the tool multiselect.
_HIDE_TOOL_PAGES = {spec.url_path for spec in PAGE_SPECS if spec.hide_global_tool_filter}
filters.render_sidebar(hide_tool=nav.url_path in _HIDE_TOOL_PAGES)

nav.run()

render_footer()
