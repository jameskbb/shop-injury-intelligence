# Narrative Labeling Guide

## 1. Purpose and disclosure

This guide governs how `tests/fixtures/narratives/labeled_validation.csv` was produced: a
blind-labeled sample of CPSC NEISS incident narratives used to validate `wii.narratives`' rule
classifier (NARR-06).

**Disclosure:** the labels in `labeled_validation.csv` were produced during development with an AI assistant, blind to the classifier's output -- not by a clinician, epidemiologist or trained NEISS coder. They are a best-effort, disclosed approximation of ground truth: useful for structural validation and a rough coverage/precision signal, not a clinically authoritative annotation. No hard accuracy target is enforced on classifier-vs-label agreement; measured numbers are published as-is.

## 2. Taxonomy definitions (copied verbatim from `02-CONTEXT.md`, so the labeler and the
classifier share one definition, not a paraphrase)

### `tool_subtype` / final tool

Final tool set = the 27 official tools + 4 narrative-refined tools: `miter_saw`, `planer`,
`angle_grinder`, `bench_grinder`. Finer detail (belt/orbital/disc sander, brad/finish nailer,
stapler, scroll saw, chop saw--metal, etc.) goes in `tool_subtype`, not a separate final tool.
Refinement is allowed ONLY for broad codes 0895, 0809, 0845, 0847, 0871, 0899, 0862, 0870;
specific codes are never overridden. A refinement requires confidence >= 0.70 and no conflicting
subtype term. "CHOP SAW" -> `miter_saw` unless metal/abrasive context (METAL, STEEL, PIPE,
REBAR, ABRASIVE, CUT-OFF, ALUMINUM, COPPER, IRON) -> subtype `chop_saw_metal`, final tool stays
`other_power_saw`. 0845 "saws not specified" may refine to any saw tool actually named in the
narrative (table, circular, band, jig, reciprocating, miter).

### `activity` (8 values)

`cutting`, `drilling`, `fastening`, `sanding_grinding`, `shaping`, `setup_maintenance`,
`carrying_moving`, `unknown`.

### `primary_mechanism` (13 values, fixed priority order highest-first)

`kickback_ejection` > `entanglement` > `fastener_discharge` > `flying_debris_fb` >
`pinch_crush` > `electrical_burn` > `fall_trip` > `loss_of_tool_control` > `workpiece_slip` >
`direct_cutting_contact` > `maintenance_setup` > `other` > `unknown`. Multi-label:
`secondary_mechanisms` may list additional categories that also apply; `primary_mechanism` is
whichever matched category is HIGHEST in this priority list.

### `maintenance_or_setup` (boolean)

