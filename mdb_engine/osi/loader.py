"""Expanded OSI model loader supporting inline models, external YAML, and merging.

This module extends the lightweight ``graph.osi_loader`` with support for:
- Inline semantic models defined in manifest.json ``osi_config.semantic_models``
- External YAML files from ``osi_config.models_path`` or specific ``osi_config.models``
- Merging inline and external models into a single registry
- Auto-discovery of ``semantic_models/`` directories alongside manifest files
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def load_all_models(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Load all OSI models from the configuration.

    Loads from both external files and inline definitions, merging them.
    External files are loaded first, then inline models are appended.

    Args:
        config: The ``osi_config`` dict from the manifest.

    Returns:
        List of parsed semantic model dicts (deduplicated by name).
    """
    models: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    # 1. Load from models_path (directory)
    models_path = config.get("models_path")
    if models_path:
        from ..graph.osi_loader import load_osi_models

        for model in load_osi_models(models_path):
            name = model.get("name", "")
            if name not in seen_names:
                models.append(model)
                seen_names.add(name)

    # 2. Load specific model files
    model_files = config.get("models", [])
    if model_files:
        from ..graph.osi_loader import load_osi_models

        for file_path in model_files:
            for model in load_osi_models(file_path):
                name = model.get("name", "")
                if name not in seen_names:
                    models.append(model)
                    seen_names.add(name)

    # 3. Load inline models from manifest
    inline_models = config.get("semantic_models", [])
    for model in inline_models:
        if isinstance(model, dict):
            name = model.get("name", "")
            if name not in seen_names:
                models.append(model)
                seen_names.add(name)

    if models:
        logger.info(f"OSI loader: loaded {len(models)} total model(s)")
    return models


def auto_discover_models(manifest_path: str | Path | None) -> str | None:
    """Auto-discover OSI models using convention over configuration.

    Checks for a ``semantic_models/`` directory alongside the manifest file.

    Args:
        manifest_path: Path to the manifest.json file (or None).

    Returns:
        Path to the semantic_models directory if found, else None.
    """
    if not manifest_path:
        return None

    manifest_dir = Path(manifest_path).parent
    convention_path = manifest_dir / "semantic_models"
    if convention_path.is_dir():
        yaml_files = list(convention_path.glob("*.yaml")) + list(convention_path.glob("*.yml"))
        if yaml_files:
            logger.info(f"Auto-discovered OSI models at {convention_path} ({len(yaml_files)} files)")
            return str(convention_path)

    return None
