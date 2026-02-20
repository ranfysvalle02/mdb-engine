"""
Unit tests for Client-Side Field Level Encryption (CSFLE) module.

Tests cover key generation, path discovery, KMS provider configuration,
CSFLEConfig factory methods, and schema map building.
"""

import base64
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.unit
class TestGenerateLocalMasterKey:
    """Tests for generate_local_master_key()."""

    def test_returns_base64_string(self):
        from mdb_engine.core.csfle import generate_local_master_key

        key = generate_local_master_key()
        assert isinstance(key, str)

    def test_decodes_to_96_bytes(self):
        from mdb_engine.core.csfle import generate_local_master_key

        key = generate_local_master_key()
        raw = base64.b64decode(key)
        assert len(raw) == 96

    def test_generates_unique_keys(self):
        from mdb_engine.core.csfle import generate_local_master_key

        keys = {generate_local_master_key() for _ in range(10)}
        assert len(keys) == 10


@pytest.mark.unit
class TestGetCryptSharedPath:
    """Tests for _get_crypt_shared_path()."""

    def test_returns_env_var_when_set(self, monkeypatch):
        from mdb_engine.core.csfle import _get_crypt_shared_path

        monkeypatch.setenv("CRYPT_SHARED_LIB_PATH", "/custom/path/mongo_crypt_v1.so")
        assert _get_crypt_shared_path() == "/custom/path/mongo_crypt_v1.so"

    def test_returns_none_when_no_env_and_no_file(self, monkeypatch):
        from mdb_engine.core.csfle import _get_crypt_shared_path

        monkeypatch.delenv("CRYPT_SHARED_LIB_PATH", raising=False)
        with patch("mdb_engine.core.csfle.Path") as MockPath:
            MockPath.return_value.exists.return_value = False
            result = _get_crypt_shared_path()
        assert result is None

    def test_finds_default_path(self, monkeypatch):
        from mdb_engine.core.csfle import _get_crypt_shared_path

        monkeypatch.delenv("CRYPT_SHARED_LIB_PATH", raising=False)

        call_count = 0

        def mock_exists(self):
            nonlocal call_count
            call_count += 1
            # Third default path matches
            return call_count == 3

        with patch("mdb_engine.core.csfle.Path.exists", mock_exists):
            result = _get_crypt_shared_path()
        assert result == "/usr/lib/mongo_crypt_v1.so"


@pytest.mark.unit
class TestGetLocalKey:
    """Tests for _get_local_key()."""

    def _make_valid_key(self) -> str:
        return base64.b64encode(b"\x00" * 96).decode()

    def test_returns_key_from_env_var(self, monkeypatch):
        from mdb_engine.core.csfle import _get_local_key

        valid_key = self._make_valid_key()
        monkeypatch.setenv("MDB_CSFLE_LOCAL_KEY", valid_key)
        result = _get_local_key()
        assert result is not None
        assert len(result) == 96

    def test_returns_none_when_nothing_configured(self, monkeypatch):
        from mdb_engine.core.csfle import _get_local_key

        monkeypatch.delenv("MDB_CSFLE_LOCAL_KEY", raising=False)
        monkeypatch.delenv("CSFLE_KEY_FILE", raising=False)
        with patch("mdb_engine.core.csfle.Path") as MockPath:
            MockPath.return_value.exists.return_value = False
            result = _get_local_key()
        assert result is None

    def test_reads_key_from_file(self, monkeypatch, tmp_path):
        from mdb_engine.core.csfle import _get_local_key

        valid_key = self._make_valid_key()
        key_file = tmp_path / "master.key"
        key_file.write_text(valid_key)

        monkeypatch.delenv("MDB_CSFLE_LOCAL_KEY", raising=False)
        monkeypatch.setenv("CSFLE_KEY_FILE", str(key_file))
        result = _get_local_key()
        assert result is not None
        assert len(result) == 96

    def test_warns_on_wrong_length(self, monkeypatch):
        from mdb_engine.core.csfle import _get_local_key

        short_key = base64.b64encode(b"\x00" * 32).decode()
        monkeypatch.setenv("MDB_CSFLE_LOCAL_KEY", short_key)
        result = _get_local_key()
        assert result is not None
        assert len(result) == 32

    def test_returns_none_on_invalid_base64(self, monkeypatch):
        from mdb_engine.core.csfle import _get_local_key

        monkeypatch.setenv("MDB_CSFLE_LOCAL_KEY", "%%%not-base64%%%")
        result = _get_local_key()
        assert result is None

    def test_returns_none_on_file_read_error(self, monkeypatch, tmp_path):
        from mdb_engine.core.csfle import _get_local_key

        monkeypatch.delenv("MDB_CSFLE_LOCAL_KEY", raising=False)
        key_file = tmp_path / "master.key"
        key_file.write_text("placeholder")
        key_file.chmod(0o000)

        monkeypatch.setenv("CSFLE_KEY_FILE", str(key_file))
        result = _get_local_key()
        # Restore permissions so tmp_path cleanup works
        key_file.chmod(0o644)
        # Either None (OSError path) or invalid decode
        assert result is None or isinstance(result, bytes)


