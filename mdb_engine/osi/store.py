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

    async def seed_from_config(self, config: dict[str, Any], force: bool = False) -> bool:
        """Seed the collection from YAML files if content has changed.

        Computes SHA-256 of the actual YAML file contents. If the hash
        matches what's stored in the ``_meta`` document, seeding is skipped.

        Args:
            config: The ``osi_config`` dict from the manifest.
            force: If True, skip the hash check and always re-seed.
                Used by the file watcher when it already knows files changed.

        Returns:
            True if models were seeded (content changed), False if skipped (unchanged).
        """
        config_hash = self._compute_config_hash(config)

        if not force:
            meta = await self._collection.find_one({"_id": self.META_DOC_ID})
            if meta and meta.get("config_hash") == config_hash:
                logger.info(
                    f"OSI store '{self._app_slug}': YAML content unchanged (hash match), " f"loading from collection"
                )
                return False

        logger.info(
            f"OSI store '{self._app_slug}': "
            f"{'force re-seeding' if force else 'seeding'} from YAML "
            f"({'file watcher triggered' if force else 'content changed or first run'})"
        )

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
        """Compute SHA-256 hash of the actual YAML file contents.

        Reads and hashes the bytes of every YAML file referenced by
        ``models_path`` (directory) and ``models`` (file list). This
        ensures that editing any YAML file changes the hash, triggering
        re-seeding to MongoDB on next startup.
        """
        from pathlib import Path

        h = hashlib.sha256()

        # Hash contents of all YAML files in models_path directory
        models_path = config.get("models_path")
        if models_path:
            h.update(models_path.encode())
            p = Path(models_path)
            if p.is_dir():
                for f in sorted(p.glob("*.yaml")) + sorted(p.glob("*.yml")):
                    try:
                        h.update(f.read_bytes())
                    except OSError:
                        pass

        # Hash contents of specific model files
        for file_path in config.get("models", []):
            h.update(str(file_path).encode())
            fp = Path(file_path)
            if fp.is_file():
                try:
                    h.update(fp.read_bytes())
                except OSError:
                    pass

        return f"sha256:{h.hexdigest()}"
