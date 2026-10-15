"""D-20/NARR-06: measured classifier quality. Two kinds of numbers, combined into one report:

1. Descriptive stats over the COMMITTED release (`data/processed/{bridge_case_products,
   narrative_enrichment,dim_product,fact_cases}.parquet`) -- coverage, unknown %, confidence
   distribution, rules-vs-llm source counts, product-code-vs-narrative disagreements, subtype
   coverage per broad code. No ground truth needed for these.
2. Accuracy against the D-19 blind-labeled fixture (`tests/fixtures/narratives/
   labeled_validation.csv`) -- the fixture's own stored narratives are re-classified through the
   CURRENT rules (never the committed enrichment, which may be stale relative to a rule change),
   then compared field-by-field and class-by-class against the labels, separately for the
   `holdout` split and `all` rows (D-19: `dev` may be used for tuning, `holdout` for reporting).

No hard accuracy target is enforced (D-20) -- this module always completes and returns a report;
`scripts/validate_classifications.py` always exits 0.
"""

from __future__ import annotations

import csv
import random
import re
from collections import defaultdict
from pathlib import Path

import polars as pl

from wii.narratives.classify import classify_case
from wii.narratives.normalize import normalize
from wii.narratives.rules import load_rules
from wii.taxonomy.loader import load_product_taxonomy

PER_FIELD_ACCURACY_FIELDS: tuple[str, ...] = (
    "tool_subtype",
    "final_tool_id",
    "activity",
    "primary_mechanism",
    "maintenance_or_setup",
    "ppe_mentioned",
    "guard_mentioned",
    "maintenance_context",
    "power_state_mentioned",
)

REVIEW_SAMPLE_PER_CELL = 3
REVIEW_SAMPLE_SEED = 20260915

_CONFIDENCE_HIGH = 0.80
_CONFIDENCE_LOW = 0.50


# --- descriptive stats over the committed release -----------------------------------------------


def compute_coverage(bridge: pl.DataFrame) -> dict:
    """Share of in-scope bridge_case_products rows with a populated classification_confidence."""
    in_scope = bridge.filter(pl.col("in_scope"))
    total = in_scope.height
    classified = (
        in_scope.filter(pl.col("classification_confidence").is_not_null()).height if total else 0
    )
    return {
        "in_scope_rows": total,
        "classified_rows": classified,
        "rate": (classified / total) if total else 0.0,
    }


def compute_unknown_pct(enrichment: pl.DataFrame) -> dict:
    total = enrichment.height
    mech_unknown = (
        enrichment.filter(pl.col("primary_mechanism") == "unknown").height if total else 0
    )
    subtype_unknown = enrichment.filter(pl.col("tool_subtype") == "unknown").height if total else 0
    return {
        "mechanism": (mech_unknown / total * 100) if total else 0.0,
        "subtype": (subtype_unknown / total * 100) if total else 0.0,
    }


def compute_confidence_distribution(enrichment: pl.DataFrame) -> dict:
    """Deciles [0.0-0.1) .. [0.9-1.0] plus high(>=0.80) / mid / low(<0.50) buckets."""
    confidences = enrichment["confidence"].to_list() if enrichment.height else []
    total = len(confidences)
    deciles = [0] * 10
    for c in confidences:
        idx = min(int(c * 10), 9) if c is not None else 0
        deciles[idx] += 1
    high = sum(1 for c in confidences if (c or 0.0) >= _CONFIDENCE_HIGH)
    low = sum(1 for c in confidences if (c or 0.0) < _CONFIDENCE_LOW)
    mid = total - high - low
    return {
        "deciles": deciles,
        "high_ge_0.80": high,
        "mid": mid,
        "low_lt_0.50": low,
        "total": total,
    }


def compute_source_counts(enrichment: pl.DataFrame) -> dict:
    if enrichment.height == 0:
        return {}
    vc = enrichment["classification_source"].value_counts()
    return {row["classification_source"]: int(row["count"]) for row in vc.iter_rows(named=True)}


