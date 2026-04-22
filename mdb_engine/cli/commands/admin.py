"""
mdb-engine admin — HTTP-parity CLI for the admin plane.

Unlike the local ``reconcile`` / ``manifest`` / ``trash`` commands (which
open their own Mongo connection against the engine process), this group
exercises the running FastAPI server over HTTP exactly the way a
third-party integrator would. That guarantees CLI and UI see byte-
identical responses — no drift between surfaces.

Quick start::

    export MDB_ADMIN_URL=https://my-app.example.com
    export MDB_ADMIN_TOKEN=$(pass show mdb/app-tokens/demo)

    mdb-engine admin health --slug demo
    mdb-engine admin reconciler plan --slug demo
    mdb-engine admin reconciler apply --slug demo --yes --idempotency-key "$(date +%s)"
    mdb-engine admin trash list --slug demo
    mdb-engine admin audit tail --slug demo
    mdb-engine admin secrets rotate --slug demo --label ci-gha --scope reconciler:read

Every command supports ``--output table|json`` (default ``table``).
The group-level ``--base-url`` / ``--token-env`` / ``--token-file``
options override the ``MDB_ADMIN_URL`` / ``MDB_ADMIN_TOKEN`` environment
variables when given.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

import click

DEFAULT_URL = "http://127.0.0.1:8000"
DEFAULT_PREFIX = "/__mdb"
TOKEN_ENV = "MDB_ADMIN_TOKEN"
URL_ENV = "MDB_ADMIN_URL"
PREFIX_ENV = "MDB_ADMIN_PREFIX"

OUTPUT_FORMATS = ("table", "json")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class AdminClient:
    """Tiny stdlib-only HTTP client for the admin plane.

    Kept dependency-free so the CLI stays cheap to install in CI
    environments that don't want to pull ``httpx`` just for a five-line
    request.
    """

    def __init__(self, base_url: str, token: str, prefix: str = DEFAULT_PREFIX):
        self.base_url = base_url.rstrip("/")
        self.prefix = prefix.rstrip("/") or DEFAULT_PREFIX
        self.token = token

    def call(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> tuple[int, Any]:
        url = f"{self.base_url}{self.prefix}{path}"
        if params:
            q = {k: v for k, v in params.items() if v is not None}
            url = f"{url}?{urlparse.urlencode(q, doseq=True)}"
        data: bytes | None = None
        headers = {
            "X-App-Token": self.token,
            "Accept": "application/json",
        }
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        req = urlrequest.Request(url=url, data=data, method=method.upper(), headers=headers)
        try:
            with urlrequest.urlopen(req, timeout=30) as resp:  # noqa: S310
                raw = resp.read().decode("utf-8") or "null"
                try:
                    return resp.status, json.loads(raw)
                except json.JSONDecodeError:
                    return resp.status, raw
        except urlerror.HTTPError as e:
            body_text = e.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(body_text) if body_text else None
            except json.JSONDecodeError:
                parsed = body_text
            return e.code, parsed
        except urlerror.URLError as e:
            raise click.ClickException(f"network error calling {url}: {e.reason}") from e


# ---------------------------------------------------------------------------
# Output rendering
# ---------------------------------------------------------------------------


def _as_table(payload: Any) -> str:
    """Render JSON-ish payload as a readable monospace table.

    Supports three shapes:

    - list of dicts → header row + one row per item
    - dict with a single list-of-dicts value (e.g. ``{"items": [...]}``)
      → rendered as the list
    - anything else → 2-column key/value table
    """
    if payload is None:
        return "(empty)"
    if isinstance(payload, dict):
        list_children = [
            (k, v) for k, v in payload.items() if isinstance(v, list) and v and all(isinstance(e, dict) for e in v)
        ]
        if len(list_children) == 1 and len(payload) <= 3:
            key, items = list_children[0]
            head = [f"{k}: {_scalar(v)}" for k, v in payload.items() if k != key]
            rendered = _table_of_dicts(items)
            return ("\n".join(head) + ("\n\n" if head else "") + rendered).rstrip()
        return _kv_table(payload)
    if isinstance(payload, list):
        if payload and all(isinstance(e, dict) for e in payload):
            return _table_of_dicts(payload)
        return "\n".join(str(item) for item in payload)
    return str(payload)


def _kv_table(d: dict[str, Any]) -> str:
    items = [(str(k), _scalar(v)) for k, v in d.items()]
    if not items:
        return "(empty)"
    key_w = max(len(k) for k, _ in items)
    return "\n".join(f"{k.ljust(key_w)}  {v}" for k, v in items)


def _table_of_dicts(rows: list[dict[str, Any]]) -> str:
    # Stable column order: union of keys in order of first appearance.
    cols: list[str] = []
    for r in rows:
        for k in r.keys():
            if k not in cols:
                cols.append(k)
    # Truncate noisy columns for display — full JSON is still in --output json.
    display_cols = [c for c in cols if c not in {"extra"}]
    widths = {c: max(len(c), *[len(_scalar(r.get(c))) for r in rows]) for c in display_cols}
    header = "  ".join(c.ljust(widths[c]) for c in display_cols)
    sep = "  ".join("-" * widths[c] for c in display_cols)
    body = "\n".join("  ".join(_scalar(r.get(c)).ljust(widths[c]) for c in display_cols) for r in rows)
    return f"{header}\n{sep}\n{body}"


def _scalar(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int | float):
        return str(v)
    if isinstance(v, dict | list):
        return json.dumps(v, default=str, separators=(",", ":"))
    return str(v)


def _emit(status: int, payload: Any, *, output: str) -> None:
    if output == "json":
        click.echo(json.dumps(payload, indent=2, default=str))
    else:
        click.echo(_as_table(payload))
    if status >= 400:
        sys.exit(1)


# ---------------------------------------------------------------------------
# Client construction with group-level overrides
# ---------------------------------------------------------------------------


CONFIRM_ENV = "MDB_CONFIRM"
"""Set to ``1`` / ``yes`` / ``true`` to skip the interactive prompt.

