"""D-18/NARR-05 tests for the optional, env-gated AnthropicProvider and get_provider(). The
`anthropic` SDK is NEVER exercised against the network here -- every AnthropicProvider is
constructed with an injected fake client. Synthetic-only, matching test_classify.py's scope."""

from __future__ import annotations

import hashlib
import json
import sys
import types

import polars as pl
import pytest

from wii.narratives import CLASSIFIER_VERSION
from wii.narratives.enrich import enrich_cases
from wii.narratives.normalize import normalize
from wii.narratives.providers import (
    DEFAULT_MODEL,
    AnthropicProvider,
    RuleProvider,
    get_provider,
)

VALID_PAYLOAD = {
    "tool_subtype": "chop_saw_metal",
    "activity": "cutting",
    "primary_mechanism": "kickback_ejection",
    "secondary_mechanisms": ["direct_cutting_contact"],
    "maintenance_or_setup": False,
    "workpiece_event": "kickback",
    "projectile_or_debris": False,
    "direct_tool_contact": True,
    "body_contact_context": "hand",
    "ppe_mentioned": "not_mentioned",
    "guard_mentioned": "not_mentioned",
    "confidence": 0.82,
    "evidence": ["kicked back"],
}


class FakeToolUseBlock:
    def __init__(self, payload: dict):
        self.type = "tool_use"
        self.name = "classify_narrative"
        self.input = payload


class FakeResponse:
    def __init__(self, content):
        self.content = content


class FakeMessages:
    def __init__(self, payload: dict):
        self.calls: list[dict] = []
        self._payload = payload

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return FakeResponse([FakeToolUseBlock(dict(self._payload))])


class FakeClient:
    def __init__(self, payload: dict = VALID_PAYLOAD):
        self.messages = FakeMessages(payload)


# --- get_provider (D-18 env gating) -------------------------------------------------------------


def test_get_provider_returns_rule_provider_with_no_env():
    assert isinstance(get_provider({}), RuleProvider)


def test_get_provider_returns_rule_provider_with_only_one_env_var_set():
    assert isinstance(get_provider({"WII_LLM_PROVIDER": "anthropic"}), RuleProvider)
    assert isinstance(get_provider({"ANTHROPIC_API_KEY": "sk-fake"}), RuleProvider)
    assert isinstance(
        get_provider({"WII_LLM_PROVIDER": "other", "ANTHROPIC_API_KEY": "x"}), RuleProvider
    )


