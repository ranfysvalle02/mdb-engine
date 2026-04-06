"""Integration coverage for Casbin/OSO authorization provider behavior."""

from __future__ import annotations

import time
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from mdb_engine.auth.base import AuthorizationError
from mdb_engine.auth.casbin_factory import create_casbin_enforcer
from mdb_engine.auth.policy_compiler import compile_manifest_policies
from mdb_engine.auth.provider import CasbinAdapter, OsoAdapter
from mdb_engine.core.engine import MongoDBEngine


def _policy_collection_name() -> str:
    return f"casbin_test_{uuid4().hex[:8]}"


async def _create_adapter(
    *,
    mongo_uri: str,
    db_name: str,
    policies_collection: str,
) -> CasbinAdapter:
    enforcer = await create_casbin_enforcer(
        mongo_uri=mongo_uri,
        db_name=db_name,
        model="rbac",
        policies_collection=policies_collection,
    )
    return CasbinAdapter(enforcer)


@pytest.mark.integration
@pytest.mark.asyncio
class TestCasbinIntegration:
    """Real MongoDB-backed Casbin behavior."""

    async def test_casbin_enforcer_round_trip(self, mongodb_connection_string: str, unique_db_name: str):
        adapter = await _create_adapter(
            mongo_uri=mongodb_connection_string,
            db_name=unique_db_name,
            policies_collection=_policy_collection_name(),
        )

        assert await adapter.add_policy("editor", "posts", "write") is True
        assert await adapter.check("editor", "posts", "write") is True
        assert await adapter.check("viewer", "posts", "write") is False

    async def test_casbin_policy_persists_across_reload(self, mongodb_connection_string: str, unique_db_name: str):
        policy_collection = _policy_collection_name()
        adapter_one = await _create_adapter(
            mongo_uri=mongodb_connection_string,
            db_name=unique_db_name,
            policies_collection=policy_collection,
        )
        assert await adapter_one.add_policy("editor", "posts", "write") is True

        adapter_two = await _create_adapter(
            mongo_uri=mongodb_connection_string,
            db_name=unique_db_name,
            policies_collection=policy_collection,
        )
        # Ensure latest persisted policies are loaded in this enforcer instance.
        await adapter_two.enforcer.load_policy()
        assert await adapter_two.has_policy("editor", "posts", "write") is True
        assert await adapter_two.check("editor", "posts", "write") is True

    async def test_casbin_role_hierarchy(self, mongodb_connection_string: str, unique_db_name: str):
        adapter = await _create_adapter(
            mongo_uri=mongodb_connection_string,
            db_name=unique_db_name,
            policies_collection=_policy_collection_name(),
        )

        assert await adapter.add_role_for_user("admin", "editor") is True
        assert await adapter.add_policy("editor", "posts", "write") is True
        assert await adapter.check("admin", "posts", "write") is True

    async def test_casbin_cache_invalidated_on_add(self, mongodb_connection_string: str, unique_db_name: str):
        adapter = await _create_adapter(
            mongo_uri=mongodb_connection_string,
            db_name=unique_db_name,
            policies_collection=_policy_collection_name(),
        )

        assert await adapter.check("viewer", "posts", "write") is False
        assert await adapter.add_policy("viewer", "posts", "write") is True
        assert await adapter.check("viewer", "posts", "write") is True


