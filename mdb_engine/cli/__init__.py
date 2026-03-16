"""
mdb-engine CLI.

Entry point for the ``mdb-engine`` command.
"""

import click

from .commands.doctor import doctor
from .commands.migrate import migrate
from .commands.new_app import new_app
from .commands.serve import serve
from .commands.validate import validate


@click.group()
@click.version_option(package_name="mdb-engine")
def main():
    """mdb-engine — MongoDB Engine CLI."""


main.add_command(validate)
main.add_command(migrate)
main.add_command(new_app, name="new-app")
main.add_command(doctor)
main.add_command(serve)