def compute_subtype_coverage(bridge: pl.DataFrame, dim_product: pl.DataFrame) -> dict:
    """Per-broad-code refinement rate (D-14): refined / total in-scope rows for that code."""
    if "broad" not in dim_product.columns:
        return {}
    broad_codes = dim_product.filter(pl.col("broad"))["product_code"].to_list()
    result: dict[str, dict] = {}
    for code in sorted(broad_codes):
        rows = bridge.filter(pl.col("in_scope") & (pl.col("product_code") == code))
        total = rows.height
        refined = rows.filter(pl.col("narrative_refined_tool").is_not_null()).height if total else 0
        result[str(code)] = {
            "total": total,
            "refined": refined,
            "rate": (refined / total) if total else 0.0,
        }
    return result


def compute_disagreements(
    bridge: pl.DataFrame, fact_cases: pl.DataFrame, dim_product: pl.DataFrame
) -> dict:
    """Specific (non-broad) code rows whose narrative names a DIFFERENT specific tool than the
    official code's own tool_id -- informational only, never changes final_tool_id (NARR-02)."""
    if "broad" not in dim_product.columns or bridge.height == 0:
        return {"count": 0, "examples": []}

    rules = load_rules()
    phrase_to_target: dict[str, str] = {}
    for broad in rules.broad_codes.values():
        for pattern in broad.patterns:
            if pattern.target:
                for phrase in pattern.phrases:
                    phrase_to_target[phrase] = pattern.target

    broad_codes = frozenset(dim_product.filter(pl.col("broad"))["product_code"].to_list())
    specific_rows = bridge.filter(
        pl.col("in_scope") & ~pl.col("product_code").is_in(list(broad_codes))
    )
    narratives_by_case = (
        dict(zip(fact_cases["case_id"].to_list(), fact_cases["narrative"].to_list(), strict=True))
        if fact_cases.height
        else {}
    )

    count = 0
    examples: list[str] = []
    for row in specific_rows.iter_rows(named=True):
        narrative = narratives_by_case.get(row["case_id"])
        if not narrative:
            continue
        normalized = normalize(narrative, rules.abbreviations)
        for phrase, target in phrase_to_target.items():
            if target == row["tool_id"]:
                continue
            if re.search(r"\b" + re.escape(phrase) + r"\b", normalized.text):
                count += 1
                if len(examples) < 10:
                    examples.append(
                        f"{row['case_id']}: assigned {row['tool_id']}, narrative names {target}"
                    )
                break
    return {"count": count, "examples": examples}


# --- accuracy against the labeled fixture --------------------------------------------------------