Shared with :mod:`mdb_engine.cli.commands.reconcile` so an operator
running ``MDB_CONFIRM=1`` in CI gets the same bypass across local
reconcile commands *and* the admin plane. Matches the terraform /
kubectl convention of a single "I mean it" escape hatch.
"""


def _confirm_destructive(
    action: str,
    *,
    slug: str,
    yes: bool,
    extra: str = "",
) -> None:
    """Guard a destructive CLI action with a kubectl-style prompt.

    Behaviour:

    - ``--yes`` or ``MDB_CONFIRM=1`` → no-op.
    - Interactive TTY → prompt; abort unless the user types the slug.
    - Non-interactive (piped stdin) → fail fast with instructions,
      never silently proceed. This is what prevents an accidental
      ``echo | mdb-engine admin reconciler apply`` in a Makefile.
    """
    if yes or os.environ.get(CONFIRM_ENV, "").strip().lower() in {"1", "yes", "true", "y"}:
        return
    banner = f"About to {action} on '{slug}'."
    if extra:
        banner += f" {extra}"
    click.echo(banner, err=True)
    click.echo("This action is DESTRUCTIVE and cannot be undone.", err=True)
    if not sys.stdin.isatty():
        raise click.ClickException(
            f"refusing to {action} non-interactively without --yes " f"(or set {CONFIRM_ENV}=1)."
        )
    typed = click.prompt(
        f"Type the slug ({slug!r}) to confirm",
        default="",
        show_default=False,
    )
    if typed.strip() != slug:
        raise click.Abort()


def _client(ctx: click.Context) -> AdminClient:
    obj = ctx.obj or {}
    base_url = obj.get("base_url") or os.environ.get(URL_ENV, DEFAULT_URL)
    prefix = obj.get("prefix") or os.environ.get(PREFIX_ENV, DEFAULT_PREFIX)
    token_env = obj.get("token_env") or TOKEN_ENV
    token_file = obj.get("token_file")
    if token_file:
        try:
            token = Path(token_file).expanduser().read_text().strip()
        except OSError as e:
            raise click.ClickException(f"could not read token file {token_file}: {e}") from e
    else:
        token = os.environ.get(token_env, "").strip()
    if not token:
        raise click.ClickException(f"Missing token. Set {token_env} or pass --token-file.")
    return AdminClient(base_url=base_url, token=token, prefix=prefix)


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------


def _common_options(cmd):
    cmd = click.option(
        "--output",
        "-o",
        type=click.Choice(OUTPUT_FORMATS),
        default="table",
        show_default=True,
        help="Render output as a table, raw JSON, or YAML.",
    )(cmd)
    return cmd


@click.group(name="admin")
@click.option("--base-url", envvar=URL_ENV, default=None, help=f"Base URL (env: {URL_ENV}; default: {DEFAULT_URL}).")
@click.option(
    "--prefix",
    envvar=PREFIX_ENV,
    default=None,
    help=f"Admin plane prefix (env: {PREFIX_ENV}; default: {DEFAULT_PREFIX}).",
)
@click.option("--token-env", default=None, help=f"Env var holding the app token (default: {TOKEN_ENV}).")
@click.option("--token-file", default=None, help="Read the app token from this file instead of an env var.")
@click.pass_context
def admin(
    ctx: click.Context,
    base_url: str | None,
    prefix: str | None,
    token_env: str | None,
    token_file: str | None,
) -> None:
    """Call the running admin plane over HTTP (requires an app token)."""
    ctx.ensure_object(dict)
    ctx.obj.update(
        {
            "base_url": base_url,
            "prefix": prefix,
            "token_env": token_env,
            "token_file": token_file,
        }
    )


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


@admin.command("health")
@click.option("--slug", required=True, help="App slug.")
@_common_options
@click.pass_context
def admin_health(ctx: click.Context, slug: str, output: str) -> None:
    """Show enabled modules + their declared scopes + rate limit policy."""
    status, payload = _client(ctx).call("GET", "/health/modules", params={"slug": slug})
    _emit(status, payload, output=output)


# ---------------------------------------------------------------------------
# reconciler
# ---------------------------------------------------------------------------


@admin.group(name="reconciler")
def reconciler_group() -> None:
    """Reconciler plan / apply / history."""


@reconciler_group.command("plan")
@click.option("--slug", required=True)
@_common_options
@click.pass_context
def reconciler_plan(ctx: click.Context, slug: str, output: str) -> None:
    status, payload = _client(ctx).call("GET", "/reconciler/plan", params={"slug": slug})
    _emit(status, payload, output=output)


@reconciler_group.command("apply")
@click.option("--slug", required=True)
@click.option("--dry-run/--no-dry-run", default=False)
@click.option("--yes", is_flag=True, default=False, help="Skip confirm_if gates.")
@click.option("--expected-head", default=None, help="Optimistic-lock manifest hash.")
@click.option(
    "--idempotency-key",
    default=None,
    help="Opaque key for safe retries; defaults to a fresh UUID.",
)
@_common_options
@click.pass_context
def reconciler_apply(
    ctx: click.Context,
    slug: str,
    dry_run: bool,
    yes: bool,
    expected_head: str | None,
    idempotency_key: str | None,
    output: str,
) -> None:
    # Plan-only runs are non-destructive — never prompt.
    if not dry_run:
        _confirm_destructive(
            "apply the reconciler plan",
            slug=slug,
            yes=yes,
            extra="This will mutate collections, indexes, and stored state.",
        )
    key = idempotency_key or f"cli-{uuid.uuid4().hex}"
    status, payload = _client(ctx).call(
        "POST",
        "/reconciler/apply",
        params={
            "slug": slug,
            "dry_run": str(dry_run).lower(),
            "yes": str(yes).lower(),
            "expected_head": expected_head,
        },
        idempotency_key=key,
    )
    _emit(status, payload, output=output)


@reconciler_group.command("history")
@click.option("--slug", required=True)
@click.option("--limit", default=20, type=int)
@_common_options
@click.pass_context
def reconciler_history(ctx: click.Context, slug: str, limit: int, output: str) -> None:
    status, payload = _client(ctx).call(
        "GET",
        "/reconciler/manifest/history",
        params={"slug": slug, "limit": limit},
    )
    _emit(status, payload, output=output)


# ---------------------------------------------------------------------------
# trash
# ---------------------------------------------------------------------------


@admin.group(name="trash")
def trash_group_cli() -> None:
    """Trash listing / restore / purge."""


@trash_group_cli.command("list")
@click.option("--slug", required=True)
@_common_options
@click.pass_context
def trash_list(ctx: click.Context, slug: str, output: str) -> None:
    status, payload = _client(ctx).call("GET", "/trash", params={"slug": slug})
    _emit(status, payload, output=output)


@trash_group_cli.command("summary")
@click.option("--slug", required=True)
@_common_options
@click.pass_context
def trash_summary(ctx: click.Context, slug: str, output: str) -> None:
    status, payload = _client(ctx).call("GET", "/trash/summary", params={"slug": slug})
    _emit(status, payload, output=output)


@trash_group_cli.command("restore")
@click.argument("trash_id")
@click.option("--slug", required=True)
@click.option("--dry-run/--no-dry-run", default=False)
@click.option("--idempotency-key", default=None)
@_common_options
@click.pass_context
def trash_restore(
    ctx: click.Context,
    trash_id: str,
    slug: str,
    dry_run: bool,
    idempotency_key: str | None,
    output: str,
) -> None:
    key = idempotency_key or f"cli-{uuid.uuid4().hex}"
    status, payload = _client(ctx).call(
        "POST",
        f"/trash/{trash_id}/restore",
        params={"slug": slug, "dry_run": str(dry_run).lower()},
        idempotency_key=key,
    )
    _emit(status, payload, output=output)


@trash_group_cli.command("purge")
@click.argument("trash_id")
@click.option("--slug", required=True)
@click.option("--yes", is_flag=True, default=False, help="Skip the interactive confirmation prompt.")
@click.option("--idempotency-key", default=None)
@_common_options
@click.pass_context
def trash_purge(
    ctx: click.Context,
    trash_id: str,
    slug: str,
    yes: bool,
    idempotency_key: str | None,
    output: str,
) -> None:
    _confirm_destructive(
        f"permanently purge trash entry {trash_id}",
        slug=slug,
        yes=yes,
        extra="Purge is IRREVERSIBLE — the soft-deleted rows will be gone.",
    )
    key = idempotency_key or f"cli-{uuid.uuid4().hex}"
    status, payload = _client(ctx).call(
        "POST",
        f"/trash/{trash_id}/purge",
        params={"slug": slug},
        idempotency_key=key,
    )
    _emit(status, payload, output=output)


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------


@admin.group(name="audit")
def audit_group() -> None:
    """Read the admin plane audit log."""


@audit_group.command("list")
@click.option("--slug", required=True)
@click.option("--module", default=None)
@click.option("--limit", default=50, type=int)
@click.option("--status-gte", default=None, type=int)
@_common_options
@click.pass_context
def audit_list(
    ctx: click.Context,
    slug: str,
    module: str | None,
    limit: int,
    status_gte: int | None,
    output: str,
) -> None:
    status, payload = _client(ctx).call(
        "GET",
        "/audit",
        params={
            "slug": slug,
            "module": module,
            "limit": limit,
            "status_gte": status_gte,
        },
    )
    _emit(status, payload, output=output)


@audit_group.command("stats")
@click.option("--slug", required=True)
@_common_options
@click.pass_context
def audit_stats(ctx: click.Context, slug: str, output: str) -> None:
    status, payload = _client(ctx).call("GET", "/audit/stats", params={"slug": slug})
    _emit(status, payload, output=output)


@audit_group.command("tail")
@click.option("--slug", required=True)
@click.option("--module", default=None, help="Filter by module name (e.g. reconciler).")
@click.option("--limit", default=50, type=int)
@_common_options
@click.pass_context
def audit_tail(
    ctx: click.Context,
    slug: str,
    module: str | None,
    limit: int,
    output: str,
) -> None:
    """Show the N most recent audit rows.

    For a live follow, pipe through ``watch``::

        watch -n 2 'mdb-engine admin audit tail --slug demo'
    """
    status, payload = _client(ctx).call(
        "GET",
        "/audit/recent",
        params={"slug": slug, "module": module, "limit": limit},
    )
    _emit(status, payload, output=output)


# ---------------------------------------------------------------------------
# secrets
# ---------------------------------------------------------------------------


@admin.group(name="secrets")
def secrets_group() -> None:
    """Per-app token management."""


@secrets_group.command("current")
@click.option("--slug", required=True)
@_common_options
@click.pass_context
def secrets_current(ctx: click.Context, slug: str, output: str) -> None:
    """Show label / scopes / rotation count of the current token."""
    status, payload = _client(ctx).call("GET", "/secrets/current", params={"slug": slug})
    _emit(status, payload, output=output)


@secrets_group.command("rotate")
@click.option("--slug", required=True)
@click.option("--label", default=None, help="Human-facing label for the new token.")
@click.option(
    "--scope", "scopes", multiple=True, help="Scope for the new token (repeatable). Example: --scope reconciler:read."
)
@click.option(
    "--overlap-seconds",
    type=click.IntRange(0, 3600),
    default=0,
    show_default=True,
    help=(
        "Grace period during which the PREVIOUS token remains valid. "
        "Use when rolling credentials across a fleet to avoid a 401 storm. "
        "Capped at 3600s."
    ),
)
@click.option("--idempotency-key", default=None)
@_common_options
@click.pass_context
def secrets_rotate(
    ctx: click.Context,
    slug: str,
    label: str | None,
    scopes: tuple[str, ...],
    overlap_seconds: int,
    idempotency_key: str | None,
    output: str,
) -> None:
    key = idempotency_key or f"cli-{uuid.uuid4().hex}"
    body: dict[str, Any] = {}
    if label:
        body["label"] = label
    if scopes:
        body["scopes"] = list(scopes)
    if overlap_seconds:
        body["overlap_seconds"] = overlap_seconds
    status, payload = _client(ctx).call(
        "POST",
        "/secrets/rotate",
        params={"slug": slug},
        body=body if body else None,
        idempotency_key=key,
    )
    _emit(status, payload, output=output)


@secrets_group.command("bootstrap")
@click.option("--slug", required=True)
@click.option(
    "--scope",
    "scopes",
    multiple=True,
    default=("*",),
    show_default=True,
    help="Scope for the new token (repeatable). Default: '*' (full admin).",
)
@click.option("--label", default="bootstrap", show_default=True, help="Human-facing label for the new token.")
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help=(
        "Overwrite an existing secret. Revokes the current token "
        "immediately with no overlap window — prefer "
        "'admin secrets rotate --overlap-seconds=...' for live apps."
    ),
)
@click.option(
    "--output",
    type=click.Choice(OUTPUT_FORMATS),
    default="table",
    show_default=True,
)
def secrets_bootstrap(
    slug: str,
    scopes: tuple[str, ...],
    label: str,
    force: bool,
    output: str,
) -> None:
    """Mint the FIRST-ever admin token for an app.

    Chicken-and-egg solution: every other ``admin`` subcommand needs a
    valid token, but the first token has to come from somewhere. This
    command talks to Mongo **directly** (no HTTP) using the same
    ``MONGODB_URI`` / ``MDB_DB_NAME`` / master-key env vars the engine
    process uses, then prints the plaintext token **exactly once**.

    Refuses to run if a secret already exists. Pass ``--force`` to
    overwrite — but prefer ``admin secrets rotate --overlap-seconds``
    for any live app so the existing fleet can roll without a 401 storm.

    Examples::

        mdb-engine admin secrets bootstrap --slug demo
        mdb-engine admin secrets bootstrap --slug ci --scope reconciler:read --scope trash:read
    """
    import asyncio

    async def _run() -> dict[str, Any]:
        from ...core.engine import MongoDBEngine

        engine = MongoDBEngine()
        await engine.initialize()
        try:
            mgr = getattr(engine, "_app_secrets_manager", None)
            if mgr is None:
                raise click.ClickException("engine has no app secrets manager — ensure MDB_MASTER_KEY is set")
            exists = await mgr.app_secret_exists(slug)
            if exists and not force:
                raise click.ClickException(
                    f"app '{slug}' already has a secret; use 'admin secrets rotate' "
                    "(or pass --force to bootstrap anyway)."
                )

            import secrets as _secrets

            new_token = _secrets.token_urlsafe(32)
            await mgr.store_app_secret(
                slug,
                new_token,
                scopes=list(scopes),
                label=label,
            )
            fingerprint_fn = getattr(mgr, "fingerprint", None)
            token_id = fingerprint_fn(new_token) if callable(fingerprint_fn) else None
            return {
                "slug": slug,
                "bootstrapped": True,
                "rotated": bool(exists),
                "token": new_token,
                "token_id": token_id,
                "label": label,
                "scopes": list(scopes),
                "notice": "Store this token immediately — it cannot be retrieved again.",
            }
        finally:
            await engine.shutdown()

    try:  # nosemgrep
        payload = asyncio.run(_run())
    except click.ClickException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(f"bootstrap failed: {exc}") from exc
    _emit(201 if payload.get("rotated") is False else 200, payload, output=output)


__all__ = ["admin"]
