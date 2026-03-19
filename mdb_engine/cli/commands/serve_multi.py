"""
Serve-multi command for CLI.

Starts a uvicorn multi-app server from an apps directory or a multi-app
manifest — zero application code required.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import click

try:
    import uvicorn as _uvicorn
except ImportError:  # pragma: no cover
    _uvicorn = None  # type: ignore[assignment]


def _discover_apps(apps_dir: Path) -> list[dict[str, str]]:
    """Scan *apps_dir* for subdirectories containing ``manifest.json``."""
    apps: list[dict[str, str]] = []
    for child in sorted(apps_dir.iterdir()):
        manifest = child / "manifest.json"
        if child.is_dir() and manifest.exists():
            try:
                with open(manifest) as f:
                    data = json.load(f)
                slug = data.get("slug", child.name)
                apps.append({"slug": slug, "dir": child.name})
            except (json.JSONDecodeError, OSError):
                continue
    return apps


def _read_multi_manifest(manifest_path: Path) -> list[dict[str, str]]:
    """Read app entries from a multi-app manifest file."""
    with open(manifest_path) as f:
        data = json.load(f)
    multi = data.get("multi_app", {})
    entries = multi.get("apps", [])
    return [
        {
            "slug": entry.get("slug", "?"),
            "prefix": entry.get("path_prefix", f"/{entry.get('slug', '?')}"),
        }
        for entry in entries
    ]


@click.command()
@click.option(
    "--apps-dir",
    "-d",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Directory containing app subdirectories, each with a manifest.json",
)
@click.option(
    "--manifest",
    "-m",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to a multi_app_manifest.json file",
)
@click.option("--host", default="0.0.0.0", show_default=True, help="Bind host")
@click.option("--port", "-p", default=8000, show_default=True, type=int, help="Bind port")
@click.option("--reload", is_flag=True, help="Enable auto-reload (development)")
@click.option("--mongo-uri", envvar="MONGODB_URI", default=None, help="MongoDB connection URI")
@click.option("--db-name", envvar="MDB_DB_NAME", default=None, help="Database name")
@click.option("--title", default="Multi-App API", show_default=True, help="Parent app title")
def serve_multi(
    apps_dir: Path | None,
    manifest: Path | None,
    host: str,
    port: int,
    reload: bool,
    mongo_uri: str | None,
    db_name: str | None,
    title: str,
) -> None:
    """Start a multi-app server from an apps directory or manifest.

    Provide exactly one of --apps-dir or --manifest.

    Examples:

        mdb-engine serve-multi --apps-dir ./blogs/

        mdb-engine serve-multi --manifest multi_app_manifest.json --port 3000

        mdb-engine serve-multi -d ./apps/ --reload --title "My Platform"
    """
    if not apps_dir and not manifest:
        raise click.ClickException("Provide --apps-dir or --manifest (at least one is required)")
    if apps_dir and manifest:
        raise click.ClickException("Provide --apps-dir or --manifest, not both")

    if _uvicorn is None:
        raise click.ClickException(
            "uvicorn is required for `mdb-engine serve-multi`. Install it with: pip install uvicorn"
        )

    click.echo(click.style("mdb-engine serve-multi", bold=True))

    if apps_dir:
        resolved = apps_dir.resolve()
        apps = _discover_apps(resolved)
        if not apps:
            raise click.ClickException(f"No apps found in {resolved} (looking for */manifest.json)")

        click.echo(f"  Apps dir : {resolved}")
        click.echo(f"  Apps     : {len(apps)} discovered")
        for app_info in apps:
            click.echo(f"    /{app_info['slug']}  ({app_info['dir']}/)")

        os.environ["_MDB_SERVE_MULTI_MODE"] = "apps_dir"
        os.environ["_MDB_SERVE_MULTI_PATH"] = str(resolved)
    else:
        resolved = manifest.resolve()
        try:
            entries = _read_multi_manifest(resolved)
        except (json.JSONDecodeError, OSError) as exc:
            raise click.ClickException(f"Failed to read {manifest}: {exc}") from exc

        click.echo(f"  Manifest : {resolved}")
        click.echo(f"  Apps     : {len(entries)} configured")
        for entry in entries:
            click.echo(f"    {entry['prefix']}  ({entry['slug']})")

        os.environ["_MDB_SERVE_MULTI_MODE"] = "manifest"
        os.environ["_MDB_SERVE_MULTI_PATH"] = str(resolved)

    click.echo(f"  Server   : http://{host}:{port}")
    click.echo(f"  Docs     : http://{host}:{port}/docs")
    click.echo()

    os.environ["_MDB_SERVE_MULTI_TITLE"] = title
    if mongo_uri:
        os.environ["MONGODB_URI"] = mongo_uri
    if db_name:
        os.environ["MDB_DB_NAME"] = db_name

    _uvicorn.run(
        "mdb_engine.cli._serve_multi_app:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )
