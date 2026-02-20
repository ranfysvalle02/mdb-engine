"""Watch for changes to OSI model files and sync to MongoDB.

Uses file modification timestamps with configurable polling interval.
When files change, re-seeds the MongoDB store and reloads the in-memory
registry so YAML and database stay synchronized.

Designed to be run as a background task alongside the FastAPI app.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .registry import OsiModelRegistry

logger = logging.getLogger(__name__)


class OsiModelWatcher:
    """Watch for changes to OSI model files and sync to MongoDB.

    Polls the models directory for file modification timestamp changes.
    When a change is detected, re-seeds the MongoDB store (if available)
    and reloads the in-memory registry.
    """

    def __init__(
        self,
        registry: OsiModelRegistry,
        interval_minutes: int = 60,
    ):
        self._registry = registry
        self._interval = interval_minutes * 60  # Convert to seconds
        self._file_timestamps: dict[str, float] = {}
        self._running = False

    def _scan_timestamps(self) -> dict[str, float]:
        """Scan all model file timestamps."""
        timestamps: dict[str, float] = {}
        config = self._registry._config  # noqa: SLF001

        models_path = config.get("models_path")
        if models_path:
            path = Path(models_path)
            if path.is_dir():
                for f in sorted(path.glob("*.yaml")) + sorted(path.glob("*.yml")):
                    try:
                        timestamps[str(f)] = f.stat().st_mtime
                    except OSError:
                        pass

        model_files = config.get("models", [])
        for file_path in model_files:
            p = Path(file_path)
            if p.is_file():
                try:
                    timestamps[str(p)] = p.stat().st_mtime
                except OSError:
                    pass

        return timestamps

    def check_for_updates(self) -> bool:
        """Check if any OSI model files have been modified.

        Returns:
            True if files changed and models should be reloaded.
        """
        current = self._scan_timestamps()

        if not self._file_timestamps:
            # First scan -- initialize baseline
            self._file_timestamps = current
            return False

        if current != self._file_timestamps:
            self._file_timestamps = current
            return True

        return False

    async def start_polling(self) -> None:
        """Start background polling loop.

        This is designed to be run as an asyncio.create_task() in the
        FastAPI app lifespan.
        """
        if self._interval <= 0:
            logger.info("OSI watcher disabled (sync_interval_minutes=0)")
            return

        self._running = True
        logger.info(f"OSI watcher started (polling every {self._interval}s)")

        # Initial scan
        self._file_timestamps = self._scan_timestamps()

        while self._running:
            await asyncio.sleep(self._interval)
            try:
                if self.check_for_updates():
                    logger.info("OSI model files changed, re-syncing to database...")

                    # Re-seed MongoDB store from YAML (force=True bypasses hash check
                    # since we already know files changed via mtime)
                    store = self._registry._store  # noqa: SLF001
                    if store:
                        config = self._registry._config  # noqa: SLF001
                        await store.seed_from_config(config, force=True)
                        logger.info("OSI store re-seeded from updated YAML files")

                    # Reload in-memory registry (from store if available, else from YAML)
                    await self._registry.reload()
                    logger.info("OSI models reloaded and synchronized successfully")
            except (OSError, ValueError, TypeError, RuntimeError) as e:
                logger.warning(f"OSI watcher sync failed: {e}")

    def stop(self) -> None:
        """Stop the polling loop."""
        self._running = False
        logger.info("OSI watcher stopped")
