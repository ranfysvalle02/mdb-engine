"""
GDPR Compliance Module

Provides GDPR compliance helpers for user data management including:
- Data discovery across collections
- Data export (Right to Access)
- Data deletion (Right to Erasure)
- Data rectification (Right to Rectification)

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from .deletion import DataDeletionService
from .discovery import DataDiscoveryService
from .export import DataExportService
from .helpers import build_user_query, generate_anonymous_id
from .rectification import DataRectificationService

__all__ = [
    "DataDiscoveryService",
    "DataExportService",
    "DataDeletionService",
    "DataRectificationService",
    "build_user_query",
    "generate_anonymous_id",
]
