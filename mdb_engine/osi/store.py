"""MongoDB-backed OSI model storage with hash-based seeding.

The ``OsiModelStore`` persists semantic models in a scoped MongoDB collection
(``{slug}_osi_models``). On startup it seeds from YAML/inline config using a
SHA-256 hash check -- if the config hasn't changed, the collection (which may
contain API-added models) is used as-is.

This enables real-time updates via API without restarts, while YAML remains
the version-controlled baseline.

Collection schema::

    # Meta document (tracks config hash)
    {"_id": "_meta", "config_hash": "sha256:...", "seeded_at": "...", "app_slug": "..."}

    # Model documents (one per semantic model, full OSI structure)
    {"_id": "model:<name>", "doc_type": "model", "name": "...", "origin": "yaml_seed|api_import|discovered",
     "status": "approved|provisional", "datasets": [...], "metrics": [...], "relationships": [...], ...}
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class OsiModelStore:
    """MongoDB-backed storage for OSI semantic models."""

    META_DOC_ID = "_meta"

    def __init__(self, collection: Any, app_slug: str):
        """
        Args:
            collection: Motor AsyncIOMotorCollection (scoped or raw).
            app_slug: Application slug for logging.
        """
        self._collection = collection
        self._app_slug = app_slug

    # ------------------------------------------------------------------
    # Seeding
    # ------------------------------------------------------------------

    async def seed_from_config(self, config: dict[str, Any]) -> bool:
        """Seed the collection from config if the config hash has changed.

        Computes SHA-256 of the serialized ``semantic_models`` + ``models_path``
        + ``models`` fields. If the hash matches what's stored in the ``_meta``
        document, seeding is skipped (the collection may have API-added models
        that should be preserved).

        Args:
            config: The ``osi_config`` dict from the manifest.

        Returns:
            True if models were seeded (config changed), False if skipped (unchanged).
        """
        # Compute hash of config sources
        config_hash = self._compute_config_hash(config)

        # Check stored hash
        meta = await self._collection.find_one({"_id": self.META_DOC_ID})
        if meta and meta.get("config_hash") == config_hash:
            logger.info(f"OSI store '{self._app_slug}': config unchanged (hash match), " f"loading from collection")
            return False

        # Hash mismatch or first run -- seed from config
        logger.info(f"OSI store '{self._app_slug}': seeding from config (hash changed or first run)")

        from .loader import load_all_models

        models = load_all_models(config)

        now = datetime.now(timezone.utc)
        for model in models:
            doc_id = f"model:{model.get('name', 'unknown')}"
            await self._collection.update_one(
                {"_id": doc_id},
                {
                    "$set": {
                        "doc_type": "model",
                        "name": model.get("name", ""),
                        "description": model.get("description", ""),
                        "ai_context": model.get("ai_context", {}),
                        "datasets": model.get("datasets", []),
                        "metrics": model.get("metrics", []),
                        "relationships": model.get("relationships", []),
                        "custom_extensions": model.get("custom_extensions", []),
                        "origin": "yaml_seed",
                        "status": "approved",
                        "updated_at": now,
                        "app_slug": self._app_slug,
                    },
                    "$setOnInsert": {
                        "created_at": now,
                    },
                },
                upsert=True,
            )

        # Update meta
        await self._collection.update_one(
            {"_id": self.META_DOC_ID},
            {
                "$set": {
                    "config_hash": config_hash,
                    "seeded_at": now,
                    "app_slug": self._app_slug,
                    "model_count": len(models),
                },
            },
            upsert=True,
        )

        logger.info(f"OSI store '{self._app_slug}': seeded {len(models)} model(s)")
        return True

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def load_all(self) -> list[dict[str, Any]]:
        """Load all semantic models from the collection.

        Returns:
            List of model dicts (same structure as YAML-loaded models).
        """
        cursor = self._collection.find({"doc_type": "model"})
        docs = await cursor.to_list(length=1000)

        models = []
        for doc in docs:
            # Strip MongoDB internal fields, return clean OSI model
            model: dict[str, Any] = {
                "name": doc.get("name", ""),
                "description": doc.get("description", ""),
                "ai_context": doc.get("ai_context", {}),
                "datasets": doc.get("datasets", []),
                "metrics": doc.get("metrics", []),
                "relationships": doc.get("relationships", []),
            }
            if doc.get("custom_extensions"):
                model["custom_extensions"] = doc["custom_extensions"]
            # Preserve metadata for API consumers
            model["_status"] = doc.get("status", "approved")
            model["_origin"] = doc.get("origin", "unknown")
            models.append(model)

        return models

    # ------------------------------------------------------------------
    # Write (for API mutations)
    # ------------------------------------------------------------------

    async def upsert_model(
        self,
        model: dict[str, Any],
        origin: str = "api_import",
        status: str = "approved",
    ) -> None:
        """Add or update a semantic model in the collection.

        Args:
            model: Full OSI semantic model dict.
            origin: Where it came from ("yaml_seed", "api_import", "discovered").
            status: Model status ("approved", "provisional").
        """
        name = model.get("name", "unknown")
        now = datetime.now(timezone.utc)

        await self._collection.update_one(
            {"_id": f"model:{name}"},
            {
                "$set": {
                    "doc_type": "model",
                    "name": name,
                    "description": model.get("description", ""),
                    "ai_context": model.get("ai_context", {}),
                    "datasets": model.get("datasets", []),
                    "metrics": model.get("metrics", []),
                    "relationships": model.get("relationships", []),
                    "custom_extensions": model.get("custom_extensions", []),
                    "origin": origin,
                    "status": status,
                    "updated_at": now,
                    "app_slug": self._app_slug,
                },
                "$setOnInsert": {
                    "created_at": now,
                },
            },
            upsert=True,
        )
        logger.info(f"OSI store '{self._app_slug}': upserted model '{name}' (origin={origin})")

    async def remove_model(self, name: str) -> bool:
        """Remove a model by name.

        Returns:
            True if deleted, False if not found.
        """
        result = await self._collection.delete_one({"_id": f"model:{name}"})
        deleted = result.deleted_count > 0
        if deleted:
            logger.info(f"OSI store '{self._app_slug}': removed model '{name}'")
        return deleted

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_config_hash(config: dict[str, Any]) -> str:
        """Compute SHA-256 hash of the config sources that affect seeding."""
        # Hash the parts of the config that determine what gets seeded
        hashable = {
            "models_path": config.get("models_path"),
            "models": config.get("models", []),
            "semantic_models": config.get("semantic_models", []),
        }
        serialized = json.dumps(hashable, sort_keys=True, default=str)
        digest = hashlib.sha256(serialized.encode()).hexdigest()
        return f"sha256:{digest}"
