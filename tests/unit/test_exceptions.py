"""
Unit tests for custom exceptions.

Tests exception hierarchy and error messages.
"""

from mdb_engine.exceptions import (ConfigurationError, InitializationError,
                                   ManifestValidationError, MongoDBEngineError)


class TestExceptionHierarchy:
    """Test exception inheritance hierarchy."""

    def test_mongodb_engine_error_is_runtime_error(self):
        """Test that MongoDBEngineError is a RuntimeError."""
        error = MongoDBEngineError("test error")
        assert isinstance(error, RuntimeError)

    def test_initialization_error_inheritance(self):
        """Test that InitializationError inherits from MongoDBEngineError."""
        error = InitializationError("init failed")
        assert isinstance(error, MongoDBEngineError)
        assert isinstance(error, RuntimeError)

    def test_manifest_validation_error_inheritance(self):
        """Test that ManifestValidationError inherits from MongoDBEngineError."""
        error = ManifestValidationError("validation failed")
        assert isinstance(error, MongoDBEngineError)
        assert isinstance(error, RuntimeError)

    def test_configuration_error_inheritance(self):
        """Test that ConfigurationError inherits from MongoDBEngineError."""
        error = ConfigurationError("config invalid")
        assert isinstance(error, MongoDBEngineError)
        assert isinstance(error, RuntimeError)


class TestExceptionMessages:
    """Test exception message formatting."""

    def test_mongodb_engine_error_message(self):
        """Test MongoDBEngineError message."""
        message = "Something went wrong"
        error = MongoDBEngineError(message)
        assert str(error) == message
        assert error.message == message
        assert error.context == {}

    def test_mongodb_engine_error_with_context(self):
        """Test MongoDBEngineError message with context."""
        message = "Something went wrong"
        context = {"app_slug": "test", "collection": "users"}
        error = MongoDBEngineError(message, context=context)
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
            message, mongo_uri="mongodb://localhost:27017", db_name="test_db"
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
            schema_version="2.0",
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
        error = ConfigurationError(message, config_key="max_pool_size", config_value=-1)
        assert error.config_key == "max_pool_size"
        assert error.config_value == -1
        assert "config_key" in error.context


class TestExceptionChaining:
    """Test exception chaining."""

    def test_exception_chaining(self):
        """Test that exceptions can be chained."""
        original_error = ValueError("Original error")
        # Test exception chaining by raising and catching
        try:
            raise original_error
        except ValueError:
            try:
                raise InitializationError("Init failed")
            except InitializationError as chained_error:
                # Manually set __cause__ to test chaining behavior
                chained_error.__cause__ = original_error
                assert chained_error.__cause__ == original_error
                assert isinstance(chained_error, InitializationError)
