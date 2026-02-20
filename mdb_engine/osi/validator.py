"""OSI semantic model schema validation.

Validates OSI YAML structure at load time with clear, actionable error
messages. Uses plain Python (no Pydantic dependency) to keep it lightweight.

Usage::

    from mdb_engine.osi.validator import validate_osi_model, validate_osi_yaml

    # Validate a parsed model dict
    result = validate_osi_model(model_dict)
    if not result.valid:
        for err in result.errors:
            print(f"[{err.path}] {err.message}")

    # Validate raw YAML string
    result = validate_osi_yaml(yaml_string)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class OsiValidationIssue:
    """A single validation error or warning."""

    path: str
    message: str
    suggestion: str = ""

    def to_dict(self) -> dict[str, str]:
        d: dict[str, str] = {"path": self.path, "message": self.message}
        if self.suggestion:
            d["suggestion"] = self.suggestion
        return d


@dataclass
class OsiValidationResult:
    """Result of validating one or more OSI models."""

    valid: bool = True
    errors: list[OsiValidationIssue] = field(default_factory=list)
    warnings: list[OsiValidationIssue] = field(default_factory=list)
    models_checked: int = 0

    def add_error(self, path: str, message: str, suggestion: str = "") -> None:
        self.errors.append(OsiValidationIssue(path=path, message=message, suggestion=suggestion))
        self.valid = False

    def add_warning(self, path: str, message: str, suggestion: str = "") -> None:
        self.warnings.append(OsiValidationIssue(path=path, message=message, suggestion=suggestion))

    def merge(self, other: OsiValidationResult) -> None:
        """Merge another result into this one."""
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
        self.models_checked += other.models_checked
        if not other.valid:
            self.valid = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "models_checked": self.models_checked,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "errors": [e.to_dict() for e in self.errors],
            "warnings": [w.to_dict() for w in self.warnings],
        }


def _check_string(value: Any, path: str, field_name: str, result: OsiValidationResult) -> bool:
    """Check that a value is a non-empty string. Returns True if valid."""
    if not isinstance(value, str) or not value.strip():
        result.add_error(path, f"'{field_name}' must be a non-empty string, got {type(value).__name__}")
        return False
    return True


def _check_synonyms(ai_context: Any, path: str, result: OsiValidationResult) -> None:
    """Validate ai_context.synonyms if present."""
    if ai_context is None:
        return
    if not isinstance(ai_context, dict):
        result.add_error(path, "ai_context must be a dict")
        return
    synonyms = ai_context.get("synonyms")
    if synonyms is None:
        return
    if not isinstance(synonyms, list):
        result.add_error(
            f"{path}.synonyms",
            f"synonyms must be a list of strings, got {type(synonyms).__name__}",
        )
        return
    for i, syn in enumerate(synonyms):
        if not isinstance(syn, str):
            result.add_error(
                f"{path}.synonyms[{i}]",
                f"Each synonym must be a string, got {type(syn).__name__}",
            )


def _validate_datasets(
    datasets: Any,
    prefix: str,
    result: OsiValidationResult,
) -> set[str]:
    """Validate the datasets array. Returns set of dataset names found."""
    dataset_names: set[str] = set()

    if datasets is None:
        return dataset_names

    if not isinstance(datasets, list):
        result.add_error(f"{prefix}.datasets", f"datasets must be a list, got {type(datasets).__name__}")
        return dataset_names

    for i, ds in enumerate(datasets):
        ds_path = f"{prefix}.datasets[{i}]"

        if not isinstance(ds, dict):
            result.add_error(ds_path, f"Each dataset must be a dict, got {type(ds).__name__}")
            continue

        ds_name = ds.get("name")
        if _check_string(ds_name, f"{ds_path}.name", "name", result):
            if ds_name in dataset_names:
                result.add_warning(f"{ds_path}.name", f"Duplicate dataset name '{ds_name}'")
            dataset_names.add(ds_name)

        # Validate fields
        fields = ds.get("fields")
        if fields is not None:
            if not isinstance(fields, list):
                result.add_error(f"{ds_path}.fields", "fields must be a list")
            else:
                for j, fld in enumerate(fields):
                    fld_path = f"{ds_path}.fields[{j}]"
                    if not isinstance(fld, dict):
                        result.add_error(fld_path, "Each field must be a dict")
                        continue
                    _check_string(fld.get("name"), f"{fld_path}.name", "name", result)
                    _check_synonyms(fld.get("ai_context"), f"{fld_path}.ai_context", result)

        # Validate ai_context
        ds_ai = ds.get("ai_context")
        _check_synonyms(ds_ai, f"{ds_path}.ai_context", result)

        # Warn if no synonyms
        if not ds_ai or not (isinstance(ds_ai, dict) and ds_ai.get("synonyms")):
            result.add_warning(
                f"{ds_path}.ai_context",
                f"Dataset '{ds_name or '?'}' has no synonyms -- entity resolution will be less effective",
                suggestion="Add ai_context.synonyms with common names for this entity type",
            )

    return dataset_names


def _validate_relationships(
    relationships: Any,
    prefix: str,
    dataset_names: set[str],
    result: OsiValidationResult,
) -> None:
    """Validate the relationships array."""
    if relationships is None:
        return

    if not isinstance(relationships, list):
        result.add_error(
            f"{prefix}.relationships",
            f"relationships must be a list, got {type(relationships).__name__}",
        )
        return

    for i, rel in enumerate(relationships):
        rel_path = f"{prefix}.relationships[{i}]"

        if not isinstance(rel, dict):
            result.add_error(rel_path, "Each relationship must be a dict")
            continue

        _check_string(rel.get("name"), f"{rel_path}.name", "name", result)

        for side in ("left_dataset", "right_dataset"):
            ref = rel.get(side)
            if ref is not None and isinstance(ref, str) and dataset_names and ref not in dataset_names:
                result.add_warning(
                    f"{rel_path}.{side}",
                    f"References dataset '{ref}' which is not defined in this model",
                    suggestion=f"Add a dataset named '{ref}' or fix the reference",
                )

        _check_synonyms(rel.get("ai_context"), f"{rel_path}.ai_context", result)


def _validate_metrics(
    metrics: Any,
    prefix: str,
    result: OsiValidationResult,
) -> None:
    """Validate the metrics array."""
    if metrics is None:
        return

    if not isinstance(metrics, list):
        result.add_error(f"{prefix}.metrics", f"metrics must be a list, got {type(metrics).__name__}")
        return

    for i, metric in enumerate(metrics):
        m_path = f"{prefix}.metrics[{i}]"

        if not isinstance(metric, dict):
            result.add_error(m_path, "Each metric must be a dict")
            continue

        _check_string(metric.get("name"), f"{m_path}.name", "name", result)
        _check_synonyms(metric.get("ai_context"), f"{m_path}.ai_context", result)


def validate_osi_model(model: dict[str, Any]) -> OsiValidationResult:
    """Validate a single OSI semantic model dict.

    Checks required fields, structure, and cross-references.
    Returns an OsiValidationResult with .valid, .errors, and .warnings.
    """
    result = OsiValidationResult(models_checked=1)

    if not isinstance(model, dict):
        result.add_error("", f"Model must be a dict, got {type(model).__name__}")
        return result

    # Required: name
    name = model.get("name")
    if not _check_string(name, "name", "name", result):
        name = "<unknown>"

    prefix = f"model[{name}]"

    # Optional: description
    desc = model.get("description")
    if desc is not None and not isinstance(desc, str):
        result.add_warning(f"{prefix}.description", "description should be a string")

    # Optional: ai_context
    _check_synonyms(model.get("ai_context"), f"{prefix}.ai_context", result)

    # Datasets, relationships, metrics
    dataset_names = _validate_datasets(model.get("datasets"), prefix, result)
    _validate_relationships(model.get("relationships"), prefix, dataset_names, result)
    _validate_metrics(model.get("metrics"), prefix, result)

    return result


def validate_osi_models(models: list[dict[str, Any]]) -> OsiValidationResult:
    """Validate a list of OSI models (batch validation)."""
    combined = OsiValidationResult()

    if not isinstance(models, list):
        combined.add_error("", f"Expected a list of models, got {type(models).__name__}")
        return combined

    for model in models:
        combined.merge(validate_osi_model(model))

    return combined


def validate_osi_yaml(yaml_content: str) -> OsiValidationResult:
    """Validate raw YAML string by parsing it and validating all models.

    Handles both ``semantic_model: [...]`` wrapper format and raw model dicts.
    """
    result = OsiValidationResult()

    if not yaml_content or not yaml_content.strip():
        result.add_error("", "YAML content is empty")
        return result

    try:
        import yaml as yaml_lib
    except ImportError:
        result.add_error("", "PyYAML is not installed -- cannot parse YAML")
        return result

    try:
        data = yaml_lib.safe_load(yaml_content)
    except yaml_lib.YAMLError as e:
        result.add_error("", f"YAML parse error: {e}")
        return result

    if not data:
        result.add_error("", "YAML parsed to empty/null content")
        return result

    if not isinstance(data, dict):
        result.add_error("", f"YAML root must be a dict, got {type(data).__name__}")
        return result

    # Extract models from semantic_model wrapper or treat as single model
    models: list[dict[str, Any]] = []
    if "semantic_model" in data:
        sm = data["semantic_model"]
        if isinstance(sm, list):
            models.extend(sm)
        elif isinstance(sm, dict):
            models.append(sm)
        else:
            result.add_error("semantic_model", f"Must be a list or dict, got {type(sm).__name__}")
            return result
    else:
        models.append(data)

    result.merge(validate_osi_models(models))
    return result
