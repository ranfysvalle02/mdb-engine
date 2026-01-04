"""
Extended tests for Ray integration to cover actor lifecycle and decorator logic.
"""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mdb_engine.core.ray_integration import (
    AppRayActor,
    get_ray_actor_handle,
    ray_actor_decorator,
)

# We need to mock 'ray' in sys.modules before importing the module under test
# or else we can't easily control the optional import behavior for all tests.
# However, we can also use patch to control the module-level variables.


class TestAppRayActorExtended:
    """Extended tests for AppRayActor lifecycle."""

    @pytest.mark.asyncio
    async def test_ensure_initialized_success(self):
        """Test successful initialization of the engine."""
        actor = AppRayActor("test_app", "mongodb://localhost", "test_db")

        # Mock the MongoDBEngine in core.engine
        with patch("mdb_engine.core.engine.MongoDBEngine") as MockEngine:
            mock_engine_instance = MockEngine.return_value
            mock_engine_instance.initialize = AsyncMock()

            await actor._ensure_initialized()

            assert actor._initialized is True
            assert actor._engine is not None
            mock_engine_instance.initialize.assert_awaited_once()

            # Second call should be no-op
            await actor._ensure_initialized()
            assert mock_engine_instance.initialize.await_count == 1

    @pytest.mark.asyncio
    async def test_ensure_initialized_failure_no_fallback(self):
        """Test initialization failure without fallback."""
        actor = AppRayActor(
            "test_app", "mongodb://localhost", "test_db", use_in_memory_fallback=False
        )

        with patch("mdb_engine.core.engine.MongoDBEngine") as MockEngine:
            mock_engine_instance = MockEngine.return_value
            mock_engine_instance.initialize = AsyncMock(side_effect=Exception("DB Error"))

            with pytest.raises(Exception, match="DB Error"):
                await actor._ensure_initialized()

            assert actor._initialized is False
            assert actor._engine is not None  # Engine instance created but init failed

    @pytest.mark.asyncio
    async def test_ensure_initialized_failure_with_fallback(self):
        """Test initialization failure with fallback enabled."""
        actor = AppRayActor(
            "test_app", "mongodb://localhost", "test_db", use_in_memory_fallback=True
        )

        with patch("mdb_engine.core.engine.MongoDBEngine") as MockEngine:
            mock_engine_instance = MockEngine.return_value
            mock_engine_instance.initialize = AsyncMock(side_effect=ConnectionError("DB Error"))

            # Should not raise exception
            await actor._ensure_initialized()

            assert actor._initialized is True
            assert actor._engine is None  # No engine in fallback mode

    @pytest.mark.asyncio
    async def test_get_app_db_success(self):
        """Test getting app db successfully."""
        actor = AppRayActor("test_app", "mongodb://localhost", "test_db")
        actor._initialized = True
        actor._engine = MagicMock()
        actor._engine.get_scoped_db = MagicMock(return_value="scoped_db")

        # Test with explicit token
        db = await actor.get_app_db(app_token="secret")
        assert db == "scoped_db"
        actor._engine.get_scoped_db.assert_called_with("test_app", app_token="secret")

        # Test with env var token
        with patch.dict(sys.modules["os"].environ, {"TEST_APP_SECRET": "env_secret"}):
            db = await actor.get_app_db()
            actor._engine.get_scoped_db.assert_called_with("test_app", app_token="env_secret")

    @pytest.mark.asyncio
    async def test_get_app_db_not_available(self):
        """Test getting app db when engine is not available."""
        # Case 1: Fallback not implemented
        actor = AppRayActor(
            "test_app", "mongodb://localhost", "test_db", use_in_memory_fallback=True
        )
        actor._initialized = True
        actor._engine = None  # Simulating fallback mode

        with pytest.raises(RuntimeError, match="In-memory fallback not yet implemented"):
            await actor.get_app_db()

        # Case 2: Engine missing (no fallback)
        actor = AppRayActor(
            "test_app", "mongodb://localhost", "test_db", use_in_memory_fallback=False
        )
        # Mock _ensure_initialized to do nothing but set initialized
        with patch.object(actor, "_ensure_initialized") as mock_init:
            # We manually simulate the state after a failed init that wasn't raised?
            # Actually _ensure_initialized raises if not fallback.
            # So if we are here, and _engine is None, it means something is wrong state-wise
            actor._initialized = True
            actor._engine = None

            with pytest.raises(RuntimeError, match="Engine not available"):
                await actor.get_app_db()

    @pytest.mark.asyncio
    async def test_shutdown(self):
        """Test actor shutdown."""
        actor = AppRayActor("test_app", "mongodb://localhost", "test_db")
        mock_engine = MagicMock()
        mock_engine.shutdown = AsyncMock()
        actor._engine = mock_engine
        actor._initialized = True

        await actor.shutdown()

        mock_engine.shutdown.assert_awaited_once()
        assert actor._engine is None
        assert actor._initialized is False

    @pytest.mark.asyncio
    async def test_health_check(self):
        """Test health check output."""
        actor = AppRayActor("test_app", "mongodb://localhost", "test_db")

        # Before init
        # We mock _ensure_initialized to avoid actually initializing
        with patch.object(actor, "_ensure_initialized", new_callable=AsyncMock):
            status = await actor.health_check()
            assert status["status"] == "initializing"
            assert status["initialized"] is False

            # After init
            actor._initialized = True
            actor._engine = MagicMock()
            status = await actor.health_check()
            assert status["status"] == "healthy"
            assert status["engine_available"] is True