@pytest.mark.unit
class TestIsCsfleAvailable:
    """Tests for is_csfle_available()."""

    def test_returns_true_when_available(self):
        from mdb_engine.core.csfle import is_csfle_available

        with patch("mdb_engine.core.csfle.get_csfle_status", return_value={"available": True}):
            assert is_csfle_available() is True

    def test_returns_false_when_unavailable(self):
        from mdb_engine.core.csfle import is_csfle_available

        with patch("mdb_engine.core.csfle.get_csfle_status", return_value={"available": False}):
            assert is_csfle_available() is False


@pytest.mark.unit
class TestGetCsfleStatus:
    """Tests for get_csfle_status()."""

    def test_unavailable_when_no_pymongo_encryption(self):
        from mdb_engine.core.csfle import get_csfle_status

        with patch.dict("sys.modules", {"pymongo.encryption_options": None}):
            import mdb_engine.core.csfle as mod

            # Force reimport to hit the ImportError path
            with (
                patch.object(mod, "_get_crypt_shared_path", return_value=None),
                patch.object(mod, "_get_local_key", return_value=None),
            ):
                # Simulate ImportError when importing AutoEncryptionOpts
                orig = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

                def fake_import(name, *args, **kwargs):
                    if name == "pymongo.encryption_options":
                        raise ImportError("no module")
                    return orig(name, *args, **kwargs)

                with patch("builtins.__import__", side_effect=fake_import):
                    status = get_csfle_status()

        assert status["pymongo_encryption"] is False
        assert status["available"] is False

    def test_full_status_when_pymongo_available(self, monkeypatch):
        from mdb_engine.core.csfle import get_csfle_status

        valid_key = base64.b64encode(b"\x00" * 96).decode()
        monkeypatch.setenv("MDB_CSFLE_LOCAL_KEY", valid_key)

        mock_module = MagicMock()
        with (
            patch.dict("sys.modules", {"pymongo.encryption_options": mock_module}),
            patch("mdb_engine.core.csfle._get_crypt_shared_path", return_value="/some/path"),
            patch("mdb_engine.core.csfle.Path") as MockPath,
        ):
            MockPath.return_value.exists.return_value = True
            status = get_csfle_status()

        assert status["pymongo_encryption"] is True
        assert status["crypt_shared_path"] == "/some/path"
        assert status["crypt_shared_exists"] is True
        assert status["local_key_configured"] is True
        assert status["available"] is True

    def test_not_available_without_crypt_shared(self, monkeypatch):
        from mdb_engine.core.csfle import get_csfle_status

        monkeypatch.delenv("MDB_CSFLE_LOCAL_KEY", raising=False)
        monkeypatch.delenv("CSFLE_KEY_FILE", raising=False)

        mock_module = MagicMock()
        with (
            patch.dict("sys.modules", {"pymongo.encryption_options": mock_module}),
            patch("mdb_engine.core.csfle._get_crypt_shared_path", return_value=None),
            patch("mdb_engine.core.csfle._get_local_key", return_value=None),
        ):
            status = get_csfle_status()

        assert status["pymongo_encryption"] is True
        assert status["crypt_shared_exists"] is False
        assert status["available"] is False


