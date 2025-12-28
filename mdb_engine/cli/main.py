"""
Main CLI entry point for MDB_ENGINE.

This module provides the command-line interface for manifest management.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import click

from .commands.generate import generate
from .commands.migrate import migrate
from .commands.show import show
from .commands.validate import validate


@click.group()
@click.version_option(version="0.1.6", prog_name="mdb")
def cli() -> None:
    """
    MDB_ENGINE CLI - Manifest management tool.

    Manage your MDB_ENGINE manifests with validation, migration, and generation.
    """
    pass


# Register commands
cli.add_command(validate)
cli.add_command(migrate)
cli.add_command(generate)
cli.add_command(show)


def main() -> None:
    """Entry point for the CLI."""
    cli()


if __name__ == "__main__":
    main()