class TestRayIntegrationLogic:
    """Test Ray integration helper functions with mocked Ray."""

    def test_ray_actor_decorator_logic(self):
        """Test the logic inside the decorator wrapper."""
        # We need to mock RAY_AVAILABLE to True to test the inner logic
        with patch("mdb_engine.core.ray_integration.RAY_AVAILABLE", True):
            with patch("mdb_engine.core.ray_integration.ray") as mock_ray:
                # Setup mock ray.remote
                mock_remote_cls = MagicMock()
                mock_ray.remote.return_value = mock_remote_cls

                # Apply decorator
                @ray_actor_decorator(app_slug="my_app", isolated=True)
                class MyActor:
                    pass

                # Check that ray.remote was called
                mock_ray.remote.assert_called()

                # Check metadata was set
                assert mock_remote_cls._app_slug == "my_app"
                assert mock_remote_cls._namespace == "modular_labs_my_app"
                assert mock_remote_cls._isolated is True
                assert hasattr(mock_remote_cls, "spawn")

                # Test auto-slug generation
                @ray_actor_decorator(isolated=False)
                class AutoSlugActor:
                    pass

                assert mock_remote_cls._app_slug == "auto_slug"
                assert mock_remote_cls._namespace == "modular_labs"

    @pytest.mark.asyncio
    async def test_get_ray_actor_handle_creation(self):
        """Test creating a new actor handle."""
        with patch("mdb_engine.core.ray_integration.RAY_AVAILABLE", True):
            with patch("mdb_engine.core.ray_integration.ray") as mock_ray:
                mock_ray.is_initialized.return_value = True
                mock_ray.get_actor.side_effect = ValueError("Actor not found")

                # Mock actor class and remote call
                mock_actor_cls = MagicMock()
                del mock_actor_cls.remote  # Ensure it doesn't have 'remote' so ray.remote is called
                mock_remote_cls = MagicMock()
                mock_actor_handle = MagicMock()

                # Chain: cls.options().remote() -> handle
                mock_remote_cls.options.return_value.remote.return_value = mock_actor_handle

                # If we pass a class that doesn't have .remote, it gets decorated
                mock_ray.remote.return_value = mock_remote_cls

                handle = await get_ray_actor_handle(
                    "new_app", create_if_missing=True, actor_class=mock_actor_cls
                )

                assert handle == mock_actor_handle
                mock_remote_cls.options.assert_called()
                args = mock_remote_cls.options.call_args[1]
                assert args["name"] == "new_app-actor"
                assert args["namespace"] == "modular_labs"

    @pytest.mark.asyncio
    async def test_get_ray_actor_handle_existing(self):
        """Test getting an existing actor handle."""
        with patch("mdb_engine.core.ray_integration.RAY_AVAILABLE", True):
            with patch("mdb_engine.core.ray_integration.ray") as mock_ray:
                mock_ray.is_initialized.return_value = True
                existing_handle = MagicMock()
                mock_ray.get_actor.return_value = existing_handle

                handle = await get_ray_actor_handle("existing_app")

                assert handle == existing_handle
                mock_ray.get_actor.assert_called_with(
                    "existing_app-actor", namespace="modular_labs"
                )

    @pytest.mark.asyncio
    async def test_get_ray_actor_handle_init_ray(self):
        """Test Ray initialization if not started."""
        with patch("mdb_engine.core.ray_integration.RAY_AVAILABLE", True):
            with patch("mdb_engine.core.ray_integration.ray") as mock_ray:
                mock_ray.is_initialized.return_value = False
                mock_ray.get_actor.side_effect = ValueError

                # Mock env var for address
                with patch.dict(sys.modules["os"].environ, {"RAY_ADDRESS": "auto"}):
                    await get_ray_actor_handle("app", create_if_missing=False)
                    mock_ray.init.assert_called_with(address="auto", namespace="modular_labs")

                # Reset and test local init
                mock_ray.init.reset_mock()
                with patch.dict(sys.modules["os"].environ, {}, clear=True):
                    await get_ray_actor_handle("app", create_if_missing=False)
                    mock_ray.init.assert_called_with(
                        namespace="modular_labs", ignore_reinit_error=True
                    )