def test_get_provider_raises_clear_runtime_error_when_package_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)
    with pytest.raises(RuntimeError, match="uv sync --extra llm"):
        get_provider({"WII_LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "sk-fake"})


def test_importing_providers_succeeds_when_anthropic_not_importable(monkeypatch):
    """Simulates `anthropic` being uninstalled: forcing sys.modules['anthropic'] = None makes
    any `import anthropic` raise ImportError. Re-importing wii.narratives.providers under that
    condition must still succeed -- the module never imports anthropic at module scope."""
    monkeypatch.setitem(sys.modules, "anthropic", None)
    if "wii.narratives.providers" in sys.modules:
        del sys.modules["wii.narratives.providers"]
    import importlib

    reloaded = importlib.import_module("wii.narratives.providers")
    assert hasattr(reloaded, "AnthropicProvider")
    assert hasattr(reloaded, "get_provider")


def test_anthropic_provider_without_injected_client_raises_when_package_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)
    with pytest.raises(RuntimeError, match="uv sync --extra llm"):
        AnthropicProvider()


# --- AnthropicProvider.classify_narrative (fake client only) -----------------------------------


def test_calls_messages_create_once_with_expected_model_tool_and_tool_choice(tmp_path):
    client = FakeClient()
    provider = AnthropicProvider(client=client, cache_path=tmp_path / "cache.jsonl")

    record = provider.classify_narrative("45YOM HAND CAUGHT WHEN SAW KICKED BACK", [845])

    assert record is not None
    assert record.primary_mechanism == "kickback_ejection"
    assert record.classification_source == "llm"
    assert len(client.messages.calls) == 1
    call = client.messages.calls[0]
    assert call["model"] == DEFAULT_MODEL
    assert call["tools"][0]["name"] == "classify_narrative"
    assert call["tool_choice"] == {"type": "tool", "name": "classify_narrative"}


def test_model_is_overridable_via_constructor_matching_wii_llm_model_env(tmp_path):
    client = FakeClient()
    provider = AnthropicProvider(
        client=client, model="claude-other-model-id", cache_path=tmp_path / "cache.jsonl"
    )
    provider.classify_narrative("some narrative with enough tokens to normalize", [845])
    assert client.messages.calls[0]["model"] == "claude-other-model-id"


def test_invalid_payload_returns_none_and_writes_no_cache(tmp_path):
    bad_payload = {"primary_mechanism": "not_a_real_mechanism", "confidence": 0.5, "evidence": []}
    client = FakeClient(bad_payload)
    cache_path = tmp_path / "cache.jsonl"
    provider = AnthropicProvider(client=client, cache_path=cache_path)

    record = provider.classify_narrative("some narrative text", [845])

    assert record is None
    assert not cache_path.exists()


def test_empty_narrative_returns_none_and_never_calls_client(tmp_path):
    client = FakeClient()
    provider = AnthropicProvider(client=client, cache_path=tmp_path / "cache.jsonl")
    assert provider.classify_narrative("", [845]) is None
    assert provider.classify_narrative(None, [845]) is None
    assert len(client.messages.calls) == 0


def test_second_call_for_same_narrative_hits_cache_no_second_client_call(tmp_path):
    client = FakeClient()
    cache_path = tmp_path / "cache.jsonl"
    provider = AnthropicProvider(client=client, cache_path=cache_path)

    first = provider.classify_narrative("45YOM HAND CAUGHT WHEN SAW KICKED BACK", [845])
    second = provider.classify_narrative("45YOM HAND CAUGHT WHEN SAW KICKED BACK", [845])

    assert len(client.messages.calls) == 1
    assert first == second


def test_cache_file_content_never_includes_the_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake-secret-never-appears-0000000000")
    client = FakeClient()
    cache_path = tmp_path / "cache.jsonl"
    provider = AnthropicProvider(client=client, cache_path=cache_path)

    provider.classify_narrative("45YOM HAND CAUGHT WHEN SAW KICKED BACK", [845])

    content = cache_path.read_text()
    assert "sk-test-fake-secret-never-appears-0000000000" not in content
    entry = json.loads(content.strip().splitlines()[0])
    assert set(entry.keys()) == {"hash", "model", "classifier_version", "prompt_version", "fields"}
    assert entry["model"] == DEFAULT_MODEL
    assert entry["classifier_version"] == CLASSIFIER_VERSION


# --- findings 7/8: cache versioning + validation, auth errors, api_key threading -------------


def test_stale_classifier_version_cache_entry_is_not_reused(tmp_path):
    """Finding 7: the cache key must include classifier_version -- an entry written under a
    different (older) classifier_version must not be served, even on a hash+model match."""
    cache_path = tmp_path / "cache.jsonl"
    narrative = "45YOM HAND CAUGHT WHEN SAW KICKED BACK"
    narrative_hash = hashlib.sha256(normalize(narrative).text.encode("utf-8")).hexdigest()
    stale_entry = {
        "hash": narrative_hash,
        "model": DEFAULT_MODEL,
        "classifier_version": "rules-0.0.1-stale",
        "fields": {
            **VALID_PAYLOAD,
            "classification_source": "llm",
            "classifier_version": "rules-0.0.1-stale",
        },
    }
    cache_path.write_text(json.dumps(stale_entry) + "\n")

    client = FakeClient()
    provider = AnthropicProvider(client=client, cache_path=cache_path)
    record = provider.classify_narrative(narrative, [845])

    assert record is not None
    assert len(client.messages.calls) == 1  # cache MISS -- fresh API call made


def test_invalid_cached_payload_is_ignored_not_used(tmp_path):
    """Finding 7: a cached payload must pass validate_llm_payload before being trusted -- a
    structurally invalid cached entry (matching hash+model+classifier_version) must be treated
    as a cache miss, not returned as-is."""
    cache_path = tmp_path / "cache.jsonl"
    narrative = "45YOM HAND CAUGHT WHEN SAW KICKED BACK"
    narrative_hash = hashlib.sha256(normalize(narrative).text.encode("utf-8")).hexdigest()
    bad_fields = {**VALID_PAYLOAD, "primary_mechanism": "not_a_real_mechanism"}
    entry = {
        "hash": narrative_hash,
        "model": DEFAULT_MODEL,
        "classifier_version": CLASSIFIER_VERSION,
        "fields": bad_fields,
    }
    cache_path.write_text(json.dumps(entry) + "\n")

    client = FakeClient()  # returns a VALID payload
    provider = AnthropicProvider(client=client, cache_path=cache_path)
    record = provider.classify_narrative(narrative, [845])

    assert record is not None
    assert record.primary_mechanism == "kickback_ejection"
    assert len(client.messages.calls) == 1  # bad cache entry was not trusted


def test_authentication_error_raises_instead_of_silently_falling_back(tmp_path, monkeypatch):
    """Finding 8: an authentication/permission error must fail loudly (raise), never be
    swallowed into a silent rules fallback like a transient network error."""

    class FakeAuthenticationError(Exception):
        pass

    class FakePermissionDeniedError(Exception):
        pass

    fake_anthropic = types.ModuleType("anthropic")
    fake_anthropic.AuthenticationError = FakeAuthenticationError
    fake_anthropic.PermissionDeniedError = FakePermissionDeniedError
    monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)

    class RaisingMessages:
        def create(self, **kwargs):
            raise FakeAuthenticationError("invalid x-api-key")

    class RaisingClient:
        messages = RaisingMessages()

    provider = AnthropicProvider(client=RaisingClient(), cache_path=tmp_path / "cache.jsonl")
    with pytest.raises(RuntimeError, match="[Aa]uth"):
        provider.classify_narrative("some narrative with enough tokens", [845])


