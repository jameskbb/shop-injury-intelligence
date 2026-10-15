"""Page modules registered via st.Page/st.navigation in app.py. Each page defines a `render()`
function and calls it at module scope -- pages return early from `render()` rather than calling
`st.stop()`, so app.py's footer (rendered after `nav.run()`) is never skipped (Wave-0 spike
decision, 03-01-SUMMARY.md)."""

from __future__ import annotations
