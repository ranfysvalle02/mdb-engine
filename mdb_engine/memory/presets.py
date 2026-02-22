"""
Named memory configuration presets.

Presets provide sensible default configurations for common use-cases so
developers can write ``"memory_config": "smart"`` instead of 50 lines
of JSON.

Usage in manifest.json::

    "memory_config": "smart"

    "memory_config": {"preset": "full", "embedding_model": "text-embedding-3-large"}

    "memory_config": true   # equivalent to "basic"
"""

from __future__ import annotations

import copy
import logging
from typing import Any

logger = logging.getLogger(__name__)

MEMORY_PRESETS: dict[str, dict[str, Any]] = {
    # ------------------------------------------------------------------
    # basic — add/search with LLM extraction, no cognitive features
    # ------------------------------------------------------------------
    "basic": {
        "enabled": True,
        "infer": True,
        "enable_cognitive": False,
    },
    # ------------------------------------------------------------------
    # smart — cognitive scoring, categories, salience gate, memory types
    # ------------------------------------------------------------------
    "smart": {
        "enabled": True,
        "infer": True,
        "enable_cognitive": True,
        "salience_gate": True,
        "categories": {"enabled": True},
        "memory_types": {"enabled": True, "auto_detect": True},
    },
    # ------------------------------------------------------------------
    # full — everything including reflection, graph, and pruning
    # ------------------------------------------------------------------
    "full": {
        "enabled": True,
        "infer": True,
        "enable_cognitive": True,
        "salience_gate": True,
        "categories": {"enabled": True},
        "memory_types": {"enabled": True, "auto_detect": True},
        "reflection": {"enabled": True, "interval_hours": 24},
        "graph": {"enabled": True, "auto_extract": True},
        "cognitive": {
            "enabled": True,
            "emotion": {"enabled": True},
            "conflict_resolution": {"enabled": True},
            "pruning": {"enabled": True},
        },
    },
}


def resolve_memory_preset(name_or_config: str | dict[str, Any] | bool) -> dict[str, Any]:
    """Resolve a preset name, shorthand ``True``, or dict-with-preset to a
    full memory config dict.

    Args:
        name_or_config: One of:
            - ``True`` — expands to ``{"enabled": True}``
            - A preset name string (``"basic"``, ``"smart"``, ``"full"``)
            - A dict with an optional ``"preset"`` key for overrides

    Returns:
        A fully-expanded memory config dict (always contains ``"enabled": True``).
    """
    if name_or_config is True:
        return {"enabled": True}

    if isinstance(name_or_config, str):
        preset = MEMORY_PRESETS.get(name_or_config)
        if preset is None:
            logger.warning(
                f"Unknown memory preset '{name_or_config}'. "
                f"Valid presets: {list(MEMORY_PRESETS.keys())}. Falling back to 'basic'."
            )
            preset = MEMORY_PRESETS["basic"]
        return copy.deepcopy(preset)

    if isinstance(name_or_config, dict):
        if "preset" in name_or_config:
            overrides = name_or_config.copy()
            preset_name = overrides.pop("preset")
            base = resolve_memory_preset(preset_name)
            _deep_merge(base, overrides)
            return base
        return name_or_config

    return {"enabled": True}


def _deep_merge(base: dict[str, Any], overrides: dict[str, Any]) -> None:
    """Recursively merge *overrides* into *base* in place."""
    for key, value in overrides.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


# ------------------------------------------------------------------
# Known embedding model dimensions
# ------------------------------------------------------------------

KNOWN_EMBEDDING_DIMS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
    "text-embedding-ada-001": 1024,
    "embed-english-v3.0": 1024,
    "embed-multilingual-v3.0": 1024,
    "embed-english-light-v3.0": 384,
    "embed-multilingual-light-v3.0": 384,
    "voyage-3": 1024,
    "voyage-3-lite": 512,
    "voyage-code-3": 1024,
}


def resolve_embedding_dims(model_name: str | None, explicit_dims: int | None = None) -> int:
    """Return embedding dimensions, preferring an explicit value, then a known
    lookup, then the default of 1536.

    Args:
        model_name: Embedding model name (e.g. ``"text-embedding-3-small"``).
        explicit_dims: Value the user set in ``embedding_model_dims`` / ``embedding_dims``.

    Returns:
        Resolved dimension count.
    """
    if explicit_dims is not None:
        return explicit_dims

    if model_name:
        known = KNOWN_EMBEDDING_DIMS.get(model_name)
        if known is not None:
            logger.info(f"Auto-resolved embedding dimensions for '{model_name}': {known}")
            return known

    return 1536
