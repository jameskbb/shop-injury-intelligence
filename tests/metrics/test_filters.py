"""D-07/D-08 filter tests: parameterization safety, pooling, annual-average, and each new
CaseFilter predicate narrows the domain as expected."""

from __future__ import annotations

import pytest

from wii.metrics import CaseFilter, estimate_by, estimate_total


def test_age_scope_adults_excludes_non_adult_cases(con):
    total_all = estimate_total(con, CaseFilter(years=(2024, 2024), age_scope="all"))
    total_adults = estimate_total(con, CaseFilter(years=(2024, 2024), age_scope="adults"))
    assert total_adults.raw_n < total_all.raw_n  # fixture has one non-adult-eligible? see below
    assert total_all.raw_n == 8


def test_age_scope_default_is_adults():
    assert CaseFilter().age_scope == "adults"


def test_unsupported_age_scope_raises(con):
    flt = CaseFilter(age_scope="teens")
    with pytest.raises(ValueError):
        flt.to_sql()


def test_multi_year_pooling_sums_across_years(con):
    """CaseFilter(years=(2024,2024)) applied twice via two separate estimate_total calls over a
    fixture that only has 2024 data -- the pooled key math is exercised more directly by the
    realdata multi-year test; here we assert the single-year path returns n_years=1."""
    record = estimate_total(con, CaseFilter(years=(2024, 2024), age_scope="all"))
    assert record.n_years == 1
    assert record.period_total == pytest.approx(record.annual_average)


def test_product_codes_filter_narrows_to_named_codes(con):
    flt = CaseFilter(years=(2024, 2024), age_scope="all", product_codes=(895,))
    record = estimate_total(con, flt)
    assert record.raw_n == 1  # only c8
    assert record.estimate == pytest.approx(12.0)


def test_tools_filter_narrows_to_named_tools(con):
    flt = CaseFilter(years=(2024, 2024), age_scope="all", tools=("circular_saw",))
    record = estimate_total(con, flt)
    assert record.raw_n == 2  # c4, c8
    assert record.estimate == pytest.approx(17.0)


def test_families_filter_matches_the_shared_synthetic_family(con):
    flt = CaseFilter(years=(2024, 2024), age_scope="all", families=("saws",))
    record = estimate_total(con, flt)
    assert record.raw_n == 8  # every synthetic case is a "saws" family tool


@pytest.mark.parametrize(
    "field,value",
    [
        ("tools", ("x' OR 1=1 --",)),
        ("product_codes", (999999,)),
        ("families", ("'; DROP TABLE fact_cases; --",)),
    ],
)
def test_injection_shaped_filter_values_return_zero_rows_not_a_sql_error(con, field, value):
    """Every predicate is bound via `?` -- a metacharacter-laden value matches nothing and never
    raises a syntax error (T-02-01)."""
    flt = CaseFilter(years=(2024, 2024), age_scope="all", **{field: value})
    record = estimate_total(con, flt)
    assert record.raw_n == 0
    assert record.estimate == pytest.approx(0.0)


def test_estimate_by_family(con):
    result = estimate_by(con, CaseFilter(years=(2024, 2024), age_scope="all"), by="family")
    row = result.filter(result["group_id"] == "saws").row(0, named=True)
    assert row["raw_n"] == 8


def test_estimate_by_year(con):
    result = estimate_by(con, CaseFilter(age_scope="all"), by="year")
    row = result.filter(result["group_id"] == 2024).row(0, named=True)
    assert row["raw_n"] == 8


def test_maintenance_or_setup_true_adds_exists_clause_with_bool_param():
    """D-08/D-14: maintenance_or_setup=True compiles to a `?`-parameterized EXISTS over
    narrative_enrichment.maintenance_or_setup."""
    flt = CaseFilter(maintenance_or_setup=True)
    sql, params = flt.to_sql()
    assert "EXISTS" in sql
    assert "maintenance_or_setup = ?" in sql
    assert "narrative_enrichment" in sql
    assert True in params


def test_maintenance_or_setup_false_adds_clause_with_false_param():
    flt = CaseFilter(maintenance_or_setup=False)
    sql, params = flt.to_sql()
    assert "maintenance_or_setup = ?" in sql
    assert False in params


def test_maintenance_or_setup_none_adds_no_clause():
    flt = CaseFilter()
    sql, _params = flt.to_sql()
    assert "maintenance_or_setup" not in sql


# D-07/Pitfall 5 (05-01): locations/age_brackets/tool_subtypes predicates.


def test_locations_filter_compiles_to_in_clause_with_params():
    sql, params = CaseFilter(locations=(1,)).to_sql("fc")
    assert "fc.location_code IN (?)" in sql
    assert params == [1]


def test_age_brackets_filter_compiles_to_in_clause():
    sql, params = CaseFilter(age_brackets=("age_25_34", "age_35_44")).to_sql("fc")
    assert "fc.age_bracket IN (?,?)" in sql
    assert params == ["age_25_34", "age_35_44"]


def test_tool_subtypes_filter_compiles_to_exists_clause_with_own_alias():
    sql, params = CaseFilter(tool_subtypes=("x",)).to_sql("fc")
    assert "EXISTS" in sql
    assert "narrative_enrichment" in sql
    assert "tool_subtype IN (?)" in sql
    assert params == ["x"]


def test_age_bracket_injection_shaped_value_returns_zero_rows(con):
    flt = CaseFilter(years=(2024, 2024), age_scope="all", age_brackets=("x' OR 1=1 --",))
    record = estimate_total(con, flt)
    assert record.raw_n == 0


def test_new_fields_empty_produce_identical_sql_to_baseline():
    baseline_sql, baseline_params = CaseFilter().to_sql()
    extended_sql, extended_params = CaseFilter(
        locations=(), age_brackets=(), tool_subtypes=()
    ).to_sql()
    assert extended_sql == baseline_sql
    assert extended_params == baseline_params
