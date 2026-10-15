"""D-19 stratified candidate sampler for the blind labeled validation fixture.

BLINDNESS PROTOCOL: this script queries the committed `data/processed` Parquet directly via
DuckDB and NEVER imports `wii.narratives` -- it must not run classify_case, RuleProvider, or
any narrative-classification function. It only selects candidate case_ids/narratives for a human
(here, the AI developer assistant per D-19's disclosure) to label blind, following
`docs/labeling_guide.md`.

Usage: `uv run python scripts/sample_labeling_candidates.py`
Writes `data/interim/labeling_candidates.csv` (untracked -- data/interim is gitignored).
"""

from __future__ import annotations

import csv
from pathlib import Path

import duckdb

from wii.utils.paths import repo_root

SEED = 20260915
DEV_SPLIT_SIZE = 40

BROAD_CODES = (895, 809, 845, 847, 871, 899, 862, 870)

# (stratum_name, min_count, WHERE-fragment over fc/bcp/dt aliases, sample_multiplier)
STRATA: list[tuple[str, int, str]] = [
    ("broad_code", 60, "bcp.product_code IN (895, 809, 845, 847, 871, 899, 862, 870)"),
    *[(f"broad_code_{code}", 5, f"bcp.product_code = {code}") for code in BROAD_CODES],
    ("chop_saw", 12, "fc.narrative ILIKE '%CHOP SAW%'"),
    ("kickback", 12, "(fc.narrative ILIKE '%KICKBACK%' OR fc.narrative ILIKE '%KICKED BACK%')"),
    ("negation_denies", 2, "fc.narrative ILIKE '%DENIES%'"),
    (
        "negation_general",
        8,
        "(fc.narrative ILIKE '%DENIES%' OR fc.narrative ILIKE '% NO %' "
        "OR fc.narrative ILIKE '%W/O%')",
    ),
    ("negation_imaging", 4, "fc.narrative ILIKE '%NEGATIVE%'"),
    ("bare_saw_845", 8, "(bcp.product_code = 845 AND fc.narrative ILIKE '%SAW%')"),
    (
        "ppe_guard",
        12,
        "(fc.narrative ILIKE '%GLASSES%' OR fc.narrative ILIKE '%GOGGLES%' "
        "OR fc.narrative ILIKE '%GLOVES%' OR fc.narrative ILIKE '%GUARD%' "
        "OR fc.narrative ILIKE '%PROTECTION%')",
    ),
    (
        "maintenance",
        8,
        "(fc.narrative ILIKE '%CHANGING%' OR fc.narrative ILIKE '%CLEANING%' "
        "OR fc.narrative ILIKE '%ADJUSTING%' OR fc.narrative ILIKE '%BLADE CHANGE%')",
    ),
    ("nail_gun", 8, "(fc.narrative ILIKE '%NAIL GUN%' OR fc.narrative ILIKE '%NAILER%')"),
    (
        "entanglement",
        4,
        "(fc.narrative ILIKE '%HAIR CAUGHT%' OR fc.narrative ILIKE '%GLOVE CAUGHT%' "
        "OR fc.narrative ILIKE '%SLEEVE CAUGHT%')",
    ),
    ("fall_trip", 5, "(fc.narrative ILIKE '%FELL%' OR fc.narrative ILIKE '%TRIPPED%')"),
]

FAMILY_MIN = 10

_BASE_QUERY = """
    SELECT
        fc.case_id, fc.year, bcp.product_code AS official_product_code,
        dt.tool_id AS official_tool_id, dt.family_id AS official_family_id, fc.narrative,
        hash(fc.case_id || '|{seed}') AS sample_key
    FROM fact_cases fc
    JOIN bridge_case_products bcp USING (case_id)
    JOIN dim_tool dt ON dt.tool_id = bcp.tool_id
    WHERE bcp.in_scope AND fc.narrative IS NOT NULL AND fc.narrative != ''
      AND ({where})
    QUALIFY ROW_NUMBER() OVER (PARTITION BY fc.case_id ORDER BY bcp.product_code) = 1
    ORDER BY sample_key
    LIMIT {limit}
"""


