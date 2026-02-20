"""
Doctor command — diagnose mdb-engine environment and configuration.

Checks MongoDB connectivity, required env vars, JWT secret strength,
manifest validity, and AI API key availability.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import click


def _check(label: str, ok: bool, detail: str = "") -> bool:
    icon = click.style("OK", fg="green") if ok else click.style("FAIL", fg="red")
    msg = f"  [{icon}] {label}"
    if detail:
        msg += f"  — {detail}"
    click.echo(msg)
    return ok


@click.command()
@click.option("--manifest", "-m", type=click.Path(exists=True, path_type=Path), help="Manifest to validate")
@click.option("--apps-dir", type=click.Path(exists=True, path_type=Path), help="Directory of app manifests")
def doctor(manifest: Path | None, apps_dir: Path | None):
    """Run diagnostics on your mdb-engine environment.

    Examples:

        mdb-engine doctor

        mdb-engine doctor --manifest apps/my-app/manifest.json

        mdb-engine doctor --apps-dir apps/
    """
    click.echo(click.style("mdb-engine doctor", bold=True))
    click.echo()
    all_ok = True

    # ── 1. Environment variables ──────────────────────────────────────

    click.echo(click.style("Environment", bold=True))

    from mdb_engine.env import get_db_name, get_jwt_secret, get_mongo_uri

    mongo_uri = get_mongo_uri()
    all_ok &= _check("MongoDB URI", bool(mongo_uri), mongo_uri[:40] + "..." if len(mongo_uri) > 40 else mongo_uri)

    db_name = get_db_name()
    all_ok &= _check("Database name", bool(db_name), db_name)

    jwt_secret = get_jwt_secret()
    if jwt_secret:
        strength = "strong" if len(jwt_secret) >= 32 else f"WEAK ({len(jwt_secret)} chars, need 32+)"
        all_ok &= _check("JWT secret", len(jwt_secret) >= 32, strength)
    else:
        all_ok &= _check("JWT secret", False, "not set (set MDB_JWT_SECRET)")

    click.echo()

    # ── 2. MongoDB connectivity ───────────────────────────────────────

    click.echo(click.style("MongoDB connectivity", bold=True))

    try:
        from pymongo import MongoClient
        from pymongo.errors import PyMongoError

        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=3000)
        info = client.server_info()
        version = info.get("version", "unknown")
        client.close()
        all_ok &= _check("Connection", True, f"MongoDB {version}")
    except (PyMongoError, OSError) as exc:
        all_ok &= _check("Connection", False, str(exc)[:80])

    click.echo()

    # ── 3. AI API keys ────────────────────────────────────────────────

    click.echo(click.style("AI services", bold=True))

    openai_key = os.getenv("OPENAI_API_KEY")
    azure_key = os.getenv("AZURE_OPENAI_API_KEY")
    _check("OpenAI API key", bool(openai_key), "set" if openai_key else "not set")
    _check("Azure OpenAI key", bool(azure_key), "set" if azure_key else "not set")

    click.echo()

    # ── 4. Manifest validation ────────────────────────────────────────

    manifests_to_check: list[Path] = []
    if manifest:
        manifests_to_check.append(manifest)
    if apps_dir:
        manifests_to_check.extend(sorted(apps_dir.rglob("manifest.json")))

    if manifests_to_check:
        click.echo(click.style("Manifest validation", bold=True))

        from mdb_engine.core.manifest import ManifestValidator

        validator = ManifestValidator()

        for mf in manifests_to_check:
            try:
                data = json.loads(mf.read_text())
                is_valid, error_msg, _paths = asyncio.run(validator.validate(data))
                all_ok &= _check(
                    str(mf),
                    is_valid,
                    "valid" if is_valid else (error_msg or "invalid"),
                )
            except (json.JSONDecodeError, ValueError, OSError) as exc:
                all_ok &= _check(str(mf), False, str(exc)[:80])

        click.echo()

    # ── Summary ───────────────────────────────────────────────────────

    if all_ok:
        click.echo(click.style("All checks passed.", fg="green", bold=True))
    else:
        click.echo(click.style("Some checks failed — see above.", fg="yellow", bold=True))
        sys.exit(1)
