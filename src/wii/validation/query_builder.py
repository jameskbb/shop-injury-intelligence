"""D-21 (VAL-01): parse the official CPSC Query Builder text tables and reconcile wii.metrics
estimates against them offline, for every in-scope workshop product code and year."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

import duckdb

from wii.metrics import CaseFilter, estimate_by
from wii.taxonomy.loader import in_scope_codes, load_product_taxonomy

# "841 - BENCH OR TABLE SAWS\t33,052\t560\t0.10\t26,402\t39,701" or a "*"-suppressed row.
_ROW_RE = re.compile(
    r"^(?P<code>\d+)\s*-\s*(?P<name>.+?)\t"
    r"(?P<estimate>[\d,]+|\*)\t(?P<n>[\d,]+|\*)\t(?P<cv>[\d.]+|\*)\t"
    r"(?P<ci_low>[\d,]+|\*)\t(?P<ci_high>[\d,]+|\*)\s*$"
)


@dataclass(frozen=True)
class OfficialRow:
    code: int
    name: str
    suppressed: bool
    estimate: int | None
    n: int | None
    cv: float | None
    ci_low: int | None
    ci_high: int | None


def parse_official_table(text: str) -> dict[int, OfficialRow]:
    """Parse a CPSC Query Builder text export (5 preamble lines, tab-separated body) into
    `{product_code: OfficialRow}`. A `"*"` cell -> `suppressed=True`, all numeric fields `None`.
    """

    def _int(v: str) -> int | None:
        return None if v == "*" else int(v.replace(",", ""))

    def _float(v: str) -> float | None:
        return None if v == "*" else float(v)

    rows: dict[int, OfficialRow] = {}
    for line in text.splitlines():
        match = _ROW_RE.match(line)
        if not match:
            continue
        code = int(match.group("code"))
        rows[code] = OfficialRow(
            code=code,
            name=match.group("name").strip(),
            suppressed=match.group("estimate") == "*",
            estimate=_int(match.group("estimate")),
            n=_int(match.group("n")),
            cv=_float(match.group("cv")),
            ci_low=_int(match.group("ci_low")),
            ci_high=_int(match.group("ci_high")),
        )
    return rows


def round_half_up(value: float) -> int:
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def round_half_up_2dp(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class ReconRow:
    code: int
    published: bool  # False -> in our taxonomy but absent from the official table ("not published")
    match: bool
    mismatches: tuple[str, ...]
    official: OfficialRow | None
    ours_estimate: float | None
    ours_n: int | None
    ours_cv: float | None
    ours_ci_low: float | None
    ours_ci_high: float | None
    ours_stable: bool | None


def compare_row(code: int, official: OfficialRow | None, our_row: dict | None) -> ReconRow:
    """Pure comparison: `our_row` is a `wii.metrics.estimate_by` output row (or None if the code
    produced no domain rows this year). Match requires half-up-rounded estimate == official,
    raw_n == official n, half-up 2dp cv == official cv, both CI bounds within +/-1.0, and
    (official suppressed) == (ours not stable).
    """
    ours_estimate = our_row["estimate"] if our_row else 0.0
    ours_n = our_row["raw_n"] if our_row else 0
    ours_cv = our_row["cv"] if our_row else None
    ours_ci_low = our_row["ci_low"] if our_row else None
    ours_ci_high = our_row["ci_high"] if our_row else None
    ours_stable = our_row["stable"] if our_row else False

    if official is None:
        return ReconRow(
            code=code,
            published=False,
            match=True,
            mismatches=(),
            official=None,
            ours_estimate=ours_estimate,
            ours_n=ours_n,
            ours_cv=ours_cv,
            ours_ci_low=ours_ci_low,
            ours_ci_high=ours_ci_high,
            ours_stable=ours_stable,
        )

    mismatches: list[str] = []
    if official.suppressed:
        if ours_stable:
            mismatches.append("suppression: official suppressed, ours stable")
    else:
        our_rounded = round_half_up(ours_estimate)
        if our_rounded != official.estimate:
            mismatches.append(f"estimate: ours={our_rounded} official={official.estimate}")
        if ours_n != official.n:
            mismatches.append(f"raw_n: ours={ours_n} official={official.n}")
        our_cv_2dp = round_half_up_2dp(ours_cv) if ours_cv is not None else None
        if our_cv_2dp != official.cv:
            mismatches.append(f"cv: ours={our_cv_2dp} official={official.cv}")
        if official.ci_low is not None and (
            ours_ci_low is None or abs(ours_ci_low - official.ci_low) > 1.0
        ):
            mismatches.append(f"ci_low: ours={ours_ci_low} official={official.ci_low}")
        if official.ci_high is not None and (
            ours_ci_high is None or abs(ours_ci_high - official.ci_high) > 1.0
        ):
            mismatches.append(f"ci_high: ours={ours_ci_high} official={official.ci_high}")
        if not ours_stable:
            mismatches.append("suppression: official published, ours suppressed")

    return ReconRow(
        code=code,
        published=True,
        match=not mismatches,
        mismatches=tuple(mismatches),
        official=official,
        ours_estimate=ours_estimate,
        ours_n=ours_n,
        ours_cv=ours_cv,
        ours_ci_low=ours_ci_low,
        ours_ci_high=ours_ci_high,
        ours_stable=ours_stable,
    )


def reconcile_year(
    con: duckdb.DuckDBPyConnection, year: int, official: dict[int, OfficialRow]
) -> list[ReconRow]:
    """Compare metric-layer rows (age_scope="all", by="product_code") against `official` for
    every in-scope product code. A code absent from `official` is "not published" (not a
    failure, per D-21)."""
    tax = load_product_taxonomy()
    scope = in_scope_codes(tax)
    ours = estimate_by(con, CaseFilter(years=(year, year), age_scope="all"), by="product_code")
    ours_by_code = {row["group_id"]: row for row in ours.iter_rows(named=True)}

    return [compare_row(code, official.get(code), ours_by_code.get(code)) for code in sorted(scope)]


def render_markdown(results_by_year: dict[int, list[ReconRow]], pooled_note: str) -> str:
    lines = [
        "# Metric Layer Validation (VAL-01)",
        "",
        "Reconciliation of `wii.metrics` estimates against the official CPSC Query Builder,",
        "offline, for every in-scope workshop product code. Product-code reconciliation is",
        "unaffected by narrative refinement -- refinement changes `final_tool_id`, never",
        "`product_code`. Per-tool rows do not sum to total burden (D-07). Every figure below is",
        "an estimate of injuries *associated with* the product, never described as caused by it.",
        "",
        "## Method",
        "",
        "Stratified with-replacement (ultimate cluster) Taylor linearization; multi-year periods",
        "pool strata as `year|stratum` and PSUs as `year|stratum|psu`; 95% CI uses",
        "t(0.975, df) with df = pooled PSUs - pooled strata (D-03).",
        "",
    ]

    total_rows = 0
    total_mismatches = 0
    for year in sorted(results_by_year):
        rows = results_by_year[year]
        lines.append(f"## {year}")
        lines.append("")
        lines.append(
            "| Product | Official Est | Ours Est | Official N | Ours N | "
            "Official CV | Ours CV | Match |"
        )
        lines.append("|---|---|---|---|---|---|---|---|")
        for r in rows:
            total_rows += 1
            if not r.published:
                ours_est = round(r.ours_estimate) if r.ours_estimate is not None else 0
                lines.append(
                    f"| {r.code} | not published | {ours_est} | -- | {r.ours_n} | -- | "
                    f"{r.ours_cv} | n/a (not published) |"
                )
                continue
            off = r.official
            official_est = "suppressed" if off.suppressed else f"{off.estimate:,}"
            official_n = "suppressed" if off.suppressed else f"{off.n}"
            official_cv = "suppressed" if off.suppressed else f"{off.cv}"
            ours_est = round(r.ours_estimate) if r.ours_estimate is not None else 0
            ours_cv = round(r.ours_cv, 4) if r.ours_cv is not None else None
            if r.match:
                match_cell = "yes"
            else:
                total_mismatches += 1
                match_cell = "MISMATCH: " + "; ".join(r.mismatches)
            lines.append(
                f"| {r.code} | {official_est} | {ours_est} | {official_n} | {r.ours_n} | "
                f"{official_cv} | {ours_cv} | {match_cell} |"
            )
        lines.append("")

    lines.append(f"**Totals: {total_mismatches} mismatch(es) across {total_rows} rows.**")
    lines.append("")
    lines.append("## Pooled multi-year")
    lines.append("")
    lines.append(pooled_note)
    lines.append("")
    return "\n".join(lines)
