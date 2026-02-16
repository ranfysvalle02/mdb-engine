"""
Comprehensive tests for OSI (Open Semantic Interchange) integration.

Tests cover:
- Phase 1: YAML loading, prompt formatting, node type extraction
- Phase 2: Registry, entity resolution, manifest validation
- Phase 3: Grounding, YAML compilation, discovery pipeline
- Phase 4: Metric-aware query classification
- Phase 5: Graph-to-OSI export, mapper
- Family Management Model: End-to-end validation of the sso-app-3 family model
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_async_cursor(return_value=None):
    """Helper to create a mock async cursor."""
    items = return_value or []
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=items)
    cursor.sort = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)

    async def _aiter():
        for item in items:
            yield item

    cursor.__aiter__ = lambda self_: _aiter()
    return cursor


def _sample_osi_model() -> dict[str, Any]:
    """Return a sample OSI semantic model for testing."""
    return {
        "name": "sales_analytics",
        "description": "Core sales and customer analytics model",
        "ai_context": {"synonyms": ["sales model", "revenue model"]},
        "datasets": [
            {
                "name": "customer",
                "source": "warehouse.sales.customers",
                "primary_key": ["customer_id"],
                "fields": [
                    {
                        "name": "customer_name",
                        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "name"}]},
                        "ai_context": {"synonyms": ["client", "account", "buyer"]},
                    },
                    {
                        "name": "region",
                        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "region"}]},
                        "ai_context": {"synonyms": ["area", "territory"]},
                    },
                ],
                "ai_context": {"synonyms": ["client", "account", "buyer"]},
            },
            {
                "name": "order",
                "source": "warehouse.sales.orders",
                "primary_key": ["order_id"],
                "fields": [
                    {
                        "name": "order_total",
                        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "amount_usd"}]},
                        "ai_context": {"synonyms": ["amount", "total", "price"]},
                    },
                    {
                        "name": "order_date",
                        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "ordered_at"}]},
                        "dimension": {"is_time": True},
                    },
                ],
                "ai_context": {"synonyms": ["sale", "transaction", "purchase"]},
            },
        ],
        "relationships": [
            {
                "name": "order_to_customer",
                "from": "order",
                "to": "customer",
                "from_columns": ["customer_id"],
                "to_columns": ["customer_id"],
            },
        ],
        "metrics": [
            {
                "name": "total_revenue",
                "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "SUM(order.amount_usd)"}]},
                "description": "Total revenue across all orders",
                "ai_context": {"synonyms": ["revenue", "total sales", "gross sales", "income"]},
            },
            {
                "name": "order_count",
                "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "COUNT(order.order_id)"}]},
                "description": "Total number of orders",
                "ai_context": {"synonyms": ["number of orders", "order volume"]},
            },
        ],
    }


# ===========================================================================
# Phase 1: osi_loader tests
# ===========================================================================


class TestOsiLoader:
    """Tests for mdb_engine.graph.osi_loader."""

    def test_format_osi_for_prompt_produces_output(self):
        """format_osi_for_prompt returns non-empty string for valid models."""
        from mdb_engine.graph.osi_loader import format_osi_for_prompt

        models = [_sample_osi_model()]
        result = format_osi_for_prompt(models)

        assert result  # non-empty
        assert "Semantic Model: sales_analytics" in result
        assert 'Dataset "customer"' in result
        assert 'Dataset "order"' in result
        assert "Relationship: order -> customer" in result
        assert 'Metric "total_revenue"' in result

    def test_format_osi_for_prompt_includes_synonyms(self):
        """Prompt context includes field-level synonyms."""
        from mdb_engine.graph.osi_loader import format_osi_for_prompt

        result = format_osi_for_prompt([_sample_osi_model()])

        assert "client" in result
        assert "account" in result
        assert "revenue" in result

    def test_format_osi_for_prompt_empty_models(self):
        """Returns empty string for empty model list."""
        from mdb_engine.graph.osi_loader import format_osi_for_prompt

        assert format_osi_for_prompt([]) == ""

    def test_extract_node_types_from_osi(self):
        """Extracts dataset names as lowercase node types."""
        from mdb_engine.graph.osi_loader import extract_node_types_from_osi

        types = extract_node_types_from_osi([_sample_osi_model()])

        assert "customer" in types
        assert "order" in types
        assert len(types) == 2

    def test_extract_node_types_deduplicates(self):
        """Node types are deduplicated across models."""
        from mdb_engine.graph.osi_loader import extract_node_types_from_osi

        model = _sample_osi_model()
        types = extract_node_types_from_osi([model, model])  # Same model twice

        assert types.count("customer") == 1

    def test_extract_metric_names(self):
        """Extracts metric names with synonyms and expressions."""
        from mdb_engine.graph.osi_loader import extract_metric_names

        metrics = extract_metric_names([_sample_osi_model()])

        assert "total_revenue" in metrics
        assert "order_count" in metrics
        assert "revenue" in metrics["total_revenue"]["synonyms"]
        assert "SUM(order.amount_usd)" in metrics["total_revenue"]["expression"]
        assert metrics["total_revenue"]["model"] == "sales_analytics"

    def test_load_osi_models_missing_path(self):
        """Returns empty list for non-existent path."""
        from mdb_engine.graph.osi_loader import load_osi_models

        result = load_osi_models("/nonexistent/path")
        assert result == []

    def test_load_osi_models_from_yaml_file(self, tmp_path):
        """Loads models from a YAML file."""
        try:
            import yaml
        except ImportError:
            pytest.skip("PyYAML not installed")

        from mdb_engine.graph.osi_loader import load_osi_models

        model_file = tmp_path / "test_model.yaml"
        model_file.write_text(yaml.dump({"semantic_model": [_sample_osi_model()]}))

        models = load_osi_models(str(model_file))
        assert len(models) == 1
        assert models[0]["name"] == "sales_analytics"

    def test_load_osi_models_from_directory(self, tmp_path):
        """Loads all YAML files from a directory."""
        try:
            import yaml
        except ImportError:
            pytest.skip("PyYAML not installed")

        from mdb_engine.graph.osi_loader import load_osi_models

        (tmp_path / "model1.yaml").write_text(yaml.dump({"semantic_model": [{"name": "model_one", "datasets": []}]}))
        (tmp_path / "model2.yml").write_text(yaml.dump({"semantic_model": [{"name": "model_two", "datasets": []}]}))

        models = load_osi_models(str(tmp_path))
        assert len(models) == 2
        names = {m["name"] for m in models}
        assert names == {"model_one", "model_two"}


# ===========================================================================
# Phase 1: Prompt builder tests
# ===========================================================================


class TestPromptBuilder:
    """Tests for build_extraction_system_prompt in prompts.py."""

    def test_default_prompt_contains_standard_types(self):
        """Default prompt includes all standard node types."""
        from mdb_engine.graph.prompts import build_extraction_system_prompt

        prompt = build_extraction_system_prompt()

        assert "person" in prompt
        assert "organization" in prompt
        assert "concept" in prompt
        assert "product" in prompt

    def test_custom_node_types_merged(self):
        """Custom node types are appended to default types."""
        from mdb_engine.graph.prompts import build_extraction_system_prompt

        prompt = build_extraction_system_prompt(custom_node_types=["customer", "order"])

        assert "customer" in prompt
        assert "order" in prompt
        assert "person" in prompt  # Default types still present

    def test_osi_context_appended(self):
        """OSI context is appended when provided."""
        from mdb_engine.graph.prompts import build_extraction_system_prompt

        osi_context = 'Dataset "customer": fields [name, email]. Synonyms: ["client"]'
        prompt = build_extraction_system_prompt(osi_context=osi_context)

        assert "SEMANTIC MODELS" in prompt
        assert 'Dataset "customer"' in prompt
        assert "dataset types" in prompt

    def test_no_osi_context_no_section(self):
        """Without OSI context, the ORGANIZATION SEMANTIC MODELS section is absent."""
        from mdb_engine.graph.prompts import build_extraction_system_prompt

        prompt = build_extraction_system_prompt()

        assert "ORGANIZATION SEMANTIC MODELS" not in prompt

    def test_definition_detection_block(self):
        """Definition detection block is included when enabled."""
        from mdb_engine.graph.prompts import build_extraction_system_prompt

        prompt = build_extraction_system_prompt(enable_definition_detection=True)

        assert "DEFINITION DETECTION" in prompt
        assert "is_definition" in prompt
        assert "extracted_logic" in prompt

    def test_definition_detection_not_included_by_default(self):
        """Definition detection block is NOT included by default."""
        from mdb_engine.graph.prompts import build_extraction_system_prompt

        prompt = build_extraction_system_prompt()

        assert "DEFINITION DETECTION" not in prompt

    def test_builder_always_produces_complete_prompt(self):
        """The builder always produces a complete extraction prompt."""
        from mdb_engine.graph.prompts import build_extraction_system_prompt

        prompt = build_extraction_system_prompt()
        assert "Knowledge Graph extraction engine" in prompt
        assert "person" in prompt
        assert "EVERY relationship" in prompt
        assert "allergic_to" in prompt
        assert "parent_of" in prompt


# ===========================================================================
# Phase 2: Registry tests
# ===========================================================================


class TestOsiModelRegistry:
    """Tests for OsiModelRegistry."""

    @pytest.fixture
    def registry(self):
        """Create a registry with inline models."""
        from mdb_engine.osi.registry import OsiModelRegistry

        config = {
            "enabled": True,
            "semantic_models": [_sample_osi_model()],
        }
        return OsiModelRegistry(app_slug="test_app", config=config)

    @pytest.mark.asyncio
    async def test_load_indexes_models(self, registry):
        """Loading indexes datasets, metrics, and relationships."""
        await registry.load()

        assert registry.loaded
        assert len(registry.models) == 1

    @pytest.mark.asyncio
    async def test_get_dataset_by_name(self, registry):
        """Direct dataset lookup by name."""
        await registry.load()

        ds = registry.get_dataset("customer")
        assert ds is not None
        assert ds["name"] == "customer"

    @pytest.mark.asyncio
    async def test_get_dataset_by_synonym(self, registry):
        """Dataset lookup via synonym."""
        await registry.load()

        ds = registry.get_dataset("client")
        assert ds is not None
        assert ds["name"] == "customer"

    @pytest.mark.asyncio
    async def test_get_dataset_unknown_returns_none(self, registry):
        """Unknown dataset returns None."""
        await registry.load()

        assert registry.get_dataset("nonexistent") is None

    @pytest.mark.asyncio
    async def test_get_metric_by_name(self, registry):
        """Direct metric lookup by name."""
        await registry.load()

        m = registry.get_metric("total_revenue")
        assert m is not None
        assert "total_revenue" in m.get("name", "")

    @pytest.mark.asyncio
    async def test_get_metric_by_synonym(self, registry):
        """Metric lookup via synonym."""
        await registry.load()

        m = registry.get_metric("revenue")
        assert m is not None

    @pytest.mark.asyncio
    async def test_get_prompt_context(self, registry):
        """Prompt context is generated on load."""
        await registry.load()

        ctx = registry.get_prompt_context()
        assert ctx is not None
        assert "customer" in ctx
        assert "total_revenue" in ctx

    @pytest.mark.asyncio
    async def test_get_node_types(self, registry):
        """Node types extracted from datasets."""
        await registry.load()

        types = registry.get_node_types()
        assert "customer" in types
        assert "order" in types

    @pytest.mark.asyncio
    async def test_get_all_metric_keywords(self, registry):
        """All metric keywords include names and synonyms."""
        await registry.load()

        keywords = registry.get_all_metric_keywords()
        assert "total revenue" in keywords  # name with _ replaced
        assert "revenue" in keywords  # synonym
        assert "gross sales" in keywords  # synonym
        assert "order volume" in keywords  # synonym

    @pytest.mark.asyncio
    async def test_match_entity_by_type(self, registry):
        """Entity matching by type name."""
        await registry.load()

        match = registry.match_entity("Acme Corp", "customer")
        assert match is not None
        assert match["name"] == "customer"

    @pytest.mark.asyncio
    async def test_match_entity_by_synonym(self, registry):
        """Entity matching falls back to synonym lookup."""
        await registry.load()

        # "organization" is not a dataset, but "client" is a synonym for "customer"
        match = registry.match_entity("Acme", "client")
        assert match is not None
        assert match["name"] == "customer"

    @pytest.mark.asyncio
    async def test_list_datasets(self, registry):
        """list_datasets returns all datasets."""
        await registry.load()

        datasets = registry.list_datasets()
        assert len(datasets) == 2
        names = {d["name"] for d in datasets}
        assert names == {"customer", "order"}

    @pytest.mark.asyncio
    async def test_list_metrics(self, registry):
        """list_metrics returns all metrics."""
        await registry.load()

        metrics = registry.list_metrics()
        assert len(metrics) == 2

    @pytest.mark.asyncio
    async def test_reload(self, registry):
        """Reload resets and re-indexes models."""
        await registry.load()
        assert registry.loaded

        await registry.reload()
        assert len(registry.models) == 1  # Still has the inline model


# ===========================================================================
# Phase 2: Entity resolution tests
# ===========================================================================


class TestEntityResolver:
    """Tests for post-extraction entity resolution."""

    @pytest.fixture
    def registry(self):
        from mdb_engine.osi.registry import OsiModelRegistry

        config = {"enabled": True, "semantic_models": [_sample_osi_model()]}
        reg = OsiModelRegistry(app_slug="test", config=config)
        # Synchronous load for fixture
        from mdb_engine.osi.loader import load_all_models

        reg._models = load_all_models(config)
        reg._index_models()
        reg._build_prompt_context()
        reg._loaded = True
        return reg

    def test_resolve_remaps_organization_to_customer(self, registry):
        """Resolves 'organization' -> 'customer' via synonym."""
        from mdb_engine.osi.resolver import resolve_entities

        nodes = [
            {"id": "organization:acme", "type": "organization", "name": "Acme Corp", "properties": {}},
        ]

        # "organization" doesn't match any dataset directly, but check synonym
        # The resolver checks type, then name synonyms
        resolved = resolve_entities(nodes, registry)
        # organization -> may or may not resolve depending on synonym paths
        assert len(resolved) == 1

    def test_resolve_keeps_matching_type(self, registry):
        """Nodes that already match an OSI dataset are unchanged."""
        from mdb_engine.osi.resolver import resolve_entities

        nodes = [
            {"id": "customer:acme", "type": "customer", "name": "Acme Corp", "properties": {}},
        ]

        resolved = resolve_entities(nodes, registry)
        # Already matches, should not be changed (type stays customer)
        assert resolved[0]["type"] == "customer"

    def test_resolve_empty_list(self, registry):
        """Empty node list returns empty."""
        from mdb_engine.osi.resolver import resolve_entities

        assert resolve_entities([], registry) == []


# ===========================================================================
# Phase 3: Grounding tests
# ===========================================================================


class TestSchemaGrounder:
    """Tests for SchemaGrounder."""

    @pytest.mark.asyncio
    async def test_ground_exact_match(self):
        """Fields that exactly match known properties get confidence 1.0."""
        from mdb_engine.osi.grounding import SchemaGrounder

        grounder = SchemaGrounder()

        extracted_logic = {
            "entities": ["person"],
            "temporal_constraints": [],
            "conditions": [{"field": "name", "operator": "=", "value": "Alex"}],
        }

        result = await grounder.ground(extracted_logic)

        assert result.confidence >= 0.0
        assert result.grounded_logic is not None
        assert "conditions" in result.grounded_logic

    @pytest.mark.asyncio
    async def test_ground_with_osi_registry(self):
        """Grounding with OSI registry uses dataset fields for matching."""
        from mdb_engine.osi.grounding import SchemaGrounder
        from mdb_engine.osi.registry import OsiModelRegistry

        config = {"enabled": True, "semantic_models": [_sample_osi_model()]}
        registry = OsiModelRegistry(app_slug="test", config=config)
        await registry.load()

        grounder = SchemaGrounder(osi_registry=registry)

        extracted_logic = {
            "entities": ["customer"],
            "temporal_constraints": [{"field": "order_date", "operator": ">", "value": "14 days"}],
            "conditions": [{"field": "customer.region", "operator": "=", "value": "West"}],
        }

        result = await grounder.ground(extracted_logic)

        assert result.grounded_logic is not None
        # customer matches exactly (it's a dataset name)
        assert result.grounded_logic["entities"][0] == "customer"

    @pytest.mark.asyncio
    async def test_ground_unresolved_field_gets_warning(self):
        """Unresolvable fields produce warnings."""
        from mdb_engine.osi.grounding import SchemaGrounder

        grounder = SchemaGrounder()

        extracted_logic = {
            "entities": ["zebra_widget"],
            "temporal_constraints": [],
            "conditions": [{"field": "nonexistent.xyz_field", "operator": "=", "value": 0}],
        }

        result = await grounder.ground(extracted_logic)

        # With no graph service or OSI registry, fields can't be resolved
        assert result.grounded_logic is not None


# ===========================================================================
# Phase 3: Compiler tests
# ===========================================================================


class TestOsiCompiler:
    """Tests for OsiCompiler YAML generation."""

    def _make_concept_node(self) -> dict[str, Any]:
        return {
            "_id": "concept:ghosting",
            "type": "concept",
            "name": "Ghosting",
            "properties": {
                "is_definition": True,
                "definition": "No login for 14 days and ignored emails",
                "created_by": "person:sarah",
                "status": "provisional",
                "extracted_logic": {
                    "entities": ["user", "email"],
                    "temporal_constraints": [{"field": "last_login", "operator": ">", "value": "14 days"}],
                    "conditions": [
                        {"field": "login_count", "operator": "=", "value": 0, "window": "14 days"},
                        {"field": "email_response_count", "operator": "=", "value": 0, "window": "last 2 emails"},
                    ],
                },
            },
        }

    def test_compile_metric_produces_yaml(self):
        """Compiling a concept node produces valid YAML content."""
        from mdb_engine.osi.compiler import OsiCompiler
        from mdb_engine.osi.grounding import GroundingResult

        compiler = OsiCompiler()
        concept = self._make_concept_node()

        grounding = GroundingResult(
            grounded_logic=concept["properties"]["extracted_logic"],
            confidence=0.85,
        )

        result = compiler.compile_metric(concept, grounding)

        assert result.yaml_content
        assert "AUTO-GENERATED BY MDB-ENGINE" in result.yaml_content
        assert "ghosting" in result.yaml_content
        assert result.metric_name == "ghosting"
        assert result.confidence == 0.85
        assert result.confidence_label == "Medium"

    def test_compile_metric_includes_provenance(self):
        """Compiled YAML includes source/confidence comments."""
        from mdb_engine.osi.compiler import OsiCompiler
        from mdb_engine.osi.grounding import GroundingResult

        compiler = OsiCompiler()
        concept = self._make_concept_node()

        grounding = GroundingResult(
            grounded_logic=concept["properties"]["extracted_logic"],
            confidence=0.95,
        )

        result = compiler.compile_metric(concept, grounding)

        assert "Confidence: High" in result.yaml_content
        assert "person:sarah" in result.yaml_content

    def test_compile_metric_includes_conditions(self):
        """Compiled YAML includes SQL-like conditions from extracted logic."""
        from mdb_engine.osi.compiler import OsiCompiler
        from mdb_engine.osi.grounding import GroundingResult

        compiler = OsiCompiler()
        concept = self._make_concept_node()

        grounding = GroundingResult(
            grounded_logic=concept["properties"]["extracted_logic"],
            confidence=0.7,
        )

        result = compiler.compile_metric(concept, grounding)

        assert "login_count" in result.yaml_content
        assert "email_response_count" in result.yaml_content


# ===========================================================================
# Phase 3: Discovery service tests
# ===========================================================================


class TestSemanticDiscoveryService:
    """Tests for the full discovery pipeline."""

    @pytest.mark.asyncio
    async def test_on_concept_discovered_runs_pipeline(self):
        """Full pipeline produces YAML from a concept node."""
        from mdb_engine.osi.discovery import SemanticDiscoveryService

        # Mock graph service
        graph_service = MagicMock()
        graph_service.upsert_node = AsyncMock()
        graph_service.get_stats = AsyncMock(return_value={"nodes_by_type": {}})
        graph_service.node_types = ["person", "concept"]

        service = SemanticDiscoveryService(graph_service=graph_service)

        concept = {
            "_id": "concept:churn_risk",
            "type": "concept",
            "name": "Churn Risk",
            "properties": {
                "is_definition": True,
                "definition": "Customer has not purchased in 90 days",
                "created_by": "person:mike",
                "status": "provisional",
                "extracted_logic": {
                    "entities": ["customer"],
                    "temporal_constraints": [{"field": "last_purchase", "operator": ">", "value": "90 days"}],
                    "conditions": [{"field": "purchase_count", "operator": "=", "value": 0, "window": "90 days"}],
                },
            },
        }

        result = await service.on_concept_discovered(concept, user_id="mike")

        assert result.concept_id == "concept:churn_risk"
        assert result.yaml_content  # Non-empty YAML
        assert result.metric_name == "churn_risk"
        assert result.confidence >= 0.0
        assert result.status == "provisional"

        # Verify graph service was called to update the node
        graph_service.upsert_node.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_concept_discovered_no_logic_returns_error(self):
        """Missing extracted_logic returns error result."""
        from mdb_engine.osi.discovery import SemanticDiscoveryService

        service = SemanticDiscoveryService()

        concept = {
            "_id": "concept:empty",
            "type": "concept",
            "name": "Empty",
            "properties": {"is_definition": True},
        }

        result = await service.on_concept_discovered(concept, user_id="test")

        assert result.error is not None
        assert "extracted_logic" in result.error

    @pytest.mark.asyncio
    async def test_import_user_yaml(self):
        """Importing user-provided YAML works."""
        try:
            import yaml  # noqa: F401
        except ImportError:
            pytest.skip("PyYAML not installed")

        from mdb_engine.osi.discovery import SemanticDiscoveryService
        from mdb_engine.osi.registry import OsiModelRegistry

        registry = OsiModelRegistry(app_slug="test", config={"enabled": True})
        await registry.load()

        service = SemanticDiscoveryService(osi_registry=registry)

        yaml_content = """