def test_non_auth_api_error_still_falls_back_and_is_counted(tmp_path):
    """Non-auth failures (network blips, etc.) must still fall back to None (never raise) but
    must be counted so silent-swallow can't hide a systemic problem."""

    class RaisingMessages:
        def create(self, **kwargs):
            raise RuntimeError("connection reset")

    class RaisingClient:
        messages = RaisingMessages()

    provider = AnthropicProvider(client=RaisingClient(), cache_path=tmp_path / "cache.jsonl")
    record = provider.classify_narrative("some narrative with enough tokens", [845])

    assert record is None
    assert provider.fallback_count == 1


def test_get_provider_threads_api_key_from_env_mapping_into_anthropic_client(monkeypatch):
    """Finding 8: the client must be constructed with the api_key taken from the SAME `env`
    mapping get_provider validated -- not implicitly from os.environ, which may differ from a
    caller-supplied env dict (e.g. in tests, or a scoped config load)."""
    captured = {}

    class FakeAnthropicClient:
        def __init__(self, api_key=None):
            captured["api_key"] = api_key

    fake_anthropic = types.ModuleType("anthropic")
    fake_anthropic.Anthropic = FakeAnthropicClient
    monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic)

    provider = get_provider(
        {"WII_LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "sk-from-env-mapping"}
    )
    assert isinstance(provider, AnthropicProvider)
    assert captured["api_key"] == "sk-from-env-mapping"


# --- enrich_cases integration: LLM consulted only for confidence < 0.50, never owns refinement --


@pytest.fixture
def two_case_fixture():
    fact_cases = pl.DataFrame(
        {
            "case_id": ["low_conf_non_broad", "low_conf_broad"],
            "year": [2024, 2024],
            "narrative": ["LAC FINGER", "45YOM INJURED WHILE WORKING"],
        }
    )
    bridge_products = pl.DataFrame(
        {
            "case_id": ["low_conf_non_broad", "low_conf_broad"],
            "product_code": [841, 899],
            "seq": [1, 1],
            "is_primary": [True, True],
            "tool_id": ["table_saw", "grinder_unspecified"],
            "family_id": ["saws", "abrasive"],
            "in_scope": [True, True],
            "is_adjacent": [False, False],
            "narrative_refined_tool": [None, None],
            "final_tool_id": ["table_saw", "grinder_unspecified"],
            "classification_confidence": [None, None],
        },
        schema_overrides={"classification_confidence": pl.Float32},
    )
    dim_product = pl.DataFrame({"product_code": [841, 899], "broad": [False, True]})
    return fact_cases, bridge_products, dim_product


def test_llm_refinement_never_overrides_tool_provenance_broad_or_non_broad(
    two_case_fixture, tmp_path
):
    """Both cases classify at low rules confidence and are routed to the LLM consultant, which
    returns a valid response -- but bridge_case_products provenance (narrative_refined_tool,
    final_tool_id) must be byte-identical to a rules-only run for BOTH cases: the non-broad code
    (841, NARR-02: never overridden) and the broad code (899, D-18: the LLM never decides
    refinement at all, only classify_case does)."""
    fact_cases, bridge_products, dim_product = two_case_fixture

    rules_only = enrich_cases(fact_cases, bridge_products, dim_product, provider=None)

    client = FakeClient()
    provider = AnthropicProvider(client=client, cache_path=tmp_path / "cache.jsonl")
    with_llm = enrich_cases(fact_cases, bridge_products, dim_product, provider=provider)

    # The LLM was actually consulted (both cases have 0.0 rules confidence).
    assert len(client.messages.calls) == 2

    for case_id in ("low_conf_non_broad", "low_conf_broad"):
        rules_row = rules_only.bridge_products.filter(pl.col("case_id") == case_id).row(
            0, named=True
        )
        llm_row = with_llm.bridge_products.filter(pl.col("case_id") == case_id).row(0, named=True)
        assert llm_row["narrative_refined_tool"] == rules_row["narrative_refined_tool"]
        assert llm_row["final_tool_id"] == rules_row["final_tool_id"]

    enrichment = with_llm.enrichment
    for case_id in ("low_conf_non_broad", "low_conf_broad"):
        row = enrichment.filter(pl.col("case_id") == case_id).row(0, named=True)
        assert row["classification_source"] == "llm"
        assert row["primary_mechanism"] == "kickback_ejection"


def test_llm_not_consulted_when_rules_confidence_already_high(tmp_path):
    """A narrative with an unambiguous rules match (confidence >= 0.70) must never reach the
    LLM consultant at all."""
    fact_cases = pl.DataFrame(
        {"case_id": ["c1"], "year": [2024], "narrative": ["43YOM USING A MITER SAW CUT FINGER"]}
    )
    bridge_products = pl.DataFrame(
        {
            "case_id": ["c1"],
            "product_code": [895],
            "seq": [1],
            "is_primary": [True],
            "tool_id": ["other_power_saw"],
            "family_id": ["saws"],
            "in_scope": [True],
            "is_adjacent": [False],
            "narrative_refined_tool": [None],
            "final_tool_id": ["other_power_saw"],
            "classification_confidence": [None],
        },
        schema_overrides={"classification_confidence": pl.Float32},
    )
    dim_product = pl.DataFrame({"product_code": [895], "broad": [True]})

    client = FakeClient()
    provider = AnthropicProvider(client=client, cache_path=tmp_path / "cache.jsonl")
    result = enrich_cases(fact_cases, bridge_products, dim_product, provider=provider)

    assert len(client.messages.calls) == 0
    row = result.enrichment.row(0, named=True)
    assert row["classification_source"] == "rules"