True when the narrative describes blade/bit change, cleaning, jam clearing, adjusting, or setup
of the tool. Separate from `primary_mechanism` (a maintenance incident can still have e.g.
`direct_cutting_contact` as its primary mechanism if that's the actual injury cause).

### `ppe_mentioned` / `guard_mentioned` (3-valued each)

`ppe_mentioned` in `{not_mentioned, mentioned_worn, mentioned_not_worn}`;
`guard_mentioned` in `{not_mentioned, mentioned_present, mentioned_absent_or_removed}`. The
`*_not_worn` / `*_absent_or_removed` values are set ONLY when the narrative explicitly says so
-- absence of any PPE/guard language is always `not_mentioned`, never inferred. Never infer
carelessness, intoxication, training, or experience from any field.

## 3. Worked examples

- **"CHOP SAW ON ALUMINUM"** -- metal context present (ALUMINUM) -> `tool_subtype`
  `chop_saw_metal`, `final_tool_id` stays `other_power_saw` (no refinement).
- **"USED A CHOP SAW ON A 2X4"** -- no metal context, wood implied -> `tool_subtype`
  `miter_saw`, `final_tool_id` `miter_saw`.
- **bare "GRINDER" with no qualifier** -- do not guess angle vs. bench; `tool_subtype` stays
  generic/`unknown`, no refinement (confidence below the 0.70 gate).
- **"SLIPPED"** -- disambiguate by subject: a tool word ("SAW SLIPPED", "BLADE SLIPPED") ->
  `loss_of_tool_control`; a body-part/material word ("HAND SLIPPED", "WOOD SLIPPED") ->
  `workpiece_slip`.
- **"TRIPPED OVER A SAW"** with no operational link to the tool -> `fall_trip` is NOT the
  primary mechanism unless the fall/trip cue co-occurs with a causal link to the tool's
  operation; an incidental tool mention in a fall narrative is not evidence of tool-caused
  injury.
- **imaging "SCANS WERE NEGATIVE"** -- `NEGATIVE` here refers to an imaging/lab result, not a
  negated tool/PPE/mechanism statement; it never negates a nearby positive match.
- **"DESPITE WEARING SAFETY GLASSES... INJURY OCCURRED"** -- label `ppe_mentioned` =
  `mentioned_worn` (a true, literal statement). Do NOT infer that the PPE failed, was
  inadequate, or that its use was somehow relevant to fault. The "never infer" guardrail applies
  directly: label only what the narrative literally states.

## 4. Blindness rule

Labels are written BLIND to the classifier's output. The labeler sees only `narrative` and
`official_product_code` (and the derived `official_tool_family`) -- never `classify_case`'s or
`RuleProvider`'s prediction for that case. `scripts/sample_labeling_candidates.py`, which draws
the candidate pool, never imports `wii.narratives`. The labeled fixture (this file's companion
CSV) is committed BEFORE any real-data classifier run, verified by git ancestry
(`tests/fixtures/narratives/labeled_validation.csv`'s first commit is an ancestor of the commit
that adds `src/wii/narratives/config/mechanisms.yaml`).

## 4a. Maintenance context and power-state labels

These two fields extend the fixture for the setup-and-maintenance analysis. Both are
labeled BLIND from the narrative text, under the same protocol as section 4 -- never by running
`classify_case`/`RuleProvider`.

### `maintenance_context` (8 values)

`not_maintenance` is the label whenever `label_maintenance_or_setup` is FALSE -- this keeps the
fixture consistent with the pre-existing boolean flag. When `label_maintenance_or_setup` is TRUE,
pick the single best-fitting category below (one example narrative pattern each):

- `blade_change` -- "CHANGING THE BLADE ON TABLE SAW" / "REPLACING A SAW BLADE"
- `bit_change` -- "CHANGING A DRILL BIT" / "REPLACING THE BIT"
- `jam_clearing` -- "CLEARING A JAM" / "UNJAMMING THE NAILER"
- `cleaning` -- "CLEANING HIS DRILL" / "CLEANING THE SAW"
- `adjustment` -- "ADJUSTING THE FENCE" / "TIGHTENING THE BLADE"
- `material_setup` -- "SETTING UP THE CUT" / "LOADING THE NAIL GUN" / "MEASURING THE BOARD"
- `other_maintenance` -- any other maintenance/setup activity not covered above (e.g. sharpening,
  oiling, repairing, or a tool used to perform a maintenance-adjacent task that doesn't fit a more
  specific category)

### `power_state_mentioned` (3 values)

`mentioned_unplugged` or `mentioned_plugged_in` ONLY when the narrative explicitly states that
condition. A negated statement such as "DID NOT UNPLUG" is labeled `not_mentioned`: this field
records explicit statements and never infers a skipped safety step. A narrative mentioning both
states (e.g. describing the tool being unplugged and later plugged back in) is also
`not_mentioned` -- the conservative, never-guessed default.

## 5. Split semantics

`dev` (40 cases) may be read while tuning classifier rules/weights. `holdout` (the remainder,
>= 110 cases) is reserved for reporting measured accuracy/coverage only -- it must not be used
to hand-tune regex patterns or confidence weights.