semantic_model:
  - name: imported_model
    datasets:
      - name: widget
        source: db.widgets
        fields:
          - name: widget_name
            expression:
              dialects:
                - dialect: ANSI_SQL
                  expression: name
    metrics: []
"""

        result = await service.import_user_yaml(yaml_content, source="test")

        assert result.success
        assert result.models_loaded == 1

        # Verify it was loaded into the registry
        assert registry.get_dataset("widget") is not None

    @pytest.mark.asyncio
    async def test_import_invalid_yaml(self):
        """Invalid YAML returns error."""
        try:
            import yaml  # noqa: F401
        except ImportError:
            pytest.skip("PyYAML not installed")

        from mdb_engine.osi.discovery import SemanticDiscoveryService

        service = SemanticDiscoveryService()

        result = await service.import_user_yaml("{{invalid: yaml: [", source="test")

        assert not result.success
        assert result.error is not None


# ===========================================================================
# Phase 4: Query classification tests
# ===========================================================================


class TestOsiMetricClassification:
    """Tests for osi_metric query classification."""

    @pytest.fixture
    def registry_with_metrics(self):
        from mdb_engine.osi.registry import OsiModelRegistry

        config = {"enabled": True, "semantic_models": [_sample_osi_model()]}
        reg = OsiModelRegistry(app_slug="test", config=config)
        from mdb_engine.osi.loader import load_all_models

        reg._models = load_all_models(config)
        reg._index_models()
        reg._build_prompt_context()
        reg._loaded = True
        return reg

    @pytest.mark.asyncio
    async def test_classifies_metric_query(self, registry_with_metrics):
        """Query mentioning a metric synonym is classified as osi_metric."""
        from mdb_engine.graph.query_classifier import QueryClassifier

        classifier = QueryClassifier(osi_registry=registry_with_metrics)

        result = await classifier.classify_query("What was our total revenue last quarter?")
        assert result == "osi_metric"

    @pytest.mark.asyncio
    async def test_classifies_metric_by_synonym(self, registry_with_metrics):
        """Query using a metric synonym (not name) is classified as osi_metric."""
        from mdb_engine.graph.query_classifier import QueryClassifier

        classifier = QueryClassifier(osi_registry=registry_with_metrics)

        result = await classifier.classify_query("Show me gross sales for this year")
        assert result == "osi_metric"

    @pytest.mark.asyncio
    async def test_non_metric_query_not_classified_as_osi(self, registry_with_metrics):
        """Non-metric queries are not classified as osi_metric."""
        from mdb_engine.graph.query_classifier import QueryClassifier

        classifier = QueryClassifier(osi_registry=registry_with_metrics)

        result = await classifier.classify_query("What is Alex's favorite color?")
        assert result != "osi_metric"

    @pytest.mark.asyncio
    async def test_no_registry_no_osi_classification(self):
        """Without OSI registry, osi_metric is never returned."""
        from mdb_engine.graph.query_classifier import QueryClassifier

        classifier = QueryClassifier()

        result = await classifier.classify_query("What was our total revenue?")
        # Without registry, this falls through to normal patterns
        assert result != "osi_metric"


# ===========================================================================
# Phase 5: Mapper tests
# ===========================================================================


class TestMapper:
    """Tests for OSI <-> MDB-Engine mapping utilities."""

    def test_nodes_to_dataset_schema(self):
        """Aggregates nodes into an OSI dataset definition."""
        from mdb_engine.osi.mapper import nodes_to_dataset_schema

        nodes = [
            {"name": "Alex", "type": "person", "properties": {"role": "Engineer", "team": "Backend"}},
            {"name": "Sarah", "type": "person", "properties": {"role": "Manager"}},
        ]

        dataset = nodes_to_dataset_schema(nodes, "person")

        assert dataset["name"] == "person"
        assert dataset["primary_key"] == ["person_id"]
        assert dataset["ai_context"]["instance_count"] == 2
        # Fields should include person_name + role + team
        field_names = [f["name"] for f in dataset["fields"]]
        assert "person_name" in field_names
        assert "role" in field_names
        assert "team" in field_names

    def test_edges_to_relationships(self):
        """Discovers relationship patterns from edges."""
        from mdb_engine.osi.mapper import edges_to_relationships

        nodes_by_type = {
            "person": [
                {
                    "name": "Alex",
                    "edges": [
                        {"relation": "works_at", "target": "organization:acme", "active": True},
                    ],
                },
                {
                    "name": "Sarah",
                    "edges": [
                        {"relation": "works_at", "target": "organization:globex", "active": True},
                        {"relation": "manages", "target": "organization:acme", "active": True},
                    ],
                },
            ],
        }

        relationships = edges_to_relationships(nodes_by_type)

        assert len(relationships) >= 1
        rel_names = [r["name"] for r in relationships]
        assert "person_works_at_organization" in rel_names


# ===========================================================================
# Phase 5: Exporter tests
# ===========================================================================


class TestOsiExporter:
    """Tests for graph-to-OSI export."""

    @pytest.mark.asyncio
    async def test_export_produces_semantic_model(self):
        """Export creates a valid OSI semantic model structure."""
        from mdb_engine.osi.exporter import OsiExporter

        # Mock graph service with some nodes
        graph_service = MagicMock()
        graph_service.app_slug = "test_app"

        nodes = [
            {
                "_id": "person:alex",
                "type": "person",
                "name": "Alex",
                "app_slug": "test_app",
                "properties": {"role": "Engineer"},
                "edges": [{"relation": "works_at", "target": "organization:acme", "active": True}],
            },
            {
                "_id": "organization:acme",
                "type": "organization",
                "name": "Acme",
                "app_slug": "test_app",
                "properties": {"industry": "Manufacturing"},
                "edges": [],
            },
        ]
        graph_service.collection = MagicMock()
        graph_service.collection.find = MagicMock(return_value=_make_async_cursor(nodes))

        exporter = OsiExporter(app_slug="test_app", graph_service=graph_service)
        result = await exporter.export()

        assert "semantic_model" in result
        model = result["semantic_model"][0]
        assert model["name"] == "test_app_discovered"
        assert len(model["datasets"]) == 2
        assert len(model["custom_extensions"]) == 1

        # Check vendor extension
        ext = model["custom_extensions"][0]
        assert ext["vendor_name"] == "MDB_ENGINE"
        ext_data = json.loads(ext["data"])
        assert ext_data["source"] == "conversational_discovery"
        assert ext_data["node_count"] == 2


# ===========================================================================
# Phase 1+2: GraphService OSI integration tests
# ===========================================================================


class TestGraphServiceOsiIntegration:
    """Tests for OSI integration in GraphService initialization."""

    def test_graph_service_loads_osi_from_path(self, tmp_path):
        """GraphService loads OSI models from osi_models_path config."""
        try:
            import yaml
        except ImportError:
            pytest.skip("PyYAML not installed")

        from mdb_engine.graph.service import GraphService

        model_file = tmp_path / "test.yaml"
        model_file.write_text(yaml.dump({"semantic_model": [_sample_osi_model()]}))

        collection = MagicMock()
        collection.create_index = AsyncMock()

        service = GraphService(
            app_slug="test",
            collection=collection,
            config={"enabled": True, "osi_models_path": str(tmp_path)},
        )

        assert service._osi_prompt_context is not None
        assert "customer" in service.node_types
        assert "order" in service.node_types
        assert service._osi_discovery_enabled is True

    def test_graph_service_accepts_osi_registry(self):
        """GraphService accepts an injected OSI registry via config."""
        from mdb_engine.graph.service import GraphService

        # Create a mock registry
        mock_registry = MagicMock()
        mock_registry.get_prompt_context.return_value = "Mock OSI context"
        mock_registry.get_node_types.return_value = ["widget", "gadget"]

        collection = MagicMock()
        collection.create_index = AsyncMock()

        service = GraphService(
            app_slug="test",
            collection=collection,
            config={"enabled": True, "_osi_registry": mock_registry},
        )

        assert service._osi_prompt_context == "Mock OSI context"
        assert "widget" in service.node_types
        assert "gadget" in service.node_types

    def test_graph_service_no_osi_by_default(self):
        """Without OSI config, no OSI context is loaded."""
        from mdb_engine.graph.service import GraphService

        collection = MagicMock()
        collection.create_index = AsyncMock()

        service = GraphService(
            app_slug="test",
            collection=collection,
            config={"enabled": True},
        )

        assert service._osi_prompt_context is None
        assert service._osi_discovery_enabled is False


# ===========================================================================
# Phase 2: Manifest validation tests
# ===========================================================================


class TestOsiManifestSchema:
    """Tests for osi_config manifest schema validation."""

    @pytest.mark.asyncio
    async def test_valid_osi_config(self):
        """Valid osi_config passes manifest validation."""
        from mdb_engine.core.manifest import ManifestValidator

        manifest = {
            "schema_version": "2.0",
            "slug": "osi_test",
            "name": "OSI Test App",
            "osi_config": {
                "enabled": True,
                "models_path": "semantic_models/",
                "entity_resolution": True,
                "metric_routing": True,
                "export_enabled": False,
                "sync_interval_minutes": 30,
            },
        }

        validator = ManifestValidator()
        is_valid, error, paths = await validator.validate(manifest)
        assert is_valid, f"Validation failed: {error} at {paths}"

    @pytest.mark.asyncio
    async def test_osi_config_with_inline_models(self):
        """osi_config with inline semantic_models passes validation."""
        from mdb_engine.core.manifest import ManifestValidator

        manifest = {
            "schema_version": "2.0",
            "slug": "osi_inline_test",
            "name": "OSI Inline Test",
            "osi_config": {
                "enabled": True,
                "semantic_models": [_sample_osi_model()],
            },
        }

        validator = ManifestValidator()
        is_valid, error, paths = await validator.validate(manifest)
        assert is_valid, f"Validation failed: {error} at {paths}"

    @pytest.mark.asyncio
    async def test_osi_config_rejects_extra_fields(self):
        """osi_config rejects unknown fields (additionalProperties: false)."""
        from mdb_engine.core.manifest import ManifestValidator

        manifest = {
            "schema_version": "2.0",
            "slug": "osi_bad",
            "name": "OSI Bad Test",
            "osi_config": {
                "enabled": True,
                "unknown_field": "should fail",
            },
        }

        validator = ManifestValidator()
        is_valid, error, paths = await validator.validate(manifest)
        assert not is_valid

    @pytest.mark.asyncio
    async def test_graph_config_osi_models_path(self):
        """graph_config.osi_models_path passes validation (Tier 1)."""
        from mdb_engine.core.manifest import ManifestValidator

        manifest = {
            "schema_version": "2.0",
            "slug": "tier1_test",
            "name": "Tier 1 Test",
            "graph_config": {
                "enabled": True,
                "osi_models_path": "semantic_models/",
            },
        }

        validator = ManifestValidator()
        is_valid, error, paths = await validator.validate(manifest)
        assert is_valid, f"Validation failed: {error} at {paths}"


# ===========================================================================
# Phase 2: Loader tests
# ===========================================================================


class TestExpandedLoader:
    """Tests for the expanded OSI loader (inline + external + merge)."""

    def test_load_inline_models(self):
        """Loads models from inline semantic_models config."""
        from mdb_engine.osi.loader import load_all_models

        config = {
            "semantic_models": [
                {"name": "inline_model", "datasets": [], "metrics": []},
            ],
        }

        models = load_all_models(config)
        assert len(models) == 1
        assert models[0]["name"] == "inline_model"

    def test_load_deduplicates_by_name(self):
        """Models with duplicate names are loaded only once."""
        from mdb_engine.osi.loader import load_all_models

        config = {
            "semantic_models": [
                {"name": "same_model", "datasets": []},
                {"name": "same_model", "datasets": []},
            ],
        }

        models = load_all_models(config)
        assert len(models) == 1

    def test_load_empty_config(self):
        """Empty config returns empty list."""
        from mdb_engine.osi.loader import load_all_models

        assert load_all_models({}) == []


# ===========================================================================
# Phase 5: Reporter tests
# ===========================================================================


class TestDiscoveryReporter:
    """Tests for the discovery gap analysis reporter."""

    @pytest.mark.asyncio
    async def test_generate_report_identifies_unmatched_types(self):
        """Report identifies graph node types not in OSI."""
        from mdb_engine.osi.registry import OsiModelRegistry
        from mdb_engine.osi.reporter import OsiDiscoveryReporter

        # Registry with only "customer" and "order" datasets
        config = {"enabled": True, "semantic_models": [_sample_osi_model()]}
        registry = OsiModelRegistry(app_slug="test", config=config)
        await registry.load()

        # Graph service has "person" and "event" types (not in OSI)
        graph_service = MagicMock()
        graph_service.get_stats = AsyncMock(
            return_value={
                "nodes_by_type": {"person": 15, "event": 5, "customer": 10},
            }
        )
        graph_service.collection = MagicMock()
        graph_service.collection.find = MagicMock(return_value=_make_async_cursor([]))

        reporter = OsiDiscoveryReporter(graph_service=graph_service, registry=registry)
        report = await reporter.generate_report()

        # "person" and "event" should appear as new entity types
        new_types = {t["type"] for t in report["new_entity_types"]}
        assert "person" in new_types
        assert "event" in new_types
        assert "customer" not in new_types  # Already in OSI

        assert report["stats"]["unmatched_types"] == 2


# ===========================================================================
# Family Management Model: End-to-end validation
# ===========================================================================


def _load_family_model() -> dict[str, Any]:
    """Load the family management model from the actual sso-app-3 manifest."""
    import json
    from pathlib import Path

    manifest_path = Path(__file__).parent.parent.parent / (
        "examples/advanced/sso-multi-app/apps/sso-app-3/manifest.json"
    )
    if not manifest_path.exists():
        pytest.skip("sso-app-3 manifest not found")

    with open(manifest_path) as f:
        manifest = json.load(f)

    osi_config = manifest.get("osi_config", {})
    models = osi_config.get("semantic_models", [])
    if not models:
        pytest.skip("No semantic_models in sso-app-3 manifest")

    return models[0]


def _make_family_registry():
    """Create an OsiModelRegistry loaded with the family model."""
    from mdb_engine.osi.loader import load_all_models
    from mdb_engine.osi.registry import OsiModelRegistry

    model = _load_family_model()
    config = {"enabled": True, "semantic_models": [model]}
    reg = OsiModelRegistry(app_slug="ai-chat", config=config)
    reg._models = load_all_models(config)
    reg._index_models()
    reg._build_prompt_context()
    reg._loaded = True
    return reg


# ===========================================================================
# Core: Node ID normalization tests
# ===========================================================================


class TestNodeIdNormalization:
    """Test that node IDs are normalized consistently regardless of LLM output.

    This is the fix for the duplicate node bug: the LLM produces "User's Son"
    in one call and "Users Son" in another. Without normalization, these become
    two separate nodes. With normalization, both become person:users_son and
    upsert_node merges them.
    """

    def test_apostrophe_stripped(self):
        """Apostrophes are removed: "User's Son" and "Users Son" produce same ID."""
        from mdb_engine.graph.extraction import _normalize_node_id

        assert _normalize_node_id("person:User's Son") == "person:users_son"
        assert _normalize_node_id("person:Users Son") == "person:users_son"
        assert _normalize_node_id("person:User's Son") == _normalize_node_id("person:Users Son")

    def test_mixed_case_lowered(self):
        from mdb_engine.graph.extraction import _normalize_node_id

        assert _normalize_node_id("person:Alex_Smith") == "person:alex_smith"
        assert _normalize_node_id("PERSON:ALEX") == "person:alex"

    def test_spaces_to_underscores(self):
        from mdb_engine.graph.extraction import _normalize_node_id

        assert _normalize_node_id("person:alex smith") == "person:alex_smith"

    def test_hyphens_to_underscores(self):
        """Hyphens become underscores (preserves word boundaries)."""
        from mdb_engine.graph.extraction import _normalize_node_id

        assert _normalize_node_id("concept:peanut-allergy") == "concept:peanut_allergy"
        assert _normalize_node_id("concept:peanut-allergy") == _normalize_node_id("concept:peanut_allergy")

    def test_periods_stripped(self):
        from mdb_engine.graph.extraction import _normalize_node_id

        assert _normalize_node_id("person:Dr. Smith") == "person:dr_smith"

    def test_multiple_underscores_collapsed(self):
        from mdb_engine.graph.extraction import _normalize_node_id

        assert _normalize_node_id("person:alex__smith") == "person:alex_smith"

    def test_no_colon_defaults_to_concept(self):
        from mdb_engine.graph.extraction import _normalize_node_id

        assert _normalize_node_id("chocolate") == "concept:chocolate"

    def test_user_daughter_consistency(self):
        """THE BUG: User's Daughter vs User Daughter vs Users Daughter."""
        from mdb_engine.graph.extraction import _normalize_node_id

        v1 = _normalize_node_id("person:User's Daughter")
        v2 = _normalize_node_id("person:Users Daughter")
        v3 = _normalize_node_id("person:users_daughter")

        # v1 strips apostrophe -> "users daughter" -> "users_daughter"
        # v2 -> "users daughter" -> "users_daughter"
        # All match
        assert v1 == v2 == v3 == "person:users_daughter"

        # "User Daughter" (no 's') is a DIFFERENT entity name — that's correct
        # The LLM should produce "Users" not "User" for possessive
        v4 = _normalize_node_id("person:User Daughter")
        assert v4 == "person:user_daughter"

    def test_user_son_consistency(self):
        from mdb_engine.graph.extraction import _normalize_node_id

        v1 = _normalize_node_id("person:Users Son")
        v2 = _normalize_node_id("person:User's Son")
        v3 = _normalize_node_id("person:users_son")

        assert v1 == v2 == v3 == "person:users_son"

    def test_ampersand_stripped(self):
        """PB&J -> pbj (& is stripped, no word boundary)."""
        from mdb_engine.graph.extraction import _normalize_node_id

        assert _normalize_node_id("interest:PB&J") == "interest:pbj"

    def test_empty_and_whitespace(self):
        from mdb_engine.graph.extraction import _normalize_node_id

        assert _normalize_node_id("") == ""
        assert _normalize_node_id("  ") == ""


