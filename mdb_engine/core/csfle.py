"""
Client-Side Field Level Encryption (CSFLE) support for MDB Engine.

This module provides automatic field-level encryption using MongoDB's
CSFLE feature. It supports:
- Local key provider (for development/testing)
- AWS KMS, Azure Key Vault, GCP Cloud KMS (for production)

Usage:
    # Simple memory encryption (from manifest)
    config = CSFLEConfig.from_memory_config(
        {"encrypted": True, "collection_name": "memories"},
        app_slug="my-app"
    )

    # Custom field encryption
    config = CSFLEConfig.from_encrypted_fields(
        {"payments": ["card_number", "cvv"]},
        {"kms_provider": "local"},
        app_slug="my-app"
    )

    # Use with MongoDBEngine
    engine = MongoDBEngine(
        mongo_uri="mongodb://localhost:27017",
        db_name="mydb",
        csfle_config=config,
    )

Environment Variables:
    CRYPT_SHARED_LIB_PATH: Path to mongo_crypt_v1.so/.dylib
    MDB_CSFLE_LOCAL_KEY: Base64-encoded 96-byte local master key

    For cloud KMS providers:
    - AWS: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_KMS_KEY_ARN
    - Azure: AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_KEY_VAULT_URL
    - GCP: GCP_EMAIL, GCP_PRIVATE_KEY
"""

from __future__ import annotations

import base64
import logging
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

# Default fields to encrypt for memory collections
DEFAULT_MEMORY_ENCRYPTED_FIELDS = ["content", "text"]

# Key vault namespace (database.collection)
DEFAULT_KEY_VAULT_NAMESPACE = "encryption.__keyVault"

# Environment variable names
ENV_CRYPT_SHARED_PATH = "CRYPT_SHARED_LIB_PATH"
ENV_LOCAL_KEY = "MDB_CSFLE_LOCAL_KEY"
ENV_CSFLE_KEY_FILE = "CSFLE_KEY_FILE"

# Default key file location (Docker volume mount)
DEFAULT_KEY_FILE_PATH = "/data/csfle/.local_master_key"

# KMS provider environment variables
ENV_AWS_ACCESS_KEY = "AWS_ACCESS_KEY_ID"
ENV_AWS_SECRET_KEY = "AWS_SECRET_ACCESS_KEY"
ENV_AWS_KMS_KEY_ARN = "AWS_KMS_KEY_ARN"
ENV_AZURE_TENANT_ID = "AZURE_TENANT_ID"
ENV_AZURE_CLIENT_ID = "AZURE_CLIENT_ID"
ENV_AZURE_CLIENT_SECRET = "AZURE_CLIENT_SECRET"
ENV_AZURE_KEY_VAULT_URL = "AZURE_KEY_VAULT_URL"
ENV_GCP_EMAIL = "GCP_EMAIL"
ENV_GCP_PRIVATE_KEY = "GCP_PRIVATE_KEY"

if TYPE_CHECKING:
    from pymongo.encryption_options import AutoEncryptionOpts


def generate_local_master_key() -> str:
    """
    Generate a new 96-byte local master key for CSFLE.

    Returns:
        Base64-encoded 96-byte key string

    Example:
        key = generate_local_master_key()
        # Add to .env: MDB_CSFLE_LOCAL_KEY=<key>
    """
    key_bytes = secrets.token_bytes(96)
    return base64.b64encode(key_bytes).decode("utf-8")


def _get_crypt_shared_path() -> str | None:
    """Get the path to the crypt_shared library."""
    # Check environment variable first
    env_path = os.environ.get(ENV_CRYPT_SHARED_PATH)
    if env_path:
        return env_path

    # Common default locations
    default_paths = [
        "/opt/mongodb/lib/mongo_crypt_v1.so",  # Docker/Linux
        "/usr/local/lib/mongo_crypt_v1.so",
        "/usr/lib/mongo_crypt_v1.so",
        "./lib/mongo_crypt_v1.so",  # Local development
        "/opt/mongodb/lib/mongo_crypt_v1.dylib",  # macOS
        "/usr/local/lib/mongo_crypt_v1.dylib",
        "./lib/mongo_crypt_v1.dylib",
    ]

    for path in default_paths:
        if Path(path).exists():
            return path

    return None


