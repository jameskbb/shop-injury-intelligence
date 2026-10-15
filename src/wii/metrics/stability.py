"""CPSC suppression rule (D-04), applied once and reused by every caller in wii.metrics.

Which of the two exact-text labels to show a user is a display decision, not a stats decision --
this module only computes the flag and the machine-readable reasons.
"""

from __future__ import annotations

EST_LT_1200 = "est_lt_1200"
N_LT_20 = "n_lt_20"
CV_GT_033 = "cv_gt_0.33"

# Exact text, including the en dashes (D-04). Consumer picks which to show.
SUPPRESSED_LABEL = "Estimate suppressed – insufficient statistical stability"
LIMITED_SAMPLE_LABEL = "Limited sample – interpret cautiously"

# D-06 display labels.
ESTIMATE_LABEL = "Estimated U.S. ER-treated injuries"
PERIOD_BASIS_LABEL = "estimated injuries during selected period"
ANNUAL_BASIS_LABEL = "estimated annual average"


def apply_suppression(estimate: float, raw_n: int, cv: float | None) -> tuple[bool, list[str]]:
    """CPSC stability rule: estimate < 1,200 OR raw N < 20 OR CV > 0.33 -> unstable.

    A `cv` of None (a zero estimate) counts as failing the CV check. All applicable reasons are
    returned, never just the first one found.
    """
    reasons: list[str] = []
    if estimate < 1_200:
        reasons.append(EST_LT_1200)
    if raw_n < 20:
        reasons.append(N_LT_20)
    if cv is None or cv > 0.33:
        reasons.append(CV_GT_033)
    return (len(reasons) == 0, reasons)
