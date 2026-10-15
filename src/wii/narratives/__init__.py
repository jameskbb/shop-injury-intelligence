"""wii.narratives -- deterministic, versioned rule-based narrative classifier (D-12).

Preprocessing only: this package is never imported by the Streamlit app. It turns CPSC NEISS
incident narratives into typed `NarrativeEnrichment` records (tool subtype, mechanism, activity,
PPE/guard mentions, confidence, evidence) with full provenance.
"""

from __future__ import annotations

CLASSIFIER_VERSION = "rules-1.2.0"
