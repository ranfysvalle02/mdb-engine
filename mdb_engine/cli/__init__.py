"""
mdb-engine CLI.

Entry point for the ``mdb-engine`` command.
"""

import click

from .commands.actions import actions
from .commands.add_user import add_user
from .commands.admin import admin
from .commands.codegen import codegen
from .commands.diff import diff
from .commands.doctor import doctor
from .commands.dry_run import dry_run
from .commands.migrate import migrate
from .commands.new_app import new_app
from .commands.reconcile import manifest_group, reconcile, trash_group
from .commands.serve import serve
from .commands.serve_multi import serve_multi
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
main.add_command(add_user, name="add-user")
main.add_command(diff)
main.add_command(dry_run, name="dry-run")
main.add_command(codegen)
main.add_command(serve_multi, name="serve-multi")
main.add_command(actions)
main.add_command(reconcile)
main.add_command(manifest_group, name="manifest")
main.add_command(trash_group, name="trash")
main.add_command(admin)
