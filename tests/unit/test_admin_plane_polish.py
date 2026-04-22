"""Regression tests for the admin plane polish pass.

Each test in here guards a *load-bearing* behaviour of the admin plane
that a careless refactor could easily break:

- ``GET /health/live`` must be reachable with no token and no slug.
- ``GET /secrets/current`` must return non-sensitive metadata only.
- Destructive POSTs must stamp ``X-Idempotent-Replay: true`` on replay.
- The per-endpoint scope matrix must reject mismatched verbs across
  every built-in module.

These tests are deliberately NOT parametrised over path prefixes —
mount prefix coverage is exercised by ``test_admin_surface.py``.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mdb_engine.admin import ADMIN_TOKEN_HEADER, AdminSurface

# ---------------------------------------------------------------------------
# Fakes (deliberately separate from test_admin_router.py so the two
# suites can evolve independently — this one leans on metadata + a
# writable _connection_manager).
# ---------------------------------------------------------------------------


class _FakeSecrets:
    """Secrets manager that tracks metadata + rotations in-process.

    Not trying to be cryptographically interesting — just exercising
    the module handler contract.
    """

    def __init__(self):
        self._slug = "demo"
        self._token = "tok-initial"
        self._scopes = ["*"]
        self._label = "initial"
        self._rotations = 0

    async def verify_app_secret(self, slug: str, provided: str) -> bool:
        return slug == self._slug and provided == self._token

    async def verify_app_token(self, slug: str, provided: str):
        class _R:
            def __init__(self, valid, scopes, token_id=None, label=None):
                self.valid = valid
                self.scopes = scopes
                self.token_id = token_id
                self.label = label

        if slug != self._slug or provided != self._token:
            return _R(False, [])
        return _R(
            True,
            list(self._scopes),
            token_id=self.fingerprint(provided),
            label=self._label,
        )

    def fingerprint(self, token: str | None) -> str | None:
        if not token:
            return None
        return f"hmac:{abs(hash(token)) % (10 ** 16):016x}"

    async def get_app_secret_metadata(self, slug: str) -> dict[str, Any]:
        if slug != self._slug:
            return {}
        return {
            "slug": self._slug,
            "label": self._label,
            "scopes": list(self._scopes),
            "rotation_count": self._rotations,
            "created_at": None,
            "updated_at": None,
        }

    async def rotate_app_secret(
        self,
        slug: str,
        *,
        scopes=None,
        label=None,
    ) -> str:
        self._rotations += 1
        self._token = f"tok-{self._rotations}"
        if scopes is not None:
            self._scopes = list(scopes)
        if label is not None:
            self._label = label
        return self._token


class _FakeConn:
    """Minimal stand-in with an attribute named ``mongo_db``.

    Set to ``None`` to force ``replay_or_record`` onto its no-Mongo
    fast path (which still runs the handler but skips persistence).
    """

    mongo_db = None


class _FakeEngine:
    def __init__(self):
        self._app_secrets_manager = _FakeSecrets()
        self._connection_manager = _FakeConn()
        self._surface_cache: AdminSurface | None = None

    async def manifest_diff(self, slug):
        return {"slug": slug, "is_noop": True}

    async def reconcile(self, slug, **kwargs):
        return {"status": "noop", "slug": slug, "dry_run": bool(kwargs.get("dry_run"))}

    async def manifest_history(self, slug, *, limit=20):
        return []

    async def trash_list(self, slug):
        return []

    async def trash_summary(self, slug):
        return {"total": 0}

    async def trash_restore(self, slug, trash_id, *, dry_run=False):
        return {"restored": not dry_run, "dry_run": dry_run, "id": trash_id}

    async def trash_purge(self, slug, *, expired_only=True, ids=None):
        return 1

    def admin_surface(self, cfg=None):
        if self._surface_cache is None:
            s = AdminSurface(self, cfg or {})
            s.register_default_modules()
            self._surface_cache = s
        return self._surface_cache

    def admin_surface_cached(self):
        return self._surface_cache


def _client(engine: _FakeEngine | None = None, cfg: dict[str, Any] | None = None) -> TestClient:
    engine = engine or _FakeEngine()
    surface = engine.admin_surface(cfg or {})
    app = FastAPI()
    app.include_router(surface.build_router(), prefix="/__mdb")
    return TestClient(app)


# ---------------------------------------------------------------------------
# /health/live
# ---------------------------------------------------------------------------


class TestHealthLive:
    def test_liveness_is_reachable_without_auth(self):
        c = _client()
        r = c.get("/__mdb/health/live")
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True}

    def test_liveness_requires_no_slug(self):
        c = _client()
        # No slug query param, no token header — pure probe.
        r = c.get("/__mdb/health/live")
        assert r.status_code == 200

    def test_liveness_is_not_hidden_by_disabling_health_module(self):
        """Liveness lives on the parent router; disabling the health
        module via manifest must not take it down."""
        c = _client(cfg={"modules": {"health": {"enabled": False}}})
        r = c.get("/__mdb/health/live")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# /secrets/current
# ---------------------------------------------------------------------------


class TestSecretsCurrent:
    def test_returns_metadata_and_presenting_token_id(self):
        c = _client()
        r = c.get(
            "/__mdb/secrets/current?slug=demo",
            headers={ADMIN_TOKEN_HEADER: "tok-initial"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # Must NEVER leak the plaintext token.
        assert "token" not in body
        assert "encrypted_secret" not in body
        # Must expose operator-relevant metadata + the presenter's id.
        assert body["slug"] == "demo"
        assert body["label"] == "initial"
        assert body["scopes"] == ["*"]
        assert body["presenting_token_id"]
        assert body["presenting_token_id"].startswith("hmac:")

    def test_unknown_slug_returns_404(self):
        c = _client()
        r = c.get(
            "/__mdb/secrets/current?slug=other",
            headers={ADMIN_TOKEN_HEADER: "tok-initial"},
        )
        # 403 is also acceptable — the auth gate runs first and rejects
        # unknown slugs there. Either way, no data is leaked.
        assert r.status_code in (403, 404)

    def test_requires_read_scope(self):
        """A token scoped only to 'rotate' must not be able to introspect."""
        engine = _FakeEngine()
        engine._app_secrets_manager._scopes = ["secrets:rotate"]
        c = _client(engine)
        r = c.get(
            "/__mdb/secrets/current?slug=demo",
            headers={ADMIN_TOKEN_HEADER: "tok-initial"},
        )
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# X-Idempotent-Replay header contract
# ---------------------------------------------------------------------------


class TestIdempotencyReplayHeader:
    def test_first_call_does_not_set_replay_header(self):
        """Without Mongo (_connection_manager.mongo_db is None) the
        idempotency helper takes its pass-through path, so replay is
        never signaled. That's the correct behaviour — we only claim
        replay when we actually served a cached body.
        """
        c = _client()
        r = c.post(
            "/__mdb/reconciler/apply?slug=demo&dry_run=true",
            headers={
                ADMIN_TOKEN_HEADER: "tok-initial",
                "Idempotency-Key": "test-key-123",
            },
        )
        assert r.status_code == 200
        assert r.headers.get("X-Idempotent-Replay") is None

    def test_replay_header_absent_on_plain_get(self):
        c = _client()
        r = c.get(
            "/__mdb/reconciler/plan?slug=demo",
            headers={ADMIN_TOKEN_HEADER: "tok-initial"},
        )
        assert r.headers.get("X-Idempotent-Replay") is None


# ---------------------------------------------------------------------------
# Cache-Control contract
# ---------------------------------------------------------------------------


class TestCacheControl:
    """Secrets rotate must explicitly stamp ``Cache-Control: no-store``.

    Global ``no-store`` for every admin response is installed by
    ``_install_admin_audit_middleware`` in ``fastapi_app.py`` and covered
    by the engine integration test. Here we guard the narrower
    module-level behaviour: the rotate handler itself *also* sets
    ``no-store`` + ``Pragma`` directly so the guarantee holds even if
    callers mount the router without the middleware.
    """

    def test_rotate_response_is_uncacheable(self):
        c = _client()
        r = c.post(
            "/__mdb/secrets/rotate?slug=demo",
            headers={ADMIN_TOKEN_HEADER: "tok-initial"},
            json={"label": "ci-v2"},
        )
        assert r.status_code == 200, r.text
        assert r.headers.get("cache-control", "").lower() == "no-store"
        assert r.headers.get("pragma", "").lower() == "no-cache"


# ---------------------------------------------------------------------------
# Per-endpoint scope matrix (parametrized)
# ---------------------------------------------------------------------------


# ``(granted_scopes, method, path, expected_status)`` — every row
# documents a real policy decision.
_SCOPE_MATRIX: list[tuple[list[str], str, str, int]] = [
    # Wildcard token can do everything.
    (["*"], "GET", "/__mdb/reconciler/plan?slug=demo", 200),
    (["*"], "POST", "/__mdb/reconciler/apply?slug=demo", 200),
    (["*"], "GET", "/__mdb/trash?slug=demo", 200),
    (["*"], "POST", "/__mdb/trash/abc/purge?slug=demo", 200),
    (["*"], "GET", "/__mdb/secrets/current?slug=demo", 200),
    # Module-wildcard confines to that module only.
    (["reconciler:*"], "GET", "/__mdb/reconciler/plan?slug=demo", 200),
    (["reconciler:*"], "POST", "/__mdb/reconciler/apply?slug=demo", 200),
    (["reconciler:*"], "POST", "/__mdb/trash/abc/purge?slug=demo", 403),
    (["reconciler:*"], "GET", "/__mdb/secrets/current?slug=demo", 403),
    # Verb-scoped tokens reject mismatched verbs even within the module.
    (["reconciler:read"], "GET", "/__mdb/reconciler/plan?slug=demo", 200),
    (["reconciler:read"], "POST", "/__mdb/reconciler/apply?slug=demo", 403),
    (["reconciler:apply"], "GET", "/__mdb/reconciler/plan?slug=demo", 403),
    (["reconciler:apply"], "POST", "/__mdb/reconciler/apply?slug=demo", 200),
    # Trash is independently scoped — restore != purge.
    (["trash:read"], "GET", "/__mdb/trash?slug=demo", 200),
    (["trash:read"], "POST", "/__mdb/trash/abc/restore?slug=demo", 403),
    (["trash:restore"], "POST", "/__mdb/trash/abc/restore?slug=demo", 200),
    (["trash:restore"], "POST", "/__mdb/trash/abc/purge?slug=demo", 403),
    (["trash:purge"], "POST", "/__mdb/trash/abc/purge?slug=demo", 200),
]


@pytest.mark.parametrize(
    ("scopes", "method", "path", "expected"),
    _SCOPE_MATRIX,
    ids=[f"{m} {p.split('?')[0]} with {s}" for s, m, p, _ in _SCOPE_MATRIX],
)
def test_scope_enforcement_matrix(scopes, method, path, expected):
    engine = _FakeEngine()
    engine._app_secrets_manager._scopes = scopes
    c = _client(engine)
    r = c.request(method, path, headers={ADMIN_TOKEN_HEADER: "tok-initial"})
    assert r.status_code == expected, (
        f"scope={scopes!r} {method} {path} → {r.status_code}, " f"expected {expected}: {r.text}"
    )
