# Test Traceability

The core testing checklist maps to real, synthetic-fixture pytest node ids below.
All tests use synthetic fixtures only; none download CPSC data during a normal run.
`realdata`-marked tests read local `data/raw/` files and are excluded by default
(`addopts = "-m 'not realdata'"` in `pyproject.toml`); they only run when raw annual files are
present on disk (a developer's local sync, or the `refresh-data.yml` runner after its own
online sync step).

## Method

Each row's "Covering tests" column cites one or more `path::test_name` pytest node ids. A test
(`tests/scripts/test_testing_doc.py`) parses this table, resolves every cited node id against the
live `tests/` tree (the file must exist and define a module-level or class-level
`def`/`async def` with that name; any `[param]` parametrize suffix is stripped before matching),
and fails if any id no longer resolves, or if any of the 14 brief items is missing or cites no
test. Renaming or deleting a cited test therefore fails this check rather than letting the doc go
silently stale.

Run the default suite (synthetic fixtures only, matches CI):

```bash
uv run pytest -q
```

Run the `realdata` suite (requires local `data/raw/neiss{YEAR}.tsv` files; never run in
`ci.yml`, only in `refresh-data.yml` after its online sync step, or locally after
`scripts/sync_neiss.py`):

```bash
uv run pytest -m realdata -q
```

Before a deployment, also follow the [deployment smoke checklist](deployment.md#browser-smoke-checklist).
HTTP health and automated tests do not verify the hosted browser session, proxy, or WebGL behavior.

## Screenshots

A change to a page's interface re-shoots its captures in the same change, so the README gallery
never shows a screen that no longer exists. Re-render the README title card too whenever the
product's look or the committed data changes:

```bash
uv run --with playwright==1.62.0 python scripts/capture_screenshots.py
uv run --with playwright==1.62.0 python docs/src/render_title_card.py
```

## Traceability Table

| Brief TESTING item | Covering tests | Notes |
| --- | --- | --- |
| Weighted injury estimates | `tests/metrics/test_estimator.py::test_estimate_by_product_code_841_matches_hand_computed_variance`, `tests/metrics/test_estimator.py::test_estimate_total_returns_estimate_record`, `tests/scripts/test_validate_against_query_builder.py::test_compare_row_matches_when_all_fields_within_tolerance` | Also reconciled offline against the official CPSC Query Builder for every in-scope code across four years (`docs/validation.md`) |
| Unique-case deduplication | `tests/test_multiyear.py::test_duplicate_case_id_across_years_aborts`, `tests/test_pipeline_e2e.py::test_duplicate_case_id_aborts`, `tests/metrics/test_estimator.py::test_duplicate_bridge_slot_for_same_code_counts_case_once` | |
| Multi-product cases | `tests/metrics/test_estimator.py::test_two_tool_case_counts_once_per_tool_and_once_in_total`, `tests/test_case_attributes.py::test_bridge_case_products_reserved_columns` | |
| Body-part bridge handling | `tests/test_body_parts_slice.py::test_two_body_part_slots_produce_paired_bridge_rows`, `tests/test_body_parts_slice.py::test_body_part_2_zero_is_a_real_internal_slot2_row`, `tests/test_body_parts_slice.py::test_unknown_body_part_code_on_any_row_aborts_and_names_field` | |
| Diagnosis bridge handling | `tests/test_dictionaries.py::test_diagnosis_two_slots_produce_paired_bridge_row_with_other_text`, `tests/test_dictionaries.py::test_diagnosis_slot2_null_produces_single_row` | |
| Date filtering | `tests/metrics/test_filters.py::test_multi_year_pooling_sums_across_years`, `tests/metrics/test_severity_fixes.py::test_years_filter_changes_disposition_share_result`, `tests/test_multiyear.py::test_treatment_date_out_of_file_year_aborts` | Year-range filter and treatment-date QA are both covered; NEISS/the app never filters at day granularity |
| Tool taxonomy | `tests/test_dictionaries.py::test_in_scope_product_mapping_fails_on_null_tool_id`, `tests/test_dictionaries.py::test_non_taxonomy_coproduct_is_advisory_only`, `tests/test_dictionaries.py::test_dim_shapes_after_run`, `tests/test_taxonomy_contract.py::test_every_in_scope_code_maps_to_exactly_one_tool_in_a_defined_family`, `tests/test_taxonomy_contract.py::test_no_code_is_both_in_scope_and_excluded`, `tests/test_taxonomy_contract.py::test_excluded_categories_named_in_tax_01_are_not_in_scope`, `tests/test_taxonomy_contract.py::test_config_carries_a_version_identifier` | Structural/config-contract coverage is fully automated. The one non-machine-readable check (every in-scope code validated against the January 2025 NEISS Product Code Comparability Table, pages 27-30) was performed as a **documented manual verification**, not an automated test. Each code was checked by hand against that table, and each records its own `validated_against: "2025 Comparability Table"` field in `src/wii/taxonomy/config/product_taxonomy.yaml`. |
| Broad-code narrative refinement | `tests/narratives/test_rules.py::test_899_angle_grinder_refines`, `tests/narratives/test_rules.py::test_845_conflicting_saw_terms_do_not_refine`, `tests/narratives/test_classify.py::test_specific_code_is_never_overridden_by_narrative` | All 8 broad codes have dedicated positive/negative-refinement tests in `tests/narratives/test_rules.py` |
| Mechanism rules | `tests/narratives/test_mechanisms_ppe.py::test_mechanism_priority_matches_d15_exactly`, `tests/narratives/test_mechanisms_ppe.py::test_kickback_with_direct_cutting_contact_resolves_to_kickback_by_priority`, `tests/narratives/test_mechanism_improvement.py::test_laceration_verb_on_saw_is_direct_cutting_contact` | |
| Severity metrics | `tests/metrics/test_shares_severity.py::test_share_by_matches_direct_weighted_ratio`, `tests/metrics/test_aggregates.py::test_agg_tool_severity_matches_severity_profile`, `tests/metrics/test_severity_fixes.py::test_not_recorded_is_weighted_estimate_derived_from_dim_disposition` | |
| Estimate suppression | `tests/metrics/test_estimator.py::test_apply_suppression`, `tests/analytics/test_insights.py::test_unstable_tool_cell_excluded` | `test_apply_suppression` is parametrized on estimate/raw_n/CV thresholds |
| Age decoding | `tests/test_case_attributes.py::test_age_decoding_behavior`, `tests/test_case_attributes.py::test_age_115_is_top_coded`, `tests/test_case_attributes.py::test_invalid_ages_abort_and_list_offending_value` | `test_age_decoding_behavior` is parametrized |
| Pre/post-2019 schemas | `tests/test_case_attributes.py::test_schema_era_pre_2019_blank_body_part_2`, `tests/test_case_attributes.py::test_schema_era_post_2019_ten_percent_filled`, `tests/test_case_attributes.py::test_secondary_fill_rate_by_era_fails_on_2018_with_any_value` | |
| Insight thresholds | `tests/analytics/test_insights.py::test_share_ratio_exactly_at_threshold_passes`, `tests/analytics/test_insights.py::test_raw_n_49_excluded`, `tests/analytics/test_insights.py::test_raw_n_50_included`, `tests/analytics/test_insights.py::test_trend_exactly_20_percent_with_nonoverlapping_ci_passes`, `tests/analytics/test_insights.py::test_load_thresholds_reads_documented_defaults` | Boundary-exact assertions on every documented threshold |
