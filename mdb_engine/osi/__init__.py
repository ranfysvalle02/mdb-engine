"""Open Semantic Interchange (OSI) integration for MDB-Engine.

This module provides bidirectional integration between MDB-Engine's
conversational knowledge graph and the OSI standard:

- **Import**: Consume OSI semantic models to enrich graph extraction
- **Registry**: Per-app storage of OSI models with synonym-based lookup
- **Entity Resolution**: Post-extraction disambiguation against OSI definitions
- **Validation**: Schema validation with clear, actionable error messages
- **Scaffolding**: Auto-generate starter OSI YAML from graph config
- **Discovery**: Auto-generate OSI YAML from conversational definitions
- **Export**: Publish discovered knowledge as OSI-compatible artifacts

Usage::

    from mdb_engine.osi import OsiModelRegistry

    registry = OsiModelRegistry(app_slug="my_app", config=osi_config)
    await registry.load()

    # Get prompt context for extraction enrichment
    context = registry.get_prompt_context()

    # Check if a metric is defined
    metric = registry.get_metric("total_revenue")

    # Validate a YAML file before loading
    from mdb_engine.osi import validate_osi_yaml, OsiValidationResult
    result = validate_osi_yaml(yaml_string)

    # Auto-scaffold a starter model from node types
    from mdb_engine.osi import scaffold_osi_model
    model = scaffold_osi_model("my_app", node_types=["actor", "movie"])
"""

from .registry import OsiModelRegistry
from .scaffold import scaffold_osi_model, scaffold_to_yaml
from .store import OsiModelStore
from .validator import OsiValidationResult, validate_osi_model, validate_osi_models, validate_osi_yaml

__all__ = [
    "OsiModelRegistry",
    "OsiModelStore",
    "OsiValidationResult",
    "scaffold_osi_model",
    "scaffold_to_yaml",
    "validate_osi_model",
    "validate_osi_models",
    "validate_osi_yaml",
]