def _get_local_key() -> bytes | None:
    """
    Get the local master key from environment variable or key file.

    Checks in order:
    1. MDB_CSFLE_LOCAL_KEY environment variable
    2. Key file at CSFLE_KEY_FILE env var location
    3. Key file at default location (/data/csfle/.local_master_key)

    Returns:
        96-byte key as bytes, or None if not found
    """
    # First, check environment variable
    key_str = os.environ.get(ENV_LOCAL_KEY)

    # If not in env, check key file
    if not key_str:
        key_file_path = os.environ.get(ENV_CSFLE_KEY_FILE, DEFAULT_KEY_FILE_PATH)
        key_file = Path(key_file_path)

        if key_file.exists():
            try:
                key_str = key_file.read_text().strip()
                logger.debug(f"Loaded CSFLE key from file: {key_file_path}")
            except OSError as e:
                logger.warning(f"Failed to read key file {key_file_path}: {e}")
                key_str = None

    if not key_str:
        return None

    try:
        key_bytes = base64.b64decode(key_str)
        if len(key_bytes) != 96:
            logger.warning(
                f"Local master key is {len(key_bytes)} bytes, expected 96. " f"This may cause encryption failures."
            )
        return key_bytes
    except (ValueError, base64.binascii.Error) as e:
        logger.exception(f"Failed to decode local master key: {e}")
        return None


def is_csfle_available() -> bool:
    """
    Check if CSFLE can be used.

    Returns:
        True if pymongo[encryption] is installed and crypt_shared library exists
    """
    status = get_csfle_status()
    return status.get("available", False)


def get_csfle_status() -> dict[str, Any]:
    """
    Get detailed CSFLE configuration status.

    Returns:
        Dictionary with:
        - available: bool - True if CSFLE can be used
        - pymongo_encryption: bool - True if pymongo[encryption] is installed
        - crypt_shared_path: str | None - Path to crypt_shared library
        - crypt_shared_exists: bool - True if library file exists
        - local_key_configured: bool - True if MDB_CSFLE_LOCAL_KEY is set

    Example:
        status = get_csfle_status()
        if not status["available"]:
            print("CSFLE not available")
        if not status["local_key_configured"]:
            print("Warning: Using ephemeral key - data won't persist!")
    """
    status: dict[str, Any] = {
        "available": False,
        "pymongo_encryption": False,
        "crypt_shared_path": None,
        "crypt_shared_exists": False,
        "local_key_configured": False,
    }

    # Check if pymongo[encryption] is installed
    try:
        from pymongo.encryption_options import AutoEncryptionOpts  # noqa: F401

        status["pymongo_encryption"] = True
    except ImportError:
        logger.debug("pymongo[encryption] not installed")
        return status

    # Check crypt_shared library
    crypt_path = _get_crypt_shared_path()
    status["crypt_shared_path"] = crypt_path
    if crypt_path:
        status["crypt_shared_exists"] = Path(crypt_path).exists()

    # Check local key
    local_key = _get_local_key()
    status["local_key_configured"] = local_key is not None

    # CSFLE is available if we have pymongo encryption support and crypt_shared
    status["available"] = status["pymongo_encryption"] and status["crypt_shared_exists"]

    return status


