"""Representative-narratives section tolerates a tool whose mechanism frame has no rows."""

from __future__ import annotations

from types import SimpleNamespace

import polars as pl

from wii.app.pages import tool_profiles
from wii.metrics import CaseFilter


def test_empty_mechanism_frame_shows_empty_state(monkeypatch):
    shown: list[str] = []
    fake_st = SimpleNamespace(
        header=lambda *args, **kwargs: None,
        caption=lambda *args, **kwargs: None,
        info=lambda body, **kwargs: shown.append(body),
        markdown=lambda *args, **kwargs: shown.append("markdown"),
    )
    monkeypatch.setattr(tool_profiles, "st", fake_st)

    # mechanism_profile_frame() returns this column-less shape when no mechanism has a row.
    tool_profiles._render_representative_narratives(CaseFilter(), "table_saw", pl.DataFrame())

    assert shown == [tool_profiles._NARRATIVE_EMPTY_COPY]
