"""
Index Management Module

Provides index management utilities for MongoDB Atlas Search and regular indexes.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

# Re-export index managers from database module for convenience
from ..database.scoped_wrapper import AsyncAtlasIndexManager, AutoIndexManager

# Export high-level management functions
from .manager import normalize_json_def, run_index_creation_for_collection

__all__ = [
    # Index managers
    "AsyncAtlasIndexManager",
    "AutoIndexManager",
    # Management functions
    "normalize_json_def",
    "run_index_creation_for_collection",
]
