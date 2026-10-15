"""PAGE-04 SETUP & MAINTENANCE deep dive (D-14, 04-08 Task 2): copy guardrails, the
maintenance/setup share-by-tool ordering, power-state labels/suppression, escaped representative
narratives, and the zero-maintenance-cases AppTest smoke path.
"""

from __future__ import annotations

import polars as pl

import wii.app.data as data_module
from wii.analytics.insights import scan_guardrail_vocabulary
from wii.app import filters as filters_module
from wii.app.components import narrative_card
from wii.app.pages import mechanisms as mechanisms_page
from wii.metrics.stability import N_LT_20

from .conftest import APP_TIMEOUT

_MECHANISMS_PATH = "src/wii/app/pages/mechanisms.py"

# D-14: the maintenance deep dive especially must never imply a documented-absent practice
# was skipped -- only report what the narrative explicitly states.
OMISSION_TERMS: tuple[str, ...] = (
    "failed to",
    "forgot",
    "neglect",
    "skipped",
    "should have",
    "careless",
    "without unplugging",
    "did not unplug",
)


def _switch_to_mechanisms(at):
    at.run(timeout=APP_TIMEOUT)
    at.switch_page(_MECHANISMS_PATH)


# MAINTENANCE_INTRO is the verbatim D-14/UI-SPEC guardrail sentence itself -- it uses "skipped"
# inside an explicit negation ("absence of a mention is not evidence a step was skipped") to STATE
# the guardrail, not to violate it. Every other Setup & Maintenance copy constant must contain
# none of OMISSION_TERMS in any form.
_OMISSION_SCAN_EXEMPT = frozenset({"MAINTENANCE_INTRO"})


def test_maintenance_copy_guardrails_and_no_omission_language():
    for name in dir(mechanisms_page):
        if name.startswith("_"):
            continue
        value = getattr(mechanisms_page, name)
        if not isinstance(value, str):
            continue
        assert scan_guardrail_vocabulary(value) == [], f"{name} fails the guardrail scan"
        if name in _OMISSION_SCAN_EXEMPT:
            continue
        lowered = value.lower()
        for term in OMISSION_TERMS:
            assert term not in lowered, f"{name} contains omission term {term!r}"


def test_power_state_labels_exact():
    assert data_module.POWER_STATE_LABELS == {
        "not_mentioned": "Not mentioned",
        "mentioned_unplugged": "Mentioned (unplugged)",
        "mentioned_plugged_in": "Mentioned (plugged in)",
    }


def test_power_state_suppressed_rows_show_dash_and_raw_n(monkeypatch):
    def fake_cached_estimate_by(flt, by):
        assert by == "power_state"
        return pl.DataFrame(
            [
                {
                    "group_id": "not_mentioned",
                    "display_label": "not_mentioned",
                    "annual_average": 1500.0,
                    "period_total": 7500.0,
                    "ci_low_annual": 1400.0,
                    "ci_high_annual": 1600.0,
                    "ci_low": 7000.0,
                    "ci_high": 8000.0,
                    "raw_n": 50,
                    "cv": 0.1,
                }
            ]
        )

    monkeypatch.setattr(data_module, "cached_estimate_by", fake_cached_estimate_by)
    # `power_state_frame` is `st.cache_data`-cached and an AppTest run in an earlier test file
    # will have populated it for this same filter, which would serve real rows past the
    # monkeypatch. Clear it so this test measures the function, not the cache.
    data_module.power_state_frame.clear()
    flt = filters_module.default_case_filter()
    result = data_module.power_state_frame(flt, "annual_average")
    rows = {r["group_id"]: r for r in result.iter_rows(named=True)}

    assert rows["not_mentioned"]["state"] == "stable"
    assert rows["mentioned_unplugged"]["display_value"] == "—"
    assert rows["mentioned_unplugged"]["raw_n"] == 0
    assert rows["mentioned_unplugged"]["state"] == data_module.stability_badge.STATE_SUPPRESSED
    assert rows["mentioned_plugged_in"]["display_value"] == "—"
    assert rows["mentioned_plugged_in"]["raw_n"] == 0


def test_maintenance_share_by_tool_stable_first(monkeypatch):
    def fake_cached_share_by(flt, numerator, by):
        assert by == "tool"
        return pl.DataFrame(
            [
                {
                    "group_id": "circular_saw",
                    "display_label": "Circular Saw",
                    "share": 0.05,
                    "share_ci_low": 0.03,
                    "share_ci_high": 0.07,
                    "num_raw_n": 30,
                    "stable": True,
                    "suppression_reason": [],
                },
                {
                    "group_id": "table_saw",
                    "display_label": "Table Saw",
                    "share": 0.20,
                    "share_ci_low": 0.15,
                    "share_ci_high": 0.25,
                    "num_raw_n": 200,
                    "stable": True,
                    "suppression_reason": [],
                },
                {
                    "group_id": "drill",
                    "display_label": "Drill",
                    "share": 0.5,
                    "share_ci_low": None,
                    "share_ci_high": None,
                    "num_raw_n": 5,
                    "stable": False,
                    "suppression_reason": [N_LT_20],
                },
            ]
        )

    def fake_load_dims():
        return {
            "tools": pl.DataFrame(
                {
                    "tool_id": ["circular_saw", "table_saw", "drill"],
                    "label": ["Circular Saw", "Table Saw", "Drill"],
                }
            )
        }

    monkeypatch.setattr(data_module, "cached_share_by", fake_cached_share_by)
    monkeypatch.setattr(data_module, "load_dims", fake_load_dims)
    # Same cache caveat as the power-state test above: an earlier AppTest run caches the real
    # frame under this very filter.
    data_module.maintenance_share_by_tool_frame.clear()
    flt = filters_module.default_case_filter()
    result = data_module.maintenance_share_by_tool_frame(flt)
    assert result["tool_id"].to_list() == ["table_saw", "circular_saw", "drill"]


def test_maintenance_narratives_escaped():
    """The page now uses the ONE shared narrative-card builder instead of its own copy; the
    escaping contract travels with it."""
    row = {
        "narrative": "Case involved a <b>blade change</b> gone wrong.",
        "diagnosis_label": "Laceration",
        "disposition_label": (
            "Treated and released, or examined and released without treatment (also includes "
            "transfers for treatment to another department of the same facility without "
            "admission)"
        ),
        "age_bracket_label": "25-34",
        "year": 2023,
    }
    card_html = narrative_card.narrative_card_html(row)
    assert "&lt;b&gt;" in card_html
    assert "<b>" not in card_html
    # The ~150-character NEISS disposition is shortened on the card.
    assert "Treated &amp; released" in card_html
    assert "also includes transfers" not in card_html


def test_maintenance_empty_state(app_test, monkeypatch):
    class _EmptyOverall:
        raw_n = 0

    real_cached_estimate_total = data_module.cached_estimate_total

    def fake_cached_estimate_total(flt):
        if getattr(flt, "maintenance_or_setup", None):
            return _EmptyOverall()
        return real_cached_estimate_total(flt)

    monkeypatch.setattr(data_module, "cached_estimate_total", fake_cached_estimate_total)

    at = app_test
    _switch_to_mechanisms(at)
    at.run(timeout=APP_TIMEOUT)
    assert not at.exception

    info_values = [i.value for i in at.info]
    assert any(mechanisms_page.MAINTENANCE_EMPTY_COPY in text for text in info_values)
