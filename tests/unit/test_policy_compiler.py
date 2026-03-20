"""Tests for the manifest-to-policy compiler (mdb_engine.auth.policy_compiler)."""

from __future__ import annotations

import pytest

from mdb_engine.auth.policy_compiler import compile_manifest_policies, has_collection_auth

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeProvider:
    """Records add_policy / add_role_for_user calls for assertions."""

    def __init__(self):
        self.policies: list[tuple[str, ...]] = []
        self.roles: list[tuple[str, ...]] = []

    async def add_policy(self, *params) -> bool:
        self.policies.append(params)
        return True

    async def add_role_for_user(self, *params) -> bool:
        self.roles.append(params)
        return True


# ---------------------------------------------------------------------------
# has_collection_auth
# ---------------------------------------------------------------------------


class TestHasCollectionAuth:
    def test_no_collections(self):
        assert has_collection_auth({}) is False

    def test_collections_without_auth(self):
        manifest = {"collections": {"posts": {"auto_crud": True}}}
        assert has_collection_auth(manifest) is False

    def test_collections_with_auth(self):
        manifest = {"collections": {"posts": {"auth": {"public_read": True, "write_roles": ["editor"]}}}}
        assert has_collection_auth(manifest) is True

    def test_mixed_collections(self):
        manifest = {
            "collections": {
                "posts": {"auto_crud": True},
                "comments": {"auth": {"roles": ["editor"]}},
            }
        }
        assert has_collection_auth(manifest) is True


# ---------------------------------------------------------------------------
# compile_manifest_policies
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCompileManifestPolicies:
    async def test_empty_manifest(self):
        provider = FakeProvider()
        count = await compile_manifest_policies(provider, {}, "test")
        assert count == 0
        assert provider.policies == []
        assert provider.roles == []

    async def test_role_hierarchy(self):
        provider = FakeProvider()
        manifest = {
            "auth": {
                "users": {
                    "role_hierarchy": {
                        "admin": ["editor", "reader"],
                        "editor": ["reader"],
                    }
                }
            }
        }
        count = await compile_manifest_policies(provider, manifest, "test")
        assert count == 3
        assert ("admin", "editor") in provider.roles
        assert ("admin", "reader") in provider.roles
        assert ("editor", "reader") in provider.roles

    async def test_public_read(self):
        provider = FakeProvider()
        manifest = {"collections": {"posts": {"auth": {"public_read": True}}}}
        count = await compile_manifest_policies(provider, manifest, "test")
        assert count == 1
        assert ("*", "posts", "read") in provider.policies

    async def test_write_roles(self):
        provider = FakeProvider()
        manifest = {"collections": {"posts": {"auth": {"write_roles": ["editor"]}}}}
        count = await compile_manifest_policies(provider, manifest, "test")
        assert count == 3
        assert ("editor", "posts", "write") in provider.policies
        assert ("editor", "posts", "create") in provider.policies
        assert ("editor", "posts", "delete") in provider.policies

    async def test_create_roles(self):
        provider = FakeProvider()
        manifest = {"collections": {"comments": {"auth": {"create_roles": ["reader"]}}}}
        count = await compile_manifest_policies(provider, manifest, "test")
        assert count == 1
        assert ("reader", "comments", "create") in provider.policies

    async def test_full_roles(self):
        provider = FakeProvider()
        manifest = {"collections": {"activity": {"auth": {"roles": ["editor"]}}}}
        count = await compile_manifest_policies(provider, manifest, "test")
        assert count == 4
        assert ("editor", "activity", "read") in provider.policies
        assert ("editor", "activity", "write") in provider.policies
        assert ("editor", "activity", "create") in provider.policies
        assert ("editor", "activity", "delete") in provider.policies

    async def test_blog_manifest_combined(self):
        """Simulates the tech-blog manifest structure."""
        provider = FakeProvider()
        manifest = {
            "auth": {
                "users": {
                    "role_hierarchy": {
                        "admin": ["editor", "reader"],
                        "editor": ["reader"],
                    }
                }
            },
            "collections": {
                "posts": {"auth": {"public_read": True, "write_roles": ["editor"]}},
                "comments": {"auth": {"public_read": True, "create_required": True}},
                "activity": {"auth": {"roles": ["editor"]}},
            },
        }
        count = await compile_manifest_policies(provider, manifest, "tech-blog")

        # 3 hierarchy + 1 public_read(posts) + 3 write_roles(posts)
        # + 1 public_read(comments) + 4 roles(activity) = 12
        assert count == 12
        assert ("*", "posts", "read") in provider.policies
        assert ("editor", "posts", "write") in provider.policies
        assert ("*", "comments", "read") in provider.policies
        assert ("editor", "activity", "read") in provider.policies

    async def test_provider_add_policy_failure(self):
        """Compiler tolerates provider failures gracefully."""

        class FailingProvider:
            async def add_policy(self, *params):
                raise RuntimeError("boom")

            async def add_role_for_user(self, *params):
                return True

        provider = FailingProvider()
        manifest = {"collections": {"posts": {"auth": {"write_roles": ["editor"]}}}}
        count = await compile_manifest_policies(provider, manifest, "test")
        assert count == 0
