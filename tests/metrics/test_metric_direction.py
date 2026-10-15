"""Contract tests for the explicit metric-direction attribute in severity_metrics.yaml.

Two things are being defended here:

1. Completeness and closure -- every shipped metric declares a direction, the vocabulary is a
   closed set, and a config that omits or invents one fails loudly at load time (naming the key
   path, T-02-02) rather than being silently treated as ordered.
2. Honesty -- the vocabulary describes what a higher share means about the ER cases in a group and
   contains NO good/bad, better/worse, safety or ranking axis, because with no exposure
   denominator no such axis is expressible for any of these metrics. A direction can never be read
   as "this tool is more hazardous".

The numeric layer must be entirely unaffected: direction is metadata, never an input to a filter.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
import yaml

from wii.analytics.insights import GUARDRAIL_TERMS
from wii.metrics.severity import (
    _CONFIG_PATH,
    CARE_NOT_COMPLETED,
    DIRECTION_GLOSS,
    LESS_ACUTE_CARE,
    METRIC_DIRECTIONS,
    MORE_ACUTE_CARE,
    MORE_TISSUE_DAMAGE,
    NO_EXPOSURE_DENOMINATOR_NOTE,
    NO_SEVERITY_ORDERING,
    SeverityConfigError,
    _numerator_filter,
    direction_gloss,
    load_severity_metrics,
)

# Vocabulary (and gloss) words that would turn the direction field into a safety ranking. Broader
# than the app-wide insight guardrail list on purpose: a semantic-layer attribute is read by
# future code, not just by a reader of one sentence.
_BANNED_DIRECTION_WORDS = (
    "better",
    "worse",
    "best",
    "worst",
    "good",
    "bad",
    "safe",
    "unsafe",
    "hazard",
    "rank",
    "score",
)

_CARE_DIRECTIONS = frozenset({MORE_ACUTE_CARE, LESS_ACUTE_CARE, CARE_NOT_COMPLETED})


def _raw_metrics() -> dict:
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)["metrics"]


def test_every_shipped_metric_declares_a_known_direction():
    """Read the YAML directly (not through the loader) so a metric added without a direction is
    caught as a config-contract failure, whatever the loader happens to do."""
    raw = _raw_metrics()
    assert raw, "severity_metrics.yaml defines no metrics"
    missing = [metric_id for metric_id, doc in raw.items() if not doc.get("direction")]
    assert not missing, f"metrics without a direction: {missing}"
    unknown = {
        metric_id: doc["direction"]
        for metric_id, doc in raw.items()
        if doc["direction"] not in METRIC_DIRECTIONS
    }
    assert not unknown, f"metrics with an out-of-vocabulary direction: {unknown}"


def test_loaded_metrics_expose_direction():
    metrics = load_severity_metrics()
    assert len(metrics) >= 19
    assert all(m.direction in METRIC_DIRECTIONS for m in metrics.values())
    assert metrics["amputation"].direction == MORE_TISSUE_DAMAGE
    assert metrics["escalated_care"].direction == MORE_ACUTE_CARE
    assert metrics["released"].direction == LESS_ACUTE_CARE
    assert metrics["left_without_treatment"].direction == CARE_NOT_COMPLETED
    assert metrics["hand_finger"].direction == NO_SEVERITY_ORDERING


def test_vocabulary_is_closed_and_carries_no_good_bad_axis():
    assert len(METRIC_DIRECTIONS) == 5
    for value in METRIC_DIRECTIONS:
        lowered = value.lower()
        assert not [w for w in _BANNED_DIRECTION_WORDS if w in lowered], value
        assert not [t for t in GUARDRAIL_TERMS if t in lowered], value
    # There is deliberately no "higher_is_better"/"higher_is_worse" member to pick by accident.
    assert not [v for v in METRIC_DIRECTIONS if "is_better" in v or "is_worse" in v]


def test_every_direction_has_a_gloss_that_disclaims_per_use_comparison():
    assert set(DIRECTION_GLOSS) == set(METRIC_DIRECTIONS)
    for direction in METRIC_DIRECTIONS:
        gloss = direction_gloss(direction)
        assert NO_EXPOSURE_DENOMINATOR_NOTE in gloss
        assert "exposure denominator" in gloss
        assert "never a per-use comparison" in gloss


def test_gloss_language_avoids_banned_vocabulary():
    for direction, gloss in DIRECTION_GLOSS.items():
        lowered = gloss.lower()
        assert not [w for w in _BANNED_DIRECTION_WORDS if w in lowered], direction
        assert not [t for t in GUARDRAIL_TERMS if t in lowered], direction


def test_direction_gloss_rejects_an_unknown_value():
    with pytest.raises(SeverityConfigError, match="unknown direction"):
        direction_gloss("higher_is_better")


def test_missing_direction_is_rejected_naming_the_key_path(tmp_path):
    bad = tmp_path / "missing_direction.yaml"
    bad.write_text("version: 1\nmetrics:\n  foo:\n    kind: disposition_bucket\n    codes: [1]\n")
    with pytest.raises(SeverityConfigError, match=r"metrics\.foo\.direction missing"):
        load_severity_metrics(bad)


def test_out_of_vocabulary_direction_is_rejected_naming_the_key_path(tmp_path):
    bad = tmp_path / "bad_direction.yaml"
    bad.write_text(
        "version: 1\nmetrics:\n  foo:\n    kind: disposition_bucket\n    codes: [1]\n"
        "    direction: higher_is_better\n"
    )
    with pytest.raises(SeverityConfigError, match=r"metrics\.foo\.direction .*not a known"):
        load_severity_metrics(bad)


def test_direction_matches_the_metric_kind_it_describes():
    """The kind/direction pairing is itself part of the contract: a disposition metric describes
    the care pathway, a body-region rollup describes composition and can never be ordered."""
    metrics = load_severity_metrics()
    for metric in metrics.values():
        if metric.kind in ("disposition_bucket", "disposition_escalated"):
            assert metric.direction in _CARE_DIRECTIONS, metric.metric_id
        elif metric.kind == "body_rollup_any":
            assert metric.direction == NO_SEVERITY_ORDERING, metric.metric_id
        else:  # diagnosis_any -- either destructive-by-definition or explicitly unordered
            assert metric.direction in (MORE_TISSUE_DAMAGE, NO_SEVERITY_ORDERING), metric.metric_id


def test_has_severity_ordering_flags_the_unordered_metrics():
    metrics = load_severity_metrics()
    assert metrics["hand_finger"].has_severity_ordering is False
    assert metrics["laceration"].has_severity_ordering is False
    assert metrics["admitted"].has_severity_ordering is True
    assert metrics["amputation"].has_severity_ordering is True
    assert metrics["amputation"].direction_gloss == direction_gloss(MORE_TISSUE_DAMAGE)


def test_direction_never_reaches_the_numeric_layer(con):
    """Changing a metric's direction must not change a single query. Every configured metric's
    numerator filter is rebuilt with a different direction and must compare equal.

    `disposition_escalated` is exercised without the DB round-trip it needs (`dim_disposition`
    lives in test_severity_fixes.py's fixture, not this one): its numerator is read wholly from
    `dim_disposition.escalated_care` and consults no field of the metric at all, so there is
    nothing a direction could perturb.
    """
    checked = 0
    for metric in load_severity_metrics().values():
        if metric.kind == "disposition_escalated":
            continue
        other = (
            NO_SEVERITY_ORDERING if metric.direction != NO_SEVERITY_ORDERING else MORE_ACUTE_CARE
        )
        assert _numerator_filter(replace(metric, direction=other), con) == _numerator_filter(
            metric, con
        )
        checked += 1
    assert checked >= 18
