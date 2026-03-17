"""
Serve command for CLI.

Starts a uvicorn API server from a manifest file — zero application code required.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import click


@click.command()
@click.argument("manifest_file", type=click.Path(exists=True, path_type=Path))
@click.option("--host", default="0.0.0.0", show_default=True, help="Bind host")
@click.option("--port", "-p", default=8000, show_default=True, type=int, help="Bind port")
@click.option("--reload", is_flag=True, help="Enable auto-reload (development)")
@click.option("--mongo-uri", envvar="MONGODB_URI", default=None, help="MongoDB connection URI")
@click.option("--db-name", envvar="MDB_DB_NAME", default=None, help="Database name")
def serve(
    manifest_file: Path,
    host: str,
    port: int,
    reload: bool,
    mongo_uri: str | None,
    db_name: str | None,
) -> None:
    """Start an API server from a manifest file.

    MANIFEST_FILE: Path to manifest.json

    Examples:

        mdb-engine serve manifest.json

        mdb-engine serve manifest.json --port 3000 --reload

        mdb-engine serve manifest.json --mongo-uri mongodb+srv://...
    """
    try:
        import uvicorn
    except ImportError:
        raise click.ClickException(
            "uvicorn is required for `mdb-engine serve`. " "Install it with: pip install uvicorn"
        ) from None

    manifest_path = manifest_file.resolve()

    try:
        with open(manifest_path) as f:
            manifest_data = json.load(f)
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"Invalid JSON in {manifest_file}: {exc}") from exc

    slug = manifest_data.get("slug")
    if not slug:
        raise click.ClickException(f"Manifest {manifest_file} is missing required 'slug' field")

    collections = manifest_data.get("collections", {})
    crud_count = sum(1 for c in collections.values() if c.get("auto_crud", True))

    click.echo(click.style("mdb-engine serve", bold=True))
    click.echo(f"  Manifest : {manifest_path}")
    click.echo(f"  App slug : {slug}")
    if crud_count:
        click.echo(f"  Auto-CRUD: {crud_count} collection(s)")
    click.echo(f"  Server   : http://{host}:{port}")
    click.echo(f"  Docs     : http://{host}:{port}/docs")
    public_dir = manifest_path.parent / "public"
    if public_dir.is_dir() and (public_dir / "index.html").exists():
        click.echo(f"  Frontend : http://{host}:{port}")
    click.echo()

    os.environ["_MDB_SERVE_MANIFEST"] = str(manifest_path)
    if mongo_uri:
        os.environ["MONGODB_URI"] = mongo_uri
    if db_name:
        os.environ["MDB_DB_NAME"] = db_name

    uvicorn.run(
        "mdb_engine.cli._serve_app:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )
