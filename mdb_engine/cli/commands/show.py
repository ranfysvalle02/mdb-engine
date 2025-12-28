"""
Show command for CLI.

Displays manifest information.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import sys
from pathlib import Path

import click

from ...core.manifest import ManifestValidator
from ..utils import format_manifest_output, load_manifest_file


@click.command()
@click.argument("manifest_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--format",
    "-f",
    type=click.Choice(["json", "yaml", "pretty"], case_sensitive=False),
    default="json",
    help="Output format (default: json)",
)
@click.option(
    "--validate",
    "-v",
    is_flag=True,
    help="Validate manifest before showing",
)
def show(manifest_file: Path, format: str, validate: bool) -> None:
    """
    Display manifest information.

    MANIFEST_FILE: Path to manifest.json file to display

    Examples:
        mdb show manifest.json
        mdb show manifest.json --format pretty
        mdb show manifest.json --format yaml --validate
    """
    try:
        # Load manifest
        manifest = load_manifest_file(manifest_file)

        # Validate if requested
        if validate:
            validator = ManifestValidator()
            is_valid, error_message, error_paths = validator.validate(manifest)
            if not is_valid:
                click.echo(
                    click.style(
                        f"⚠️  Warning: Manifest is invalid: {error_message}",
                        fg="yellow",
                    ),
                    err=True,
                )
                if error_paths:
                    click.echo("Error paths:", err=True)
                    for path in error_paths:
                        click.echo(f"  - {path}", err=True)

        # Format and display
        output = format_manifest_output(manifest, format.lower())
        click.echo(output)
        sys.exit(0)
    except click.ClickException:
        raise
