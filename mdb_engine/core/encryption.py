"""
Envelope Encryption Service

Provides envelope encryption for app secrets using a master key and
per-secret data encryption keys (DEKs).

This module is part of MDB_ENGINE - MongoDB Engine.

Envelope Encryption Model:
- Master Key (MK): Encrypts DEKs, stored in environment variable
- Data Encryption Key (DEK): Encrypts app secrets, encrypted with MK
- App Secret: Plaintext secret, encrypted with DEK

This provides defense-in-depth: even if DEKs are compromised, they
cannot be decrypted without the master key.
"""

import base64
import logging
import os
import secrets

import cryptography.exceptions
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

# Master key environment variable name
MASTER_KEY_ENV_VAR = "MDB_ENGINE_MASTER_KEY"

# AES-GCM configuration
AES_KEY_SIZE = 32  # 256 bits
AES_NONCE_SIZE = 12  # 96 bits for GCM


class EnvelopeEncryptionService:
    """
    Service for envelope encryption of app secrets.

    Uses AES-256-GCM for encryption with envelope encryption pattern:
    1. Generate random DEK for each secret
    2. Encrypt secret with DEK
    3. Encrypt DEK with master key
    4. Store both encrypted values

    This allows master key rotation without re-encrypting all secrets.
    """

    def __init__(self, master_key: bytes | None = None):
        """
        Initialize the encryption service.

        Args:
            master_key: Master key bytes. If None, loads from environment.

        Raises:
            ValueError: If master key is not provided and not in environment.
        """
        self._master_key = master_key or self._load_master_key()
        if not self._master_key:
            raise ValueError(
                f"Master key not found. Set {MASTER_KEY_ENV_VAR} environment variable "
                "or provide master_key parameter."
            )

    def _load_master_key(self) -> bytes | None:
        """
        Load master key from environment variable.

        Returns:
            Master key bytes if found, None otherwise.

        Raises:
            ValueError: If master key format is invalid.
        """
        master_key_str = os.getenv(MASTER_KEY_ENV_VAR)
        if not master_key_str:
            return None

        try:
            # Decode base64-encoded master key
            master_key_bytes = base64.b64decode(master_key_str.encode())
            if len(master_key_bytes) != AES_KEY_SIZE:
                raise ValueError(
                    f"Master key must be {AES_KEY_SIZE} bytes (256 bits) when decoded. "
                    f"Got {len(master_key_bytes)} bytes."
                )
            return master_key_bytes
        except (ValueError, TypeError, UnicodeDecodeError) as e:
            raise ValueError(
                f"Invalid master key format in {MASTER_KEY_ENV_VAR}. "
                f"Expected base64-encoded {AES_KEY_SIZE}-byte key. Error: {e}"
            ) from e

    @staticmethod
    def generate_master_key() -> str:
        """
        Generate a new master key.

        Returns:
            Base64-encoded master key string (suitable for environment variable).

        Example:
            >>> key = EnvelopeEncryptionService.generate_master_key()
            >>> # Store in .env: MDB_ENGINE_MASTER_KEY={key}
        """
        key_bytes = secrets.token_bytes(AES_KEY_SIZE)
        return base64.b64encode(key_bytes).decode()

    @staticmethod
    def generate_dek() -> bytes:
        """
        Generate a random Data Encryption Key (DEK).

        Returns:
            Random 32-byte DEK.

        Note:
            Each secret gets its own DEK for better security isolation.
        """
        return secrets.token_bytes(AES_KEY_SIZE)

    def encrypt_secret(self, secret: str, master_key: bytes | None = None) -> tuple[bytes, bytes]:
        """
        Encrypt a secret using envelope encryption.

        Args:
            secret: Plaintext secret to encrypt.
            master_key: Master key to use. If None, uses instance master key.

        Returns:
            Tuple of (encrypted_secret, encrypted_dek) as bytes.

        Process:
            1. Generate random DEK
            2. Encrypt secret with DEK using AES-GCM
            3. Encrypt DEK with master key using AES-GCM
            4. Return both encrypted values

        Example:
            >>> service = EnvelopeEncryptionService()
            >>> encrypted_secret, encrypted_dek = service.encrypt_secret("my_secret")
        """
        master_key = master_key or self._master_key

        # Generate random DEK
        dek = self.generate_dek()

        # Encrypt secret with DEK
        aesgcm_secret = AESGCM(dek)
        nonce_secret = secrets.token_bytes(AES_NONCE_SIZE)
        encrypted_secret = aesgcm_secret.encrypt(nonce_secret, secret.encode(), None)

        # Prepend nonce to encrypted secret
        encrypted_secret_with_nonce = nonce_secret + encrypted_secret

        # Encrypt DEK with master key
        aesgcm_dek = AESGCM(master_key)
        nonce_dek = secrets.token_bytes(AES_NONCE_SIZE)
        encrypted_dek = aesgcm_dek.encrypt(nonce_dek, dek, None)

        # Prepend nonce to encrypted DEK
        encrypted_dek_with_nonce = nonce_dek + encrypted_dek

        return encrypted_secret_with_nonce, encrypted_dek_with_nonce

    def decrypt_secret(
        self,
        encrypted_secret: bytes,
        encrypted_dek: bytes,
        master_key: bytes | None = None,
    ) -> str:
        """
        Decrypt a secret using envelope decryption.

        Args:
            encrypted_secret: Encrypted secret (with nonce prepended).
            encrypted_dek: Encrypted DEK (with nonce prepended).
            master_key: Master key to use. If None, uses instance master key.

        Returns:
            Decrypted plaintext secret.

        Raises:
            ValueError: If decryption fails (invalid key, corrupted data, etc.).

        Process:
            1. Decrypt DEK with master key
            2. Decrypt secret with DEK
            3. Return plaintext secret

        Example:
            >>> service = EnvelopeEncryptionService()
            >>> secret = service.decrypt_secret(encrypted_secret, encrypted_dek)
        """
        master_key = master_key or self._master_key

        try:
            # Extract nonce and encrypted data for DEK
            if len(encrypted_dek) < AES_NONCE_SIZE:
                raise ValueError("Encrypted DEK too short (missing nonce)")
            nonce_dek = encrypted_dek[:AES_NONCE_SIZE]
            encrypted_dek_data = encrypted_dek[AES_NONCE_SIZE:]

            # Decrypt DEK with master key
            aesgcm_dek = AESGCM(master_key)
            dek = aesgcm_dek.decrypt(nonce_dek, encrypted_dek_data, None)

            # Extract nonce and encrypted data for secret
            if len(encrypted_secret) < AES_NONCE_SIZE:
                raise ValueError("Encrypted secret too short (missing nonce)")
            nonce_secret = encrypted_secret[:AES_NONCE_SIZE]
            encrypted_secret_data = encrypted_secret[AES_NONCE_SIZE:]

            # Decrypt secret with DEK
            aesgcm_secret = AESGCM(dek)
            secret_bytes = aesgcm_secret.decrypt(nonce_secret, encrypted_secret_data, None)

            return secret_bytes.decode()
        except (ValueError, TypeError, UnicodeDecodeError, cryptography.exceptions.InvalidTag) as e:
            logger.warning(f"Decryption failed: {e}")
            raise ValueError(f"Failed to decrypt secret: {e}") from e
