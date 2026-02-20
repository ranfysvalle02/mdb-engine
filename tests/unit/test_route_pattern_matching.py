"""Tests for route pattern matching and the protected_routes / default_policy features."""

from mdb_engine.auth.shared_middleware import _match_route_patterns


class TestMatchRoutePatterns:
    """Tests for _match_route_patterns helper."""

    def test_exact_match(self):
        assert _match_route_patterns("/health", ["/health"]) is True

    def test_exact_no_match(self):
        assert _match_route_patterns("/other", ["/health"]) is False

    def test_single_wildcard(self):
        assert _match_route_patterns("/api/foo", ["/api/*"]) is True
        assert _match_route_patterns("/api/foo/bar", ["/api/*"]) is True

    def test_single_wildcard_in_middle(self):
        assert _match_route_patterns("/api/v1/items", ["/api/*/items"]) is True
        assert _match_route_patterns("/api/v2/items", ["/api/*/items"]) is True

    def test_double_star_matches_any_depth(self):
        assert _match_route_patterns("/api/foo", ["/api/**"]) is True
        assert _match_route_patterns("/api/foo/bar", ["/api/**"]) is True
        assert _match_route_patterns("/api/foo/bar/baz", ["/api/**"]) is True
        assert _match_route_patterns("/api/", ["/api/**"]) is True

    def test_double_star_no_match_different_prefix(self):
        assert _match_route_patterns("/other/foo", ["/api/**"]) is False

    def test_multiple_patterns(self):
        patterns = ["/health", "/api/**"]
        assert _match_route_patterns("/health", patterns) is True
        assert _match_route_patterns("/api/deep/path", patterns) is True
        assert _match_route_patterns("/admin", patterns) is False

    def test_empty_patterns_list(self):
        assert _match_route_patterns("/anything", []) is False

    def test_pattern_without_leading_slash_normalized(self):
        assert _match_route_patterns("/health", ["health"]) is True

    def test_root_path(self):
        assert _match_route_patterns("/", ["/"]) is True


class TestProtectedRoutesInversion:
    """Tests for default_policy='public' with protected_routes."""

    def test_public_policy_everything_public_by_default(self):
        from unittest.mock import AsyncMock, MagicMock

        from mdb_engine.auth.shared_middleware import SharedAuthMiddleware

        middleware = SharedAuthMiddleware(
            app=AsyncMock(),
            user_pool=MagicMock(),
            app_slug="test",
            default_policy="public",
            protected_routes=["/admin/**"],
        )
        assert middleware._is_public_route("/") is True
        assert middleware._is_public_route("/api/data") is True
        assert middleware._is_public_route("/admin/users") is False
        assert middleware._is_public_route("/admin/settings/deep") is False

    def test_protected_policy_everything_protected_by_default(self):
        from unittest.mock import AsyncMock, MagicMock

        from mdb_engine.auth.shared_middleware import SharedAuthMiddleware

        middleware = SharedAuthMiddleware(
            app=AsyncMock(),
            user_pool=MagicMock(),
            app_slug="test",
            default_policy="protected",
            public_routes=["/health", "/api/**"],
        )
        assert middleware._is_public_route("/health") is True
        assert middleware._is_public_route("/api/data") is True
        assert middleware._is_public_route("/admin") is False
        assert middleware._is_public_route("/settings") is False

    def test_glob_in_protected_routes(self):
        from unittest.mock import AsyncMock, MagicMock

        from mdb_engine.auth.shared_middleware import SharedAuthMiddleware

        middleware = SharedAuthMiddleware(
            app=AsyncMock(),
            user_pool=MagicMock(),
            app_slug="test",
            default_policy="public",
            protected_routes=["/api/admin/**", "/api/settings/**"],
        )
        assert middleware._is_public_route("/api/public") is True
        assert middleware._is_public_route("/api/admin/users") is False
        assert middleware._is_public_route("/api/settings/profile") is False
