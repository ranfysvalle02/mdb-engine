"""
Data Deletion Service

Deletes or anonymizes user data for GDPR compliance (Right to Erasure).

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import asyncio
import logging
from datetime import datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import PyMongoError

from .discovery import DataDiscoveryService
from .helpers import (
    build_user_query,
    generate_anonymous_email,
    generate_anonymous_id,
)

logger = logging.getLogger(__name__)


class DataDeletionService:
    """
    Service for deleting or anonymizing user data.

    Supports hard delete, soft delete, and anonymization strategies.
    """

    def __init__(self, db: AsyncIOMotorDatabase):
        """
        Initialize data deletion service.

        Args:
            db: MongoDB database instance
        """
        self.db = db
        self.discovery_service = DataDiscoveryService(db)

    async def delete_user_data(
        self,
        user_identifier: str,
        identifier_type: str = "email",
        app_slug: str | None = None,
        anonymize: bool = False,
        soft_delete: bool = True,
        memory_service: Any | None = None,
        chat_history_collection: Any | None = None,
    ) -> dict[str, Any]:
        """
        Delete or anonymize user data.

        Args:
            user_identifier: User email or user_id
            identifier_type: Type of identifier ("email" or "user_id")
            app_slug: Optional app slug to scope deletion
            anonymize: If True, anonymize instead of delete
            soft_delete: If True, mark as deleted (for legal retention)
            memory_service: Optional memory service instance for deleting memories
            chat_history_collection: Optional chat history collection

        Returns:
            Dictionary with deletion results
        """
        # Discover collections with user data
        collections = await self.discovery_service.discover_user_collections(
            user_identifier, identifier_type, app_slug
        )

        deletion_results = {
            "collections_processed": [],
            "documents_deleted": 0,
            "documents_anonymized": 0,
            "documents_soft_deleted": 0,
            "errors": [],
            "deleted_at": datetime.utcnow().isoformat(),
        }

        # Build query
        query = build_user_query(user_identifier, identifier_type)

        # Get user_id if we have email (needed for memory service)
        user_id = user_identifier
        if identifier_type == "email":
            user_id = await self.discovery_service.get_user_id_from_email(user_identifier, app_slug)
            if not user_id:
                # Try using email as user_id fallback
                user_id = user_identifier

        # Process each collection
        for collection_info in collections:
            collection_name = collection_info["name"]
            try:
                collection = self.db[collection_name]

                if anonymize:
                    # Anonymize data
                    anonymous_id = generate_anonymous_id(user_identifier)
                    anonymous_email = generate_anonymous_email(user_identifier)

                    update = {
                        "$set": {
                            "email": anonymous_email,
                            "user_email": anonymous_email,
                            "user_id": f"deleted_{anonymous_id}",
                            "anonymized_at": datetime.utcnow(),
                            "gdpr_anonymized": True,
                        },
                        "$unset": {
                            "password_hash": "",
                            "password": "",
                        },
                    }

                    result = await collection.update_many(query, update)
                    deletion_results["documents_anonymized"] += result.modified_count
                    logger.info(
                        f"Anonymized {result.modified_count} documents in "
                        f"collection '{collection_name}'"
                    )

                elif soft_delete:
                    # Soft delete (mark as deleted)
                    update = {
                        "$set": {
                            "deleted_at": datetime.utcnow(),
                            "deleted": True,
                            "gdpr_deleted": True,
                        }
                    }
                    result = await collection.update_many(query, update)
                    deletion_results["documents_soft_deleted"] += result.modified_count
                    logger.info(
                        f"Soft deleted {result.modified_count} documents in "
                        f"collection '{collection_name}'"
                    )

                else:
                    # Hard delete (permanent removal)
                    result = await collection.delete_many(query)
                    deletion_results["documents_deleted"] += result.deleted_count
                    logger.info(
                        f"Hard deleted {result.deleted_count} documents in "
                        f"collection '{collection_name}'"
                    )

                deletion_results["collections_processed"].append(collection_name)

            except PyMongoError as e:
                error_info = {
                    "collection": collection_name,
                    "error": str(e),
                }
                deletion_results["errors"].append(error_info)
                logger.exception(f"Error deleting from '{collection_name}'")
                continue

        # Delete from memory service if available
        if memory_service and user_id:
            try:
                # Pass deletion strategy to memory service
                # Hard delete memories if not soft_delete (GDPR compliance)
                hard_delete_memories = not soft_delete

                # Use asyncio.to_thread if delete_all is synchronous
                if asyncio.iscoroutinefunction(memory_service.delete_all):
                    success = await memory_service.delete_all(
                        user_id=user_id, hard_delete=hard_delete_memories
                    )
                else:
                    success = await asyncio.to_thread(
                        memory_service.delete_all, user_id=user_id, hard_delete=hard_delete_memories
                    )

                if success:
                    deletion_results["collections_processed"].append("memory_service")
                    deletion_type = "hard deleted" if hard_delete_memories else "soft deleted"
                    logger.info(
                        f"{deletion_type.capitalize()} memories for user '{user_id}' "
                        f"(strategy: {'hard_delete' if hard_delete_memories else 'soft_delete'})"
                    )
                else:
                    deletion_results["errors"].append(
                        {
                            "collection": "memory_service",
                            "error": "delete_all returned False",
                        }
                    )
            except (
                AttributeError,
                TypeError,
                ValueError,
                RuntimeError,
            ) as e:
                deletion_results["errors"].append(
                    {
                        "collection": "memory_service",
                        "error": str(e),
                    }
                )
                logger.exception("Error deleting memory data")

        # Delete from chat history if available
        if chat_history_collection is not None and user_id:
            try:
                chat_query = {"user_id": str(user_id)}
                if anonymize:
                    # Anonymize chat history
                    anonymous_id = generate_anonymous_id(user_identifier)
                    update = {
                        "$set": {
                            "user_id": f"deleted_{anonymous_id}",
                            "anonymized_at": datetime.utcnow(),
                            "gdpr_anonymized": True,
                        }
                    }
                    result = await chat_history_collection.update_many(chat_query, update)
                    deletion_results["documents_anonymized"] += result.modified_count
                elif soft_delete:
                    update = {
                        "$set": {
                            "deleted_at": datetime.utcnow(),
                            "deleted": True,
                            "gdpr_deleted": True,
                        }
                    }
                    result = await chat_history_collection.update_many(chat_query, update)
                    deletion_results["documents_soft_deleted"] += result.modified_count
                else:
                    result = await chat_history_collection.delete_many(chat_query)
                    deletion_results["documents_deleted"] += result.deleted_count

                deletion_results["collections_processed"].append("chat_history")
                logger.info(f"Deleted chat history for user '{user_id}'")
            except (PyMongoError, AttributeError, TypeError, ValueError) as e:
                deletion_results["errors"].append(
                    {
                        "collection": "chat_history",
                        "error": str(e),
                    }
                )
                logger.exception("Error deleting chat history")

        return deletion_results