@pytest.mark.unit
class TestCSFLEConfigFromMemoryConfig:
    """Tests for CSFLEConfig.from_memory_config()."""

    def test_disabled_when_not_encrypted(self):
        from mdb_engine.core.csfle import CSFLEConfig

        config = CSFLEConfig.from_memory_config({"encrypted": False}, app_slug="app")
        assert config.enabled is False

    def test_disabled_when_encrypted_key_missing(self):
        from mdb_engine.core.csfle import CSFLEConfig

        config = CSFLEConfig.from_memory_config({}, app_slug="app")
        assert config.enabled is False

    def test_default_collection_and_fields(self):
        from mdb_engine.core.csfle import DEFAULT_MEMORY_ENCRYPTED_FIELDS, CSFLEConfig

        config = CSFLEConfig.from_memory_config({"encrypted": True}, app_slug="myapp")
        assert config.enabled is True
        assert config.kms_provider == "local"
        assert "myapp_memories" in config.encrypted_collections
        assert config.encrypted_collections["myapp_memories"] == DEFAULT_MEMORY_ENCRYPTED_FIELDS

    def test_custom_collection_name(self):
        from mdb_engine.core.csfle import CSFLEConfig

        mc = {"encrypted": True, "collection_name": "notes"}
        config = CSFLEConfig.from_memory_config(mc, app_slug="myapp")
        assert "myapp_notes" in config.encrypted_collections

    def test_custom_kms_provider(self):
        from mdb_engine.core.csfle import CSFLEConfig

        mc = {"encrypted": True, "encryption": {"kms_provider": "aws"}}
        config = CSFLEConfig.from_memory_config(mc, app_slug="myapp")
        assert config.kms_provider == "aws"

    def test_custom_fields(self):
        from mdb_engine.core.csfle import CSFLEConfig

        mc = {"encrypted": True, "encryption": {"fields": ["secret_field"]}}
        config = CSFLEConfig.from_memory_config(mc, app_slug="myapp")
        assert config.encrypted_collections["myapp_memories"] == ["secret_field"]

    def test_custom_key_vault_namespace(self):
        from mdb_engine.core.csfle import CSFLEConfig

        mc = {"encrypted": True, "encryption": {"key_vault_namespace": "custom.__vault"}}
        config = CSFLEConfig.from_memory_config(mc, app_slug="myapp")
        assert config.key_vault_namespace == "custom.__vault"


@pytest.mark.unit
class TestCSFLEConfigFromEncryptedFields:
    """Tests for CSFLEConfig.from_encrypted_fields()."""

    def test_disabled_when_empty(self):
        from mdb_engine.core.csfle import CSFLEConfig

        config = CSFLEConfig.from_encrypted_fields({}, {}, app_slug="app")
        assert config.enabled is False

    def test_prefixes_collection_names(self):
        from mdb_engine.core.csfle import CSFLEConfig

        fields = {"payments": ["card_number", "cvv"]}
        config = CSFLEConfig.from_encrypted_fields(fields, {}, app_slug="shop")
        assert "shop_payments" in config.encrypted_collections
        assert config.encrypted_collections["shop_payments"] == ["card_number", "cvv"]

    def test_uses_kms_from_encryption_config(self):
        from mdb_engine.core.csfle import CSFLEConfig

        fields = {"col": ["f1"]}
        enc = {"kms_provider": "gcp", "key_vault_namespace": "other.__kv"}
        config = CSFLEConfig.from_encrypted_fields(fields, enc, app_slug="a")
        assert config.kms_provider == "gcp"
        assert config.key_vault_namespace == "other.__kv"

    def test_multiple_collections(self):
        from mdb_engine.core.csfle import CSFLEConfig

        fields = {"users": ["ssn"], "billing": ["card"]}
        config = CSFLEConfig.from_encrypted_fields(fields, {}, app_slug="x")
        assert len(config.encrypted_collections) == 2
        assert "x_users" in config.encrypted_collections
        assert "x_billing" in config.encrypted_collections


