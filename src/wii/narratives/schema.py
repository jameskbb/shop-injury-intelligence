"""Typed narrative-enrichment record (D-17) and validated output vocabularies (NARR-01..04).

`NarrativeEnrichment` is the frozen record every provider (`RuleProvider` now, `AnthropicProvider`
in 02-03) must produce. `validate()` raises `ValueError` naming the offending field/value for any
out-of-vocabulary output -- both providers share this one gate.
"""

from __future__ import annotations

from dataclasses import dataclass

# D-15: 13-category mechanism taxonomy, fixed priority order (highest first). Stored as a tuple
# (not just a frozenset) because the sequence itself is the D-15 contract -- tests assert it
# equals this exact order.
MECHANISM_PRIORITY: tuple[str, ...] = (
    "kickback_ejection",
    "entanglement",
    "fastener_discharge",
    "flying_debris_fb",
    "pinch_crush",
    "electrical_burn",
    "fall_trip",
    "loss_of_tool_control",
    "workpiece_slip",
    "direct_cutting_contact",
    "maintenance_setup",
    "other",
    "unknown",
)
MECHANISMS: frozenset[str] = frozenset(MECHANISM_PRIORITY)

# D-17: 8 activities.
ACTIVITIES: frozenset[str] = frozenset(
    {
        "cutting",
        "drilling",
        "fastening",
        "sanding_grinding",
        "shaping",
        "setup_maintenance",
        "carrying_moving",
        "unknown",
    }
)

# D-16: 3-valued PPE / guard states. The *_not_worn / *_absent values are set ONLY when the
# narrative explicitly says so -- absence of a mention is always not_mentioned (never inferred).
PPE_STATES: frozenset[str] = frozenset({"not_mentioned", "mentioned_worn", "mentioned_not_worn"})
GUARD_STATES: frozenset[str] = frozenset(
    {"not_mentioned", "mentioned_present", "mentioned_absent_or_removed"}
)

# D-14: 8-valued maintenance/setup context and 3-valued literal power-state mention. Both are
# preprocessing-only, explicit-only fields (Phase 4 Setup & Maintenance deep dive) -- absence of
# a mention is always the "not_*" value, never inferred (same guardrail as PPE_STATES/
# GUARD_STATES above).
MAINTENANCE_CONTEXTS: frozenset[str] = frozenset(
    {
        "blade_change",
        "bit_change",
        "jam_clearing",
        "cleaning",
        "adjustment",
        "material_setup",
        "other_maintenance",
        "not_maintenance",
    }
)
POWER_STATES: frozenset[str] = frozenset(
    {"not_mentioned", "mentioned_unplugged", "mentioned_plugged_in"}
)

# D-18: classification provenance.
SOURCES: frozenset[str] = frozenset({"rules", "llm"})

MAX_EVIDENCE_ITEMS = 3
MAX_EVIDENCE_PHRASE_LEN = 60


@dataclass(frozen=True)
class NarrativeEnrichment:
    """One case's narrative-derived enrichment (D-17). Every field is deterministic and
    versioned via `classifier_version`; `evidence` traces the matched phrases behind the call.
    Never reads or emits alcohol_code / drug_code (D-16) -- no blame/intoxication field exists.
    """

    tool_subtype: str
    activity: str
    primary_mechanism: str
    secondary_mechanisms: tuple[str, ...]
    maintenance_or_setup: bool
    workpiece_event: str | None
    projectile_or_debris: bool
    direct_tool_contact: bool
    body_contact_context: str | None
    ppe_mentioned: str
    guard_mentioned: str
    confidence: float
    evidence: tuple[str, ...]
    classification_source: str
    classifier_version: str
    # D-14: appended last, defaulted, so every pre-existing positional/keyword construction
    # (including validate_llm_payload below) keeps working unchanged.
    maintenance_context: str = "not_maintenance"
    power_state_mentioned: str = "not_mentioned"


@dataclass(frozen=True)
class ProductRefinement:
    """Per-product-code refinement outcome (D-14). One row per `bridge_case_products` slot on
    the case. `narrative_refined_tool` is None unless the broad-code + confidence-gate + no
    conflicting-term conditions all hold; `final_tool_id` falls back to `official_tool_id`."""

    product_code: int
    official_tool_id: str
    narrative_refined_tool: str | None
    final_tool_id: str
    refinement_confidence: float


