"""
CLI commands for managing actions.

Provides ``mdb-engine actions new`` and ``mdb-engine actions list``.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

_ACTION_TEMPLATE = '''\
"""
{title} action.

Trigger: {trigger}
"""

from mdb_engine.actions import ActionContext


async def handler(ctx: ActionContext):
    db = await ctx.get_db()
    # TODO: implement action logic
    return ctx.json_response({{"ok": True}})
'''

_SCHEDULED_ACTION_TEMPLATE = '''\
"""
{title} scheduled action.

Runs on a recurring schedule.
"""

__trigger__ = "schedule"
__interval_seconds__ = {interval}

from mdb_engine.actions import ActionContext


async def handler(ctx: ActionContext):
    db = await ctx.get_db()
    # TODO: implement scheduled logic
'''

_EVENT_ACTION_TEMPLATE = '''\
"""
{title} event action.

Fires on {event} for the '{collection}' collection.
"""

__trigger__ = "event"
__event__ = "{event}"
__collection__ = "{collection}"

from mdb_engine.actions import ActionContext


async def handler(ctx: ActionContext):
    doc = ctx.event_doc
    user = ctx.user
    db = await ctx.get_db()
    # TODO: implement event handler logic
'''


@click.group()
def actions():
    """Manage mdb-engine actions."""


@actions.command("new")
@click.argument("name")
@click.option(
    "--trigger",
    type=click.Choice(["http", "schedule", "event"]),
    default="http",
    help="Trigger type",
)
@click.option("--event", default="after_create", help="Event name (for trigger=event)")
@click.option("--collection", default="", help="Collection name (for trigger=event)")
@click.option("--interval", default=3600, type=int, help="Interval in seconds (for trigger=schedule)")
@click.option(
    "--output-dir", "-o", type=click.Path(path_type=Path), default=None, help="Parent directory (default: ./actions)"
)
def new_action(
    name: str,
    trigger: str,
    event: str,
    collection: str,
    interval: int,
    output_dir: Path | None,
) -> None:
    """Scaffold a new action handler.

    Creates actions/NAME.py with a handler template.

    Examples:

        mdb-engine actions new send-email

        mdb-engine actions new cleanup --trigger schedule --interval 3600

        mdb-engine actions new on-signup --trigger event --event after_create --collection users
    """
    if output_dir is None:
        output_dir = Path(".") / "actions"

    output_dir.mkdir(parents=True, exist_ok=True)

    filename = name.replace(" ", "-").lower()
    file_path = output_dir / f"{filename}.py"

    if file_path.exists():
        raise click.ClickException(f"Action file already exists: {file_path}")

    title = name.replace("-", " ").replace("_", " ").title()

    if trigger == "schedule":
        content = _SCHEDULED_ACTION_TEMPLATE.format(title=title, interval=interval)
    elif trigger == "event":
        if not collection:
            raise click.ClickException("--collection is required for event triggers")
        content = _EVENT_ACTION_TEMPLATE.format(title=title, event=event, collection=collection)
    else:
        content = _ACTION_TEMPLATE.format(title=title, trigger=trigger)

    file_path.write_text(content)

    click.echo(click.style(f"Created action '{name}' at {file_path}", fg="green"))
    click.echo(f"  Trigger: {trigger}")
    if trigger == "schedule":
        click.echo(f"  Interval: {interval}s")
    elif trigger == "event":
        click.echo(f"  Event: {event} on '{collection}'")
    click.echo(f"\nEndpoint: POST /actions/v1/{filename}" if trigger == "http" else "")


@actions.command("list")
@click.argument("manifest_file", type=click.Path(exists=True, path_type=Path), required=False, default=None)
@click.option(
    "--actions-dir", "-d", type=click.Path(exists=True, path_type=Path), default=None, help="Actions directory"
)
def list_actions(manifest_file: Path | None, actions_dir: Path | None) -> None:
    """List all discovered actions.

    Reads from the actions/ directory and optional manifest config.

    Examples:

        mdb-engine actions list manifest.json

        mdb-engine actions list --actions-dir ./my-app/actions
    """
    # Resolve actions directory
    if actions_dir is None and manifest_file is not None:
        actions_dir = manifest_file.parent / "actions"
    elif actions_dir is None:
        actions_dir = Path(".") / "actions"

    if not actions_dir.is_dir():
        click.echo("No actions/ directory found.")
        return

    # Load manifest actions config if available
    actions_config: dict = {}
    if manifest_file is not None:
        try:
            with open(manifest_file) as f:
                manifest_data = json.load(f)
            actions_config = manifest_data.get("actions", {})
        except (json.JSONDecodeError, OSError) as exc:
            click.echo(f"Warning: could not read manifest: {exc}", err=True)

    from mdb_engine.actions.discovery import discover_actions

    action_defs = discover_actions(actions_dir, actions_config, slug="cli")

    if not action_defs:
        click.echo("No actions found.")
        return

    click.echo(click.style(f"Found {len(action_defs)} action(s):\n", bold=True))

    for adef in action_defs:
        trigger_label = adef.trigger.upper()
        click.echo(click.style(f"  {adef.name}", fg="cyan", bold=True))
        click.echo(f"    Trigger:  {trigger_label}")
        if adef.trigger == "http":
            click.echo(f"    Method:   {adef.method}")
            click.echo(f"    Endpoint: /actions/v1/{adef.name}")
        elif adef.trigger == "schedule":
            if adef.schedule:
                click.echo(f"    Schedule: {adef.schedule}")
            elif adef.interval_seconds:
                click.echo(f"    Interval: {adef.interval_seconds}s")
        elif adef.trigger == "event":
            click.echo(f"    Event:    {adef.event} on '{adef.collection}'")
        if adef.auth_roles:
            click.echo(f"    Auth:     roles={adef.auth_roles}")
        elif adef.auth_required:
            click.echo("    Auth:     required")
        click.echo(f"    Timeout:  {adef.timeout}s")
        click.echo(f"    Source:   {adef.source_path}")
        click.echo()
