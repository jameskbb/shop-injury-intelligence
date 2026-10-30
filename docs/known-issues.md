# Known issues

Reviewed 2026-10-23. The [release checklist](release-checklist.md) tracks verification,
publication, and hosting work. This page records application findings and methodological limits.

## Remaining limits

- The pooled multi-year comparison against the live CPSC Query Builder is not implemented.
  The offline single-year reconciliation covers 108 product/year rows with zero mismatches;
  synthetic tests cover pooling mechanics. These checks are not a live pooled reconciliation.
- Narrative-derived classifications remain approximate. Mechanism holdout accuracy is 70.1%,
  and 36.3% of in-scope product rows have an unknown mechanism. The validation fixture uses
  disclosed AI-assisted labels rather than clinical expert ground truth. See
  [classifier validation](classifier_validation.md).
- Browser verification uses Chromium with emulated desktop and phone viewports. Physical phones,
  Safari, and the eventual production host need their own checks.
- A new-year refresh and automated pull-request creation have not been observed on the hosted
  runner. The successful current-data check exercises only the no-op path.

## Findings addressed before release

| Prior finding | Resolution |
| --- | --- |
| S-1: threshold multiples implied comparable strength across categories | Removed strength tiers and bars. Findings are grouped by kind and ordered by descriptive difference, with no evidence-strength interpretation. |
| S-2: peer comparisons changed with the display basis | Peer membership and values consistently use annual-average reportability and estimates. |
| S-3: impact labels implied consequences | Removed impact tier names. |
| S-4: filtered baselines were called workshop-wide | Baseline text explicitly names the current filters. |
| H-1: heatmap annotation and year labels collided | The coding change uses the chart legend; year labels are sparse and horizontal, with bounded tool-label margins and full names retained in hover. |
| H-2: heatmap caption described the wrong ranking | The caption distinguishes full-record heatmap ordering from the preceding selected-window ranking. |
| O-1: Overview compared incompatible effect-size units | The shared engine selects findings in turns across kinds and orders only within each kind. |
| C-1: window lengths could drift from configuration | Trend and KPI delta windows read the configured trend-window length. |
| L-1 and L-2: tier styling and inaccurate sanitizer explanation | The tier renderer and its obsolete styling explanation were removed. |
| L-3: estimate precision differed between chips and prose | Current shared estimate formatting rounds to the nearest hundred in both. |
| L-4: direction metadata had no UI consumer | Retained as engine metadata. It does not drive any displayed ranking or claim. |

Suppressed estimates now say "Not reportable" or "N/R" in compact cells, with the full state in
tooltips. A repository-wide writing guard checks the prohibited punctuation rule.
