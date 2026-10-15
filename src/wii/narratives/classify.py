"""D-12 classification orchestration: normalize -> subtype YAML rules -> mechanism/activity/
PPE/guard rules -> refinement gate -> typed record. `classify_case` is the single entry point
`RuleProvider` (and every realdata caller) uses.
"""

from __future__ import annotations

import re

from wii.narratives import CLASSIFIER_VERSION
from wii.narratives.normalize import NormalizedText, normalize
from wii.narratives.rules import (
    RuleTables,
    context_present,
    is_redaction_adjacent,
    load_rules,
    match_phrases,
    preceding_token,
)
from wii.narratives.schema import NarrativeEnrichment, ProductRefinement

REFINEMENT_CONFIDENCE_GATE = 0.70
MECHANISM_UNKNOWN_THRESHOLD = 0.50
BASE_CONFIDENCE_PHRASE = 0.90
BASE_CONFIDENCE_WORD = 0.75
SHORT_NARRATIVE_TOKEN_THRESHOLD = 5
TOOL_WORDS = ("SAW", "DRILL", "GRINDER", "SANDER", "TOOL", "BLADE", "NAILER", "PLANER", "ROUTER")
UNPLUG_PHRASES = ("UNPLUG", "UNPLUGGING", "UNPLUGGED", "UNPLUGS")
_BLADE_CAUGHT_PHRASES = ("CAUGHT ON BLADE", "CAUGHT BY BLADE")

_RULES_CACHE: RuleTables | None = None


def _get_rules() -> RuleTables:
    global _RULES_CACHE
    if _RULES_CACHE is None:
        _RULES_CACHE = load_rules()
    return _RULES_CACHE


def _base_confidence(phrase: str) -> float:
    return BASE_CONFIDENCE_PHRASE if " " in phrase.strip() else BASE_CONFIDENCE_WORD


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _empty_enrichment(source: str = "rules") -> NarrativeEnrichment:
    return NarrativeEnrichment(
        tool_subtype="unknown",
        activity="unknown",
        primary_mechanism="unknown",
        secondary_mechanisms=(),
        maintenance_or_setup=False,
        workpiece_event=None,
        projectile_or_debris=False,
        direct_tool_contact=False,
        body_contact_context=None,
        ppe_mentioned="not_mentioned",
        guard_mentioned="not_mentioned",
        confidence=0.0,
        evidence=(),
        classification_source=source,
        classifier_version=CLASSIFIER_VERSION,
        maintenance_context="not_maintenance",
        power_state_mentioned="not_mentioned",
    )


# --- subtype / refinement (D-14) -------------------------------------------------------------


def _classify_subtype(
    normalized: NormalizedText, product_code: int, rules: RuleTables
) -> tuple[str, str | None, float, tuple[str, ...]]:
    """Returns `(tool_subtype, refinement_target_or_None, confidence, evidence_phrases)` for one
    broad product code against one narrative. D-14: refinement is withheld when the same
    narrative matches patterns pointing to more than one distinct target (conflicting term)."""
    broad = rules.broad_codes.get(product_code)
    if broad is None or not normalized.text:
        return "unknown", None, 0.0, ()

    candidates: list[tuple[str, str | None, float, str]] = []
    for pattern in broad.patterns:
        matches = match_phrases(pattern.phrases, pattern.pattern_id, normalized)
        positive = [m for m in matches if not m.negated]
        if not positive:
            continue
        if pattern.require_context and not context_present(
            rules.context_terms.get(pattern.require_context, ()), normalized
        ):
            continue
        if pattern.exclude_context and context_present(
            rules.context_terms.get(pattern.exclude_context, ()), normalized
        ):
            continue
        candidates.append((pattern.subtype, pattern.target, pattern.confidence, positive[0].phrase))

    if not candidates:
        return "unknown", None, 0.0, ()

    distinct_targets = {c[1] for c in candidates if c[1] is not None}
    subtype, target, confidence, phrase = max(candidates, key=lambda c: c[2])
    if len(distinct_targets) > 1:
        # D-14: conflicting subtype terms in the same narrative withhold refinement entirely.
        target = None
    if target is not None and target not in broad.allowed_targets:
        target = None

    return subtype, target, confidence, (phrase[:60],)


# --- mechanism taxonomy (D-15) ----------------------------------------------------------------


