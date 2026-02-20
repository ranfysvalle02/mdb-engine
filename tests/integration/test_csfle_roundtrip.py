"""
Integration tests for Client-Side Field Level Encryption (CSFLE) round-trip.

Verifies that documents encrypted via CSFLE are:
- Readable through an encrypted client (transparent decryption)
- NOT readable as plaintext through a raw client (data at rest is encrypted)
"""

from __future__ import annotations

import base64

import pytest

pymongocrypt = pytest.importorskip("pymongocrypt")

from bson import Binary  # noqa: E402
from bson.binary import STANDARD  # noqa: E402
from bson.codec_options import CodecOptions  # noqa: E402
from pymongo import MongoClient  # noqa: E402
from pymongo.encryption import ClientEncryption  # noqa: E402
from pymongo.encryption_options import AutoEncryptionOpts  # noqa: E402

from mdb_engine.core.csfle import CSFLEConfig, generate_local_master_key  # noqa: E402

DB_NAME = "test_csfle_roundtrip"
KEY_VAULT_NAMESPACE = "encryption.__keyVault"
PATIENTS_COLLECTION = "patients"


def _is_csfle_runtime_error(exc: Exception) -> bool:
    """Return True if the exception signals missing mongocryptd / crypt_shared."""
    msg = str(exc).lower()
    return "mongocryptd" in msg or "crypt_shared" in msg or "mongocrypt" in msg


@pytest.mark.integration
class TestCSFLERoundTrip:
    """Test CSFLE encrypt/decrypt round-trip against a real MongoDB instance."""

    @pytest.fixture(autouse=True)
    def setup_csfle(self, mongodb_connection_string):
        """Generate a local master key, create a data-encryption key, and build AutoEncryptionOpts."""
        self.conn_str = mongodb_connection_string

        # 1. Generate local master key
        key_b64 = generate_local_master_key()
        local_key_bytes = base64.b64decode(key_b64)
        self.kms_providers = {"local": {"key": local_key_bytes}}

        # 2. CSFLEConfig for a "patients" collection (ssn + diagnosis encrypted)
        self.csfle_config = CSFLEConfig(
            enabled=True,
            kms_provider="local",
            key_vault_namespace=KEY_VAULT_NAMESPACE,
            encrypted_collections={PATIENTS_COLLECTION: ["ssn", "diagnosis"]},
        )

        # 3. Create the data-encryption key in the key vault
        setup_client = MongoClient(self.conn_str)
        try:
            enc = ClientEncryption(
                self.kms_providers,
                KEY_VAULT_NAMESPACE,
                setup_client,
                CodecOptions(uuid_representation=STANDARD),
            )
            try:
                self.data_key_id = enc.create_data_key("local", key_alt_names=["patients_key"])
            except Exception as exc:
                if _is_csfle_runtime_error(exc):
                    pytest.skip(f"CSFLE runtime unavailable: {exc}")
                raise
            finally:
                enc.close()
        finally:
            setup_client.close()

        # 4. Schema map tells the driver which fields to auto-encrypt
        self.schema_map = {
            f"{DB_NAME}.{PATIENTS_COLLECTION}": {
                "bsonType": "object",
                "properties": {
                    "ssn": {
                        "encrypt": {
                            "bsonType": "string",
                            "algorithm": "AEAD_AES_256_CBC_HMAC_SHA_512-Random",
                            "keyId": [self.data_key_id],
                        }
                    },
                    "diagnosis": {
                        "encrypt": {
                            "bsonType": "string",
                            "algorithm": "AEAD_AES_256_CBC_HMAC_SHA_512-Random",
                            "keyId": [self.data_key_id],
                        }
                    },
                },
            }
        }

        # 5. Build AutoEncryptionOpts (may fail if native libs are missing)
        try:
            self.auto_enc_opts = AutoEncryptionOpts(
                kms_providers=self.kms_providers,
                key_vault_namespace=KEY_VAULT_NAMESPACE,
                schema_map=self.schema_map,
            )
        except Exception as exc:
            if _is_csfle_runtime_error(exc):
                pytest.skip(f"CSFLE runtime unavailable: {exc}")
            raise

        yield

        # Cleanup
        cleanup = MongoClient(self.conn_str)
        try:
            cleanup.drop_database(DB_NAME)
            cleanup.drop_database("encryption")
        except Exception:
            pass
        finally:
            cleanup.close()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _encrypted_client(self) -> MongoClient:
        """Return a MongoClient configured for automatic CSFLE."""
        return MongoClient(self.conn_str, auto_encryption_opts=self.auto_enc_opts)

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------
    def test_encrypted_client_reads_plaintext(self):
        """Encrypted client transparently decrypts fields on read."""
        client = self._encrypted_client()
        try:
            coll = client[DB_NAME][PATIENTS_COLLECTION]
            doc = {
                "name": "Jane Doe",
                "ssn": "123-45-6789",
                "diagnosis": "Hypertension",
                "age": 42,
            }
            rid = coll.insert_one(doc).inserted_id

            found = coll.find_one({"_id": rid})
            assert found is not None
            assert found["name"] == "Jane Doe"
            assert found["ssn"] == "123-45-6789"
            assert found["diagnosis"] == "Hypertension"
            assert found["age"] == 42
        except Exception as exc:
            if _is_csfle_runtime_error(exc):
                pytest.skip(f"CSFLE runtime unavailable: {exc}")
            raise
        finally:
            client.close()

    def test_raw_client_cannot_read_encrypted_fields(self):
        """Raw client sees Binary blobs instead of plaintext for encrypted fields."""
        enc_client = self._encrypted_client()
        try:
            coll = enc_client[DB_NAME][PATIENTS_COLLECTION]
            doc = {
                "name": "John Smith",
                "ssn": "987-65-4321",
                "diagnosis": "Diabetes",
                "age": 55,
            }
            inserted_id = coll.insert_one(doc).inserted_id
        except Exception as exc:
            if _is_csfle_runtime_error(exc):
                pytest.skip(f"CSFLE runtime unavailable: {exc}")
            raise
        finally:
            enc_client.close()

        raw = MongoClient(self.conn_str)
        try:
            raw_doc = raw[DB_NAME][PATIENTS_COLLECTION].find_one({"_id": inserted_id})
            assert raw_doc is not None

            # Unencrypted fields are still readable as-is
            assert raw_doc["name"] == "John Smith"
            assert raw_doc["age"] == 55

            # Encrypted fields must NOT match their original plaintext
            assert raw_doc["ssn"] != "987-65-4321"
            assert raw_doc["diagnosis"] != "Diabetes"
            assert isinstance(raw_doc["ssn"], Binary | bytes)
            assert isinstance(raw_doc["diagnosis"], Binary | bytes)
        finally:
            raw.close()

    def test_csfle_config_maps_fields_correctly(self):
        """CSFLEConfig.from_encrypted_fields produces the expected encrypted_collections."""
        config = CSFLEConfig.from_encrypted_fields(
            {"patients": ["ssn", "diagnosis"]},
            {"kms_provider": "local"},
            app_slug="myapp",
        )
        assert config.enabled is True
        assert config.kms_provider == "local"
        assert "myapp_patients" in config.encrypted_collections
        assert set(config.encrypted_collections["myapp_patients"]) == {"ssn", "diagnosis"}
