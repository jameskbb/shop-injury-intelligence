"""D-09/D-10 severity metrics: disposition/diagnosis/body-region shares driven by
severity_metrics.yaml + the dim tables (never a hardcoded code literal, matching the Phase 1
`escalated_care`-from-`dim_disposition` pattern).

Every metric also declares an explicit `direction` (semantic layer, not a convention buried in
whichever page happens to render it). The vocabulary is closed (`METRIC_DIRECTIONS`) and describes
what a HIGHER share means about the CLINICAL CONTENT of a group's ER-treated cases -- never
"higher is better/worse". There is no good/bad value in the vocabulary and there cannot be one:
these are shares among cases that already reached an emergency department, and NEISS carries no
exposure denominator, so a higher share can never be read as a per-use comparison between tools.
`direction` is metadata only -- nothing in this module's estimates consults it, so adding or
changing one cannot move a number.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

import duckdb
import polars as pl
import yaml

from wii.metrics.estimator import estimate_by, share_by
from wii.metrics.filters import CaseFilter

_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "severity_metrics.yaml"
_VALID_KINDS = {"disposition_bucket", "disposition_escalated", "diagnosis_any", "body_rollup_any"}

# The closed direction vocabulary. See severity_metrics.yaml's header for the full reasoning; the
# short version is that "higher is better" is not expressible for any metric in this file, so no
# such value exists to be chosen by accident.
MORE_ACUTE_CARE = "higher_means_more_acute_care"
LESS_ACUTE_CARE = "higher_means_less_acute_care"
CARE_NOT_COMPLETED = "higher_means_care_not_completed"
MORE_TISSUE_DAMAGE = "higher_means_more_tissue_damage"
NO_SEVERITY_ORDERING = "no_severity_ordering"

METRIC_DIRECTIONS: frozenset[str] = frozenset(
    {
        MORE_ACUTE_CARE,
        LESS_ACUTE_CARE,
        CARE_NOT_COMPLETED,
        MORE_TISSUE_DAMAGE,
        NO_SEVERITY_ORDERING,
    }
)

# Appended to every gloss. Any UI that surfaces a direction surfaces this sentence with it, so the
# direction can never be read on its own as a ranking of tools.
NO_EXPOSURE_DENOMINATOR_NOTE = (
    "NEISS has no exposure denominator, so this describes the ER-treated cases in a group, "
    "never a per-use comparison between tools."
)

_DIRECTION_MEANINGS: dict[str, str] = {
    MORE_ACUTE_CARE: (
        "A higher share means more of this group's ER cases needed care beyond "
        "emergency-department treat-and-release."
    ),
    LESS_ACUTE_CARE: (
        "A higher share means more of this group's ER cases were treated and released."
    ),
    CARE_NOT_COMPLETED: (
        "A higher share means more cases left without being seen or treated -- patient behaviour "
        "and department process, not injury severity."
    ),
    MORE_TISSUE_DAMAGE: (
        "A higher share means more of this group's ER cases carried a diagnosis that is "
        "destructive or structural by definition rather than one spanning trivial to severe."
    ),
    NO_SEVERITY_ORDERING: (
        "A higher share carries no severity meaning in either direction: this metric describes "
        "where injuries landed or what they were called, not how grave they were."
    ),
}

DIRECTION_GLOSS: dict[str, str] = {
    direction: f"{meaning} {NO_EXPOSURE_DENOMINATOR_NOTE}"
    for direction, meaning in _DIRECTION_MEANINGS.items()
}


class SeverityConfigError(Exception):
    """Raised for a structural problem in severity_metrics.yaml. Message names the key path."""


@dataclass(frozen=True)
class SeverityMetric:
    metric_id: str
    kind: str
    direction: str
    codes: tuple[int, ...] = ()
    rollup_id: str | None = None

    @property
    def direction_gloss(self) -> str:
        """The honest, renderable sentence for this metric's direction, including the
        no-exposure-denominator caveat."""
        return direction_gloss(self.direction)

    @property
    def has_severity_ordering(self) -> bool:
        """False when a higher value carries no severity meaning at all -- the flag a consumer
        checks BEFORE it sorts, colour-codes or narrates a metric as an ordered scale."""
        return self.direction != NO_SEVERITY_ORDERING


def direction_gloss(direction: str) -> str:
    """Human-readable meaning of a direction value. Raises on an unknown value rather than
    returning a bland default that would let an unlabelled metric render as if it were ordered."""
    try:
        return DIRECTION_GLOSS[direction]
    except KeyError:
        raise SeverityConfigError(f"unknown direction {direction!r}") from None


def load_severity_metrics(path: Path | None = None) -> dict[str, SeverityMetric]:
    path = path or _CONFIG_PATH
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    if not doc or "metrics" not in doc:
        raise SeverityConfigError(f"{path}: 'metrics' key missing or file is empty")

    metrics: dict[str, SeverityMetric] = {}
    for metric_id, mdoc in doc["metrics"].items():
        if not isinstance(mdoc, dict) or "kind" not in mdoc:
            raise SeverityConfigError(f"metrics.{metric_id}.kind missing")
        kind = mdoc["kind"]
        if kind not in _VALID_KINDS:
            raise SeverityConfigError(f"metrics.{metric_id}.kind {kind!r} is not a known kind")
        direction = mdoc.get("direction")
        if not direction:
            raise SeverityConfigError(f"metrics.{metric_id}.direction missing")
        if direction not in METRIC_DIRECTIONS:
            raise SeverityConfigError(
                f"metrics.{metric_id}.direction {direction!r} is not a known direction"
            )
        if kind in ("disposition_bucket", "diagnosis_any"):
            codes = mdoc.get("codes")
            if not codes:
                raise SeverityConfigError(f"metrics.{metric_id}.codes missing or empty")
            metrics[metric_id] = SeverityMetric(
                metric_id=metric_id,
                kind=kind,
                direction=direction,
                codes=tuple(int(c) for c in codes),
            )
        elif kind == "body_rollup_any":
            rollup_id = mdoc.get("rollup_id")
            if not rollup_id:
                raise SeverityConfigError(f"metrics.{metric_id}.rollup_id missing")
            metrics[metric_id] = SeverityMetric(
                metric_id=metric_id, kind=kind, direction=direction, rollup_id=rollup_id
            )
        else:  # disposition_escalated
            metrics[metric_id] = SeverityMetric(metric_id=metric_id, kind=kind, direction=direction)
    return metrics


def _numerator_filter(metric: SeverityMetric, con: duckdb.DuckDBPyConnection) -> CaseFilter:
    if metric.kind == "disposition_bucket":
        return CaseFilter(dispositions=metric.codes)
    if metric.kind == "disposition_escalated":
        codes = tuple(
            r[0]
            for r in con.execute(
                "SELECT code FROM dim_disposition WHERE escalated_care = TRUE"
            ).fetchall()
        )
        return CaseFilter(dispositions=codes)
    if metric.kind == "diagnosis_any":
        return CaseFilter(diagnosis_codes=metric.codes, diagnosis_basis="any")
    if metric.kind == "body_rollup_any":
        return CaseFilter(body_regions=(metric.rollup_id,), body_basis="any")
    raise SeverityConfigError(f"unhandled kind {metric.kind!r}")


def _disposition_codes_by_bucket(con: duckdb.DuckDBPyConnection, *, not_recorded: bool) -> tuple:
    op = "=" if not_recorded else "!="
    return tuple(
        r[0]
        for r in con.execute(
            f"SELECT code FROM dim_disposition WHERE severity_bucket {op} 'not_recorded'"
        ).fetchall()
    )


def _known_disposition_denominator_filter(
    con: duckdb.DuckDBPyConnection, flt: CaseFilter
) -> CaseFilter:
    """Finding 1: the disposition denominator is the CALLER's `flt` (years, tools, sexes,
    regions, age_scope, ...) AND known-disposition -- never a bare known-disposition-only filter
    that silently drops every other caller constraint."""
    codes = _disposition_codes_by_bucket(con, not_recorded=False)
    return replace(flt, dispositions=codes)


def _not_recorded_estimates(
    con: duckdb.DuckDBPyConnection, flt: CaseFilter, by: str
) -> dict | None:
    """Finding 9: not-recorded reported as a WEIGHTED estimate + raw_n (never an unweighted
    count folded in among weighted shares), scoped to the caller's own `flt`, with the
    not-recorded code(s) read from `dim_disposition.severity_bucket == 'not_recorded'` -- never a
    hardcoded literal. Returns `{group_id: (estimate, raw_n)}` (a group absent from the result
    genuinely had zero not-recorded cases -- callers default a missing key to `(0.0, 0)`), or
    `None` if the domain is too sparse to compute variance at all (ValueError) -- callers must
    treat `None` as "not recorded is unknown here", never as zero.
    """
    not_recorded_codes = _disposition_codes_by_bucket(con, not_recorded=True)
    if not not_recorded_codes:
        return {}
    nr_flt = replace(flt, dispositions=not_recorded_codes)
    try:
        result = estimate_by(con, nr_flt, by=by)
    except ValueError:
        return None
    return {
        row["group_id"]: (row["estimate"], row["raw_n"]) for row in result.iter_rows(named=True)
    }


def severity_profile(
    con: duckdb.DuckDBPyConnection,
    flt: CaseFilter,
    by: str = "tool",
    diagnosis_basis: Literal["any", "primary"] = "any",
) -> pl.DataFrame:
    """One row per group x metric_id, metric_id drawn from severity_metrics.yaml (D-09/D-10).

    Disposition metrics use denominator = caller `flt` AND known-disposition cases;
    `not_recorded_estimate`/`not_recorded_raw_n` report the excluded not-recorded cases as a
    weighted estimate (D-09, findings 1 and 9). All other metrics use the full group as
    denominator (`flt`). `escalated_care` numerator is sourced from `dim_disposition
    .escalated_care`, never a hardcoded code literal.
    """
    metrics = load_severity_metrics()
    known_disposition_flt = _known_disposition_denominator_filter(con, flt)
    not_recorded_by_group = _not_recorded_estimates(con, flt, by)

    rows = []
    for metric in metrics.values():
        numerator = _numerator_filter(metric, con)
        if metric.kind in ("disposition_bucket", "disposition_escalated"):
            denominator = known_disposition_flt
        else:
            denominator = flt
            if metric.kind == "diagnosis_any":
                numerator = replace_basis(numerator, diagnosis_basis)

        result = share_by(con, denominator, numerator, by=by)
        for row in result.iter_rows(named=True):
            rec = dict(row)
            rec["metric_id"] = metric.metric_id
            if metric.kind in ("disposition_bucket", "disposition_escalated"):
                if not_recorded_by_group is None:
                    estimate, raw_n = None, None
                else:
                    estimate, raw_n = not_recorded_by_group.get(row["group_id"], (0.0, 0))
                rec["not_recorded_estimate"] = estimate
                rec["not_recorded_raw_n"] = raw_n
            else:
                rec["not_recorded_estimate"] = None
                rec["not_recorded_raw_n"] = None
            rows.append(rec)
    return pl.DataFrame(rows)


def replace_basis(flt: CaseFilter, diagnosis_basis: str) -> CaseFilter:
    return replace(flt, diagnosis_basis=diagnosis_basis)
