## Validation

This app's survey-weighted metric layer is reconciled, product code by product code and year by
year, against the official CPSC Query Builder's own published estimates, sample sizes, and
coefficients of variation. Any mismatch between this app's numbers and the official tool is
recorded and shown below, rather than assumed away.

The narrative reader is evaluated on a labeled sample split into a development set, used to tune
the rules, and a separate holdout set that the rules were never tuned against. Reported per-field
accuracy and per-class precision/recall below come from the holdout split, since accuracy on the
development split would be optimistic. No accuracy target was set in advance for this classifier;
the numbers below are published as measured, whatever they turned out to be, not gated against a
threshold picked to look favorable.
