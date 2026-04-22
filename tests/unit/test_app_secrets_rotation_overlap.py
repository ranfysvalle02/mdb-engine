"""
Graceful-rotation tests for AppSecretsManager.

``rotate_app_secret(overlap_seconds=N)`` keeps the previous token
valid for N seconds. These tests pin the behaviour end-to-end using
an in-memory collection stub that faithfully replays the operations
``AppSecretsManager`` calls (``find_one`` / ``replace_one`` /
``update_one`` / ``insert_one``).

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from __future__ import annotations

import base64
import copy
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from mdb_engine.core.app_secrets import AppSecretsManager
from mdb_engine.core.encryption import EnvelopeEncryptionService


class _InMemoryCollection:
    """Minimal Motor-compatible stub for the secrets collection."""

    def __init__(self):
        self._docs: dict[str, dict[str, Any]] = {}

    async def find_one(self, filt, projection=None):
        doc = self._docs.get(filt.get("_id"))
        if doc is None:
            return None
        # Return a deep copy so the caller can't mutate our storage.
        return copy.deepcopy(doc)

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
    def __init__(self, coll):
        self._coll = coll

    def __getitem__(self, name):
        return self._coll


@pytest.fixture
def enc_service():
    master = EnvelopeEncryptionService.generate_master_key()
    return EnvelopeEncryptionService(base64.b64decode(master.encode()))


@pytest.fixture
def mgr(enc_service):
    coll = _InMemoryCollection()
    db = _InMemoryDB(coll)
    return AppSecretsManager(db, enc_service), coll


@pytest.mark.asyncio
class TestGracefulRotation:
    """Contract: previous token keeps working during overlap window."""

    async def test_no_overlap_revokes_previous_token_immediately(self, mgr):
        manager, _ = mgr
        await manager.store_app_secret("demo", "orig-tok", scopes=["read"], label="v1")
        new_tok = await manager.rotate_app_secret("demo")

        # New token works.
        r = await manager.verify_app_token("demo", new_tok)
        assert r.valid is True

        # Previous token is instantly dead.
        r = await manager.verify_app_token("demo", "orig-tok")
        assert r.valid is False

    async def test_overlap_window_keeps_previous_token_valid(self, mgr):
        manager, _ = mgr
        await manager.store_app_secret("demo", "orig-tok", scopes=["read"], label="v1")
        new_tok = await manager.rotate_app_secret(
            "demo",
            scopes=["read", "apply"],
            label="v2",
            overlap_seconds=60,
        )

        # New token works with new scopes + label.
        r_new = await manager.verify_app_token("demo", new_tok)
        assert r_new.valid is True
        assert r_new.scopes == ["read", "apply"]
        assert r_new.label == "v2"

        # Previous token STILL works, with OLD scopes + label.
        r_old = await manager.verify_app_token("demo", "orig-tok")
        assert r_old.valid is True
        assert r_old.scopes == ["read"]
        assert r_old.label == "v1"

    async def test_previous_token_dies_after_expiry(self, mgr):
        manager, coll = mgr
        await manager.store_app_secret("demo", "orig-tok", scopes=["read"], label="v1")
        await manager.rotate_app_secret("demo", overlap_seconds=60)

        # Force-expire the previous slot to simulate the TTL elapsing.
        coll._docs["demo"]["previous_expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)

        r = await manager.verify_app_token("demo", "orig-tok")
        assert r.valid is False

    async def test_second_rotation_evicts_the_overlap_slot(self, mgr):
        """After a rotation without overlap, the previous-slot is wiped.

        Regression guard: if rotation #2 doesn't ``$unset`` the
        previous_* fields, a caller could keep presenting the
        "grandparent" token forever.
        """
        manager, coll = mgr
        await manager.store_app_secret("demo", "tok-1", scopes=["read"])

        await manager.rotate_app_secret("demo", overlap_seconds=60)
        # tok-1 is the "previous" now
        assert "previous_encrypted_secret" in coll._docs["demo"]

        tok_3 = await manager.rotate_app_secret("demo", overlap_seconds=0)

        # Fresh rotation without overlap → previous slot gone.
        assert "previous_encrypted_secret" not in coll._docs["demo"]

        # tok-1 rejected; tok-3 accepted.
        assert (await manager.verify_app_token("demo", "tok-1")).valid is False
        assert (await manager.verify_app_token("demo", tok_3)).valid is True

    async def test_overlap_is_clamped_to_max(self, mgr, caplog):
        import logging

        manager, coll = mgr
        await manager.store_app_secret("demo", "orig-tok")
        with caplog.at_level(logging.WARNING):
            await manager.rotate_app_secret("demo", overlap_seconds=99999)
        expires = coll._docs["demo"]["previous_expires_at"]
        max_allowed = datetime.now(timezone.utc) + timedelta(seconds=AppSecretsManager.MAX_OVERLAP_SECONDS + 5)
        assert expires <= max_allowed, "overlap window must be clamped"
        assert any("clamped" in rec.message.lower() for rec in caplog.records)

    async def test_token_id_fingerprints_actually_presented_token(self, mgr):
        """Forensics: the audit stream must show 'Alice still uses the
        old cred' vs 'Alice switched', which means ``token_id`` must
        fingerprint what the client sent, not what was stored."""
        manager, _ = mgr
        await manager.store_app_secret("demo", "orig-tok")
        new_tok = await manager.rotate_app_secret("demo", overlap_seconds=60)

        old_id = (await manager.verify_app_token("demo", "orig-tok")).token_id
        new_id = (await manager.verify_app_token("demo", new_tok)).token_id
        assert old_id != new_id
        assert old_id and new_id
