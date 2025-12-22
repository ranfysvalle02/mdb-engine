"""
Input validation utilities for MDB_ENGINE.

These are private helper functions that don't affect the public API.
They provide validation for internal use while maintaining backward compatibility.
"""
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def validate_collection_name(name: str) -> str:
    """
    Validate and sanitize collection name.
    
    This is a private helper function for internal validation.
    It doesn't change the public API contract.
    
    Args:
        name: Collection name to validate
        
    Returns:
        Validated collection name
        
    Raises:
        ValueError: If collection name is invalid
    """
    if not name or not isinstance(name, str):
        raise ValueError("Collection name must be a non-empty string")
    
    if not re.match(r'^[a-zA-Z0-9_]+$', name):
        raise ValueError(
            f"Invalid collection name: {name}. "
            f"Only alphanumeric characters and underscores are allowed."
        )
    
    if len(name) > 255:
        raise ValueError(f"Collection name too long: {name} (max 255 characters)")
    
    return name


def validate_app_slug(slug: str) -> str:
    """
    Validate app slug format.
    
    This is a private helper function for internal validation.
    It doesn't change the public API contract.
    
    Args:
        slug: App slug to validate
        
    Returns:
        Validated slug
        
    Raises:
        ValueError: If slug is invalid
    """
    if not slug or not isinstance(slug, str):
        raise ValueError("App slug must be a non-empty string")
    
    if not re.match(r'^[a-z0-9_-]+$', slug):
        raise ValueError(
            f"Invalid app slug: {slug}. "
            f"Slug must contain only lowercase letters, numbers, underscores, and hyphens."
        )
    
    if len(slug) > 100:
        raise ValueError(f"App slug too long: {slug} (max 100 characters)")
    
    return slug


def validate_mongo_uri(uri: str) -> str:
    """
    Basic validation for MongoDB URI format.
    
    This is a private helper function for internal validation.
    It doesn't change the public API contract.
    
    Args:
        uri: MongoDB connection URI
        
    Returns:
        Validated URI
        
    Raises:
        ValueError: If URI format is invalid
    """
    if not uri or not isinstance(uri, str):
        raise ValueError("MongoDB URI must be a non-empty string")
    
    if not uri.startswith(('mongodb://', 'mongodb+srv://')):
        raise ValueError(
            f"Invalid MongoDB URI format: {uri}. "
            f"URI must start with 'mongodb://' or 'mongodb+srv://'"
        )
    
    return uri