@pytest.mark.integration
@pytest.mark.asyncio
class TestPolicyCompilerIntegration:
    """Compiler integration against a real Casbin adapter."""

    async def test_compiled_write_roles_enforce(self, mongodb_connection_string: str, unique_db_name: str):
        adapter = await _create_adapter(
            mongo_uri=mongodb_connection_string,
            db_name=unique_db_name,
            policies_collection=_policy_collection_name(),
        )
        manifest = {
            "schema_version": "2.0",
            "slug": "compiler-app",
            "auth": {"users": {}},
            "collections": {"posts": {"auth": {"write_roles": ["editor"]}}},
        }

        count = await compile_manifest_policies(adapter, manifest, "compiler-app")
        assert count == 3
        assert await adapter.check("editor", "posts", "write") is True
        assert await adapter.check("viewer", "posts", "write") is False

    async def test_compiled_public_read_enforce(self, mongodb_connection_string: str, unique_db_name: str):
        adapter = await _create_adapter(
            mongo_uri=mongodb_connection_string,
            db_name=unique_db_name,
            policies_collection=_policy_collection_name(),
        )
        manifest = {
            "schema_version": "2.0",
            "slug": "compiler-app",
            "collections": {"posts": {"auth": {"public_read": True}}},
        }

        count = await compile_manifest_policies(adapter, manifest, "compiler-app")
        assert count == 1
        assert await adapter.check("*", "posts", "read") is True

    async def test_compiled_role_hierarchy_enforce(self, mongodb_connection_string: str, unique_db_name: str):
        adapter = await _create_adapter(
            mongo_uri=mongodb_connection_string,
            db_name=unique_db_name,
            policies_collection=_policy_collection_name(),
        )
        manifest = {
            "schema_version": "2.0",
            "slug": "compiler-app",
            "auth": {"users": {"role_hierarchy": {"admin": ["editor"]}}},
            "collections": {"posts": {"auth": {"write_roles": ["editor"]}}},
        }

        count = await compile_manifest_policies(adapter, manifest, "compiler-app")
        assert count == 4
        assert await adapter.check("admin", "posts", "write") is True
        assert await adapter.check("viewer", "posts", "write") is False

    async def test_compiled_full_blog_manifest(self, mongodb_connection_string: str, unique_db_name: str):
        adapter = await _create_adapter(
            mongo_uri=mongodb_connection_string,
            db_name=unique_db_name,
            policies_collection=_policy_collection_name(),
        )
        manifest = {
            "schema_version": "2.0",
            "slug": "tech-blog",
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

        count = await compile_manifest_policies(adapter, manifest, "tech-blog")
        assert count == 12
        assert await adapter.check("*", "posts", "read") is True
        assert await adapter.check("editor", "posts", "write") is True
        assert await adapter.check("admin", "activity", "delete") is True
        assert await adapter.check("viewer", "activity", "write") is False


@pytest.mark.integration
@pytest.mark.asyncio
class TestAutoCrudAuthEndToEnd:
    """HTTP-level auth behavior through create_app + auto-CRUD routes."""

    async def test_unauthenticated_public_read(
        self,
        mongodb_connection_string: str,
        unique_db_name: str,
        auth_manifest_factory,
    ):
        manifest_path = auth_manifest_factory(
            provider="casbin",
            slug="e2e-public-read",
            users_config={"enabled": True, "allow_registration": True},
            collections={
                "posts": {
                    "auto_crud": True,
                    "auth": {
                        "public_read": True,
                        "write_roles": ["editor"],
                    },
                }
            },
        )
        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=unique_db_name)
        app = engine.create_app(slug="e2e-public-read", manifest=manifest_path)

        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get("/api/posts")
                assert response.status_code == 200

    async def test_unauthenticated_write_rejected(
        self,
        mongodb_connection_string: str,
        unique_db_name: str,
        auth_manifest_factory,
    ):
        manifest_path = auth_manifest_factory(
            provider="casbin",
            slug="e2e-unauth-write",
            users_config={"enabled": True, "allow_registration": True},
            collections={
                "posts": {
                    "auto_crud": True,
                    "auth": {
                        "public_read": True,
                        "write_roles": ["editor"],
                    },
                }
            },
        )
        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=unique_db_name)
        app = engine.create_app(slug="e2e-unauth-write", manifest=manifest_path)

        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/api/posts", json={"title": "blocked"})
                assert response.status_code == 401

    async def test_authenticated_user_with_role_can_write(
        self,
        mongodb_connection_string: str,
        unique_db_name: str,
        auth_manifest_factory,
    ):
        manifest_path = auth_manifest_factory(
            provider="casbin",
            slug="e2e-editor-write",
            users_config={
                "enabled": True,
                "allow_registration": True,
                "registration_role": "editor",
            },
            collections={
                "posts": {
                    "auto_crud": True,
                    "auth": {
                        "public_read": True,
                        "write_roles": ["editor"],
                    },
                }
            },
        )
        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=unique_db_name)
        app = engine.create_app(slug="e2e-editor-write", manifest=manifest_path)

        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                register = await client.post(
                    "/auth/register",
                    json={"email": "editor@example.com", "password": "secret123"},
                )
                assert register.status_code == 200
                assert hasattr(app.state, "authz_provider")
                await app.state.authz_provider.add_role_for_user("editor@example.com", "editor")

                write = await client.post("/api/posts", json={"title": "allowed"})
                assert write.status_code in {200, 201}

    async def test_authenticated_user_without_role_denied(
        self,
        mongodb_connection_string: str,
        unique_db_name: str,
        auth_manifest_factory,
    ):
        manifest_path = auth_manifest_factory(
            provider="casbin",
            slug="e2e-viewer-denied",
            users_config={
                "enabled": True,
                "allow_registration": True,
                "registration_role": "viewer",
            },
            collections={
                "posts": {
                    "auto_crud": True,
                    "auth": {
                        "public_read": True,
                        "write_roles": ["editor"],
                    },
                }
            },
        )
        engine = MongoDBEngine(mongo_uri=mongodb_connection_string, db_name=unique_db_name)
        app = engine.create_app(slug="e2e-viewer-denied", manifest=manifest_path)

        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                register = await client.post(
                    "/auth/register",
                    json={"email": "viewer@example.com", "password": "secret123"},
                )
                assert register.status_code == 200
                assert hasattr(app.state, "authz_provider")
                await app.state.authz_provider.add_role_for_user("viewer@example.com", "viewer")

                write = await client.post("/api/posts", json={"title": "denied"})
                assert write.status_code == 403


