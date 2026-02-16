"""
Data Rectification Service

Updates user data across collections for GDPR compliance (Right to Rectification).

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import logging
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import PyMongoError

from .discovery import DataDiscoveryService
from .helpers import build_user_query

logger = logging.getLogger(__name__)


class DataRectificationService:
    """
    Service for updating user data across collections.

    Supports field-level updates with validation.
    """

    def __init__(self, db: AsyncIOMotorDatabase):
        """
        Initialize data rectification service.

        Args:
            db: MongoDB database instance
        """
        self.db = db
        self.discovery_service = DataDiscoveryService(db)

    async def update_user_data(
        self,
        user_identifier: str,
        updates: dict[str, Any],
        identifier_type: str = "email",
        app_slug: str | None = None,
    ) -> dict[str, Any]:
        """
        Update user data across collections.

        Args:
            user_identifier: User email or user_id
            updates: Dictionary of field updates
            identifier_type: Type of identifier ("email" or "user_id")
            app_slug: Optional app slug to scope updates

        Returns:
            Dictionary with update results
        """
        # Validate updates
        if not updates or not isinstance(updates, dict):
            raise ValueError("Updates must be a non-empty dictionary")

        # Discover collections with user data
        collections = await self.discovery_service.discover_user_collections(user_identifier, identifier_type, app_slug)

        update_results = {
            "collections_processed": [],
            "documents_updated": 0,
            "errors": [],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        # Build query
        query = build_user_query(user_identifier, identifier_type)

        # Prepare update document
        # Add updated_at timestamp
        update_doc = {
            "$set": {
                **updates,
                "updated_at": datetime.now(timezone.utc),
                "gdpr_updated": True,
            }
        }

        # Process each collection
        for collection_info in collections:
            collection_name = collection_info["name"]
            try:
                collection = self.db[collection_name]

                # Update all matching documents
                result = await collection.update_many(query, update_doc)

                if result.modified_count > 0:
                    update_results["documents_updated"] += result.modified_count
                    update_results["collections_processed"].append(collection_name)
                    logger.info(f"Updated {result.modified_count} documents in " f"collection '{collection_name}'")
            except PyMongoError as e:
                error_info = {
                    "collection": collection_name,
                    "error": str(e),
                }
                update_results["errors"].append(error_info)
                logger.exception(f"Error updating collection '{collection_name}'")
                continue

        return update_results
