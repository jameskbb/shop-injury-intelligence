"""wii.app.charts: pure Plotly figure-builder functions (D-06/D-09). Every `build_*` function
takes prepared Polars frames + `basis` and returns a `go.Figure` -- no `st.*` calls inside a
builder, so every chart is unit-testable without an AppTest/running app session (though the
modules may still import `wii.app.components.stability_badge`, which itself imports `streamlit`
only for one unrelated helper -- the "no Streamlit" property that matters here is "builders never
call an `st.*` function themselves", not "never imports a module that imports streamlit").
"""

from __future__ import annotations
