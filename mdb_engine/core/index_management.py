"""
Index management for MongoDB Engine.

This module handles index creation and validation for apps.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import asyncio
import logging
from typing import TYPE_CHECKING

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import (
    ConnectionFailure,
    InvalidOperation,
    OperationFailure,
    ServerSelectionTimeoutError,
)

from ..indexes import run_index_creation_for_collection

if TYPE_CHECKING:
    from .types import ManifestDict

logger = logging.getLogger(__name__)


class IndexManager:
    """
    Manages index creation for apps.

    Handles index validation and creation from manifest definitions.
    """

    def __init__(self, mongo_db: AsyncIOMotorDatabase) -> None:
        """
        Initialize the index manager.

        Args:
            mongo_db: MongoDB database instance
        """
        self._mongo_db = mongo_db

    async def create_app_indexes(self, slug: str, manifest: "ManifestDict") -> None:
        """
        Create managed indexes for an app.

        Args:
            slug: App slug
            manifest: App manifest
        """
        # Import validate_managed_indexes from manifest module
        from .manifest import validate_managed_indexes

        managed_indexes = manifest.get("managed_indexes", {})
        logger.info(
            f"[{slug}] Creating app indexes. managed_indexes keys: "
            f"{list(managed_indexes.keys()) if managed_indexes else 'None'}"
        )
        if not managed_indexes:
            logger.warning(
                f"[{slug}] No 'managed_indexes' found in manifest. " f"Skipping index creation."
            )
            return

        # Check for conflicts with memory service managed indexes
        memory_config = manifest.get("memory_config", {})
        if memory_config.get("enabled", False):
            memory_collection_base = memory_config.get("collection_name", "memories")
            # Remove slug prefix if present (it will be added automatically)
            if memory_collection_base.startswith(f"{slug}_"):
                memory_collection_base = memory_collection_base[len(f"{slug}_") :]

            # Check if user is trying to manually define indexes for memory collection
            if memory_collection_base in managed_indexes:
                indexes = managed_indexes[memory_collection_base]
                # Check if any index is a vectorSearch index (memory service manages these)
                for idx_def in indexes:
                    if idx_def.get("type") in ("vectorSearch", "search"):
                        raise ValueError(
                            f"❌ Cannot manually define vector search indexes for memory "
                            f"collection '{memory_collection_base}'. "
                            f"The memory service automatically manages its own vector search "
                            f"index. Please remove the '{memory_collection_base}' entry from "
                            f"'managed_indexes' in your manifest.json. "
                            f"The memory service will automatically create the required index "
                            f"'{slug}_{memory_collection_base}_vector_index'."
                        )
                # Also check for any indexes on the prefixed collection name
                # (user might have used full name)
                prefixed_memory_collection = f"{slug}_{memory_collection_base}"
                if prefixed_memory_collection in managed_indexes:
                    indexes = managed_indexes[prefixed_memory_collection]
                    for idx_def in indexes:
                        if idx_def.get("type") in ("vectorSearch", "search"):
                            raise ValueError(
                                f"❌ Cannot manually define vector search indexes for memory "
                                f"collection '{prefixed_memory_collection}'. "
                                f"The memory service automatically manages its own vector search "
                                f"index. Please remove the '{prefixed_memory_collection}' entry "
                                f"from 'managed_indexes' in your manifest.json. "
                                f"The memory service will automatically create the required index "
                                f"'{prefixed_memory_collection}_vector_index'."
                            )

        # Validate indexes
        logger.info(
            f"[{slug}] Validating {len(managed_indexes)} collection(s) " f"with managed indexes..."
        )
        is_valid, error = validate_managed_indexes(managed_indexes)
        logger.info(f"[{slug}] Index validation result: is_valid={is_valid}, " f"error={error}")
        if not is_valid:
            logger.error(
                f"[{slug}] ❌ Invalid 'managed_indexes' configuration: {error}. "
                f"Skipping index creation."
            )
            return

        # Create indexes for each collection
        logger.info(
            f"[{slug}] Processing {len(managed_indexes)} collection(s) " f"with managed indexes"
        )
        for collection_base_name, indexes in managed_indexes.items():
            logger.info(
                f"[{slug}] Processing collection '{collection_base_name}' "
                f"with {len(indexes)} index definition(s)"
            )
            if not collection_base_name or not isinstance(indexes, list):
                logger.warning(
                    f"[{slug}] Invalid 'managed_indexes' for "
                    f"'{collection_base_name}': "
                    f"collection_base_name={collection_base_name}, "
                    f"indexes={indexes}"
                )
                continue

            prefixed_collection_name = f"{slug}_{collection_base_name}"
            prefixed_defs = []
            logger.debug(f"[{slug}] Prefixed collection name: '{prefixed_collection_name}'")

            for idx_def in indexes:
                idx_n = idx_def.get("name")
                idx_type = idx_def.get("type")
                logger.debug(
                    f"[{slug}] Processing index def: name={idx_n}, "
                    f"type={idx_type}, def={idx_def}"
                )
                if not idx_n or not idx_type:
                    logger.warning(
                        f"[{slug}] Skipping malformed index def in "
                        f"'{collection_base_name}': "
                        f"name={idx_n}, type={idx_type}"
                    )
                    continue

                idx_copy = idx_def.copy()
                idx_copy["name"] = f"{slug}_{idx_n}"
                prefixed_defs.append(idx_copy)
                logger.debug(f"[{slug}] Added prefixed index: '{idx_copy['name']}'")

            if not prefixed_defs:
                logger.warning(
                    f"[{slug}] No valid index definitions for "
                    f"'{collection_base_name}'. Skipping."
                )
                continue

            logger.info(
                f"[{slug}] Creating {len(prefixed_defs)} index(es) for "
                f"'{prefixed_collection_name}'..."
            )
            try:
                await run_index_creation_for_collection(
                    db=self._mongo_db,
                    slug=slug,
                    collection_name=prefixed_collection_name,
                    index_definitions=prefixed_defs,
                )
                # Wait a bit after all indexes are created to ensure they're all ready
                await asyncio.sleep(1.0)  # Extra wait after all indexes are created
                logger.info(
                    f"[{slug}] ✅ Completed index creation for " f"'{prefixed_collection_name}'"
                )
            except (
                OperationFailure,
                ConnectionFailure,
                ServerSelectionTimeoutError,
                InvalidOperation,
                ValueError,
                TypeError,
            ) as e:
                logger.error(
                    f"[{slug}] Error creating indexes for '{prefixed_collection_name}': {e}",
                    exc_info=True,
                )
                # Re-raise to surface errors in tests
                raise
