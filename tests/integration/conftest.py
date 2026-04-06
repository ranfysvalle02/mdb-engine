"""Shared fixtures for integration auth-provider tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest


@pytest.fixture
def unique_db_name() -> str:
    """Return a unique database name for test isolation."""
    return f"test_auth_{os.getpid()}_{uuid4().hex[:8]}"


@pytest.fixture
def auth_manifest_factory(tmp_path_factory: pytest.TempPathFactory):
    """Build and write manifest.json files for auth integration tests.

    Args accepted by the returned callable:
        provider: Auth provider name ("casbin" by default).
        slug: App slug.
        name: App display name.
        collections: Manifest collections config.
        role_hierarchy: auth.users.role_hierarchy mapping.
        users_config: auth.users overrides.
        auth_mode: auth.mode value.
        data_access: Optional data_access override.
    """

    def _create_manifest(
        *,
        provider: str = "casbin",
        slug: str = "auth-test-app",
        name: str = "Auth Test App",
        collections: dict[str, Any] | None = None,
        role_hierarchy: dict[str, list[str]] | None = None,
        users_config: dict[str, Any] | None = None,
        auth_mode: str = "app",
        data_access: dict[str, Any] | None = None,
    ) -> Path:
        manifest_dir = tmp_path_factory.mktemp(f"manifest_{slug}_{provider}")
        manifest_path = manifest_dir / "manifest.json"

        default_users = {
            "enabled": True,
            "allow_registration": True,
            "registration_role": "viewer",
        }
        merged_users = {**default_users, **(users_config or {})}
        if role_hierarchy:
            merged_users["role_hierarchy"] = role_hierarchy

        manifest: dict[str, Any] = {
            "schema_version": "2.0",
            "slug": slug,
            "name": name,
            "status": "active",
            "data_access": data_access or {"read_scopes": [slug], "write_scope": slug},
            "auth": {
                "mode": auth_mode,
                "policy": {
                    "provider": provider,
                },
                "users": merged_users,
            },
            "collections": collections
            or {
                "posts": {
                    "auto_crud": True,
                    "auth": {
                        "public_read": True,
                        "write_roles": ["editor"],
                    },
                }
            },
        }

        manifest_path.write_text(json.dumps(manifest, indent=2))
        return manifest_path

    return _create_manifest
