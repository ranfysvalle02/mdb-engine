"""
Custom exceptions for MDB_ENGINE.

These exceptions provide more specific error types while maintaining
backward compatibility with RuntimeError.
"""

from typing import Any


class MongoDBEngineError(RuntimeError):
    """
    Base exception for MongoDB Engine errors.

    This exception maintains backward compatibility with RuntimeError
    while providing a more specific base class for MDB_ENGINE errors.

    Attributes:
        message: Error message
        context: Optional dictionary with additional context (app_slug,
                 collection_name, etc.)
    """

    def __init__(self, message: str, context: dict[str, Any] | None = None) -> None:
        """
        Initialize the exception.

        Args:
            message: Error message
            context: Optional dictionary with additional context information
        """
        super().__init__(message)
        self.message = message
        self.context = context or {}

    def __str__(self) -> str:
        """Return formatted error message with context if available."""
        if self.context:
            context_str = ", ".join(f"{k}={v}" for k, v in self.context.items())
            return f"{self.message} (context: {context_str})"
        return self.message


class InitializationError(MongoDBEngineError):
    """
    Raised when engine initialization fails.

    This exception is raised when MongoDB connection fails or
    other critical initialization steps fail.

    Attributes:
        message: Error message
        mongo_uri: MongoDB connection URI (if available)
        db_name: Database name (if available)
        context: Additional context information
    """

    def __init__(
        self,
        message: str,
        mongo_uri: str | None = None,
        db_name: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        """
        Initialize the initialization error.

        Args:
            message: Error message
            mongo_uri: MongoDB connection URI (if available)
            db_name: Database name (if available)
            context: Additional context information
        """
        context = context or {}
        if mongo_uri:
            context["mongo_uri"] = mongo_uri
        if db_name:
            context["db_name"] = db_name
        super().__init__(message, context=context)
        self.mongo_uri = mongo_uri
        self.db_name = db_name


class ManifestValidationError(MongoDBEngineError):
    """
    Raised when manifest validation fails.

    This exception provides more context about validation failures
    while maintaining compatibility with RuntimeError.

    Attributes:
        message: Error message
        error_paths: List of JSON paths with validation errors
        manifest_slug: App slug from manifest (if available)
        schema_version: Schema version used for validation (if available)
        context: Additional context information
    """

    def __init__(
        self,
        message: str,
        error_paths: list[str] | None = None,
        manifest_slug: str | None = None,
        schema_version: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        """
        Initialize the manifest validation error.

        Args:
            message: Error message
            error_paths: List of JSON paths with validation errors
            manifest_slug: App slug from manifest (if available)
            schema_version: Schema version used for validation (if available)
            context: Additional context information
        """
        context = context or {}
        if error_paths:
            context["error_paths"] = error_paths
        if manifest_slug:
            context["manifest_slug"] = manifest_slug
        if schema_version:
            context["schema_version"] = schema_version
        super().__init__(message, context=context)
        self.error_paths = error_paths
        self.manifest_slug = manifest_slug
        self.schema_version = schema_version


class ConfigurationError(MongoDBEngineError):
    """
    Raised when configuration is invalid or missing.

    This exception is raised when required configuration
    parameters are missing or invalid.

    Attributes:
        message: Error message
        config_key: Configuration key that caused the error (if available)
        config_value: Configuration value that caused the error (if available)
        context: Additional context information
    """

    def __init__(
        self,
        message: str,
        config_key: str | None = None,
        config_value: Any | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        """
        Initialize the configuration error.

        Args:
            message: Error message
            config_key: Configuration key that caused the error (if available)
            config_value: Configuration value that caused the error (if available)
            context: Additional context information
        """
        context = context or {}
        if config_key:
            context["config_key"] = config_key
        if config_value is not None:
            context["config_value"] = config_value
        super().__init__(message, context=context)
        self.config_key = config_key
        self.config_value = config_value


class QueryValidationError(MongoDBEngineError):
    """
    Raised when a query fails validation checks.

    This exception is raised when a query contains dangerous operators,
    exceeds complexity limits, or violates security policies.

    Attributes:
        message: Error message
        query_type: Type of query that failed (filter, pipeline, etc.)
        operator: Dangerous operator that was found (if applicable)
        path: JSON path where the issue was found (if applicable)
        context: Additional context information
    """

    def __init__(
        self,
        message: str,
        query_type: str | None = None,
        operator: str | None = None,
        path: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        """
        Initialize the query validation error.

        Args:
            message: Error message
            query_type: Type of query that failed (filter, pipeline, etc.)
            operator: Dangerous operator that was found (if applicable)
            path: JSON path where the issue was found (if applicable)
            context: Additional context information
        """
        context = context or {}
        if query_type:
            context["query_type"] = query_type
        if operator:
            context["operator"] = operator
        if path:
            context["path"] = path
        super().__init__(message, context=context)
        self.query_type = query_type
        self.operator = operator
        self.path = path


class ResourceLimitExceeded(MongoDBEngineError):
    """
    Raised when a resource limit is exceeded.

    This exception is raised when queries exceed timeouts, result sizes,
    or other resource limits.

    Attributes:
        message: Error message
        limit_type: Type of limit that was exceeded (timeout, size, etc.)
        limit_value: The limit value that was exceeded
        actual_value: The actual value that exceeded the limit
        context: Additional context information
    """

    def __init__(
        self,
        message: str,
        limit_type: str | None = None,
        limit_value: Any | None = None,
        actual_value: Any | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        """
        Initialize the resource limit exceeded error.

        Args:
            message: Error message
            limit_type: Type of limit that was exceeded (timeout, size, etc.)
            limit_value: The limit value that was exceeded
            actual_value: The actual value that exceeded the limit
            context: Additional context information
        """
        context = context or {}
        if limit_type:
            context["limit_type"] = limit_type
        if limit_value is not None:
            context["limit_value"] = limit_value
        if actual_value is not None:
            context["actual_value"] = actual_value
        super().__init__(message, context=context)
        self.limit_type = limit_type
        self.limit_value = limit_value
        self.actual_value = actual_value
