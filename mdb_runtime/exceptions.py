"""
Custom exceptions for MDB_RUNTIME.

These exceptions provide more specific error types while maintaining
backward compatibility with RuntimeError.
"""
from typing import Optional, Dict, Any, List


class RuntimeEngineError(RuntimeError):
    """
    Base exception for runtime engine errors.
    
    This exception maintains backward compatibility with RuntimeError
    while providing a more specific base class for MDB_RUNTIME errors.
    
    Attributes:
        message: Error message
        context: Optional dictionary with additional context (app_slug, 
                 collection_name, etc.)
    """
    
    def __init__(
        self,
        message: str,
        context: Optional[Dict[str, Any]] = None
    ) -> None:
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


class InitializationError(RuntimeEngineError):
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
        mongo_uri: Optional[str] = None,
        db_name: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None
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


class ManifestValidationError(RuntimeEngineError):
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
        error_paths: Optional[List[str]] = None,
        manifest_slug: Optional[str] = None,
        schema_version: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None
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


class ConfigurationError(RuntimeEngineError):
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
        config_key: Optional[str] = None,
        config_value: Optional[Any] = None,
        context: Optional[Dict[str, Any]] = None
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

