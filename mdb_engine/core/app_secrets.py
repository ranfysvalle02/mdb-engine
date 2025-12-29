"""
App Secrets Manager

Manages encrypted app secrets stored in MongoDB using envelope encryption.

This module is part of MDB_ENGINE - MongoDB Engine.

The AppSecretsManager stores encrypted app secrets in the `_mdb_engine_app_secrets`
collection, which is only accessible via raw MongoDB client (not scoped wrapper).
"""

import base64
import logging
import secrets
from datetime import datetime
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import OperationFailure

from .encryption import EnvelopeEncryptionService

logger = logging.getLogger(__name__)

# Collection name for storing encrypted app secrets
SECRETS_COLLECTION_NAME = "_mdb_engine_app_secrets"


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

    async def store_app_secret(self, app_slug: str, secret: str) -> None:
        """
        Store an encrypted app secret.

        Args:
            app_slug: App slug identifier
            secret: Plaintext secret to encrypt and store

        Raises:
            OperationFailure: If MongoDB operation fails

        The secret is encrypted using envelope encryption and stored with
        metadata (created_at, updated_at, rotation_count).
        """
        try:
            # Encrypt secret
            encrypted_secret, encrypted_dek = self._encryption_service.encrypt_secret(
                secret
            )

            # Encode as base64 for storage
            encrypted_secret_b64 = base64.b64encode(encrypted_secret).decode()
            encrypted_dek_b64 = base64.b64encode(encrypted_dek).decode()

            # Prepare document
            now = datetime.utcnow()
            document = {
                "_id": app_slug,
                "encrypted_secret": encrypted_secret_b64,
                "encrypted_dek": encrypted_dek_b64,
                "algorithm": "AES-256-GCM",
                "updated_at": now,
            }

            # Check if secret already exists
            existing = await self._secrets_collection.find_one({"_id": app_slug})
            if existing:
                # Update existing secret
                document["created_at"] = existing.get("created_at", now)
                document["rotation_count"] = existing.get("rotation_count", 0) + 1
                await self._secrets_collection.replace_one({"_id": app_slug}, document)
                logger.info(
                    f"Updated encrypted secret for app '{app_slug}' "
                    f"(rotation #{document['rotation_count']})"
                )
            else:
                # Insert new secret
                document["created_at"] = now
                document["rotation_count"] = 0
                await self._secrets_collection.insert_one(document)
                logger.info(f"Stored encrypted secret for app '{app_slug}'")

        except Exception as e:
            logger.error(
                f"Failed to store secret for app '{app_slug}': {e}", exc_info=True
            )
            raise OperationFailure(f"Failed to store app secret: {e}") from e

    def verify_app_secret_sync(self, app_slug: str, provided_secret: str) -> bool:
        """
        Synchronous version of verify_app_secret for use in sync contexts.

        Note: This uses asyncio.run() which creates a new event loop.
        Use verify_app_secret() in async contexts for better performance.

        Args:
            app_slug: App slug identifier
            provided_secret: Secret to verify

        Returns:
            True if secret matches, False otherwise
        """
        import asyncio

        try:
            # Try to get running loop
            asyncio.get_running_loop()
            # If we're in an async context, we can't use asyncio.run()
            # Return False and log warning
            logger.warning(
                f"Cannot verify app secret synchronously in async context "
                f"for '{app_slug}'. Use verify_app_secret() instead."
            )
            return False
        except RuntimeError:
            # No running loop - safe to use asyncio.run()
            return asyncio.run(self.verify_app_secret(app_slug, provided_secret))

    async def verify_app_secret(self, app_slug: str, provided_secret: str) -> bool:
        """
        Verify an app secret against stored encrypted value.

        Args:
            app_slug: App slug identifier
            provided_secret: Secret to verify

        Returns:
            True if secret matches, False otherwise

        Uses constant-time comparison to prevent timing attacks.
        """
        # Get encrypted secret from database
        doc = await self._secrets_collection.find_one({"_id": app_slug})
        if not doc:
            logger.warning(f"Secret verification failed: app '{app_slug}' not found")
            return False

        # Decode base64
        try:
            encrypted_secret = base64.b64decode(doc["encrypted_secret"])
            encrypted_dek = base64.b64decode(doc["encrypted_dek"])
        except (ValueError, KeyError, TypeError) as e:
            logger.warning(f"Secret verification error for app '{app_slug}': {e}")
            return False

        # Decrypt stored secret
        try:
            stored_secret = self._encryption_service.decrypt_secret(
                encrypted_secret, encrypted_dek
            )
        except ValueError:
            logger.warning(f"Secret decryption failed for app '{app_slug}'")
            return False

        # Constant-time comparison to prevent timing attacks
        if len(provided_secret) != len(stored_secret):
            logger.info(
                f"Secret verification failed for app '{app_slug}': length mismatch"
            )
            return False

        # Use secrets.compare_digest for constant-time comparison
        result = secrets.compare_digest(
            provided_secret.encode(), stored_secret.encode()
        )
        if result:
            logger.debug(f"Secret verification succeeded for app '{app_slug}'")
        else:
            logger.warning(
                f"Secret verification failed for app '{app_slug}': secret mismatch"
            )
        return result

    async def get_app_secret(self, app_slug: str) -> Optional[str]:
        """
        Get decrypted app secret (for rotation purposes only).

        Args:
            app_slug: App slug identifier

        Returns:
            Decrypted secret if found, None otherwise

        Warning:
            This method returns plaintext secrets. Use only for rotation.
            Regular verification should use verify_app_secret().
        """
        doc = await self._secrets_collection.find_one({"_id": app_slug})
        if not doc:
            return None

        # Decode base64
        try:
            encrypted_secret = base64.b64decode(doc["encrypted_secret"])
            encrypted_dek = base64.b64decode(doc["encrypted_dek"])
        except (ValueError, KeyError, TypeError) as e:
            logger.warning(f"Failed to decode secret for app '{app_slug}': {e}")
            return None

        # Decrypt secret
        try:
            secret = self._encryption_service.decrypt_secret(
                encrypted_secret, encrypted_dek
            )
            return secret
        except ValueError as e:
            logger.warning(f"Failed to decrypt secret for app '{app_slug}': {e}")
            return None

    async def rotate_app_secret(self, app_slug: str) -> str:
        """
        Rotate an app secret (generate new secret, re-encrypt and store).

        Args:
            app_slug: App slug identifier

        Returns:
            New plaintext secret (caller must store securely)

        Raises:
            ValueError: If app secret not found

        The new secret is encrypted and stored, replacing the old one.
        """
        # Generate new secret
        new_secret = secrets.token_urlsafe(32)

        # Store new encrypted secret
        await self.store_app_secret(app_slug, new_secret)

        logger.info(f"Rotated secret for app '{app_slug}'")
        return new_secret

    def app_secret_exists_sync(self, app_slug: str) -> bool:
        """
        Synchronous version of app_secret_exists for use in sync contexts.

        Args:
            app_slug: App slug identifier

        Returns:
            True if secret exists, False otherwise

        Note: If called from an async context, this will return False and log a warning.
        Use app_secret_exists() in async contexts.
        """
        import asyncio

        try:
            # Try to get running loop
            asyncio.get_running_loop()
            # We're in an async context - can't safely check without blocking
            # Return False (assume no secret) to allow backward compatibility
            # The secret will be verified at query time through async methods
            logger.debug(
                f"Cannot check app secret existence synchronously in async context "
                f"for '{app_slug}'. Assuming no secret exists (backward compatibility)."
            )
            return False
        except RuntimeError:
            # No running loop - safe to use asyncio.run()
            return asyncio.run(self.app_secret_exists(app_slug))

    async def app_secret_exists(self, app_slug: str) -> bool:
        """
        Check if an app secret exists.

        Args:
            app_slug: App slug identifier

        Returns:
            True if secret exists, False otherwise
        """
        doc = await self._secrets_collection.find_one(
            {"_id": app_slug}, projection={"_id": 1}
        )
        return doc is not None
