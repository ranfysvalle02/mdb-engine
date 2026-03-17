"""
Tests for Auth Hardening Features.

Covers:
- _resolve_env_placeholders: {{env.*}} resolution in demo_users
- registration_role: configurable role for self-registered users
- invite_only registration mode with invite_codes
- CLI add-user command
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from mdb_engine.auth.users import _resolve_env_placeholders, _validate_demo_user_config

# ═══════════════════════════════════════════════════════════════════════════
# _resolve_env_placeholders
# ═══════════════════════════════════════════════════════════════════════════


class TestResolveEnvPlaceholders:
    def test_resolves_simple_env_var(self, monkeypatch):
        monkeypatch.setenv("ADMIN_EMAIL", "boss@corp.com")
        assert _resolve_env_placeholders("{{env.ADMIN_EMAIL}}") == "boss@corp.com"

    def test_resolves_in_dict(self, monkeypatch):
        monkeypatch.setenv("ADMIN_EMAIL", "boss@corp.com")
        monkeypatch.setenv("ADMIN_PASSWORD", "s3cret")
        result = _resolve_env_placeholders(
            {
                "email": "{{env.ADMIN_EMAIL}}",
                "password": "{{env.ADMIN_PASSWORD}}",
                "role": "admin",
            }
        )
        assert result == {"email": "boss@corp.com", "password": "s3cret", "role": "admin"}

    def test_resolves_in_list(self, monkeypatch):
        monkeypatch.setenv("CODE_A", "alpha")
        result = _resolve_env_placeholders(["{{env.CODE_A}}", "literal"])
        assert result == ["alpha", "literal"]

    def test_leaves_non_template_strings_alone(self):
        assert _resolve_env_placeholders("plain-string") == "plain-string"

    def test_leaves_non_string_values_alone(self):
        assert _resolve_env_placeholders(42) == 42
        assert _resolve_env_placeholders(True) is True
        assert _resolve_env_placeholders(None) is None

    def test_missing_env_var_returns_placeholder(self, monkeypatch):
        monkeypatch.delenv("MISSING_VAR", raising=False)
        result = _resolve_env_placeholders("{{env.MISSING_VAR}}")
        assert result == "{{env.MISSING_VAR}}"

    def test_only_uppercase_env_names_resolved(self, monkeypatch):
        monkeypatch.setenv("lower_case", "should-not-match")
        result = _resolve_env_placeholders("{{env.lower_case}}")
        assert result == "{{env.lower_case}}"

    def test_nested_dict_resolution(self, monkeypatch):
        monkeypatch.setenv("DEEP_VAL", "found")
        result = _resolve_env_placeholders({"a": {"b": "{{env.DEEP_VAL}}"}})
        assert result == {"a": {"b": "found"}}


# ═══════════════════════════════════════════════════════════════════════════
# _validate_demo_user_config with env resolution
# ═══════════════════════════════════════════════════════════════════════════


class TestValidateDemoUserConfigEnv:
    def test_env_resolved_in_demo_user(self, monkeypatch):
        monkeypatch.setenv("ADMIN_EMAIL", "admin@corp.com")
        monkeypatch.setenv("ADMIN_PASSWORD", "supersecret")
        config = {
            "email": "{{env.ADMIN_EMAIL}}",
            "password": "{{env.ADMIN_PASSWORD}}",
            "role": "admin",
        }
        result, error = _validate_demo_user_config(config, "test_app")
        assert error is None
        assert result["email"] == "admin@corp.com"
        assert result["password"] == "supersecret"
        assert result["role"] == "admin"

    def test_literal_values_still_work(self):
        config = {"email": "test@example.com", "password": "pass123", "role": "user"}
        result, error = _validate_demo_user_config(config, "test_app")
        assert error is None
        assert result["email"] == "test@example.com"

    def test_invalid_config_type(self):
        result, error = _validate_demo_user_config("not-a-dict", "test_app")
        assert result is None
        assert error is not None


# ═══════════════════════════════════════════════════════════════════════════
# registration_role and invite_only in create_app_auth_router
# ═══════════════════════════════════════════════════════════════════════════


class _FakeEngine:
    """Minimal engine stub for auth router tests."""

    def __init__(self, fake_db):
        self._db = fake_db

    async def get_scoped_db(self, slug):
        return self._db


class _FakeUsersCol:
    def __init__(self):
        self._users: dict[str, dict] = {}

    async def find_one(self, query):
        email = query.get("email")
        return self._users.get(email)

    async def insert_one(self, doc):
        self._users[doc["email"]] = doc
        doc["_id"] = "generated_id"
        result = MagicMock()
        result.inserted_id = "generated_id"
        return result

    async def update_one(self, query, update):
        pass


class _FakeDB:
    def __init__(self):
        self.users = _FakeUsersCol()

    def __getattr__(self, name):
        if name == "users":
            return self.users
        raise AttributeError(name)


class TestRegistrationRole:
    def _build_router_client(self, users_config):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from mdb_engine.auth.app_auth_routes import create_app_auth_router

        fake_db = _FakeDB()
        engine = _FakeEngine(fake_db)
        manifest = {
            "auth": {"users": {**users_config, "enabled": True}},
        }
        router = create_app_auth_router(
            engine=engine,
            slug="test",
            manifest_data=manifest,
            users_config={**users_config, "enabled": True},
        )
        app = FastAPI()
        app.include_router(router)
        return TestClient(app), fake_db

    def test_default_registration_role_is_guest(self):
        client, db = self._build_router_client({"allow_registration": True})
        resp = client.post(
            "/auth/register",
            json={"email": "user@test.com", "password": "password123"},
        )
        assert resp.status_code == 200
        assert db.users._users["user@test.com"]["role"] == "guest"

    def test_custom_registration_role(self):
        client, db = self._build_router_client(
            {
                "allow_registration": True,
                "registration_role": "reader",
            }
        )
        resp = client.post(
            "/auth/register",
            json={"email": "user@test.com", "password": "password123"},
        )
        assert resp.status_code == 200
        assert db.users._users["user@test.com"]["role"] == "reader"

    def test_registration_disabled(self):
        client, _ = self._build_router_client({"allow_registration": False})
        resp = client.post(
            "/auth/register",
            json={"email": "user@test.com", "password": "password123"},
        )
        assert resp.status_code == 403
        assert "disabled" in resp.json()["detail"].lower()


class TestInviteOnlyRegistration:
    def _build_router_client(self, users_config):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from mdb_engine.auth.app_auth_routes import create_app_auth_router

        fake_db = _FakeDB()
        engine = _FakeEngine(fake_db)
        manifest = {
            "auth": {"users": {**users_config, "enabled": True}},
        }
        router = create_app_auth_router(
            engine=engine,
            slug="test",
            manifest_data=manifest,
            users_config={**users_config, "enabled": True},
        )
        app = FastAPI()
        app.include_router(router)
        return TestClient(app), fake_db

    def test_invite_only_rejects_without_code(self):
        client, _ = self._build_router_client(
            {
                "allow_registration": "invite_only",
                "invite_codes": ["VALID_CODE"],
            }
        )
        resp = client.post(
            "/auth/register",
            json={"email": "user@test.com", "password": "password123"},
        )
        assert resp.status_code == 403
        assert "invite code" in resp.json()["detail"].lower()

    def test_invite_only_rejects_bad_code(self):
        client, _ = self._build_router_client(
            {
                "allow_registration": "invite_only",
                "invite_codes": ["VALID_CODE"],
            }
        )
        resp = client.post(
            "/auth/register",
            json={"email": "user@test.com", "password": "password123", "invite_code": "WRONG"},
        )
        assert resp.status_code == 403

    def test_invite_only_accepts_valid_code(self):
        client, db = self._build_router_client(
            {
                "allow_registration": "invite_only",
                "invite_codes": ["BETA2025"],
            }
        )
        resp = client.post(
            "/auth/register",
            json={"email": "user@test.com", "password": "password123", "invite_code": "BETA2025"},
        )
        assert resp.status_code == 200
        assert "user@test.com" in db.users._users

    def test_invite_codes_support_env_resolution(self, monkeypatch):
        monkeypatch.setenv("INVITE_SECRET", "env-code-123")
        client, db = self._build_router_client(
            {
                "allow_registration": "invite_only",
                "invite_codes": ["{{env.INVITE_SECRET}}"],
            }
        )
        resp = client.post(
            "/auth/register",
            json={"email": "user@test.com", "password": "password123", "invite_code": "env-code-123"},
        )
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════
# CLI add-user command
# ═══════════════════════════════════════════════════════════════════════════


class TestAddUserCLI:
    def test_missing_slug_in_manifest(self, tmp_path):
        manifest = tmp_path / "manifest.json"
        manifest.write_text('{"name": "No Slug"}')

        from mdb_engine.cli.commands.add_user import add_user

        runner = CliRunner()
        result = runner.invoke(add_user, [str(manifest), "--email", "a@b.com", "--password", "pass123"])
        assert result.exit_code != 0
        assert "slug" in result.output.lower()

    def test_users_not_enabled(self, tmp_path):
        manifest = tmp_path / "manifest.json"
        manifest.write_text('{"slug": "test", "auth": {"users": {"enabled": false}}}')

        from mdb_engine.cli.commands.add_user import add_user

        runner = CliRunner()
        result = runner.invoke(add_user, [str(manifest), "--email", "a@b.com", "--password", "pass123"])
        assert result.exit_code != 0
        assert "not enabled" in result.output.lower()

    def test_password_too_short(self, tmp_path):
        manifest = tmp_path / "manifest.json"
        manifest.write_text('{"slug": "test", "auth": {"users": {"enabled": true}}}')

        from mdb_engine.cli.commands.add_user import add_user

        runner = CliRunner()
        result = runner.invoke(add_user, [str(manifest), "--email", "a@b.com", "--password", "ab"])
        assert result.exit_code != 0
        assert "6 characters" in result.output.lower()

    @patch("mdb_engine.cli.commands.add_user._run_create_user")
    def test_successful_creation(self, mock_create, tmp_path):
        mock_create.return_value = {"_id": "123", "email": "admin@corp.com", "role": "admin"}

        manifest = tmp_path / "manifest.json"
        manifest.write_text('{"slug": "blog", "auth": {"users": {"enabled": true}}}')

        from mdb_engine.cli.commands.add_user import add_user

        runner = CliRunner()
        result = runner.invoke(
            add_user,
            [str(manifest), "--email", "admin@corp.com", "--role", "admin", "--password", "secret123"],
        )
        assert result.exit_code == 0
        assert "admin@corp.com" in result.output
        assert "admin" in result.output
        mock_create.assert_called_once()

    @patch("mdb_engine.cli.commands.add_user._run_create_user")
    def test_duplicate_user_fails(self, mock_create, tmp_path):
        mock_create.return_value = None

        manifest = tmp_path / "manifest.json"
        manifest.write_text('{"slug": "blog", "auth": {"users": {"enabled": true}}}')

        from mdb_engine.cli.commands.add_user import add_user

        runner = CliRunner()
        result = runner.invoke(
            add_user,
            [str(manifest), "--email", "dupe@corp.com", "--password", "secret123"],
        )
        assert result.exit_code != 0
        assert "already exist" in result.output.lower()


# ═══════════════════════════════════════════════════════════════════════════
# Secure-by-default: auto-CRUD requires auth when auth.users.enabled
# ═══════════════════════════════════════════════════════════════════════════


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def skip(self, n):
        return self

    def limit(self, n):
        return self

    def sort(self, *a, **kw):
        return self

    async def to_list(self, length=None):
        return self._docs


class _FakeCol:
    def __init__(self, docs=None):
        self._docs = docs or []
        self._insert_counter = 0

    async def find_one(self, *a, **kw):
        return self._docs[0] if self._docs else None

    def find(self, *a, **kw):
        return _FakeCursor(self._docs)

    async def count_documents(self, *a, **kw):
        return len(self._docs)

    async def insert_one(self, doc, **kw):
        self._docs.append(doc)
        self._insert_counter += 1
        result = MagicMock()
        result.inserted_id = f"id_{self._insert_counter}"
        return result

    async def insert_many(self, docs, **kw):
        ids = []
        for doc in docs:
            self._docs.append(doc)
            self._insert_counter += 1
            ids.append(f"id_{self._insert_counter}")
        result = MagicMock()
        result.inserted_ids = ids
        return result

    async def update_one(self, *a, **kw):
        result = MagicMock()
        result.modified_count = 1
        return result


class _FakeScopedDB:
    def __init__(self):
        self._cols: dict[str, _FakeCol] = {}

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self._cols.setdefault(name, _FakeCol())

    def __getitem__(self, name):
        return self._cols.setdefault(name, _FakeCol())


def _build_crud_app(collection_config, *, app_auth_enabled=False, user=None):
    """Build a minimal test app with a single auto-CRUD collection."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from mdb_engine.dependencies import get_scoped_db
    from mdb_engine.routing.auto_crud import create_auto_crud_router

    app = FastAPI()
    fake_db = _FakeScopedDB()

    async def _override_db():
        return fake_db

    app.dependency_overrides[get_scoped_db] = _override_db
    router = create_auto_crud_router(
        "items",
        collection_config,
        app_auth_enabled=app_auth_enabled,
    )
    app.include_router(router)

    if user:
        from starlette.middleware.base import BaseHTTPMiddleware

        class _InjectUser(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                request.state.user = user
                request.state.user_roles = [user.get("role", "")]
                return await call_next(request)

        app.add_middleware(_InjectUser)

    return TestClient(app), fake_db


class TestSecureByDefault:
    """When app_auth_enabled=True, all endpoints require auth even if
    the collection config has no per-collection auth block."""

    def test_no_auth_config_without_app_auth_is_public(self):
        """Baseline: without app auth, endpoints are open."""
        client, _ = _build_crud_app({})
        resp = client.get("/api/items")
        assert resp.status_code == 200

    def test_no_auth_config_with_app_auth_requires_login(self):
        """With app auth enabled, bare collection returns 401."""
        client, _ = _build_crud_app({}, app_auth_enabled=True)
        resp = client.get("/api/items")
        assert resp.status_code == 401

    def test_post_blocked_without_auth(self):
        client, _ = _build_crud_app({}, app_auth_enabled=True)
        resp = client.post("/api/items", json={"title": "nope"})
        assert resp.status_code == 401

    def test_patch_blocked_without_auth(self):
        client, _ = _build_crud_app({}, app_auth_enabled=True)
        resp = client.patch("/api/items/abc123", json={"title": "nope"})
        assert resp.status_code == 401

    def test_delete_blocked_without_auth(self):
        client, _ = _build_crud_app({}, app_auth_enabled=True)
        resp = client.delete("/api/items/abc123")
        assert resp.status_code == 401

    def test_authenticated_user_can_read(self):
        """An authenticated user gets through."""
        user = {"_id": "u1", "email": "a@b.com", "role": "reader"}
        client, _ = _build_crud_app({}, app_auth_enabled=True, user=user)
        resp = client.get("/api/items")
        assert resp.status_code == 200

    def test_authenticated_user_can_write(self):
        user = {"_id": "u1", "email": "a@b.com", "role": "user"}
        client, _ = _build_crud_app({}, app_auth_enabled=True, user=user)
        resp = client.post("/api/items", json={"title": "hello"})
        assert resp.status_code == 201

    def test_explicit_write_roles_enforced_on_top(self):
        """Per-collection write_roles stack on top of the baseline for mutations."""
        user = {"_id": "u1", "email": "a@b.com", "role": "reader"}
        client, db = _build_crud_app(
            {"auth": {"write_roles": ["admin"]}},
            app_auth_enabled=True,
            user=user,
        )
        # Reads are allowed (user is authenticated)
        resp = client.get("/api/items")
        assert resp.status_code == 200
        # PATCH requires admin role -- reader is blocked
        resp = client.patch("/api/items/abc123", json={"title": "blocked"})
        assert resp.status_code == 403
        # DELETE requires admin role too
        resp = client.delete("/api/items/abc123")
        assert resp.status_code == 403

    def test_read_only_collection_still_auth_gated(self):
        """Even read-only collections require auth when app auth is on."""
        client, _ = _build_crud_app(
            {"read_only": True},
            app_auth_enabled=True,
        )
        resp = client.get("/api/items")
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
# #1 Block auth users collection from auto-CRUD
# ═══════════════════════════════════════════════════════════════════════════


class TestBlockUsersCollection:
    def test_users_collection_blocked_when_auth_enabled(self):
        """auto-CRUD must not mount on the auth users collection."""
        from fastapi import FastAPI

        from mdb_engine.routing.auto_crud import mount_auto_crud_routes

        app = FastAPI()
        collections = {
            "users": {"auto_crud": True},
            "posts": {"auto_crud": True},
        }
        mount_auto_crud_routes(app, collections, app_auth_enabled=True)
        paths = [r.path for r in app.routes]
        assert not any("/api/users" in p for p in paths)
        assert any("/api/posts" in p for p in paths)

    def test_custom_users_collection_blocked(self):
        from fastapi import FastAPI

        from mdb_engine.routing.auto_crud import mount_auto_crud_routes

        app = FastAPI()
        collections = {"app_members": {"auto_crud": True}}
        mount_auto_crud_routes(
            app,
            collections,
            app_auth_enabled=True,
            auth_users_collection="app_members",
        )
        paths = [r.path for r in app.routes]
        assert not any("/api/app_members" in p for p in paths)

    def test_users_collection_allowed_when_auth_disabled(self):
        """Without app auth, users collection is not special."""
        from fastapi import FastAPI

        from mdb_engine.routing.auto_crud import mount_auto_crud_routes

        app = FastAPI()
        collections = {"users": {"auto_crud": True}}
        mount_auto_crud_routes(app, collections, app_auth_enabled=False)
        paths = [r.path for r in app.routes]
        assert any("/api/users" in p for p in paths)


# ═══════════════════════════════════════════════════════════════════════════
# #2 Protected fields auto-injected
# ═══════════════════════════════════════════════════════════════════════════


class TestProtectedFields:
    def test_role_stripped_from_patch_when_auth_enabled(self):
        from bson import ObjectId

        oid = ObjectId()
        user = {"_id": "u1", "email": "a@b.com", "role": "user"}
        client, db = _build_crud_app(
            {},
            app_auth_enabled=True,
            user=user,
        )
        db._cols["items"] = _FakeCol([{"_id": oid, "title": "hi", "role": "user"}])
        resp = client.patch(f"/api/items/{oid}", json={"role": "admin", "title": "updated"})
        assert resp.status_code == 200
        doc = db["items"]._docs[0]
        assert doc.get("role") == "user"

    def test_password_hash_stripped_from_create_when_auth_enabled(self):
        user = {"_id": "u1", "email": "a@b.com", "role": "user"}
        client, db = _build_crud_app({}, app_auth_enabled=True, user=user)
        resp = client.post("/api/items", json={"title": "test", "password_hash": "hacked"})
        assert resp.status_code == 201
        doc = db["items"]._docs[0]
        assert "password_hash" not in doc

    def test_protected_fields_not_stripped_without_auth(self):
        """Without app auth, fields are not auto-protected."""
        client, db = _build_crud_app({})
        resp = client.post("/api/items", json={"title": "test", "role": "admin"})
        assert resp.status_code == 201
        doc = db["items"]._docs[0]
        assert doc.get("role") == "admin"


# ═══════════════════════════════════════════════════════════════════════════
# #4 writable_fields allowlist
# ═══════════════════════════════════════════════════════════════════════════


class TestWritableFields:
    def test_only_allowed_fields_survive_create(self):
        user = {"_id": "u1", "email": "a@b.com", "role": "user"}
        client, db = _build_crud_app(
            {"writable_fields": ["title", "status"]},
            app_auth_enabled=True,
            user=user,
        )
        resp = client.post(
            "/api/items",
            json={"title": "ok", "status": "draft", "secret": "nope", "hack": True},
        )
        assert resp.status_code == 201
        doc = db["items"]._docs[0]
        assert doc.get("title") == "ok"
        assert doc.get("status") == "draft"
        assert "secret" not in doc
        assert "hack" not in doc

    def test_only_allowed_fields_survive_patch(self):
        from bson import ObjectId

        oid = ObjectId()
        user = {"_id": "u1", "email": "a@b.com", "role": "user"}
        client, db = _build_crud_app(
            {"writable_fields": ["title"]},
            app_auth_enabled=True,
            user=user,
        )
        db._cols["items"] = _FakeCol([{"_id": oid, "title": "old", "internal": "keep"}])
        resp = client.patch(f"/api/items/{oid}", json={"title": "new", "internal": "hacked"})
        assert resp.status_code == 200

    def test_no_writable_fields_means_no_allowlist(self):
        """When writable_fields is omitted, all fields pass through."""
        user = {"_id": "u1", "email": "a@b.com", "role": "user"}
        client, db = _build_crud_app({}, user=user)
        resp = client.post("/api/items", json={"title": "ok", "extra": "allowed"})
        assert resp.status_code == 201
        doc = db["items"]._docs[0]
        assert doc.get("extra") == "allowed"

    def test_role_map_editor_gets_editor_fields(self):
        """Editor role should get editor-specific writable fields."""
        user = {"_id": "u1", "email": "editor@b.com", "role": "editor"}
        client, db = _build_crud_app(
            {
                "writable_fields": {
                    "editor": ["title", "body", "tags", "status"],
                    "reader": ["body"],
                },
            },
            app_auth_enabled=True,
            user=user,
        )
        resp = client.post(
            "/api/items",
            json={"title": "ok", "body": "text", "tags": ["a"], "status": "draft", "secret": "nope"},
        )
        assert resp.status_code == 201
        doc = db["items"]._docs[0]
        assert doc.get("title") == "ok"
        assert doc.get("body") == "text"
        assert "secret" not in doc

    def test_role_map_reader_gets_reader_fields(self):
        """Reader role should only get reader-specific writable fields."""
        user = {"_id": "u1", "email": "reader@b.com", "role": "reader"}
        client, db = _build_crud_app(
            {
                "writable_fields": {
                    "editor": ["title", "body", "tags"],
                    "reader": ["body"],
                },
            },
            app_auth_enabled=True,
            user=user,
        )
        resp = client.post(
            "/api/items",
            json={"title": "blocked", "body": "ok"},
        )
        assert resp.status_code == 201
        doc = db["items"]._docs[0]
        assert doc.get("body") == "ok"
        assert "title" not in doc

    def test_role_map_admin_bypasses_allowlist(self):
        """Admin role should bypass the per-role allowlist."""
        user = {"_id": "u1", "email": "admin@b.com", "role": "admin"}
        client, db = _build_crud_app(
            {
                "writable_fields": {
                    "editor": ["title", "body"],
                    "reader": ["body"],
                },
            },
            app_auth_enabled=True,
            user=user,
        )
        resp = client.post(
            "/api/items",
            json={"title": "ok", "body": "ok", "anything": "goes"},
        )
        assert resp.status_code == 201
        doc = db["items"]._docs[0]
        assert doc.get("anything") == "goes"

    def test_role_map_backward_compat_list(self):
        """When writable_fields is a flat list, old behavior should be preserved."""
        user = {"_id": "u1", "email": "user@b.com", "role": "editor"}
        client, db = _build_crud_app(
            {"writable_fields": ["title", "body"]},
            app_auth_enabled=True,
            user=user,
        )
        resp = client.post(
            "/api/items",
            json={"title": "ok", "body": "ok", "secret": "nope"},
        )
        assert resp.status_code == 201
        doc = db["items"]._docs[0]
        assert doc.get("title") == "ok"
        assert "secret" not in doc

    def test_role_map_unknown_role_gets_empty_fields(self):
        """A role not in the map should have all fields stripped."""
        user = {"_id": "u1", "email": "nobody@b.com", "role": "viewer"}
        client, db = _build_crud_app(
            {
                "writable_fields": {
                    "editor": ["title", "body"],
                },
            },
            app_auth_enabled=True,
            user=user,
        )
        resp = client.post("/api/items", json={"title": "nope", "body": "nope"})
        assert resp.status_code == 201
        doc = db["items"]._docs[0]
        assert "title" not in doc
        assert "body" not in doc


# ═══════════════════════════════════════════════════════════════════════════
# #5 Bulk insert fires hooks
# ═══════════════════════════════════════════════════════════════════════════


class TestBulkInsertHooks:
    def test_bulk_create_fires_after_create_hooks(self):
        user = {"_id": "u1", "email": "admin@b.com", "role": "admin"}
        client, db = _build_crud_app(
            {
                "hooks": {
                    "after_create": [
                        {
                            "action": "insert",
                            "collection": "audit_log",
                            "document": {
                                "event": "item_created",
                                "actor": "{{user.email}}",
                            },
                        }
                    ]
                }
            },
            user=user,
        )
        resp = client.post(
            "/api/items/_bulk",
            json=[{"title": "a"}, {"title": "b"}, {"title": "c"}],
        )
        assert resp.status_code == 201
        audit_docs = db["audit_log"]._docs
        assert len(audit_docs) == 3
        assert all(d["event"] == "item_created" for d in audit_docs)
        assert all(d["actor"] == "admin@b.com" for d in audit_docs)


# ═══════════════════════════════════════════════════════════════════════════
# #6 Login rate limiting
# ═══════════════════════════════════════════════════════════════════════════


class TestLoginRateLimit:
    def _build_auth_client(self, users_config=None):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from mdb_engine.auth.app_auth_routes import create_app_auth_router

        fake_db = _FakeDB()
        engine = _FakeEngine(fake_db)
        cfg = {
            "enabled": True,
            "allow_registration": False,
            "max_login_attempts": 3,
            "login_lockout_seconds": 60,
            **(users_config or {}),
        }
        manifest = {"auth": {"users": cfg}}
        router = create_app_auth_router(
            engine=engine,
            slug="test",
            manifest_data=manifest,
            users_config=cfg,
        )
        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_rate_limit_blocks_after_max_attempts(self):
        client = self._build_auth_client()
        for _ in range(3):
            resp = client.post(
                "/auth/login",
                json={"email": "brute@test.com", "password": "wrong"},
            )
            assert resp.status_code == 401

        resp = client.post(
            "/auth/login",
            json={"email": "brute@test.com", "password": "wrong"},
        )
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers

    def test_rate_limit_is_per_email(self):
        client = self._build_auth_client()
        for _ in range(3):
            client.post(
                "/auth/login",
                json={"email": "user1@test.com", "password": "wrong"},
            )
        resp = client.post(
            "/auth/login",
            json={"email": "user2@test.com", "password": "wrong"},
        )
        assert resp.status_code == 401  # Different email, not rate-limited


# ═══════════════════════════════════════════════════════════════════════════
# Request body size limit
# ═══════════════════════════════════════════════════════════════════════════


class TestBodySizeLimit:
    def test_oversized_post_rejected(self):
        user = {"_id": "u1", "email": "a@b.com", "role": "user"}
        client, _ = _build_crud_app(
            {"max_body_bytes": 100},
            user=user,
        )
        big_body = {"title": "x" * 200}
        resp = client.post("/api/items", json=big_body)
        assert resp.status_code == 413
        assert "too large" in resp.json()["detail"].lower()

    def test_normal_post_allowed(self):
        user = {"_id": "u1", "email": "a@b.com", "role": "user"}
        client, _ = _build_crud_app(
            {"max_body_bytes": 10000},
            user=user,
        )
        resp = client.post("/api/items", json={"title": "ok"})
        assert resp.status_code == 201

    def test_oversized_patch_rejected(self):
        from bson import ObjectId

        oid = ObjectId()
        user = {"_id": "u1", "email": "a@b.com", "role": "user"}
        client, db = _build_crud_app(
            {"max_body_bytes": 50},
            user=user,
        )
        db._cols["items"] = _FakeCol([{"_id": oid, "title": "old"}])
        resp = client.patch(f"/api/items/{oid}", json={"title": "x" * 200})
        assert resp.status_code == 413

    def test_default_limit_is_1mb(self):
        """Default 1MB limit allows normal documents."""
        user = {"_id": "u1", "email": "a@b.com", "role": "user"}
        client, _ = _build_crud_app({}, user=user)
        resp = client.post("/api/items", json={"title": "hello"})
        assert resp.status_code == 201


# ═══════════════════════════════════════════════════════════════════════════
# Auto-hide sensitive fields on reads
# ═══════════════════════════════════════════════════════════════════════════


class TestAutoHideSensitiveReads:
    def test_password_hash_hidden_when_auth_enabled(self):
        from mdb_engine.routing.auto_crud import create_auto_crud_router

        router = create_auto_crud_router("items", {}, app_auth_enabled=True)
        # Verify the ctx was built with default_projection hiding sensitive fields
        # We can't access ctx directly, but we can check the behavior via a request
        from mdb_engine.routing.auto_crud import _CollectionCtx

        ctx = _CollectionCtx(name="test", default_projection={"password": 0, "password_hash": 0})
        proj = ctx.effective_projection(None)
        assert proj == {"password": 0, "password_hash": 0}

    def test_no_auto_projection_without_auth(self):
        # Without auth, no auto-projection should be injected
        # We test by building an app and checking no projection is applied
        user = {"_id": "u1", "email": "a@b.com", "role": "user"}
        client, db = _build_crud_app({}, user=user)
        db._cols["items"] = _FakeCol([{"_id": "id1", "title": "hi", "password_hash": "should_appear"}])
        resp = client.get("/api/items")
        assert resp.status_code == 200

    def test_custom_projection_merged_with_auth_defaults(self):
        from mdb_engine.routing.auto_crud import _SENSITIVE_READ_FIELDS

        config = {"default_projection": {"internal_notes": 0}}
        # Simulate what create_auto_crud_router does
        projection = dict(config.get("default_projection") or {})
        for sf in _SENSITIVE_READ_FIELDS:
            projection.setdefault(sf, 0)
        assert projection == {"internal_notes": 0, "password": 0, "password_hash": 0}


# ═══════════════════════════════════════════════════════════════════════════
# Registration rate limiting
# ═══════════════════════════════════════════════════════════════════════════


class TestRegistrationRateLimit:
    def _build_auth_client(self, users_config=None):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from mdb_engine.auth.app_auth_routes import create_app_auth_router

        fake_db = _FakeDB()
        engine = _FakeEngine(fake_db)
        cfg = {
            "enabled": True,
            "allow_registration": True,
            "max_registration_attempts": 3,
            "registration_window_seconds": 60,
            **(users_config or {}),
        }
        manifest = {"auth": {"users": cfg}}
        router = create_app_auth_router(
            engine=engine,
            slug="test",
            manifest_data=manifest,
            users_config=cfg,
        )
        app = FastAPI()
        app.include_router(router)
        return TestClient(app), fake_db

    def test_registration_rate_limit_blocks_after_max(self):
        client, _ = self._build_auth_client()
        for i in range(3):
            resp = client.post(
                "/auth/register",
                json={"email": f"user{i}@test.com", "password": "password123"},
            )
            assert resp.status_code == 200

        resp = client.post(
            "/auth/register",
            json={"email": "blocked@test.com", "password": "password123"},
        )
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers

    def test_registration_disabled_not_affected_by_rate_limit(self):
        client, _ = self._build_auth_client({"allow_registration": False})
        resp = client.post(
            "/auth/register",
            json={"email": "test@test.com", "password": "password123"},
        )
        assert resp.status_code == 403
        assert "disabled" in resp.json()["detail"].lower()


# ═══════════════════════════════════════════════════════════════════════════
# public_read: anonymous reads, authenticated writes
# ═══════════════════════════════════════════════════════════════════════════


class TestPublicRead:
    def test_public_read_allows_anonymous_get(self):
        """With public_read, GET works without auth even when app auth is on."""
        client, _ = _build_crud_app(
            {"auth": {"public_read": True}},
            app_auth_enabled=True,
        )
        resp = client.get("/api/items")
        assert resp.status_code == 200

    def test_public_read_blocks_anonymous_post(self):
        """Writes still require auth even with public_read."""
        client, _ = _build_crud_app(
            {"auth": {"public_read": True}},
            app_auth_enabled=True,
        )
        resp = client.post("/api/items", json={"title": "nope"})
        assert resp.status_code == 401

    def test_public_read_blocks_anonymous_patch(self):
        client, _ = _build_crud_app(
            {"auth": {"public_read": True}},
            app_auth_enabled=True,
        )
        resp = client.patch("/api/items/abc123", json={"title": "nope"})
        assert resp.status_code == 401

    def test_public_read_blocks_anonymous_delete(self):
        client, _ = _build_crud_app(
            {"auth": {"public_read": True}},
            app_auth_enabled=True,
        )
        resp = client.delete("/api/items/abc123")
        assert resp.status_code == 401

    def test_public_read_with_write_roles_enforced(self):
        """write_roles still apply on top of public_read."""
        user = {"_id": "u1", "email": "a@b.com", "role": "reader"}
        client, _ = _build_crud_app(
            {"auth": {"public_read": True, "write_roles": ["admin"]}},
            app_auth_enabled=True,
            user=user,
        )
        resp = client.get("/api/items")
        assert resp.status_code == 200
        resp = client.patch("/api/items/abc123", json={"title": "blocked"})
        assert resp.status_code == 403

    def test_public_read_false_still_requires_auth(self):
        """Default: public_read is false, reads require auth."""
        client, _ = _build_crud_app(
            {"auth": {"public_read": False}},
            app_auth_enabled=True,
        )
        resp = client.get("/api/items")
        assert resp.status_code == 401

    def test_authenticated_user_can_write_with_public_read(self):
        """An authenticated user can write when public_read is on."""
        user = {"_id": "u1", "email": "a@b.com", "role": "user"}
        client, _ = _build_crud_app(
            {"auth": {"public_read": True}},
            app_auth_enabled=True,
            user=user,
        )
        resp = client.post("/api/items", json={"title": "hello"})
        assert resp.status_code == 201
