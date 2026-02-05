"""
Memory Service Module
---------------------
This module provides intelligent memory management using native MongoDB Atlas Vector Search.

Key Features:
- **MongoDB Atlas Vector Search**: Native integration with MongoDB for semantic search
- **Intelligent Fact Extraction**: Uses LLM to extract atomic facts from conversations
- **Memory Categories**: Classifies memories as biographical, preferences, temporal, relational
- **Embedding Service Integration**: Uses mdb_engine.embeddings for vector generation
- **Metadata Support**: Full support for bucket_id, bucket_type, and custom metadata
- **Cognitive Memory**: Advanced memory system with importance scoring,
  reinforcement, decay, merging, and pruning
- **Redaction Layer**: Configurable privacy protection for sensitive data (SSN, credit cards, etc.)
- **Reflection Service**: Periodic memory consolidation to prevent bloat
- **Extensible Architecture**: Base class allows for future memory provider implementations

For GraphRAG functionality (knowledge graph with $graphLookup traversal), use the standalone
mdb_engine.graph module:
    from mdb_engine.graph import GraphService, get_graph_service

Dependencies:
    pip install pymongo openai litellm
"""

# Import base classes
from .base import BaseMemoryService, MemoryServiceError

# Import cognitive memory service (THE memory service - customizable)
from .cognitive import (
    MEMORY_CATEGORIES,
    CognitiveMemoryService,
    CognitiveMemoryServiceError,
)

# Import memory fusion service
from .fusion import MemoryFusionError, MemoryFusionService

# Import cognitive orchestrator
from .orchestrator import (
    ChatHistoryService,
    CognitiveEngine,
    GeminiProvider,
    LLMProvider,
    OpenAIProvider,
)

# Import perception engine
from .perceptions import PerceptionEngine

# Import procedural memory service
from .procedural import ProceduralMemoryService

# Import privacy and enhancement services
# Note: Redaction service moved to mdb_engine.redaction (standalone module)
from .reflection import ReflectionService, ReflectionServiceError, create_reflection_service

# Import factory function
from .service import get_memory_service

# Backwards compatibility: CustomMemoryService is an alias for CognitiveMemoryService
CustomMemoryService = CognitiveMemoryService
CustomMemoryServiceError = CognitiveMemoryServiceError

__all__ = [
    # Base classes (for extensibility)
    "BaseMemoryService",
    "MemoryServiceError",
    # Cognitive memory service (THE memory service - customizable)
    "CognitiveMemoryService",
    "CognitiveMemoryServiceError",
    "MEMORY_CATEGORIES",
    # Backwards compatibility aliases
    "CustomMemoryService",
    "CustomMemoryServiceError",
    # Cognitive architecture
    "ChatHistoryService",
    "CognitiveEngine",
    # LLM Provider abstraction
    "LLMProvider",
    "OpenAIProvider",
    "GeminiProvider",
    # Privacy and enhancement services
    # Note: Redaction service available via mdb_engine.redaction
    "ReflectionService",
    "ReflectionServiceError",
    "create_reflection_service",
    # Memory Fusion Service
    "MemoryFusionService",
    "MemoryFusionError",
    # Perception Engine
    "PerceptionEngine",
    # Procedural Memory Service
    "ProceduralMemoryService",
    # Factory function
    "get_memory_service",
]
