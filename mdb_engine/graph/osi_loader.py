"""Load and format OSI semantic model files for prompt injection.

This lightweight module parses OSI (Open Semantic Interchange) YAML definitions
and formats them as context for the graph extraction prompt. It extracts dataset
names, fields, synonyms, relationships, and metrics into a concise prompt-friendly
format that helps the LLM use domain-specific entity types and vocabulary.

Usage::

    from mdb_engine.graph.osi_loader import load_osi_models, format_osi_for_prompt

    models = load_osi_models("semantic_models/")
    prompt_context = format_osi_for_prompt(models)
    node_types = extract_node_types_from_osi(models)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def load_osi_models(path: str | Path) -> list[dict[str, Any]]:
    """Load OSI semantic model YAML files from a path.

    Args:
        path: Path to a single YAML file or directory of YAML files.

    Returns:
        List of parsed semantic model dicts. Empty list if loading fails
        or no models are found.
    """
    try:
        import yaml  # noqa: F811
    except ImportError:
        logger.warning("PyYAML not installed; OSI model loading disabled. Install with: pip install pyyaml")
        return []

    path = Path(path)
    models: list[dict[str, Any]] = []

    if path.is_file():
        files = [path]
    elif path.is_dir():
        files = sorted(path.glob("*.yaml")) + sorted(path.glob("*.yml"))
    else:
        logger.warning(f"OSI models path not found: {path}")
        return []

    for file in files:
        try:
            with open(file) as f:
                data = yaml.safe_load(f)
            if not data:
                continue
            # OSI spec wraps models in a top-level "semantic_model" key (list)
            file_models: list[dict[str, Any]] = []
            if "semantic_model" in data:
                sm = data["semantic_model"]
                if isinstance(sm, list):
                    file_models.extend(sm)
                elif isinstance(sm, dict):
                    file_models.append(sm)
            else:
                # Treat the entire document as a single model
                file_models.append(data)
            # Tag each model with its source file for error reporting
            for m in file_models:
                if isinstance(m, dict):
                    m["_source_file"] = str(file)
            models.extend(file_models)
        except (OSError, yaml.YAMLError) as e:
            logger.warning(f"Failed to load OSI model {file}: {e}")

    if models:
        logger.info(f"Loaded {len(models)} OSI semantic model(s) from {path}")
    return models


def format_osi_for_prompt(models: list[dict[str, Any]]) -> str:
    """Format OSI models as prompt context for the extraction engine.

    Extracts dataset names, field names, synonyms, and relationships
    into a concise prompt-friendly format.

    Args:
        models: List of parsed OSI semantic model dicts.

    Returns:
        Formatted string for prompt injection. Empty string if no models.
    """
    if not models:
        return ""

    lines: list[str] = []

    for model in models:
        model_name = model.get("name", "unknown")
        model_desc = model.get("description", "")
        header = f"Semantic Model: {model_name}"
        if model_desc:
            header += f" -- {model_desc}"
        lines.append(header)

        # Datasets -> entity types with fields and synonyms
        for dataset in model.get("datasets", []):
            ds_name = dataset.get("name", "")
            if not ds_name:
                continue
            fields = [f.get("name", "") for f in dataset.get("fields", []) if f.get("name")]
            synonyms = _extract_synonyms(dataset.get("ai_context"))

            line = f'  Dataset "{ds_name}"'
            if fields:
                # Show up to 8 field names for brevity
                line += f': fields [{", ".join(fields[:8])}]'
                if len(fields) > 8:
                    line += f" (+{len(fields) - 8} more)"
            if synonyms:
                line += f". Synonyms: {synonyms}"
            lines.append(line)

            # Include field-level synonyms for richer recognition
            for field in dataset.get("fields", []):
                field_synonyms = _extract_synonyms(field.get("ai_context"))
                if field_synonyms:
                    lines.append(f'    Field "{field.get("name", "")}": synonyms {field_synonyms}')

        # Relationships -> valid edge types
        for rel in model.get("relationships", []):
            rel_name = rel.get("name", "")
            from_ds = rel.get("from", "")
            to_ds = rel.get("to", "")
            if from_ds and to_ds:
                lines.append(f"  Relationship: {from_ds} -> {to_ds} ({rel_name})")

        # Metrics -> recognizable terms
        for metric in model.get("metrics", []):
            m_name = metric.get("name", "")
            if not m_name:
                continue
            synonyms = _extract_synonyms(metric.get("ai_context"))
            m_desc = metric.get("description", "")
            line = f'  Metric "{m_name}"'
            if m_desc:
                line += f": {m_desc}"
            if synonyms:
                line += f". Synonyms: {synonyms}"
            lines.append(line)

        lines.append("")  # Blank line between models

    return "\n".join(lines).rstrip()


def extract_node_types_from_osi(models: list[dict[str, Any]]) -> list[str]:
    """Extract dataset names as additional node types for graph extraction.

    Args:
        models: List of parsed OSI semantic model dicts.

    Returns:
        List of dataset names suitable as node types (deduplicated, lowercase).
    """
    seen: set[str] = set()
    types: list[str] = []
    for model in models:
        for dataset in model.get("datasets", []):
            name = dataset.get("name", "").strip().lower()
            if name and name not in seen:
                seen.add(name)
                types.append(name)
    return types


def extract_metric_names(models: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Extract metric names and their synonyms for query matching.

    Args:
        models: List of parsed OSI semantic model dicts.

    Returns:
        Dict mapping metric name -> {synonyms: list, expression: str, model: str, description: str}
    """
    metrics: dict[str, dict[str, Any]] = {}
    for model in models:
        model_name = model.get("name", "unknown")
        for metric in model.get("metrics", []):
            m_name = metric.get("name", "")
            if not m_name:
                continue
            synonyms = _extract_synonyms(metric.get("ai_context")) or []
            expression = ""
            dialects = metric.get("expression", {})
            if isinstance(dialects, dict):
                dialect_list = dialects.get("dialects", [])
                if dialect_list and isinstance(dialect_list, list):
                    expression = dialect_list[0].get("expression", "")
            description = metric.get("description", "")
            metrics[m_name] = {
                "synonyms": synonyms,
                "expression": expression,
                "model": model_name,
                "description": description,
            }
    return metrics


def _extract_synonyms(ai_context: Any) -> list[str] | None:
    """Extract synonyms from an ai_context field.

    ai_context can be a dict with a "synonyms" key, a plain string (ignored),
    or None.
    """
    if not ai_context:
        return None
    if isinstance(ai_context, dict):
        syns = ai_context.get("synonyms")
        if isinstance(syns, list):
            return syns
    return None
