# NEISS workshop pipeline: QA report

STATUS: PASSED

All counts are raw NEISS sample records, not national estimates.

Generated at: 2026-09-15T16:09:21.434779+00:00
Processed years: 2006, 2007, 2008, 2009, 2010, 2011, 2012, 2013, 2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025

## Structural

| check_id | result | count | detail |
|---|---|---|---|
| years_processed | PASS | 20 | 20 year(s) processed |
| header_contract | PASS | 0 | All years match the 25-column header contract |
| unique_case_ids_within_year | PASS | 0 | No duplicate case_ids within any year |
| no_unparseable_rows | PASS | 0 | Every year's rows parsed and cast cleanly after rejoin |
| weights_positive | PASS | 0 | Every weight is positive and non-null |
| treatment_dates_in_file_year | PASS | 0 | Every treatment_date falls within its file's year |
| unique_case_ids_global | PASS | 0 | No case_id duplicated across years |
| design_psu_covers_fact_cases | PASS | 0 | Every fact (year,stratum,psu) exists in design_psu |
| bridge_products_reference_fact | PASS | 0 | Every bridge case_id references a fact case, and every fact case has an in-scope bridge row |
| known_body_part_codes | PASS | 0 | Every observed body_part code exists in its dictionary |
| known_diagnosis_codes | PASS | 0 | Every observed diagnosis code exists in its dictionary |
| known_disposition_codes | PASS | 0 | Every observed disposition code exists in its dictionary |
| known_location_codes | PASS | 0 | Every observed location code exists in its dictionary |
| known_sex_codes | PASS | 0 | Every observed sex code exists in its dictionary |
| in_scope_product_mapping | PASS | 0 | Every in-scope bridge row maps to a tool/family; dim_product matches the taxonomy |
| ages_in_valid_domain | PASS | 0 | Every age_raw value is in {0} U [2,115] U [201,223] |
| secondary_fill_rate_by_era | PASS | 0 | Every year's Body_Part_2 fill rate matches its schema era |
| enrichment_one_row_per_case | PASS | 0 | narrative_enrichment has exactly one row per fact_cases case_id (63425) |
| specific_codes_never_refined | PASS | 0 | Every non-broad-code in-scope row keeps final_tool_id == tool_id, no refinement |

## Advisory

| check_id | result | count | detail |
|---|---|---|---|
| rejoined_records | FAIL | 10 | year 2010: 1 rejoined; year 2013: 1 rejoined; year 2017: 1 rejoined; year 2018: 1 rejoined; year 2019: 1 rejoined; year 2021: 2 rejoined; year 2023: 3 rejoined |
| cp1252_fallback_years | FAIL | 3 | Years decoded via cp1252 fallback: [2008, 2009, 2017] |
| yoy_workshop_volume_within_40pct | PASS | 0 | No consecutive-year workshop volume swings exceed 40% |
| non_taxonomy_coproduct_codes | FAIL | 360 | 360 distinct non-taxonomy co-product code(s) observed |
| missing_narratives | PASS | 0 | Every case has a narrative |
| secondary_field_availability | PASS | 20 | year 2006: body_part_2=0.0%, diagnosis_2=0.0%, product_3=0.0%; year 2007: body_part_2=0.0%, diagnosis_2=0.0%, product_3=0.0%; year 2008: body_part_2=0.0%, diagnosis_2=0.0%, product_3=0.0%; year 2009: body_part_2=0.0%, diagnosis_2=0.0%, product_3=0.0%; year 2010: body_part_2=0.0%, diagnosis_2=0.0%, product_3=0.0%; year 2011: body_part_2=0.0%, diagnosis_2=0.0%, product_3=0.0%; year 2012: body_part_2=0.0%, diagnosis_2=0.0%, product_3=0.0%; year 2013: body_part_2=0.0%, diagnosis_2=0.0%, product_3=0.0%; year 2014: body_part_2=0.0%, diagnosis_2=0.0%, product_3=0.0%; year 2015: body_part_2=0.0%, diagnosis_2=0.0%, product_3=0.0%; year 2016: body_part_2=0.0%, diagnosis_2=0.0%, product_3=0.0%; year 2017: body_part_2=0.0%, diagnosis_2=0.0%, product_3=0.0%; year 2018: body_part_2=0.0%, diagnosis_2=0.0%, product_3=0.0%; year 2019: body_part_2=19.6%, diagnosis_2=19.6%, product_3=2.0%; year 2020: body_part_2=21.2%, diagnosis_2=21.2%, product_3=2.4%; year 2021: body_part_2=21.2%, diagnosis_2=21.2%, product_3=2.0%; year 2022: body_part_2=22.4%, diagnosis_2=22.4%, product_3=1.8%; year 2023: body_part_2=23.7%, diagnosis_2=23.7%, product_3=1.7%; year 2024: body_part_2=24.5%, diagnosis_2=24.5%, product_3=1.8%; year 2025: body_part_2=23.6%, diagnosis_2=23.6%, product_3=2.0% |
| workshop_case_volume_sanity | PASS | 0 | Every year's raw in-scope case count is within [1500, 6000] |
| classifier_coverage | PASS | 63658 | 63658/63658 in-scope rows classified (100.0%) |
| broad_code_refinement_rate | PASS | 29969 | 1434/29969 in-scope broad-code rows refined (4.8%) |
| aggregate_cells_omitted | PASS | 0 | 0 cells omitted |

## Secondary-field availability by year

| year | body_part_2 | diagnosis_2 | product_3 |
|---|---|---|---|
| 2006 | 0.0% | 0.0% | 0.0% |
| 2007 | 0.0% | 0.0% | 0.0% |
| 2008 | 0.0% | 0.0% | 0.0% |
| 2009 | 0.0% | 0.0% | 0.0% |
| 2010 | 0.0% | 0.0% | 0.0% |
| 2011 | 0.0% | 0.0% | 0.0% |
| 2012 | 0.0% | 0.0% | 0.0% |
| 2013 | 0.0% | 0.0% | 0.0% |
| 2014 | 0.0% | 0.0% | 0.0% |
| 2015 | 0.0% | 0.0% | 0.0% |
| 2016 | 0.0% | 0.0% | 0.0% |
| 2017 | 0.0% | 0.0% | 0.0% |
| 2018 | 0.0% | 0.0% | 0.0% |
| 2019 | 19.6% | 19.6% | 2.0% |
| 2020 | 21.2% | 21.2% | 2.4% |
| 2021 | 21.2% | 21.2% | 2.0% |
| 2022 | 22.4% | 22.4% | 1.8% |
| 2023 | 23.7% | 23.7% | 1.7% |
| 2024 | 24.5% | 24.5% | 1.8% |
| 2025 | 23.6% | 23.6% | 2.0% |

## Examples

### cp1252_fallback_years
```
2008
2009
2017
```

### non_taxonomy_coproduct_codes
```
1819(3445)
1607(754)
1876(421)
1647(409)
1807(407)
374(402)
1884(341)
4078(313)
4056(302)
896(213)
```
