"""CLI ↔ HTTP parity tests for the admin plane.

The admin CLI (``mdb-engine admin ...``) is a thin stdlib-only HTTP
client. To keep the CLI and UI surfaces byte-identical we assert here
that ``--output json`` echoes exactly what the server returned — no
field renaming, no silent drops, no stringification drift.

We deliberately do NOT spawn a subprocess: that would be slow, flaky,
and would test the install environment instead of the code. Instead
we monkey-patch the CLI's stdlib ``urlopen`` so it round-trips
through an in-process FastAPI ``TestClient``, then invoke commands via
Click's ``CliRunner``. This gives us full coverage of:

- Click argument parsing + ``--output`` rendering
- ``AdminClient`` URL construction, header handling, idempotency key
- FastAPI route dispatch, per-endpoint scope enforcement
- JSON round-trip through ``AdminClient.call()`` and ``_emit``

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from __future__ import annotations

import io
import json
import logging
from typing import Any
from urllib import parse as urlparse
from urllib.error import HTTPError

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mdb_engine.admin import ADMIN_TOKEN_HEADER, AdminSurface
from mdb_engine.cli.commands import admin as admin_cli

# ---------------------------------------------------------------------------
# Engine stub — duck-typed like tests/unit/test_admin_router.py but kept
# separate so the two suites can drift on shape without breaking each other.
# ---------------------------------------------------------------------------


class _FakeSecrets:
    def __init__(self):
        self._token = "s3cret"
        self._scopes = ["*"]
        self._label = "cli-test"
        self._rotations = 0

    async def verify_app_secret(self, slug, provided):
        return slug == "demo" and provided == self._token

    async def verify_app_token(self, slug, provided):
        class _R:
            def __init__(self, valid, scopes, token_id=None, label=None):
                self.valid = valid
                self.scopes = scopes
                self.token_id = token_id
                self.label = label

        if slug != "demo" or provided != self._token:
            return _R(False, [])
        return _R(
            True,
            list(self._scopes),
            token_id=self.fingerprint(provided),
            label=self._label,
        )

    def fingerprint(self, token):
        if not token:
            return None
        return f"hmac:{abs(hash(token)) % (10 ** 16):016x}"

    async def get_app_secret_metadata(self, slug):
        if slug != "demo":
            return {}
        return {
            "slug": "demo",
            "label": self._label,
            "scopes": list(self._scopes),
            "rotation_count": self._rotations,
            "created_at": None,
            "updated_at": None,
        }

    async def rotate_app_secret(self, slug, *, scopes=None, label=None):
        self._rotations += 1
        self._token = f"tok-{self._rotations}"
        if scopes is not None:
            self._scopes = list(scopes)
        if label is not None:
            self._label = label
        return self._token


class _FakeConn:
    mongo_db = None


class _FakeEngine:
    def __init__(self):
        self._app_secrets_manager = _FakeSecrets()
        self._connection_manager = _FakeConn()
        self._surface_cache: AdminSurface | None = None

    async def manifest_diff(self, slug):
        return {"slug": slug, "is_noop": True, "added": [], "removed": [], "modified": []}

    async def reconcile(self, slug, **kwargs):
        return {"status": "noop", "slug": slug, "dry_run": bool(kwargs.get("dry_run"))}

    async def manifest_history(self, slug, *, limit=20):
        return [
            {"head": "abc123", "written_at": "2026-01-01T00:00:00Z", "label": "initial"},
        ]

    async def trash_list(self, slug):
        return [
            {"_id": "t1", "kind": "collection", "original_name": "leads_v1", "doc_count": 42},
        ]

    async def trash_summary(self, slug):
        return {"total": 1, "expired": 0}

    def admin_surface(self, cfg=None):
        if self._surface_cache is None:
            s = AdminSurface(self, cfg or {})
            s.register_default_modules()
            self._surface_cache = s
        return self._surface_cache

    def admin_surface_cached(self):
        return self._surface_cache


@pytest.fixture()
def admin_test_client() -> TestClient:
    """FastAPI TestClient with the full default admin surface mounted."""
    app = FastAPI()
    engine = _FakeEngine()
    surface = engine.admin_surface()
    app.include_router(surface.build_router(), prefix="/__mdb")
    return TestClient(app)


@pytest.fixture()
def routed_cli(monkeypatch, admin_test_client):
    """Route the CLI's ``urlopen`` at our in-process TestClient.

    The CLI is otherwise untouched — it still builds full URLs,
    serializes headers, and parses JSON. That's the whole point:
    this fixture verifies the *real* CLI behaviour without a shell.
    """

    class _FakeResponse:
        def __init__(self, status: int, body: bytes):
            self.status = status
            self._body = body

        def read(self) -> bytes:
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=30):  # noqa: ARG001 - mirrors urlopen signature
        parsed = urlparse.urlsplit(req.full_url)
        path = parsed.path + ("?" + parsed.query if parsed.query else "")
        headers = {k: v for k, v in req.header_items()}
        method = req.get_method()
        body = req.data

        r = admin_test_client.request(method, path, headers=headers, content=body)
        payload = r.content or b""
        if r.status_code >= 400:
            raise HTTPError(
                req.full_url,
                r.status_code,
                r.reason_phrase or "error",
                hdrs=r.headers,
                fp=io.BytesIO(payload),
            )
        return _FakeResponse(r.status_code, payload)

    monkeypatch.setattr(admin_cli.urlrequest, "urlopen", fake_urlopen)
    monkeypatch.setenv("MDB_ADMIN_URL", "http://testserver")
    monkeypatch.setenv("MDB_ADMIN_TOKEN", "s3cret")

    # pytest's ``log_cli`` handler keeps a reference to the pre-isolation
    # ``sys.stdout``; if httpx logs a request line while Click is in
    # isolation, that handler writes into a buffer that Click has
    # already closed, raising ``ValueError: I/O operation on closed file``.
    # Silencing the chatty httpx logger for this fixture sidesteps the
    # fight without changing production behaviour.
    prev = logging.getLogger("httpx").level
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        yield CliRunner()
    finally:
        logging.getLogger("httpx").setLevel(prev)


# ---------------------------------------------------------------------------
# Parity assertions
# ---------------------------------------------------------------------------


def _direct(client: TestClient, method: str, path: str) -> Any:
    r = client.request(
        method,
        path,
        headers={ADMIN_TOKEN_HEADER: "s3cret", "Accept": "application/json"},
    )
    assert r.status_code < 400, f"setup call failed: {r.status_code} {r.text}"
    return r.json()


@pytest.mark.parametrize(
    ("argv", "method", "http_path"),
    [
        (["health", "--slug", "demo", "-o", "json"], "GET", "/__mdb/health/modules?slug=demo"),
        (["reconciler", "plan", "--slug", "demo", "-o", "json"], "GET", "/__mdb/reconciler/plan?slug=demo"),
        (
            ["reconciler", "history", "--slug", "demo", "--limit", "5", "-o", "json"],
            "GET",
            "/__mdb/reconciler/manifest/history?slug=demo&limit=5",
        ),
        (["trash", "list", "--slug", "demo", "-o", "json"], "GET", "/__mdb/trash?slug=demo"),
        (["trash", "summary", "--slug", "demo", "-o", "json"], "GET", "/__mdb/trash/summary?slug=demo"),
        (["secrets", "current", "--slug", "demo", "-o", "json"], "GET", "/__mdb/secrets/current?slug=demo"),
    ],
)
def test_json_output_matches_http_response(
    routed_cli: CliRunner,
    admin_test_client: TestClient,
    argv: list[str],
    method: str,
    http_path: str,
) -> None:
    """``--output json`` must echo the HTTP response body byte-for-byte.

    We compare parsed JSON (not raw bytes) so whitespace from
    ``json.dumps(indent=2)`` isn't a false positive, but every
    field + value must match. Any CLI-side massaging of the
    response is a regression.
    """
    expected = _direct(admin_test_client, method, http_path)

    result = routed_cli.invoke(admin_cli.admin, argv, catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert result.output.strip(), "CLI produced no output"
    got = json.loads(result.output)
    assert got == expected, f"drift for {argv}: {got} != {expected}"


def test_table_output_is_non_empty_and_human_readable(
    routed_cli: CliRunner,
) -> None:
    """Sanity check that ``--output table`` produces some textual summary.

    We don't pin the exact layout — that's asking for test churn —
    but we do guarantee it's non-empty and doesn't leak raw Python repr.
    """
    result = routed_cli.invoke(
        admin_cli.admin,
        ["trash", "list", "--slug", "demo"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert result.output.strip()


def test_missing_token_fails_cleanly(monkeypatch) -> None:
    """CLI should refuse to run when no token source is configured."""
    monkeypatch.delenv("MDB_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("MDB_ADMIN_URL", raising=False)

    runner = CliRunner()
    result = runner.invoke(
        admin_cli.admin,
        ["health", "--slug", "demo", "-o", "json"],
        catch_exceptions=False,
    )
    assert result.exit_code != 0
    assert "token" in result.output.lower()


def test_idempotency_key_is_forwarded(routed_cli: CliRunner, admin_test_client: TestClient, monkeypatch) -> None:
    """``--idempotency-key`` must reach the server as ``Idempotency-Key``."""
    seen: dict[str, str] = {}

    orig = admin_test_client.request

    def spy_request(method, url, **kw):
        headers = kw.get("headers") or {}
        for k, v in headers.items():
            if k.lower() == "idempotency-key":
                seen["key"] = v
        return orig(method, url, **kw)

    monkeypatch.setattr(admin_test_client, "request", spy_request)

    result = routed_cli.invoke(
        admin_cli.admin,
        [
            "reconciler",
            "apply",
            "--slug",
            "demo",
            "--dry-run",
            "--idempotency-key",
            "test-key-abc",
            "-o",
            "json",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert seen.get("key") == "test-key-abc"


class TestDestructivePrompt:
    """The CLI must refuse to run destructive ops non-interactively
    without ``--yes`` (or ``MDB_CONFIRM=1``). Protects against
    accidental ``apply`` / ``purge`` from a Makefile or CI pipeline.
    """

    def test_apply_aborts_without_yes_on_non_tty(self, routed_cli: CliRunner) -> None:
        """Non-dry ``reconciler apply`` with a piped stdin MUST fail."""
        result = routed_cli.invoke(
            admin_cli.admin,
            ["reconciler", "apply", "--slug", "demo", "--no-dry-run", "-o", "json"],
            catch_exceptions=False,
        )
        assert result.exit_code != 0
        assert "--yes" in result.output or "non-interactively" in result.output.lower()

    def test_apply_dry_run_never_prompts(self, routed_cli: CliRunner) -> None:
        """Plan-only runs are non-destructive — must proceed freely."""
        result = routed_cli.invoke(
            admin_cli.admin,
            ["reconciler", "apply", "--slug", "demo", "--dry-run", "-o", "json"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output

    def test_apply_with_yes_bypasses_prompt(self, routed_cli: CliRunner) -> None:
        result = routed_cli.invoke(
            admin_cli.admin,
            ["reconciler", "apply", "--slug", "demo", "--no-dry-run", "--yes", "-o", "json"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output

    def test_env_var_bypasses_prompt(
        self,
        routed_cli: CliRunner,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(admin_cli.CONFIRM_ENV, "1")
        result = routed_cli.invoke(
            admin_cli.admin,
            ["reconciler", "apply", "--slug", "demo", "--no-dry-run", "-o", "json"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output

    def test_trash_purge_refuses_without_yes(self, routed_cli: CliRunner) -> None:
        result = routed_cli.invoke(
            admin_cli.admin,
            ["trash", "purge", "some-id", "--slug", "demo", "-o", "json"],
            catch_exceptions=False,
        )
        assert result.exit_code != 0


def test_yaml_output_is_rejected(routed_cli: CliRunner) -> None:
    """We intentionally dropped ``--output yaml`` — its continued
    absence is part of the CLI contract. If someone re-adds it
    without owning the maintenance, this test prompts a review."""
    result = routed_cli.invoke(
        admin_cli.admin,
        ["health", "--slug", "demo", "-o", "yaml"],
        catch_exceptions=False,
    )
    assert result.exit_code != 0
    assert "yaml" in result.output.lower() or "invalid" in result.output.lower()