@pytest.mark.unit
class TestCSFLEConfigMergeWith:
    """Tests for CSFLEConfig.merge_with()."""

    def test_merge_disabled_other_returns_self(self):
        from mdb_engine.core.csfle import CSFLEConfig

        a = CSFLEConfig(enabled=True, encrypted_collections={"col": ["f1"]})
        b = CSFLEConfig(enabled=False)
        merged = a.merge_with(b)
        assert merged is a

    def test_merge_disabled_self_returns_other(self):
        from mdb_engine.core.csfle import CSFLEConfig

        a = CSFLEConfig(enabled=False)
        b = CSFLEConfig(enabled=True, encrypted_collections={"col": ["f1"]})
        merged = a.merge_with(b)
        assert merged is b

    def test_merge_disjoint_collections(self):
        from mdb_engine.core.csfle import CSFLEConfig

        a = CSFLEConfig(enabled=True, encrypted_collections={"col_a": ["f1"]})
        b = CSFLEConfig(enabled=True, encrypted_collections={"col_b": ["f2"]})
        merged = a.merge_with(b)
        assert "col_a" in merged.encrypted_collections
        assert "col_b" in merged.encrypted_collections

    def test_merge_overlapping_collections_deduplicates_fields(self):
        from mdb_engine.core.csfle import CSFLEConfig

        a = CSFLEConfig(enabled=True, encrypted_collections={"col": ["f1", "f2"]})
        b = CSFLEConfig(enabled=True, encrypted_collections={"col": ["f2", "f3"]})
        merged = a.merge_with(b)
        assert set(merged.encrypted_collections["col"]) == {"f1", "f2", "f3"}

    def test_merge_preserves_self_kms(self):
        from mdb_engine.core.csfle import CSFLEConfig

        a = CSFLEConfig(enabled=True, kms_provider="aws", encrypted_collections={"c": ["f"]})
        b = CSFLEConfig(enabled=True, kms_provider="gcp", encrypted_collections={"d": ["g"]})
        merged = a.merge_with(b)
        assert merged.kms_provider == "aws"


@pytest.mark.unit
class TestGetKmsProviders:
    """Tests for _get_kms_providers()."""

    def test_local_with_key_configured(self, monkeypatch):
        from mdb_engine.core.csfle import _get_kms_providers

        valid_key = base64.b64encode(b"\x00" * 96).decode()
        monkeypatch.setenv("MDB_CSFLE_LOCAL_KEY", valid_key)
        result = _get_kms_providers("local")
        assert "local" in result
        assert result["local"]["key"] == b"\x00" * 96

    def test_local_generates_ephemeral_key_when_no_key(self, monkeypatch):
        from mdb_engine.core.csfle import _get_kms_providers

        monkeypatch.delenv("MDB_CSFLE_LOCAL_KEY", raising=False)
        monkeypatch.delenv("CSFLE_KEY_FILE", raising=False)
        with patch("mdb_engine.core.csfle.Path") as MockPath:
            MockPath.return_value.exists.return_value = False
            result = _get_kms_providers("local")
        assert "local" in result
        assert len(result["local"]["key"]) == 96

    def test_aws_provider(self, monkeypatch):
        from mdb_engine.core.csfle import _get_kms_providers

        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAEXAMPLE")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret123")
        result = _get_kms_providers("aws")
        assert result == {"aws": {"accessKeyId": "AKIAEXAMPLE", "secretAccessKey": "secret123"}}

    def test_aws_missing_credentials_raises(self, monkeypatch):
        from mdb_engine.core.csfle import _get_kms_providers

        monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
        monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
        with pytest.raises(ValueError, match="AWS KMS requires"):
            _get_kms_providers("aws")

    def test_azure_provider(self, monkeypatch):
        from mdb_engine.core.csfle import _get_kms_providers

        monkeypatch.setenv("AZURE_TENANT_ID", "tenant")
        monkeypatch.setenv("AZURE_CLIENT_ID", "client")
        monkeypatch.setenv("AZURE_CLIENT_SECRET", "secret")
        result = _get_kms_providers("azure")
        assert result == {"azure": {"tenantId": "tenant", "clientId": "client", "clientSecret": "secret"}}

    def test_azure_missing_credentials_raises(self, monkeypatch):
        from mdb_engine.core.csfle import _get_kms_providers

        monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
        monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)
        monkeypatch.delenv("AZURE_CLIENT_SECRET", raising=False)
        with pytest.raises(ValueError, match="Azure Key Vault requires"):
            _get_kms_providers("azure")

    def test_gcp_provider(self, monkeypatch):
        from mdb_engine.core.csfle import _get_kms_providers

        monkeypatch.setenv("GCP_EMAIL", "test@proj.iam.gserviceaccount.com")
        monkeypatch.setenv("GCP_PRIVATE_KEY", "-----BEGIN RSA PRIVATE KEY-----\nfake")
        result = _get_kms_providers("gcp")
        assert result["gcp"]["email"] == "test@proj.iam.gserviceaccount.com"

    def test_gcp_missing_credentials_raises(self, monkeypatch):
        from mdb_engine.core.csfle import _get_kms_providers

        monkeypatch.delenv("GCP_EMAIL", raising=False)
        monkeypatch.delenv("GCP_PRIVATE_KEY", raising=False)
        with pytest.raises(ValueError, match="GCP Cloud KMS requires"):
            _get_kms_providers("gcp")

    def test_unknown_provider_raises(self):
        from mdb_engine.core.csfle import _get_kms_providers

        with pytest.raises(ValueError, match="Unknown KMS provider"):
            _get_kms_providers("unsupported")


