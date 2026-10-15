# Classifier Validation Report (NARR-06)

**Disclosure:** the labels in `tests/fixtures/narratives/labeled_validation.csv` were produced during development with an AI assistant, blind to the classifier's output, not by a clinician, epidemiologist or trained NEISS coder. They are a best-effort, disclosed approximation of ground truth.

**Dev vs holdout:** rules were tuned only against the `dev` split (40 cases); the `holdout` split (the remainder) was never used to hand-tune regex patterns or confidence weights, and is the reported accuracy source of truth.

**No accuracy target is enforced.** Numbers below are published as measured, not gated against a threshold.

Classifier version: `rules-1.2.0`

## Coverage

- In-scope rows: 63658
- Classified: 63658 (100.0%)

## Unknown %

- Mechanism unknown: 36.3%
- Subtype unknown: 89.9%

## Confidence distribution

- High (>=0.80): 18351
- Mid: 24621
- Low (<0.50): 20453
- Deciles: [20430, 0, 0, 0, 23, 0, 21280, 3341, 0, 18351]

## Source counts (rules vs llm)

{'rules': 63425}

## Product-code vs narrative disagreements

- Count: 314

## Subtype coverage by broad code

| product_code | total | refined | rate |
|---|---|---|---|
| 809 | 1235 | 25 | 2.0% |
| 845 | 6770 | 83 | 1.2% |
| 847 | 4640 | 40 | 0.9% |
| 862 | 874 | 0 | 0.0% |
| 870 | 1595 | 12 | 0.8% |
| 871 | 2000 | 24 | 1.2% |
| 895 | 5466 | 1073 | 19.6% |
| 899 | 7389 | 177 | 2.4% |

## Per-field accuracy (labeled fixture)

| split | tool_subtype | final_tool_id | activity | primary_mechanism | maintenance_or_setup | ppe_mentioned | guard_mentioned | maintenance_context | power_state_mentioned |
|---|---|---|---|---|---|---|---|---|---|
| holdout | 99.3% | 99.3% | 82.5% | 70.1% | 99.3% | 98.5% | 100.0% | 99.3% | 100.0% |
| all | 99.4% | 99.4% | 83.1% | 64.4% | 98.3% | 98.3% | 100.0% | 98.3% | 100.0% |

## Per-class precision/recall (labeled fixture, holdout split)

### tool_subtype

| class | precision | recall | support |
|---|---|---|---|
| angle_grinder | 100.0% | 100.0% | 1 |
| chisel | 100.0% | 100.0% | 7 |
| chop_saw_metal | 100.0% | 100.0% | 2 |
| grinder_generic | 100.0% | 100.0% | 10 |
| miter_saw | 100.0% | 92.3% | 13 |
| unknown | 99.0% | 100.0% | 104 |

### primary_mechanism

| class | precision | recall | support |
|---|---|---|---|
| direct_cutting_contact | 81.6% | 62.0% | 50 |
| entanglement | 100.0% | 83.3% | 6 |
| fall_trip | 100.0% | 12.5% | 8 |
| flying_debris_fb | 35.7% | 100.0% | 5 |
| kickback_ejection | 100.0% | 100.0% | 15 |
| loss_of_tool_control | 0.0% | 0.0% | 0 |
| maintenance_setup | 0.0% | 0.0% | 1 |
| pinch_crush | 50.0% | 50.0% | 2 |
| unknown | 61.0% | 76.6% | 47 |
| workpiece_slip | 100.0% | 66.7% | 3 |

## Labeled fixture size

- All: 177
- Holdout: 137
- Dev: 40

