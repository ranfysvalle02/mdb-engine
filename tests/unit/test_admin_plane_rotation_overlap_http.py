"""End-to-end HTTP test for graceful secret rotation over the admin plane.

The contract — both sides of the wire — is:

1. ``POST /__mdb/secrets/rotate`` with ``{"overlap_seconds": N}`` returns
   a fresh plaintext token *and* keeps the previous token valid for
   ``N`` seconds.
2. During the overlap window, **both** tokens authenticate against the
   admin auth gate and pass scope checks.
3. After the window elapses the previous token produces a 401 while
   the rotated token continues to work.

Two gaps motivate this file:

- ``tests/unit/test_app_secrets_rotation_overlap.py`` covers the
  storage layer but never touches HTTP, so middleware / auth-gate
  regressions would slip through.
- The boot tests use a fake ``_FakeSecrets`` that doesn't model
  overlap at all.

This test exercises the **real** :class:`AppSecretsManager` behind a
real :class:`AdminSurface`, backed by in-memory Mongo stubs so no
container is required.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from __future__ import annotations

import base64
import copy
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mdb_engine.admin import ADMIN_TOKEN_HEADER, AdminSurface
from mdb_engine.core.app_secrets import AppSecretsManager
from mdb_engine.core.encryption import EnvelopeEncryptionService
from mdb_engine.core.fastapi_app import FastAPIAppMixin

# ---------------------------------------------------------------------------
# Mongo stubs
# ---------------------------------------------------------------------------


class _InMemoryCollection:
    """Faithful replay of the ops ``AppSecretsManager`` uses."""

    def __init__(self):
        self._docs: dict[str, dict[str, Any]] = {}

    async def find_one(self, filt, projection=None):
        doc = self._docs.get(filt.get("_id"))
        return copy.deepcopy(doc) if doc else None

    async def replace_one(self, filt, doc):
        self._docs[filt["_id"]] = copy.deepcopy(doc)

    async def insert_one(self, doc):
        self._docs[doc["_id"]] = copy.deepcopy(doc)

    async def update_one(self, filt, update):
        doc = self._docs.get(filt["_id"])
        if doc is None:
            return
        if "$set" in update:
            doc.update(copy.deepcopy(update["$set"]))
        if "$unset" in update:
            for key in update["$unset"]:
                doc.pop(key, None)


class _InMemoryDB:
    def __init__(self, coll: _InMemoryCollection):
        self._coll = coll

    def __getitem__(self, _name: str) -> _InMemoryCollection:
        return self._coll


class _FakeConn:
    """Stand-in so the idempotency layer takes its no-Mongo fast path."""

    mongo_db = None


# ---------------------------------------------------------------------------
# Engine harness
# ---------------------------------------------------------------------------


class _RotationEngine(FastAPIAppMixin):
    """Lightweight engine that plugs a **real** :class:`AppSecretsManager`
    into the same wiring methods the production factory uses."""

    def __init__(self, manager: AppSecretsManager):
        self._connection_manager = _FakeConn()  # type: ignore[assignment]
        self._app_secrets_manager = manager
        self._admin_surfaces: dict[str, AdminSurface] = {}

    def admin_surface(self, cfg: dict[str, Any] | None = None) -> AdminSurface:
        key = str((cfg or {}).get("path_prefix") or "/__mdb")
        if key in self._admin_surfaces:
            return self._admin_surfaces[key]
        s = AdminSurface(self, cfg or {})
        s.register_default_modules()
        self._admin_surfaces[key] = s
        return s

    def admin_surface_cached(self) -> AdminSurface | None:
        return next(iter(self._admin_surfaces.values()), None)

    async def manifest_diff(self, slug):  # pragma: no cover - unused here
        return {"slug": slug, "is_noop": True}

    async def reconcile(self, slug, **kw):  # pragma: no cover - unused here
        return {"status": "noop", "slug": slug, "dry_run": bool(kw.get("dry_run"))}

    async def manifest_history(self, slug, *, limit=20):  # pragma: no cover
        return []

    async def trash_list(self, slug):  # pragma: no cover
        return []

    async def trash_summary(self, slug):  # pragma: no cover
        return {"total": 0}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def harness():
    """Return ``(client, manager, coll, prefix)`` with "demo" slug seeded."""
    master = EnvelopeEncryptionService.generate_master_key()
    enc = EnvelopeEncryptionService(base64.b64decode(master.encode()))
    coll = _InMemoryCollection()
    mgr = AppSecretsManager(_InMemoryDB(coll), enc)
    engine = _RotationEngine(mgr)
    cfg = {"enabled": True, "path_prefix": "/__mdb"}
    prefix = cfg["path_prefix"]
    app = FastAPI()
    surface = engine.admin_surface(cfg)
    app.include_router(surface.build_router(), prefix=prefix)
    engine._install_admin_audit_middleware(app, surface, prefix)
    engine._install_admin_rate_limit(app, prefix, cfg)
    client = TestClient(app)
    return client, mgr, coll, prefix


async def _seed_initial_token(mgr: AppSecretsManager) -> str:
    """Mint the first-ever token for ``demo`` with wildcard scope.

    ``store_app_secret`` takes a caller-supplied plaintext, so we
    generate one here using the same primitive the manager itself
    uses during rotation.
    """
    import secrets as _stdlib_secrets

    plaintext = _stdlib_secrets.token_urlsafe(32)
    await mgr.store_app_secret("demo", plaintext, scopes=["*"], label="v1")
    return plaintext


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRotationOverlapOverHttp:
    """Both tokens authenticate during the overlap window."""

    async def test_both_tokens_work_inside_overlap_window(self, harness):
        client, mgr, _, _ = harness
        old_token = await _seed_initial_token(mgr)

        # 1. The seeded token works.
        r = client.get(
            "/__mdb/health/modules?slug=demo",
            headers={ADMIN_TOKEN_HEADER: old_token},
        )
        assert r.status_code == 200

        # 2. Rotate with a generous overlap.
        r = client.post(
            "/__mdb/secrets/rotate?slug=demo",
            headers={ADMIN_TOKEN_HEADER: old_token},
            json={"overlap_seconds": 300, "scopes": ["*"], "label": "v2"},
        )
        assert r.status_code == 200, r.text
        payload = r.json()
        assert payload["rotated"] is True
        new_token = payload["token"]
        assert new_token and new_token != old_token
        assert payload["overlap_seconds"] == 300
        assert payload["previous_expires_at"] is not None

        # The rotation response must never be cached — we just shipped
        # a plaintext token over the wire.
        assert r.headers.get("cache-control", "").lower() == "no-store"

        # 3. BOTH tokens authenticate inside the window.
        r_old = client.get(
            "/__mdb/health/modules?slug=demo",
            headers={ADMIN_TOKEN_HEADER: old_token},
        )
        assert r_old.status_code == 200, (
            "previous token must stay valid during overlap " f"(got {r_old.status_code}: {r_old.text})"
        )
        r_new = client.get(
            "/__mdb/health/modules?slug=demo",
            headers={ADMIN_TOKEN_HEADER: new_token},
        )
        assert r_new.status_code == 200

    async def test_previous_token_dies_when_overlap_expires(self, harness):
        client, mgr, coll, _ = harness
        old_token = await _seed_initial_token(mgr)

        r = client.post(
            "/__mdb/secrets/rotate?slug=demo",
            headers={ADMIN_TOKEN_HEADER: old_token},
            json={"overlap_seconds": 60, "scopes": ["*"], "label": "v2"},
        )
        assert r.status_code == 200, r.text
        new_token = r.json()["token"]

        # Expire the overlap by rewriting previous_expires_at into the
        # past. This is semantically identical to waiting 61 seconds
        # but avoids adding a 60s sleep to the test suite.
        doc = coll._docs["demo"]
        doc["previous_expires_at"] = datetime.now(tz=timezone.utc) - timedelta(seconds=1)

        # 1. The old token is now rejected.
        # 403 is the admin auth gate's response for "bad token"; 401 is
        # reserved for "missing token". Either is a valid rejection.
        r_old = client.get(
            "/__mdb/health/modules?slug=demo",
            headers={ADMIN_TOKEN_HEADER: old_token},
        )
        assert r_old.status_code in (401, 403), (
            "previous token must be rejected after the overlap window expires "
            f"(got {r_old.status_code}: {r_old.text})"
        )

        # 2. The new token continues to work — rotation didn't nuke
        # the primary slot.
        r_new = client.get(
            "/__mdb/health/modules?slug=demo",
            headers={ADMIN_TOKEN_HEADER: new_token},
        )
        assert r_new.status_code == 200

    async def test_zero_overlap_matches_legacy_immediate_revocation(self, harness):
        client, mgr, _, _ = harness
        old_token = await _seed_initial_token(mgr)

        r = client.post(
            "/__mdb/secrets/rotate?slug=demo",
            headers={ADMIN_TOKEN_HEADER: old_token},
            json={"overlap_seconds": 0, "scopes": ["*"], "label": "v2"},
        )
        assert r.status_code == 200, r.text
        payload = r.json()
        new_token = payload["token"]
        assert payload["overlap_seconds"] == 0
        assert payload.get("previous_expires_at") is None

        # Old token should be instantly dead — same contract the
        # storage-level test asserts, but over real HTTP.
        r_old = client.get(
            "/__mdb/health/modules?slug=demo",
            headers={ADMIN_TOKEN_HEADER: old_token},
        )
        assert r_old.status_code in (401, 403)

        r_new = client.get(
            "/__mdb/health/modules?slug=demo",
            headers={ADMIN_TOKEN_HEADER: new_token},
        )
        assert r_new.status_code == 200

    async def test_rotation_response_echoes_fresh_token_id(self, harness):
        """``token_id`` in the rotate response must match a fingerprint of
        the *new* token — not the presenting one. Regression guard: a
        previous iteration accidentally returned the old fingerprint,
        which made ``secrets/current → token_id`` useless for tracking
        the freshly-minted credential through audit."""
        client, mgr, _, _ = harness
        old_token = await _seed_initial_token(mgr)
        old_fp = mgr.fingerprint(old_token)

        r = client.post(
            "/__mdb/secrets/rotate?slug=demo",
            headers={ADMIN_TOKEN_HEADER: old_token},
            json={"overlap_seconds": 30, "scopes": ["*"], "label": "v2"},
        )
        assert r.status_code == 200, r.text
        payload = r.json()
        new_fp = mgr.fingerprint(payload["token"])
        assert payload["token_id"] == new_fp
        assert payload["token_id"] != old_fp
