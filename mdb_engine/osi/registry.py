"""Per-app OSI model registry with MongoDB-backed storage.

The ``OsiModelRegistry`` is the central service for managing OSI semantic
models within an MDB-Engine application. It provides:

- Indexed lookup of datasets, metrics, and relationships by name or synonym
- Prompt context generation for extraction enrichment
- Node type extraction for dynamic graph schema
- Metric synonym matching for query classification
- MongoDB-backed persistence via ``OsiModelStore`` (optional)

When backed by a store, models are seeded from YAML/config on first boot
(hash-checked), then live in MongoDB. API mutations propagate immediately
without restarts.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .store import OsiModelStore

logger = logging.getLogger(__name__)


class OsiModelRegistry:
    """Per-app registry for OSI semantic models.

    Loads, indexes, and serves OSI semantic model definitions.
    Provides lookup methods for entity resolution and metric detection.

    When ``store`` is provided, models are persisted in MongoDB and
    API mutations (add/remove) write through to the collection.
    When ``store`` is None, operates in-memory only (backward compatible).
    """

    def __init__(
        self,
        app_slug: str,
        config: dict[str, Any],
        store: OsiModelStore | None = None,
    ):
        self._app_slug = app_slug
        self._config = config
        self._store = store
        self._models: list[dict[str, Any]] = []
        self._datasets: dict[str, dict[str, Any]] = {}
        self._metrics: dict[str, dict[str, Any]] = {}
        self._relationships: list[dict[str, Any]] = []
        self._synonym_to_dataset: dict[str, str] = {}
        self._synonym_to_metric: dict[str, str] = {}
        self._prompt_context: str | None = None
        self._node_types: list[str] = []
        self._loaded = False

    @property
    def loaded(self) -> bool:
        """Whether models have been loaded."""
        return self._loaded

    @property
    def models(self) -> list[dict[str, Any]]:
        """The raw loaded models."""
        return self._models

    async def load(self) -> None:
        """Load and index all OSI models.

        If a store is configured: seed from config (hash-checked), then
        load from MongoDB. This means API-added models are preserved across
        restarts, and YAML changes trigger a re-seed.

        If no store: load from config directly (in-memory only).
        """
        if self._store:
            # Seed collection from config if hash changed
            await self._store.seed_from_config(self._config)
            # Load all models from collection (includes API-added ones)
            self._models = await self._store.load_all()
        else:
            # In-memory fallback (no MongoDB collection)
            from .loader import load_all_models

            self._models = load_all_models(self._config)

        self._index_models()
        self._build_prompt_context()
        self._loaded = True

        logger.info(
            f"OSI registry for '{self._app_slug}': "
            f"{len(self._models)} models, "
            f"{len(self._datasets)} datasets, "
            f"{len(self._metrics)} metrics, "
            f"{len(self._relationships)} relationships"
            f"{' (MongoDB-backed)' if self._store else ' (in-memory)'}"
        )

    async def reload(self) -> None:
        """Reload models from the backing store (or config if no store)."""
        if self._store:
            self._models = await self._store.load_all()
        else:
            from .loader import load_all_models

            self._models = load_all_models(self._config)

        self._index_models()
        self._build_prompt_context()
        logger.info(f"OSI registry reloaded for '{self._app_slug}'")

    def reload_sync(self) -> None:
        """Synchronously reload from config (for watcher use, no store)."""
        from .loader import load_all_models

        self._models = load_all_models(self._config)
        self._index_models()
        self._build_prompt_context()
        logger.info(f"OSI registry reloaded (sync) for '{self._app_slug}'")

    # ------------------------------------------------------------------
    # Write-through mutations (require store)
    # ------------------------------------------------------------------

    async def add_model(
        self,
        model: dict[str, Any],
        origin: str = "api_import",
        status: str = "approved",
    ) -> None:
        """Add or update a model. Writes to store and re-indexes.

        Args:
            model: Full OSI semantic model dict.
            origin: Source ("api_import", "discovered", "yaml_seed").
            status: Model status ("approved", "provisional").
        """
        if self._store:
            await self._store.upsert_model(model, origin=origin, status=status)
            # Reload from store to pick up changes
            self._models = await self._store.load_all()
        else:
            # In-memory: just append (deduplicate by name)
            name = model.get("name", "")
            self._models = [m for m in self._models if m.get("name") != name]
            model["_status"] = status
            model["_origin"] = origin
            self._models.append(model)

        self._index_models()
        self._build_prompt_context()

    async def remove_model(self, name: str) -> bool:
        """Remove a model by name. Writes to store and re-indexes.

        Returns:
            True if removed, False if not found.
        """
        if self._store:
            removed = await self._store.remove_model(name)
            if removed:
                self._models = await self._store.load_all()
                self._index_models()
                self._build_prompt_context()
            return removed
        else:
            before = len(self._models)
            self._models = [m for m in self._models if m.get("name") != name]
            if len(self._models) < before:
                self._index_models()
                self._build_prompt_context()
                return True
            return False

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def _index_models(self) -> None:
        """Build internal indexes from loaded models."""
        self._datasets.clear()
        self._metrics.clear()
        self._relationships.clear()
        self._synonym_to_dataset.clear()
        self._synonym_to_metric.clear()
        node_types_set: set[str] = set()

        for model in self._models:
            for dataset in model.get("datasets", []):
                ds_name = dataset.get("name", "")
                if not ds_name:
                    continue
                self._datasets[ds_name] = dataset
                node_types_set.add(ds_name.lower())

                synonyms = self._extract_synonyms(dataset.get("ai_context"))
                if synonyms:
                    for syn in synonyms:
                        self._synonym_to_dataset[syn.lower()] = ds_name

                for field in dataset.get("fields", []):
                    field_syns = self._extract_synonyms(field.get("ai_context"))
                    if field_syns:
                        for syn in field_syns:
                            self._synonym_to_dataset[syn.lower()] = ds_name

            for metric in model.get("metrics", []):
                m_name = metric.get("name", "")
                if not m_name:
                    continue
                metric_info = dict(metric)
                metric_info["_model_name"] = model.get("name", "unknown")
                self._metrics[m_name] = metric_info

                synonyms = self._extract_synonyms(metric.get("ai_context"))
                if synonyms:
                    for syn in synonyms:
                        self._synonym_to_metric[syn.lower()] = m_name
                self._synonym_to_metric[m_name.lower().replace("_", " ")] = m_name

            for rel in model.get("relationships", []):
                self._relationships.append(rel)

        self._node_types = sorted(node_types_set)

    def _build_prompt_context(self) -> None:
        """Build cached prompt context string."""
        from ..graph.osi_loader import format_osi_for_prompt

        self._prompt_context = format_osi_for_prompt(self._models) or None

    # ------------------------------------------------------------------
    # Read API (unchanged from before)
    # ------------------------------------------------------------------

    def get_prompt_context(self) -> str | None:
        """Get formatted prompt context for extraction enrichment."""
        return self._prompt_context

    def get_node_types(self) -> list[str]:
        """Get dataset names as additional node types."""
        return list(self._node_types)

    def get_dataset(self, name: str) -> dict[str, Any] | None:
        """Lookup a dataset definition by name or synonym (case-insensitive)."""
        if name in self._datasets:
            return self._datasets[name]
        canonical = self._synonym_to_dataset.get(name.lower())
        if canonical:
            return self._datasets.get(canonical)
        return None

    def get_metric(self, name: str) -> dict[str, Any] | None:
        """Lookup a metric definition by name or synonym (case-insensitive)."""
        if name in self._metrics:
            return self._metrics[name]
        canonical = self._synonym_to_metric.get(name.lower())
        if canonical:
            return self._metrics.get(canonical)
        return None

    def match_entity(self, extracted_name: str, extracted_type: str) -> dict[str, Any] | None:
        """Check if an extracted entity matches an OSI dataset."""
        name_lower = extracted_name.lower()
        type_lower = extracted_type.lower()

        if type_lower in self._datasets:
            return self._datasets[type_lower]

        canonical = self._synonym_to_dataset.get(type_lower)
        if canonical:
            return self._datasets.get(canonical)

        canonical = self._synonym_to_dataset.get(name_lower)
        if canonical:
            return self._datasets.get(canonical)

        for syn, ds_name in self._synonym_to_dataset.items():
            if type_lower in syn or syn in type_lower:
                return self._datasets.get(ds_name)

        return None

    def get_metric_synonyms(self) -> dict[str, list[str]]:
        """Get all metric names mapped to their synonyms."""
        result: dict[str, list[str]] = {}
        for m_name, m_info in self._metrics.items():
            synonyms = self._extract_synonyms(m_info.get("ai_context")) or []
            result[m_name] = synonyms
        return result

    def get_all_metric_keywords(self) -> set[str]:
        """Get all metric-related keywords for query matching."""
        keywords: set[str] = set()
        for m_name, m_info in self._metrics.items():
            keywords.add(m_name.lower().replace("_", " "))
            synonyms = self._extract_synonyms(m_info.get("ai_context")) or []
            for syn in synonyms:
                keywords.add(syn.lower())
        return keywords

    def list_datasets(self) -> list[dict[str, Any]]:
        """List all loaded dataset definitions."""
        return list(self._datasets.values())

    def list_metrics(self) -> list[dict[str, Any]]:
        """List all loaded metric definitions."""
        return list(self._metrics.values())

    def list_relationships(self) -> list[dict[str, Any]]:
        """List all loaded relationship definitions."""
        return list(self._relationships)

    @staticmethod
    def _extract_synonyms(ai_context: Any) -> list[str] | None:
        """Extract synonyms from an ai_context field."""
        if not ai_context:
            return None
        if isinstance(ai_context, dict):
            syns = ai_context.get("synonyms")
            if isinstance(syns, list):
                return syns
        return None
