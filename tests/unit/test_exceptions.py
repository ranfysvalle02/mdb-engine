"""
Unit tests for custom exceptions.

Tests exception hierarchy and error messages.
"""
import pytest
from mdb_engine.exceptions import (
    RuntimeEngineError,
    InitializationError,
    ManifestValidationError,
    ConfigurationError,
)


class TestExceptionHierarchy:
    """Test exception inheritance hierarchy."""
    
    def test_runtime_engine_error_is_runtime_error(self):
        """Test that RuntimeEngineError is a RuntimeError."""
        error = RuntimeEngineError("test error")
        assert isinstance(error, RuntimeError)
    
    def test_initialization_error_inheritance(self):
        """Test that InitializationError inherits from RuntimeEngineError."""
        error = InitializationError("init failed")
        assert isinstance(error, RuntimeEngineError)
        assert isinstance(error, RuntimeError)
    
    def test_manifest_validation_error_inheritance(self):
        """Test that ManifestValidationError inherits from RuntimeEngineError."""
        error = ManifestValidationError("validation failed")
        assert isinstance(error, RuntimeEngineError)
        assert isinstance(error, RuntimeError)
    
    def test_configuration_error_inheritance(self):
        """Test that ConfigurationError inherits from RuntimeEngineError."""
        error = ConfigurationError("config invalid")
        assert isinstance(error, RuntimeEngineError)
        assert isinstance(error, RuntimeError)


class TestExceptionMessages:
    """Test exception message formatting."""
    
    def test_runtime_engine_error_message(self):
        """Test RuntimeEngineError message."""
        message = "Something went wrong"
        error = RuntimeEngineError(message)
        assert str(error) == message
        assert error.message == message
        assert error.context == {}
    
    def test_runtime_engine_error_with_context(self):
        """Test RuntimeEngineError message with context."""
        message = "Something went wrong"
        context = {"app_slug": "test", "collection": "users"}
        error = RuntimeEngineError(message, context=context)
        assert "context:" in str(error)
        assert "app_slug=test" in str(error)
        assert error.context == context
    
    def test_initialization_error_message(self):
        """Test InitializationError message."""
        message = "Failed to initialize"
        error = InitializationError(message)
        assert str(error) == message
        assert error.message == message
    
    def test_initialization_error_with_context(self):
        """Test InitializationError with MongoDB context."""
        message = "Connection failed"
        error = InitializationError(
            message,
            mongo_uri="mongodb://localhost:27017",
            db_name="test_db"
        )
        assert error.mongo_uri == "mongodb://localhost:27017"
        assert error.db_name == "test_db"
        assert "mongo_uri" in error.context
        assert "db_name" in error.context
    
    def test_manifest_validation_error_message(self):
        """Test ManifestValidationError message."""
        message = "Invalid manifest"
        error = ManifestValidationError(message)
        assert str(error) == message
        assert error.message == message
    
    def test_manifest_validation_error_with_paths(self):
        """Test ManifestValidationError with error paths."""
        message = "Validation failed"
        error_paths = ["slug", "name"]
        error = ManifestValidationError(
            message,
            error_paths=error_paths,
            manifest_slug="test_exp",
            schema_version="2.0"
        )
        assert error.error_paths == error_paths
        assert error.manifest_slug == "test_exp"
        assert error.schema_version == "2.0"
        assert "error_paths" in error.context
    
    def test_configuration_error_message(self):
        """Test ConfigurationError message."""
        message = "Invalid configuration"
        error = ConfigurationError(message)
        assert str(error) == message
        assert error.message == message
    
    def test_configuration_error_with_key(self):
        """Test ConfigurationError with config key."""
        message = "Invalid value"
        error = ConfigurationError(
            message,
            config_key="max_pool_size",
            config_value=-1
        )
        assert error.config_key == "max_pool_size"
        assert error.config_value == -1
        assert "config_key" in error.context


class TestExceptionChaining:
    """Test exception chaining."""
    
    def test_exception_chaining(self):
        """Test that exceptions can be chained."""
        original_error = ValueError("Original error")
        chained_error = InitializationError("Init failed") from original_error
        
        assert chained_error.__cause__ == original_error
        assert isinstance(chained_error, InitializationError)