@dataclass
class CSFLEConfig:
    """
    Configuration for Client-Side Field Level Encryption.

    This class holds the encryption configuration for collections and fields.
    Use the factory methods to create instances from manifest configurations.

    Attributes:
        enabled: Whether CSFLE is enabled
        kms_provider: KMS provider ("local", "aws", "azure", "gcp")
        key_vault_namespace: Namespace for key storage (database.collection)
        encrypted_collections: Dict mapping collection names to lists of encrypted fields
    """

    enabled: bool = True
    kms_provider: str = "local"
    key_vault_namespace: str = DEFAULT_KEY_VAULT_NAMESPACE
    encrypted_collections: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def from_memory_config(
        cls,
        memory_config: dict[str, Any],
        app_slug: str,
    ) -> CSFLEConfig:
        """
        Create CSFLEConfig from a manifest's memory_config.

        Args:
            memory_config: The memory_config section from manifest
            app_slug: App slug for collection name prefixing

        Returns:
            CSFLEConfig instance

        Example:
            config = CSFLEConfig.from_memory_config(
                {"encrypted": True, "collection_name": "memories"},
                "my-app"
            )
        """
        if not memory_config.get("encrypted", False):
            return cls(enabled=False)

        # Get collection name with app prefix
        collection_name = memory_config.get("collection_name", "memories")
        full_collection_name = f"{app_slug}_{collection_name}"

        # Get encryption settings
        encryption = memory_config.get("encryption", {})
        kms_provider = encryption.get("kms_provider", "local")
        key_vault_namespace = encryption.get("key_vault_namespace", DEFAULT_KEY_VAULT_NAMESPACE)

        # Get fields to encrypt
        fields = encryption.get("fields", DEFAULT_MEMORY_ENCRYPTED_FIELDS)

        return cls(
            enabled=True,
            kms_provider=kms_provider,
            key_vault_namespace=key_vault_namespace,
            encrypted_collections={full_collection_name: fields},
        )

    @classmethod
    def from_encrypted_fields(
        cls,
        encrypted_fields: dict[str, list[str]],
        encryption_config: dict[str, Any],
        app_slug: str,
    ) -> CSFLEConfig:
        """
        Create CSFLEConfig from manifest's encrypted_fields section.

        Args:
            encrypted_fields: Dict mapping collection names to lists of fields
            encryption_config: Global encryption config (kms_provider, etc.)
            app_slug: App slug for collection name prefixing

        Returns:
            CSFLEConfig instance

        Example:
            config = CSFLEConfig.from_encrypted_fields(
                {"payments": ["card_number", "cvv"]},
                {"kms_provider": "aws"},
                "my-app"
            )
        """
        if not encrypted_fields:
            return cls(enabled=False)

        kms_provider = encryption_config.get("kms_provider", "local")
        key_vault_namespace = encryption_config.get("key_vault_namespace", DEFAULT_KEY_VAULT_NAMESPACE)

        # Prefix collection names with app slug
        prefixed_collections = {f"{app_slug}_{name}": fields for name, fields in encrypted_fields.items()}

        return cls(
            enabled=True,
            kms_provider=kms_provider,
            key_vault_namespace=key_vault_namespace,
            encrypted_collections=prefixed_collections,
        )

    def merge_with(self, other: CSFLEConfig) -> CSFLEConfig:
        """
        Merge this config with another CSFLEConfig.

        Collections from both configs are combined. KMS provider and
        key vault namespace are taken from self (first config takes precedence).

        Args:
            other: Another CSFLEConfig to merge with

        Returns:
            New merged CSFLEConfig
        """
        if not other.enabled:
            return self
        if not self.enabled:
            return other

        merged_collections = {**self.encrypted_collections}
        for collection, fields in other.encrypted_collections.items():
            if collection in merged_collections:
                # Merge field lists, avoiding duplicates
                existing = set(merged_collections[collection])
                merged_collections[collection] = list(existing | set(fields))
            else:
                merged_collections[collection] = fields

        return CSFLEConfig(
            enabled=True,
            kms_provider=self.kms_provider,
            key_vault_namespace=self.key_vault_namespace,
            encrypted_collections=merged_collections,
        )

    def __repr__(self) -> str:
        collections = list(self.encrypted_collections.keys())
        return (
            f"CSFLEConfig(enabled={self.enabled}, "
            f"kms_provider='{self.kms_provider}', "
            f"collections={collections})"
        )


