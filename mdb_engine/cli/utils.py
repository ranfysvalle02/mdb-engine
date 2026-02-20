"""Shared helpers for CLI commands."""

import json
from pathlib import Path


def load_manifest_file(path: Path) -> dict:
    """Read and parse a manifest JSON file."""
    with open(path) as f:
        return json.load(f)


def save_manifest_file(path: Path, data: dict) -> None:
    """Write a manifest dict back to JSON with consistent formatting."""
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