def validate(record: NarrativeEnrichment) -> None:
    """Raise `ValueError` naming the offending field/value for any out-of-vocabulary output.

    Reused by 02-03's `AnthropicProvider` to validate LLM output against the same vocabularies.
    """
    if record.primary_mechanism not in MECHANISMS:
        raise ValueError(f"primary_mechanism {record.primary_mechanism!r} is not a known mechanism")
    for m in record.secondary_mechanisms:
        if m not in MECHANISMS:
            raise ValueError(f"secondary_mechanisms contains unknown mechanism {m!r}")
    if record.activity not in ACTIVITIES:
        raise ValueError(f"activity {record.activity!r} is not a known activity")
    if record.ppe_mentioned not in PPE_STATES:
        raise ValueError(f"ppe_mentioned {record.ppe_mentioned!r} is not a known PPE state")
    if record.guard_mentioned not in GUARD_STATES:
        raise ValueError(f"guard_mentioned {record.guard_mentioned!r} is not a known guard state")
    if record.maintenance_context not in MAINTENANCE_CONTEXTS:
        raise ValueError(
            f"maintenance_context {record.maintenance_context!r} is not a known maintenance context"
        )
    if record.power_state_mentioned not in POWER_STATES:
        raise ValueError(
            f"power_state_mentioned {record.power_state_mentioned!r} is not a known power state"
        )
    if record.classification_source not in SOURCES:
        raise ValueError(
            f"classification_source {record.classification_source!r} is not a known source"
        )
    if not (0.0 <= record.confidence <= 1.0):
        raise ValueError(f"confidence {record.confidence!r} is out of [0, 1]")
    if len(record.evidence) > MAX_EVIDENCE_ITEMS:
        raise ValueError(f"evidence has {len(record.evidence)} items, max {MAX_EVIDENCE_ITEMS}")
    for phrase in record.evidence:
        if len(phrase) > MAX_EVIDENCE_PHRASE_LEN:
            raise ValueError(f"evidence phrase {phrase!r} exceeds {MAX_EVIDENCE_PHRASE_LEN} chars")


def _require_bool(payload: dict, key: str, default: bool = False) -> bool:
    val = payload.get(key, default)
    if not isinstance(val, bool):
        raise ValueError(f"llm payload field {key!r} is not a boolean")
    return val


def _optional_str(payload: dict, key: str) -> str | None:
    val = payload.get(key)
    if val is not None and not isinstance(val, str):
        raise ValueError(f"llm payload field {key!r} is not a string")
    return val


def validate_llm_payload(payload: dict) -> NarrativeEnrichment:
    """D-18: validate/coerce an `AnthropicProvider` tool_use payload into a `NarrativeEnrichment`
    (`classification_source='llm'`). Checks field types and vocabularies (via `validate()`),
    clamps `confidence` to [0, 1], and truncates `evidence` to `MAX_EVIDENCE_ITEMS` phrases of
    `MAX_EVIDENCE_PHRASE_LEN` chars each. Raises `ValueError` naming the offending field for any
    structurally invalid payload -- the caller (`AnthropicProvider`) treats any `ValueError` here
    as "keep the rules result", never crashing the pipeline on a malformed model response.
    """
    if not isinstance(payload, dict):
        raise ValueError("llm payload is not a JSON object")

    primary_mechanism = payload.get("primary_mechanism")
    if not isinstance(primary_mechanism, str) or not primary_mechanism:
        raise ValueError("llm payload field 'primary_mechanism' is required and must be a string")

    tool_subtype = _optional_str(payload, "tool_subtype") or "unknown"
    activity = _optional_str(payload, "activity") or "unknown"
    ppe_mentioned = _optional_str(payload, "ppe_mentioned") or "not_mentioned"
    guard_mentioned = _optional_str(payload, "guard_mentioned") or "not_mentioned"
    workpiece_event = _optional_str(payload, "workpiece_event")
    body_contact_context = _optional_str(payload, "body_contact_context")

    secondary_raw = payload.get("secondary_mechanisms", [])
    if not isinstance(secondary_raw, list) or not all(isinstance(m, str) for m in secondary_raw):
        raise ValueError("llm payload field 'secondary_mechanisms' must be a list of strings")

    evidence_raw = payload.get("evidence", [])
    if not isinstance(evidence_raw, list) or not all(isinstance(e, str) for e in evidence_raw):
        raise ValueError("llm payload field 'evidence' must be a list of strings")
    evidence = tuple(e[:MAX_EVIDENCE_PHRASE_LEN] for e in evidence_raw[:MAX_EVIDENCE_ITEMS])

    confidence_raw = payload.get("confidence", 0.0)
    if not isinstance(confidence_raw, int | float) or isinstance(confidence_raw, bool):
        raise ValueError("llm payload field 'confidence' is not a number")
    confidence = max(0.0, min(1.0, float(confidence_raw)))

    record = NarrativeEnrichment(
        tool_subtype=tool_subtype,
        activity=activity,
        primary_mechanism=primary_mechanism,
        secondary_mechanisms=tuple(secondary_raw),
        maintenance_or_setup=_require_bool(payload, "maintenance_or_setup"),
        workpiece_event=workpiece_event,
        projectile_or_debris=_require_bool(payload, "projectile_or_debris"),
        direct_tool_contact=_require_bool(payload, "direct_tool_contact"),
        body_contact_context=body_contact_context,
        ppe_mentioned=ppe_mentioned,
        guard_mentioned=guard_mentioned,
        confidence=confidence,
        evidence=evidence,
        classification_source="llm",
        classifier_version=_llm_classifier_version(),
    )
    validate(record)
    return record


def _llm_classifier_version() -> str:
    from wii.narratives import CLASSIFIER_VERSION

    return CLASSIFIER_VERSION