def _connect(processed_dir: Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    for path in sorted(processed_dir.glob("*.parquet")):
        escaped = str(path).replace("'", "''")
        con.execute(f"CREATE VIEW \"{path.stem}\" AS SELECT * FROM read_parquet('{escaped}')")
    return con


def sample_candidates(con: duckdb.DuckDBPyConnection) -> list[dict]:
    rows_by_case: dict[int, dict] = {}
    tags_by_case: dict[int, set[str]] = {}

    for name, min_count, where in STRATA:
        # A small buffer over the minimum absorbs dedup overlap with other strata without
        # blowing up the total candidate-pool size (this pool is hand-labeled, D-19).
        limit = min_count + 3
        query = _BASE_QUERY.format(seed=SEED, where=where, limit=limit)
        for row in con.execute(query).fetchall():
            case_id = row[0]
            tags_by_case.setdefault(case_id, set()).add(name)
            if case_id not in rows_by_case:
                rows_by_case[case_id] = {
                    "case_id": row[0],
                    "year": row[1],
                    "official_product_code": row[2],
                    "official_tool_id": row[3],
                    "official_family_id": row[4],
                    "narrative": row[5],
                }

    # Family minimums: top up any family under FAMILY_MIN with an additional seeded sample.
    family_counts: dict[str, int] = {}
    for r in rows_by_case.values():
        family_counts[r["official_family_id"]] = family_counts.get(r["official_family_id"], 0) + 1

    families = con.execute("SELECT DISTINCT family_id FROM dim_tool").fetchall()
    for (family_id,) in families:
        have = family_counts.get(family_id, 0)
        if have >= FAMILY_MIN:
            continue
        where = f"dt.family_id = '{family_id}'"
        query = _BASE_QUERY.format(seed=SEED, where=where, limit=(FAMILY_MIN - have) + 3)
        for row in con.execute(query).fetchall():
            case_id = row[0]
            tags_by_case.setdefault(case_id, set()).add(f"family_{family_id}")
            if case_id not in rows_by_case:
                rows_by_case[case_id] = {
                    "case_id": row[0],
                    "year": row[1],
                    "official_product_code": row[2],
                    "official_tool_id": row[3],
                    "official_family_id": row[4],
                    "narrative": row[5],
                }
                family_counts[family_id] = family_counts.get(family_id, 0) + 1

    out = []
    for case_id, row in rows_by_case.items():
        row = dict(row)
        row["stratum_tags"] = ";".join(sorted(tags_by_case.get(case_id, set())))
        out.append(row)
    return out


def assign_splits(rows: list[dict]) -> list[dict]:
    # Deterministic seeded shuffle via DuckDB's hash() on case_id -- reused so the split is
    # reproducible without a separate RNG dependency.
    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE t (case_id VARCHAR)")
    con.executemany("INSERT INTO t VALUES (?)", [(r["case_id"],) for r in rows])
    order = con.execute(
        f"SELECT case_id FROM t ORDER BY hash(case_id || '|split|{SEED}')"
    ).fetchall()
    dev_ids = {r[0] for r in order[:DEV_SPLIT_SIZE]}
    for row in rows:
        row["split"] = "dev" if row["case_id"] in dev_ids else "holdout"
    return rows


def main() -> None:
    root = repo_root()
    processed_dir = root / "data" / "processed"
    con = _connect(processed_dir)
    rows = sample_candidates(con)
    rows = assign_splits(rows)
    rows.sort(key=lambda r: r["case_id"])

    out_dir = root / "data" / "interim"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "labeling_candidates.csv"
    fieldnames = [
        "case_id",
        "year",
        "official_product_code",
        "official_tool_id",
        "official_family_id",
        "narrative",
        "stratum_tags",
        "split",
    ]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} candidates to {out_path}")
    holdout_n = sum(1 for r in rows if r["split"] == "holdout")
    print(f"dev={len(rows) - holdout_n} holdout={holdout_n}")


if __name__ == "__main__":
    main()
