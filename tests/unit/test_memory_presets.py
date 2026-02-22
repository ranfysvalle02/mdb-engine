"""
Unit tests for memory presets and embedding dimension resolution.
"""

import copy

import pytest

from mdb_engine.memory.presets import (
    KNOWN_EMBEDDING_DIMS,
    MEMORY_PRESETS,
    _deep_merge,
    resolve_embedding_dims,
    resolve_memory_preset,
)


class TestResolveMemoryPreset:
    """Tests for resolve_memory_preset()."""

    def test_true_returns_enabled_dict(self):
        result = resolve_memory_preset(True)
        assert result == {"enabled": True}

    def test_string_basic(self):
        result = resolve_memory_preset("basic")
        assert result["enabled"] is True
        assert result.get("enable_cognitive") is False

    def test_string_smart(self):
        result = resolve_memory_preset("smart")
        assert result["enabled"] is True
        assert result["enable_cognitive"] is True
        assert result["salience_gate"] is True
        assert result["categories"]["enabled"] is True

    def test_string_full(self):
        result = resolve_memory_preset("full")
        assert result["enabled"] is True
        assert result["reflection"]["enabled"] is True
        assert result["graph"]["enabled"] is True
        assert result["cognitive"]["enabled"] is True

    def test_unknown_preset_falls_back_to_basic(self):
        result = resolve_memory_preset("nonexistent")
        assert result["enabled"] is True
        assert result == MEMORY_PRESETS["basic"]

    def test_dict_with_preset_key(self):
        result = resolve_memory_preset({"preset": "smart", "max_depth": 500})
        assert result["enabled"] is True
        assert result["enable_cognitive"] is True
        assert result["max_depth"] == 500

    def test_dict_without_preset(self):
        original = {"enabled": True, "infer": False}
        result = resolve_memory_preset(original)
        assert result is original

    def test_preset_override_deep_merges(self):
        result = resolve_memory_preset(
            {
                "preset": "full",
                "cognitive": {"pruning": {"max_capacity": 5000}},
            }
        )
        assert result["cognitive"]["enabled"] is True
        assert result["cognitive"]["pruning"]["max_capacity"] == 5000
        assert result["cognitive"]["emotion"]["enabled"] is True

    def test_returns_deep_copy(self):
        result1 = resolve_memory_preset("smart")
        result2 = resolve_memory_preset("smart")
        result1["max_depth"] = 999
        assert "max_depth" not in result2

    def test_non_dict_non_string_returns_enabled(self):
        result = resolve_memory_preset(42)  # type: ignore[arg-type]
        assert result == {"enabled": True}


class TestDeepMerge:
    """Tests for _deep_merge()."""

    def test_simple_override(self):
        base = {"a": 1, "b": 2}
        _deep_merge(base, {"b": 3, "c": 4})
        assert base == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self):
        base = {"nested": {"a": 1, "b": 2}}
        _deep_merge(base, {"nested": {"b": 3}})
        assert base == {"nested": {"a": 1, "b": 3}}

    def test_deeply_nested(self):
        base = {"l1": {"l2": {"l3": "old"}}}
        _deep_merge(base, {"l1": {"l2": {"l3": "new"}}})
        assert base["l1"]["l2"]["l3"] == "new"

    def test_override_dict_with_scalar(self):
        base = {"a": {"nested": True}}
        _deep_merge(base, {"a": False})
        assert base["a"] is False


class TestResolveEmbeddingDims:
    """Tests for resolve_embedding_dims()."""

    def test_explicit_dims_preferred(self):
        assert resolve_embedding_dims("text-embedding-3-large", 1024) == 1024

    def test_known_model_lookup(self):
        assert resolve_embedding_dims("text-embedding-3-small") == 1536
        assert resolve_embedding_dims("text-embedding-3-large") == 3072
        assert resolve_embedding_dims("text-embedding-ada-002") == 1536

    def test_unknown_model_defaults_to_1536(self):
        assert resolve_embedding_dims("some-custom-model") == 1536

    def test_none_model_defaults_to_1536(self):
        assert resolve_embedding_dims(None) == 1536

    def test_none_model_with_explicit_dims(self):
        assert resolve_embedding_dims(None, 768) == 768


class TestKnownEmbeddingDims:
    """Verify the lookup table has expected entries."""

    def test_openai_models_present(self):
        assert "text-embedding-3-small" in KNOWN_EMBEDDING_DIMS
        assert "text-embedding-3-large" in KNOWN_EMBEDDING_DIMS
        assert "text-embedding-ada-002" in KNOWN_EMBEDDING_DIMS

    def test_cohere_models_present(self):
        assert "embed-english-v3.0" in KNOWN_EMBEDDING_DIMS

    def test_voyage_models_present(self):
        assert "voyage-3" in KNOWN_EMBEDDING_DIMS


class TestMemoryPresetsIntegrity:
    """Verify preset definitions are well-formed."""

    @pytest.mark.parametrize("preset_name", ["basic", "smart", "full"])
    def test_preset_has_enabled(self, preset_name):
        assert MEMORY_PRESETS[preset_name]["enabled"] is True

    @pytest.mark.parametrize("preset_name", ["basic", "smart", "full"])
    def test_preset_is_immutable_on_resolve(self, preset_name):
        original = copy.deepcopy(MEMORY_PRESETS[preset_name])
        result = resolve_memory_preset(preset_name)
        result["MUTATED"] = True
        assert MEMORY_PRESETS[preset_name] == original