def _classify_mechanisms(
    normalized: NormalizedText, rules: RuleTables
) -> tuple[str, tuple[str, ...], float, tuple[str, ...]]:
    """Returns `(primary_mechanism, secondary_mechanisms, confidence, evidence_phrases)`. Negated
    matches are discarded before scoring (D-13). Confidence tiers: exact multi-word phrase 0.90,
    single distinguishing word 0.75; deductions: redaction-adjacent match -0.20, narrative under
    `SHORT_NARRATIVE_TOKEN_THRESHOLD` meaningful tokens -0.15. Mechanism is `unknown` when the
    best category's confidence is below `MECHANISM_UNKNOWN_THRESHOLD`.
    """
    if not normalized.text:
        return "unknown", (), 0.0, ()

    hits: dict[str, tuple[float, str]] = {}

    for category, phrases in rules.mechanisms.simple_phrase_categories.items():
        matches = match_phrases(phrases, category, normalized)
        positive = [m for m in matches if not m.negated]
        if not positive:
            continue
        m = positive[0]
        conf = _base_confidence(m.phrase)
        if is_redaction_adjacent(normalized, m.span):
            conf -= 0.20
        hits[category] = (conf, m.phrase)

    # Subject-disambiguated ambiguous verbs (SLIPPED / CAUGHT) -- Pitfall 4.
    for cue, disamb in rules.mechanisms.subject_disambiguated.items():
        for m in match_phrases((cue,), cue, normalized):
            if m.negated:
                continue
            if cue == "CAUGHT" and normalized.text[m.span[0] : m.span[0] + 15].startswith(
                _BLADE_CAUGHT_PHRASES
            ):
                # rules-1.1.0: "CAUGHT ON/BY BLADE" is a more specific direct_cutting_contact
                # signal than the generic subject-disambiguated CAUGHT->pinch_crush/entanglement
                # rule -- let the cutting_verbs pass below own this match instead.
                continue
            prev = preceding_token(normalized, m.span[0])
            if prev in disamb.tool_subject_terms:
                category = disamb.tool_category
            elif prev in disamb.other_subject_terms:
                category = disamb.other_category
            else:
                continue
            conf = _base_confidence(m.phrase)
            if is_redaction_adjacent(normalized, m.span):
                conf -= 0.20
            if category not in hits or conf > hits[category][0]:
                hits[category] = (conf, m.phrase)

    # fall_trip requires a fall/trip cue AND a co-occurring tool-operation link (Pitfall 5).
    ft = rules.mechanisms.fall_trip
    ft_positive = [m for m in match_phrases(ft.phrases, "fall_trip", normalized) if not m.negated]
    if ft_positive and any(term in normalized.tokens for term in ft.link_terms):
        hits["fall_trip"] = (_base_confidence(ft_positive[0].phrase), ft_positive[0].phrase)

    # rules-1.1.0: generic cutting-injury verbs (LACERATED, CUT, SLICED, AMPUTATED, NICKED, ...)
    # count as direct_cutting_contact ONLY on cutting-tool cases (a tool-context term present
    # anywhere in the narrative), at MODERATE confidence -- never overrides an existing (more
    # specific, higher-tier) direct_cutting_contact match already found above.
    if "direct_cutting_contact" not in hits:
        cv = rules.mechanisms.cutting_verbs
        if context_present(cv.tool_context_terms, normalized):
            cv_positive = [
                m for m in match_phrases(cv.phrases, "cutting_verb", normalized) if not m.negated
            ]
            if cv_positive:
                m = cv_positive[0]
                conf = cv.confidence
                if is_redaction_adjacent(normalized, m.span):
                    conf -= 0.20
                hits["direct_cutting_contact"] = (conf, m.phrase)

    if not hits:
        return "unknown", (), 0.0, ()

    if len(normalized.tokens) < SHORT_NARRATIVE_TOKEN_THRESHOLD:
        hits = {cat: (conf - 0.15, phrase) for cat, (conf, phrase) in hits.items()}

    ordered = [cat for cat in rules.mechanisms.priority if cat in hits]
    primary = ordered[0]
    primary_conf, primary_phrase = hits[primary]
    primary_conf = _clamp(primary_conf)
    if primary_conf < MECHANISM_UNKNOWN_THRESHOLD:
        return "unknown", (), primary_conf, ()

    secondary = tuple(ordered[1:])
    return primary, secondary, primary_conf, (primary_phrase[:60],)


def _classify_activity(normalized: NormalizedText, rules: RuleTables) -> str:
    if not normalized.text:
        return "unknown"
    for category, phrases in rules.activities.items():
        if any(not m.negated for m in match_phrases(phrases, category, normalized)):
            return category
    return "unknown"


