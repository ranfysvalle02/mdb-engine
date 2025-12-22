"""
Authentication Integration Helpers

Helpers for integrating authentication features from manifest configuration.

This module is part of MDB_RUNTIME - MongoDB Runtime Engine.
"""

import logging
from typing import Optional, Dict, Any
from fastapi import FastAPI

from .helpers import initialize_token_management
from .middleware import create_security_middleware
from .config_defaults import (
    SECURITY_CONFIG_DEFAULTS,
    TOKEN_MANAGEMENT_DEFAULTS,
    CORS_DEFAULTS,
    OBSERVABILITY_DEFAULTS
)
from .config_helpers import merge_config_with_defaults

logger = logging.getLogger(__name__)

# Cache for auth configs
_auth_config_cache: Dict[str, Dict[str, Any]] = {}


def invalidate_auth_config_cache(slug_id: Optional[str] = None) -> None:
    """
    Invalidate auth config cache for a specific app or all apps.
    
    Args:
        slug_id: App slug identifier. If None, invalidates entire cache.
    """
    if slug_id:
        _auth_config_cache.pop(slug_id, None)
        logger.debug(f"Invalidated auth config cache for {slug_id}")
    else:
        _auth_config_cache.clear()
        logger.debug("Invalidated entire auth config cache")


async def get_auth_config(slug_id: str, engine) -> Dict[str, Any]:
    """
    Retrieve authentication configuration from manifest.
    
    Caches results for performance.
    
    Args:
        slug_id: App slug identifier
        engine: RuntimeEngine instance
    
    Returns:
        Dictionary with token_management, auth_policy, and sub_auth configs
    """
    # Check cache first
    if slug_id in _auth_config_cache:
        return _auth_config_cache[slug_id]
    
    try:
        # Get manifest
        manifest = await engine.get_manifest(slug_id)
        if not manifest:
            logger.warning(f"Manifest not found for {slug_id}")
            return {}
        
        # Extract auth configs
        config = {
            "token_management": manifest.get("token_management", {}),
            "auth_policy": manifest.get("auth_policy", {}),
            "sub_auth": manifest.get("sub_auth", {}),
        }
        
        # Cache it
        _auth_config_cache[slug_id] = config
        
        return config
    except Exception as e:
        logger.error(f"Error getting auth config for {slug_id}: {e}", exc_info=True)
        return {}


