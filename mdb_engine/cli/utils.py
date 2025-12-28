"""
Utility functions for CLI commands.

This module provides shared utilities for CLI operations.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import json
from pathlib import Path
from typing import Any, Dict

import click


def load_manifest_file(file_path: Path) -> Dict[str, Any]:
    """
    Load a manifest JSON file.

    Args:
        file_path: Path to manifest.json file

    Returns:
        Manifest dictionary

    Raises:
        click.ClickException: If file doesn't exist or is invalid JSON
    """
    if not file_path.exists():
        raise click.ClickException(f"Manifest file not found: {file_path}")

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise click.ClickException(f"Invalid JSON in manifest file: {e}")


def save_manifest_file(file_path: Path, manifest: Dict[str, Any]) -> None:
    """
    Save a manifest dictionary to a JSON file.

    Args:
        file_path: Path to save manifest.json file
        manifest: Manifest dictionary

    Raises:
        click.ClickException: If file cannot be written
    """
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
    except (IOError, OSError) as e:
        raise click.ClickException(f"Failed to write manifest file: {e}")


def format_manifest_output(manifest: Dict[str, Any], format_type: str) -> str:
    """
    Format manifest for output.

    Args:
        manifest: Manifest dictionary
        format_type: Output format ('json', 'yaml', 'pretty')

    Returns:
        Formatted string representation
    """
    if format_type == "json":
        return json.dumps(manifest, indent=2, ensure_ascii=False)
    elif format_type == "yaml":
        try:
            import yaml

            return yaml.dump(manifest, default_flow_style=False, sort_keys=False)
        except ImportError:
            click.echo(
                "Warning: PyYAML not installed. Falling back to JSON format.",
                err=True,
            )
            return json.dumps(manifest, indent=2, ensure_ascii=False)
    elif format_type == "pretty":
        # Pretty print with key information
        lines = []
        lines.append(f"Schema Version: {manifest.get('schema_version', '2.0')}")
        lines.append(f"Slug: {manifest.get('slug', 'N/A')}")
        lines.append(f"Name: {manifest.get('name', 'N/A')}")
        lines.append(f"Status: {manifest.get('status', 'draft')}")
        if manifest.get("description"):
            lines.append(f"Description: {manifest.get('description')}")
        return "\n".join(lines)
    else:
        return json.dumps(manifest, indent=2, ensure_ascii=False)