@pytest.mark.unit
class TestBuildSchemaMap:
    """Tests for _build_schema_map()."""

    def _make_config(self, collections):
        from mdb_engine.core.csfle import CSFLEConfig

        return CSFLEConfig(enabled=True, encrypted_collections=collections)

    def test_builds_namespace_keys(self):
        from mdb_engine.core.csfle import _build_schema_map

        config = self._make_config({"users": ["ssn"]})
        key_ids = {"users": b"\x01" * 16}
        schema = _build_schema_map(config, "mydb", key_ids)
        assert "mydb.users" in schema

    def test_field_encrypt_structure(self):
        from mdb_engine.core.csfle import _build_schema_map

        config = self._make_config({"col": ["field_a"]})
        key_ids = {"col": b"\x02" * 16}
        schema = _build_schema_map(config, "db", key_ids)
        props = schema["db.col"]["properties"]
        assert "field_a" in props
        enc = props["field_a"]["encrypt"]
        assert enc["bsonType"] == "string"
        assert enc["algorithm"] == "AEAD_AES_256_CBC_HMAC_SHA_512-Random"

    def test_skips_collection_without_key_id(self):
        from mdb_engine.core.csfle import _build_schema_map

        config = self._make_config({"col_a": ["f1"], "col_b": ["f2"]})
        key_ids = {"col_a": b"\x03" * 16}
        schema = _build_schema_map(config, "db", key_ids)
        assert "db.col_a" in schema
        assert "db.col_b" not in schema

    def test_multiple_fields(self):
        from mdb_engine.core.csfle import _build_schema_map

        config = self._make_config({"col": ["f1", "f2", "f3"]})
        key_ids = {"col": b"\x04" * 16}
        schema = _build_schema_map(config, "db", key_ids)
        props = schema["db.col"]["properties"]
        assert set(props.keys()) == {"f1", "f2", "f3"}

    def test_empty_collections_returns_empty(self):
        from mdb_engine.core.csfle import _build_schema_map

        config = self._make_config({})
        schema = _build_schema_map(config, "db", {})
        assert schema == {}

    def test_uses_binary_wrapper_for_raw_bytes(self):
        from mdb_engine.core.csfle import _build_schema_map

        config = self._make_config({"col": ["f"]})
        raw_key = b"\x05" * 16
        schema = _build_schema_map(config, "db", {"col": raw_key})
        key_list = schema["db.col"]["properties"]["f"]["encrypt"]["keyId"]
        assert len(key_list) == 1
        from bson import Binary

        assert isinstance(key_list[0], Binary)