def _classify_ppe_guard(normalized: NormalizedText, rules: RuleTables) -> tuple[str, str]:
    if not normalized.text:
        return "not_mentioned", "not_mentioned"
    pg = rules.ppe_guard

    def _hit(phrases: tuple[str, ...]) -> bool:
        # Unlike other rule tables, PPE/guard phrases are self-contained literal cues that
        # already encode their own polarity ("NO EYE PROTECTION", "WITHOUT GUARD") -- several
        # of them start with a word (NO/WITHOUT) that normalize.py's generic negation window
        # also treats as a negation cue. Applying that generic negation filter here would
        # discard the very phrase that IS the not-worn/absent signal, so PPE/guard matching
        # ignores `.negated` and keys only on whether the literal phrase is present.
        return len(match_phrases(phrases, "ppe_guard", normalized)) > 0

    if _hit(pg.ppe_not_worn):
        ppe = "mentioned_not_worn"
    elif _hit(pg.ppe_worn):
        ppe = "mentioned_worn"
    else:
        ppe = "not_mentioned"

    if _hit(pg.guard_absent):
        guard = "mentioned_absent_or_removed"
    elif _hit(pg.guard_present):
        guard = "mentioned_present"
    else:
        guard = "not_mentioned"

    return ppe, guard


def _classify_maintenance(normalized: NormalizedText, rules: RuleTables) -> bool:
    """True only when a maintenance cue matches, or a non-negated "UNPLUG*" mention co-occurs
    with a word-bounded tool word -- a bare "UNPLUG" with no tool context is NOT maintenance
    (research: several sampled "UNPLUG" narratives were unrelated household falls). Both the
    UNPLUG match and the tool-word match are word-boundary-bounded (`match_phrases`/`\\b`) so
    "STOOL"/"SAWDUST" substrings never falsely satisfy the tool-word side, and a negated UNPLUG
    ("DENIES UNPLUGGING...") never satisfies the UNPLUG side."""
    if not normalized.text:
        return False
    maint_cues = rules.mechanisms.simple_phrase_categories.get("maintenance_setup", ())
    if any(not m.negated for m in match_phrases(maint_cues, "maintenance", normalized)):
        return True
    unplug_hits = [m for m in match_phrases(UNPLUG_PHRASES, "unplug", normalized) if not m.negated]
    if unplug_hits and any(match_phrases((w,), "tool_word", normalized) for w in TOOL_WORDS):
        return True
    return False


# --- maintenance context / power state (D-14) --------------------------------------------------


def _classify_power_state(normalized: NormalizedText, rules: RuleTables) -> str:
    """Literal, explicit-only power-state mention. Uses the default negation-aware
    `match_phrases`/`.negated` shape (NOT the PPE/guard `_hit` exception, per Pitfall 6) --
    "UNPLUG" is a positive-polarity cue on its own, so a negated match ("DID NOT UNPLUG") must be
    discarded, never treated as evidence of the opposite state. Conflicting mentions (both an
    unplugged and a plugged-in phrase present) resolve to "not_mentioned" -- never guessed."""
    if not normalized.text:
        return "not_mentioned"
    unplugged_hits = [
        m
        for m in match_phrases(
            rules.mechanisms.power_unplugged_phrases, "power_unplugged", normalized
        )
        if not m.negated
    ]
    plugged_in_hits = [
        m
        for m in match_phrases(
            rules.mechanisms.power_plugged_in_phrases, "power_plugged_in", normalized
        )
        if not m.negated
    ]
    if unplugged_hits and not plugged_in_hits:
        return "mentioned_unplugged"
    if plugged_in_hits and not unplugged_hits:
        return "mentioned_plugged_in"
    return "not_mentioned"


def _classify_maintenance_context(
    normalized: NormalizedText, rules: RuleTables, base_flag: bool
) -> str:
    """`base_flag` is `_classify_maintenance`'s pre-existing (broader-recall) result. Take the
    first category in priority order with a non-negated phrase match; a category hit is only
    accepted as the context when a tool-context term also appears anywhere in the narrative, or
    `base_flag` is already True (the pre-existing maintenance cue/unplug+tool-word rule). This
    keeps the `maintenance_or_setup = (context != "not_maintenance")` invariant and never lowers
    recall versus today, because `base_flag` True always yields a non-"not_maintenance" context.
    """
    if not normalized.text:
        return "not_maintenance"
    mc = rules.mechanisms
    category_hit: str | None = None
    for category in mc.maintenance_context_priority:
        phrases = mc.maintenance_context_phrases.get(category, ())
        if phrases and any(not m.negated for m in match_phrases(phrases, category, normalized)):
            category_hit = category
            break
    tool_context = any(
        match_phrases((term,), "maintenance_tool_context", normalized)
        for term in mc.maintenance_tool_context_terms
    )
    if category_hit is not None and (tool_context or base_flag):
        return category_hit
    if base_flag:
        return "other_maintenance"
    return "not_maintenance"


