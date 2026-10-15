# Known Issues

Open findings from the 2026-09-17 adversarial sweep of `01fc117` that were NOT fixed in
`06b3523`. Recorded rather than dropped. Each cites the reviewer's own evidence.

## High

### S-1 · Signals "threshold multiple" does not make insight kinds comparable
`signals.py:215-233` normalises each finding to `ratio / share_ratio_min`. But a share ratio is
bounded by `1 / baseline`, so the achievable ceiling differs per category. Measured on committed
data: hand/finger (baseline 64.0%) tops out at **1.04x** and can therefore never reach NOTABLE;
direct-cutting-contact tops out at 1.53x and can never reach HIGH IMPACT; puncture reaches 11.8x
and fatality 4,700x. The tier is thus partly a function of how rare a category is, not how strong
the finding is. `share_pp_min` (the second half of the engine's own gate) is not consulted.

**Why it matters:** the page states "a given multiple means the same thing for every kind"
(`signals.py:18-19`, `insights.yaml:58-59`). That claim is false as written.

**Direction:** rank within kind, or normalise against each kind's attainable ceiling, and correct
the claim either way.

### S-2 · `baseline_comparison` strength moves with the display toggle
Same finding reads 2.73x on Annual average and 3.09x on Period total, because the "average tool"
changes membership at the 1,200 reportability floor (19 vs 22 qualifying tools). Pre-existing in
`insights.py:281-313`, but Signals now ranks it #2 and tags it HIGH IMPACT. It is a volume
finding: the popularity confound the Methodology callout explicitly disclaims.

### S-3 · "HIGH IMPACT" is consequence language
`test_tier_names_are_not_hazard_language` passes only because it checks the five
`GUARDRAIL_TERMS`. "Impact" asserts consequence, which this dataset cannot support. Rename to a
strength-of-evidence word.

### S-4 · Signals baseline wording is wrong under a family/tool filter
With `families=("saws",)` a card reads "than the workshop baseline: 12.5% vs 4.8%", but 4.8% is
the saws-only eye share; workshop-wide is 15.4%. Template pre-existing (`insights.py:178`), but
`signals.py:89-95` hard-codes "Compared with the workshop-wide share…" and the page title says
"across every tool".

## Medium

- **H-1** Heatmap: the 2019 annotation overdraws the legend on desktop; at 390px `dtick=1`
  overlaps 19 of 20 year labels (`temporal_heatmap.py:280`).
- **H-2** `overview.py:10` says the heatmap reuses the ranked-bars ordering; it does not
  (all-years total vs sidebar window). Its caption points at Tool Comparison / Tool Profiles for
  "the full ranking", which is actually the chart directly above it.
- **O-1** Overview still orders insight cards by raw `effect_size`, the ordering `signals.py:13-16`
  argues is invalid across kinds. No visible contradiction today because no trend candidate
  qualifies, but a qualifying trend would rank #1 on Overview and possibly MODERATE on Signals.
- **C-1** `_TREND_WINDOW_YEARS = 3` is hard-coded in `data.py` while Signals prints the YAML value;
  `_KPI_DELTA_WINDOW_YEARS` lives in no config.

## Low

- **L-1** NOTABLE uses `--wii-caution`, the app's "Limited sample" colour; a tier and a stability
  state now share a hue.
- **L-2** `signals.py:97-99` asserts Streamlit's sanitizer strips inline styles. It does not;
  `kpi_card`'s inline style survives.
- **L-3** Insight chips show unrounded estimates ("30,931") beside prose rounded to the hundred
  ("30,900"), inherited from `insight_card.py:57`.
- **L-4** `direction` metadata has no consumer under `src/wii/app`; it is currently inert.

## Verified sound in the same sweep

Guardrail scan clean over all new copy including runtime-interpolated text; `candidate_pool`
bypasses no gate; the delta CI gate behaves correctly (all three blocked cards genuinely overlap);
heatmap log floor at 1,200 hides no displayable cell; QA panel matches `qa_report.json`; no
cache-key collisions; no perf regression (Overview cold 3.39s vs 3.74s pre-change); perspective is
real; no horizontal scroll at 390px.
