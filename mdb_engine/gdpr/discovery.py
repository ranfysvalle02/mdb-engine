"""
Data Discovery Service

Discovers all collections containing user data for GDPR compliance.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import logging
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import PyMongoError

from .helpers import KNOWN_USER_COLLECTIONS, build_user_query, should_skip_collection

logger = logging.getLogger(__name__)


class DataDiscoveryService:
    """
    Service for discovering user data across collections.

    Scans all collections in the database to find documents containing
    user data based on email or user_id.
    """

    def __init__(self, db: AsyncIOMotorDatabase):
        """
        Initialize data discovery service.

        Args:
            db: MongoDB database instance
        """
        self.db = db

    async def discover_user_collections(
        self,
        user_identifier: str,
        identifier_type: str = "email",
        app_slug: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Discover all collections containing user data.

        Args:
            user_identifier: User email or user_id
            identifier_type: Type of identifier ("email" or "user_id")
            app_slug: Optional app slug to scope discovery

        Returns:
            List of collection info dictionaries with name and document_count
        """
        collections = []

        # Get all collection names
        try:
            collection_names = await self.db.list_collection_names()
        except PyMongoError:
            logger.exception("Failed to list collections")
            return collections

        # Filter by app_slug if provided
        if app_slug:
            collection_names = [
                name
                for name in collection_names
                if name.startswith(f"{app_slug}_") or name in KNOWN_USER_COLLECTIONS
            ]

        # Build query
        query = build_user_query(user_identifier, identifier_type)

        # Check each collection
        for collection_name in collection_names:
            # Skip system collections
            if should_skip_collection(collection_name):
                continue

            try:
                collection = self.db[collection_name]

                # Count matching documents
                count = await collection.count_documents(query)

                if count > 0:
                    collections.append(
                        {
                            "name": collection_name,
                            "document_count": count,
                        }
                    )
                    logger.debug(
                        f"Found {count} documents in collection '{collection_name}' "
                        f"for user '{user_identifier}'"
                    )
            except PyMongoError as e:
                logger.warning(f"Error checking collection '{collection_name}': {e}. Skipping.")
                continue

        return collections

    async def get_user_id_from_email(
        self,
        email: str,
        app_slug: str | None = None,
    ) -> str | None:
        """
        Get user_id from email by checking user collections.

        Args:
            email: User email
            app_slug: Optional app slug to scope lookup

        Returns:
            User ID as string if found, None otherwise
        """
        # Check app-specific users collection first
        if app_slug:
            try:
                # Try to get users collection using getattr (works with both
                # AsyncIOMotorDatabase and ScopedMongoWrapper)
                users_collection = getattr(self.db, "users", None)
                if users_collection is not None and hasattr(users_collection, "find_one"):
                    user = await users_collection.find_one({"email": email})
                    if user and "_id" in user:
                        return str(user["_id"])
            except (AttributeError, TypeError, ValueError, KeyError) as e:
                logger.debug(f"Could not check app users collection: {e}")

        # Check shared_users collection
        try:
            shared_users = getattr(self.db, "shared_users", None)
            if shared_users is not None and hasattr(shared_users, "find_one"):
                user = await shared_users.find_one({"email": email})
                if user and "_id" in user:
                    return str(user["_id"])
        except (AttributeError, TypeError, ValueError, KeyError) as e:
            logger.debug(f"Could not check shared_users collection: {e}")

        # Check users collection (top-level)
        try:
            users = getattr(self.db, "users", None)
            if users is not None and hasattr(users, "find_one"):
                user = await users.find_one({"email": email})
                if user and "_id" in user:
                    return str(user["_id"])
        except (AttributeError, TypeError, ValueError, KeyError) as e:
            logger.debug(f"Could not check users collection: {e}")

        return None
