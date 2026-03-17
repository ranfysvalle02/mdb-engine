"""
Manifest dry-run command for CLI.

Loads a manifest without connecting to MongoDB and prints all generated
routes, scopes, indexes, hooks, and auth policies.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import click

from ..utils import load_manifest_file


def _parse_time_string(s: str) -> int | None:
    """Parse time strings like '90d', '24h', '30m', '60s' to seconds."""
    if not s:
        return None
    unit = s[-1].lower()
    try:
        val = int(s[:-1])
    except (ValueError, IndexError):
        return None
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return val * multipliers.get(unit, 1)


class ManifestDryRun:
    """Analyze a manifest and produce a structured report."""

    def __init__(self, manifest: dict[str, Any]) -> None:
        self.manifest = manifest
        self.routes: list[dict[str, str]] = []
        self.scopes: dict[str, list[str]] = {}
        self.indexes: list[dict[str, str]] = []
        self.hooks: list[dict[str, str]] = []
        self.auth_summary: dict[str, Any] = {}

    def analyze(self) -> None:
        self._analyze_auth()
        collections = self.manifest.get("collections", {})
        for name, config in collections.items():
            self._analyze_collection(name, config)
        self._analyze_managed_indexes()

    def _analyze_auth(self) -> None:
        auth = self.manifest.get("auth", {})
        self.auth_summary = {
            "mode": auth.get("mode", "none"),
            "users_enabled": auth.get("users", {}).get("enabled", False),
            "registration": auth.get("users", {}).get("allow_registration", False),
            "policy_provider": auth.get("policy", {}).get("provider", "none"),
        }

    def _analyze_collection(self, name: str, config: dict[str, Any]) -> None:
        if not config.get("auto_crud", True):
            return

        auth_cfg = config.get("auth", {})
        auth_label = "public"
        if auth_cfg.get("roles"):
            auth_label = f"roles:{','.join(auth_cfg['roles'])}"
        elif auth_cfg.get("required") or self.auth_summary.get("users_enabled"):
            auth_label = "authenticated"

        prefix = f"/api/{name}"
        read_only = config.get("read_only", False)

        self.routes.append({"method": "GET", "path": prefix, "auth": auth_label})
        self.routes.append({"method": "GET", "path": f"{prefix}/_count", "auth": auth_label})
        self.routes.append({"method": "GET", "path": f"{prefix}/{{id}}", "auth": auth_label})

        if config.get("pipelines"):
            for pipe_name in config["pipelines"]:
                self.routes.append(
                    {
                        "method": "GET",
                        "path": f"{prefix}/_agg/{pipe_name}",
                        "auth": auth_label,
                    }
                )

        if not read_only:
            write_auth = auth_label
            if auth_cfg.get("write_roles"):
                write_auth = f"roles:{','.join(auth_cfg['write_roles'])}"
            self.routes.append({"method": "POST", "path": prefix, "auth": write_auth})
            if config.get("bulk_insert", True):
                self.routes.append({"method": "POST", "path": f"{prefix}/_bulk", "auth": write_auth})
            self.routes.append({"method": "PUT", "path": f"{prefix}/{{id}}", "auth": write_auth})
            self.routes.append({"method": "PATCH", "path": f"{prefix}/{{id}}", "auth": write_auth})
            self.routes.append({"method": "DELETE", "path": f"{prefix}/{{id}}", "auth": write_auth})

            if config.get("soft_delete"):
                self.routes.append({"method": "GET", "path": f"{prefix}/_trash", "auth": auth_label})
                self.routes.append(
                    {
                        "method": "POST",
                        "path": f"{prefix}/{{id}}/_restore",
                        "auth": write_auth,
                    }
                )

        scope_cfg = config.get("scopes", {})
        if scope_cfg:
            self.scopes[name] = list(scope_cfg.keys())

        schema = config.get("schema", {})
        if schema.get("properties"):
            for prop_name, prop_def in schema["properties"].items():
                if isinstance(prop_def, dict) and prop_def.get("x-unique"):
                    self.indexes.append(
                        {
                            "collection": name,
                            "name": f"auto_unique_{prop_name}",
                            "type": "unique",
                            "keys": prop_name,
                        }
                    )

        ttl_cfg = config.get("ttl")
        if ttl_cfg:
            seconds = _parse_time_string(ttl_cfg.get("expire_after", ""))
            self.indexes.append(
                {
                    "collection": name,
                    "name": f"auto_ttl_{ttl_cfg['field']}",
                    "type": "ttl",
                    "keys": ttl_cfg["field"],
                    "expire_after": f"{seconds}s" if seconds else ttl_cfg.get("expire_after", "?"),
                }
            )

        hooks_cfg = config.get("hooks", {})
        for event, actions in hooks_cfg.items():
            if isinstance(actions, list):
                for action_def in actions:
                    entry: dict[str, str] = {
                        "collection": name,
                        "event": event,
                        "action": action_def.get("action", "?"),
                        "target": action_def.get("collection", action_def.get("url", "?")),
                    }
                    if action_def.get("if"):
                        entry["condition"] = "conditional"
                    self.hooks.append(entry)

    def _analyze_managed_indexes(self) -> None:
        for col_name, idx_list in self.manifest.get("managed_indexes", {}).items():
            if not isinstance(idx_list, list):
                continue
            for idx_def in idx_list:
                self.indexes.append(
                    {
                        "collection": col_name,
                        "name": idx_def.get("name", "unnamed"),
                        "type": idx_def.get("type", "regular"),
                        "keys": str(idx_def.get("keys", {})),
                    }
                )


def analyze_manifest(manifest: dict) -> ManifestDryRun:
    """Public API for testing: analyze a manifest and return the report."""
    runner = ManifestDryRun(manifest)
    runner.analyze()
    return runner


@click.command("dry-run")
@click.argument("manifest_file", type=click.Path(exists=True, path_type=Path))
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def dry_run(manifest_file: Path, as_json: bool) -> None:
    """Print all routes, scopes, indexes, and hooks a manifest would generate.

    MANIFEST_FILE: Path to manifest.json

    Examples:
        mdb-engine dry-run manifest.json
        mdb-engine dry-run manifest.json --json
    """
    try:
        manifest = load_manifest_file(manifest_file)
    except (FileNotFoundError, ValueError) as e:
        raise click.ClickException(str(e)) from e

    report = analyze_manifest(manifest)

    if as_json:
        import json

        click.echo(
            json.dumps(
                {
                    "auth": report.auth_summary,
                    "routes": report.routes,
                    "scopes": report.scopes,
                    "indexes": report.indexes,
                    "hooks": report.hooks,
                },
                indent=2,
            )
        )
        sys.exit(0)

    click.echo(click.style("=== Auth ===", bold=True))
    for k, v in report.auth_summary.items():
        click.echo(f"  {k}: {v}")

    click.echo()
    click.echo(click.style(f"=== Routes ({len(report.routes)}) ===", bold=True))
    for r in report.routes:
        method = click.style(f"{r['method']:6s}", fg="cyan")
        auth = click.style(f"[{r['auth']}]", fg="yellow")
        click.echo(f"  {method} {r['path']}  {auth}")

    if report.scopes:
        click.echo()
        click.echo(click.style("=== Scopes ===", bold=True))
        for col, scope_names in report.scopes.items():
            click.echo(f"  {col}: {', '.join(scope_names)}")

    if report.indexes:
        click.echo()
        click.echo(click.style(f"=== Indexes ({len(report.indexes)}) ===", bold=True))
        for idx in report.indexes:
            idx_type = click.style(f"[{idx['type']}]", fg="magenta")
            click.echo(f"  {idx['collection']}.{idx['name']} {idx_type} keys={idx['keys']}")

    if report.hooks:
        click.echo()
        click.echo(click.style(f"=== Hooks ({len(report.hooks)}) ===", bold=True))
        for h in report.hooks:
            cond = " (conditional)" if h.get("condition") else ""
            click.echo(f"  {h['collection']}.{h['event']} -> {h['action']} {h['target']}{cond}")

    sys.exit(0)
