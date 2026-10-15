"""wii.metrics: the only place survey-weighted math happens (D-01).

Nothing outside this package may sum `weight`. Every per-tool/per-code/any-recorded row is a
distinct-case count within its own group and does NOT sum to the total burden (D-07) -- a case
associated with two in-scope tools counts once per tool and once in the overall total.
"""

from __future__ import annotations

from wii.metrics.db import connect
from wii.metrics.estimator import (
    EstimateRecord,
    estimate_by,
    estimate_by_pair,
    estimate_total,
    share_by,
)
from wii.metrics.filters import CaseFilter
from wii.metrics.severity import severity_profile
from wii.metrics.stability import (
    ANNUAL_BASIS_LABEL,
    ESTIMATE_LABEL,
    LIMITED_SAMPLE_LABEL,
    PERIOD_BASIS_LABEL,
    SUPPRESSED_LABEL,
    apply_suppression,
)
from wii.metrics.variance import InsufficientPSUsError

__all__ = [
    "connect",
    "CaseFilter",
    "estimate_total",
    "estimate_by",
    "estimate_by_pair",
    "share_by",
    "severity_profile",
    "EstimateRecord",
    "apply_suppression",
    "SUPPRESSED_LABEL",
    "LIMITED_SAMPLE_LABEL",
    "ESTIMATE_LABEL",
    "PERIOD_BASIS_LABEL",
    "ANNUAL_BASIS_LABEL",
    "InsufficientPSUsError",
]
