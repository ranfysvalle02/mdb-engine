"""
App Secrets Manager

Manages encrypted app secrets stored in MongoDB using envelope encryption.

This module is part of MDB_ENGINE - MongoDB Engine.

The AppSecretsManager stores encrypted app secrets in the `_mdb_engine_app_secrets`
collection, which is only accessible via raw MongoDB client (not scoped wrapper).
"""

import base64
import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import OperationFailure, PyMongoError

from .encryption import EnvelopeEncryptionService

logger = logging.getLogger(__name__)

# Collection name for storing encrypted app secrets
SECRETS_COLLECTION_NAME = "_mdb_engine_app_secrets"

WILDCARD_SCOPE = "*"
"""Token scope matching every admin plane action (legacy default)."""


@dataclass
class VerifyResult:
    """Result of :meth:`AppSecretsManager.verify_app_token`.

    ``valid`` mirrors the legacy boolean return of
    :meth:`verify_app_secret`. ``scopes`` is the list the token was
    minted with (defaults to ``['*']`` for secrets created before
    scopes were a concept). ``token_id`` is a short HMAC fingerprint
    suitable for audit logging — callers should *never* use it as an
    auth primitive. ``label`` is the human-facing identifier set at
    issue/rotation time (e.g. ``"ci-gha"``).
    """

    valid: bool
    scopes: list[str] = field(default_factory=lambda: [WILDCARD_SCOPE])
    token_id: str | None = None
    label: str | None = None


