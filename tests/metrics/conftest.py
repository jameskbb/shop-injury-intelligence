"""Synthetic fixture for wii.metrics: a tiny 2-strata x 3-PSU design with 8 cases.

Hand-computed variance for the product_code=841 ("table_saw") group, all-ages, single year 2024
(comments below double as the derivation so the numbers in test_estimator.py are not "magic"):

design: stratum A = PSUs {1, 2, 3}, stratum B = PSUs {1, 2, 3}. PSU A3 and B3 carry zero domain
cases for the 841 group (verifies COALESCE-to-0 zero-domain-PSU handling).

Cases (case_id, stratum, psu, weight, product_code(s)):
  c1  A 1 10   841
  c2  A 1 20   841
  c3  A 2 15   841
  c4  A 2  5   832            (different product -- excluded from the 841 group)
  c5  B 1 30   841
  c6  B 1 25   841
  c7  B 2 40   841 (TWICE: bridge seq 1 and seq 2, same code -- D-07 dedup test)
  c8  B 3 12   895 AND 832    (two different in-scope tools -- D-07 multi-membership test)

841-group PSU totals (y_hp): A1=10+20=30, A2=15, A3=0, B1=30+25=55, B2=40 (c7 deduped, not 80),
B3=0.
Stratum A: n_h=3, y=[30,15,0], mean=15, sum((y-mean)^2)=225+0+225=450, v_h=3/2*450=675
Stratum B: n_h=3, y=[55,40,0], mean=31.66667, sum((y-mean)^2)=544.4444+69.4444+1002.7778=1616.6667,
  v_h=3/2*1616.6667=2425.0
estimate = (30+15+0) + (55+40+0) = 45 + 95 = 140
raw_n = 6 (c1,c2,c3,c5,c6,c7 -- the c7 duplicate slot counts once)
variance = 675 + 2425 = 3100.0 -> se = sqrt(3100) = 55.67764...
cv = se / estimate = 55.67764 / 140 = 0.39770... (> 0.33)
total_psus = 6, n_strata = 2, df = 4

tool='table_saw' group is identical to product_code=841 (every 841 case maps to tool table_saw
and no other product maps to it).
tool='other_power_saw' group: only c8 (weight 12) -> raw_n=1, estimate=12.
tool='circular_saw' group: c4 (weight 5) + c8 (weight 12) -> raw_n=2, estimate=17.

estimate_total (distinct case_id over ALL 8 cases, no product filter):
  A1=30, A2=20 (15+5), A3=0, B1=55, B2=40, B3=12
  Stratum A: n_h=3, y=[30,20,0], sum=50
  Stratum B: n_h=3, y=[55,40,12], sum=107
  estimate = 50 + 107 = 157, raw_n = 8 (every case counted exactly once, including c7 and c8)
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from wii.metrics.db import connect


@pytest.fixture
def synthetic_processed_dir(tmp_path) -> Path:
    d = tmp_path / "processed"
    d.mkdir()

    design_psu = pl.DataFrame(
        {
            "year": [2024] * 6,
            "stratum": ["A", "A", "A", "B", "B", "B"],
            "psu": [1, 2, 3, 1, 2, 3],
            "full_file_case_count": [2, 2, 0, 2, 1, 1],
            "full_file_weight_sum": [30.0, 20.0, 0.0, 55.0, 40.0, 12.0],
        }
    )
    design_psu.write_parquet(d / "design_psu.parquet")

    fact_cases = pl.DataFrame(
        {
            "case_id": ["c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8"],
            "year": [2024] * 8,
            # c4 is the one non-adult case (used by test_filters.py's age_scope test). It carries
            # product 832 (not 841), so every all-ages hand-computed value above is unaffected.
            "is_adult": [True, True, True, False, True, True, True, True],
            "stratum": ["A", "A", "A", "A", "B", "B", "B", "B"],
            "psu": [1, 1, 2, 2, 1, 1, 2, 3],
            "weight": [10.0, 20.0, 15.0, 5.0, 30.0, 25.0, 40.0, 12.0],
            # age_bracket/location_code (05-01 Task 2): plain placeholder values -- the
            # CaseFilter.age_brackets/locations predicates are simple `IN` clauses on these raw
            # columns, no dim-table join, so any distinct values exercise them correctly.
            "age_bracket": [
                "age_25_34",
                "age_35_44",
                "age_25_34",
                "age_0_17",
                "age_45_54",
                "age_25_34",
                "age_35_44",
                "age_25_34",
            ],
            "location_code": [1, 1, 2, 2, 1, 3, 1, 2],
        }
    )
    fact_cases.write_parquet(d / "fact_cases.parquet")

    bridge_case_products = pl.DataFrame(
        {
            "case_id": ["c1", "c2", "c3", "c4", "c5", "c6", "c7", "c7", "c8", "c8"],
            "product_code": [841, 841, 841, 832, 841, 841, 841, 841, 895, 832],
            "seq": [1, 1, 1, 1, 1, 1, 1, 2, 1, 2],
            "is_primary": [True, True, True, True, True, True, True, False, True, False],
            "tool_id": [
                "table_saw",
                "table_saw",
                "table_saw",
                "circular_saw",
                "table_saw",
                "table_saw",
                "table_saw",
                "table_saw",
                "other_power_saw",
                "circular_saw",
            ],
            "family_id": ["saws"] * 10,
            "in_scope": [True] * 10,
            "is_adjacent": [False] * 10,
            "narrative_refined_tool": [None] * 10,
            "final_tool_id": [
                "table_saw",
                "table_saw",
                "table_saw",
                "circular_saw",
                "table_saw",
                "table_saw",
                "table_saw",
                "table_saw",
                "other_power_saw",
                "circular_saw",
            ],
            "classification_confidence": [None] * 10,
        },
        schema_overrides={"classification_confidence": pl.Float32},
    )
    bridge_case_products.write_parquet(d / "bridge_case_products.parquet")

    dim_tool = pl.DataFrame(
        {
            "tool_id": ["table_saw", "circular_saw", "other_power_saw"],
            "label": ["Table Saw", "Circular Saw", "Other Power Saw"],
            "family_id": ["saws", "saws", "saws"],
            "family_sort_order": [1, 1, 1],
        }
    )
    dim_tool.write_parquet(d / "dim_tool.parquet")

    dim_product = pl.DataFrame(
        {
            "product_code": [841, 832, 895],
            "code_label": ["TABLE SAWS", "CIRCULAR SAWS", "OTHER POWER SAWS"],
            "official_name": ["BENCH OR TABLE SAWS", "CIRCULAR SAWS", "OTHER POWER SAWS"],
            "tool_id": ["table_saw", "circular_saw", "other_power_saw"],
            "family_id": ["saws", "saws", "saws"],
            "start_year": [2000, 2000, 2000],
            "in_scope": [True, True, True],
            "is_adjacent": [False, False, False],
            "broad": [False, False, False],
            "validated_against": ["", "", ""],
        }
    )
    dim_product.write_parquet(d / "dim_product.parquet")

    return d


@pytest.fixture
def con(synthetic_processed_dir):
    return connect(synthetic_processed_dir)


@pytest.fixture
def pair_processed_dir(synthetic_processed_dir) -> Path:
    """Extends synthetic_processed_dir with narrative_enrichment.parquet (D-11 pair-estimator
    fixture). `synthetic_processed_dir` is left byte-identical -- this fixture only ADDS a new
    table to the same directory; Task 2 (see the fact_cases/dim_body_part/dim_diagnosis/
    dim_disposition additions immediately below) extends it further with primary-basis columns.

    primary_mechanism by case: c1/c3/c5/c7 direct_cutting_contact, c2/c6 kickback_ejection,
    c4 unknown, c8 flying_debris_fb. maintenance_or_setup: True for c1 and c2 (04-08 extends c2
    to a maintenance/setup case too, so the maintenance_context breakdown has 2 populated
    categories to distinguish from each other), False elsewhere. maintenance_context: c1
    blade_change, c2 cleaning, c3-c8 not_maintenance (invariant maintenance_or_setup ==
    (maintenance_context != 'not_maintenance') holds for every case, matching the real classifier,
    D-14). power_state_mentioned: c1 mentioned_unplugged, all others not_mentioned.

    Hand-computed tool x mechanism pair cells (see test_estimate_by_pair.py for the assertions):

    (table_saw, direct_cutting_contact): c1(A1,10) c3(A2,15) c5(B1,30) c7(B2,40)
      PSU totals A=[10,15,0], B=[30,40,0]
      stratum A: mean=8.3333, sum((y-mean)^2)=2.7778+44.4444+69.4444=116.6667,
        v_A=1.5*116.6667=175.0
      stratum B: mean=23.3333, sum((y-mean)^2)=44.4444+277.7778+544.4444=866.6667,
        v_B=1.5*866.6667=1300.0
      estimate=25+70=95.0, raw_n=4, variance=175.0+1300.0=1475.0, se=sqrt(1475.0)=38.40573...,
        df=4

    (table_saw, kickback_ejection): c2(A1,20) c6(B1,25)
      A=[20,0,0] v_A=400.0, B=[25,0,0] v_B=625.0 -> estimate=45.0, raw_n=2, variance=1025.0

    table_saw pair estimates sum to 140.0 == estimate_by(flt, "tool") table_saw row (95.0+45.0).
    (other_power_saw, flying_debris_fb) = 12.0 (c8 only, under age_scope="all").
    (circular_saw, flying_debris_fb) = 12.0 (c8's OTHER in-scope tool -- multi-membership).
    (circular_saw, unknown) = 5.0 (c4 only).

    Task 2 (D-09/D-15 Sankey-stage primary specs) extends `fact_cases` in the SAME directory with
    primary_body_part_code/primary_diagnosis_code/disposition_code, and adds dim_body_part,
    bridge_case_body_parts, dim_diagnosis, bridge_case_diagnoses, dim_disposition. Per-case
    assignments (region/category shown for readability -- the real columns hold the raw codes):
      case  body_part(region)   diagnosis(category)     disposition
      c1    92 finger           59 laceration            1 released
      c2    82 hand             57 fracture               1 released
      c3    92 finger           50 amputation             4 admitted
      c4    77 eye              56 foreign_body           1 released
      c5    92 finger           59 laceration              1 released
      c6    82 hand             59 laceration              2 transferred
      c7    92 finger (+secondary slot 82 hand, seq 2)  50 amputation  4 admitted
      c8    77 eye              56 foreign_body           9 not_recorded

    Full Sankey-stage chain (tool_primary -> mechanism -> body_region_primary ->
    diagnosis_category_primary -> disposition) is exactly bookkeeping over these 8 cases (each
    contributes once per stage), so every stage conserves flow exactly by construction:
      tool_primary: table_saw=140.0 (c1,c2,c3,c5,c6,c7), other_power_saw=12.0 (c8, seq-1 slot
        895), circular_saw=5.0 (c4 only) -> sums to estimate_total=157.0.
      (mechanism, body_region_primary): (direct_cutting_contact,finger)=95.0 [c1,c3,c5,c7],
        (kickback_ejection,hand)=45.0 [c2,c6], (unknown,eye)=5.0 [c4],
        (flying_debris_fb,eye)=12.0 [c8].
      (body_region_primary, diagnosis_category_primary): (finger,laceration)=40.0 [c1,c5],
        (finger,amputation)=55.0 [c3,c7], (hand,fracture)=20.0 [c2], (hand,laceration)=25.0 [c6],
        (eye,foreign_body)=17.0 [c4,c8].
      (diagnosis_category_primary, disposition): (laceration,1)=40.0 [c1,c5], (laceration,2)=25.0
        [c6], (fracture,1)=20.0 [c2], (amputation,4)=55.0 [c3,c7], (foreign_body,1)=5.0 [c4],
        (foreign_body,9)=12.0 [c8].
    """
    d = synthetic_processed_dir
    narrative_enrichment = pl.DataFrame(
        {
            "case_id": ["c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8"],
            "primary_mechanism": [
                "direct_cutting_contact",
                "kickback_ejection",
                "direct_cutting_contact",
                "unknown",
                "direct_cutting_contact",
                "kickback_ejection",
                "direct_cutting_contact",
                "flying_debris_fb",
            ],
            "maintenance_or_setup": [True, True, False, False, False, False, False, False],
            "maintenance_context": [
                "blade_change",
                "cleaning",
                "not_maintenance",
                "not_maintenance",
                "not_maintenance",
                "not_maintenance",
                "not_maintenance",
                "not_maintenance",
            ],
            "power_state_mentioned": [
                "mentioned_unplugged",
                "not_mentioned",
                "not_mentioned",
                "not_mentioned",
                "not_mentioned",
                "not_mentioned",
                "not_mentioned",
                "not_mentioned",
            ],
        }
    )
    narrative_enrichment.write_parquet(d / "narrative_enrichment.parquet")

    # Task 2: rewrite fact_cases (same 8 cases, same design) with the three primary-basis
    # columns the new _BY_SPECS entries read.
    fact_cases = pl.DataFrame(
        {
            "case_id": ["c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8"],
            "year": [2024] * 8,
            "is_adult": [True, True, True, False, True, True, True, True],
            "stratum": ["A", "A", "A", "A", "B", "B", "B", "B"],
            "psu": [1, 1, 2, 2, 1, 1, 2, 3],
            "weight": [10.0, 20.0, 15.0, 5.0, 30.0, 25.0, 40.0, 12.0],
            "primary_body_part_code": [92, 82, 92, 77, 92, 82, 92, 77],
            "primary_diagnosis_code": [59, 57, 50, 56, 59, 59, 50, 56],
            "disposition_code": [1, 1, 4, 1, 1, 2, 4, 9],
        }
    )
    fact_cases.write_parquet(d / "fact_cases.parquet")

    dim_body_part = pl.DataFrame(
        {
            "code": [77, 82, 92],
            "label": ["Eye", "Hand", "Finger"],
            "region_id": ["eye", "hand", "finger"],
        }
    )
    dim_body_part.write_parquet(d / "dim_body_part.parquet")

    bridge_case_body_parts = pl.DataFrame(
        {
            "case_id": ["c1", "c2", "c3", "c4", "c5", "c6", "c7", "c7", "c8"],
            "body_part_code": [92, 82, 92, 77, 92, 82, 92, 82, 77],
            "seq": [1, 1, 1, 1, 1, 1, 1, 2, 1],
            "is_primary": [True, True, True, True, True, True, True, False, True],
        }
    )
    bridge_case_body_parts.write_parquet(d / "bridge_case_body_parts.parquet")

    dim_diagnosis = pl.DataFrame(
        {
            "code": [50, 56, 57, 59],
            "label": ["Amputation", "Foreign Body", "Fracture", "Laceration"],
            "injury_category": ["amputation", "foreign_body", "fracture", "laceration"],
        }
    )
    dim_diagnosis.write_parquet(d / "dim_diagnosis.parquet")

    bridge_case_diagnoses = pl.DataFrame(
        {
            "case_id": ["c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8"],
            "diagnosis_code": [59, 57, 50, 56, 59, 59, 50, 56],
            "seq": [1] * 8,
            "is_primary": [True] * 8,
        }
    )
    bridge_case_diagnoses.write_parquet(d / "bridge_case_diagnoses.parquet")

    dim_disposition = pl.DataFrame(
        {
            "code": [1, 2, 4, 5, 6, 8, 9],
            "label": [
                "Treated and released, or examined and released without treatment",
                "Treated and transferred to another hospital",
                "Treated and admitted for hospitalization",
                "Held for observation",
                "Left without being seen",
                "Fatality",
                "Not recorded",
            ],
            "severity_bucket": [
                "released",
                "escalated",
                "escalated",
                "observation",
                "left_without_treatment",
                "fatality",
                "not_recorded",
            ],
            "escalated_care": [False, True, True, False, False, False, False],
            "sort_order": [1, 2, 3, 4, 5, 6, 7],
        }
    )
    dim_disposition.write_parquet(d / "dim_disposition.parquet")

    return d


@pytest.fixture
def pair_con(pair_processed_dir):
    return connect(pair_processed_dir)
