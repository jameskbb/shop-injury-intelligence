"""D-13/D-14 YAML rule-table loader and phrase matcher.

`load_rules` validates every target tool named in `subtypes.yaml` against the official
`tool_id` set (from `wii.taxonomy`) unioned with the 4 refined tools (D-14) -- errors name the
full key path, matching the `wii.taxonomy.loader` validated-loader pattern this module copies.
Patterns may never match the redaction placeholder itself: `match_phrases` uses `\\b`-bounded
regexes, and `normalize.py` glues `ZREDZ` directly to its neighbours with no inserted spaces, so
a word boundary never falls at the placeholder/word join.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from wii.narratives.normalize import NormalizedText, token_spans
from wii.narratives.schema import MAINTENANCE_CONTEXTS, MECHANISM_PRIORITY

CONFIG_DIR = Path(__file__).resolve().parent / "config"

REFINED_TOOLS = frozenset({"miter_saw", "planer", "angle_grinder", "bench_grinder"})


class RuleConfigError(Exception):
    """Raised for any structural problem in a narratives config YAML. Message names the key
    path."""


def _load_yaml(path: Path) -> dict:
    if not path.is_file():
        raise RuleConfigError(f"{path.name}: config file not found: {path}")
    with open(path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    if not doc:
        raise RuleConfigError(f"{path.name}: file is empty")
    return doc


@dataclass(frozen=True)
class SubtypePattern:
    pattern_id: str
    phrases: tuple[str, ...]
    subtype: str
    target: str | None  # refinement target tool_id, or None for subtype-only (no refinement)
    confidence: float
    require_context: str | None = None
    exclude_context: str | None = None


@dataclass(frozen=True)
class BroadCodeRules:
    allowed_targets: tuple[str, ...]
    patterns: tuple[SubtypePattern, ...]


@dataclass(frozen=True)
class SubjectDisambiguatedCue:
    """A verb cue (e.g. "SLIPPED", "CAUGHT") whose mechanism category depends on the nearest
    preceding subject noun (D-15 subject-disambiguation, Pitfall 4)."""

    cue: str
    tool_category: str
    other_category: str
    tool_subject_terms: tuple[str, ...]
    other_subject_terms: tuple[str, ...]


@dataclass(frozen=True)
class FallTripRules:
    phrases: tuple[str, ...]
    link_terms: tuple[str, ...]


@dataclass(frozen=True)
class CuttingVerbRules:
    """rules-1.1.0: generic cutting-injury verbs (LACERATED, CUT, SLICED, AMPUTATED, NICKED, ...),
    valid as direct_cutting_contact evidence ONLY alongside a cutting-tool context term."""

    confidence: float
    phrases: tuple[str, ...]
    tool_context_terms: tuple[str, ...]


@dataclass(frozen=True)
class MechanismRules:
    priority: tuple[str, ...]
    simple_phrase_categories: dict[str, tuple[str, ...]]
    subject_disambiguated: dict[str, SubjectDisambiguatedCue]
    fall_trip: FallTripRules
    body_part_terms: tuple[str, ...]
    cutting_verbs: CuttingVerbRules
    # D-14: maintenance/setup context + literal power-state phrase tables. Deliberately separate
    # from simple_phrase_categories -- mechanism scoring never reads these, so primary_mechanism
    # cannot change as a side effect of this extension.
    maintenance_context_priority: tuple[str, ...]
    maintenance_context_phrases: dict[str, tuple[str, ...]]
    maintenance_tool_context_terms: tuple[str, ...]
    power_unplugged_phrases: tuple[str, ...]
    power_plugged_in_phrases: tuple[str, ...]


@dataclass(frozen=True)
class PpeGuardRules:
    ppe_worn: tuple[str, ...]
    ppe_not_worn: tuple[str, ...]
    guard_present: tuple[str, ...]
    guard_absent: tuple[str, ...]


@dataclass(frozen=True)
class RuleTables:
    broad_codes: dict[int, BroadCodeRules]
    context_terms: dict[str, tuple[str, ...]]
    abbreviations: dict[str, str]
    mechanisms: MechanismRules
    activities: dict[str, tuple[str, ...]]
    ppe_guard: PpeGuardRules


def _official_tool_ids(official_tool_ids: frozenset[str] | None) -> frozenset[str]:
    if official_tool_ids is not None:
        return official_tool_ids
    from wii.taxonomy.loader import load_product_taxonomy

    tax = load_product_taxonomy()
    return frozenset(tax.tools.keys())


def _parse_pattern(
    pdoc: dict, code: int, valid_targets: frozenset[str], context_terms: dict
) -> SubtypePattern:
    for required in ("id", "phrases", "subtype", "confidence"):
        if required not in pdoc:
            raise RuleConfigError(
                f"subtypes.broad_codes.{code}.patterns[{pdoc.get('id', '?')}].{required} missing"
            )
    target = pdoc.get("target")
    if target is not None and target not in valid_targets:
        raise RuleConfigError(
            f"subtypes.broad_codes.{code}.patterns[{pdoc['id']}].target references unknown "
            f"tool {target!r}"
        )
    require_ctx = pdoc.get("require_context")
    exclude_ctx = pdoc.get("exclude_context")
    for ctx_name in (require_ctx, exclude_ctx):
        if ctx_name is not None and ctx_name not in context_terms:
            raise RuleConfigError(
                f"subtypes.broad_codes.{code}.patterns[{pdoc['id']}] references unknown "
                f"context {ctx_name!r}"
            )
    return SubtypePattern(
        pattern_id=pdoc["id"],
        phrases=tuple(pdoc["phrases"]),
        subtype=pdoc["subtype"],
        target=target,
        confidence=float(pdoc["confidence"]),
        require_context=require_ctx,
        exclude_context=exclude_ctx,
    )


def _load_mechanisms(cdir: Path) -> MechanismRules:
    doc = _load_yaml(cdir / "mechanisms.yaml")
    for key in (
        "priority",
        "simple_phrase_categories",
        "subject_disambiguated",
        "fall_trip",
        "maintenance_context",
        "power_state",
    ):
        if key not in doc:
            raise RuleConfigError(f"mechanisms.{key} missing")

    priority = tuple(doc["priority"])
    if priority != MECHANISM_PRIORITY:
        raise RuleConfigError(
            f"mechanisms.priority {list(priority)} != schema.MECHANISM_PRIORITY "
            f"{list(MECHANISM_PRIORITY)}"
        )

    simple = {name: tuple(phrases) for name, phrases in doc["simple_phrase_categories"].items()}
    for name in simple:
        if name not in MECHANISM_PRIORITY:
            raise RuleConfigError(
                f"mechanisms.simple_phrase_categories.{name} is not a known mechanism"
            )

    subject_disambiguated = {}
    for cue, cdoc in doc["subject_disambiguated"].items():
        for required in (
            "tool_category",
            "other_category",
            "tool_subject_terms",
            "other_subject_terms",
        ):
            if required not in cdoc:
                raise RuleConfigError(f"mechanisms.subject_disambiguated.{cue}.{required} missing")
        for cat in (cdoc["tool_category"], cdoc["other_category"]):
            if cat not in MECHANISM_PRIORITY:
                raise RuleConfigError(
                    f"mechanisms.subject_disambiguated.{cue} references unknown mechanism {cat!r}"
                )
        subject_disambiguated[cue] = SubjectDisambiguatedCue(
            cue=cue,
            tool_category=cdoc["tool_category"],
            other_category=cdoc["other_category"],
            tool_subject_terms=tuple(cdoc["tool_subject_terms"]),
            other_subject_terms=tuple(cdoc["other_subject_terms"]),
        )

    fall_trip_doc = doc["fall_trip"]
    for required in ("phrases", "link_terms"):
        if required not in fall_trip_doc:
            raise RuleConfigError(f"mechanisms.fall_trip.{required} missing")
    fall_trip = FallTripRules(
        phrases=tuple(fall_trip_doc["phrases"]), link_terms=tuple(fall_trip_doc["link_terms"])
    )

    cv_doc = doc.get("cutting_verbs", {})
    for required in ("confidence", "phrases", "tool_context_terms"):
        if required not in cv_doc:
            raise RuleConfigError(f"mechanisms.cutting_verbs.{required} missing")
    cutting_verbs = CuttingVerbRules(
        confidence=float(cv_doc["confidence"]),
        phrases=tuple(cv_doc["phrases"]),
        tool_context_terms=tuple(cv_doc["tool_context_terms"]),
    )

    mc_doc = doc["maintenance_context"]
    for required in ("priority", "tool_context_terms", "phrases"):
        if required not in mc_doc:
            raise RuleConfigError(f"mechanisms.maintenance_context.{required} missing")
    maintenance_categories = MAINTENANCE_CONTEXTS - {"not_maintenance"}
    mc_priority = tuple(mc_doc["priority"])
    for category in mc_priority:
        if category not in maintenance_categories:
            raise RuleConfigError(
                f"mechanisms.maintenance_context.priority references unknown category {category!r}"
            )
    maintenance_context_phrases: dict[str, tuple[str, ...]] = {}
    for category, phrases in mc_doc["phrases"].items():
        if category not in maintenance_categories:
            raise RuleConfigError(
                f"mechanisms.maintenance_context.phrases.{category} is not a known "
                f"maintenance context"
            )
        maintenance_context_phrases[category] = tuple(phrases)

    ps_doc = doc["power_state"]
    for required in ("unplugged", "plugged_in"):
        if required not in ps_doc:
            raise RuleConfigError(f"mechanisms.power_state.{required} missing")

    return MechanismRules(
        priority=priority,
        simple_phrase_categories=simple,
        subject_disambiguated=subject_disambiguated,
        fall_trip=fall_trip,
        body_part_terms=tuple(doc.get("body_part_terms", ())),
        cutting_verbs=cutting_verbs,
        maintenance_context_priority=mc_priority,
        maintenance_context_phrases=maintenance_context_phrases,
        maintenance_tool_context_terms=tuple(mc_doc["tool_context_terms"]),
        power_unplugged_phrases=tuple(ps_doc["unplugged"]),
        power_plugged_in_phrases=tuple(ps_doc["plugged_in"]),
    )


def _load_activities(cdir: Path) -> dict[str, tuple[str, ...]]:
    doc = _load_yaml(cdir / "activity.yaml")
    if "categories" not in doc:
        raise RuleConfigError("activity.categories missing")
    return {name: tuple(phrases) for name, phrases in doc["categories"].items()}


def _load_ppe_guard(cdir: Path) -> PpeGuardRules:
    doc = _load_yaml(cdir / "ppe_guard.yaml")
    for key in ("ppe", "guard"):
        if key not in doc:
            raise RuleConfigError(f"ppe_guard.{key} missing")
    for key in ("worn", "not_worn"):
        if key not in doc["ppe"]:
            raise RuleConfigError(f"ppe_guard.ppe.{key} missing")
    for key in ("present", "absent"):
        if key not in doc["guard"]:
            raise RuleConfigError(f"ppe_guard.guard.{key} missing")
    return PpeGuardRules(
        ppe_worn=tuple(doc["ppe"]["worn"]),
        ppe_not_worn=tuple(doc["ppe"]["not_worn"]),
        guard_present=tuple(doc["guard"]["present"]),
        guard_absent=tuple(doc["guard"]["absent"]),
    )


def load_rules(
    config_dir: Path | None = None, *, official_tool_ids: frozenset[str] | None = None
) -> RuleTables:
    cdir = config_dir or CONFIG_DIR
    subtypes_doc = _load_yaml(cdir / "subtypes.yaml")
    abbrev_doc = _load_yaml(cdir / "abbreviations.yaml")
    mechanisms = _load_mechanisms(cdir)
    activities = _load_activities(cdir)
    ppe_guard = _load_ppe_guard(cdir)

    for key in ("refined_tools", "broad_codes"):
        if key not in subtypes_doc:
            raise RuleConfigError(f"subtypes.{key} missing")

    declared_refined = frozenset(subtypes_doc["refined_tools"].keys())
    if declared_refined != REFINED_TOOLS:
        raise RuleConfigError(
            f"subtypes.refined_tools {sorted(declared_refined)} != expected {sorted(REFINED_TOOLS)}"
        )

    valid_targets = _official_tool_ids(official_tool_ids) | REFINED_TOOLS

    context_doc = subtypes_doc.get("context_terms", {})
    context_terms: dict[str, tuple[str, ...]] = {
        name: tuple(terms) for name, terms in context_doc.items()
    }

    broad_codes: dict[int, BroadCodeRules] = {}
    for code_key, cdoc in subtypes_doc["broad_codes"].items():
        code = int(code_key)
        if "allowed_targets" not in cdoc:
            raise RuleConfigError(f"subtypes.broad_codes.{code}.allowed_targets missing")
        allowed = tuple(cdoc["allowed_targets"])
        for t in allowed:
            if t not in valid_targets:
                raise RuleConfigError(
                    f"subtypes.broad_codes.{code}.allowed_targets references unknown tool {t!r}"
                )
        patterns = tuple(
            _parse_pattern(pdoc, code, valid_targets, context_terms)
            for pdoc in cdoc.get("patterns", [])
        )
        broad_codes[code] = BroadCodeRules(allowed_targets=allowed, patterns=patterns)

    abbreviations = {str(k): str(v) for k, v in abbrev_doc.items() if k != "version"}

    return RuleTables(
        broad_codes=broad_codes,
        context_terms=context_terms,
        abbreviations=abbreviations,
        mechanisms=mechanisms,
        activities=activities,
        ppe_guard=ppe_guard,
    )


def load_refined_tool_defs(config_dir: Path | None = None) -> dict[str, dict[str, str]]:
    """D-14: `label`/`family_id` for the 4 narrative-refined tools, from `subtypes.yaml`
    `refined_tools`. Used by `wii.narratives.enrich` to build their `dim_tool` rows."""
    cdir = config_dir or CONFIG_DIR
    doc = _load_yaml(cdir / "subtypes.yaml")
    return {
        tool_id: {"label": d["label"], "family_id": d["family_id"]}
        for tool_id, d in doc["refined_tools"].items()
    }


@dataclass(frozen=True)
class PhraseMatch:
    pattern_id: str
    phrase: str
    span: tuple[int, int]
    negated: bool


def match_phrases(
    phrases: tuple[str, ...], pattern_id: str, normalized: NormalizedText
) -> list[PhraseMatch]:
    """Find every occurrence of any of `phrases` in `normalized.text`, word-bounded. A match
    spanning any negated token is reported with `negated=True` -- callers must discard those
    before treating a match as positive evidence (D-13)."""
    if not normalized.text:
        return []
    spans = token_spans(normalized.text)
    out: list[PhraseMatch] = []
    for phrase in phrases:
        pattern = re.compile(r"\b" + re.escape(phrase) + r"\b")
        for m in pattern.finditer(normalized.text):
            s, e = m.span()
            negated = any(
                ts < e and te > s and normalized.negated_mask[i] for i, (ts, te) in enumerate(spans)
            )
            out.append(
                PhraseMatch(pattern_id=pattern_id, phrase=phrase, span=(s, e), negated=negated)
            )
    return out


def preceding_token(normalized: NormalizedText, span_start: int) -> str | None:
    """The token immediately before character offset `span_start` in `normalized.text`, or None
    if the match is at the start of the narrative. Used for D-15 subject disambiguation
    ("SLIPPED"/"CAUGHT" -- nearest preceding noun decides the mechanism category)."""
    spans = token_spans(normalized.text)
    prev = None
    for ts, te in spans:
        if te <= span_start:
            prev = normalized.text[ts:te]
        else:
            break
    return prev


def is_redaction_adjacent(normalized: NormalizedText, span: tuple[int, int]) -> bool:
    """True if the matched span sits inside a token that also contains the redaction
    placeholder (e.g. a phrase glued directly to a `***`-redacted brand name)."""
    from wii.narratives.normalize import REDACTION_PLACEHOLDER

    for ts, te in token_spans(normalized.text):
        if ts < span[1] and te > span[0]:
            if REDACTION_PLACEHOLDER in normalized.text[ts:te]:
                return True
    return False


def context_present(terms: tuple[str, ...], normalized: NormalizedText) -> bool:
    """True if any of `terms` appears anywhere (word-bounded) in the normalized narrative,
    regardless of negation -- context terms scope subtype disambiguation, they are not
    themselves treated as positive evidence."""
    if not normalized.text or not terms:
        return False
    return any(re.search(r"\b" + re.escape(t) + r"\b", normalized.text) for t in terms)
