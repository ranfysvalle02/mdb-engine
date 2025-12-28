"""
Index Management Orchestration

High-level functions for creating and managing indexes based on manifest definitions.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import json
import logging
from typing import Any, Dict, List

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import (CollectionInvalid, ConnectionFailure,
                            OperationFailure, ServerSelectionTimeoutError)

# Import constants
from ..constants import INDEX_TYPE_REGULAR, INDEX_TYPE_TTL, MIN_TTL_SECONDS

# Import index manager from database module
try:
    from ..database.scoped_wrapper import AsyncAtlasIndexManager
except ImportError:
    AsyncAtlasIndexManager = None
    logging.warning("AsyncAtlasIndexManager not available")

# Import helper functions
from .helpers import (check_and_update_index, is_id_index, normalize_keys,
                      validate_index_definition_basic)

# Check if index manager is available
INDEX_MANAGER_AVAILABLE = AsyncAtlasIndexManager is not None

logger = logging.getLogger(__name__)


async def _handle_regular_index(
    index_manager: AsyncAtlasIndexManager,
    index_def: Dict[str, Any],
    index_name: str,
    log_prefix: str,
) -> None:
    """Handle creation of a regular index."""
    logger.info(f"{log_prefix} _handle_regular_index called for '{index_name}'")
    keys = index_def.get("keys")
    logger.info(f"{log_prefix} Index keys: {keys}, index_def: {index_def}")

    is_valid, error_msg = validate_index_definition_basic(
        index_def, index_name, ["keys"], log_prefix
    )
    logger.info(
        f"{log_prefix} Validation result: is_valid={is_valid}, "
        f"error_msg={error_msg}"
    )
    if not is_valid:
        logger.error(f"{log_prefix} ❌ Validation failed: {error_msg}")
        return

    if is_id_index(keys):
        logger.info(
            f"{log_prefix} Skipping '_id' index '{index_name}'. "
            f"MongoDB automatically creates '_id' indexes on all "
            f"collections and they cannot be customized. "
            f"This is expected behavior - no action needed."
        )
        return

    # Get wait_for_ready from index definition (default: True for managed indexes)
    wait_for_ready = index_def.get("wait_for_ready", True)

    options = {
        **index_def.get("options", {}),
        "name": index_name,
        "wait_for_ready": wait_for_ready,
    }
    logger.debug(f"{log_prefix} Checking if index '{index_name}' exists...")
    exists, existing = await check_and_update_index(
        index_manager, index_name, keys, options, log_prefix
    )
    logger.debug(
        f"{log_prefix} Index exists check result: exists={exists}, "
        f"existing={existing}"
    )

    if exists and existing:
        logger.info(f"{log_prefix} Regular index '{index_name}' matches; skipping.")
        return

    logger.info(
        f"{log_prefix} Creating regular index '{index_name}' with keys {keys} "
        f"and options {options}..."
    )
    try:
        created_name = await index_manager.create_index(keys, **options)
        logger.info(
            f"{log_prefix} ✔️ Created regular index '{created_name}' "
            f"(requested: '{index_name}')."
        )

        # Wait for index to be ready and verify it was actually created
        import asyncio

        max_wait = 10  # Wait up to 10 seconds for index to be ready
        poll_interval = 0.5
        waited = 0

        while waited < max_wait:
            await asyncio.sleep(poll_interval)
            waited += poll_interval

            all_indexes = await index_manager.list_indexes()
            verify_index = await index_manager.get_index(index_name)

            if verify_index:
                logger.info(
                    f"{log_prefix} ✅ Verified index '{index_name}' exists "
                    f"after {waited:.1f}s."
                )
                break

            logger.debug(
                f"{log_prefix} Waiting for index '{index_name}' to be ready... "
                f"({waited:.1f}s/{max_wait}s). "
                f"Available indexes: {[idx.get('name') for idx in all_indexes]}"
            )
        else:
            # Timeout - index still not found
            all_indexes = await index_manager.list_indexes()
            logger.error(
                f"{log_prefix} ❌ Index '{index_name}' was NOT found after "
                f"{max_wait}s! create_index returned '{created_name}' but index "
                f"is not visible. Available indexes: "
                f"{[idx.get('name') for idx in all_indexes]}"
            )
            raise RuntimeError(
                f"Index '{index_name}' was not found after {max_wait}s despite "
                f"create_index returning '{created_name}'"
            )
    except (
        OperationFailure,
        ConnectionFailure,
        ServerSelectionTimeoutError,
        RuntimeError,
        ValueError,
        TypeError,
    ) as e:
        logger.error(
            f"{log_prefix} ❌ Failed to create regular index '{index_name}': {e}",
            exc_info=True,
        )
        raise


async def _handle_ttl_index(
    index_manager: AsyncAtlasIndexManager,
    index_def: Dict[str, Any],
    index_name: str,
    log_prefix: str,
) -> None:
    """Handle creation of a TTL index."""
    keys = index_def.get("keys")
    is_valid, error_msg = validate_index_definition_basic(
        index_def, index_name, ["keys"], log_prefix
    )
    if not is_valid:
        logger.warning(error_msg)
        return

    options = index_def.get("options", {})
    expire_after = options.get("expireAfterSeconds")
    if (
        not expire_after
        or not isinstance(expire_after, int)
        or expire_after < MIN_TTL_SECONDS
    ):
        logger.warning(
            f"{log_prefix} TTL index '{index_name}' missing or "
            f"invalid 'expireAfterSeconds' in options. "
            f"TTL indexes require 'options.expireAfterSeconds' to be "
            f"a positive integer. "
            f"Skipping this index definition."
        )
        return

    ttl_keys = normalize_keys(keys)
    index_options = {**options, "name": index_name}
    exists, existing = await check_and_update_index(
        index_manager, index_name, ttl_keys, index_options, log_prefix
    )
    if exists and existing:
        logger.info(f"{log_prefix} TTL index '{index_name}' matches; skipping.")
        return

    logger.info(
        f"{log_prefix} Creating TTL index '{index_name}' on field(s) {ttl_keys} "
        f"with expireAfterSeconds={expire_after}..."
    )
    await index_manager.create_index(ttl_keys, **index_options)
    logger.info(
        f"{log_prefix} ✔️ Created TTL index '{index_name}' "
        f"(expires after {expire_after} seconds)."
    )


async def _handle_partial_index(
    index_manager: AsyncAtlasIndexManager,
    index_def: Dict[str, Any],
    index_name: str,
    log_prefix: str,
) -> None:
    """Handle creation of a partial index."""
    keys = index_def.get("keys")
    if not keys:
        logger.warning(
            f"{log_prefix} Missing 'keys' field on partial index '{index_name}'. "
            f"Partial indexes require a 'keys' field. Skipping this index definition."
        )
        return

    options = index_def.get("options", {})
    partial_filter = options.get("partialFilterExpression")
    if not partial_filter:
        logger.warning(
            f"{log_prefix} Partial index '{index_name}' missing "
            f"'partialFilterExpression' in options. "
            f"Partial indexes require "
            f"'options.partialFilterExpression' to specify which "
            f"documents to index. "
            f"Skipping this index definition."
        )
        return

    if isinstance(keys, dict):
        partial_keys = [(k, v) for k, v in keys.items()]
    else:
        partial_keys = keys

    index_options = {**options, "name": index_name}
    existing_index = await index_manager.get_index(index_name)
    if existing_index:
        existing_key = existing_index.get("key", {})
        expected_key = (
            {k: v for k, v in partial_keys}
            if isinstance(partial_keys, list)
            else {k: v for k, v in keys.items()}
        )
        existing_partial = existing_index.get("partialFilterExpression")
        expected_partial = partial_filter

        if existing_key != expected_key or existing_partial != expected_partial:
            logger.warning(
                f"{log_prefix} Partial index '{index_name}' definition mismatch. "
                f"Existing: keys={existing_key}, filter={existing_partial}. "
                f"Expected: keys={expected_key}, filter={expected_partial}. "
                f"Dropping existing index and recreating."
            )
            await index_manager.drop_index(index_name)
        else:
            logger.info(f"{log_prefix} Partial index '{index_name}' matches; skipping.")
            return

    logger.info(
        f"{log_prefix} Creating partial index '{index_name}' "
        f"on field(s) {partial_keys} "
        f"with filter: {partial_filter}..."
    )
    await index_manager.create_index(partial_keys, **index_options)
    logger.info(f"{log_prefix} ✔️ Created partial index '{index_name}'.")


async def _handle_text_index(
    index_manager: AsyncAtlasIndexManager,
    index_def: Dict[str, Any],
    index_name: str,
    log_prefix: str,
) -> None:
    """Handle creation of a text index."""
    keys = index_def.get("keys")
    if not keys:
        logger.warning(
            f"{log_prefix} Missing 'keys' field on text index '{index_name}'. "
            f"Text indexes require a 'keys' field with at least one 'text' "
            f"type field. Skipping this index definition."
        )
        return

    if isinstance(keys, dict):
        text_keys = [(k, v) for k, v in keys.items()]
    else:
        text_keys = keys

    has_text = any(
        (
            isinstance(k, list)
            and len(k) >= 2
            and (k[1] == "text" or k[1] == "TEXT" or k[1] == 1)
        )
        or (
            isinstance(k, tuple)
            and len(k) >= 2
            and (k[1] == "text" or k[1] == "TEXT" or k[1] == 1)
        )
        for k in text_keys
    ) or any(
        v == "text" or v == "TEXT" or v == 1
        for k, v in (keys.items() if isinstance(keys, dict) else [])
    )

    if not has_text:
        logger.warning(
            f"{log_prefix} Text index '{index_name}' has no fields with "
            f"'text' type. At least one field must have type 'text'. "
            f"Skipping this index definition."
        )
        return

    options = {**index_def.get("options", {}), "name": index_name}
    existing_index = await index_manager.get_index(index_name)
    if existing_index:
        existing_key = existing_index.get("key", {})
        expected_key = (
            {k: v for k, v in text_keys}
            if isinstance(text_keys, list)
            else {k: v for k, v in keys.items()}
        )

        if existing_key != expected_key:
            logger.warning(
                f"{log_prefix} Text index '{index_name}' definition mismatch. "
                f"Existing keys: {existing_key}, Expected keys: {expected_key}. "
                f"Dropping existing index and recreating."
            )
            await index_manager.drop_index(index_name)
        else:
            logger.info(f"{log_prefix} Text index '{index_name}' matches; skipping.")
            return

    logger.info(
        f"{log_prefix} Creating text index '{index_name}' on field(s) {text_keys}..."
    )
    await index_manager.create_index(text_keys, **options)
    logger.info(f"{log_prefix} ✔️ Created text index '{index_name}'.")


async def _handle_geospatial_index(
    index_manager: AsyncAtlasIndexManager,
    index_def: Dict[str, Any],
    index_name: str,
    log_prefix: str,
) -> None:
    """Handle creation of a geospatial index."""
    keys = index_def.get("keys")
    if not keys:
        logger.warning(
            f"{log_prefix} Missing 'keys' field on geospatial index "
            f"'{index_name}'. Geospatial indexes require a 'keys' field with "
            f"at least one geospatial type ('2dsphere', '2d', 'geoHaystack'). "
            f"Skipping this index definition."
        )
        return

    if isinstance(keys, dict):
        geo_keys = [(k, v) for k, v in keys.items()]
    else:
        geo_keys = keys

    geo_types = ["2dsphere", "2d", "geoHaystack"]
    has_geo = any(
        (isinstance(k, list) and len(k) >= 2 and k[1] in geo_types)
        or (isinstance(k, tuple) and len(k) >= 2 and k[1] in geo_types)
        for k in geo_keys
    ) or any(
        v in geo_types for k, v in (keys.items() if isinstance(keys, dict) else [])
    )

    if not has_geo:
        logger.warning(
            f"{log_prefix} Geospatial index '{index_name}' has no fields with "
            f"geospatial type. At least one field must have type '2dsphere', "
            f"'2d', or 'geoHaystack'. Skipping this index definition."
        )
        return

    options = {**index_def.get("options", {}), "name": index_name}
    existing_index = await index_manager.get_index(index_name)
    if existing_index:
        existing_key = existing_index.get("key", {})
        expected_key = (
            {k: v for k, v in geo_keys}
            if isinstance(geo_keys, list)
            else {k: v for k, v in keys.items()}
        )

        if existing_key != expected_key:
            logger.warning(
                f"{log_prefix} Geospatial index '{index_name}' definition "
                f"mismatch. Existing keys: {existing_key}, Expected keys: "
                f"{expected_key}. Dropping existing index and recreating."
            )
            await index_manager.drop_index(index_name)
        else:
            logger.info(
                f"{log_prefix} Geospatial index '{index_name}' matches; skipping."
            )
            return

    logger.info(
        f"{log_prefix} Creating geospatial index '{index_name}' "
        f"on field(s) {geo_keys}..."
    )
    await index_manager.create_index(geo_keys, **options)
    logger.info(f"{log_prefix} ✔️ Created geospatial index '{index_name}'.")


async def _handle_search_index(
    index_manager: AsyncAtlasIndexManager,
    index_def: Dict[str, Any],
    index_name: str,
    index_type: str,
    slug: str,
    log_prefix: str,
) -> None:
    """Handle creation of a search or vectorSearch index."""
    definition = index_def.get("definition")
    if not definition:
        logger.warning(
            f"{log_prefix} Missing 'definition' field for {index_type} "
            f"index '{index_name}'. Atlas Search and Vector Search indexes "
            f"require a 'definition' object specifying fields and configuration. "
            f"Skipping this index definition."
        )
        return

    fields = definition.get("fields", [])
    has_app_id_filter = any(
        isinstance(f, dict) and f.get("type") == "filter" and f.get("path") == "app_id"
        for f in fields
    )
    if not has_app_id_filter:
        app_id_filter = {"type": "filter", "path": "app_id"}
        fields = [app_id_filter] + fields
        definition = {**definition, "fields": fields}
        logger.info(
            f"{log_prefix} Automatically added 'app_id' filter to "
            f"{index_type} index '{index_name}' "
            f"(required by scoped wrapper)."
        )
    existing_index = await index_manager.get_search_index(index_name)
    if existing_index:
        current_def = existing_index.get(
            "latestDefinition", existing_index.get("definition")
        )
        normalized_current = normalize_json_def(current_def)
        normalized_expected = normalize_json_def(definition)

        if normalized_current == normalized_expected:
            logger.info(f"{log_prefix} Search index '{index_name}' definition matches.")
            if (
                not existing_index.get("queryable")
                and existing_index.get("status") != "FAILED"
            ):
                logger.info(
                    f"{log_prefix} Index '{index_name}' not queryable yet; waiting."
                )
                await index_manager._wait_for_search_index_ready(
                    index_name, index_manager.DEFAULT_SEARCH_TIMEOUT
                )
                logger.info(f"{log_prefix} Index '{index_name}' now ready.")
            elif existing_index.get("status") == "FAILED":
                logger.error(
                    f"{log_prefix} Index '{index_name}' is in "
                    f"FAILED state. "
                    f"This indicates the index build failed - check "
                    f"Atlas UI for detailed error messages. "
                    f"Manual intervention required to resolve the "
                    f"issue before the index can be used."
                )
            else:
                logger.info(f"{log_prefix} Index '{index_name}' is ready.")
        else:
            logger.warning(
                f"{log_prefix} Search index '{index_name}' "
                f"definition changed; updating."
            )
            current_fields = (
                normalized_current.get("fields", [])
                if isinstance(normalized_current, dict)
                else []
            )
            expected_fields = (
                normalized_expected.get("fields", [])
                if isinstance(normalized_expected, dict)
                else []
            )

            current_paths = [
                f.get("path", "?") for f in current_fields if isinstance(f, dict)
            ]
            expected_paths = [
                f.get("path", "?") for f in expected_fields if isinstance(f, dict)
            ]

            logger.info(f"{log_prefix} Current index filter fields: {current_paths}")
            logger.info(f"{log_prefix} Expected index filter fields: {expected_paths}")
            logger.info(
                f"{log_prefix} Updating index '{index_name}' "
                f"with new definition (this may take a few moments)..."
            )

            # Type 4: Let index update errors bubble up to framework handler
            await index_manager.update_search_index(
                name=index_name,
                definition=definition,
                wait_for_ready=True,
            )
            logger.info(
                f"{log_prefix} ✔️ Successfully updated search index "
                f"'{index_name}'. Index is now ready."
            )
    else:
        logger.info(f"{log_prefix} Creating new search index '{index_name}'...")
        await index_manager.create_search_index(
            name=index_name,
            definition=definition,
            index_type=index_type,
            wait_for_ready=True,
        )
        logger.info(f"{log_prefix} ✔️ Created new '{index_type}' index '{index_name}'.")


async def _handle_hybrid_index(
    index_manager: AsyncAtlasIndexManager,
    index_def: Dict[str, Any],
    index_name: str,
    slug: str,
    log_prefix: str,
) -> None:
    """Handle creation of a hybrid index."""
    hybrid_config = index_def.get("hybrid")
    if not hybrid_config:
        logger.warning(
            f"{log_prefix} Missing 'hybrid' field for hybrid index '{index_name}'. "
            f"Hybrid indexes require a 'hybrid' object with 'vector_index' and "
            f"'text_index' definitions. Skipping this index definition."
        )
        return

    vector_index_config = hybrid_config.get("vector_index")
    text_index_config = hybrid_config.get("text_index")

    if not vector_index_config or not text_index_config:
        logger.warning(
            f"{log_prefix} Hybrid index '{index_name}' requires both "
            f"'vector_index' and 'text_index' in 'hybrid' field. "
            f"Skipping this index definition."
        )
        return

    vector_base_name = vector_index_config.get("name")
    text_base_name = text_index_config.get("name")

    if vector_base_name:
        if not vector_base_name.startswith(f"{slug}_"):
            vector_index_name = f"{slug}_{vector_base_name}"
        else:
            vector_index_name = vector_base_name
    else:
        vector_index_name = f"{index_name}_vector"

    if text_base_name:
        if not text_base_name.startswith(f"{slug}_"):
            text_index_name = f"{slug}_{text_base_name}"
        else:
            text_index_name = text_base_name
    else:
        text_index_name = f"{index_name}_text"

    vector_definition = vector_index_config.get("definition")
    text_definition = text_index_config.get("definition")

    if not vector_definition or not text_definition:
        logger.warning(
            f"{log_prefix} Hybrid index '{index_name}' requires 'definition' in "
            f"both 'vector_index' and 'text_index'. "
            f"Skipping this index definition."
        )
        return

    # Process vector index
    logger.info(
        f"{log_prefix} Processing vector index '{vector_index_name}' "
        f"for hybrid search..."
    )
    existing_vector_index = await index_manager.get_search_index(vector_index_name)
    if existing_vector_index:
        current_vector_def = existing_vector_index.get(
            "latestDefinition", existing_vector_index.get("definition")
        )
        normalized_current_vector = normalize_json_def(current_vector_def)
        normalized_expected_vector = normalize_json_def(vector_definition)

        if normalized_current_vector == normalized_expected_vector:
            logger.info(
                f"{log_prefix} Vector index '{vector_index_name}' definition matches."
            )
            if (
                not existing_vector_index.get("queryable")
                and existing_vector_index.get("status") != "FAILED"
            ):
                logger.info(
                    f"{log_prefix} Vector index '{vector_index_name}' "
                    f"not queryable yet; waiting."
                )
                await index_manager._wait_for_search_index_ready(
                    vector_index_name, index_manager.DEFAULT_SEARCH_TIMEOUT
                )
                logger.info(
                    f"{log_prefix} Vector index '{vector_index_name}' now ready."
                )
            elif existing_vector_index.get("status") == "FAILED":
                logger.error(
                    f"{log_prefix} Vector index '{vector_index_name}' "
                    f"is in FAILED state. "
                    f"Check Atlas UI for detailed error messages."
                )
            else:
                logger.info(
                    f"{log_prefix} Vector index '{vector_index_name}' is ready."
                )
        else:
            logger.warning(
                f"{log_prefix} Vector index '{vector_index_name}' "
                f"definition changed; updating."
            )
            # Type 4: Let index update errors bubble up to framework handler
            await index_manager.update_search_index(
                name=vector_index_name,
                definition=vector_definition,
                wait_for_ready=True,
            )
            logger.info(
                f"{log_prefix} ✔️ Successfully updated vector index "
                f"'{vector_index_name}'."
            )
    else:
        logger.info(f"{log_prefix} Creating new vector index '{vector_index_name}'...")
        await index_manager.create_search_index(
            name=vector_index_name,
            definition=vector_definition,
            index_type="vectorSearch",
            wait_for_ready=True,
        )
        logger.info(f"{log_prefix} ✔️ Created vector index '{vector_index_name}'.")

    # Process text index
    logger.info(
        f"{log_prefix} Processing text index '{text_index_name}' "
        f"for hybrid search..."
    )
    existing_text_index = await index_manager.get_search_index(text_index_name)
    if existing_text_index:
        current_text_def = existing_text_index.get(
            "latestDefinition", existing_text_index.get("definition")
        )
        normalized_current_text = normalize_json_def(current_text_def)
        normalized_expected_text = normalize_json_def(text_definition)

        if normalized_current_text == normalized_expected_text:
            logger.info(
                f"{log_prefix} Text index '{text_index_name}' definition matches."
            )
            if (
                not existing_text_index.get("queryable")
                and existing_text_index.get("status") != "FAILED"
            ):
                logger.info(
                    f"{log_prefix} Text index '{text_index_name}' "
                    f"not queryable yet; waiting."
                )
                await index_manager._wait_for_search_index_ready(
                    text_index_name, index_manager.DEFAULT_SEARCH_TIMEOUT
                )
                logger.info(f"{log_prefix} Text index '{text_index_name}' now ready.")
            elif existing_text_index.get("status") == "FAILED":
                logger.error(
                    f"{log_prefix} Text index '{text_index_name}' is in FAILED "
                    f"state. Check Atlas UI for detailed error messages."
                )
            else:
                logger.info(f"{log_prefix} Text index '{text_index_name}' is ready.")
        else:
            logger.warning(
                f"{log_prefix} Text index '{text_index_name}' "
                f"definition changed; updating."
            )
            # Type 4: Let index update errors bubble up to framework handler
            await index_manager.update_search_index(
                name=text_index_name,
                definition=text_definition,
                wait_for_ready=True,
            )
            logger.info(
                f"{log_prefix} ✔️ Successfully updated text index "
                f"'{text_index_name}'."
            )
    else:
        logger.info(f"{log_prefix} Creating new text index '{text_index_name}'...")
        await index_manager.create_search_index(
            name=text_index_name,
            definition=text_definition,
            index_type="search",
            wait_for_ready=True,
        )
        logger.info(f"{log_prefix} ✔️ Created text index '{text_index_name}'.")

    logger.info(
        f"{log_prefix} ✔️ Hybrid search indexes ready: "
        f"'{vector_index_name}' (vector) and "
        f"'{text_index_name}' (text)."
    )


def normalize_json_def(obj: Any) -> Any:
    """
    Normalize a JSON-serializable object for comparison by:
    1. Converting to JSON string (which sorts dict keys)
    2. Parsing back to dict/list
    This makes comparisons order-insensitive and format-insensitive.
    """
    try:
        return json.loads(json.dumps(obj, sort_keys=True))
    except (TypeError, ValueError) as e:
        # If it can't be serialized, return as-is for fallback comparison
        logger.warning(f"Could not normalize JSON def: {e}")
        return obj


async def run_index_creation_for_collection(
    db: AsyncIOMotorDatabase,
    slug: str,
    collection_name: str,
    index_definitions: List[Dict[str, Any]],
):
    """Create or update indexes for a collection based on index definitions."""
    log_prefix = f"[{slug} -> {collection_name}]"

    if not INDEX_MANAGER_AVAILABLE:
        logger.warning(f"{log_prefix} Index Manager not available.")
        return

    try:
        real_collection = db[collection_name]
        # Ensure collection exists before creating indexes
        # MongoDB will create the collection if it doesn't exist, but we need to
        # ensure it exists for index operations. We can do this by inserting and
        # deleting a dummy document, or by creating the collection explicitly.
        try:
            # Try to create the collection explicitly
            await db.create_collection(collection_name)
            logger.debug(
                f"{log_prefix} Created collection '{collection_name}' "
                f"for index operations."
            )
        except CollectionInvalid as e:
            if "already exists" in str(e).lower():
                # Collection already exists, which is fine
                logger.debug(
                    f"{log_prefix} Collection '{collection_name}' already exists."
                )
            else:
                # Some other CollectionInvalid error - log but continue
                logger.warning(
                    f"{log_prefix} CollectionInvalid when ensuring collection "
                    f"exists: {e}"
                )
        except (
            OperationFailure,
            ConnectionFailure,
            ServerSelectionTimeoutError,
        ) as e:
            # If collection creation fails for other reasons, try to ensure it exists
            # by doing a no-op operation that will create it
            logger.debug(
                f"{log_prefix} Could not create collection explicitly: {e}. "
                f"Ensuring it exists via insert/delete."
            )
            try:
                # Insert and immediately delete a dummy doc to ensure collection exists
                dummy_id = await real_collection.insert_one({"_temp": True})
                await real_collection.delete_one({"_id": dummy_id.inserted_id})
            except (
                OperationFailure,
                ConnectionFailure,
                ServerSelectionTimeoutError,
            ) as ensure_error:
                logger.warning(
                    f"{log_prefix} Could not ensure collection exists: "
                    f"{ensure_error}"
                )

        index_manager = AsyncAtlasIndexManager(real_collection)
        logger.info(
            f"{log_prefix} Initialized IndexManager for collection "
            f"'{collection_name}'. Checking {len(index_definitions)} index defs."
        )

        # Log current indexes for debugging
        current_indexes = await index_manager.list_indexes()
        logger.debug(
            f"{log_prefix} Current indexes in collection: "
            f"{[idx.get('name') for idx in current_indexes]}"
        )
    except (
        OperationFailure,
        ConnectionFailure,
        ServerSelectionTimeoutError,
        AttributeError,
        TypeError,
        ValueError,
    ) as e:
        logger.error(
            f"{log_prefix} Failed to initialize IndexManager for collection "
            f"'{collection_name}': {e}. "
            f"This prevents all index operations for this collection. "
            f"Check MongoDB connection and collection permissions.",
            exc_info=True,
        )
        return

    for index_def in index_definitions:
        index_name = index_def.get("name")
        index_type = index_def.get("type")
        try:
            if index_type == INDEX_TYPE_REGULAR:
                await _handle_regular_index(
                    index_manager, index_def, index_name, log_prefix
                )
                # Wait for index to be ready after creation
                import asyncio

                await asyncio.sleep(0.5)  # Give MongoDB time to make index visible
            elif index_type == INDEX_TYPE_TTL:
                await _handle_ttl_index(
                    index_manager, index_def, index_name, log_prefix
                )
            elif index_type == "partial":
                await _handle_partial_index(
                    index_manager, index_def, index_name, log_prefix
                )
            elif index_type == "text":
                await _handle_text_index(
                    index_manager, index_def, index_name, log_prefix
                )
            elif index_type == "geospatial":
                await _handle_geospatial_index(
                    index_manager, index_def, index_name, log_prefix
                )
            elif index_type in ("vectorSearch", "search"):
                await _handle_search_index(
                    index_manager, index_def, index_name, index_type, slug, log_prefix
                )
            elif index_type == "hybrid":
                await _handle_hybrid_index(
                    index_manager, index_def, index_name, slug, log_prefix
                )
            else:
                from ..constants import SUPPORTED_INDEX_TYPES

                supported_types_str = ", ".join(f"'{t}'" for t in SUPPORTED_INDEX_TYPES)
                logger.warning(
                    f"{log_prefix} Unknown index type '{index_type}' "
                    f"for index '{index_name}'. "
                    f"Supported types: {supported_types_str}. "
                    f"Skipping this index definition. "
                    f"Update manifest.json with a supported index type."
                )
        except (
            OperationFailure,
            ConnectionFailure,
            ServerSelectionTimeoutError,
            ValueError,
            TypeError,
            KeyError,
            RuntimeError,
        ) as e:
            logger.error(
                f"{log_prefix} Error managing index '{index_name}' "
                f"(type: {index_type}): {e}. "
                f"Collection: {collection_name}. "
                f"This index will be skipped, but other indexes may still be "
                f"created.",
                exc_info=True,
            )
            # Re-raise to surface the error in tests
            raise