@pytest.mark.integration
@pytest.mark.asyncio
class TestOsoAdapterUnit:
    """Adapter contract checks for OSO behavior using a mock client."""

    # --- check() ---

    async def test_oso_adapter_check_delegates_to_authorize(self):
        oso_client = MagicMock()
        oso_client.authorize = MagicMock(return_value=True)
        adapter = OsoAdapter(oso_client)

        assert await adapter.check("alice", "posts", "read") is True
        oso_client.authorize.assert_called_once()

    async def test_oso_adapter_check_deny(self):
        oso_client = MagicMock()
        oso_client.authorize = MagicMock(return_value=False)
        adapter = OsoAdapter(oso_client)

        assert await adapter.check("alice", "posts", "read") is False

    async def test_oso_adapter_fail_closed(self):
        oso_client = MagicMock()
        oso_client.authorize = MagicMock(side_effect=RuntimeError("boom"))
        adapter = OsoAdapter(oso_client)

        assert await adapter.check("alice", "posts", "read") is False

    async def test_oso_adapter_check_uninitialized_denies(self):
        oso_client = MagicMock()
        oso_client.authorize = MagicMock(return_value=True)
        adapter = OsoAdapter(oso_client)
        adapter._initialized = False

        assert await adapter.check("alice", "posts", "read") is False
        oso_client.authorize.assert_not_called()

    async def test_oso_adapter_none_client_raises(self):
        with pytest.raises(AuthorizationError):
            OsoAdapter(None)

    # --- add_role_for_user() ---

    async def test_oso_adapter_add_role_calls_insert(self):
        oso_client = MagicMock()
        oso_client.insert = MagicMock(return_value=True)
        adapter = OsoAdapter(oso_client)

        assert await adapter.add_role_for_user("alice@example.com", "admin", "documents") is True
        oso_client.insert.assert_called_once()

    async def test_oso_adapter_add_role_wrong_param_count(self):
        oso_client = MagicMock()
        adapter = OsoAdapter(oso_client)

        assert await adapter.add_role_for_user("only_one") is False

    # --- add_policy() ---

    async def test_oso_adapter_add_policy_calls_insert(self):
        oso_client = MagicMock()
        oso_client.insert = MagicMock(return_value=True)
        adapter = OsoAdapter(oso_client)

        assert await adapter.add_policy("editor", "posts", "write") is True
        oso_client.insert.assert_called_once()

    async def test_oso_adapter_add_policy_wrong_param_count(self):
        oso_client = MagicMock()
        adapter = OsoAdapter(oso_client)

        assert await adapter.add_policy("only", "two") is False

    # --- save_policy() ---

    async def test_oso_adapter_save_policy_noop(self):
        oso_client = MagicMock(spec=[])
        adapter = OsoAdapter(oso_client)

        assert await adapter.save_policy() is True

    async def test_oso_adapter_save_policy_delegates_to_save(self):
        oso_client = MagicMock()
        oso_client.save = MagicMock(return_value=True)
        adapter = OsoAdapter(oso_client)

        assert await adapter.save_policy() is True
        oso_client.save.assert_called_once()

    # --- has_policy() ---

    async def test_oso_adapter_has_policy_wrong_param_count(self):
        oso_client = MagicMock()
        adapter = OsoAdapter(oso_client)

        assert await adapter.has_policy("only", "two") is False

    async def test_oso_adapter_has_policy_no_query_returns_true(self):
        oso_client = MagicMock(spec=["insert"])
        adapter = OsoAdapter(oso_client)

        assert await adapter.has_policy("editor", "posts", "write") is True

    # --- has_role_for_user() ---

    async def test_oso_adapter_has_role_wrong_param_count(self):
        oso_client = MagicMock()
        adapter = OsoAdapter(oso_client)

        assert await adapter.has_role_for_user("only") is False

    async def test_oso_adapter_has_role_no_query_returns_true(self):
        oso_client = MagicMock(spec=["insert"])
        adapter = OsoAdapter(oso_client)

        assert await adapter.has_role_for_user("alice", "admin") is True

    # --- remove_role_for_user() ---

    async def test_oso_adapter_remove_role_calls_delete(self):
        oso_client = MagicMock()
        oso_client.delete = MagicMock(return_value=True)
        adapter = OsoAdapter(oso_client)

        assert await adapter.remove_role_for_user("alice", "admin") is True
        oso_client.delete.assert_called_once()

    async def test_oso_adapter_remove_role_wrong_param_count(self):
        oso_client = MagicMock()
        adapter = OsoAdapter(oso_client)

        assert await adapter.remove_role_for_user("only") is False

    # --- clear_cache() ---

    async def test_oso_adapter_clear_cache(self):
        oso_client = MagicMock()
        oso_client.authorize = MagicMock(return_value=True)
        adapter = OsoAdapter(oso_client)

        await adapter.check("alice", "posts", "read")
        assert len(adapter._cache) == 1

        await adapter.clear_cache()
        assert len(adapter._cache) == 0

    # --- cache TTL ---

    async def test_oso_adapter_cache_hit_avoids_call(self):
        oso_client = MagicMock()
        oso_client.authorize = MagicMock(return_value=True)
        adapter = OsoAdapter(oso_client)

        assert await adapter.check("alice", "posts", "read") is True
        assert await adapter.check("alice", "posts", "read") is True
        oso_client.authorize.assert_called_once()

    async def test_oso_adapter_cache_expired_re_calls(self):
        oso_client = MagicMock()
        oso_client.authorize = MagicMock(return_value=True)
        adapter = OsoAdapter(oso_client)

        assert await adapter.check("alice", "posts", "read") is True
        adapter._cache[("alice", "posts", "read")] = (True, time.time() - 600)

        assert await adapter.check("alice", "posts", "read") is True
        assert oso_client.authorize.call_count == 2


