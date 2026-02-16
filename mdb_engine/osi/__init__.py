"""Open Semantic Interchange (OSI) integration for MDB-Engine.

This module provides bidirectional integration between MDB-Engine's
conversational knowledge graph and the OSI standard:

- **Import**: Consume OSI semantic models to enrich graph extraction
- **Registry**: Per-app storage of OSI models with synonym-based lookup
- **Entity Resolution**: Post-extraction disambiguation against OSI definitions
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
"""

from .registry import OsiModelRegistry
from .store import OsiModelStore

__all__ = [
    "OsiModelRegistry",
    "OsiModelStore",
]
