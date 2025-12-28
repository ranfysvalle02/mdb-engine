"""
Initial Data Seeding

Provides utilities for seeding initial data into collections based on manifest configuration.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List

from pymongo.errors import (ConnectionFailure, OperationFailure,
                            ServerSelectionTimeoutError)

logger = logging.getLogger(__name__)


async def seed_initial_data(
    db, app_slug: str, initial_data: Dict[str, List[Dict[str, Any]]]
) -> Dict[str, int]:
    """
    Seed initial data into collections.

    This function:
    1. Checks if each collection is empty
    2. Only seeds if collection is empty (idempotent)
    3. Tracks seeded collections in metadata collection
    4. Handles datetime conversion for seed data

    Args:
        db: Database wrapper (ScopedMongoWrapper or AppDB)
        app_slug: App slug identifier
        initial_data: Dictionary mapping collection names to arrays of documents

    Returns:
        Dictionary mapping collection names to number of documents inserted
    """
    results = {}

    # Check metadata collection to see if we've already seeded
    # Use a non-underscore name to avoid ScopedMongoWrapper attribute access restrictions
    metadata_collection_name = "app_seeding_metadata"
    # Use getattr to access collection (works with ScopedMongoWrapper and AppDB)
    metadata_collection = getattr(db, metadata_collection_name)

    # Get existing seeding metadata
    seeding_metadata = await metadata_collection.find_one({"app_slug": app_slug})
    seeded_collections = (
        set(seeding_metadata.get("seeded_collections", []))
        if seeding_metadata
        else set()
    )

    for collection_name, documents in initial_data.items():
        try:
            # Check if already seeded
            if collection_name in seeded_collections:
                logger.debug(
                    f"Collection '{collection_name}' already seeded for {app_slug}, skipping"
                )
                results[collection_name] = 0
                continue

            # Get collection
            collection = getattr(db, collection_name)

            # Check if collection is empty (idempotent check)
            count = await collection.count_documents({})
            if count > 0:
                logger.info(
                    f"Collection '{collection_name}' is not empty "
                    f"({count} documents) for {app_slug}, "
                    f"skipping seed to avoid duplicates"
                )
                # Mark as seeded even though we didn't insert (collection already has data)
                seeded_collections.add(collection_name)
                results[collection_name] = 0
                continue

            # Prepare documents for insertion
            # Convert datetime strings to datetime objects if needed
            prepared_docs = []
            for doc in documents:
                prepared_doc = doc.copy()

                # Convert datetime strings to datetime objects
                for key, value in prepared_doc.items():
                    if isinstance(value, str):
                        # Try to parse ISO format datetime strings
                        try:
                            # Check if it looks like a datetime string
                            if "T" in value and (
                                "Z" in value or "+" in value or "-" in value[-6:]
                            ):
                                # Try parsing as ISO format
                                from dateutil.parser import parse as parse_date

                                prepared_doc[key] = parse_date(value)
                        except (ValueError, ImportError):
                            # Not a datetime string or dateutil not available, keep as string
                            pass
                    elif isinstance(value, dict) and "$date" in value:
                        # MongoDB extended JSON format
                        try:
                            from dateutil.parser import parse as parse_date

                            prepared_doc[key] = parse_date(value["$date"])
                        except (ValueError, ImportError):
                            pass

                # Add created_at if not present and document doesn't have timestamp fields
                if (
                    "created_at" not in prepared_doc
                    and "date_created" not in prepared_doc
                ):
                    prepared_doc["created_at"] = datetime.utcnow()

                prepared_docs.append(prepared_doc)

            # Insert documents
            if prepared_docs:
                result = await collection.insert_many(prepared_docs)
                inserted_count = len(result.inserted_ids)
                results[collection_name] = inserted_count

                # Mark as seeded
                seeded_collections.add(collection_name)

                logger.info(
                    f"✅ Seeded {inserted_count} document(s) into collection "
                    f"'{collection_name}' for {app_slug}"
                )
            else:
                results[collection_name] = 0
                logger.warning(
                    f"No documents to seed for collection '{collection_name}' in {app_slug}"
                )

        except (
            OperationFailure,
            ConnectionFailure,
            ServerSelectionTimeoutError,
            Exception,
        ) as e:
            # Type 2: Recoverable - log error and continue with other collections
            logger.exception(
                f"Failed to seed collection '{collection_name}' for {app_slug}: {e}"
            )
            results[collection_name] = 0

    # Update seeding metadata
    try:
        if seeding_metadata:
            await metadata_collection.update_one(
                {"app_slug": app_slug},
                {"$set": {"seeded_collections": list(seeded_collections)}},
            )
        else:
            await metadata_collection.insert_one(
                {
                    "app_slug": app_slug,
                    "seeded_collections": list(seeded_collections),
                    "created_at": datetime.utcnow(),
                }
            )
    except (
        OperationFailure,
        ConnectionFailure,
        ServerSelectionTimeoutError,
        ValueError,
        TypeError,
        KeyError,
    ) as e:
        logger.warning(
            f"Failed to update seeding metadata for {app_slug}: {e}", exc_info=True
        )

    return results