# ---------------------------------------------------------------------------
# Thin fake OSO client that evaluates facts locally (no network, no package).
# ---------------------------------------------------------------------------


class _FakeOsoClient:
    """In-memory fake that mirrors how Casbin resolves policies + role inheritance."""

    def __init__(self):
        self._policies: set[tuple[str, str, str]] = set()
        self._roles: dict[str, set[str]] = {}

    @staticmethod
    def _id(val) -> str:
        """Extract plain string ID from an oso_cloud.Value or plain string."""
        return val.id if hasattr(val, "id") else str(val)

    def insert(self, fact: list) -> bool:
        name = fact[0]
        if name == "grants_permission":
            _, role, action, resource = fact
            self._policies.add((self._id(role), self._id(resource), self._id(action)))
        elif name == "has_role":
            _, user, role, *_ = fact
            self._roles.setdefault(self._id(user), set()).add(self._id(role))
        return True

    def _effective_roles(self, user: str) -> set[str]:
        visited: set[str] = set()
        stack = list(self._roles.get(user, set()))
        while stack:
            role = stack.pop()
            if role not in visited:
                visited.add(role)
                stack.extend(self._roles.get(role, set()))
        return visited

    def authorize(self, actor, action, resource) -> bool:
        user = self._id(actor)
        act = self._id(action)
        res = self._id(resource)
        roles = self._effective_roles(user) | {user}
        return any((r, res, act) in self._policies for r in roles)

    def delete(self, *_args) -> bool:
        return True


