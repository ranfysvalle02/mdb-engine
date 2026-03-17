"""
Tests for multi-role users and role hierarchy.

Covers:
- get_effective_roles with single role, multi-role, and hierarchy
- require_role dependency with hierarchy expansion
- Backward compatibility with single string role
"""

from __future__ import annotations

from mdb_engine.dependencies import get_effective_roles


class TestGetEffectiveRoles:
    def test_single_role_string(self):
        user = {"_id": "u1", "role": "editor"}
        assert get_effective_roles(user) == {"editor"}

    def test_roles_array(self):
        user = {"_id": "u1", "roles": ["editor", "moderator"]}
        assert get_effective_roles(user) == {"editor", "moderator"}

    def test_both_role_and_roles(self):
        user = {"_id": "u1", "role": "admin", "roles": ["editor"]}
        assert get_effective_roles(user) == {"admin", "editor"}

    def test_empty_user(self):
        user = {"_id": "u1"}
        assert get_effective_roles(user) == set()

    def test_hierarchy_expansion(self):
        user = {"_id": "u1", "role": "admin"}
        hierarchy = {
            "admin": ["editor", "moderator"],
            "editor": ["reader"],
            "moderator": ["reader"],
        }
        result = get_effective_roles(user, hierarchy)
        assert result == {"admin", "editor", "moderator", "reader"}

    def test_hierarchy_no_expansion_without_hierarchy(self):
        user = {"_id": "u1", "role": "admin"}
        result = get_effective_roles(user, None)
        assert result == {"admin"}

    def test_hierarchy_transitive(self):
        """admin -> editor -> reader (transitive chain)."""
        user = {"_id": "u1", "role": "admin"}
        hierarchy = {
            "admin": ["editor"],
            "editor": ["reader"],
        }
        result = get_effective_roles(user, hierarchy)
        assert result == {"admin", "editor", "reader"}

    def test_hierarchy_with_multi_role(self):
        user = {"_id": "u1", "roles": ["editor", "moderator"]}
        hierarchy = {
            "editor": ["reader"],
            "moderator": ["reader"],
        }
        result = get_effective_roles(user, hierarchy)
        assert result == {"editor", "moderator", "reader"}

    def test_hierarchy_prevents_infinite_loop(self):
        """Circular hierarchy should not loop forever."""
        user = {"_id": "u1", "role": "a"}
        hierarchy = {"a": ["b"], "b": ["a"]}
        result = get_effective_roles(user, hierarchy)
        assert result == {"a", "b"}

    def test_role_not_in_hierarchy(self):
        """Role not present in hierarchy mapping keeps itself."""
        user = {"_id": "u1", "role": "custom"}
        hierarchy = {"admin": ["editor"]}
        result = get_effective_roles(user, hierarchy)
        assert result == {"custom"}


class TestRequireRoleWithHierarchy:
    """Integration-style tests using FastAPI TestClient."""

    def test_admin_inherits_editor_role(self):
        from fastapi import Depends, FastAPI
        from fastapi.testclient import TestClient
        from starlette.middleware.base import BaseHTTPMiddleware

        from mdb_engine.dependencies import require_role

        app = FastAPI()
        app.state.role_hierarchy = {
            "admin": ["editor", "reader"],
            "editor": ["reader"],
        }

        class InjectUser(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                request.state.user = {"_id": "u1", "role": "admin"}
                request.state.user_roles = ["admin"]
                return await call_next(request)

        app.add_middleware(InjectUser)

        @app.get("/editor-only")
        async def editor_route(user=Depends(require_role("editor"))):
            return {"ok": True}

        client = TestClient(app)
        resp = client.get("/editor-only")
        assert resp.status_code == 200

    def test_reader_cannot_access_editor_route(self):
        from fastapi import Depends, FastAPI
        from fastapi.testclient import TestClient
        from starlette.middleware.base import BaseHTTPMiddleware

        from mdb_engine.dependencies import require_role

        app = FastAPI()
        app.state.role_hierarchy = {
            "admin": ["editor", "reader"],
            "editor": ["reader"],
        }

        class InjectUser(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                request.state.user = {"_id": "u2", "role": "reader"}
                request.state.user_roles = ["reader"]
                return await call_next(request)

        app.add_middleware(InjectUser)

        @app.get("/editor-only")
        async def editor_route(user=Depends(require_role("editor"))):
            return {"ok": True}

        client = TestClient(app)
        resp = client.get("/editor-only")
        assert resp.status_code == 403

    def test_backward_compat_no_hierarchy(self):
        from fastapi import Depends, FastAPI
        from fastapi.testclient import TestClient
        from starlette.middleware.base import BaseHTTPMiddleware

        from mdb_engine.dependencies import require_role

        app = FastAPI()

        class InjectUser(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                request.state.user = {"_id": "u1", "role": "admin"}
                request.state.user_roles = ["admin"]
                return await call_next(request)

        app.add_middleware(InjectUser)

        @app.get("/admin-only")
        async def admin_route(user=Depends(require_role("admin"))):
            return {"ok": True}

        client = TestClient(app)
        resp = client.get("/admin-only")
        assert resp.status_code == 200
