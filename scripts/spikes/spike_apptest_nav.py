"""Wave-0 spike A5 (+ Pattern-1 caveat, A3 side-check): resolves the `streamlit.testing.v1.AppTest`
harness shape for an `st.navigation` app -- whether `AppTest.from_file(entry)` + `switch_page`
sees shared `st.session_state`, whether a page module runs standalone under `AppTest`, whether
`st.navigation`'s return value exposes the current page BEFORE `.run()` (so `app.py` can decide
`hide_tool` before rendering the sidebar), whether a hidden widget's key is dropped from
`session_state` on rerun, how `st.html`/unsafe-markdown content is observable from `AppTest`, and
whether a frozen dataclass with tuple fields works as an `st.cache_data` argument.

Writes a throwaway 2-page app into a `tempfile.TemporaryDirectory` -- nothing here is a permanent
app surface. Run: uv run python scripts/spikes/spike_apptest_nav.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from streamlit.testing.v1 import AppTest

_ENTRY_SRC = """
import streamlit as st

page_a = st.Page("page_a.py", title="Page A", url_path="page-a", default=True)
page_b = st.Page("page_b.py", title="Page B", url_path="page-b")
nav = st.navigation([page_a, page_b])

# (d): st.navigation's return value exposes the CURRENT page's title/url_path BEFORE .run() --
# this lets app.py decide whether to hide the global tool filter before rendering the sidebar.
hide_tool = nav.url_path == "page-b"
st.session_state["_nav_probe"] = {"title": nav.title, "url_path": nav.url_path}
if not hide_tool:
    st.sidebar.selectbox("Probe tool", ["a", "b", "c"], key="probe_tool")

nav.run()
st.caption("FOOTER_MARKER")
"""

_PAGE_A_SRC = """
from dataclasses import dataclass

import streamlit as st

st.session_state.setdefault("shared_key", "value_from_a")
st.write("PAGE_A_SHARED=" + str(st.session_state["shared_key"]))


@dataclass(frozen=True)
class ProbeFilter:
    tools: tuple = ()


_counter = st.session_state.setdefault("_cache_probe_calls", [0])


@st.cache_data
def _cached_probe(flt: ProbeFilter) -> int:
    _counter[0] += 1
    return _counter[0]


try:
    f1 = ProbeFilter(tools=("saw", "drill"))
    f2 = ProbeFilter(tools=("saw", "drill"))
    r1 = _cached_probe(f1)
    r2 = _cached_probe(f2)
    st.session_state["_cache_probe_result"] = {
        "hashable_ok": True,
        "call_count": _counter[0],
        "r1": r1,
        "r2": r2,
    }
except Exception as e:  # noqa: BLE001 -- spike-only, captures the failure mode itself
    st.session_state["_cache_probe_result"] = {"hashable_ok": False, "error": repr(e)}
"""

_PAGE_B_SRC = """
import html

import streamlit as st

# Defensive .get(default=) rather than a bare lookup -- (c) checks whether this page survives
# AppTest.from_file(page_b) standalone, without app.py/page_a ever having run.
shared = st.session_state.get("shared_key", "MISSING")
st.write("PAGE_B_SEES_SHARED=" + str(shared))

safe_text = html.escape("Tooltip <script>x</script> & more")
st.html("<div id='probe-html'><span>" + safe_text + "</span></div>")
st.markdown(
    "<div id='probe-markdown-card'>Card content " + safe_text + "</div>",
    unsafe_allow_html=True,
)
"""


def main() -> int:
    details: dict = {}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            entry_path = tmp_path / "spike_entry.py"
            page_a_path = tmp_path / "page_a.py"
            page_b_path = tmp_path / "page_b.py"
            entry_path.write_text(_ENTRY_SRC, encoding="utf-8")
            page_a_path.write_text(_PAGE_A_SRC, encoding="utf-8")
            page_b_path.write_text(_PAGE_B_SRC, encoding="utf-8")

            # (a) AppTest.from_file(entry) runs without exception.
            at = AppTest.from_file(str(entry_path))
            at.run(timeout=30)
            details["a_entry_runs_without_exception"] = not at.exception
            details["a_entry_exception"] = [str(e) for e in at.exception]

            # (d) nav.url_path/title observable before .run(); (e) widget key present pre-hide.
            details["d_nav_probe_before_run"] = (
                at.session_state["_nav_probe"] if "_nav_probe" in at.session_state else None
            )
            details["e_probe_tool_present_on_page_a"] = "probe_tool" in at.session_state

            # (g) frozen dataclass w/ tuple field as an st.cache_data argument.
            details["g_cache_probe_result"] = (
                at.session_state["_cache_probe_result"]
                if "_cache_probe_result" in at.session_state
                else None
            )

            # (b) switch_page + shared session_state; (e) widget key dropped once hidden.
            at.switch_page(str(page_b_path))
            at.run(timeout=30)
            details["b_page_b_runs_without_exception"] = not at.exception
            details["b_page_b_exception"] = [str(e) for e in at.exception]
            md_values = [m.value for m in at.markdown]
            details["b_page_b_sees_page_a_session_value"] = any(
                "PAGE_B_SEES_SHARED=value_from_a" in v for v in md_values
            )
            details["e_probe_tool_dropped_when_hidden"] = "probe_tool" not in at.session_state

            # (f) how st.html / unsafe markdown are observable from AppTest.
            html_els = at.get("html")
            details["f_html_element_type"] = type(html_els[0]).__name__ if html_els else None
            details["f_html_values"] = [e.value for e in html_els]
            details["f_markdown_card_values"] = [v for v in md_values if "probe-markdown-card" in v]

            # (c) AppTest.from_file(page_b) standalone -- no app.py/page_a ever ran.
            at_standalone = AppTest.from_file(str(page_b_path))
            at_standalone.run(timeout=30)
            details["c_standalone_runs_without_exception"] = not at_standalone.exception
            standalone_md = [m.value for m in at_standalone.markdown]
            details["c_standalone_defensive_default_seen"] = any(
                "PAGE_B_SEES_SHARED=MISSING" in v for v in standalone_md
            )
    except Exception as exc:  # noqa: BLE001 -- harness failure, not a spike finding
        print(json.dumps({"verdict": "harness_failure", "details": {"error": repr(exc)}}))
        return 1

    ok = (
        details["a_entry_runs_without_exception"]
        and details["b_page_b_runs_without_exception"]
        and details["c_standalone_runs_without_exception"]
    )
    verdict = "apptest_ok" if ok else "apptest_failed"
    print(json.dumps({"verdict": verdict, "details": details}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
