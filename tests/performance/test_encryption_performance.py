"""
Performance tests for encryption operations.

Ensures encryption/decryption operations meet performance targets.
"""

import time

import pytest

from mdb_engine.core.encryption import EnvelopeEncryptionService


@pytest.fixture
def encryption_service():
    """Create encryption service with test master key."""
    master_key = EnvelopeEncryptionService.generate_master_key()
    import base64

    key_bytes = base64.b64decode(master_key.encode())
    return EnvelopeEncryptionService(key_bytes)


class TestEncryptionPerformance:
    """Test encryption performance targets."""

    def test_encrypt_performance(self, encryption_service):
        """Test encryption performance (target: < 10ms)."""
        secret = "test_secret_token_12345"

        start = time.perf_counter()
        encrypted_secret, encrypted_dek = encryption_service.encrypt_secret(secret)
        elapsed = (time.perf_counter() - start) * 1000  # Convert to milliseconds

        assert elapsed < 10, f"Encryption took {elapsed:.2f}ms, target is < 10ms"
        assert encrypted_secret is not None
        assert encrypted_dek is not None

    def test_decrypt_performance(self, encryption_service):
        """Test decryption performance (target: < 10ms)."""
        secret = "test_secret_token_12345"
        encrypted_secret, encrypted_dek = encryption_service.encrypt_secret(secret)

        start = time.perf_counter()
        decrypted = encryption_service.decrypt_secret(encrypted_secret, encrypted_dek)
        elapsed = (time.perf_counter() - start) * 1000  # Convert to milliseconds

        assert elapsed < 10, f"Decryption took {elapsed:.2f}ms, target is < 10ms"
        assert decrypted == secret

    def test_encrypt_decrypt_roundtrip_performance(self, encryption_service):
        """Test full encrypt/decrypt roundtrip performance."""
        secret = "test_secret_token_12345"

        start = time.perf_counter()
        encrypted_secret, encrypted_dek = encryption_service.encrypt_secret(secret)
        decrypted = encryption_service.decrypt_secret(encrypted_secret, encrypted_dek)
        elapsed = (time.perf_counter() - start) * 1000  # Convert to milliseconds

        assert elapsed < 20, f"Roundtrip took {elapsed:.2f}ms, target is < 20ms"
        assert decrypted == secret

    def test_concurrent_encryption(self, encryption_service):
        """Test concurrent encryption operations."""
        import concurrent.futures

        secrets = [f"secret_{i}" for i in range(100)]

        start = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [
                executor.submit(encryption_service.encrypt_secret, secret) for secret in secrets
            ]
            results = [future.result() for future in concurrent.futures.as_completed(futures)]
        elapsed = (time.perf_counter() - start) * 1000

        # Average should be < 10ms per operation
        avg_time = elapsed / len(secrets)
        assert avg_time < 10, f"Average encryption time: {avg_time:.2f}ms, target is < 10ms"
        assert len(results) == len(secrets)

    def test_concurrent_decryption(self, encryption_service):
        """Test concurrent decryption operations."""
        import concurrent.futures

        # Pre-encrypt secrets
        encrypted_pairs = [encryption_service.encrypt_secret(f"secret_{i}") for i in range(100)]

        start = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [
                executor.submit(encryption_service.decrypt_secret, encrypted_secret, encrypted_dek)
                for encrypted_secret, encrypted_dek in encrypted_pairs
            ]
            results = [future.result() for future in concurrent.futures.as_completed(futures)]
        elapsed = (time.perf_counter() - start) * 1000

        # Average should be < 10ms per operation
        avg_time = elapsed / len(encrypted_pairs)
        assert avg_time < 10, f"Average decryption time: {avg_time:.2f}ms, target is < 10ms"
        assert len(results) == len(encrypted_pairs)