def _get_kms_providers(kms_provider: str) -> dict[str, Any]:
    """
    Get KMS provider credentials from environment.

    Args:
        kms_provider: Provider name ("local", "aws", "azure", "gcp")

    Returns:
        KMS provider configuration dict

    Raises:
        ValueError: If required credentials are missing
    """
    if kms_provider == "local":
        local_key = _get_local_key()
        if local_key is None:
            # Generate ephemeral key with warning
            logger.warning(
                "No MDB_CSFLE_LOCAL_KEY configured. Generating ephemeral key. "
                "WARNING: Data encrypted with this key will be UNREADABLE "
                "after application restart! Set MDB_CSFLE_LOCAL_KEY for "
                "persistent encryption."
            )
            local_key = secrets.token_bytes(96)

        return {"local": {"key": local_key}}

    elif kms_provider == "aws":
        access_key = os.environ.get(ENV_AWS_ACCESS_KEY)
        secret_key = os.environ.get(ENV_AWS_SECRET_KEY)

        if not access_key or not secret_key:
            raise ValueError(
                f"AWS KMS requires {ENV_AWS_ACCESS_KEY} and " f"{ENV_AWS_SECRET_KEY} environment variables"
            )

        return {
            "aws": {
                "accessKeyId": access_key,
                "secretAccessKey": secret_key,
            }
        }

    elif kms_provider == "azure":
        tenant_id = os.environ.get(ENV_AZURE_TENANT_ID)
        client_id = os.environ.get(ENV_AZURE_CLIENT_ID)
        client_secret = os.environ.get(ENV_AZURE_CLIENT_SECRET)

        if not all([tenant_id, client_id, client_secret]):
            raise ValueError(
                f"Azure Key Vault requires {ENV_AZURE_TENANT_ID}, "
                f"{ENV_AZURE_CLIENT_ID}, and {ENV_AZURE_CLIENT_SECRET} "
                f"environment variables"
            )

        return {
            "azure": {
                "tenantId": tenant_id,
                "clientId": client_id,
                "clientSecret": client_secret,
            }
        }

    elif kms_provider == "gcp":
        email = os.environ.get(ENV_GCP_EMAIL)
        private_key = os.environ.get(ENV_GCP_PRIVATE_KEY)

        if not email or not private_key:
            raise ValueError(
                f"GCP Cloud KMS requires {ENV_GCP_EMAIL} and " f"{ENV_GCP_PRIVATE_KEY} environment variables"
            )

        return {
            "gcp": {
                "email": email,
                "privateKey": private_key,
            }
        }

    else:
        raise ValueError(f"Unknown KMS provider: {kms_provider}")


def _ensure_data_keys(
    kms_providers: dict[str, Any],
    key_vault_namespace: str,
    mongo_uri: str,
    config: CSFLEConfig,
) -> dict[str, bytes]:
    """
    Ensure data keys exist in the key vault, creating them if needed.

    Creates one data key per collection, using key alt names for lookup.
    Returns a mapping of collection names to their key IDs.

    Args:
        kms_providers: KMS provider configuration
        key_vault_namespace: Key vault namespace (db.collection)
        mongo_uri: MongoDB connection URI
        config: CSFLE configuration

    Returns:
        Dict mapping collection names to their key IDs (Binary UUID)
    """
    from bson.binary import STANDARD
    from bson.codec_options import CodecOptions
    from pymongo import MongoClient
    from pymongo.encryption import ClientEncryption

    key_ids: dict[str, bytes] = {}

    # NOTE: CSFLE key management requires sync PyMongo operations (ClientEncryption API).
    # This is a legitimate exception to the "no direct MongoDB connections" rule.
    # If called from within engine context, consider using engine._connection_manager.mongo_client.delegate
    # to get the underlying PyMongo client instead of creating a new connection.
    # However, this function is designed to work standalone for CSFLE setup/initialization.
    client = MongoClient(mongo_uri)

    try:
        # Set up key vault collection with unique index on keyAltNames
        key_vault_db, key_vault_coll = key_vault_namespace.split(".", 1)
        key_vault = client[key_vault_db][key_vault_coll]

        # Create unique index on keyAltNames (idempotent)
        try:
            key_vault.create_index(
                "keyAltNames",
                unique=True,
                partialFilterExpression={"keyAltNames": {"$exists": True}},
            )
        except (ValueError, TypeError, AttributeError) as e:
            # Index might already exist or have different options
            logger.debug(f"Key vault index creation: {e}")

        # Create ClientEncryption for key management
        client_encryption = ClientEncryption(
            kms_providers, key_vault_namespace, client, CodecOptions(uuid_representation=STANDARD)
        )

        try:
            for collection in config.encrypted_collections.keys():
                # Use collection name as key alt name for easy lookup
                key_alt_name = f"mdb_engine_{collection}"

                # Check if key already exists
                existing_key = key_vault.find_one({"keyAltNames": key_alt_name}, {"_id": 1})

                if existing_key:
                    key_ids[collection] = existing_key["_id"]
                    logger.debug(f"Using existing data key for {collection}")
                else:
                    # Create new data key
                    key_id = client_encryption.create_data_key(config.kms_provider, key_alt_names=[key_alt_name])
                    key_ids[collection] = key_id
                    logger.info(f"Created new data key for {collection}")
        finally:
            client_encryption.close()
    finally:
        client.close()

    return key_ids


