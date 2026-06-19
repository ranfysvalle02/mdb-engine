"""Unit tests for the LLM Model Capability Registry."""

from mdb_engine.llm.capabilities import (
    ModelCapabilities,
    default_grounding_model,
    filter_registry,
    list_registry,
    resolve_capabilities,
)


class TestResolveCurated:
    """Exact / curated registry resolution."""

    def test_gemini_25_flash_grounds(self):
        caps = resolve_capabilities("gemini/gemini-2.5-flash")
        assert caps.web_search is True
        assert caps.thinking is True
        assert caps.provider == "gemini"
        assert caps.family == "gemini-2.5"

    def test_gemini_flash_latest_does_not_ground(self):
        # The hard-won truth this whole effort is built around.
        assert resolve_capabilities("gemini/gemini-flash-latest").web_search is False

    def test_gemini_pro_latest_does_not_ground(self):
        assert resolve_capabilities("gemini/gemini-pro-latest").web_search is False

    def test_openai_gpt4o_no_web_search(self):
        caps = resolve_capabilities("openai/gpt-4o")
        assert caps.web_search is False
        assert caps.thinking is False
        assert caps.provider == "openai"


class TestResolveHeuristics:
    """Family heuristics for models not in the curated map."""

    def test_unknown_gemini_25_grounds(self):
        caps = resolve_capabilities("gemini/gemini-2.5-flash-preview-09-2025")
        assert caps.web_search is True
        assert caps.family == "gemini-2.5"

    def test_any_latest_alias_does_not_ground(self):
        # Even an unseen -latest alias must default to no grounding.
        caps = resolve_capabilities("gemini/gemini-ultra-latest")
        assert caps.web_search is False
        assert caps.family == "gemini-latest"

    def test_bare_model_id_infers_provider(self):
        caps = resolve_capabilities("gpt-4o")
        assert caps.provider == "openai"
        assert caps.model == "openai/gpt-4o"

    def test_vertex_ai_prefix_maps_to_gemini(self):
        caps = resolve_capabilities("vertex_ai/gemini-2.5-pro")
        assert caps.provider == "gemini"
        assert caps.web_search is True

    def test_openai_reasoning_family_thinks(self):
        assert resolve_capabilities("openai/o3-mini").thinking is True

    def test_always_returns_value(self):
        caps = resolve_capabilities("totally/made-up-model")
        assert isinstance(caps, ModelCapabilities)


class TestOverrides:
    """Manifest overrides layered on top of resolution."""

    def test_override_by_canonical_id(self):
        overrides = {"gemini/gemini-flash-latest": {"web_search": True}}
        assert resolve_capabilities("gemini/gemini-flash-latest", overrides).web_search is True

    def test_override_by_bare_id(self):
        overrides = {"gemini-flash-latest": {"web_search": True}}
        assert resolve_capabilities("gemini/gemini-flash-latest", overrides).web_search is True

    def test_override_ignores_unknown_keys(self):
        overrides = {"gemini/gemini-2.5-flash": {"bogus_field": 123, "web_search": False}}
        caps = resolve_capabilities("gemini/gemini-2.5-flash", overrides)
        assert caps.web_search is False
        assert not hasattr(caps, "bogus_field")

    def test_override_cannot_change_model_id(self):
        overrides = {"gemini/gemini-2.5-flash": {"model": "evil/model"}}
        assert resolve_capabilities("gemini/gemini-2.5-flash", overrides).model == "gemini/gemini-2.5-flash"


class TestFilterRegistry:
    def test_filter_web_search_true(self):
        models = filter_registry(provider="gemini", web_search=True)
        assert models
        assert all(m.web_search and m.provider == "gemini" for m in models)
        assert all("latest" not in m.model for m in models)

    def test_filter_thinking(self):
        models = filter_registry(thinking=True)
        assert all(m.thinking for m in models)

    def test_filter_with_overrides(self):
        overrides = {"gemini/gemini-flash-latest": {"web_search": True}}
        ids = {m.model for m in filter_registry(provider="gemini", web_search=True, overrides=overrides)}
        assert "gemini/gemini-flash-latest" in ids

    def test_list_registry_nonempty(self):
        assert len(list_registry()) >= 5


class TestDefaultGroundingModel:
    def test_gemini_default(self):
        assert default_grounding_model("gemini") == "gemini/gemini-2.5-flash"

    def test_openai_has_no_default(self):
        assert default_grounding_model("openai") is None
