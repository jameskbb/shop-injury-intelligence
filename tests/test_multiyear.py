"""Multi-year runs: full-file design_psu, sha256-keyed caching, and weight/date/dedup QA gates."""

from __future__ import annotations

import json

import pytest

from tests.conftest import write_neiss_tsv
from wii.data.pipeline import CACHE_VERSION
from wii.data.sync import main
from wii.utils.years import YearSpecError, parse_years_arg


def _write_year(raw_dir, year, records):
    write_neiss_tsv(raw_dir / f"neiss{year}.tsv", records)


def test_design_psu_includes_zero_workshop_psu(tmp_path, neiss_record):
    """2024 includes a PSU whose only records are non-workshop; design_psu still gets a row."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    data_root = tmp_path / "data_root"

    records_2023 = [
        neiss_record(Product_1="841", Stratum="S", PSU="1", Treatment_Date="01/15/2023")
    ]
    records_2024 = [
        neiss_record(Product_1="841", Stratum="S", PSU="1"),
        neiss_record(Product_1="1267", Stratum="M", PSU="99"),  # non-workshop-only PSU
        neiss_record(Product_1="1267", Stratum="M", PSU="99"),
    ]
    _write_year(raw_dir, 2023, records_2023)
    _write_year(raw_dir, 2024, records_2024)

    exit_code = main(
        [
            "--years",
            "2023:2024",
            "--offline",
            "--raw-dir",
            str(raw_dir),
            "--data-root",
            str(data_root),
        ]
    )
    assert exit_code == 0

    import polars as pl

    design_psu = pl.read_parquet(data_root / "processed" / "design_psu.parquet")
    row = design_psu.filter(
        (pl.col("year") == 2024) & (pl.col("stratum") == "M") & (pl.col("psu") == 99)
    )
    assert row.height == 1
    assert row["full_file_case_count"][0] == 2


def test_cache_hits_and_byte_identical_rerun(tmp_path, neiss_record):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    data_root = tmp_path / "data_root"

    _write_year(raw_dir, 2023, [neiss_record(Product_1="841", Treatment_Date="01/15/2023")])
    _write_year(raw_dir, 2024, [neiss_record(Product_1="841")])

    exit_code = main(
        [
            "--years",
            "2023:2024",
            "--offline",
            "--raw-dir",
            str(raw_dir),
            "--data-root",
            str(data_root),
        ]
    )
    assert exit_code == 0
    first_bytes = (data_root / "processed" / "fact_cases.parquet").read_bytes()

    from wii.data.pipeline import run_pipeline
    from wii.utils.paths import DataPaths

    paths = DataPaths.from_root(data_root, raw_dir=raw_dir)
    result = run_pipeline({2023: raw_dir / "neiss2023.tsv", 2024: raw_dir / "neiss2024.tsv"}, paths)
    assert result.cache_hits == [2023, 2024]
    second_bytes = (data_root / "processed" / "fact_cases.parquet").read_bytes()
    assert first_bytes == second_bytes

    # Touching the bytes of neiss2024.tsv invalidates only 2024's cache entry.
    year_2024_path = raw_dir / "neiss2024.tsv"
    year_2024_path.write_bytes(year_2024_path.read_bytes() + b"\r\n")
    result2 = run_pipeline({2023: raw_dir / "neiss2023.tsv", 2024: year_2024_path}, paths)
    assert result2.cache_hits == [2023]


def test_weight_zero_or_blank_aborts(tmp_path, neiss_record):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    data_root = tmp_path / "data_root"
    _write_year(raw_dir, 2024, [neiss_record(Weight="0"), neiss_record(Weight="")])

    exit_code = main(
        ["--years", "2024", "--offline", "--raw-dir", str(raw_dir), "--data-root", str(data_root)]
    )
    assert exit_code == 1
    report = json.loads((data_root / "metadata" / "qa_report.json").read_text())
    check = next(r for r in report["results"] if r["check_id"] == "weights_positive")
    assert check["passed"] is False
    assert check["count"] == 2


def test_treatment_date_out_of_file_year_aborts(tmp_path, neiss_record):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    data_root = tmp_path / "data_root"
    _write_year(raw_dir, 2023, [neiss_record(Treatment_Date="12/31/2022")])

    exit_code = main(
        ["--years", "2023", "--offline", "--raw-dir", str(raw_dir), "--data-root", str(data_root)]
    )
    assert exit_code == 1
    report = json.loads((data_root / "metadata" / "qa_report.json").read_text())
    check = next(r for r in report["results"] if r["check_id"] == "treatment_dates_in_file_year")
    assert check["passed"] is False
    assert check["count"] == 1


def test_duplicate_case_id_across_years_aborts(tmp_path, neiss_record):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    data_root = tmp_path / "data_root"
    shared_id = "999999999"
    _write_year(
        raw_dir,
        2023,
        [
            neiss_record(
                **{
                    "CPSC_Case_Number": shared_id,
                    "Product_1": "841",
                    "Treatment_Date": "01/15/2023",
                }
            )
        ],
    )
    _write_year(
        raw_dir, 2024, [neiss_record(**{"CPSC_Case_Number": shared_id, "Product_1": "841"})]
    )

    exit_code = main(
        [
            "--years",
            "2023:2024",
            "--offline",
            "--raw-dir",
            str(raw_dir),
            "--data-root",
            str(data_root),
        ]
    )
    assert exit_code == 1
    report = json.loads((data_root / "metadata" / "qa_report.json").read_text())
    check = next(r for r in report["results"] if r["check_id"] == "unique_case_ids_global")
    assert check["passed"] is False
    assert check["count"] == 1


def test_keyboard_interrupt_leaves_prior_report_untouched(tmp_path, neiss_record, monkeypatch):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    data_root = tmp_path / "data_root"
    _write_year(raw_dir, 2024, [neiss_record(Product_1="841")])

    exit_code = main(
        ["--years", "2024", "--offline", "--raw-dir", str(raw_dir), "--data-root", str(data_root)]
    )
    assert exit_code == 0
    report_path = data_root / "metadata" / "qa_report.json"
    before = report_path.read_bytes()

    import wii.data.quality as quality_mod

    def _raise_keyboard_interrupt(ctx):
        raise KeyboardInterrupt

    monkeypatch.setattr(quality_mod, "STRUCTURAL_CHECKS", [_raise_keyboard_interrupt])

    from wii.data.pipeline import run_pipeline
    from wii.utils.paths import DataPaths

    paths = DataPaths.from_root(data_root, raw_dir=raw_dir)
    with pytest.raises(KeyboardInterrupt):
        run_pipeline({2024: raw_dir / "neiss2024.tsv"}, paths)

    after = report_path.read_bytes()
    assert before == after


def test_value_error_check_recorded_and_report_written(tmp_path, neiss_record, monkeypatch):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    data_root = tmp_path / "data_root"
    _write_year(raw_dir, 2024, [neiss_record(Product_1="841")])

    import wii.data.quality as quality_mod

    def _raise_value_error(ctx):
        raise ValueError("boom")

    monkeypatch.setattr(
        quality_mod,
        "STRUCTURAL_CHECKS",
        [*quality_mod.STRUCTURAL_CHECKS, _raise_value_error],
    )

    from wii.data.pipeline import run_pipeline
    from wii.utils.paths import DataPaths

    paths = DataPaths.from_root(data_root, raw_dir=raw_dir)
    result = run_pipeline({2024: raw_dir / "neiss2024.tsv"}, paths)
    assert result.report.aborted is True
    failing = next(r for r in result.report.results if not r.passed and "boom" in r.detail)
    assert "ValueError" in failing.detail
    assert (data_root / "metadata" / "qa_report.json").exists()


@pytest.mark.parametrize(
    "spec,expected",
    [
        ("2019:2025", list(range(2019, 2026))),
        ("2019,2021", [2019, 2021]),
    ],
)
def test_parse_years_arg_valid(spec, expected):
    import datetime

    result = parse_years_arg(spec, today=datetime.date(2026, 9, 14))
    assert result == expected


@pytest.mark.parametrize("spec", ["2025:2019", "20x9", "../etc", "2019:"])
def test_parse_years_arg_invalid_raises(spec):
    import datetime

    with pytest.raises(YearSpecError):
        parse_years_arg(spec, today=datetime.date(2026, 9, 14))


def test_parse_years_arg_future_year_raises():
    import datetime

    with pytest.raises(YearSpecError):
        parse_years_arg("2030", today=datetime.date(2026, 9, 14))


@pytest.mark.parametrize("years_arg", ["2025:2019", "20x9", "../etc", "2019:", "2030"])
def test_main_returns_usage_for_bad_years_spec(tmp_path, years_arg):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    data_root = tmp_path / "data_root"
    exit_code = main(
        [
            "--years",
            years_arg,
            "--offline",
            "--raw-dir",
            str(raw_dir),
            "--data-root",
            str(data_root),
        ]
    )
    assert exit_code == 2


def test_data_metadata_has_required_keys(tmp_path, neiss_record):
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    data_root = tmp_path / "data_root"
    # 2018 (pre-2019) must have a 0% Body_Part_2 fill rate; 2019 uses the fixture's post-2019
    # default fill (D-16/secondary_fill_rate_by_era).
    _write_year(
        raw_dir,
        2018,
        [neiss_record(Product_1="841", Treatment_Date="01/15/2018", Body_Part_2="")],
    )
    _write_year(raw_dir, 2019, [neiss_record(Product_1="841", Treatment_Date="01/15/2019")])

    exit_code = main(
        [
            "--years",
            "2018:2019",
            "--offline",
            "--raw-dir",
            str(raw_dir),
            "--data-root",
            str(data_root),
        ]
    )
    assert exit_code == 0

    metadata = json.loads((data_root / "metadata" / "data_metadata.json").read_text())
    assert "years" in metadata
    assert "taxonomy_version" in metadata
    assert "config_sha256" in metadata
    assert "product_taxonomy.yaml" in metadata["config_sha256"]
    assert metadata["cache_version"] == CACHE_VERSION
    assert "count_semantics" in metadata
    assert set(metadata["years"].keys()) == {"2018", "2019"}
