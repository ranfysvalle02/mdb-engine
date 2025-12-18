"""
Unit tests for RuntimeEngine.

Tests the core orchestration engine functionality including:
- Initialization and shutdown
- App registration
- Manifest validation
- Database scoping
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from mdb_runtime.core.engine import RuntimeEngine
from mdb_runtime.exceptions import InitializationError, RuntimeEngineError


class TestRuntimeEngineInitialization:
    """Test RuntimeEngine initialization and lifecycle."""
    
    @pytest.mark.asyncio
    async def test_engine_initialization_success(self, mock_mongo_client, runtime_engine_config):
        """Test successful engine initialization."""
        with patch('mdb_runtime.core.engine.AsyncIOMotorClient', return_value=mock_mongo_client):
            engine = RuntimeEngine(**runtime_engine_config)
            await engine.initialize()
            
            assert engine._initialized is True
            assert engine.mongo_client is not None
            assert engine.mongo_db is not None
            assert engine.mongo_db.name == runtime_engine_config["db_name"]
            
            await engine.shutdown()
    
    @pytest.mark.asyncio
    async def test_engine_initialization_failure_connection(self, runtime_engine_config):
        """Test engine initialization failure due to connection error."""
        mock_client = MagicMock()
        mock_client.admin.command = AsyncMock(side_effect=Exception("Connection failed"))
        
        with patch('mdb_runtime.core.engine.AsyncIOMotorClient', return_value=mock_client):
            engine = RuntimeEngine(**runtime_engine_config)
            
            with pytest.raises(InitializationError) as exc_info:
                await engine.initialize()
            
            assert "Failed to connect to MongoDB" in str(exc_info.value)
            assert engine._initialized is False
    
    @pytest.mark.asyncio
    async def test_engine_double_initialization(self, runtime_engine):
        """Test that double initialization is handled gracefully."""
        # First initialization happens in fixture
        assert runtime_engine._initialized is True
        
        # Second initialization should be a no-op
        await runtime_engine.initialize()
        assert runtime_engine._initialized is True
    
    @pytest.mark.asyncio
    async def test_engine_shutdown(self, runtime_engine):
        """Test engine shutdown."""
        assert runtime_engine._initialized is True
        
        await runtime_engine.shutdown()
        
        assert runtime_engine._initialized is False
        assert len(runtime_engine._apps) == 0
    
    @pytest.mark.asyncio
    async def test_engine_shutdown_idempotent(self, runtime_engine):
        """Test that shutdown is idempotent."""
        await runtime_engine.shutdown()
        await runtime_engine.shutdown()  # Should not raise
    
    @pytest.mark.asyncio
    async def test_engine_context_manager(self, mock_mongo_client, runtime_engine_config):
        """Test engine as async context manager."""
        with patch('mdb_runtime.core.engine.AsyncIOMotorClient', return_value=mock_mongo_client):
            async with RuntimeEngine(**runtime_engine_config) as engine:
                assert engine._initialized is True
            
            # After context exit, should be shut down
            assert engine._initialized is False


class TestRuntimeEngineProperties:
    """Test RuntimeEngine property accessors."""
    
    @pytest.mark.asyncio
    async def test_mongo_client_property_uninitialized(self, uninitialized_runtime_engine):
        """Test accessing mongo_client before initialization raises error."""
        with pytest.raises(RuntimeError, match="not initialized"):
            _ = uninitialized_runtime_engine.mongo_client
    
    @pytest.mark.asyncio
    async def test_mongo_db_property_uninitialized(self, uninitialized_runtime_engine):
        """Test accessing mongo_db before initialization raises error."""
        with pytest.raises(RuntimeError, match="not initialized"):
            _ = uninitialized_runtime_engine.mongo_db
    
    @pytest.mark.asyncio
    async def test_mongo_client_property_initialized(self, runtime_engine):
        """Test accessing mongo_client after initialization."""
        client = runtime_engine.mongo_client
        assert client is not None
    
    @pytest.mark.asyncio
    async def test_mongo_db_property_initialized(self, runtime_engine):
        """Test accessing mongo_db after initialization."""
        db = runtime_engine.mongo_db
        assert db is not None
        assert db.name == "test_db"


class TestRuntimeEngineScopedDatabase:
    """Test scoped database wrapper creation."""
    
    @pytest.mark.asyncio
    async def test_get_scoped_db_success(self, runtime_engine):
        """Test successful scoped database creation."""
        scoped_db = runtime_engine.get_scoped_db("test_app")
        
        assert scoped_db is not None
        assert scoped_db._read_scopes == ["test_app"]
        assert scoped_db._write_scope == "test_app"
    
    @pytest.mark.asyncio
    async def test_get_scoped_db_custom_scopes(self, runtime_engine):
        """Test scoped database with custom read/write scopes."""
        scoped_db = runtime_engine.get_scoped_db(
            app_slug="test_app",
            read_scopes=["app1", "app2"],
            write_scope="app1"
        )
        
        assert scoped_db._read_scopes == ["app1", "app2"]
        assert scoped_db._write_scope == "app1"
    
    @pytest.mark.asyncio
    async def test_get_scoped_db_uninitialized(self, uninitialized_runtime_engine):
        """Test getting scoped db before initialization raises error."""
        with pytest.raises(RuntimeError, match="not initialized"):
            uninitialized_runtime_engine.get_scoped_db("test_app")
    
    @pytest.mark.asyncio
    async def test_get_scoped_db_auto_index_disabled(self, runtime_engine):
        """Test scoped database with auto_index disabled."""
        scoped_db = runtime_engine.get_scoped_db("test_app", auto_index=False)
        
        assert scoped_db._auto_index is False


class TestRuntimeEngineManifestValidation:
    """Test manifest validation functionality."""
    
    @pytest.mark.asyncio
    async def test_validate_manifest_valid(self, runtime_engine, sample_manifest):
        """Test validation of a valid manifest."""
        is_valid, error, paths = await runtime_engine.validate_manifest(sample_manifest)
        
        assert is_valid is True
        assert error is None
        assert paths is None
    
    @pytest.mark.asyncio
    async def test_validate_manifest_invalid(self, runtime_engine, invalid_manifest):
        """Test validation of an invalid manifest."""
        is_valid, error, paths = await runtime_engine.validate_manifest(invalid_manifest)
        
        assert is_valid is False
        assert error is not None
        assert paths is not None
        assert len(paths) > 0
    
    @pytest.mark.asyncio
    async def test_load_manifest_from_file(self, runtime_engine, tmp_path, sample_manifest):
        """Test loading manifest from file."""
        import json
        
        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text(json.dumps(sample_manifest))
        
        loaded = await runtime_engine.load_manifest(manifest_file)
        
        assert loaded["slug"] == sample_manifest["slug"]
        assert loaded["name"] == sample_manifest["name"]
    
    @pytest.mark.asyncio
    async def test_load_manifest_file_not_found(self, runtime_engine, tmp_path):
        """Test loading non-existent manifest file."""
        manifest_file = tmp_path / "nonexistent.json"
        
        with pytest.raises(FileNotFoundError):
            await runtime_engine.load_manifest(manifest_file)


class TestRuntimeEngineTenantRegistration:
    """Test app registration functionality."""
    
    @pytest.mark.asyncio
    async def test_register_app_success(self, runtime_engine, sample_manifest):
        """Test successful app registration."""
        result = await runtime_engine.register_app(sample_manifest, create_indexes=False)
        
        assert result is True
        assert sample_manifest["slug"] in runtime_engine._apps
        assert runtime_engine.get_app(sample_manifest["slug"]) == sample_manifest
    
    @pytest.mark.asyncio
    async def test_register_app_missing_slug(self, runtime_engine, sample_manifest):
        """Test registration with missing slug."""
        manifest_no_slug = {k: v for k, v in sample_manifest.items() if k != "slug"}
        
        result = await runtime_engine.register_app(manifest_no_slug)
        
        assert result is False
        assert len(runtime_engine._apps) == 0
    
    @pytest.mark.asyncio
    async def test_register_app_invalid_manifest(self, runtime_engine, invalid_manifest):
        """Test registration with invalid manifest."""
        result = await runtime_engine.register_app(invalid_manifest)
        
        assert result is False
        assert len(runtime_engine._apps) == 0
    
    @pytest.mark.asyncio
    async def test_register_app_uninitialized(self, uninitialized_runtime_engine, sample_manifest):
        """Test registration before initialization raises error."""
        with pytest.raises(RuntimeError, match="not initialized"):
            await uninitialized_runtime_engine.register_app(sample_manifest)
    
    @pytest.mark.asyncio
    async def test_get_app(self, runtime_engine, sample_manifest):
        """Test getting registered app."""
        await runtime_engine.register_app(sample_manifest, create_indexes=False)
        
        app = runtime_engine.get_app(sample_manifest["slug"])
        assert app is not None
        assert app["slug"] == sample_manifest["slug"]
    
    @pytest.mark.asyncio
    async def test_get_app_not_found(self, runtime_engine):
        """Test getting non-existent app."""
        app = runtime_engine.get_app("nonexistent")
        assert app is None
    
    @pytest.mark.asyncio
    async def test_list_apps(self, runtime_engine, sample_manifest):
        """Test listing all apps."""
        assert len(runtime_engine.list_apps()) == 0
        
        await runtime_engine.register_app(sample_manifest, create_indexes=False)
        
        apps = runtime_engine.list_apps()
        assert len(apps) == 1
        assert sample_manifest["slug"] in apps