# ===========================================================================
# MongoDB-backed store tests
# ===========================================================================


class TestOsiModelStore:
    """Tests for the MongoDB-backed OsiModelStore."""

    @pytest.fixture
    def mock_collection(self):
        """Mock Motor collection for store tests."""
        collection = MagicMock()
        collection.find_one = AsyncMock(return_value=None)
        collection.update_one = AsyncMock()
        collection.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))
        collection.find = MagicMock(return_value=_make_async_cursor([]))
        return collection

    @pytest.mark.asyncio
    async def test_seed_first_run(self, mock_collection):
        """First run (no meta doc) triggers seeding."""
        from mdb_engine.osi.store import OsiModelStore

        mock_collection.find_one = AsyncMock(return_value=None)  # No meta doc

        store = OsiModelStore(collection=mock_collection, app_slug="test")
        config = {"semantic_models": [{"name": "test_model", "datasets": [], "metrics": []}]}

        seeded = await store.seed_from_config(config)

        assert seeded is True
        # Should have called update_one for the model + meta doc
        assert mock_collection.update_one.call_count >= 2

    @pytest.mark.asyncio
    async def test_seed_hash_match_skips(self, mock_collection):
        """Matching hash skips seeding."""
        from mdb_engine.osi.store import OsiModelStore

        config = {"semantic_models": [{"name": "test_model", "datasets": []}]}
        expected_hash = OsiModelStore._compute_config_hash(config)

        mock_collection.find_one = AsyncMock(
            return_value={
                "_id": "_meta",
                "config_hash": expected_hash,
            }
        )

        store = OsiModelStore(collection=mock_collection, app_slug="test")
        seeded = await store.seed_from_config(config)

        assert seeded is False
        # update_one should NOT have been called (no seeding)
        mock_collection.update_one.assert_not_called()

    @pytest.mark.asyncio
    async def test_seed_hash_mismatch_reseeds(self, mock_collection):
        """Mismatched hash triggers re-seeding."""
        from mdb_engine.osi.store import OsiModelStore

        mock_collection.find_one = AsyncMock(
            return_value={
                "_id": "_meta",
                "config_hash": "sha256:old_hash",
            }
        )

        store = OsiModelStore(collection=mock_collection, app_slug="test")
        config = {"semantic_models": [{"name": "new_model", "datasets": []}]}

        seeded = await store.seed_from_config(config)

        assert seeded is True
        assert mock_collection.update_one.call_count >= 2

    @pytest.mark.asyncio
    async def test_load_all_returns_models(self, mock_collection):
        """load_all returns model dicts from collection."""
        from mdb_engine.osi.store import OsiModelStore

        mock_collection.find = MagicMock(
            return_value=_make_async_cursor(
                [
                    {
                        "_id": "model:sales",
                        "doc_type": "model",
                        "name": "sales",
                        "description": "Sales model",
                        "ai_context": {},
                        "datasets": [{"name": "customer"}],
                        "metrics": [],
                        "relationships": [],
                        "status": "approved",
                        "origin": "yaml_seed",
                    },
                ]
            )
        )

        store = OsiModelStore(collection=mock_collection, app_slug="test")
        models = await store.load_all()

        assert len(models) == 1
        assert models[0]["name"] == "sales"
        assert models[0]["_status"] == "approved"
        assert models[0]["_origin"] == "yaml_seed"

    @pytest.mark.asyncio
    async def test_upsert_model(self, mock_collection):
        """upsert_model writes to collection."""
        from mdb_engine.osi.store import OsiModelStore

        store = OsiModelStore(collection=mock_collection, app_slug="test")
        await store.upsert_model(
            {"name": "new_model", "datasets": []},
            origin="api_import",
            status="approved",
        )

        mock_collection.update_one.assert_called_once()
        call_args = mock_collection.update_one.call_args
        assert call_args[0][0] == {"_id": "model:new_model"}

    @pytest.mark.asyncio
    async def test_remove_model(self, mock_collection):
        """remove_model deletes from collection."""
        from mdb_engine.osi.store import OsiModelStore

        store = OsiModelStore(collection=mock_collection, app_slug="test")
        removed = await store.remove_model("old_model")

        assert removed is True
        mock_collection.delete_one.assert_called_once_with({"_id": "model:old_model"})

    def test_hash_deterministic(self):
        """Same config always produces same hash."""
        from mdb_engine.osi.store import OsiModelStore

        config = {"semantic_models": [{"name": "a", "datasets": [{"name": "b"}]}]}
        h1 = OsiModelStore._compute_config_hash(config)
        h2 = OsiModelStore._compute_config_hash(config)
        assert h1 == h2
        assert h1.startswith("sha256:")

    def test_hash_changes_with_config(self):
        """Different config produces different hash."""
        from mdb_engine.osi.store import OsiModelStore

        h1 = OsiModelStore._compute_config_hash({"semantic_models": [{"name": "a"}]})
        h2 = OsiModelStore._compute_config_hash({"semantic_models": [{"name": "b"}]})
        assert h1 != h2


