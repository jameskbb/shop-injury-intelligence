## Product coding

Every NEISS case lists up to three official CPSC product codes describing what was involved in
the injury. This app selects only the product codes that identify a home-workshop tool (power
saws, hand tools, drill presses, sanders, and similar) and discards every other product code on
the same case. A case can carry a workshop product code alongside unrelated products, such as a
ladder or a workbench, which are not in scope here.

## Narrative coding

Each case also includes a short, de-identified free-text narrative written by ER staff describing
what happened. This app applies a deterministic, rule-based reader to every narrative to extract
structured signals -- which specific tool was involved, what mechanism of injury occurred, whether
protective equipment or a guard was mentioned, and similar fields -- described fully under
"Narrative-classification methodology" below.

## Tool taxonomy

Workshop products are grouped into a curated taxonomy of individual tools (for example "table
saw", "angle grinder", "hand plane") nested under broader tool families (saws, abrasive tools,
cutting/shaping tools, drilling/fastening tools, manual tools). The taxonomy is versioned; the
version in effect for the data currently loaded is shown in the live pipeline metadata below.

## Multi-product cases

A single ER case can list more than one workshop product code -- for example, a case that
mentions both a table saw and a push stick. Such a case is counted once for each workshop product
it lists when computing per-product totals, so it is possible for the sum of per-product raw
counts to exceed the number of distinct workshop cases in the data.

## Body-part coding changes around 2019

CPSC introduced a more granular secondary body-part field starting with the 2019 data year. Before
2019, only a single primary body part was coded per case; from 2019 onward, a second body part is
also captured for a subset of cases. The app treats trend comparisons that cross this boundary
with care, since a shift in granularity is not the same as a shift in injury patterns.

## Diagnosis coding changes around 2019

Similarly, CPSC added an optional secondary diagnosis field starting in 2019, alongside the
existing primary diagnosis. As with body-part coding, the app does not treat this schema change as
evidence of any real change in injury severity or type over time.

## Data coverage

This app loads every NEISS annual public file from 2006 through the latest year CPSC has
published, filtered down to workshop-relevant cases. The specific years currently loaded are shown
in the live pipeline metadata below, read from the committed data, never assumed.

## Consumer and non-work-related scope

NEISS is a consumer product surveillance system: it captures ER visits regardless of whether the
injury happened at home, at work, or elsewhere, as long as a consumer product was involved. This
app does not attempt to separate occupational from personal use, since NEISS narratives rarely
state this distinction reliably.
