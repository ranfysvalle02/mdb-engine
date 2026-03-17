"""
Add-user command for CLI.

Creates a user in an app's users collection from a manifest file,
with proper bcrypt hashing.  Designed for production admin provisioning
without putting secrets in manifest files or env vars.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import click
from pymongo.errors import PyMongoError


def _run_create_user(
    *,
    mongo_uri: str | None,
    db_name: str | None,
    slug: str,
    collection_name: str,
    email: str,
    password: str,
    role: str,
) -> dict | None:
    """Run the async user-creation flow synchronously."""

    async def _inner():
        from mdb_engine import MongoDBEngine

        engine = MongoDBEngine(mongo_uri=mongo_uri, db_name=db_name)
        await engine.initialize()
        try:
            db = await engine.get_scoped_db(slug)
            from mdb_engine.auth.users import create_app_user

            return await create_app_user(
                db=db,
                email=email,
                password=password,
                role=role,
                collection_name=collection_name,
            )
        finally:
            await engine.shutdown()

    return asyncio.run(_inner())


@click.command("add-user")
@click.argument("manifest_file", type=click.Path(exists=True, path_type=Path))
@click.option("--email", required=True, help="User email address")
@click.option("--role", default="admin", show_default=True, help="User role")
@click.option(
    "--password",
    default=None,
    help="User password (prompted interactively if omitted)",
)
@click.option("--mongo-uri", envvar="MONGODB_URI", default=None, help="MongoDB connection URI")
@click.option("--db-name", envvar="MDB_DB_NAME", default=None, help="Database name")
def add_user(
    manifest_file: Path,
    email: str,
    role: str,
    password: str | None,
    mongo_uri: str | None,
    db_name: str | None,
) -> None:
    """Create a user in an app's database from a manifest file.

    Reads the manifest to determine the app slug and users collection,
    connects to MongoDB, and creates the user with bcrypt-hashed password.

    MANIFEST_FILE: Path to manifest.json

    Examples:

        mdb-engine add-user manifest.json --email admin@foo.com --role admin

        mdb-engine add-user manifest.json --email reader@foo.com --role reader --password s3cret
    """
    manifest_path = manifest_file.resolve()

    try:
        with open(manifest_path) as f:
            manifest_data = json.load(f)
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"Invalid JSON in {manifest_file}: {exc}") from exc

    slug = manifest_data.get("slug")
    if not slug:
        raise click.ClickException(f"Manifest {manifest_file} is missing required 'slug' field")

    auth_config = manifest_data.get("auth", {})
    users_config = auth_config.get("users", {})
    if not users_config.get("enabled", False):
        raise click.ClickException(
            f"App-level users are not enabled in {manifest_file}. " "Set auth.users.enabled to true in the manifest."
        )

    collection_name = users_config.get("collection_name", "users")

    if not password:
        password = click.prompt("Password", hide_input=True, confirmation_prompt=True)

    if len(password) < 6:
        raise click.ClickException("Password must be at least 6 characters")

    click.echo(click.style("mdb-engine add-user", bold=True))
    click.echo(f"  App slug   : {slug}")
    click.echo(f"  Collection : {collection_name}")
    click.echo(f"  Email      : {email}")
    click.echo(f"  Role       : {role}")
    click.echo()

    try:
        user = _run_create_user(
            mongo_uri=mongo_uri,
            db_name=db_name,
            slug=slug,
            collection_name=collection_name,
            email=email,
            password=password,
            role=role,
        )
    except (PyMongoError, KeyError, ValueError, OSError, RuntimeError) as exc:
        raise click.ClickException(f"Failed to create user: {exc}") from exc

    if user:
        click.echo(click.style(f"User {email} created with role '{role}'.", fg="green"))
    else:
        raise click.ClickException(f"Could not create user {email}. The user may already exist.")
