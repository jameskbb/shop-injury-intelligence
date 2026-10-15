"""D-08 CaseFilter: a single dataclass compiling to one parameterized SQL WHERE clause.

Values are never interpolated into SQL text -- every predicate uses `?` placeholders, so a
filter value containing SQL metacharacters (e.g. `"x' OR 1=1 --"`) returns zero rows rather than
corrupting the query or raising a syntax error (T-02-01). This is the same object Phase 3's app
will use to drive its own queries (D-08).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AgeScope = Literal["adults", "all"]
Basis = Literal["any", "primary"]


@dataclass(frozen=True)
class CaseFilter:
    years: tuple[int, int] | None = None
    age_scope: AgeScope = "adults"
    sexes: tuple[int, ...] = ()
    product_codes: tuple[int, ...] = ()
    families: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    body_regions: tuple[str, ...] = ()
    body_basis: Basis = "any"
    diagnosis_codes: tuple[int, ...] = ()
    diagnosis_categories: tuple[str, ...] = ()
    diagnosis_basis: Basis = "any"
    dispositions: tuple[int, ...] = ()
    mechanisms: tuple[str, ...] = ()
    locations: tuple[int, ...] = ()
    age_brackets: tuple[str, ...] = ()
    tool_subtypes: tuple[str, ...] = ()
    maintenance_or_setup: bool | None = None

    def to_sql(self, alias: str = "fc") -> tuple[str, list]:
        clauses: list[str] = []
        params: list = []

        if self.years is not None:
            clauses.append(f"{alias}.year BETWEEN ? AND ?")
            params.extend([self.years[0], self.years[1]])

        if self.age_scope == "adults":
            clauses.append(f"{alias}.is_adult = TRUE")
        elif self.age_scope != "all":
            raise ValueError(f"unsupported age_scope={self.age_scope!r}")

        if self.sexes:
            placeholders = ",".join("?" for _ in self.sexes)
            clauses.append(f"{alias}.sex_code IN ({placeholders})")
            params.extend(self.sexes)

        if self.dispositions:
            placeholders = ",".join("?" for _ in self.dispositions)
            clauses.append(f"{alias}.disposition_code IN ({placeholders})")
            params.extend(self.dispositions)

        if self.product_codes:
            placeholders = ",".join("?" for _ in self.product_codes)
            clauses.append(
                f"EXISTS (SELECT 1 FROM bridge_case_products bcp_pc "
                f"WHERE bcp_pc.case_id = {alias}.case_id AND bcp_pc.in_scope "
                f"AND bcp_pc.product_code IN ({placeholders}))"
            )
            params.extend(self.product_codes)

        if self.families:
            placeholders = ",".join("?" for _ in self.families)
            clauses.append(
                f"EXISTS (SELECT 1 FROM bridge_case_products bcp_fam "
                f"WHERE bcp_fam.case_id = {alias}.case_id AND bcp_fam.in_scope "
                f"AND bcp_fam.family_id IN ({placeholders}))"
            )
            params.extend(self.families)

        if self.tools:
            placeholders = ",".join("?" for _ in self.tools)
            clauses.append(
                f"EXISTS (SELECT 1 FROM bridge_case_products bcp_tool "
                f"WHERE bcp_tool.case_id = {alias}.case_id AND bcp_tool.in_scope "
                f"AND bcp_tool.final_tool_id IN ({placeholders}))"
            )
            params.extend(self.tools)

        if self.body_regions:
            sql, p = self._body_region_clause(alias)
            clauses.append(sql)
            params.extend(p)

        if self.diagnosis_codes or self.diagnosis_categories:
            sql, p = self._diagnosis_clause(alias)
            clauses.append(sql)
            params.extend(p)

        if self.mechanisms:
            placeholders = ",".join("?" for _ in self.mechanisms)
            clauses.append(
                f"EXISTS (SELECT 1 FROM narrative_enrichment ne "
                f"WHERE ne.case_id = {alias}.case_id AND ne.primary_mechanism IN ({placeholders}))"
            )
            params.extend(self.mechanisms)

        if self.locations:
            placeholders = ",".join("?" for _ in self.locations)
            clauses.append(f"{alias}.location_code IN ({placeholders})")
            params.extend(self.locations)

        if self.age_brackets:
            placeholders = ",".join("?" for _ in self.age_brackets)
            clauses.append(f"{alias}.age_bracket IN ({placeholders})")
            params.extend(self.age_brackets)

        if self.tool_subtypes:
            placeholders = ",".join("?" for _ in self.tool_subtypes)
            clauses.append(
                f"EXISTS (SELECT 1 FROM narrative_enrichment ne_st "
                f"WHERE ne_st.case_id = {alias}.case_id AND ne_st.tool_subtype IN ({placeholders}))"
            )
            params.extend(self.tool_subtypes)

        if self.maintenance_or_setup is not None:
            clauses.append(
                f"EXISTS (SELECT 1 FROM narrative_enrichment ne_ms "
                f"WHERE ne_ms.case_id = {alias}.case_id "
                f"AND ne_ms.maintenance_or_setup = ?)"
            )
            params.append(self.maintenance_or_setup)

        if not clauses:
            return "1=1", params
        return " AND ".join(clauses), params

    def _body_region_clause(self, alias: str) -> tuple[str, list]:
        placeholders = ",".join("?" for _ in self.body_regions)
        # Body regions/rollups are looked up in SQL from dim_body_part / dim_body_rollup, never
        # hardcoded here (D-10).
        if self.body_basis == "any":
            sql = (
                f"EXISTS (SELECT 1 FROM bridge_case_body_parts bbp "
                f"LEFT JOIN dim_body_part dbp ON dbp.code = bbp.body_part_code "
                f"LEFT JOIN dim_body_rollup dbr ON dbr.body_part_code = bbp.body_part_code "
                f"WHERE bbp.case_id = {alias}.case_id "
                f"AND (dbp.region_id IN ({placeholders}) OR dbr.rollup_id IN ({placeholders})))"
            )
        elif self.body_basis == "primary":
            sql = (
                f"EXISTS (SELECT 1 FROM dim_body_part dbp "
                f"LEFT JOIN dim_body_rollup dbr ON dbr.body_part_code = dbp.code "
                f"WHERE dbp.code = {alias}.primary_body_part_code "
                f"AND (dbp.region_id IN ({placeholders}) OR dbr.rollup_id IN ({placeholders})))"
            )
        else:
            raise ValueError(f"unsupported body_basis={self.body_basis!r}")
        return sql, [*self.body_regions, *self.body_regions]

    def _diagnosis_clause(self, alias: str) -> tuple[str, list]:
        table_alias = "dd_any" if self.diagnosis_basis == "any" else "dd_pri"
        parts: list[str] = []
        params: list = []
        if self.diagnosis_codes:
            ph = ",".join("?" for _ in self.diagnosis_codes)
            parts.append(f"{table_alias}.code IN ({ph})")
            params.extend(self.diagnosis_codes)
        if self.diagnosis_categories:
            ph = ",".join("?" for _ in self.diagnosis_categories)
            parts.append(f"{table_alias}.injury_category IN ({ph})")
            params.extend(self.diagnosis_categories)
        condition = " OR ".join(parts)

        if self.diagnosis_basis == "any":
            sql = (
                f"EXISTS (SELECT 1 FROM bridge_case_diagnoses bcd "
                f"JOIN dim_diagnosis {table_alias} ON {table_alias}.code = bcd.diagnosis_code "
                f"WHERE bcd.case_id = {alias}.case_id AND ({condition}))"
            )
        elif self.diagnosis_basis == "primary":
            sql = (
                f"EXISTS (SELECT 1 FROM dim_diagnosis {table_alias} "
                f"WHERE {table_alias}.code = {alias}.primary_diagnosis_code AND ({condition}))"
            )
        else:
            raise ValueError(f"unsupported diagnosis_basis={self.diagnosis_basis!r}")
        return sql, params