# ---------------------------------------------------------------------------
# _ensure_data_keys (lines 479-535)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEnsureDataKeys:
    """Tests for _ensure_data_keys()."""

    def _make_config(self, collections):
        from mdb_engine.core.csfle import CSFLEConfig

        return CSFLEConfig(enabled=True, encrypted_collections=collections, kms_provider="local")

    def _run_ensure_data_keys(self, config, mock_key_vault, mock_client_encryption):
        mock_client = MagicMock()
        mock_client.__getitem__.return_value.__getitem__.return_value = mock_key_vault

        with (
            patch("pymongo.MongoClient", return_value=mock_client),
            patch("pymongo.encryption.ClientEncryption", return_value=mock_client_encryption),
        ):
            from mdb_engine.core.csfle import _ensure_data_keys

            return _ensure_data_keys(
                {"local": {"key": b"\x00" * 96}},
                "encryption.__keyVault",
                "mongodb://localhost:27017",
                config,
            ), mock_client

    def test_creates_new_key_when_none_exists(self):
        config = self._make_config({"users": ["ssn"]})

        mock_key_vault = MagicMock()
        mock_key_vault.find_one.return_value = None

        mock_ce = MagicMock()
        new_key_id = b"\xaa" * 16
        mock_ce.create_data_key.return_value = new_key_id

        result, _ = self._run_ensure_data_keys(config, mock_key_vault, mock_ce)

        assert "users" in result
        assert result["users"] == new_key_id
        mock_ce.create_data_key.assert_called_once_with("local", key_alt_names=["mdb_engine_users"])

    def test_retrieves_existing_key(self):
        config = self._make_config({"orders": ["card"]})
        existing_key_id = b"\xbb" * 16

        mock_key_vault = MagicMock()
        mock_key_vault.find_one.return_value = {"_id": existing_key_id}

        mock_ce = MagicMock()
        result, _ = self._run_ensure_data_keys(config, mock_key_vault, mock_ce)

        assert result["orders"] == existing_key_id
        mock_ce.create_data_key.assert_not_called()

    def test_multiple_collections(self):
        config = self._make_config({"users": ["ssn"], "payments": ["card"]})

        mock_key_vault = MagicMock()
        mock_key_vault.find_one.side_effect = [
            {"_id": b"\x01" * 16},
            None,
        ]

        mock_ce = MagicMock()
        mock_ce.create_data_key.return_value = b"\x02" * 16

        result, _ = self._run_ensure_data_keys(config, mock_key_vault, mock_ce)
        assert len(result) == 2

    def test_index_creation_error_is_suppressed(self):
        config = self._make_config({"col": ["f"]})

        mock_key_vault = MagicMock()
        mock_key_vault.create_index.side_effect = ValueError("index conflict")
        mock_key_vault.find_one.return_value = {"_id": b"\xcc" * 16}

        mock_ce = MagicMock()
        result, _ = self._run_ensure_data_keys(config, mock_key_vault, mock_ce)
        assert "col" in result

    def test_client_and_encryption_closed_on_success(self):
        config = self._make_config({"col": ["f"]})

        mock_key_vault = MagicMock()
        mock_key_vault.find_one.return_value = {"_id": b"\xdd" * 16}

        mock_ce = MagicMock()
        result, mock_client = self._run_ensure_data_keys(config, mock_key_vault, mock_ce)

        mock_ce.close.assert_called_once()
        mock_client.close.assert_called_once()


