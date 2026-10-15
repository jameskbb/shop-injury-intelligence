"""T-03-06 insight card escaping tests: data-derived text and labels never reach markup raw."""

from __future__ import annotations

import pytest

from wii.analytics.insights import Insight
from wii.app.components import insight_card


@pytest.fixture
def captured_markdown(monkeypatch):
    calls: list[str] = []

    def fake_markdown(body, unsafe_allow_html=False):
        assert unsafe_allow_html
        calls.append(body)

    monkeypatch.setattr(insight_card.st, "markdown", fake_markdown)
    return calls


def _insight(text: str, label: str) -> Insight:
    return Insight(
        kind="share",
        text=text,
        tool_id="tool",
        numbers=((label, 0.25),),
        raw_n=1500,
        effect_size=2.0,
    )


def test_insight_text_and_labels_are_escaped(captured_markdown):
    insight_card._render_cards([_insight("<script>alert(1)</script> & co", "<b>Share</b>")])

    (body,) = captured_markdown
    assert "<script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; co" in body
    assert "<b>Share</b>" not in body
    assert "&lt;b&gt;Share&lt;/b&gt;" in body
