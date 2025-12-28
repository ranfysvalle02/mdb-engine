"""
Generate command for CLI.

Generates a template manifest.json file.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import sys
from pathlib import Path
from typing import Any, Dict

import click

from ...core.manifest import CURRENT_SCHEMA_VERSION
from ..utils import save_manifest_file


@click.command()
@click.option(
    "--slug",
    "-s",
    prompt="App slug",
    help="App slug (lowercase alphanumeric, underscores, hyphens)",
)
@click.option(
    "--name",
    "-n",
    prompt="App name",
    help="Human-readable app name",
)
@click.option(
    "--description",
    "-d",
    default="",
    help="App description",
)
@click.option(
    "--output",
    "-o",
    default="manifest.json",
    type=click.Path(path_type=Path),
    help="Output file path (default: manifest.json)",
)
@click.option(
    "--minimal",
    "-m",
    is_flag=True,
    help="Generate minimal template (basic fields only)",
)
def generate(
    slug: str,
    name: str,
    description: str,
    output: Path,
    minimal: bool,
) -> None:
    """
    Generate a template manifest.json file.

    Examples:
        mdb generate --slug my-app --name "My App"
        mdb generate --slug my-app --name "My App" --output custom.json
        mdb generate --slug my-app --name "My App" --minimal
    """
    try:
        # Validate slug format
        if not slug or not all(c.isalnum() or c in ("_", "-") for c in slug):
            raise click.ClickException(
                "Invalid slug format. Use lowercase alphanumeric, underscores, or hyphens."
            )

        # Generate template manifest
        manifest: Dict[str, Any] = {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "slug": slug,
            "name": name,
            "status": "draft",
        }

        if description:
            manifest["description"] = description

        if not minimal:
            # Add common sections
            manifest["auth"] = {
                "policy": {
                    "provider": "casbin",
                    "required": False,
                    "allow_anonymous": True,
                    "authorization": {
                        "model": "rbac",
                        "link_users_roles": True,
                    },
                },
            }
            manifest["managed_indexes"] = {}
            manifest["websockets"] = {}

        # Save manifest
        save_manifest_file(output, manifest)
        click.echo(click.style(f"✅ Generated manifest template: {output}", fg="green"))
        sys.exit(0)
    except click.ClickException:
        raise
