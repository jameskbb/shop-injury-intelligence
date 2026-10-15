## What NEISS is

The National Electronic Injury Surveillance System (NEISS) is a probability sample of U.S.
hospital emergency departments, operated by the Consumer Product Safety Commission. Each record
is a single ER visit where a clinician coded a consumer product as associated with the injury.
NEISS does not track every injury that occurs, only injuries treated at a sampled ER, and it does
not track any use of a product that did not end in an ER visit.

## Hospital probability sampling

Per CPSC's own description of the NEISS program, the system draws a probability sample of about
100 U.S. hospital emergency departments from a stratified national sample of hospitals with
24-hour emergency departments, weighted by ER visit volume so both very large and very small
hospitals are represented. Because each hospital is included with a known probability, every case
in the sample carries a survey weight that lets counts from those hospitals be scaled up into a
national estimate.

## Weights

Each NEISS case has a sample weight reflecting the inverse of its hospital's selection
probability. National estimates are the sum of weights for matching cases, never a simple row
count. A tool that appears in many ER visits at a handful of large hospitals can produce a larger
national estimate than one appearing at fewer, smaller hospitals with a raw sample count that
looks similar.

## National estimates

Every estimate this app shows is labeled "Estimated U.S. ER-treated injuries" and is a
survey-weighted projection, not a literal count of visits observed. Estimates are shown either as
a period total (the estimated injuries across the selected years) or an annual average
(period total divided by the number of years), and the app always states which basis is in use.

## Raw sample N

Alongside every weighted estimate, the app also shows the raw sample N: the number of actual
NEISS records behind that estimate, before weighting. Raw N is what determines statistical
stability, not the (much larger) weighted estimate itself.

## Confidence and statistical stability

NEISS estimates carry sampling error because they come from a probability sample, not a census.
The app follows the CPSC's own stability convention, using each estimate's raw sample N and
coefficient of variation (CV) to flag results with wide uncertainty rather than presenting every
number with equal confidence.

## Estimate suppression

Following the CPSC's published convention, an estimate is treated as unstable and suppressed when
any of the following hold: the estimate itself is below 1,200, the raw sample N is below 20, or
the coefficient of variation exceeds 0.33. Suppressed values are never shown as a number; the app
shows "Estimate suppressed – insufficient statistical stability" instead. Estimates that pass this
check but still carry meaningful uncertainty are labeled "Limited sample – interpret cautiously."
