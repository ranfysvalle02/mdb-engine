"""
CLI commands for the manifest reconciler.

Exposes:

- ``mdb-engine reconcile <manifest.json> [--dry-run] [--mode ...] [--force]
    [--yes] [--output-format={text,json,markdown}] [--expected-head=HASH]
    [--against=<path-or-git-ref>] [--manifest-only]``
- ``mdb-engine manifest history <slug>``
- ``mdb-engine manifest diff <slug>``
- ``mdb-engine manifest show <slug> <revision>``
- ``mdb-engine manifest adopt <slug>``
- ``mdb-engine trash ls <slug>``
- ``mdb-engine trash summary``
- ``mdb-engine trash restore <slug> <trash_id> [--dry-run]``
- ``mdb-engine trash purge <slug> [--expired|--all]``

Exit codes are stable (see :mod:`mdb_engine.constants`):

- ``0``  success
- ``1``  unexpected error
- ``2``  drift detected (``--expected-head`` mismatch)
- ``3``  locked — another worker is applying
- ``4``  confirmation required (pass ``--yes`` / ``MDB_CONFIRM=1``)

All commands require a live MongoDB connection (respect ``MONGODB_URI`` /
``MDB_DB_NAME`` env vars, same as ``mdb-engine serve``).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import click

from ...constants import (
    CLI_EXIT_CONFIRMATION_REQUIRED,
    CLI_EXIT_DRIFT,
    CLI_EXIT_ERROR,
    CLI_EXIT_LOCKED,
    CLI_EXIT_OK,
)
from ..utils import load_manifest_file


async def _with_engine(action):
    """Create + initialize a MongoDBEngine, run ``action(engine)``, shut down."""
    from ...core.engine import MongoDBEngine

    engine = MongoDBEngine()
    await engine.initialize()
    try:
        return await action(engine)
    finally:
        await engine.shutdown()


def _dump_json(value: Any) -> str:
    return json.dumps(value, default=str, indent=2, sort_keys=True)


def _format_plan_markdown(plan: dict[str, Any]) -> str:
    """Render a plan as Markdown for PR comments / CI logs."""
    lines: list[str] = []
    slug = plan.get("slug", "?")
    lines.append(f"### Reconcile plan — `{slug}`")
    lines.append("")
    lines.append(f"- Mode: `{plan.get('mode')}`")
    lines.append(f"- Hash: `{plan.get('from_hash')}` → `{plan.get('to_hash')}`")
    lines.append(f"- Destructive: **{'yes' if plan.get('is_destructive') else 'no'}**")
    lines.append("")
    summary = plan.get("summary") or {}
    if not summary:
        lines.append("_(no changes)_")
        return "\n".join(lines)
    lines.append("**Summary:**")
    for op, n in sorted(summary.items()):
        marker = "~" if op.endswith("_skipped") else "•"
        lines.append(f"- {marker} `{op}`: {n}")
    patch = plan.get("patch") or []
    if patch:
        lines.append("")
        lines.append("<details><summary>Manifest patch</summary>")
        lines.append("")
        from ...core.manifest_diff import format_patch_markdown

        lines.append(format_patch_markdown(patch))
        lines.append("")
        lines.append("</details>")
    return "\n".join(lines)


def _load_against(ref: str) -> dict[str, Any]:
    """Load a prior manifest from a path or ``git show`` reference.

    A value that looks like ``HEAD~3:manifest.json`` or contains ``:`` is
    treated as a git ref; otherwise it's a file path.
    """
    if ":" in ref and not Path(ref).exists():
        try:
            payload = subprocess.check_output(
                ["git", "show", ref],
                stderr=subprocess.PIPE,
                text=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError) as e:  # noqa: BLE001
            raise click.ClickException(f"could not resolve --against ref {ref!r}: {e}") from e
        try:
            return json.loads(payload)
        except json.JSONDecodeError as e:
            raise click.ClickException(f"--against {ref!r} is not valid JSON") from e
    path = Path(ref)
    if not path.exists():
        raise click.ClickException(f"--against {ref!r} does not exist")
    return load_manifest_file(path)


def _map_status_to_exit_code(status: str) -> int:
    return {
        "applied": CLI_EXIT_OK,
        "noop": CLI_EXIT_OK,
        "dry_run": CLI_EXIT_OK,
        "locked": CLI_EXIT_LOCKED,
        "drift": CLI_EXIT_DRIFT,
        "confirmation_required": CLI_EXIT_CONFIRMATION_REQUIRED,
        "invalid_manifest": CLI_EXIT_ERROR,
    }.get(status, CLI_EXIT_ERROR)


# ----- reconcile -------------------------------------------------------


@click.command("reconcile")
@click.argument("manifest_file", type=click.Path(exists=True, path_type=Path))
@click.option("--dry-run", is_flag=True, help="Compute the plan without applying it.")
@click.option(
    "--mode",
    type=click.Choice(["safe", "reconcile", "strict"]),
    default=None,
    help="Override manifest_tracking.mode for this run.",
)
@click.option("--force", is_flag=True, help="Apply even if manifest hash is unchanged.")
@click.option(
    "--yes",
    "yes",
    is_flag=True,
    help="Bypass manifest_tracking.confirm_if safety gates (also via MDB_CONFIRM=1).",
)
@click.option(
    "--output-format",
    type=click.Choice(["text", "json", "markdown"]),
    default="text",
    help="Output format. markdown is useful for posting plans as PR comments.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Alias for --output-format=json.",
)
@click.option(
    "--expected-head",
    default=None,
    help="Fail with exit code 2 if the current HEAD revision hash does not match.",
)
@click.option(
    "--against",
    default=None,
    help="Compare the manifest against a previous revision (file path or 'git ref:path').",
)
@click.option(
    "--manifest-only",
    is_flag=True,
    help="When used with --against, diff purely against a file; never touch the DB ledger.",
)
@click.option(
    "--user",
    "user",
    default=None,
    help="Operator identity recorded on the revision row (default: $USER).",
)
@click.option(
    "--git-commit",
    "git_commit",
    default=None,
    help="Git SHA recorded on the revision row (default: $MDB_GIT_COMMIT / $GIT_COMMIT).",
)
def reconcile(
    manifest_file: Path,
    dry_run: bool,
    mode: str | None,
    force: bool,
    yes: bool,
    output_format: str,
    as_json: bool,
    expected_head: str | None,
    against: str | None,
    manifest_only: bool,
    user: str | None,
    git_commit: str | None,
) -> None:
    """Reconcile a manifest against the database for its slug.

    When ``--manifest-only --against=<ref>`` is used the command never
    connects to the database — a useful CI check for PRs before merge.
    """
    try:
        manifest = load_manifest_file(manifest_file)
    except (FileNotFoundError, ValueError) as e:
        raise click.ClickException(str(e)) from e

    slug = manifest.get("slug")
    if not slug:
        raise click.ClickException("manifest is missing 'slug'")

    if mode is not None:
        manifest.setdefault("manifest_tracking", {})
        manifest["manifest_tracking"]["mode"] = mode

    resolved_format = "json" if as_json else output_format

    # --- Pure-CI path: diff two manifests without talking to MongoDB ---
    if manifest_only:
        if not against:
            raise click.ClickException("--manifest-only requires --against=<ref>")
        prev = _load_against(against)
        from ...core.manifest_diff import format_patch_markdown, manifest_patch
        from ...core.manifest_hash import compute_manifest_hash

        patch = manifest_patch(prev, manifest)
        result = {
            "status": "dry_run",
            "slug": slug,
            "from_hash": compute_manifest_hash(prev),
            "to_hash": compute_manifest_hash(manifest),
            "patch": patch,
            "manifest_only": True,
        }
        if resolved_format == "json":
            click.echo(_dump_json(result))
        elif resolved_format == "markdown":
            click.echo(f"### Manifest diff — `{slug}`")
            click.echo("")
            click.echo(f"- From: `{result['from_hash']}`")
            click.echo(f"- To:   `{result['to_hash']}`")
            click.echo("")
            click.echo(format_patch_markdown(patch))
        else:
            click.echo(click.style(f"Manifest-only diff for {slug}", bold=True))
            click.echo(f"  {result['from_hash']} -> {result['to_hash']}")
            click.echo(f"  {len(patch)} structural change(s)")
        sys.exit(CLI_EXIT_OK)

    async def run(engine):
        return await engine.reconcile(
            slug,
            manifest,
            dry_run=dry_run,
            force=force,
            confirm=yes,
            expected_head=expected_head,
            caused_by_commit=git_commit,
            caused_by_user=user,
        )

    try:
        result = asyncio.run(_with_engine(run))
    except (RuntimeError, ValueError) as e:
        raise click.ClickException(str(e)) from e

    status = result.get("status", "unknown")
    # Interactive confirmation: if the gate tripped, we're on a TTY,
    # and the operator did not already opt in, prompt.
    if (
        status == "confirmation_required"
        and sys.stdin.isatty()
        and not yes
        and not os.environ.get("MDB_CONFIRM", "").strip()
    ):
        plan = result.get("plan") or {}
        click.echo(click.style("Confirmation required:", fg="yellow"))
        for r in result.get("reasons") or []:
            click.echo(f"  - {r}")
        click.echo(click.style("Plan summary:", bold=True))
        for op, n in sorted((plan.get("summary") or {}).items()):
            click.echo(f"  {op}: {n}")
        choice = (
            click.prompt(
                "Proceed? [y/N/diff]",
                default="n",
                show_default=False,
                prompt_suffix=" ",
            )
            .strip()
            .lower()
        )
        if choice == "diff":
            from ...core.manifest_diff import format_patch_markdown

            click.echo(format_patch_markdown(plan.get("patch") or []))
            choice = click.prompt("Proceed? [y/N]", default="n", show_default=False, prompt_suffix=" ").strip().lower()
        if choice in ("y", "yes"):

            async def run2(engine):
                return await engine.reconcile(
                    slug,
                    manifest,
                    dry_run=dry_run,
                    force=force,
                    confirm=True,
                    expected_head=expected_head,
                    caused_by_commit=git_commit,
                    caused_by_user=user,
                )

            result = asyncio.run(_with_engine(run2))
            status = result.get("status", "unknown")
    exit_code = _map_status_to_exit_code(status)

    if resolved_format == "json":
        click.echo(_dump_json(result))
        sys.exit(exit_code)
    if resolved_format == "markdown":
        plan = result.get("plan") or {}
        click.echo(_format_plan_markdown(plan))
        if status == "drift":
            click.echo(
                f"\n> **Drift**: expected_head=`{result.get('expected_head')}` "
                f"!= actual=`{result.get('actual_head')}`"
            )
        elif status == "confirmation_required":
            reasons = "; ".join(result.get("reasons") or [])
            click.echo(f"\n> **Confirmation required**: {reasons}")
        sys.exit(exit_code)

    plan = result.get("plan") or {}
    click.echo(click.style(f"Status: {status}", bold=True))
    if status == "drift":
        click.echo(
            click.style(
                f"Drift: expected={result.get('expected_head')} actual={result.get('actual_head')}",
                fg="red",
            )
        )
        sys.exit(exit_code)
    if status == "confirmation_required":
        click.echo(click.style("Confirmation required:", fg="yellow"))
        for r in result.get("reasons") or []:
            click.echo(f"  - {r}")
        click.echo("  Re-run with --yes (or MDB_CONFIRM=1) to apply.")
        sys.exit(exit_code)
    if status == "invalid_manifest":
        click.echo(click.style(f"Invalid manifest: {result.get('reason')}", fg="red"))
        sys.exit(exit_code)
    if status == "locked":
        click.echo(click.style("Another worker holds the reconcile lock.", fg="yellow"))
        sys.exit(exit_code)
    click.echo(f"Slug:   {plan.get('slug')}")
    click.echo(f"Mode:   {plan.get('mode')}")
    click.echo(f"Hash:   {plan.get('from_hash')} -> {plan.get('to_hash')}")
    summary = plan.get("summary") or {}
    if summary:
        click.echo(click.style("Changes:", bold=True))
        for op, n in sorted(summary.items()):
            if op.endswith("_skipped"):
                click.echo(click.style(f"  ~ {op}: {n}", fg="cyan"))
            elif op.startswith("drop_") or op.startswith("disable_"):
                click.echo(click.style(f"  - {op}: {n}", fg="red"))
            else:
                click.echo(click.style(f"  + {op}: {n}", fg="green"))
    else:
        click.echo("(no changes)")
    sys.exit(exit_code)


# ----- manifest history / diff / show / adopt ----------------------------


@click.group("manifest")
def manifest_group() -> None:
    """Inspect manifest history and pending changes."""


@manifest_group.command("history")
@click.argument("slug")
@click.option("--limit", "-n", type=int, default=20, help="Max revisions to show.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def manifest_history(slug: str, limit: int, as_json: bool) -> None:
    """Show recent manifest revisions for SLUG (newest first)."""

    async def run(engine):
        return await engine.manifest_history(slug, limit=limit)

    try:
        rows = asyncio.run(_with_engine(run))
    except (RuntimeError, ValueError) as e:
        raise click.ClickException(str(e)) from e

    if as_json:
        click.echo(_dump_json(rows))
        sys.exit(CLI_EXIT_OK)

    if not rows:
        click.echo(f"No revisions recorded for '{slug}'")
        sys.exit(CLI_EXIT_OK)

    click.echo(click.style(f"=== Revisions for {slug} ===", bold=True))
    for r in rows:
        rev = r.get("revision")
        when = r.get("applied_at")
        mode = r.get("mode")
        destructive = "destructive" if r.get("is_destructive") else "additive"
        summary = r.get("summary") or {}
        parts = ", ".join(f"{k}={v}" for k, v in sorted(summary.items()))
        click.echo(f"  r{rev:<4} {str(when):<30}  mode={mode}  [{destructive}]  {parts}")
    sys.exit(CLI_EXIT_OK)


@manifest_group.command("diff")
@click.argument("slug")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@click.option(
    "--output-format",
    type=click.Choice(["text", "json", "markdown"]),
    default="text",
)
def manifest_diff(slug: str, as_json: bool, output_format: str) -> None:
    """Preview the plan that would be applied against the current persisted manifest."""

    async def run(engine):
        return await engine.manifest_diff(slug)

    try:
        plan = asyncio.run(_with_engine(run))
    except (RuntimeError, ValueError) as e:
        raise click.ClickException(str(e)) from e

    resolved_format = "json" if as_json else output_format
    if resolved_format == "json":
        click.echo(_dump_json(plan))
        sys.exit(CLI_EXIT_OK)
    if resolved_format == "markdown":
        click.echo(_format_plan_markdown(plan))
        sys.exit(CLI_EXIT_OK)

    summary = plan.get("summary") or {}
    click.echo(click.style(f"=== Diff for {slug} ===", bold=True))
    click.echo(f"Mode:   {plan.get('mode')}")
    click.echo(f"Hash:   {plan.get('from_hash')} -> {plan.get('to_hash')}")
    if plan.get("is_noop"):
        click.echo("(no changes)")
        sys.exit(CLI_EXIT_OK)
    for op, n in sorted(summary.items()):
        click.echo(f"  {op}: {n}")
    sys.exit(CLI_EXIT_OK)


@manifest_group.command("show")
@click.argument("slug")
@click.argument("revision", type=int)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def manifest_show(slug: str, revision: int, as_json: bool) -> None:
    """Show a specific manifest revision for SLUG."""

    async def run(engine):
        rows = await engine.manifest_history(slug, limit=1000)
        for r in rows:
            if int(r.get("revision", -1)) == revision:
                return r
        return None

    try:
        row = asyncio.run(_with_engine(run))
    except (RuntimeError, ValueError) as e:
        raise click.ClickException(str(e)) from e

    if row is None:
        click.echo(click.style(f"Revision r{revision} not found for '{slug}'", fg="red"))
        sys.exit(CLI_EXIT_ERROR)

    if as_json:
        click.echo(_dump_json(row))
    else:
        click.echo(_dump_json(row))
    sys.exit(CLI_EXIT_OK)


@manifest_group.command("adopt")
@click.argument("slug")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Report what would be adopted without modifying the ledger.",
)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def manifest_adopt(slug: str, dry_run: bool, as_json: bool) -> None:
    """Seed the reconciler ledger from existing <slug>_* collections."""

    async def run(engine):
        if dry_run:
            from motor.motor_asyncio import AsyncIOMotorDatabase  # noqa: F401

            db = engine._app_registration_manager._mongo_db  # noqa: SLF001
            prefix = f"{slug}_"
            names = await db.list_collection_names()
            return {
                "dry_run": True,
                "would_adopt_collections": [n[len(prefix) :] for n in names if n.startswith(prefix)],
            }
        return await engine.manifest_adopt(slug)

    try:
        result = asyncio.run(_with_engine(run))
    except (RuntimeError, ValueError) as e:
        raise click.ClickException(str(e)) from e

    if as_json:
        click.echo(_dump_json(result))
    else:
        click.echo(click.style(f"Adopt {slug}: {_dump_json(result)}", fg="green"))
    sys.exit(CLI_EXIT_OK)


# ----- trash commands --------------------------------------------------


@click.group("trash")
def trash_group() -> None:
    """List / restore / purge quarantined artifacts."""


@trash_group.command("ls")
@click.argument("slug", required=False)
@click.option("--all", "list_all", is_flag=True, help="List across all slugs (admin view).")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def trash_ls(slug: str | None, list_all: bool, as_json: bool) -> None:
    """List quarantined artifacts for SLUG (or every slug with --all)."""

    if list_all and not os.environ.get("MDB_ADMIN", "").strip() and not sys.stdin.isatty():
        # Defensive — surface the requirement early in CI usage.
        click.echo("--all requires MDB_ADMIN=1 or an interactive terminal.", err=True)
        sys.exit(CLI_EXIT_ERROR)

    async def run(engine):
        if list_all:
            return await engine.trash_list_all()
        if not slug:
            raise click.ClickException("trash ls requires a SLUG (or pass --all).")
        return await engine.trash_list(slug)

    try:
        rows = asyncio.run(_with_engine(run))
    except (RuntimeError, ValueError) as e:
        raise click.ClickException(str(e)) from e

    if as_json:
        click.echo(_dump_json(rows))
        sys.exit(CLI_EXIT_OK)

    if not rows:
        click.echo(f"No quarantined artifacts for '{slug or 'any slug'}'")
        sys.exit(CLI_EXIT_OK)

    header = f"=== Trash for {slug} ===" if slug and not list_all else "=== Trash (all slugs) ==="
    click.echo(click.style(header, bold=True))
    from datetime import datetime, timezone

    # Column widths pre-computed for alignment.
    id_w = max(24, max(len(str(r.get("_id"))) for r in rows))
    kind_w = max(10, max(len(str(r.get("kind", ""))) for r in rows))
    name_w = max(16, max(len(str(r.get("original_name", ""))) for r in rows))
    now = datetime.now(timezone.utc)
    for r in rows:
        expires = r.get("expires_at")
        days_left = None
        if isinstance(expires, datetime):
            days_left = int((expires - now).total_seconds() // 86400)
        if days_left is None:
            color = "white"
        elif days_left < 0:
            color = "red"
        elif days_left < 2:
            color = "yellow"
        else:
            color = "green"
        line = (
            f"  {str(r.get('_id')):<{id_w}}  "
            f"slug={r.get('slug', '-'):<16}  "
            f"kind={str(r.get('kind', '')):<{kind_w}}  "
            f"orig={str(r.get('original_name', '')):<{name_w}}  "
            f"docs={r.get('doc_count', '?'):<7}  "
            f"expires={expires} ({days_left}d)"
        )
        click.echo(click.style(line, fg=color))
    sys.exit(CLI_EXIT_OK)


@trash_group.command("summary")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def trash_summary_cmd(as_json: bool) -> None:
    """Show per-slug trash totals."""

    async def run(engine):
        return await engine.trash_summary()

    try:
        rows = asyncio.run(_with_engine(run))
    except (RuntimeError, ValueError) as e:
        raise click.ClickException(str(e)) from e

    if as_json:
        click.echo(_dump_json(rows))
        sys.exit(CLI_EXIT_OK)

    if not rows:
        click.echo("No quarantined artifacts.")
        sys.exit(CLI_EXIT_OK)

    click.echo(click.style("=== Trash summary ===", bold=True))
    for r in rows:
        click.echo(
            f"  {r.get('slug'):<24} n={r.get('n', 0):<4} docs={r.get('total_docs', 0):<9} "
            f"next_expires_at={r.get('next_expires_at')}"
        )
    sys.exit(CLI_EXIT_OK)


@trash_group.command("restore")
@click.argument("slug")
@click.argument("trash_id")
@click.option("--dry-run", is_flag=True, help="Preview whether the restore can succeed.")
def trash_restore(slug: str, trash_id: str, dry_run: bool) -> None:
    """Restore a quarantined artifact by its ObjectId."""

    async def run(engine):
        return await engine.trash_restore(slug, trash_id, dry_run=dry_run)

    try:
        result = asyncio.run(_with_engine(run))
    except (RuntimeError, ValueError) as e:
        raise click.ClickException(str(e)) from e

    if dry_run:
        can = result.get("can_restore")
        if can:
            click.echo(click.style("Would restore (no conflicts).", fg="green"))
        else:
            click.echo(click.style("Cannot restore:", fg="red"))
            for reason in result.get("reasons") or []:
                click.echo(f"  - {reason}")
        sys.exit(CLI_EXIT_OK if can else CLI_EXIT_ERROR)

    click.echo(click.style(f"Restored: {result}", fg="green"))
    sys.exit(CLI_EXIT_OK)


@trash_group.command("purge")
@click.argument("slug")
@click.option("--all", "purge_all", is_flag=True, help="Purge all (not just expired).")
@click.option(
    "--expired/--all-items",
    "expired_only",
    default=True,
    help="Only purge expired tombstones (default).",
)
def trash_purge(slug: str, purge_all: bool, expired_only: bool) -> None:
    """Hard-drop quarantined artifacts."""

    async def run(engine):
        return await engine.trash_purge(slug, expired_only=(not purge_all) and expired_only)

    try:
        count = asyncio.run(_with_engine(run))
    except (RuntimeError, ValueError) as e:
        raise click.ClickException(str(e)) from e

    click.echo(click.style(f"Purged {count} artifact(s)", fg="yellow"))
    sys.exit(CLI_EXIT_OK)


__all__ = [
    "reconcile",
    "manifest_group",
    "trash_group",
]
