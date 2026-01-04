"""
Validate command for CLI.

Validates a manifest against the schema.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import sys
from pathlib import Path

import click

from ...core.manifest import ManifestValidator
from ..utils import load_manifest_file


@click.command()
@click.argument("manifest_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Show detailed validation errors",
)
def validate(manifest_file: Path, verbose: bool) -> None:
    """
    Validate a manifest.json file against the schema.

    MANIFEST_FILE: Path to manifest.json file to validate

    Examples:
        mdb validate manifest.json
        mdb validate path/to/manifest.json --verbose
    """
    try:
        # Load manifest
        manifest = load_manifest_file(manifest_file)

        # Validate
        validator = ManifestValidator()
        is_valid, error_message, error_paths = validator.validate(manifest)

        if is_valid:
            click.echo(click.style(f"✅ Manifest '{manifest_file}' is valid!", fg="green"))
            sys.exit(0)
        else:
            click.echo(click.style(f"❌ Manifest '{manifest_file}' is invalid!", fg="red"))
            if error_message:
                click.echo(click.style(f"Error: {error_message}", fg="red"))
            if error_paths and verbose:
                click.echo("\nError paths:")
                for path in error_paths:
                    click.echo(f"  - {path}")
            sys.exit(1)
    except click.ClickException:
        raise
    except (FileNotFoundError, ValueError, KeyError) as e:
        raise click.ClickException(str(e)) from e
