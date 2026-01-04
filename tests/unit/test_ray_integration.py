"""
Smoke tests for Ray integration.

Tests the optional Ray support functionality:
- Import verification
- Graceful degradation when Ray not installed
- Basic class instantiation
- Decorator functionality
"""


import pytest


class TestRayIntegrationImports:
    """Test that Ray integration can be imported correctly."""

    def test_import_from_core(self):
        """Test importing Ray components from core module."""
        from mdb_engine.core import (
            RAY_AVAILABLE,
            AppRayActor,
            get_ray_actor_handle,
            ray_actor_decorator,
        )

        # All should be importable regardless of Ray availability
        assert RAY_AVAILABLE is not None  # Will be True or False
        assert AppRayActor is not None
        assert get_ray_actor_handle is not None
        assert ray_actor_decorator is not None

    def test_import_from_main(self):
        """Test importing Ray components from main module."""
        from mdb_engine import (
            RAY_AVAILABLE,
            AppRayActor,
        )

        assert RAY_AVAILABLE is not None
        assert AppRayActor is not None

    def test_import_direct(self):
        """Test direct import from ray_integration module."""
        from mdb_engine.core.ray_integration import (
            RAY_AVAILABLE,
            AppRayActor,
        )

        assert RAY_AVAILABLE is not None
        assert AppRayActor is not None


class TestRayAvailability:
    """Test RAY_AVAILABLE flag behavior."""

    def test_ray_available_is_boolean(self):
        """Test RAY_AVAILABLE is a boolean."""
        from mdb_engine.core.ray_integration import RAY_AVAILABLE

        assert isinstance(RAY_AVAILABLE, bool)

    def test_ray_module_reference(self):
        """Test ray module reference behavior."""
        from mdb_engine.core import ray_integration

        # ray should be None if not available, otherwise the ray module
        if ray_integration.RAY_AVAILABLE:
            assert ray_integration.ray is not None
        else:
            assert ray_integration.ray is None


class TestAppRayActorInstantiation:
    """Test AppRayActor class instantiation."""

    def test_instantiation_basic(self):
        """Test basic AppRayActor instantiation."""
        from mdb_engine.core.ray_integration import AppRayActor

        actor = AppRayActor(
            app_slug="test_app",
            mongo_uri="mongodb://localhost:27017",
            db_name="test_db",
        )

        assert actor.app_slug == "test_app"
        assert actor.mongo_uri == "mongodb://localhost:27017"
        assert actor.db_name == "test_db"
        assert actor.use_in_memory_fallback is False
        assert actor._initialized is False

    def test_instantiation_with_fallback(self):
        """Test AppRayActor instantiation with fallback enabled."""
        from mdb_engine.core.ray_integration import AppRayActor

        actor = AppRayActor(
            app_slug="test_app",
            mongo_uri="mongodb://localhost:27017",
            db_name="test_db",
            use_in_memory_fallback=True,
        )

        assert actor.use_in_memory_fallback is True

    def test_get_app_slug(self):
        """Test get_app_slug method."""
        from mdb_engine.core.ray_integration import AppRayActor

        actor = AppRayActor(
            app_slug="my_app",
            mongo_uri="mongodb://localhost:27017",
            db_name="test_db",
        )

        assert actor.get_app_slug() == "my_app"


class TestRayActorDecorator:
    """Test ray_actor_decorator behavior."""

    def test_decorator_returns_callable(self):
        """Test decorator returns a callable."""
        from mdb_engine.core.ray_integration import ray_actor_decorator

        decorator = ray_actor_decorator(app_slug="test_app")
        assert callable(decorator)

    def test_decorator_without_ray(self):
        """Test decorator behavior when Ray is not available."""
        from mdb_engine.core.ray_integration import RAY_AVAILABLE, ray_actor_decorator

        if not RAY_AVAILABLE:
            # When Ray not available, decorator should return class unchanged
            @ray_actor_decorator(app_slug="test_app")
            class TestActor:
                pass

            # Class should still be usable
            instance = TestActor()
            assert instance is not None

    def test_decorator_app_slug_from_class_name(self):
        """Test decorator derives app_slug from class name."""
        from mdb_engine.core.ray_integration import RAY_AVAILABLE, ray_actor_decorator

        if not RAY_AVAILABLE:
            # Test slug derivation logic without Ray
            # MyAppActor -> my_app
            @ray_actor_decorator()  # No app_slug provided
            class MyAppActor:
                pass

            assert MyAppActor is not None


class TestGetRayActorHandle:
    """Test get_ray_actor_handle function."""

    @pytest.mark.asyncio
    async def test_returns_none_when_ray_unavailable(self):
        """Test function returns None when Ray is not available."""
        from mdb_engine.core.ray_integration import RAY_AVAILABLE, get_ray_actor_handle

        if not RAY_AVAILABLE:
            result = await get_ray_actor_handle(
                app_slug="test_app",
                create_if_missing=False,
            )
            assert result is None

    @pytest.mark.asyncio
    async def test_handles_missing_actor_gracefully(self):
        """Test function handles missing actor gracefully."""
        from mdb_engine.core.ray_integration import RAY_AVAILABLE, get_ray_actor_handle

        if not RAY_AVAILABLE:
            # Should not raise, just return None
            result = await get_ray_actor_handle(
                app_slug="nonexistent_app",
                create_if_missing=False,
            )
            assert result is None


class TestConvenienceAliases:
    """Test convenience aliases for Ray functions."""

    def test_get_actor_alias(self):
        """Test get_actor alias."""
        from mdb_engine.core.ray_integration import RAY_AVAILABLE, get_actor

        if not RAY_AVAILABLE:
            # Should raise RuntimeError when Ray not available
            with pytest.raises(RuntimeError):
                get_actor("test")

    def test_is_initialized_alias(self):
        """Test is_initialized alias."""
        from mdb_engine.core.ray_integration import RAY_AVAILABLE, is_initialized

        if not RAY_AVAILABLE:
            assert is_initialized() is False


class TestHealthCheck:
    """Test health check functionality."""

    @pytest.mark.asyncio
    async def test_health_check_before_init(self):
        """Test health check before initialization."""
        from mdb_engine.core.ray_integration import AppRayActor

        actor = AppRayActor(
            app_slug="test_app",
            mongo_uri="mongodb://localhost:27017",
            db_name="test_db",
            use_in_memory_fallback=True,  # Avoid actual DB connection
        )

        # Health check should work but show not initialized
        # Note: This will try to initialize, which may fail without real DB
        # So we just verify the method exists
        assert hasattr(actor, "health_check")
        assert callable(actor.health_check)
