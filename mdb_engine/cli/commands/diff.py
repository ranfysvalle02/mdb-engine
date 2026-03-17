"""
Manifest diff command for CLI.

Compares two manifest files and reports structural changes, including
breaking changes that may affect existing data or frontends.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import click

from ..utils import load_manifest_file

_SYMBOL_ADD = click.style("+ ", fg="green")
_SYMBOL_REMOVE = click.style("- ", fg="red")
_SYMBOL_CHANGE = click.style("~ ", fg="yellow")
_SYMBOL_BREAK = click.style("⚠ BREAKING: ", fg="red", bold=True)


class ManifestDiff:
    """Compute and format the diff between two manifests."""

    def __init__(self, old: dict[str, Any], new: dict[str, Any]) -> None:
        self.old = old
        self.new = new
        self.lines: list[str] = []
        self.breaking: int = 0

    def compute(self) -> list[str]:
        self._diff_top_level()
        self._diff_collections()
        self._diff_auth()
        return self.lines

    def _add(self, symbol: str, message: str) -> None:
        self.lines.append(f"{symbol}{message}")

    def _diff_top_level(self) -> None:
        for key in ("slug", "name", "status", "schema_version"):
            old_val = self.old.get(key)
            new_val = self.new.get(key)
            if old_val != new_val:
                if old_val is None:
                    self._add(_SYMBOL_ADD, f"{key}: {new_val!r}")
                elif new_val is None:
                    self._add(_SYMBOL_REMOVE, f"{key}: was {old_val!r}")
                else:
                    self._add(_SYMBOL_CHANGE, f"{key}: {old_val!r} -> {new_val!r}")

    def _diff_collections(self) -> None:
        old_cols = self.old.get("collections", {})
        new_cols = self.new.get("collections", {})

        for name in sorted(set(old_cols) | set(new_cols)):
            if name not in old_cols:
                flags = []
                cfg = new_cols[name]
                if cfg.get("auto_crud", True):
                    flags.append("auto_crud")
                if cfg.get("auth", {}).get("public_read"):
                    flags.append("public_read")
                self._add(_SYMBOL_ADD, f'collection "{name}" added ({", ".join(flags) or "default"})')
            elif name not in new_cols:
                self._add(_SYMBOL_REMOVE, f'collection "{name}" removed')
                self.breaking += 1
                self._add(_SYMBOL_BREAK, f'collection "{name}" removed — endpoints will disappear')
            else:
                self._diff_collection(name, old_cols[name], new_cols[name])

    def _diff_collection(self, name: str, old: dict, new: dict) -> None:
        prefix = f"{name}."

        self._diff_schema(name, old.get("schema", {}), new.get("schema", {}))
        self._diff_dict_keys(prefix + "scopes", old.get("scopes", {}), new.get("scopes", {}))
        self._diff_dict_keys(prefix + "pipelines", old.get("pipelines", {}), new.get("pipelines", {}))
        self._diff_dict_keys(prefix + "hooks", old.get("hooks", {}), new.get("hooks", {}))

        for key in (
            "writable_fields",
            "write_roles",
            "policy",
            "defaults",
            "owner_field",
            "soft_delete",
            "read_only",
            "timestamps",
            "auth",
        ):
            old_val = old.get(key)
            new_val = new.get(key)
            if old_val != new_val:
                if old_val is None:
                    self._add(_SYMBOL_ADD, f"{prefix}{key}: {_compact(new_val)}")
                elif new_val is None:
                    self._add(_SYMBOL_REMOVE, f"{prefix}{key}: was {_compact(old_val)}")
                else:
                    self._add(_SYMBOL_CHANGE, f"{prefix}{key}: changed")

    def _diff_schema(self, col_name: str, old_schema: dict, new_schema: dict) -> None:
        if old_schema == new_schema:
            return

        prefix = f"{col_name}.schema"
        old_props = set((old_schema.get("properties") or {}).keys())
        new_props = set((new_schema.get("properties") or {}).keys())

        for p in sorted(new_props - old_props):
            self._add(_SYMBOL_ADD, f"{prefix}.properties.{p}")
        for p in sorted(old_props - new_props):
            self._add(_SYMBOL_REMOVE, f"{prefix}.properties.{p}")

        old_required = set(old_schema.get("required", []))
        new_required = set(new_schema.get("required", []))
        for r in sorted(new_required - old_required):
            self.breaking += 1
            self._add(
                _SYMBOL_BREAK,
                f'{prefix}.required now includes "{r}" (existing docs may fail validation)',
            )
        for r in sorted(old_required - new_required):
            self._add(_SYMBOL_REMOVE, f'{prefix}.required no longer includes "{r}"')

    def _diff_auth(self) -> None:
        old_auth = self.old.get("auth", {})
        new_auth = self.new.get("auth", {})
        if old_auth == new_auth:
            return
        for key in ("mode", "users", "policy"):
            old_val = old_auth.get(key)
            new_val = new_auth.get(key)
            if old_val != new_val:
                if old_val is None:
                    self._add(_SYMBOL_ADD, f"auth.{key}: {_compact(new_val)}")
                elif new_val is None:
                    self._add(_SYMBOL_REMOVE, f"auth.{key}: was {_compact(old_val)}")
                else:
                    self._add(_SYMBOL_CHANGE, f"auth.{key}: changed")

    def _diff_dict_keys(self, label: str, old: dict, new: dict) -> None:
        old_keys = set(old.keys())
        new_keys = set(new.keys())
        for k in sorted(new_keys - old_keys):
            self._add(_SYMBOL_ADD, f"{label}.{k}")
        for k in sorted(old_keys - new_keys):
            self._add(_SYMBOL_REMOVE, f"{label}.{k}")
        for k in sorted(old_keys & new_keys):
            if old[k] != new[k]:
                self._add(_SYMBOL_CHANGE, f"{label}.{k}: changed")


def _compact(val: Any) -> str:
    """Short repr for display."""
    s = repr(val)
    return s if len(s) < 80 else s[:77] + "..."


def compute_diff(old: dict, new: dict) -> ManifestDiff:
    """Compute diff between two manifests (public API for testing)."""
    differ = ManifestDiff(old, new)
    differ.compute()
    return differ


@click.command()
@click.argument("old_manifest", type=click.Path(exists=True, path_type=Path))
@click.argument("new_manifest", type=click.Path(exists=True, path_type=Path))
def diff(old_manifest: Path, new_manifest: Path) -> None:
    """Compare two manifest files and show what changed.

    OLD_MANIFEST: Path to the original manifest.json
    NEW_MANIFEST: Path to the new manifest.json

    Examples:
        mdb-engine diff manifest.v1.json manifest.v2.json
    """
    try:
        old = load_manifest_file(old_manifest)
        new = load_manifest_file(new_manifest)
    except (FileNotFoundError, ValueError) as e:
        raise click.ClickException(str(e)) from e

    differ = compute_diff(old, new)
    if not differ.lines:
        click.echo(click.style("No changes detected.", fg="green"))
        sys.exit(0)

    for line in differ.lines:
        click.echo(line)

    if differ.breaking:
        click.echo()
        click.echo(click.style(f"{differ.breaking} breaking change(s) detected.", fg="red", bold=True))
        sys.exit(1)
    sys.exit(0)
