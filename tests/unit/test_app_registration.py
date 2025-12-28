"""
Unit tests for AppRegistrationManager.

Tests app registration, validation, callbacks, and reload functionality.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mdb_engine.core.app_registration import AppRegistrationManager
from mdb_engine.core.manifest import ManifestParser, ManifestValidator


@pytest.fixture
def app_registration_manager(mock_mongo_database):
    """Create an AppRegistrationManager instance."""
    validator = ManifestValidator()
    parser = ManifestParser()
    return AppRegistrationManager(
        mongo_db=mock_mongo_database,
        manifest_validator=validator,
        manifest_parser=parser,
    )


class TestAppRegistrationErrorHandling:
    """Test error handling in app registration."""

    @pytest.mark.asyncio
    async def test_validate_manifest_exception_handling(
        self, app_registration_manager, sample_manifest
    ):
        """Test handling exceptions during manifest validation."""
        # Mock validator to raise exception
        with patch.object(
            app_registration_manager.manifest_validator,
            "validate",
            side_effect=Exception("Validation error"),
        ):
            with pytest.raises(Exception, match="Validation error"):
                await app_registration_manager.validate_manifest(sample_manifest)

    @pytest.mark.asyncio
    async def test_register_app_callback_errors(
        self, app_registration_manager, sample_manifest, mock_mongo_database
    ):
        """Test handling callback execution errors."""
        # Mock successful validation - validate is a sync method that returns a tuple
        with patch.object(
            app_registration_manager.manifest_validator,
            "validate",
            return_value=(True, None, None),
        ):

            # Mock database operations
            mock_collection = MagicMock()
            mock_collection.replace_one = AsyncMock(
                return_value=MagicMock(modified_count=1, upserted_id="test_id")
            )
            mock_mongo_database.__getitem__ = lambda name: mock_collection
            app_registration_manager.mongo_db = mock_mongo_database
            # Also need to set apps_config attribute
            app_registration_manager._mongo_db = mock_mongo_database
            mock_mongo_database.apps_config = mock_collection

            # Mock callbacks that raise errors
            error_callback = AsyncMock(side_effect=Exception("Callback error"))

            result = await app_registration_manager.register_app(
                sample_manifest,
                create_indexes_callback=error_callback,
                seed_data_callback=error_callback,
                initialize_memory_callback=error_callback,
                register_websockets_callback=error_callback,
                setup_observability_callback=error_callback,
            )

            # Should still register app even if callbacks fail
            assert result is True
            assert sample_manifest["slug"] in app_registration_manager._apps

    @pytest.mark.asyncio
    async def test_register_app_persistence_errors(
        self, app_registration_manager, sample_manifest, mock_mongo_database
    ):
        """Test handling MongoDB persistence errors."""
        from pymongo.errors import (ConnectionFailure, InvalidOperation,
                                    OperationFailure)

        # Mock successful validation
        with patch.object(
            app_registration_manager.manifest_validator,
            "validate",
            return_value=(True, None, None),
        ):
            mock_collection = MagicMock()
            # Test ConnectionFailure
            mock_collection.replace_one = AsyncMock(
                side_effect=ConnectionFailure("Connection failed")
            )
            mock_mongo_database.__getitem__ = lambda name: mock_collection
            mock_mongo_database.apps_config = mock_collection
            app_registration_manager.mongo_db = mock_mongo_database
            app_registration_manager._mongo_db = mock_mongo_database

            result = await app_registration_manager.register_app(sample_manifest)
            # Should still register app in memory even if persistence fails
            assert result is True
            assert sample_manifest["slug"] in app_registration_manager._apps

            # Test OperationFailure
            mock_collection.replace_one = AsyncMock(
                side_effect=OperationFailure("Operation failed")
            )
            result = await app_registration_manager.register_app(sample_manifest)
            assert result is True

            # Test InvalidOperation
            mock_collection.replace_one = AsyncMock(
                side_effect=InvalidOperation("Client closed")
            )
            result = await app_registration_manager.register_app(sample_manifest)
            assert result is True

    @pytest.mark.asyncio
    async def test_register_app_auth_cache_invalidation_error(
        self, app_registration_manager, sample_manifest, mock_mongo_database
    ):
        """Test handling auth cache invalidation errors."""
        # Mock successful validation
        with patch.object(
            app_registration_manager.manifest_validator,
            "validate",
            return_value=(True, None, None),
        ):
            mock_collection = MagicMock()
            mock_collection.replace_one = AsyncMock(
                return_value=MagicMock(modified_count=1, upserted_id="test_id")
            )
            mock_mongo_database.__getitem__ = lambda name: mock_collection
            mock_mongo_database.apps_config = mock_collection
            app_registration_manager.mongo_db = mock_mongo_database
            app_registration_manager._mongo_db = mock_mongo_database

            # Mock auth integration to raise errors - the function is imported from auth.integration
            # Test AttributeError (function doesn't exist in module)
            with patch(
                "mdb_engine.auth.integration.invalidate_auth_config_cache",
                side_effect=AttributeError("No module"),
            ):
                result = await app_registration_manager.register_app(sample_manifest)
                assert result is True

            # Test ImportError (module import fails)
            import sys

            original_auth = sys.modules.get("mdb_engine.auth.integration")
            try:
                if "mdb_engine.auth.integration" in sys.modules:
                    del sys.modules["mdb_engine.auth.integration"]
                result = await app_registration_manager.register_app(sample_manifest)
                assert result is True
            finally:
                if original_auth:
                    sys.modules["mdb_engine.auth.integration"] = original_auth

            # Test RuntimeError
            with patch(
                "mdb_engine.auth.integration.invalidate_auth_config_cache",
                side_effect=RuntimeError("Runtime error"),
            ):
                result = await app_registration_manager.register_app(sample_manifest)
                assert result is True


class TestAppRegistrationEdgeCases:
    """Test edge cases in app registration."""

    @pytest.mark.asyncio
    async def test_register_app_with_callbacks(
        self, app_registration_manager, sample_manifest, mock_mongo_database
    ):
        """Test registering app with all callback types."""
        # Mock successful validation - validate is a sync method that returns a tuple
        with patch.object(
            app_registration_manager.manifest_validator,
            "validate",
            return_value=(True, None, None),
        ):

            # Mock database operations
            mock_collection = MagicMock()
            mock_collection.replace_one = AsyncMock(
                return_value=MagicMock(modified_count=1, upserted_id="test_id")
            )
            mock_mongo_database.__getitem__ = lambda name: mock_collection
            app_registration_manager.mongo_db = mock_mongo_database
            # Also need to set apps_config attribute
            app_registration_manager._mongo_db = mock_mongo_database
            mock_mongo_database.apps_config = mock_collection

            # Create callbacks
            create_indexes_callback = AsyncMock()
            seed_data_callback = AsyncMock()
            initialize_memory_callback = AsyncMock()
            register_websockets_callback = AsyncMock()
            setup_observability_callback = AsyncMock()

            manifest_with_all = {
                **sample_manifest,
                "managed_indexes": {
                    "collection": [
                        {"name": "idx", "type": "regular", "keys": [("field", 1)]}
                    ]
                },
                "initial_data": {"collection": [{"field": "value"}]},
                "memory_config": {"enabled": True},
                "websockets": {"endpoint": {"path": "/ws"}},
                "observability": {"health_checks": {"enabled": True}},
            }

            result = await app_registration_manager.register_app(
                manifest_with_all,
                create_indexes_callback=create_indexes_callback,
                seed_data_callback=seed_data_callback,
                initialize_memory_callback=initialize_memory_callback,
                register_websockets_callback=register_websockets_callback,
                setup_observability_callback=setup_observability_callback,
            )

            assert result is True
            # Verify callbacks were called
            create_indexes_callback.assert_called_once()
            seed_data_callback.assert_called_once()
            initialize_memory_callback.assert_called_once()
            register_websockets_callback.assert_called_once()
            setup_observability_callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_register_app_callback_failures(
        self, app_registration_manager, sample_manifest, mock_mongo_database
    ):
        """Test handling callback failures gracefully."""
        # Mock successful validation - validate is a sync method that returns a tuple
        with patch.object(
            app_registration_manager.manifest_validator,
            "validate",
            return_value=(True, None, None),
        ):

            # Mock database operations
            mock_collection = MagicMock()
            mock_collection.replace_one = AsyncMock(
                return_value=MagicMock(modified_count=1, upserted_id="test_id")
            )
            mock_mongo_database.__getitem__ = lambda name: mock_collection
            app_registration_manager.mongo_db = mock_mongo_database
            # Also need to set apps_config attribute
            app_registration_manager._mongo_db = mock_mongo_database
            mock_mongo_database.apps_config = mock_collection

            # Callbacks that fail
            failing_callback = AsyncMock(side_effect=Exception("Callback failed"))

            result = await app_registration_manager.register_app(
                sample_manifest,
                create_indexes_callback=failing_callback,
                seed_data_callback=failing_callback,
            )

            # Should still register app
            assert result is True
            assert sample_manifest["slug"] in app_registration_manager._apps

    @pytest.mark.asyncio
    async def test_reload_apps_empty(
        self, app_registration_manager, mock_mongo_database
    ):
        """Test reloading when no apps exist."""
        # Mock empty cursor - find() returns a cursor, then limit(), then to_list()
        mock_cursor = MagicMock()
        mock_cursor.limit = MagicMock(return_value=mock_cursor)
        mock_cursor.to_list = AsyncMock(return_value=[])
        mock_collection = MagicMock()
        # find() is NOT async - it returns a cursor immediately
        mock_collection.find = MagicMock(return_value=mock_cursor)
        mock_mongo_database.__getitem__ = lambda name: mock_collection
        mock_mongo_database.apps_config = mock_collection

        app_registration_manager.mongo_db = mock_mongo_database
        app_registration_manager._mongo_db = mock_mongo_database

        register_callback = AsyncMock()
        count = await app_registration_manager.reload_apps(
            register_app_callback=register_callback
        )

        assert count == 0
        register_callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_reload_apps_with_errors(
        self, app_registration_manager, mock_mongo_database, sample_manifest
    ):
        """Test handling errors during app reload."""
        # Mock cursor with apps - find() returns cursor, then limit(), then to_list()
        # The code does: await self._mongo_db.apps_config.find(...).limit(500).to_list(None)
        # So find() must return a cursor (not async), limit() returns cursor, to_list() is async
        mock_cursor = MagicMock()
        mock_cursor.limit = MagicMock(return_value=mock_cursor)
        mock_cursor.to_list = AsyncMock(return_value=[sample_manifest])
        mock_collection = MagicMock()
        # find() is NOT async - it returns a cursor immediately
        mock_collection.find = MagicMock(return_value=mock_cursor)
        mock_mongo_database.__getitem__ = lambda name: mock_collection
        mock_mongo_database.apps_config = mock_collection

        app_registration_manager.mongo_db = mock_mongo_database
        app_registration_manager._mongo_db = mock_mongo_database

        # Callback that fails with a MongoDB exception (which will be caught)
        from pymongo.errors import OperationFailure

        failing_callback = AsyncMock(
            side_effect=OperationFailure("Registration failed")
        )

        # Should handle MongoDB errors gracefully and return 0
        # Note: reload_apps catches MongoDB exceptions and returns 0
        count = await app_registration_manager.reload_apps(
            register_app_callback=failing_callback
        )

        # Should return 0 when MongoDB errors occur
        assert count == 0

    @pytest.mark.asyncio
    async def test_reload_apps_success(
        self, app_registration_manager, mock_mongo_database, sample_manifest
    ):
        """Test successful app reload."""
        # Mock cursor with apps - find() returns cursor, then limit(), then to_list()
        mock_cursor = MagicMock()
        mock_cursor.limit = MagicMock(return_value=mock_cursor)
        mock_cursor.to_list = AsyncMock(return_value=[sample_manifest])
        mock_collection = MagicMock()
        # find() is NOT async - it returns a cursor immediately
        mock_collection.find = MagicMock(return_value=mock_cursor)
        mock_mongo_database.__getitem__ = lambda name: mock_collection
        mock_mongo_database.apps_config = mock_collection

        app_registration_manager.mongo_db = mock_mongo_database
        app_registration_manager._mongo_db = mock_mongo_database

        # Mock successful validation
        app_registration_manager.manifest_validator.validate = AsyncMock(
            return_value=(True, None, None)
        )

        register_callback = AsyncMock(return_value=True)

        count = await app_registration_manager.reload_apps(
            register_app_callback=register_callback
        )

        assert count == 1
        # reload_apps calls callback with manifest and create_indexes=True
        register_callback.assert_called_once_with(sample_manifest, create_indexes=True)
