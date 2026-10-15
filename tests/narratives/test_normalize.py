"""D-13 normalization regression tests (code review finding 4): negation window must end at a
comma, not just AND/BUT/period/semicolon."""

from __future__ import annotations

from wii.narratives.classify import classify_case


def test_negation_window_stops_at_comma():
    """'PT DENIES LOC, CUT FINGER ON TABLE SAW' -- the comma after LOC must end the negation
    window so 'CUT FINGER' (direct_cutting_contact) is NOT swept up as negated. Before the fix,
    the 4-token window from DENIES ran straight through the comma and negated CUT FINGER too,
    leaving the mechanism unknown."""
    e, _r = classify_case("PT DENIES LOC, CUT FINGER ON TABLE SAW", [(841, "table_saw")])
    assert e.primary_mechanism == "direct_cutting_contact"
