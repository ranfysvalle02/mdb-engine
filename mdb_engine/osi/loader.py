"""OSI model loader -- YAML files only.

Loads OSI semantic models exclusively from YAML files:
- External YAML files from ``osi_config.models_path`` directory
- Specific YAML files from ``osi_config.models`` file list
- Auto-discovery of ``semantic_models/`` directories alongside manifest files

YAML is the single source of truth for OSI compliance. Inline model
definitions in manifest.json are not supported.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def load_all_models(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Load all OSI models from YAML files.

    Loads from ``models_path`` directory and/or specific ``models`` file paths.
    Each model is validated on load with clear error/warning messages.

    Args:
        config: The ``osi_config`` dict from the manifest.

    Returns:
        List of parsed semantic model dicts (deduplicated by name).
    """
    from .validator import validate_osi_model

    models: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    sources_tried: list[str] = []

    # 1. Load from models_path (directory)
    models_path = config.get("models_path")
    if models_path:
        p = Path(models_path)
        sources_tried.append(f"models_path={models_path}")
        if not p.exists():
            logger.warning(
                "OSI models_path '%s' does not exist. " "Check your manifest osi_config.models_path setting.",
                models_path,
            )
        elif p.is_dir():
            yaml_files = list(p.glob("*.yaml")) + list(p.glob("*.yml"))
            if not yaml_files:
                logger.warning(
                    "OSI models_path '%s' exists but contains no .yaml or .yml files.",
                    models_path,
                )
            else:
                from ..graph.osi_loader import load_osi_models

                for model in load_osi_models(models_path):
                    name = model.get("name", "")
                    if name not in seen_names:
                        models.append(model)
                        seen_names.add(name)
        else:
            # It's a file, not a directory -- load directly
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
            sources_tried.append(f"models[]={file_path}")
            fp = Path(file_path)
            if not fp.exists():
                logger.warning(
                    "OSI model file '%s' does not exist. " "Check your manifest osi_config.models list.",
                    file_path,
                )
                continue
            for model in load_osi_models(file_path):
                name = model.get("name", "")
                if name not in seen_names:
                    models.append(model)
                    seen_names.add(name)

    # 3. Validate each loaded model
    for model in models:
        model_name = model.get("name", "<unnamed>")
        source_file = model.get("_source_file", "")
        source_hint = f" (from {source_file})" if source_file else ""
        result = validate_osi_model(model)
        if not result.valid:
            logger.error(
                "OSI model '%s'%s has %d validation error(s):",
                model_name,
                source_hint,
                len(result.errors),
            )
            for err in result.errors:
                logger.error("  [%s] %s", err.path, err.message)
        for warn in result.warnings:
            logger.warning("  OSI '%s'%s [%s] %s", model_name, source_hint, warn.path, warn.message)

    if models:
        logger.info(f"OSI loader: loaded {len(models)} total model(s) from YAML")
    elif config.get("enabled") and sources_tried:
        logger.warning(
            "OSI is enabled but no models were loaded. Sources tried: %s. "
            "Verify that your YAML files exist and contain valid semantic_model definitions.",
            ", ".join(sources_tried),
        )
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
