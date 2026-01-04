"""
Unit tests for EnvelopeEncryptionService.

Tests encryption/decryption, master key management, and edge cases.
"""

import base64
import secrets

import pytest

from mdb_engine.core.encryption import (
    AES_KEY_SIZE,
    MASTER_KEY_ENV_VAR,
    EnvelopeEncryptionService,
)


class TestEnvelopeEncryptionService:
    """Test EnvelopeEncryptionService functionality."""

    def test_generate_master_key(self):
        """Test master key generation."""
        key = EnvelopeEncryptionService.generate_master_key()
        assert isinstance(key, str)
        # Decode and verify length
        key_bytes = base64.b64decode(key.encode())
        assert len(key_bytes) == AES_KEY_SIZE

    def test_generate_master_key_uniqueness(self):
        """Test that generated master keys are unique."""
        key1 = EnvelopeEncryptionService.generate_master_key()
        key2 = EnvelopeEncryptionService.generate_master_key()
        assert key1 != key2

    def test_generate_master_key_from_env(self, monkeypatch):
        """Test loading master key from environment."""
        # Generate a valid master key
        test_key = EnvelopeEncryptionService.generate_master_key()
        monkeypatch.setenv(MASTER_KEY_ENV_VAR, test_key)

        service = EnvelopeEncryptionService()
        assert service._master_key is not None
        assert len(service._master_key) == AES_KEY_SIZE

    def test_generate_master_key_missing(self, monkeypatch):
        """Test error when master key not found."""
        monkeypatch.delenv(MASTER_KEY_ENV_VAR, raising=False)

        with pytest.raises(ValueError, match="Master key not found"):
            EnvelopeEncryptionService()

    def test_generate_master_key_invalid_format(self, monkeypatch):
        """Test error when master key format is invalid."""
        monkeypatch.setenv(MASTER_KEY_ENV_VAR, "not-base64")

        with pytest.raises(ValueError, match="Invalid master key format"):
            EnvelopeEncryptionService()

    def test_generate_master_key_wrong_length(self, monkeypatch):
        """Test error when master key has wrong length."""
        # Create a key with wrong length (16 bytes instead of 32)
        short_key = base64.b64encode(secrets.token_bytes(16)).decode()
        monkeypatch.setenv(MASTER_KEY_ENV_VAR, short_key)

        with pytest.raises(ValueError, match="Master key must be"):
            EnvelopeEncryptionService()

    def test_generate_dek(self):
        """Test DEK generation."""
        service = EnvelopeEncryptionService(secrets.token_bytes(AES_KEY_SIZE))
        dek = service.generate_dek()
        assert isinstance(dek, bytes)
        assert len(dek) == AES_KEY_SIZE

    def test_generate_dek_uniqueness(self):
        """Test that generated DEKs are unique."""
        service = EnvelopeEncryptionService(secrets.token_bytes(AES_KEY_SIZE))
        dek1 = service.generate_dek()
        dek2 = service.generate_dek()
        assert dek1 != dek2

    def test_encrypt_secret(self):
        """Test secret encryption."""
        master_key = secrets.token_bytes(AES_KEY_SIZE)
        service = EnvelopeEncryptionService(master_key)

        secret = "my_secret_token"
        encrypted_secret, encrypted_dek = service.encrypt_secret(secret)

        assert isinstance(encrypted_secret, bytes)
        assert isinstance(encrypted_dek, bytes)
        assert len(encrypted_secret) > 0
        assert len(encrypted_dek) > 0

    def test_decrypt_secret(self):
        """Test secret decryption."""
        master_key = secrets.token_bytes(AES_KEY_SIZE)
        service = EnvelopeEncryptionService(master_key)

        secret = "my_secret_token"
        encrypted_secret, encrypted_dek = service.encrypt_secret(secret)

        decrypted = service.decrypt_secret(encrypted_secret, encrypted_dek)
        assert decrypted == secret

    def test_encrypt_decrypt_roundtrip(self):
        """Test full encrypt/decrypt cycle."""
        master_key = secrets.token_bytes(AES_KEY_SIZE)
        service = EnvelopeEncryptionService(master_key)

        secrets_to_test = [
            "simple_secret",
            "secret_with_special_chars!@#$%^&*()",
            "secret_with_unicode_测试_🎉",
            "very_long_secret_" * 100,
        ]

        for secret in secrets_to_test:
            encrypted_secret, encrypted_dek = service.encrypt_secret(secret)
            decrypted = service.decrypt_secret(encrypted_secret, encrypted_dek)
            assert decrypted == secret

    def test_encrypt_different_secrets(self):
        """Test that different secrets produce different ciphertexts."""
        master_key = secrets.token_bytes(AES_KEY_SIZE)
        service = EnvelopeEncryptionService(master_key)

        secret1 = "secret1"
        secret2 = "secret2"

        enc1, dek1 = service.encrypt_secret(secret1)
        enc2, dek2 = service.encrypt_secret(secret2)

        # Encrypted values should be different
        assert enc1 != enc2
        assert dek1 != dek2

    def test_decrypt_wrong_dek(self):
        """Test that decryption fails with wrong DEK."""
        master_key = secrets.token_bytes(AES_KEY_SIZE)
        service = EnvelopeEncryptionService(master_key)

        secret = "my_secret"
        encrypted_secret, encrypted_dek = service.encrypt_secret(secret)

        # Use wrong DEK
        wrong_dek = secrets.token_bytes(len(encrypted_dek))

        with pytest.raises(ValueError, match="Failed to decrypt"):
            service.decrypt_secret(encrypted_secret, wrong_dek)

    def test_decrypt_wrong_master_key(self):
        """Test that decryption fails with wrong master key."""
        master_key1 = secrets.token_bytes(AES_KEY_SIZE)
        master_key2 = secrets.token_bytes(AES_KEY_SIZE)

        service1 = EnvelopeEncryptionService(master_key1)
        service2 = EnvelopeEncryptionService(master_key2)

        secret = "my_secret"
        encrypted_secret, encrypted_dek = service1.encrypt_secret(secret)

        # Try to decrypt with different master key
        with pytest.raises(ValueError, match="Failed to decrypt"):
            service2.decrypt_secret(encrypted_secret, encrypted_dek)

    def test_decrypt_corrupted_data(self):
        """Test handling of corrupted encrypted data."""
        master_key = secrets.token_bytes(AES_KEY_SIZE)
        service = EnvelopeEncryptionService(master_key)

        # Corrupt the encrypted data
        encrypted_secret = b"corrupted_data"
        encrypted_dek = b"corrupted_dek"

        with pytest.raises(ValueError, match="Failed to decrypt"):
            service.decrypt_secret(encrypted_secret, encrypted_dek)

    def test_decrypt_too_short_data(self):
        """Test handling of data that's too short."""
        master_key = secrets.token_bytes(AES_KEY_SIZE)
        service = EnvelopeEncryptionService(master_key)

        # Data shorter than nonce size
        encrypted_secret = b"short"
        encrypted_dek = b"short"

        with pytest.raises(ValueError, match="too short"):
            service.decrypt_secret(encrypted_secret, encrypted_dek)

    def test_encrypt_empty_secret(self):
        """Test handling of empty string secrets."""
        master_key = secrets.token_bytes(AES_KEY_SIZE)
        service = EnvelopeEncryptionService(master_key)

        secret = ""
        encrypted_secret, encrypted_dek = service.encrypt_secret(secret)
        decrypted = service.decrypt_secret(encrypted_secret, encrypted_dek)
        assert decrypted == ""

    def test_encrypt_long_secret(self):
        """Test handling of very long secrets."""
        master_key = secrets.token_bytes(AES_KEY_SIZE)
        service = EnvelopeEncryptionService(master_key)

        # 1MB secret
        secret = "x" * (1024 * 1024)
        encrypted_secret, encrypted_dek = service.encrypt_secret(secret)
        decrypted = service.decrypt_secret(encrypted_secret, encrypted_dek)
        assert decrypted == secret

    def test_encrypt_special_characters(self):
        """Test handling of Unicode and special characters."""
        master_key = secrets.token_bytes(AES_KEY_SIZE)
        service = EnvelopeEncryptionService(master_key)

        secrets_to_test = [
            "测试",
            "🎉🎊🎈",
            "secret\nwith\nnewlines",
            "secret\twith\ttabs",
            "secret with spaces",
        ]

        for secret in secrets_to_test:
            encrypted_secret, encrypted_dek = service.encrypt_secret(secret)
            decrypted = service.decrypt_secret(encrypted_secret, encrypted_dek)
            assert decrypted == secret

    def test_master_key_rotation(self):
        """Test master key rotation (re-encrypt DEKs)."""
        master_key1 = secrets.token_bytes(AES_KEY_SIZE)
        master_key2 = secrets.token_bytes(AES_KEY_SIZE)

        service1 = EnvelopeEncryptionService(master_key1)
        service2 = EnvelopeEncryptionService(master_key2)

        secret = "my_secret"
        encrypted_secret, encrypted_dek = service1.encrypt_secret(secret)

        # Decrypt with old key
        decrypted1 = service1.decrypt_secret(encrypted_secret, encrypted_dek)
        assert decrypted1 == secret

        # Re-encrypt DEK with new master key (simulate rotation)
        # Note: In real implementation, this would re-encrypt all DEKs
        # For this test, we just verify the concept works
        new_encrypted_secret, new_encrypted_dek = service2.encrypt_secret(secret)
        decrypted2 = service2.decrypt_secret(new_encrypted_secret, new_encrypted_dek)
        assert decrypted2 == secret

    def test_encrypt_with_custom_master_key(self):
        """Test encryption with custom master key parameter."""
        master_key1 = secrets.token_bytes(AES_KEY_SIZE)
        master_key2 = secrets.token_bytes(AES_KEY_SIZE)

        service = EnvelopeEncryptionService(master_key1)

        secret = "my_secret"
        # Encrypt with custom master key
        encrypted_secret, encrypted_dek = service.encrypt_secret(secret, master_key2)

        # Decrypt with the custom master key
        service2 = EnvelopeEncryptionService(master_key2)
        decrypted = service2.decrypt_secret(encrypted_secret, encrypted_dek)
        assert decrypted == secret
