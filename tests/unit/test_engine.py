"""
Unit tests for MongoDBEngine.

Tests the core orchestration engine functionality including:
- Initialization and shutdown
- App registration
- Manifest validation
- Database scoping
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from mdb_engine.core.engine import MongoDBEngine
from mdb_engine.exceptions import InitializationError, MongoDBEngineError


class TestMongoDBEngineInitialization:
    """Test MongoDBEngine initialization and lifecycle."""
    
    @pytest.mark.asyncio
    async def test_engine_initialization_success(self, mock_mongo_client, mongodb_engine_config):
        """Test successful engine initialization."""
        with patch('mdb_engine.core.engine.AsyncIOMotorClient', return_value=mock_mongo_client):
            engine = MongoDBEngine(**mongodb_engine_config)
            await engine.initialize()
            
            assert engine._initialized is True
            assert engine.mongo_client is not None
            assert engine.mongo_db is not None
            assert engine.mongo_db.name == mongodb_engine_config["db_name"]
            
            await engine.shutdown()
    
    @pytest.mark.asyncio
    async def test_engine_initialization_failure_connection(self, mongodb_engine_config):
        """Test engine initialization failure due to connection error."""
        mock_client = MagicMock()
        mock_client.admin.command = AsyncMock(side_effect=Exception("Connection failed"))
        
        with patch('mdb_engine.core.engine.AsyncIOMotorClient', return_value=mock_client):
            engine = MongoDBEngine(**mongodb_engine_config)
            
            with pytest.raises(InitializationError) as exc_info:
                await engine.initialize()
            
            assert "Failed to connect to MongoDB" in str(exc_info.value)
            assert engine._initialized is False
    
    @pytest.mark.asyncio
    async def test_engine_double_initialization(self, mongodb_engine):
        """Test that double initialization is handled gracefully."""
        # First initialization happens in fixture
        assert mongodb_engine._initialized is True
        
        # Second initialization should be a no-op
        await mongodb_engine.initialize()
        assert mongodb_engine._initialized is True
    
    @pytest.mark.asyncio
    async def test_engine_shutdown(self, mongodb_engine):
        """Test engine shutdown."""
        assert mongodb_engine._initialized is True
        
        await mongodb_engine.shutdown()
        
        assert mongodb_engine._initialized is False
        assert len(mongodb_engine._apps) == 0
    
    @pytest.mark.asyncio
    async def test_engine_shutdown_idempotent(self, mongodb_engine):
        """Test that shutdown is idempotent."""
        await mongodb_engine.shutdown()
        await mongodb_engine.shutdown()  # Should not raise
    
    @pytest.mark.asyncio
    async def test_engine_context_manager(self, mock_mongo_client, mongodb_engine_config):
        """Test engine as async context manager."""
        with patch('mdb_engine.core.engine.AsyncIOMotorClient', return_value=mock_mongo_client):
            async with MongoDBEngine(**mongodb_engine_config) as engine:
                assert engine._initialized is True
            
            # After context exit, should be shut down
            assert engine._initialized is False


class TestMongoDBEngineProperties:
    """Test MongoDBEngine property accessors."""
    
    @pytest.mark.asyncio
    async def test_mongo_client_property_uninitialized(self, uninitialized_mongodb_engine):
        """Test accessing mongo_client before initialization raises error."""
        with pytest.raises(RuntimeError, match="not initialized"):
            _ = uninitialized_mongodb_engine.mongo_client
    
    @pytest.mark.asyncio
    async def test_mongo_db_property_uninitialized(self, uninitialized_mongodb_engine):
        """Test accessing mongo_db before initialization raises error."""
        with pytest.raises(RuntimeError, match="not initialized"):
            _ = uninitialized_mongodb_engine.mongo_db
    
    @pytest.mark.asyncio
    async def test_mongo_client_property_initialized(self, mongodb_engine):
        """Test accessing mongo_client after initialization."""
        client = mongodb_engine.mongo_client
        assert client is not None
    
    @pytest.mark.asyncio
    async def test_mongo_db_property_initialized(self, mongodb_engine):
        """Test accessing mongo_db after initialization."""
        db = mongodb_engine.mongo_db
        assert db is not None
        assert db.name == "test_db"


class TestMongoDBEngineScopedDatabase:
    """Test scoped database wrapper creation."""
    
    @pytest.mark.asyncio
    async def test_get_scoped_db_success(self, mongodb_engine):
        """Test successful scoped database creation."""
        scoped_db = mongodb_engine.get_scoped_db("test_app")
        
        assert scoped_db is not None
        assert scoped_db._read_scopes == ["test_app"]
        assert scoped_db._write_scope == "test_app"
    
    @pytest.mark.asyncio
    async def test_get_scoped_db_custom_scopes(self, mongodb_engine):
        """Test scoped database with custom read/write scopes."""
        scoped_db = mongodb_engine.get_scoped_db(
            app_slug="test_app",
            read_scopes=["app1", "app2"],
            write_scope="app1"
        )
        
        assert scoped_db._read_scopes == ["app1", "app2"]
        assert scoped_db._write_scope == "app1"
    
    @pytest.mark.asyncio
    async def test_get_scoped_db_uninitialized(self, uninitialized_mongodb_engine):
        """Test getting scoped db before initialization raises error."""
        with pytest.raises(RuntimeError, match="not initialized"):
            uninitialized_mongodb_engine.get_scoped_db("test_app")
    
    @pytest.mark.asyncio
    async def test_get_scoped_db_auto_index_disabled(self, mongodb_engine):
        """Test scoped database with auto_index disabled."""
        scoped_db = mongodb_engine.get_scoped_db("test_app", auto_index=False)
        
        assert scoped_db._auto_index is False


class TestMongoDBEngineManifestValidation:
    """Test manifest validation functionality."""
    
    @pytest.mark.asyncio
    async def test_validate_manifest_valid(self, mongodb_engine, sample_manifest):
        """Test validation of a valid manifest."""
        is_valid, error, paths = await mongodb_engine.validate_manifest(sample_manifest)
        
        assert is_valid is True
        assert error is None
        assert paths is None
    
    @pytest.mark.asyncio
    async def test_validate_manifest_invalid(self, mongodb_engine, invalid_manifest):
        """Test validation of an invalid manifest."""
        is_valid, error, paths = await mongodb_engine.validate_manifest(invalid_manifest)
        
        assert is_valid is False
        assert error is not None
        assert paths is not None
        assert len(paths) > 0
    
    @pytest.mark.asyncio
    async def test_load_manifest_from_file(self, mongodb_engine, tmp_path, sample_manifest):
        """Test loading manifest from file."""
        import json
        
        manifest_file = tmp_path / "manifest.json"
        manifest_file.write_text(json.dumps(sample_manifest))
        
        loaded = await mongodb_engine.load_manifest(manifest_file)
        
        assert loaded["slug"] == sample_manifest["slug"]
        assert loaded["name"] == sample_manifest["name"]
    
    @pytest.mark.asyncio
    async def test_load_manifest_file_not_found(self, mongodb_engine, tmp_path):
        """Test loading non-existent manifest file."""
        manifest_file = tmp_path / "nonexistent.json"
        
        with pytest.raises(FileNotFoundError):
            await mongodb_engine.load_manifest(manifest_file)


class TestMongoDBEngineTenantRegistration:
    """Test app registration functionality."""
    
    @pytest.mark.asyncio
    async def test_register_app_success(self, mongodb_engine, sample_manifest):
        """Test successful app registration."""
        result = await mongodb_engine.register_app(sample_manifest, create_indexes=False)
        
        assert result is True
        assert sample_manifest["slug"] in mongodb_engine._apps
        assert mongodb_engine.get_app(sample_manifest["slug"]) == sample_manifest
    
    @pytest.mark.asyncio
    async def test_register_app_missing_slug(self, mongodb_engine, sample_manifest):
        """Test registration with missing slug."""
        manifest_no_slug = {k: v for k, v in sample_manifest.items() if k != "slug"}
        
        result = await mongodb_engine.register_app(manifest_no_slug)
        
        assert result is False
        assert len(mongodb_engine._apps) == 0
    
    @pytest.mark.asyncio
    async def test_register_app_invalid_manifest(self, mongodb_engine, invalid_manifest):
        """Test registration with invalid manifest."""
        result = await mongodb_engine.register_app(invalid_manifest)
        
        assert result is False
        assert len(mongodb_engine._apps) == 0
    
    @pytest.mark.asyncio
    async def test_register_app_uninitialized(self, uninitialized_mongodb_engine, sample_manifest):
        """Test registration before initialization raises error."""
        with pytest.raises(RuntimeError, match="not initialized"):
            await uninitialized_mongodb_engine.register_app(sample_manifest)
    
    @pytest.mark.asyncio
    async def test_get_app(self, mongodb_engine, sample_manifest):
        """Test getting registered app."""
        await mongodb_engine.register_app(sample_manifest, create_indexes=False)
        
        app = mongodb_engine.get_app(sample_manifest["slug"])
        assert app is not None
        assert app["slug"] == sample_manifest["slug"]
    
    @pytest.mark.asyncio
    async def test_get_app_not_found(self, mongodb_engine):
        """Test getting non-existent app."""
        app = mongodb_engine.get_app("nonexistent")
        assert app is None
    
    @pytest.mark.asyncio
    async def test_list_apps(self, mongodb_engine, sample_manifest):
        """Test listing all apps."""
        assert len(mongodb_engine.list_apps()) == 0
        
        await mongodb_engine.register_app(sample_manifest, create_indexes=False)
        
        apps = mongodb_engine.list_apps()
        assert len(apps) == 1
        assert sample_manifest["slug"] in apps

