"""Tests for mdb_engine.dependencies.PlatformInfo and get_platform_info."""

from mdb_engine.dependencies import PlatformInfo


class TestPlatformInfo:
    """Tests for the PlatformInfo data class."""

    def test_basic_construction(self):
        info = PlatformInfo(
            current_slug="app-1",
            current_path="/app-1",
            apps=[
                {"slug": "app-1", "path_prefix": "/app-1", "status": "mounted"},
                {"slug": "app-2", "path_prefix": "/app-2", "status": "mounted"},
            ],
        )
        assert info.current_slug == "app-1"
        assert info.current_path == "/app-1"
        assert len(info.apps) == 2

    def test_get_app_found(self):
        info = PlatformInfo(
            current_slug="a",
            current_path="/a",
            apps=[
                {"slug": "a", "path_prefix": "/a"},
                {"slug": "b", "path_prefix": "/b"},
            ],
        )
        found = info.get_app("b")
        assert found is not None
        assert found["slug"] == "b"

    def test_get_app_not_found(self):
        info = PlatformInfo(current_slug="a", current_path="/a", apps=[])
        assert info.get_app("nonexistent") is None

    def test_empty_apps(self):
        info = PlatformInfo(current_slug="solo", current_path="/solo", apps=[])
        assert info.apps == []
        assert info.get_app("solo") is None


class TestGetPlatformInfoDependency:
    """Tests for the get_platform_info FastAPI dependency."""

    async def test_returns_platform_info(self):
        from unittest.mock import MagicMock

        from mdb_engine.dependencies import get_platform_info

        request = MagicMock()
        request.app.state.app_slug = "child-1"
        request.state.app_base_path = "/child-1"
        request.state.mounted_apps = {
            "child-1": {"slug": "child-1", "path_prefix": "/child-1", "status": "mounted"},
            "child-2": {"slug": "child-2", "path_prefix": "/child-2", "status": "mounted"},
        }

        info = await get_platform_info(request)
        assert isinstance(info, PlatformInfo)
        assert info.current_slug == "child-1"
        assert len(info.apps) == 2

    async def test_empty_when_no_mounted_apps(self):
        from unittest.mock import MagicMock

        from mdb_engine.dependencies import get_platform_info

        request = MagicMock()
        request.app.state.app_slug = "solo"
        request.state.app_base_path = ""
        request.state.mounted_apps = None

        info = await get_platform_info(request)
        assert info.apps == []