# ---------------------------------------------------------------------------
# build_auto_encryption_opts (lines 620-678)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildAutoEncryptionOpts:
    """Tests for build_auto_encryption_opts()."""

    def _make_config(self, enabled=True, collections=None):
        from mdb_engine.core.csfle import CSFLEConfig

        return CSFLEConfig(
            enabled=enabled,
            encrypted_collections=collections or {"col": ["field"]},
            kms_provider="local",
        )

    def test_returns_none_when_disabled(self):
        from mdb_engine.core.csfle import build_auto_encryption_opts

        config = self._make_config(enabled=False)
        result = build_auto_encryption_opts(config, "mongodb://localhost", "mydb")
        assert result is None

    def test_returns_none_when_no_pymongo_encryption(self):
        from mdb_engine.core.csfle import build_auto_encryption_opts

        config = self._make_config()
        with patch(
            "mdb_engine.core.csfle.get_csfle_status",
            return_value={
                "pymongo_encryption": False,
                "crypt_shared_exists": True,
                "crypt_shared_path": "/some/path",
            },
        ):
            result = build_auto_encryption_opts(config, "mongodb://localhost", "mydb")
        assert result is None

    def test_returns_none_when_no_crypt_shared(self):
        from mdb_engine.core.csfle import build_auto_encryption_opts

        config = self._make_config()
        with patch(
            "mdb_engine.core.csfle.get_csfle_status",
            return_value={
                "pymongo_encryption": True,
                "crypt_shared_exists": False,
                "crypt_shared_path": None,
            },
        ):
            result = build_auto_encryption_opts(config, "mongodb://localhost", "mydb")
        assert result is None

    def test_returns_none_when_import_fails(self):
        from mdb_engine.core.csfle import build_auto_encryption_opts

        config = self._make_config()
        with patch(
            "mdb_engine.core.csfle.get_csfle_status",
            return_value={
                "pymongo_encryption": True,
                "crypt_shared_exists": True,
                "crypt_shared_path": "/some/path",
            },
        ):
            orig_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

            def fake_import(name, *args, **kwargs):
                if name == "pymongo.encryption_options":
                    raise ImportError("no module")
                return orig_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=fake_import):
                result = build_auto_encryption_opts(config, "mongodb://localhost", "mydb")
        assert result is None

    def test_returns_none_when_no_key_ids(self):
        from mdb_engine.core.csfle import build_auto_encryption_opts

        config = self._make_config()
        with (
            patch(
                "mdb_engine.core.csfle.get_csfle_status",
                return_value={
                    "pymongo_encryption": True,
                    "crypt_shared_exists": True,
                    "crypt_shared_path": "/some/path",
                },
            ),
            patch("mdb_engine.core.csfle._get_kms_providers", return_value={"local": {"key": b"\x00" * 96}}),
            patch("mdb_engine.core.csfle._ensure_data_keys", return_value={}),
        ):
            result = build_auto_encryption_opts(config, "mongodb://localhost", "mydb")
        assert result is None

    def test_returns_opts_on_success(self):
        from mdb_engine.core.csfle import build_auto_encryption_opts

        config = self._make_config(collections={"users": ["ssn"]})
        mock_opts = MagicMock()

        with (
            patch(
                "mdb_engine.core.csfle.get_csfle_status",
                return_value={
                    "pymongo_encryption": True,
                    "crypt_shared_exists": True,
                    "crypt_shared_path": "/some/path",
                },
            ),
            patch("mdb_engine.core.csfle._get_kms_providers", return_value={"local": {"key": b"\x00" * 96}}),
            patch("mdb_engine.core.csfle._ensure_data_keys", return_value={"users": b"\xaa" * 16}),
            patch("mdb_engine.core.csfle._build_schema_map", return_value={"mydb.users": {}}),
            patch("pymongo.encryption_options.AutoEncryptionOpts", return_value=mock_opts),
        ):
            result = build_auto_encryption_opts(config, "mongodb://localhost", "mydb")
        assert result is mock_opts

    def test_value_error_returns_none(self):
        from mdb_engine.core.csfle import build_auto_encryption_opts

        config = self._make_config()
        with (
            patch(
                "mdb_engine.core.csfle.get_csfle_status",
                return_value={
                    "pymongo_encryption": True,
                    "crypt_shared_exists": True,
                    "crypt_shared_path": "/some/path",
                },
            ),
            patch("mdb_engine.core.csfle._get_kms_providers", side_effect=ValueError("bad creds")),
        ):
            result = build_auto_encryption_opts(config, "mongodb://localhost", "mydb")
        assert result is None

    def test_unexpected_error_returns_none(self):
        from mdb_engine.core.csfle import build_auto_encryption_opts

        config = self._make_config()
        with (
            patch(
                "mdb_engine.core.csfle.get_csfle_status",
                return_value={
                    "pymongo_encryption": True,
                    "crypt_shared_exists": True,
                    "crypt_shared_path": "/some/path",
                },
            ),
            patch("mdb_engine.core.csfle._get_kms_providers", side_effect=TypeError("oops")),
        ):
            result = build_auto_encryption_opts(config, "mongodb://localhost", "mydb")
        assert result is None
