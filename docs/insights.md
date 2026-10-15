# Insight engine thresholds

`wii.analytics.insights` generates candidate statements from metric-layer output frames only
(`src/wii/app/data.py`'s `insight_frames()` bundle builder resolves raw `wii.metrics` rows into
basis-matched, stability-classified cells before handing them to the engine). Every threshold
below lives in `src/wii/analytics/config/insights.yaml` (`load_thresholds()`); no finding text
or number is hard-coded in `insights.py`.

## Thresholds

| Key | Value | Meaning |
|-----|-------|---------|
| `min_raw_n` | 50 | Minimum raw sample-case count required of **every** compared cell. A cell with raw N 49 is excluded; 50 is included (`>=`). |
| `share_ratio_min` | 1.5 | A tool's share must be at least this many **times** the baseline/other-tool's share for `severity`/`disproportionate`/`pairwise_comparison` insights. Ratio exactly 1.5 passes. |
| `share_pp_min` | 5.0 | A tool's share must also be at least this many **percentage points** higher than the baseline/other-tool's share. 5.0 pp exactly passes; 4.99 pp fails. Both `share_ratio_min` and `share_pp_min` must pass together (AND, not OR). |
| `burden_outcome_ratio_min` | 1.5 | For `burden_vs_share`: (tool's share of a severe outcome's workshop-wide total) / (tool's share of overall burden) must be `>=` this ratio. |
| `trend_pct_min` | 20.0 | Minimum `|percent change|` between the latest-N-year and prior-N-year annual averages for a `trend` insight. 20.0% exactly passes (when the CI gate below also passes). |
| `trend_window_years` | 3 | Size (in years) of each of the two compared windows. |
| `trend_require_nonoverlapping_ci` | true | When true, a `trend` insight also requires the two windows' 95% CIs to not overlap. |
| `max_cards` | 5 | Maximum number of insights returned by `generate_insights`/`compare_tools`, after ranking. |
| `min_cards_for_section` | 3 | Below this many passing insights, the Overview page shows only the passing ones (possibly zero) plus a one-line note, **never padded** with weaker statements that didn't clear the thresholds above. |
| `narrative_min_confidence` | 0.70 | Minimum narrative-excerpt classifier confidence for the Tool Profiles "Representative Case Patterns" section (03-03), not consumed by this plan's charts. |

## The inclusive `>=` rule

Every threshold comparison in this module is inclusive: a value **exactly equal** to a threshold
passes. This applies to `min_raw_n` (raw N 50 passes), `share_ratio_min` (ratio 1.5 passes),
`share_pp_min` (5.0 pp passes), `burden_outcome_ratio_min` (ratio 1.5 passes), and `trend_pct_min`
(20.0% passes, subject to the CI-overlap gate). `tests/analytics/test_insights.py` asserts both
the at-threshold pass and the one-step-below fail for each gate.

## Unrounded evaluation, rounded display

Every threshold is evaluated against the **unrounded** metric value produced by `wii.metrics`
(e.g. a share of `0.14949` is compared against `share_pp_min` as `0.14949`, never as the
display-rounded `14.9%`). Rounding happens only in the rendered text: shares are shown to 1
decimal place (`14.9%`), estimates are rounded to the nearest 100. `wii.analytics.insights`
never receives a pre-rounded value: `src/wii/app/data.py`'s `insight_frames()` passes
`stability_badge.resolve`/`resolve_share`'s raw `.value` float, not `.display_value`. Mechanism
shares pass the same raw `share` float through too (`_mechanism_disproportionate_dims` reads
`resolve_share(row).value`), so a 4.995 pp gap that displays as "15.0% vs 10.0%" still fails the
5.0 pp gate.

## Stability and min-N gates

A comparison is only generated when **every** cell involved is `stability_badge.STATE_STABLE`
(never `limited` or `suppressed`, because comparing a
basis-mismatched, limited, or suppressed cell) **and** has `raw_n >= min_raw_n`. This is checked
independently for each side of a comparison (both a tool's cell and the workshop baseline cell
for `severity`/`disproportionate`; both windows for `trend`; both tools for
`pairwise_comparison`).

## Disproportionate dimensions (diagnosis, body region, mechanism)

The `disproportionate` kind (a body region, diagnosis or mechanism over-represented for one tool) is built from
three sources, all reduced to the same dimension-agnostic `DimensionCategory`/`ToolCell` shape
before reaching `wii.analytics.insights`; the engine itself has no idea which source a dimension
came from, so the same threshold gates and "higher than baseline" wording apply to all three:

- **Diagnosis** and **body region** come from `severity_metrics.yaml` metrics of kind
  `diagnosis_any` / `body_rollup_any`, via `src/wii/app/data.py`'s
  `_severity_and_disproportionate_dims`. Each metric's within-tool share (`by="tool"`) is compared
  against the workshop-wide share (`by="all"`) of the same diagnosis/body-region metric.
- **Primary mechanism** comes from `_mechanism_disproportionate_dims`: for each of the 11
  candidate `theme.MECHANISM_COLORS` keys (13 total, minus `unknown`/`other`; see below), the
  tool's within-tool share `share_by(flt, CaseFilter(mechanisms=(key,)), by="tool")` is compared
  against the all-workshop share `share_by(flt, CaseFilter(mechanisms=(key,)), by="all")` of that
  same mechanism. This is the SAME basis Tool Profiles' mechanism profile
  (`mechanism_profile_frame`) and Comparison's mechanism fingerprint columns
  (`_comparison_mechanism_dims`) already use.

**Mechanism is narrative-derived, not a coded NEISS field.** `primary_mechanism` comes from the
deterministic narrative classifier's output, mined from ER narrative text; it is not an official
CPSC-coded variable the way diagnosis and body part are. Every mechanism `category_label` starts
with "narrative-derived share of cases associated with {mechanism}" so the wording never implies
otherwise, and the rendered text uses only "associated with" / "among ER cases" framing, never
causal or per-use-risk language (same guardrail scan as every other insight kind). The share
denominator is all of the tool's filtered ER cases, including cases whose mechanism is unknown,
the same denominator the existing mechanism profile chart uses.

**`unknown` and `other` are never insight candidates.** Both are deterministic-narrative-classifier
coverage residue (cases the classifier could not confidently assign to a specific mechanism), not
an injury pattern; surfacing either as a "disproportionate" finding would misrepresent classifier
coverage gaps as a real workshop-safety signal. The exclusion is a
`src/wii/app/data.py` constant (`_MECHANISM_INSIGHT_EXCLUDED`), not an `insights.yaml` threshold
key; it is a candidate-set rule, not a numeric gate.

**No new threshold keys.** The mechanism dimension reuses the exact same `min_raw_n` /
`share_ratio_min` / `share_pp_min` gates documented above; nothing is hard-coded outside
`insights.yaml`. Mechanism dimensions also flow into `compare_tools`' pairwise "how they differ"
statements on Comparison, through the same `(*frames.severity, *frames.disproportionate)` pooling
every other disproportionate dimension already uses.

**Bounded cost.** Each candidate mechanism costs exactly two cached grouped queries (one
`share_by(..., by="tool")`, one `share_by(..., by="all")`), independent of how many tools exist in
the catalog (never a per-tool loop). For the 11-candidate mechanism palette this is 22 queries
total, the same order of magnitude as the existing diagnosis/body-region dimensions.

## Ranking rule

Candidates from every kind are pooled and ranked by `effect_size` descending (ratio for
`severity`/`disproportionate`/`burden_vs_share`/`baseline_comparison`/`pairwise_comparison`,
`|percent change|` for `trend`), then capped at `max_cards`. Effect-size scale differs across
kinds by design (a ratio and a percentage are not directly comparable); this is a documented
simplification, not a normalized composite score. Ties break deterministically by tool label (or
`tool_id`, or `""` for workshop-wide insights), then by `kind`, so re-running the engine on
identical input always produces the identical card order.

## Wording guardrails

Every template and every generated insight text is scanned (case-insensitive) for the banned
vocabulary: `risk`, `danger`, `dangerous`, `safer`, `caused by`. Insight language only ever uses
"associated with" / "among ER cases" framing; this engine never claims causation or per-use
risk, because NEISS carries no exposure denominator. Only the "higher than baseline/other tool"
direction is ever reported; the engine never emits a "lower"/"safer" framed statement.

## Zero and under-`min_cards_for_section` outputs

`generate_insights` returns an empty list when zero candidates qualify; the caller (Overview
page) shows the UI-SPEC empty-state copy, never a blank or omitted section. When fewer than
`min_cards_for_section` candidates qualify, the page shows exactly the passing candidates (which
may be zero) plus a one-line note; it never pads the section with weaker, threshold-failing
statements to reach a target count.