def load_fixture_rows(fixture_path: Path) -> list[dict]:
    with open(fixture_path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def reclassify_fixture_rows(rows: list[dict]) -> list[dict]:
    """Re-classify each fixture row's stored narrative through the CURRENT rules (never the
    committed enrichment, which may be stale relative to a rule change)."""
    tax = load_product_taxonomy()
    out: list[dict] = []
    for row in rows:
        code = int(row["official_product_code"])
        tool_id = tax.codes[code].tool_id if code in tax.codes else row.get("official_tool_id", "")
        enrichment, refinements = classify_case(row["narrative"], [(code, tool_id)])
        pred_final_tool_id = refinements[0].final_tool_id if refinements else tool_id
        out.append(
            {
                **row,
                "pred_tool_subtype": enrichment.tool_subtype,
                "pred_final_tool_id": pred_final_tool_id,
                "pred_activity": enrichment.activity,
                "pred_primary_mechanism": enrichment.primary_mechanism,
                "pred_maintenance_or_setup": enrichment.maintenance_or_setup,
                "pred_ppe_mentioned": enrichment.ppe_mentioned,
                "pred_guard_mentioned": enrichment.guard_mentioned,
                "pred_maintenance_context": enrichment.maintenance_context,
                "pred_power_state_mentioned": enrichment.power_state_mentioned,
                "pred_confidence": enrichment.confidence,
                "pred_evidence": list(enrichment.evidence),
            }
        )
    return out


def per_class_precision_recall(labels: list[str], predictions: list[str]) -> dict[str, dict]:
    """Precision/recall/support per class present in `labels` or `predictions`."""
    classes = sorted({*labels, *predictions})
    result: dict[str, dict] = {}
    pairs = list(zip(labels, predictions, strict=True))
    for cls in classes:
        tp = sum(1 for lbl, pred in pairs if lbl == cls and pred == cls)
        fp = sum(1 for lbl, pred in pairs if lbl != cls and pred == cls)
        fn = sum(1 for lbl, pred in pairs if lbl == cls and pred != cls)
        support = sum(1 for lbl in labels if lbl == cls)
        result[cls] = {
            "precision": (tp / (tp + fp)) if (tp + fp) else 0.0,
            "recall": (tp / (tp + fn)) if (tp + fn) else 0.0,
            "support": support,
        }
    return result


def per_field_accuracy(
    rows: list[dict], fields: tuple[str, ...] = PER_FIELD_ACCURACY_FIELDS
) -> dict[str, float]:
    """Field-by-field accuracy: `label_{field}` vs `pred_{field}` across `rows`. A row with no
    label for a field is excluded from that field's denominator (not counted as wrong)."""
    result: dict[str, float] = {}
    for field in fields:
        label_key, pred_key = f"label_{field}", f"pred_{field}"
        total = correct = 0
        for row in rows:
            label_val = row.get(label_key)
            if label_val in (None, ""):
                continue
            total += 1
            pred_val = row.get(pred_key)
            if field == "maintenance_or_setup":
                label_bool = str(label_val).strip().upper() == "TRUE"
                if label_bool == bool(pred_val):
                    correct += 1
            elif str(label_val) == str(pred_val):
                correct += 1
        result[field] = (correct / total) if total else 0.0
    return result


# --- orchestration --------------------------------------------------------------------------------


def build_validation_report(processed_dir: Path, fixture_path: Path) -> tuple[dict, list[dict]]:
    from wii.narratives import CLASSIFIER_VERSION

    processed_dir = Path(processed_dir)
    bridge = pl.read_parquet(processed_dir / "bridge_case_products.parquet")
    enrichment = pl.read_parquet(processed_dir / "narrative_enrichment.parquet")
    dim_product = pl.read_parquet(processed_dir / "dim_product.parquet")
    fact_cases = pl.read_parquet(processed_dir / "fact_cases.parquet")

    fixture_rows = load_fixture_rows(Path(fixture_path))
    predicted_rows = reclassify_fixture_rows(fixture_rows)
    holdout_rows = [r for r in predicted_rows if r.get("split") == "holdout"]

    def _class_report(label_field: str, pred_field: str) -> dict:
        return {
            "holdout": per_class_precision_recall(
                [r[label_field] for r in holdout_rows], [r[pred_field] for r in holdout_rows]
            ),
            "all": per_class_precision_recall(
                [r[label_field] for r in predicted_rows], [r[pred_field] for r in predicted_rows]
            ),
        }

    report = {
        "coverage": compute_coverage(bridge),
        "unknown_pct": compute_unknown_pct(enrichment),
        "confidence_distribution": compute_confidence_distribution(enrichment),
        "source_counts": compute_source_counts(enrichment),
        "disagreements": compute_disagreements(bridge, fact_cases, dim_product),
        "subtype_coverage": compute_subtype_coverage(bridge, dim_product),
        "per_field_accuracy": {
            "holdout": per_field_accuracy(holdout_rows),
            "all": per_field_accuracy(predicted_rows),
        },
        "per_class": {
            "tool_subtype": _class_report("label_tool_subtype", "pred_tool_subtype"),
            "primary_mechanism": _class_report("label_primary_mechanism", "pred_primary_mechanism"),
        },
        "labeled": {
            "all_n": len(predicted_rows),
            "holdout_n": len(holdout_rows),
            "dev_n": len(predicted_rows) - len(holdout_rows),
        },
        "classifier_version": CLASSIFIER_VERSION,
    }
    return report, predicted_rows


_REVIEW_SAMPLE_FIELDS = (
    "case_id",
    "narrative",
    "pred_tool_subtype",
    "pred_final_tool_id",
    "pred_activity",
    "pred_primary_mechanism",
    "pred_maintenance_or_setup",
    "pred_ppe_mentioned",
    "pred_guard_mentioned",
    "pred_confidence",
    "pred_evidence",
)


def write_review_sample(
    predicted_rows: list[dict], out_path: Path, seed: int = REVIEW_SAMPLE_SEED
) -> None:
    """Seeded random sample, up to REVIEW_SAMPLE_PER_CELL rows per (tool x primary_mechanism)
    cell, for human spot-review."""
    rng = random.Random(seed)
    cells: dict[tuple, list[dict]] = defaultdict(list)
    for row in predicted_rows:
        key = (row.get("pred_final_tool_id"), row.get("pred_primary_mechanism"))
        cells[key].append(row)

    sample_rows: list[dict] = []
    for key in sorted(cells, key=lambda k: (k[0] or "", k[1] or "")):
        candidates = list(cells[key])
        rng.shuffle(candidates)
        sample_rows.extend(candidates[:REVIEW_SAMPLE_PER_CELL])

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(_REVIEW_SAMPLE_FIELDS))
        writer.writeheader()
        for row in sample_rows:
            writer.writerow({k: row.get(k) for k in _REVIEW_SAMPLE_FIELDS})