def _build_schema_map(
    config: CSFLEConfig,
    db_name: str,
    key_ids: dict[str, bytes],
) -> dict[str, dict[str, Any]]:
    """
    Build the encrypted fields schema map for auto-encryption.

    This creates the JSON schema that tells MongoDB which fields to encrypt.
    Uses deterministic encryption for queryable fields, random for others.

    Args:
        config: CSFLE configuration
        db_name: Database name
        key_ids: Dict mapping collection names to their data key IDs

    Returns:
        Schema map dict for AutoEncryptionOpts
    """
    from bson import Binary

    schema_map: dict[str, dict[str, Any]] = {}

    for collection, fields in config.encrypted_collections.items():
        namespace = f"{db_name}.{collection}"
        key_id = key_ids.get(collection)

        if not key_id:
            logger.warning(f"No key ID found for collection {collection}, skipping")
            continue

        # Build properties for each encrypted field
        properties: dict[str, dict[str, Any]] = {}
        for field_name in fields:
            # Use "AEAD_AES_256_CBC_HMAC_SHA_512-Random" for random encryption
            # (more secure, but field can't be queried)
            properties[field_name] = {
                "encrypt": {
                    "bsonType": "string",
                    "algorithm": "AEAD_AES_256_CBC_HMAC_SHA_512-Random",
                    "keyId": [key_id] if isinstance(key_id, Binary) else [Binary(key_id)],
                }
            }

        schema_map[namespace] = {
            "bsonType": "object",
            "properties": properties,
        }

    return schema_map


def build_auto_encryption_opts(
    config: CSFLEConfig,
    mongo_uri: str,
    db_name: str,
) -> AutoEncryptionOpts | None:
    """
    Build AutoEncryptionOpts from CSFLEConfig.

    This creates the configuration object needed by PyMongo to enable
    automatic client-side field level encryption. It will also ensure
    that data keys exist in the key vault, creating them if needed.

    Args:
        config: CSFLE configuration
        mongo_uri: MongoDB connection URI (for key vault client)
        db_name: Database name

    Returns:
        AutoEncryptionOpts instance, or None if CSFLE cannot be configured

    Example:
        opts = build_auto_encryption_opts(config, uri, db_name)
        if opts:
            client = MongoClient(uri, auto_encryption_opts=opts)

    Note:
        CSFLE key management operations (_ensure_data_keys) require sync PyMongo operations.
        This is a legitimate exception to the "no direct MongoDB connections" rule when engine is available.
        When called from engine context, prefer using engine's connection manager if possible.
    """
    if not config.enabled:
        return None

    # Check CSFLE availability
    status = get_csfle_status()
    if not status["pymongo_encryption"]:
        logger.warning("pymongo[encryption] not installed. CSFLE disabled.")
        return None

    if not status["crypt_shared_exists"]:
        logger.warning(f"crypt_shared library not found at {status['crypt_shared_path']}. " "CSFLE disabled.")
        return None

    try:
        from pymongo.encryption_options import AutoEncryptionOpts
    except ImportError:
        logger.warning("Failed to import AutoEncryptionOpts. CSFLE disabled.")
        return None

    try:
        # Get KMS provider credentials
        kms_providers = _get_kms_providers(config.kms_provider)

        # Ensure data keys exist and get their IDs
        key_ids = _ensure_data_keys(
            kms_providers,
            config.key_vault_namespace,
            mongo_uri,
            config,
        )

        if not key_ids:
            logger.error("Failed to create or retrieve data keys. CSFLE disabled.")
            return None

        # Build schema map with key IDs
        schema_map = _build_schema_map(config, db_name, key_ids)

        # Build auto encryption options
        opts = AutoEncryptionOpts(
            kms_providers=kms_providers,
            key_vault_namespace=config.key_vault_namespace,
            schema_map=schema_map,
            crypt_shared_lib_path=status["crypt_shared_path"],
        )

        logger.info(
            f"Built AutoEncryptionOpts: kms_provider={config.kms_provider}, "
            f"collections={list(config.encrypted_collections.keys())}"
        )

        return opts

    except ValueError as e:
        logger.exception(f"Failed to build auto encryption options: {e}")
        return None
    except (TypeError, AttributeError, RuntimeError) as e:
        logger.exception(f"Unexpected error building auto encryption options: {e}")
        return None