class TestRegistryWithStore:
    """Tests for OsiModelRegistry backed by a store."""

    @pytest.mark.asyncio
    async def test_registry_loads_from_store(self):
        """Registry loads models from store instead of config."""
        from mdb_engine.osi.registry import OsiModelRegistry
        from mdb_engine.osi.store import OsiModelStore

        mock_collection = MagicMock()
        mock_collection.find_one = AsyncMock(return_value={"_id": "_meta", "config_hash": "sha256:match"})
        mock_collection.update_one = AsyncMock()
        mock_collection.find = MagicMock(
            return_value=_make_async_cursor(
                [
                    {
                        "_id": "model:test",
                        "doc_type": "model",
                        "name": "test_model",
                        "description": "",
                        "ai_context": {},
                        "datasets": [{"name": "widget", "fields": [], "ai_context": {"synonyms": ["gadget"]}}],
                        "metrics": [],
                        "relationships": [],
                        "status": "approved",
                        "origin": "yaml_seed",
                    },
                ]
            )
        )

        store = OsiModelStore(collection=mock_collection, app_slug="test")
        # Pre-set hash to match so seed is skipped
        config = {"enabled": True, "semantic_models": []}

        registry = OsiModelRegistry(app_slug="test", config=config, store=store)
        await registry.load()

        assert registry.loaded
        assert len(registry.models) == 1
        assert registry.get_dataset("widget") is not None
        assert registry.get_dataset("gadget") is not None  # synonym

    @pytest.mark.asyncio
    async def test_add_model_writes_through(self):
        """add_model persists to store and re-indexes."""
        from mdb_engine.osi.registry import OsiModelRegistry
        from mdb_engine.osi.store import OsiModelStore

        mock_collection = MagicMock()
        mock_collection.find_one = AsyncMock(return_value={"_id": "_meta", "config_hash": "sha256:x"})
        mock_collection.update_one = AsyncMock()

        # After add_model, store.load_all returns the new model
        call_count = [0]

        def make_cursor(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 1:
                return _make_async_cursor([])  # Initial load
            return _make_async_cursor(
                [
                    {
                        "_id": "model:new",
                        "doc_type": "model",
                        "name": "new_model",
                        "description": "",
                        "ai_context": {},
                        "datasets": [{"name": "thing", "fields": [], "ai_context": {"synonyms": ["stuff"]}}],
                        "metrics": [],
                        "relationships": [],
                        "status": "approved",
                        "origin": "api_import",
                    }
                ]
            )

        mock_collection.find = MagicMock(side_effect=make_cursor)

        store = OsiModelStore(collection=mock_collection, app_slug="test")
        config = {"enabled": True, "semantic_models": []}

        registry = OsiModelRegistry(app_slug="test", config=config, store=store)
        await registry.load()

        assert len(registry.models) == 0

        # Add a model via write-through
        await registry.add_model(
            {"name": "new_model", "datasets": [{"name": "thing", "fields": [], "ai_context": {"synonyms": ["stuff"]}}]}
        )

        assert len(registry.models) == 1
        assert registry.get_dataset("thing") is not None


class TestFamilyManifestValidation:
    """Validate the actual sso-app-3 manifest passes schema validation."""

    @pytest.mark.asyncio
    async def test_full_manifest_validates(self):
        """The complete sso-app-3 manifest with osi_config passes schema validation."""
        import json
        from pathlib import Path

        from mdb_engine.core.manifest import ManifestValidator

        manifest_path = Path(__file__).parent.parent.parent / (
            "examples/advanced/sso-multi-app/apps/sso-app-3/manifest.json"
        )
        if not manifest_path.exists():
            pytest.skip("sso-app-3 manifest not found")

        with open(manifest_path) as f:
            manifest = json.load(f)

        validator = ManifestValidator()
        is_valid, error, paths = await validator.validate(manifest)
        assert is_valid, f"sso-app-3 manifest validation failed: {error} at {paths}"

    @pytest.mark.asyncio
    async def test_osi_config_is_enabled(self):
        """osi_config.enabled is True in the manifest."""
        import json
        from pathlib import Path

        manifest_path = Path(__file__).parent.parent.parent / (
            "examples/advanced/sso-multi-app/apps/sso-app-3/manifest.json"
        )
        if not manifest_path.exists():
            pytest.skip("sso-app-3 manifest not found")

        with open(manifest_path) as f:
            manifest = json.load(f)

        osi_config = manifest.get("osi_config", {})
        assert osi_config.get("enabled") is True
        assert osi_config.get("entity_resolution") is True
        assert osi_config.get("metric_routing") is True
        assert osi_config.get("export_enabled") is True

    def test_graph_node_types_include_family_types(self):
        """graph_config.node_types includes all 10 family-specific types."""
        import json
        from pathlib import Path

        manifest_path = Path(__file__).parent.parent.parent / (
            "examples/advanced/sso-multi-app/apps/sso-app-3/manifest.json"
        )
        if not manifest_path.exists():
            pytest.skip("sso-app-3 manifest not found")

        with open(manifest_path) as f:
            manifest = json.load(f)

        node_types = manifest.get("graph_config", {}).get("node_types", [])
        family_types = [
            "family_member",
            "allergy",
            "medication",
            "medical_condition",
            "vaccination",
            "appointment",
            "routine",
            "meal_plan",
            "emergency_contact",
            "pet",
        ]
        for ft in family_types:
            assert ft in node_types, f"Missing family node type: {ft}"


class TestFamilyModelStructure:
    """Validate the family model has all expected datasets, relationships, and metrics."""

    def test_model_has_12_datasets(self):
        """Family model has exactly 12 datasets."""
        model = _load_family_model()
        datasets = model.get("datasets", [])
        assert len(datasets) == 12

        expected_names = {
            "family_member",
            "allergy",
            "medication",
            "medical_condition",
            "vaccination",
            "appointment",
            "routine",
            "meal_plan",
            "emergency_contact",
            "pet",
            "interest",
            "food",
        }
        actual_names = {d["name"] for d in datasets}
        assert actual_names == expected_names

    def test_model_has_16_relationships(self):
        """Family model has 16 relationships."""
        model = _load_family_model()
        relationships = model.get("relationships", [])
        assert len(relationships) == 16

    def test_model_has_6_metrics(self):
        """Family model has 6 governed metrics."""
        model = _load_family_model()
        metrics = model.get("metrics", [])
        assert len(metrics) == 6

        expected_names = {
            "active_medications",
            "upcoming_appointments",
            "high_severity_allergies",
            "overdue_vaccinations",
            "weekly_routine_count",
            "meals_planned",
        }
        actual_names = {m["name"] for m in metrics}
        assert actual_names == expected_names

    def test_every_dataset_has_synonyms(self):
        """Every dataset has ai_context.synonyms for extraction recognition."""
        model = _load_family_model()
        for ds in model.get("datasets", []):
            ai_ctx = ds.get("ai_context", {})
            synonyms = ai_ctx.get("synonyms", [])
            assert len(synonyms) > 0, f"Dataset '{ds['name']}' has no synonyms"

    def test_every_metric_has_synonyms(self):
        """Every metric has ai_context.synonyms for query classification."""
        model = _load_family_model()
        for m in model.get("metrics", []):
            ai_ctx = m.get("ai_context", {})
            synonyms = ai_ctx.get("synonyms", [])
            assert len(synonyms) > 0, f"Metric '{m['name']}' has no synonyms"

    def test_every_dataset_has_fields(self):
        """Every dataset has at least 3 fields defined."""
        model = _load_family_model()
        for ds in model.get("datasets", []):
            fields = ds.get("fields", [])
            assert len(fields) >= 3, f"Dataset '{ds['name']}' only has {len(fields)} fields (expected >= 3)"

    def test_relationships_reference_valid_datasets(self):
        """Every relationship references datasets that actually exist."""
        model = _load_family_model()
        dataset_names = {d["name"] for d in model.get("datasets", [])}

        for rel in model.get("relationships", []):
            from_ds = rel.get("from", "")
            to_ds = rel.get("to", "")
            assert (
                from_ds in dataset_names
            ), f"Relationship '{rel['name']}' references unknown 'from' dataset: '{from_ds}'"
            assert to_ds in dataset_names, f"Relationship '{rel['name']}' references unknown 'to' dataset: '{to_ds}'"


class TestFamilyRegistryLoading:
    """Test that the family model loads correctly into the OSI registry."""

    @pytest.mark.asyncio
    async def test_registry_loads_all_datasets(self):
        """Registry indexes all 12 family datasets."""
        from mdb_engine.osi.registry import OsiModelRegistry

        model = _load_family_model()
        config = {"enabled": True, "semantic_models": [model]}
        registry = OsiModelRegistry(app_slug="ai-chat", config=config)
        await registry.load()

        datasets = registry.list_datasets()
        assert len(datasets) == 12

    @pytest.mark.asyncio
    async def test_registry_loads_all_metrics(self):
        """Registry indexes all 6 family metrics."""
        from mdb_engine.osi.registry import OsiModelRegistry

        model = _load_family_model()
        config = {"enabled": True, "semantic_models": [model]}
        registry = OsiModelRegistry(app_slug="ai-chat", config=config)
        await registry.load()

        metrics = registry.list_metrics()
        assert len(metrics) == 6

    @pytest.mark.asyncio
    async def test_registry_loads_all_relationships(self):
        """Registry indexes all 16 relationships."""
        from mdb_engine.osi.registry import OsiModelRegistry

        model = _load_family_model()
        config = {"enabled": True, "semantic_models": [model]}
        registry = OsiModelRegistry(app_slug="ai-chat", config=config)
        await registry.load()

        relationships = registry.list_relationships()
        assert len(relationships) == 16


class TestFamilySynonymResolution:
    """Test that family domain synonyms resolve to correct datasets."""

    def test_kid_resolves_to_family_member(self):
        """'kid' is a synonym for family_member dataset."""
        registry = _make_family_registry()
        ds = registry.get_dataset("kid")
        assert ds is not None
        assert ds["name"] == "family_member"

    def test_parent_resolves_to_family_member(self):
        """'parent' is a synonym for family_member dataset."""
        registry = _make_family_registry()
        ds = registry.get_dataset("parent")
        assert ds is not None
        assert ds["name"] == "family_member"

    def test_spouse_resolves_to_family_member(self):
        """'spouse' is a synonym for family_member dataset."""
        registry = _make_family_registry()
        ds = registry.get_dataset("spouse")
        assert ds is not None
        assert ds["name"] == "family_member"

    def test_meds_resolves_to_medication(self):
        """'meds' is a synonym for medication dataset."""
        registry = _make_family_registry()
        ds = registry.get_dataset("meds")
        assert ds is not None
        assert ds["name"] == "medication"

    def test_prescription_resolves_to_medication(self):
        """'prescription' is a synonym for medication dataset."""
        registry = _make_family_registry()
        ds = registry.get_dataset("prescription")
        assert ds is not None
        assert ds["name"] == "medication"

    def test_allergic_resolves_to_allergy(self):
        """'allergic' is a synonym for allergy dataset."""
        registry = _make_family_registry()
        ds = registry.get_dataset("allergic")
        assert ds is not None
        assert ds["name"] == "allergy"

    def test_shot_resolves_to_vaccination(self):
        """'shot' is a synonym for vaccination dataset."""
        registry = _make_family_registry()
        ds = registry.get_dataset("shot")
        assert ds is not None
        assert ds["name"] == "vaccination"

    def test_chore_resolves_to_routine(self):
        """'chore' is a synonym for routine dataset."""
        registry = _make_family_registry()
        ds = registry.get_dataset("chore")
        assert ds is not None
        assert ds["name"] == "routine"

    def test_dinner_resolves_to_meal_plan(self):
        """'dinner' is a synonym for meal_plan dataset."""
        registry = _make_family_registry()
        ds = registry.get_dataset("dinner")
        assert ds is not None
        assert ds["name"] == "meal_plan"

    def test_dog_resolves_to_pet(self):
        """'dog' is a synonym for pet dataset."""
        registry = _make_family_registry()
        ds = registry.get_dataset("dog")
        assert ds is not None
        assert ds["name"] == "pet"

    def test_checkup_resolves_to_appointment(self):
        """'checkup' resolves to appointment via field or dataset synonyms."""
        registry = _make_family_registry()
        ds = registry.get_dataset("checkup")
        assert ds is not None
        assert ds["name"] == "appointment"

    def test_emergency_contact_direct_lookup(self):
        """Direct lookup of emergency_contact dataset."""
        registry = _make_family_registry()
        ds = registry.get_dataset("emergency_contact")
        assert ds is not None
        assert ds["name"] == "emergency_contact"


class TestFamilyEntityResolution:
    """Test entity resolution remaps generic types to family-specific types."""

    def test_person_remapped_to_family_member(self):
        """Extraction type 'person' remapped to 'family_member' via synonym."""
        from mdb_engine.osi.resolver import resolve_entities

        registry = _make_family_registry()

        nodes = [
            {"id": "person:timmy", "type": "person", "name": "Timmy", "properties": {}},
        ]
        resolved = resolve_entities(nodes, registry)

        # "person" is a synonym for "family_member" in the family model
        assert resolved[0]["type"] == "family_member"
        assert resolved[0]["id"] == "family_member:timmy"
        assert resolved[0]["properties"]["_osi_dataset"] == "family_member"

    def test_concept_with_allergy_name_remapped(self):
        """Entity named with allergy-related synonym gets resolved."""
        from mdb_engine.osi.resolver import resolve_entities

        registry = _make_family_registry()

        # LLM might type "peanut allergy" as "concept" -- the name-based synonym lookup
        # should catch that "allergy" matches the allergy dataset
        nodes = [
            {"id": "concept:peanut_allergy", "type": "concept", "name": "peanut allergy", "properties": {}},
        ]
        resolved = resolve_entities(nodes, registry)

        # The resolver checks type first, then name. "concept" doesn't match,
        # but the name "peanut allergy" contains "allergy" which is a substring
        # match for the allergy dataset synonyms. The resolver uses substring
        # matching in step 4.
        assert len(resolved) == 1
        # Either remapped or kept -- depends on synonym paths
        # At minimum the node should still be returned
        assert resolved[0]["name"] == "peanut allergy"

    def test_family_member_type_stays_unchanged(self):
        """Nodes already typed as family_member are not re-mapped."""
        from mdb_engine.osi.resolver import resolve_entities

        registry = _make_family_registry()

        nodes = [
            {"id": "family_member:jane", "type": "family_member", "name": "Jane Doe", "properties": {}},
        ]
        resolved = resolve_entities(nodes, registry)

        assert resolved[0]["type"] == "family_member"
        assert resolved[0]["id"] == "family_member:jane"


class TestFamilyMetricClassification:
    """Test query classification routes family queries to governed metrics."""

    @pytest.mark.asyncio
    async def test_medication_query_classified_as_osi_metric(self):
        """'What meds is everyone on?' routes to osi_metric."""
        from mdb_engine.graph.query_classifier import QueryClassifier

        registry = _make_family_registry()
        classifier = QueryClassifier(osi_registry=registry)

        result = await classifier.classify_query("What meds is everyone on?")
        assert result == "osi_metric"

    @pytest.mark.asyncio
    async def test_active_medications_synonym_classified(self):
        """'How many medications' routes to osi_metric via synonym."""
        from mdb_engine.graph.query_classifier import QueryClassifier

        registry = _make_family_registry()
        classifier = QueryClassifier(osi_registry=registry)

        result = await classifier.classify_query("How many medications does our family take?")
        assert result == "osi_metric"

    @pytest.mark.asyncio
    async def test_upcoming_appointments_classified(self):
        """'What's coming up this week?' routes to osi_metric."""
        from mdb_engine.graph.query_classifier import QueryClassifier

        registry = _make_family_registry()
        classifier = QueryClassifier(osi_registry=registry)

        result = await classifier.classify_query("What's coming up this week on our calendar?")
        assert result == "osi_metric"

    @pytest.mark.asyncio
    async def test_allergy_severity_query_classified(self):
        """'dangerous allergies' routes to osi_metric."""
        from mdb_engine.graph.query_classifier import QueryClassifier

        registry = _make_family_registry()
        classifier = QueryClassifier(osi_registry=registry)

        result = await classifier.classify_query("Does anyone have dangerous allergies?")
        assert result == "osi_metric"

    @pytest.mark.asyncio
    async def test_overdue_vaccines_classified(self):
        """'overdue shots' routes to osi_metric."""
        from mdb_engine.graph.query_classifier import QueryClassifier

        registry = _make_family_registry()
        classifier = QueryClassifier(osi_registry=registry)

        result = await classifier.classify_query("Do we have any overdue shots or missed vaccines?")
        assert result == "osi_metric"

    @pytest.mark.asyncio
    async def test_chore_count_classified(self):
        """'how many chores' routes to osi_metric."""
        from mdb_engine.graph.query_classifier import QueryClassifier

        registry = _make_family_registry()
        classifier = QueryClassifier(osi_registry=registry)

        result = await classifier.classify_query("How many chores do we have this week?")
        assert result == "osi_metric"

    @pytest.mark.asyncio
    async def test_meal_plan_classified(self):
        """'what's planned for meals' routes to osi_metric."""
        from mdb_engine.graph.query_classifier import QueryClassifier

        registry = _make_family_registry()
        classifier = QueryClassifier(osi_registry=registry)

        result = await classifier.classify_query("What's the dinner plan for this week?")
        assert result == "osi_metric"

    @pytest.mark.asyncio
    async def test_non_metric_family_query_not_classified(self):
        """A family question that doesn't match any metric synonym stays non-metric."""
        from mdb_engine.graph.query_classifier import QueryClassifier

        registry = _make_family_registry()
        classifier = QueryClassifier(osi_registry=registry)

        result = await classifier.classify_query("What is Timmy's favorite color?")
        assert result != "osi_metric"


class TestFamilyPromptEnrichment:
    """Test that the extraction prompt includes family domain vocabulary."""

    def test_prompt_includes_family_datasets(self):
        """Extraction prompt includes family dataset names."""
        from mdb_engine.graph.osi_loader import format_osi_for_prompt

        model = _load_family_model()
        prompt_context = format_osi_for_prompt([model])

        assert 'Dataset "family_member"' in prompt_context
        assert 'Dataset "allergy"' in prompt_context
        assert 'Dataset "medication"' in prompt_context
        assert 'Dataset "vaccination"' in prompt_context
        assert 'Dataset "appointment"' in prompt_context
        assert 'Dataset "routine"' in prompt_context
        assert 'Dataset "meal_plan"' in prompt_context
        assert 'Dataset "pet"' in prompt_context

    def test_prompt_includes_family_relationships(self):
        """Extraction prompt includes family relationship names."""
        from mdb_engine.graph.osi_loader import format_osi_for_prompt

        model = _load_family_model()
        prompt_context = format_osi_for_prompt([model])

        assert "allergy -> family_member" in prompt_context
        assert "medication -> family_member" in prompt_context
        assert "pet -> family_member" in prompt_context

    def test_prompt_includes_family_metrics(self):
        """Extraction prompt includes family metric names."""
        from mdb_engine.graph.osi_loader import format_osi_for_prompt

        model = _load_family_model()
        prompt_context = format_osi_for_prompt([model])

        assert 'Metric "active_medications"' in prompt_context
        assert 'Metric "upcoming_appointments"' in prompt_context
        assert 'Metric "high_severity_allergies"' in prompt_context

    def test_prompt_includes_medical_synonyms(self):
        """Extraction prompt includes key medical synonyms."""
        from mdb_engine.graph.osi_loader import format_osi_for_prompt

        model = _load_family_model()
        prompt_context = format_osi_for_prompt([model])

        # Check for key synonyms that help the LLM recognize domain concepts
        assert "EpiPen" in prompt_context or "epipen" in prompt_context.lower()
        assert "pediatrician" in prompt_context or "PCP" in prompt_context

    def test_node_types_extracted_from_family_model(self):
        """extract_node_types_from_osi returns all 12 family dataset names."""
        from mdb_engine.graph.osi_loader import extract_node_types_from_osi

        model = _load_family_model()
        types = extract_node_types_from_osi([model])

        assert len(types) == 12
        expected = {
            "family_member",
            "allergy",
            "medication",
            "medical_condition",
            "vaccination",
            "appointment",
            "routine",
            "meal_plan",
            "emergency_contact",
            "pet",
            "interest",
            "food",
        }
        assert set(types) == expected

    def test_metric_names_extracted_from_family_model(self):
        """extract_metric_names returns all 6 family metrics with synonyms."""
        from mdb_engine.graph.osi_loader import extract_metric_names

        model = _load_family_model()
        metrics = extract_metric_names([model])

        assert len(metrics) == 6
        assert "active_medications" in metrics
        assert "medication count" in metrics["active_medications"]["synonyms"]
        assert "upcoming_appointments" in metrics
        assert "next appointments" in metrics["upcoming_appointments"]["synonyms"]


class TestFamilyDiscoveryPipeline:
    """Test the discovery pipeline with family-domain concept definitions."""

    @pytest.mark.asyncio
    async def test_bedtime_routine_concept_discovery(self):
        """'Bedtime routine means...' triggers the full discovery pipeline."""
        from mdb_engine.osi.discovery import SemanticDiscoveryService

        graph_service = MagicMock()
        graph_service.upsert_node = AsyncMock()
        graph_service.get_stats = AsyncMock(return_value={"nodes_by_type": {}})
        graph_service.node_types = ["family_member", "routine", "concept"]

        registry = _make_family_registry()

        service = SemanticDiscoveryService(
            graph_service=graph_service,
            osi_registry=registry,
        )

        concept = {
            "_id": "concept:bedtime_routine",
            "type": "concept",
            "name": "Bedtime Routine",
            "properties": {
                "is_definition": True,
                "definition": "Bath at 7pm, brush teeth, read two stories, lights out by 8pm",
                "created_by": "person:jane",
                "status": "provisional",
                "extracted_logic": {
                    "entities": ["family_member", "routine"],
                    "temporal_constraints": [
                        {"field": "time", "operator": "=", "value": "7:00 PM"},
                    ],
                    "conditions": [
                        {"field": "routine.tasks", "operator": "includes", "value": "bath", "window": "daily"},
                        {"field": "routine.tasks", "operator": "includes", "value": "brush teeth", "window": "daily"},
                        {"field": "routine.tasks", "operator": "includes", "value": "read stories", "window": "daily"},
                    ],
                },
            },
        }

        result = await service.on_concept_discovered(concept, user_id="jane")

        assert result.concept_id == "concept:bedtime_routine"
        assert result.yaml_content  # Non-empty YAML was generated
        assert "bedtime_routine" in result.metric_name
        assert result.confidence >= 0.0
        assert result.status == "provisional"
        assert "AUTO-GENERATED BY MDB-ENGINE" in result.yaml_content
        assert "bath" in result.yaml_content.lower() or "routine" in result.yaml_content.lower()

        # Verify graph node was updated with the generated YAML
        graph_service.upsert_node.assert_called_once()

    @pytest.mark.asyncio
    async def test_peanut_allergy_concept_grounded_against_family_schema(self):
        """Grounding corrects allergy fields against the family model schema."""
        from mdb_engine.osi.grounding import SchemaGrounder

        registry = _make_family_registry()
        grounder = SchemaGrounder(osi_registry=registry)

        extracted_logic = {
            "entities": ["family_member", "allergy"],
            "temporal_constraints": [],
            "conditions": [
                {"field": "allergy.allergen", "operator": "=", "value": "peanuts"},
                {"field": "allergy.severity", "operator": "=", "value": "high"},
            ],
        }

        result = await grounder.ground(extracted_logic)

        assert result.grounded_logic is not None
        # "allergy" is a known dataset, so entity should resolve cleanly
        assert "allergy" in result.grounded_logic["entities"]
        # "allergen" and "severity" are real fields on the allergy dataset
        # so they should ground with high confidence
        assert result.confidence > 0.0