def render_markdown(report: dict) -> str:
    lines = [
        "# Classifier Validation Report (NARR-06)",
        "",
        "**Disclosure:** the labels in `tests/fixtures/narratives/labeled_validation.csv` were "
        "produced by the AI developer assistant during this project's build, not by a domain "
        "expert (clinician, injury epidemiologist, or trained NEISS coder). They are a "
        "best-effort, disclosed approximation of ground truth (D-19).",
        "",
        "**Dev vs holdout:** rules were tuned only against the `dev` split (40 cases); the "
        "`holdout` split (the remainder) was never used to hand-tune regex patterns or "
        "confidence weights, and is the reported accuracy source of truth.",
        "",
        "**No accuracy target is enforced.** Numbers below are published as measured (D-20), not "
        "gated against a threshold.",
        "",
        f"Classifier version: `{report.get('classifier_version', 'unknown')}`",
        "",
        "## Coverage",
        "",
        f"- In-scope rows: {report['coverage']['in_scope_rows']}",
        f"- Classified: {report['coverage']['classified_rows']} ({report['coverage']['rate']:.1%})",
        "",
        "## Unknown %",
        "",
        f"- Mechanism unknown: {report['unknown_pct']['mechanism']:.1f}%",
        f"- Subtype unknown: {report['unknown_pct']['subtype']:.1f}%",
        "",
        "## Confidence distribution",
        "",
        f"- High (>=0.80): {report['confidence_distribution']['high_ge_0.80']}",
        f"- Mid: {report['confidence_distribution']['mid']}",
        f"- Low (<0.50): {report['confidence_distribution']['low_lt_0.50']}",
        f"- Deciles: {report['confidence_distribution']['deciles']}",
        "",
        "## Source counts (rules vs llm)",
        "",
        f"{report['source_counts']}",
        "",
        "## Product-code vs narrative disagreements",
        "",
        f"- Count: {report['disagreements']['count']}",
        "",
        "## Subtype coverage by broad code",
        "",
        "| product_code | total | refined | rate |",
        "|---|---|---|---|",
    ]
    for code, stats in report["subtype_coverage"].items():
        lines.append(f"| {code} | {stats['total']} | {stats['refined']} | {stats['rate']:.1%} |")

    lines += [
        "",
        "## Per-field accuracy (labeled fixture)",
        "",
        "| split | " + " | ".join(PER_FIELD_ACCURACY_FIELDS) + " |",
        "|---|" + "---|" * len(PER_FIELD_ACCURACY_FIELDS),
    ]
    for split in ("holdout", "all"):
        vals = report["per_field_accuracy"][split]
        row = " | ".join(f"{vals[f]:.1%}" for f in PER_FIELD_ACCURACY_FIELDS)
        lines.append(f"| {split} | {row} |")

    lines += [
        "",
        "## Per-class precision/recall (labeled fixture, holdout split)",
        "",
    ]
    for label_field in ("tool_subtype", "primary_mechanism"):
        lines.append(f"### {label_field}")
        lines.append("")
        lines.append("| class | precision | recall | support |")
        lines.append("|---|---|---|---|")
        for cls, stats in sorted(report["per_class"][label_field]["holdout"].items()):
            lines.append(
                f"| {cls} | {stats['precision']:.1%} | {stats['recall']:.1%} | {stats['support']} |"
            )
        lines.append("")

    labeled = report["labeled"]
    lines += [
        "## Labeled fixture size",
        "",
        f"- All: {labeled['all_n']}",
        f"- Holdout: {labeled['holdout_n']}",
        f"- Dev: {labeled['dev_n']}",
        "",
    ]
    return "\n".join(lines) + "\n"