def _workpiece_event_and_flags(
    normalized: NormalizedText, primary_mechanism: str, rules: RuleTables
) -> tuple[str | None, bool, bool, str | None]:
    n = normalized.text
    workpiece_event: str | None = None
    if primary_mechanism == "kickback_ejection":
        workpiece_event = "kickback"
    elif primary_mechanism == "workpiece_slip":
        workpiece_event = "slip"
    elif primary_mechanism == "flying_debris_fb":
        workpiece_event = "ejection"

    projectile_or_debris = (
        primary_mechanism == "flying_debris_fb" or "FLYING" in n or "PROJECTILE" in n
    )
    direct_tool_contact = primary_mechanism == "direct_cutting_contact" or "STRUCK BY BLADE" in n

    body_contact_context = None
    for term in rules.mechanisms.body_part_terms:
        if re.search(r"\b" + re.escape(term) + r"\b", n):
            body_contact_context = term.lower()
            break

    return workpiece_event, projectile_or_debris, direct_tool_contact, body_contact_context


# --- orchestration -----------------------------------------------------------------------------


def classify_case(
    narrative: str | None,
    product_codes: list[tuple[int, str]],
    dim_product_broad: set[int] | None = None,
) -> tuple[NarrativeEnrichment, list[ProductRefinement]]:
    """Classify one case's narrative. `product_codes` is `[(product_code, official_tool_id), ...]`
    for every `bridge_case_products` slot on this case. `dim_product_broad` overrides which codes
    are broad; defaults to the codes present in the loaded `subtypes.yaml` `broad_codes` table.
    Never reads `alcohol_code`/`drug_code` (D-16) -- the signature has no such parameter.
    """
    rules = _get_rules()
    broad_set = dim_product_broad if dim_product_broad is not None else set(rules.broad_codes)

    if not narrative or not narrative.strip():
        enrichment = _empty_enrichment()
        refinements = [
            ProductRefinement(
                product_code=code,
                official_tool_id=tool_id,
                narrative_refined_tool=None,
                final_tool_id=tool_id,
                refinement_confidence=0.0,
            )
            for code, tool_id in product_codes
        ]
        return enrichment, refinements

    normalized = normalize(narrative, rules.abbreviations)

    refinements: list[ProductRefinement] = []
    best_subtype = "unknown"
    best_subtype_confidence = 0.0
    best_subtype_evidence: tuple[str, ...] = ()

    for code, tool_id in product_codes:
        if code not in broad_set:
            refinements.append(
                ProductRefinement(
                    product_code=code,
                    official_tool_id=tool_id,
                    narrative_refined_tool=None,
                    final_tool_id=tool_id,
                    refinement_confidence=0.0,
                )
            )
            continue

        subtype, target, confidence, evidence = _classify_subtype(normalized, code, rules)
        if confidence > best_subtype_confidence:
            best_subtype = subtype
            best_subtype_confidence = confidence
            best_subtype_evidence = evidence

        gate_ok = target is not None and confidence >= REFINEMENT_CONFIDENCE_GATE
        refined = target if gate_ok else None
        final_tool = refined if refined is not None else tool_id
        refinements.append(
            ProductRefinement(
                product_code=code,
                official_tool_id=tool_id,
                narrative_refined_tool=refined,
                final_tool_id=final_tool,
                refinement_confidence=confidence,
            )
        )

    primary_mechanism, secondary_mechanisms, mech_confidence, mech_evidence = _classify_mechanisms(
        normalized, rules
    )
    activity = _classify_activity(normalized, rules)
    ppe_mentioned, guard_mentioned = _classify_ppe_guard(normalized, rules)
    base_maintenance_flag = _classify_maintenance(normalized, rules)
    maintenance_context = _classify_maintenance_context(normalized, rules, base_maintenance_flag)
    maintenance_or_setup = maintenance_context != "not_maintenance"
    power_state_mentioned = _classify_power_state(normalized, rules)
    workpiece_event, projectile_or_debris, direct_tool_contact, body_contact_context = (
        _workpiece_event_and_flags(normalized, primary_mechanism, rules)
    )

    overall_confidence = max(best_subtype_confidence, mech_confidence)
    evidence = (mech_evidence or best_subtype_evidence)[:3]

    enrichment = NarrativeEnrichment(
        tool_subtype=best_subtype,
        activity=activity,
        primary_mechanism=primary_mechanism,
        secondary_mechanisms=secondary_mechanisms,
        maintenance_or_setup=maintenance_or_setup,
        workpiece_event=workpiece_event,
        projectile_or_debris=projectile_or_debris,
        direct_tool_contact=direct_tool_contact,
        body_contact_context=body_contact_context,
        ppe_mentioned=ppe_mentioned,
        guard_mentioned=guard_mentioned,
        confidence=_clamp(overall_confidence),
        evidence=evidence,
        classification_source="rules",
        classifier_version=CLASSIFIER_VERSION,
        maintenance_context=maintenance_context,
        power_state_mentioned=power_state_mentioned,
    )
    return enrichment, refinements
