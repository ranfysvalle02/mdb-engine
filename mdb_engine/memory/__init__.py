"""
Memory Service Module (Mem0 Integration)
-----------------------------------------
This module provides intelligent memory management using Mem0.ai.

Mem0 enables applications to:
- Store and retrieve user memories automatically
- Build knowledge graphs from conversations
- Provide context-aware responses based on user history

Key Features:
- **MongoDB Integration**: Uses MongoDB as the vector store (native integration with mdb-engine)
- **Standalone Operation**: Works with just MongoDB - no LLM required
- **Optional LLM Inference**: Can leverage LLM service for automatic memory
  extraction (set infer: false to disable)
- **Graph Support**: Optional knowledge graph construction for entity relationships

Dependencies:
    pip install mem0ai
"""

# Import service components (mem0 import is lazy within service.py)
from .service import Mem0MemoryService, Mem0MemoryServiceError, get_memory_service

__all__ = [
    "Mem0MemoryService",
    "Mem0MemoryServiceError",
    "get_memory_service",
]