class AppSecretsManager:
    """
    Manages encrypted app secrets using envelope encryption.

    Secrets are stored encrypted in MongoDB and can only be verified,
    not retrieved in plaintext (except during rotation).
    """

    def __init__(
        self,
        mongo_db: AsyncIOMotorDatabase,
        encryption_service: EnvelopeEncryptionService,
    ):
        """
        Initialize the app secrets manager.

        Args:
            mongo_db: MongoDB database instance (raw, not scoped)
            encryption_service: Envelope encryption service instance
        """
        self._mongo_db = mongo_db
        self._encryption_service = encryption_service
        self._secrets_collection = mongo_db[SECRETS_COLLECTION_NAME]
        self._fingerprint_degraded_logged = False

    # ------------------------------------------------------------------
    # Fingerprinting
    # ------------------------------------------------------------------

    def fingerprint(self, token: str | None) -> str | None:
        """Return a non-reversible HMAC identifier for a token.

        Unlike a bare SHA-256 prefix, an HMAC prevents correlation of
        a token's identity across systems that do not share the
        engine's master key. The 16-hex-char truncation (64 bits) is
        enough to distinguish "Alice's token" from "Bob's token" in
        audit forensics without creating a useful verification oracle.

        Returns ``None`` if the engine has no master key to derive
        from — we deliberately refuse to fall back to a public
        constant, because a predictable fingerprint is worse than no
        fingerprint: it would let an attacker who learns ``token_id``
        recover the token by dictionary attack. Callers already
        tolerate ``None`` (audit row just records no principal id).
        """
        if not token:
            return None
        key = self._hmac_key()
        if key is None:
            # First miss emits a WARNING; after that we stay quiet to
            # avoid drowning the log stream on every admin call.
            if not self._fingerprint_degraded_logged:
                logger.error(
                    "AppSecretsManager has no master key; token fingerprints "
                    "will be None. Audit forensics and rate-limit bucketing "
                    "are degraded until a master key is configured."
                )
                self._fingerprint_degraded_logged = True
            return None
        digest = hmac.new(key, token.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"hmac:{digest[:16]}"

    def _hmac_key(self) -> bytes | None:
        """Derive a stable key used only for fingerprinting.

        We intentionally do not use the raw master key — deriving a
        distinct key means a compromised fingerprint cannot aid a
        decryption attempt against stored secrets. Returns ``None``
        when the engine has no master key (misconfiguration or test
        harness); :meth:`fingerprint` surfaces that as a ``None``
        result rather than a predictable public constant.
        """
        master = getattr(self._encryption_service, "_master_key", None)
        if not master:
            return None
        return hmac.new(master, b"mdb-engine:admin-token-fingerprint", hashlib.sha256).digest()

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    async def store_app_secret(
        self,
        app_slug: str,
        secret: str,
        *,
        scopes: list[str] | None = None,
        label: str | None = None,
    ) -> None:
        """
        Store an encrypted app secret.

        Args:
            app_slug: App slug identifier
            secret: Plaintext secret to encrypt and store
            scopes: Optional scope list constraining what admin-plane
                modules this token can call. Defaults to ``["*"]``
                (full access) to preserve pre-scope behaviour.
            label: Optional human-facing identifier for the token
                (e.g. ``"ci-gha"``). Shown in the audit log.

        Raises:
            OperationFailure: If MongoDB operation fails

        The secret is encrypted using envelope encryption and stored with
        metadata (created_at, updated_at, rotation_count, scopes, label).
        """
        try:
            encrypted_secret, encrypted_dek = self._encryption_service.encrypt_secret(secret)
            encrypted_secret_b64 = base64.b64encode(encrypted_secret).decode()
            encrypted_dek_b64 = base64.b64encode(encrypted_dek).decode()

            now = datetime.now(timezone.utc)
            scope_list = [str(s) for s in (scopes or [WILDCARD_SCOPE])] or [WILDCARD_SCOPE]
            document: dict[str, Any] = {
                "_id": app_slug,
                "encrypted_secret": encrypted_secret_b64,
                "encrypted_dek": encrypted_dek_b64,
                "algorithm": "AES-256-GCM",
                "scopes": scope_list,
                "label": (str(label)[:120] if label else None),
                "updated_at": now,
            }

            existing = await self._secrets_collection.find_one({"_id": app_slug})
            if existing:
                document["created_at"] = existing.get("created_at", now)
                document["rotation_count"] = existing.get("rotation_count", 0) + 1
                await self._secrets_collection.replace_one({"_id": app_slug}, document)
                logger.info(f"Updated encrypted secret for app '{app_slug}' (rotation #{document['rotation_count']})")
            else:
                document["created_at"] = now
                document["rotation_count"] = 0
                await self._secrets_collection.insert_one(document)
                logger.info(f"Stored encrypted secret for app '{app_slug}'")

        except PyMongoError as e:
            logger.error(f"Database error storing secret for app '{app_slug}': {e}", exc_info=True)
            raise OperationFailure(f"Failed to store app secret: {e}") from e
        except (ValueError, TypeError) as e:
            logger.error(f"Encryption error storing secret for app '{app_slug}': {e}", exc_info=True)
            raise

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    async def verify_app_token(self, app_slug: str, provided_secret: str) -> VerifyResult:
        """Verify a provided token and return its scopes + identity.

        The admin plane uses this shape so per-module scope gating
        can read the token's scopes without another DB round-trip.
        Legacy callers can keep using :meth:`verify_app_secret` which
        returns a plain boolean.

        **Graceful rotation**: if a previous token is still inside its
        overlap window (set via ``rotate_app_secret(overlap_seconds=N)``)
        this method accepts it too, using the *previous* token's label
        + scopes. The response ``token_id`` always fingerprints the
        actually-presented token, so audit rows can distinguish
        "still-using-old-cred" from "already-rotated" callers.
        """
        doc = await self._secrets_collection.find_one({"_id": app_slug})
        if not doc:
            logger.warning(f"Secret verification failed: app '{app_slug}' not found")
            return VerifyResult(valid=False)
        try:
            encrypted_secret = base64.b64decode(doc["encrypted_secret"])
            encrypted_dek = base64.b64decode(doc["encrypted_dek"])
        except (ValueError, KeyError, TypeError) as e:
            logger.warning(f"Secret verification error for app '{app_slug}': {e}", exc_info=True)
            return VerifyResult(valid=False)
        try:
            stored_secret = self._encryption_service.decrypt_secret(encrypted_secret, encrypted_dek)
        except ValueError:
            logger.warning(f"Secret decryption failed for app '{app_slug}'", exc_info=True)
            return VerifyResult(valid=False)
        provided_bytes = provided_secret.encode()
        if secrets.compare_digest(provided_bytes, stored_secret.encode()):
            scopes_raw = doc.get("scopes")
            scopes = [str(s) for s in scopes_raw] if isinstance(scopes_raw, list) and scopes_raw else [WILDCARD_SCOPE]
            label = doc.get("label")
            return VerifyResult(
                valid=True,
                scopes=scopes,
                token_id=self.fingerprint(provided_secret),
                label=label if isinstance(label, str) else None,
            )
        # Graceful rotation fallback: is a previous token still valid?
        prev_ok = self._verify_previous_token(doc, provided_bytes)
        if prev_ok is not None:
            prev_label, prev_scopes = prev_ok
            logger.info(
                "app '%s' authenticated with PREVIOUS token during rotation overlap window",
                app_slug,
            )
            return VerifyResult(
                valid=True,
                scopes=prev_scopes,
                token_id=self.fingerprint(provided_secret),
                label=prev_label,
            )
        logger.warning(f"Secret verification failed for app '{app_slug}'")
        return VerifyResult(valid=False)

    def _verify_previous_token(
        self,
        doc: dict[str, Any],
        provided_bytes: bytes,
    ) -> tuple[str | None, list[str]] | None:
        """Check the previous-token overlap slot on ``doc``.

        Returns ``(label, scopes)`` when the provided bytes match an
        unexpired ``previous_*`` entry; ``None`` otherwise. A separate
        method so the hot path stays flat and so the legacy "no
        overlap set" case skips all base64 decoding work.
        """
        prev_secret_b64 = doc.get("previous_encrypted_secret")
        prev_dek_b64 = doc.get("previous_encrypted_dek")
        prev_expires_at = doc.get("previous_expires_at")
        if not (prev_secret_b64 and prev_dek_b64 and prev_expires_at):
            return None
        now = datetime.now(timezone.utc)
        # ``prev_expires_at`` may come back tz-naive from older Mongo drivers.
        if prev_expires_at.tzinfo is None:
            prev_expires_at = prev_expires_at.replace(tzinfo=timezone.utc)
        if now >= prev_expires_at:
            return None
        try:
            prev_encrypted = base64.b64decode(prev_secret_b64)
            prev_dek = base64.b64decode(prev_dek_b64)
            prev_plain = self._encryption_service.decrypt_secret(prev_encrypted, prev_dek)
        except (ValueError, KeyError, TypeError):
            return None
        if not secrets.compare_digest(provided_bytes, prev_plain.encode()):
            return None
        prev_scopes_raw = doc.get("previous_scopes")
        prev_scopes = (
            [str(s) for s in prev_scopes_raw]
            if isinstance(prev_scopes_raw, list) and prev_scopes_raw
            else [WILDCARD_SCOPE]
        )
        prev_label = doc.get("previous_label")
        return (
            prev_label if isinstance(prev_label, str) else None,
            prev_scopes,
        )

    async def verify_app_secret(self, app_slug: str, provided_secret: str) -> bool:
        """
        Verify an app secret against stored encrypted value.

        Retained as a boolean shim for legacy callers; the admin plane
        uses :meth:`verify_app_token` which surfaces scopes + identity.
        Uses constant-time comparison to prevent timing attacks.
        """
        doc = await self._secrets_collection.find_one({"_id": app_slug})
        if not doc:
            logger.warning(f"Secret verification failed: app '{app_slug}' not found")
            return False
        try:
            encrypted_secret = base64.b64decode(doc["encrypted_secret"])
            encrypted_dek = base64.b64decode(doc["encrypted_dek"])
        except (ValueError, KeyError, TypeError) as e:
            logger.warning(f"Secret verification error for app '{app_slug}': {e}", exc_info=True)
            return False
        try:
            stored_secret = self._encryption_service.decrypt_secret(encrypted_secret, encrypted_dek)
        except ValueError:
            logger.warning(f"Secret decryption failed for app '{app_slug}'", exc_info=True)
            return False
        result = secrets.compare_digest(provided_secret.encode(), stored_secret.encode())
        if result:
            logger.debug(f"Secret verification succeeded for app '{app_slug}'")
        else:
            logger.warning(f"Secret verification failed for app '{app_slug}'")
        return result

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    async def get_app_secret(self, app_slug: str) -> str | None:
        """
        Get decrypted app secret (for rotation purposes only).

        Warning:
            This method returns plaintext secrets. Use only for rotation.
            Regular verification should use verify_app_secret().
        """
        doc = await self._secrets_collection.find_one({"_id": app_slug})
        if not doc:
            return None
        try:
            encrypted_secret = base64.b64decode(doc["encrypted_secret"])
            encrypted_dek = base64.b64decode(doc["encrypted_dek"])
        except (ValueError, KeyError, TypeError) as e:
            logger.warning(f"Failed to decode secret for app '{app_slug}': {e}")
            return None
        try:
            return self._encryption_service.decrypt_secret(encrypted_secret, encrypted_dek)
        except ValueError as e:
            logger.warning(f"Failed to decrypt secret for app '{app_slug}': {e}")
            return None

    async def get_app_secret_metadata(self, app_slug: str) -> dict[str, Any]:
        """Return non-sensitive metadata for an app secret.

        Returns an empty dict when no secret exists — callers never
        receive a ``None`` they have to guard against.
        """
        doc = await self._secrets_collection.find_one(
            {"_id": app_slug},
            projection={
                "scopes": 1,
                "label": 1,
                "created_at": 1,
                "updated_at": 1,
                "rotation_count": 1,
            },
        )
        if not doc:
            return {}
        out: dict[str, Any] = {
            "slug": app_slug,
            "scopes": list(doc.get("scopes") or []),
            "label": doc.get("label"),
            "created_at": doc.get("created_at"),
            "updated_at": doc.get("updated_at"),
            "rotation_count": doc.get("rotation_count", 0),
        }
        return out

    MAX_OVERLAP_SECONDS = 3600
    """Upper bound for graceful-rotation overlap: 1 hour.

    Long enough for a CI fleet to roll its cached token everywhere,
    short enough that a leaked previous token can't be weaponized for
    a whole day. Callers asking for more are silently clamped and a
    warning is logged so misconfiguration is visible.
    """

    async def rotate_app_secret(
        self,
        app_slug: str,
        *,
        scopes: list[str] | None = None,
        label: str | None = None,
        overlap_seconds: int = 0,
    ) -> str:
        """Rotate an app secret (generate new secret, re-encrypt, store).

        Args:
            app_slug: App slug identifier
            scopes: Optional new scope list. When ``None`` the previous
                token's scopes are preserved (legacy tokens without a
                scopes field upgrade to ``["*"]``).
            label: Optional new label. When ``None`` the previous
                label is preserved.
            overlap_seconds: When > 0, the current token stays valid
                for this many seconds after the rotation completes
                (capped at :attr:`MAX_OVERLAP_SECONDS`). Lets callers
                roll credentials across a fleet without a window of
                401s. Default ``0`` matches the legacy
                "immediate revoke" behaviour.

        Returns:
            New plaintext secret (caller must store securely)

        Raises:
            ValueError: If app secret not found
        """
        new_secret = secrets.token_urlsafe(32)

        existing = await self._secrets_collection.find_one(
            {"_id": app_slug},
            projection={
                "scopes": 1,
                "label": 1,
                "encrypted_secret": 1,
                "encrypted_dek": 1,
            },
        )
        if scopes is None:
            if existing and isinstance(existing.get("scopes"), list) and existing["scopes"]:
                scopes = [str(s) for s in existing["scopes"]]
            else:
                scopes = [WILDCARD_SCOPE]
        if label is None and existing:
            existing_label = existing.get("label")
            if isinstance(existing_label, str):
                label = existing_label

        await self.store_app_secret(app_slug, new_secret, scopes=scopes, label=label)

        overlap = max(0, min(int(overlap_seconds or 0), self.MAX_OVERLAP_SECONDS))
        if overlap_seconds and overlap < int(overlap_seconds):
            logger.warning(
                "rotate_app_secret: overlap_seconds=%s clamped to %s (MAX_OVERLAP_SECONDS)",
                overlap_seconds,
                overlap,
            )
        if overlap > 0 and existing and existing.get("encrypted_secret"):
            prev_scopes_raw = existing.get("scopes") or [WILDCARD_SCOPE]
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=overlap)
            await self._secrets_collection.update_one(
                {"_id": app_slug},
                {
                    "$set": {
                        "previous_encrypted_secret": existing.get("encrypted_secret"),
                        "previous_encrypted_dek": existing.get("encrypted_dek"),
                        "previous_scopes": [str(s) for s in prev_scopes_raw],
                        "previous_label": existing.get("label"),
                        "previous_expires_at": expires_at,
                    }
                },
            )
            logger.info(
                "rotate_app_secret: previous token for '%s' remains valid for %ss (until %s)",
                app_slug,
                overlap,
                expires_at.isoformat(),
            )
        else:
            # No overlap → actively evict any stale previous-slot.
            # Idempotent: ``$unset`` on an absent field is a no-op.
            await self._secrets_collection.update_one(
                {"_id": app_slug},
                {
                    "$unset": {
                        "previous_encrypted_secret": "",
                        "previous_encrypted_dek": "",
                        "previous_scopes": "",
                        "previous_label": "",
                        "previous_expires_at": "",
                    }
                },
            )

        logger.info(
            f"Rotated secret for app '{app_slug}' " f"(scopes={scopes}, label={label or '<none>'}, overlap_s={overlap})"
        )
        return new_secret

    async def app_secret_exists(self, app_slug: str) -> bool:
        """Check if an app secret exists."""
        doc = await self._secrets_collection.find_one({"_id": app_slug}, projection={"_id": 1})
        return doc is not None