# ---------------------------------------------------------------------------
# Provider-parity contract tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.asyncio
class TestProviderParityContract:
    """Identical authorization sequences through both adapters.

    Proves that application code using BaseAuthorizationProvider gets
    the same results regardless of which backend is configured.
    """

    async def _make_casbin_adapter(self, mongodb_connection_string: str, unique_db_name: str) -> CasbinAdapter:
        enforcer = await create_casbin_enforcer(
            mongo_uri=mongodb_connection_string,
            db_name=unique_db_name,
            model="rbac",
            policies_collection=_policy_collection_name(),
        )
        return CasbinAdapter(enforcer)

    @staticmethod
    def _make_oso_adapter() -> OsoAdapter:
        return OsoAdapter(_FakeOsoClient())

    async def test_add_policy_and_check(self, mongodb_connection_string: str, unique_db_name: str):
        for adapter in (
            await self._make_casbin_adapter(mongodb_connection_string, unique_db_name),
            self._make_oso_adapter(),
        ):
            assert await adapter.add_policy("editor", "posts", "write") is True
            assert await adapter.check("editor", "posts", "write") is True
            assert await adapter.check("viewer", "posts", "write") is False

    async def test_role_inheritance(self, mongodb_connection_string: str, unique_db_name: str):
        for adapter in (
            await self._make_casbin_adapter(mongodb_connection_string, unique_db_name),
            self._make_oso_adapter(),
        ):
            assert await adapter.add_role_for_user("admin", "editor") is True
            assert await adapter.add_policy("editor", "posts", "write") is True
            assert await adapter.check("admin", "posts", "write") is True
            assert await adapter.check("viewer", "posts", "write") is False

    async def test_has_policy(self, mongodb_connection_string: str, unique_db_name: str):
        for adapter in (
            await self._make_casbin_adapter(mongodb_connection_string, unique_db_name),
            self._make_oso_adapter(),
        ):
            assert await adapter.add_policy("editor", "posts", "write") is True
            assert await adapter.has_policy("editor", "posts", "write") is True

    async def test_clear_cache_does_not_raise(self, mongodb_connection_string: str, unique_db_name: str):
        for adapter in (
            await self._make_casbin_adapter(mongodb_connection_string, unique_db_name),
            self._make_oso_adapter(),
        ):
            await adapter.clear_cache()

    async def test_save_policy_succeeds(self, mongodb_connection_string: str, unique_db_name: str):
        for adapter in (
            await self._make_casbin_adapter(mongodb_connection_string, unique_db_name),
            self._make_oso_adapter(),
        ):
            result = await adapter.save_policy()
            assert result is not False
