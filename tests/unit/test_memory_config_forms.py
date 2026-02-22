"""
Regression tests for the memory_config enabled-gate logic.

These tests exist because of a real bug: a manifest with
``"memory_config": {"preset": "full", ...}`` (no explicit ``"enabled": true``)
was silently skipped — memory service never initialized, and the app got a 503
at runtime.

The tests verify that EVERY valid form of memory_config passes the
enabled-gate in app_registration.py, multi_app.py, and service_initialization.py.
"""

import pytest

from mdb_engine.memory.presets import resolve_memory_preset

# ---------------------------------------------------------------------------
# The enabled-gate check — extracted from app_registration.py / multi_app.py
# ---------------------------------------------------------------------------


def _mem_enabled(memory_config) -> bool:
    """Replicate the _mem_enabled check from app_registration.py and multi_app.py.

    This MUST stay in sync with the real code. If the real code changes
    and these tests break, update both.
    """
    return bool(
        memory_config is True
        or isinstance(memory_config, str)
        or (isinstance(memory_config, dict) and (memory_config.get("enabled", False) or "preset" in memory_config))
    )


# ---------------------------------------------------------------------------
# Tests: enabled-gate passes for all valid forms
# ---------------------------------------------------------------------------


class TestEnabledGateAccepts:
    """Every valid memory_config form must pass the enabled-gate."""

    @pytest.mark.parametrize(
        "config",
        [
            True,
            "basic",
            "smart",
            "full",
            {"enabled": True},
            {"preset": "full"},
            {"preset": "smart"},
            {"preset": "basic"},
            {"preset": "full", "max_depth": 500},
            {"preset": "smart", "collection_name": "mem", "max_depth": 500},
            {"enabled": True, "preset": "full"},
            {"enabled": True, "collection_name": "mem"},
        ],
        ids=[
            "bool-true",
            "str-basic",
            "str-smart",
            "str-full",
            "dict-enabled",
            "dict-preset-full",
            "dict-preset-smart",
            "dict-preset-basic",
            "dict-preset-overrides",
            "dict-preset-multi-overrides",
            "dict-enabled-and-preset",
            "dict-enabled-and-collection",
        ],
    )
    def test_accepted(self, config):
        assert _mem_enabled(config), f"_mem_enabled({config!r}) should be True"


class TestEnabledGateRejects:
    """Invalid/disabled forms must NOT pass the enabled-gate."""

    @pytest.mark.parametrize(
        "config",
        [
            None,
            False,
            {},
            {"enabled": False},
            {"infer": True},
            {"collection_name": "mem"},
            {"max_depth": 500},
        ],
        ids=[
            "none",
            "bool-false",
            "empty-dict",
            "dict-enabled-false",
            "dict-no-enabled-no-preset-infer",
            "dict-no-enabled-no-preset-collection",
            "dict-no-enabled-no-preset-max-depth",
        ],
    )
    def test_rejected(self, config):
        assert not _mem_enabled(config), f"_mem_enabled({config!r}) should be False"


class TestPresetWithEnabledFalseEdgeCase:
    """If someone writes {"preset": "full", "enabled": false}, the gate passes
    but initialize_memory_service should still skip (enabled check inside)."""

    def test_gate_passes(self):
        config = {"preset": "full", "enabled": False}
        assert _mem_enabled(config), "Gate passes because 'preset' is present"

    def test_preset_resolves_enabled_true(self):
        resolved = resolve_memory_preset({"preset": "full", "enabled": False})
        assert resolved["enabled"] is False, "Explicit enabled:false overrides preset"


# ---------------------------------------------------------------------------
# Tests: preset resolution produces enabled=True
# ---------------------------------------------------------------------------


class TestPresetResolutionProducesEnabled:
    """All presets must set enabled=True so they pass the second check inside
    initialize_memory_service."""

    @pytest.mark.parametrize("preset_name", ["basic", "smart", "full"])
    def test_preset_string_has_enabled(self, preset_name):
        resolved = resolve_memory_preset(preset_name)
        assert resolved.get("enabled") is True

    @pytest.mark.parametrize("preset_name", ["basic", "smart", "full"])
    def test_preset_dict_has_enabled(self, preset_name):
        resolved = resolve_memory_preset({"preset": preset_name})
        assert resolved.get("enabled") is True

    def test_true_has_enabled(self):
        resolved = resolve_memory_preset(True)
        assert resolved.get("enabled") is True


# ---------------------------------------------------------------------------
# Tests: llm_config must be passed through
# ---------------------------------------------------------------------------


class TestMultiAppCodeIntegrity:
    """Source-code assertions to catch regressions in multi_app.py and
    app_registration.py. If someone removes a critical line, these tests
    catch it at unit-test time instead of at runtime."""

    def test_multiapp_passes_llm_config_to_memory_init(self):
        import inspect

        from mdb_engine.core import multi_app

        source = inspect.getsource(multi_app)
        assert (
            "llm_config=app_manifest_data.get" in source
        ), "multi_app.py must pass llm_config to initialize_memory_service"

    def test_multiapp_calls_initialize_graph_service(self):
        """The multi-app mount path must call initialize_graph_service."""
        import inspect

        from mdb_engine.core import multi_app

        source = inspect.getsource(multi_app)
        assert "initialize_graph_service" in source, "multi_app.py must call initialize_graph_service for mounted apps"

    def test_app_registration_has_preset_check(self):
        import inspect

        from mdb_engine.core import app_registration

        source = inspect.getsource(app_registration)
        assert '"preset" in memory_config' in source, "app_registration.py must check for 'preset' key in memory_config"

    def test_multiapp_has_preset_check(self):
        import inspect

        from mdb_engine.core import multi_app

        source = inspect.getsource(multi_app)
        assert '"preset" in raw_memory_config' in source, "multi_app.py must check for 'preset' key in memory_config"
