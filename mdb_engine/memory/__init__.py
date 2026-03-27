"""
Memory Service Module
---------------------
Persistent AI memory backed by MongoDB Atlas Vector Search.

Quick Start:
    # In your manifest.json, enable memory:
    # {"memory_config": {"enabled": true, "provider": "cognitive"}}

    from mdb_engine.dependencies import get_memory_service

    @app.post("/remember")
    async def remember(text: str, memory=Depends(get_memory_service)):
        return await memory.add(messages=text, user_id="user1")

    @app.get("/recall")
    async def recall(q: str, memory=Depends(get_memory_service)):
        return await memory.search(query=q, user_id="user1")

Key Features:
- Semantic search via MongoDB Atlas Vector Search
- LLM-powered fact extraction from conversations
- Memory categories (biographical, preferences, temporal, relational)
- Optional cognitive features (importance scoring, reinforcement, decay, merging)
- Chat orchestration with short-term + long-term memory (ChatEngine)

For GraphRAG (knowledge graph with $graphLookup traversal), see:
    from mdb_engine.graph import GraphService, get_graph_service

Dependencies:
    pip install pymongo openai google-genai
"""

# ---------------------------------------------------------------------------
# Tier 1 — Primary exports (what most developers need)
# ---------------------------------------------------------------------------

# The memory service — use MemoryService as the primary name
from .base import BaseMemoryService, MemoryServiceError

# ---------------------------------------------------------------------------
# Tier 2 — Advanced exports (commonly needed for richer apps)
# ---------------------------------------------------------------------------
from .chat_history import ChatHistoryService
from .cognitive import (
    CognitiveMemoryService,
    CognitiveMemoryServiceError,
    MemoryService,  # <-- preferred alias
)

# ---------------------------------------------------------------------------
# Tier 3 — Specialist features
#
# These are importable (for backwards compatibility) but are NOT in __all__
# so they won't clutter IDE autocomplete. Import from their submodules:
#
#   from mdb_engine.memory.reflective import ReflectiveMemory
#   from mdb_engine.memory.predictive import PredictiveMemory
#   from mdb_engine.memory.prospective import ProspectiveMemory
#   from mdb_engine.memory.shared import SharedMemory
#   from mdb_engine.memory.veto import MemoryVeto
#   from mdb_engine.memory.recall import QueryAwareRecall
#   from mdb_engine.memory.versioning import MemoryVersioning
#   from mdb_engine.memory.timeline import TimelineService
#   from mdb_engine.memory.consolidator import MemoryConsolidator
#   from mdb_engine.memory.prompt import SystemPromptGenerator
# ---------------------------------------------------------------------------
from .consolidator import MemoryConsolidator, MemoryConsolidatorError
from .extraction import MEMORY_CATEGORIES
from .hygiene import run_daily_hygiene
from .orchestrator import (
    ChatEngine,  # <-- preferred alias
    CognitiveEngine,
)
from .persona import PersonaEngine
from .predictive import PredictiveMemory, PredictiveMemoryError
from .procedural import (
    ProceduralMemory,
    ProceduralMemoryError,
    retrieve_procedural_memory,
)
from .prompt import SystemPromptGenerator
from .prospective import ProspectiveMemory, ProspectiveMemoryError
from .recall import QueryAwareRecall, QueryAwareRecallError
from .reflection import ReflectionService, ReflectionServiceError, create_reflection_service
from .reflective import ReflectiveMemory, ReflectiveMemoryError
from .service import get_memory_service
from .shared import SharedMemory, SharedMemoryError
from .strategies import (
    # Context dataclasses
    ConsolidationResult,
    # Built-in alternatives
    CustomWeightPersonaBlend,
    # Protocols (for type hints)
    DecayStrategy,
    ExponentialDecay,
    ExtractedFact,
    ExtractionContext,
    ExtractionStrategy,
    ImportanceContext,
    ImportanceStrategy,
    LinearDecay,
    # Default implementations
    LLMImportance,
    MemoryDocument,
    NoDecay,
    PerfectRecallScoring,
    PersonaStrategy,
    QueryContext,
    RecencyDecayScoring,
    ReflectionContext,
    ReflectionStats,
    ReflectionStrategy,
    RuleBasedImportance,
    ScoringStrategy,
    TimeCountReflection,
    WeightedPersonaBlend,
)
from .system import (
    CognitiveMemory,
    CognitiveMemoryError,
    MultiTierMemory,  # <-- preferred alias
)
from .timeline import TimelineService
from .versioning import MemoryVersioning, MemoryVersioningError
from .veto import MemoryVeto, MemoryVetoError

__all__ = [
    # ---- Tier 1: Primary (start here) ----
    "MemoryService",
    "ChatEngine",
    "BaseMemoryService",
    "MemoryServiceError",
    "get_memory_service",
    # Backwards-compatible names
    "CognitiveMemoryService",
    "CognitiveMemoryServiceError",
    "CognitiveEngine",
    # ---- Tier 2: Advanced ----
    "ChatHistoryService",
    "PersonaEngine",
    "ReflectionService",
    "ReflectionServiceError",
    "create_reflection_service",
    "ProceduralMemory",
    "ProceduralMemoryError",
    "retrieve_procedural_memory",
    "MultiTierMemory",
    "CognitiveMemory",
    "CognitiveMemoryError",
    "MEMORY_CATEGORIES",
    # ---- Tier 3: Pluggable Strategy API ----
    # Protocols
    "ScoringStrategy",
    "DecayStrategy",
    "ExtractionStrategy",
    "ImportanceStrategy",
    "PersonaStrategy",
    "ReflectionStrategy",
    # Default implementations
    "PerfectRecallScoring",
    "NoDecay",
    "LLMImportance",
    "WeightedPersonaBlend",
    "TimeCountReflection",
    # Built-in alternatives
    "RecencyDecayScoring",
    "ExponentialDecay",
    "LinearDecay",
    "RuleBasedImportance",
    "CustomWeightPersonaBlend",
    # Context dataclasses
    "MemoryDocument",
    "QueryContext",
    "ExtractionContext",
    "ExtractedFact",
    "ImportanceContext",
    "ReflectionStats",
    "ReflectionContext",
    "ConsolidationResult",
]
