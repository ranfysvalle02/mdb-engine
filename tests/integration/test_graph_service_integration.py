"""
Integration tests for GraphService

High-value tests that verify:
- Public API contracts (method signatures exist)
- Manifest schema validation
- Service initialization patterns
- Component wiring (not implementation details)

Note: Tests requiring real MongoDB should set MONGODB_URI.
"""

import inspect
import os

import pytest

# Skip DB-dependent tests if MongoDB not available
pytestmark = pytest.mark.skipif(
    not os.getenv("MONGODB_URI"),
    reason="MONGODB_URI not set - skipping integration tests",
)


class TestManifestGraphConfig:
    """Test manifest graph_config schema validation - CRITICAL for configuration."""

    def test_graph_config_schema_exists(self):
        """Verify graph_config is properly defined in manifest schema."""
        from mdb_engine.core.manifest import ManifestConfig

        schema = ManifestConfig.get_schema()
        properties = schema.get("properties", {})

        assert "graph_config" in properties, "graph_config missing from manifest schema"
        graph_schema = properties["graph_config"]["properties"]

        # Core config options must exist
        required_options = ["enabled", "collection_name", "auto_extract", "default_max_depth"]
        for opt in required_options:
            assert opt in graph_schema, f"graph_config.{opt} missing from schema"

    def test_graph_config_enabled_defaults_to_true(self):
        """Verify graph_config.enabled defaults to True (enabled by default)."""
        from mdb_engine.core.manifest import ManifestConfig

        schema = ManifestConfig.get_schema()
        graph_schema = schema["properties"]["graph_config"]["properties"]
        enabled_schema = graph_schema["enabled"]

        # Enabled should default to True
        assert (
            enabled_schema.get("default") is True
        ), "graph_config.enabled should default to True (enabled by default)"

    def test_graph_config_validates_correctly(self):
        """Verify manifest validation accepts valid graph_config."""
        from mdb_engine.core.manifest import ManifestConfig

        valid_manifest = {
            "app_name": "Test App",
            "app_slug": "test_app",
            "version": "1.0.0",
            "database_name": "test_db",
            "graph_config": {
                "enabled": True,
                "collection_name": "kg_test",
                "auto_extract": True,
                "default_max_depth": 2,
            },
        }

        # Should not raise
        ManifestConfig.validate(valid_manifest)


class TestPublicAPIContracts:
    """Verify public API signatures - ensures backwards compatibility."""

    def test_cognitive_engine_accepts_graph_service_param(self):
        """CognitiveEngine must accept graph_service for dependency injection."""
        from mdb_engine.memory.orchestrator import CognitiveEngine

        sig = inspect.signature(CognitiveEngine.__init__)
        assert "graph_service" in sig.parameters

    def test_memory_service_accepts_graph_service_param(self):
        """CognitiveMemoryService must accept graph_service for injection."""
        from mdb_engine.memory.cognitive import CognitiveMemoryService

        sig = inspect.signature(CognitiveMemoryService.__init__)
        assert "graph_service" in sig.parameters

    def test_memory_factory_accepts_graph_service_param(self):
        """get_memory_service factory must support graph_service injection."""
        from mdb_engine.memory.service import get_memory_service

        sig = inspect.signature(get_memory_service)
        assert "graph_service" in sig.parameters

    def test_engine_has_get_graph_service_method(self):
        """MongoDBEngine must expose get_graph_service for retrieval."""
        from mdb_engine.core.engine import MongoDBEngine

        assert hasattr(MongoDBEngine, "get_graph_service")
        assert callable(MongoDBEngine.get_graph_service)


class TestGraphServicePublicInterface:
    """Verify GraphService exposes required public methods."""

    def test_graph_service_has_required_methods(self):
        """GraphService must implement all required methods from BaseGraphService."""
        from mdb_engine.graph import GraphService

        required_methods = [
            "upsert_node",
            "get_node",
            "delete_node",
            "list_nodes",
            "add_edge",
            "remove_edge",
            "update_edge",
            "deactivate_edge",
            "traverse",
            "get_neighbors",
            "hybrid_search",
            "extract_graph_from_text",
            "format_graph_context",
            "get_stats",
        ]

        for method in required_methods:
            assert hasattr(GraphService, method), f"GraphService missing method: {method}"
            assert callable(getattr(GraphService, method))

    def test_factory_function_exists(self):
        """get_graph_service factory function must be importable."""
        from mdb_engine.graph import get_graph_service

        assert callable(get_graph_service)


class TestModuleExports:
    """Verify module __all__ exports are correct."""

    def test_graph_module_exports(self):
        """graph module must export core classes."""
        from mdb_engine import graph

        expected_exports = ["GraphService", "BaseGraphService", "GraphServiceError"]
        for export in expected_exports:
            assert hasattr(graph, export), f"mdb_engine.graph missing export: {export}"
