"""
Migrate command for CLI.

Migrates a manifest to a target schema version.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import sys
from pathlib import Path

import click

from ...core.manifest import CURRENT_SCHEMA_VERSION, migrate_manifest
from ..utils import load_manifest_file, save_manifest_file


@click.command()
@click.argument("manifest_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--target-version",
    "-t",
    default=CURRENT_SCHEMA_VERSION,
    help=f"Target schema version (default: {CURRENT_SCHEMA_VERSION})",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    help="Output file path (default: stdout)",
)
@click.option(
    "--in-place",
    "-i",
    is_flag=True,
    help="Update manifest file in place",
)
def migrate(
    manifest_file: Path,
    target_version: str,
    output: Path | None,
    in_place: bool,
) -> None:
    """
    Migrate a manifest.json file to a target schema version.

    MANIFEST_FILE: Path to manifest.json file to migrate

    Examples:
        mdb migrate manifest.json
        mdb migrate manifest.json --target-version 2.0 --output manifest.v2.json
        mdb migrate manifest.json --in-place
    """
    try:
        # Load manifest
        manifest = load_manifest_file(manifest_file)

        # Migrate
        migrated_manifest = migrate_manifest(manifest, target_version=target_version)

        # Determine output
        if in_place:
            output_path = manifest_file
        elif output:
            output_path = output
        else:
            # Output to stdout
            import json

            click.echo(json.dumps(migrated_manifest, indent=2, ensure_ascii=False))
            sys.exit(0)

        # Save to file
        save_manifest_file(output_path, migrated_manifest)
        click.echo(
            click.style(
                f"✅ Migrated manifest to version {target_version}: {output_path}",
                fg="green",
            )
        )
        sys.exit(0)
    except click.ClickException:
        raise