async def setup_auth_from_manifest(app: FastAPI, engine, slug_id: str) -> bool:
    """
    Set up authentication features from manifest configuration.
    
    This function:
    1. Reads token_management config from manifest
    2. Initializes TokenBlacklist and SessionManager if enabled
    3. Sets up security middleware if configured
    4. Registers rate limiters
    5. Stores config in app.state for easy access
    
    Args:
        app: FastAPI application instance
        engine: RuntimeEngine instance
        slug_id: App slug identifier
    
    Returns:
        True if setup was successful, False otherwise
    """
    try:
        # Get auth config
        config = await get_auth_config(slug_id, engine)
        token_management = config.get("token_management", {})
        
        # Check if token management is enabled
        if not token_management.get("enabled", True):
            logger.info(f"Token management disabled for {slug_id}")
            return False
        
        # Store config in app state for easy access
        merged_token_config = merge_config_with_defaults(token_management, TOKEN_MANAGEMENT_DEFAULTS)
        app.state.token_management_config = merged_token_config
        app.state.auth_config = config
        
        # Extract and store security policy config with defaults merged
        security_config = token_management.get("security", {})
        app.state.security_config = merge_config_with_defaults(security_config, SECURITY_CONFIG_DEFAULTS)
        
        # Initialize token management (blacklist and session manager)
        if token_management.get("auto_setup", True):
            try:
                db = engine.get_database()
                await initialize_token_management(app, db)
                
                # Configure session fingerprinting if session manager exists
                session_mgr = getattr(app.state, "session_manager", None)
                if session_mgr:
                    fingerprinting_config = app.state.security_config.get("session_fingerprinting", {})
                    session_mgr.configure_fingerprinting(
                        enabled=fingerprinting_config.get("enabled", True),
                        strict=fingerprinting_config.get("strict_mode", False)
                    )
                
                logger.info(f"Token management initialized for {slug_id}")
            except Exception as e:
                logger.warning(f"Could not initialize token management for {slug_id}: {e}")
                # Continue without token management (backward compatibility)
        
        # Set up security middleware (if not already added)
        security_config = token_management.get("security", {})
        if security_config.get("csrf_protection", True) or security_config.get("require_https", False):
            try:
                from .middleware import SecurityMiddleware
                # Only add middleware if app hasn't started yet
                if not hasattr(app.state, "_started"):
                    app.add_middleware(
                        SecurityMiddleware,
                        require_https=security_config.get("require_https", False),
                        csrf_protection=security_config.get("csrf_protection", True),
                        security_headers=True
                    )
                    logger.info(f"Security middleware added for {slug_id}")
                else:
                    logger.warning(f"Security middleware not added for {slug_id} - app already started")
            except RuntimeError as e:
                if "Cannot add middleware" in str(e):
                    logger.warning(f"Security middleware not added for {slug_id} - app already started")
                else:
                    logger.warning(f"Could not set up security middleware for {slug_id}: {e}")
            except Exception as e:
                logger.warning(f"Could not set up security middleware for {slug_id}: {e}")
        
        # Extract and store CORS, observability, features, and environment configs
        # Get manifest data first if available
        manifest_data = None
        if hasattr(engine, 'get_manifest'):
            try:
                manifest_data = await engine.get_manifest(slug_id)
            except Exception as e:
                logger.warning(f"Could not retrieve manifest for {slug_id}: {e}")
                manifest_data = None
        
        # Extract and store CORS config
        cors_config = manifest_data.get("cors", {}) if manifest_data else {}
        app.state.cors_config = merge_config_with_defaults(cors_config, CORS_DEFAULTS)
        
        # Extract and store observability config
        observability_config = manifest_data.get("observability", {}) if manifest_data else {}
        app.state.observability_config = merge_config_with_defaults(observability_config, OBSERVABILITY_DEFAULTS)
        
        # Set up CORS middleware if enabled
        if app.state.cors_config.get("enabled", False):
            try:
                from fastapi.middleware.cors import CORSMiddleware
                if not hasattr(app.state, "_started"):
                    app.add_middleware(
                        CORSMiddleware,
                        allow_origins=app.state.cors_config.get("allow_origins", ["*"]),
                        allow_credentials=app.state.cors_config.get("allow_credentials", False),
                        allow_methods=app.state.cors_config.get("allow_methods", ["GET", "POST", "PUT", "DELETE", "PATCH"]),
                        allow_headers=app.state.cors_config.get("allow_headers", ["*"]),
                        expose_headers=app.state.cors_config.get("expose_headers", []),
                        max_age=app.state.cors_config.get("max_age", 3600)
                    )
                    logger.info(f"CORS middleware added for {slug_id}")
                else:
                    logger.warning(f"CORS middleware not added for {slug_id} - app already started")
            except Exception as e:
                logger.warning(f"Could not set up CORS middleware for {slug_id}: {e}")
        
        logger.info(f"Auth setup completed for {slug_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error setting up auth from manifest for {slug_id}: {e}", exc_info=True)
        return False


async def add_security_middleware(app: FastAPI, slug_id: str, engine) -> bool:
    """
    Add security middleware to FastAPI app.
    
    This should be called after setup_auth_from_manifest to actually add the middleware.
    
    Args:
        app: FastAPI application instance
        slug_id: App slug identifier
        engine: RuntimeEngine instance
    
    Returns:
        True if middleware was added, False otherwise
    """
    try:
        # Get config
        config = await get_auth_config(slug_id, engine)
        token_management = config.get("token_management", {})
        
        if not token_management.get("enabled", True):
            return False
        
        security_config = token_management.get("security", {})
        if security_config.get("csrf_protection", True) or security_config.get("require_https", False):
            from .middleware import SecurityMiddleware
            app.add_middleware(
                SecurityMiddleware,
                require_https=security_config.get("require_https", False),
                csrf_protection=security_config.get("csrf_protection", True),
                security_headers=True
            )
            logger.info(f"Security middleware added to app for {slug_id}")
            return True
        
        return False
    except Exception as e:
        logger.error(f"Error adding security middleware for {slug_id}: {e}", exc_info=True)
        return False

