"""
Authentication Integration Helpers

Helpers for integrating authentication features from manifest configuration.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import logging
import os
from typing import Any, Dict, Optional

from fastapi import FastAPI

from .config_defaults import (CORS_DEFAULTS, OBSERVABILITY_DEFAULTS,
                              SECURITY_CONFIG_DEFAULTS,
                              TOKEN_MANAGEMENT_DEFAULTS)
from .config_helpers import merge_config_with_defaults
from .helpers import initialize_token_management

logger = logging.getLogger(__name__)

# Cache for auth configs
_auth_config_cache: Dict[str, Dict[str, Any]] = {}


def _has_cors_middleware(app: FastAPI) -> bool:
    """
    Check if CORS middleware is already added to the FastAPI app.

    Args:
        app: FastAPI application instance

    Returns:
        True if CORS middleware exists, False otherwise
    """
    try:
        from fastapi.middleware.cors import CORSMiddleware

        # Check if CORS middleware is in the middleware stack
        # FastAPI stores middleware in app.user_middleware list
        for middleware in app.user_middleware:
            # Middleware is stored as (middleware_class, options) tuple
            if len(middleware) >= 1:
                middleware_cls = middleware[0]
                # Check if it's CORSMiddleware or a subclass
                if middleware_cls == CORSMiddleware or (
                    hasattr(middleware_cls, "__name__")
                    and "CORS" in middleware_cls.__name__
                ):
                    return True
        return False
    except (AttributeError, TypeError, ValueError) as e:
        logger.debug(f"Error checking for CORS middleware: {e}")
        return False


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
        engine: MongoDBEngine instance

    Returns:
        Dictionary with token_management and auth (containing policy and users) configs
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

        # Extract auth configs - support both old and new format for backward compatibility
        auth_config = manifest.get("auth", {})

        # Migrate old format if present
        if "auth_policy" in manifest or "sub_auth" in manifest:
            if "policy" not in auth_config and "auth_policy" in manifest:
                auth_config["policy"] = manifest.get("auth_policy", {})
            if "users" not in auth_config and "sub_auth" in manifest:
                auth_config["users"] = manifest.get("sub_auth", {})

        config = {
            "token_management": manifest.get("token_management", {}),
            "auth": auth_config,
        }

        # Cache it
        _auth_config_cache[slug_id] = config

        return config
    except (AttributeError, TypeError, ValueError, KeyError, RuntimeError) as e:
        logger.error(f"Error getting auth config for {slug_id}: {e}", exc_info=True)
        return {}


async def _setup_authorization_provider(
    app: FastAPI, engine, slug_id: str, config: Dict[str, Any]
) -> None:
    """Set up authorization provider (Casbin/OSO/custom) from manifest."""
    auth = config.get("auth", {})
    auth_policy = auth.get("policy", {})
    provider = auth_policy.get("provider", "casbin" if auth_policy else None)

    if provider == "casbin" or (provider is None and auth_policy):
        # Auto-create Casbin provider
        try:
            from .casbin_factory import initialize_casbin_from_manifest

            authz_provider = await initialize_casbin_from_manifest(
                engine, slug_id, config
            )
            if authz_provider:
                app.state.authz_provider = authz_provider
                logger.info(
                    f"Authorization provider (Casbin) auto-created for {slug_id}"
                )
            else:
                logger.debug(
                    f"Casbin provider not created for {slug_id} (may not be installed)"
                )
        except (
            ImportError,
            AttributeError,
            TypeError,
            ValueError,
            RuntimeError,
            KeyError,
        ) as e:
            logger.warning(f"Could not auto-create Casbin provider for {slug_id}: {e}")
    elif provider == "oso":
        # Auto-create OSO Cloud provider
        try:
            from .oso_factory import initialize_oso_from_manifest

            authz_provider = await initialize_oso_from_manifest(engine, slug_id, config)
            if authz_provider:
                app.state.authz_provider = authz_provider
                logger.info(
                    f"✅ Authorization provider (OSO Cloud) auto-created for {slug_id}"
                )
            else:
                logger.error(
                    f"❌ OSO Cloud provider not created for {slug_id}. "
                    f"Check logs above for details. "
                    f"OSO_AUTH={'SET' if os.getenv('OSO_AUTH') else 'NOT SET'}, "
                    f"OSO_URL={os.getenv('OSO_URL', 'NOT SET')}"
                )
        except (
            ImportError,
            AttributeError,
            TypeError,
            ValueError,
            RuntimeError,
            ConnectionError,
            KeyError,
        ) as e:
            logger.error(
                f"❌ Could not auto-create OSO Cloud provider for {slug_id}: {e}",
                exc_info=True,
            )
    elif provider == "custom":
        logger.info(f"Custom provider specified for {slug_id} - manual setup required")


async def _setup_demo_users(
    app: FastAPI, engine, slug_id: str, config: Dict[str, Any]
) -> list:
    """Set up demo users and link with OSO roles if applicable."""
    auth = config.get("auth", {})
    users_config = auth.get("users", {})
    demo_users = []

    if not users_config.get("enabled", False):
        return demo_users

    seed_strategy = users_config.get("demo_user_seed_strategy", "auto")
    demo_users_config = users_config.get("demo_users", [])

    # Only auto-create if strategy is explicitly "auto" or default (when not specified)
    if seed_strategy == "auto":
        # Check if demo_users is explicitly configured or using defaults
        has_explicit_demo_users = len(demo_users_config) > 0
        auto_link_platform = users_config.get("auto_link_platform_demo", True)

        if has_explicit_demo_users or auto_link_platform:
            try:
                from .users import ensure_demo_users_exist

                db = engine.get_scoped_db(slug_id)

                logger.info(
                    f"Auto-creating demo users for {slug_id} "
                    f"(strategy: {seed_strategy}, "
                    f"explicit_users: {has_explicit_demo_users}, "
                    f"auto_link_platform: {auto_link_platform})"
                )

                demo_users = await ensure_demo_users_exist(
                    db=db,
                    slug_id=slug_id,
                    config=config,
                    mongo_uri=engine.mongo_uri,
                    db_name=engine.db_name,
                )
                if demo_users:
                    logger.info(
                        f"✅ Created/verified {len(demo_users)} demo user(s) for {slug_id}: "
                        f"{', '.join([u.get('email', 'unknown') for u in demo_users])}"
                    )
                else:
                    logger.debug(
                        f"No demo users created for {slug_id} "
                        f"(may already exist or config disabled)"
                    )
            except (
                ValueError,
                TypeError,
                AttributeError,
                RuntimeError,
                ConnectionError,
                KeyError,
            ) as e:
                logger.warning(
                    f"Could not create demo users for {slug_id}: {e}",
                    exc_info=True,
                )
        else:
            logger.debug(
                f"Skipping demo user creation for {slug_id}: "
                f"demo_user_seed_strategy is 'auto' but no demo_users configured "
                f"and auto_link_platform_demo is disabled"
            )
    elif seed_strategy == "disabled":
        logger.debug(
            f"Demo user creation disabled for {slug_id} (demo_user_seed_strategy: disabled)"
        )
    elif seed_strategy == "manual":
        logger.debug(
            f"Demo user creation set to manual for {slug_id} - skipping auto-creation"
        )

    # Link demo users with OSO initial_roles if auth provider is OSO
    if hasattr(app.state, "authz_provider") and demo_users:
        try:
            auth = config.get("auth", {})
            auth_policy = auth.get("policy", {})
            authorization = auth_policy.get("authorization", {})
            initial_roles = authorization.get("initial_roles", [])

            if initial_roles:
                # Match demo users by email to initial_roles entries
                for demo_user in demo_users:
                    user_email = demo_user.get("email")
                    if not user_email:
                        continue

                    for role_assignment in initial_roles:
                        if role_assignment.get("user") == user_email:
                            role = role_assignment.get("role")
                            resource = role_assignment.get("resource", "documents")
                            try:
                                await app.state.authz_provider.add_role_for_user(
                                    user_email, role, resource
                                )
                                logger.info(
                                    f"✅ Assigned role '{role}' on resource '{resource}' "
                                    f"to demo user '{user_email}' for {slug_id}"
                                )
                            except (
                                ValueError,
                                TypeError,
                                AttributeError,
                                RuntimeError,
                                ConnectionError,
                            ) as e:
                                logger.warning(
                                    f"Failed to assign role '{role}' to user '{user_email}' "
                                    f"for {slug_id}: {e}"
                                )
        except (
            ValueError,
            TypeError,
            AttributeError,
            RuntimeError,
            ConnectionError,
            KeyError,
        ) as e:
            logger.warning(
                f"Could not link demo users with OSO roles for {slug_id}: {e}",
                exc_info=True,
            )

    return demo_users


async def _setup_token_management(
    app: FastAPI, engine, slug_id: str, token_management: Dict[str, Any]
) -> None:
    """Initialize token management (blacklist and session manager)."""
    if token_management.get("auto_setup", True):
        try:
            db = engine.get_database()
            await initialize_token_management(app, db)

            # Configure session fingerprinting if session manager exists
            session_mgr = getattr(app.state, "session_manager", None)
            if session_mgr:
                fingerprinting_config = app.state.security_config.get(
                    "session_fingerprinting", {}
                )
                session_mgr.configure_fingerprinting(
                    enabled=fingerprinting_config.get("enabled", True),
                    strict=fingerprinting_config.get("strict_mode", False),
                )

            logger.info(f"Token management initialized for {slug_id}")
        except (
            ImportError,
            AttributeError,
            TypeError,
            ValueError,
            RuntimeError,
            KeyError,
        ) as e:
            logger.warning(f"Could not initialize token management for {slug_id}: {e}")
            # Continue without token management (backward compatibility)


async def _setup_security_middleware(
    app: FastAPI, slug_id: str, security_config: Dict[str, Any]
) -> None:
    """Set up security middleware (if not already added)."""
    if security_config.get("csrf_protection", True) or security_config.get(
        "require_https", False
    ):
        try:
            from .middleware import SecurityMiddleware

            # Try to add middleware - FastAPI will raise RuntimeError if app has started
            # FastAPI with lifespan might have initialized middleware stack already
            # Try to add middleware and catch the error if it fails
            try:
                app.add_middleware(
                    SecurityMiddleware,
                    require_https=security_config.get("require_https", False),
                    csrf_protection=security_config.get("csrf_protection", True),
                    security_headers=True,
                )
                logger.info(f"Security middleware added for {slug_id}")
            except (RuntimeError, ValueError) as e:
                error_msg = str(e).lower()
                if "cannot add middleware" in error_msg or "middleware" in error_msg:
                    # App has already started - this is expected with lifespan context managers
                    # The middleware is optional for security, so we just log
                    # a debug message
                    logger.debug(
                        f"Security middleware not added for {slug_id} - "
                        f"app middleware stack already initialized. "
                        f"This is normal when using lifespan context managers."
                    )
                else:
                    logger.warning(
                        f"Could not set up security middleware for {slug_id}: {e}"
                    )
        except (AttributeError, TypeError, ValueError, RuntimeError, ImportError) as e:
            logger.warning(f"Could not set up security middleware for {slug_id}: {e}")


async def _setup_cors_and_observability(
    app: FastAPI, engine, slug_id: str, config: Dict[str, Any]
) -> None:
    """Set up CORS and observability configs and middleware."""
    # Get manifest data first if available
    manifest_data = None
    if hasattr(engine, "get_manifest"):
        try:
            manifest_data = await engine.get_manifest(slug_id)
        except (AttributeError, TypeError, ValueError, RuntimeError, KeyError) as e:
            logger.warning(f"Could not retrieve manifest for {slug_id}: {e}")
            manifest_data = None

    # Extract and store CORS config
    cors_config = manifest_data.get("cors", {}) if manifest_data else {}
    app.state.cors_config = merge_config_with_defaults(cors_config, CORS_DEFAULTS)

    # Extract and store observability config
    observability_config = (
        manifest_data.get("observability", {}) if manifest_data else {}
    )
    app.state.observability_config = merge_config_with_defaults(
        observability_config, OBSERVABILITY_DEFAULTS
    )

    # Set up CORS middleware if enabled
    if app.state.cors_config.get("enabled", False):
        try:
            # Check if CORS middleware already exists to avoid duplication
            if _has_cors_middleware(app):
                logger.debug(
                    f"CORS middleware already exists for {slug_id}, skipping addition"
                )
            else:
                from fastapi.middleware.cors import CORSMiddleware

                if not hasattr(app.state, "_started"):
                    try:
                        app.add_middleware(
                            CORSMiddleware,
                            allow_origins=app.state.cors_config.get(
                                "allow_origins", ["*"]
                            ),
                            allow_credentials=app.state.cors_config.get(
                                "allow_credentials", False
                            ),
                            allow_methods=app.state.cors_config.get(
                                "allow_methods",
                                ["GET", "POST", "PUT", "DELETE", "PATCH"],
                            ),
                            allow_headers=app.state.cors_config.get(
                                "allow_headers", ["*"]
                            ),
                            expose_headers=app.state.cors_config.get(
                                "expose_headers", []
                            ),
                            max_age=app.state.cors_config.get("max_age", 3600),
                        )
                        logger.info(f"CORS middleware added for {slug_id}")
                    except (RuntimeError, ValueError) as e:
                        error_msg = str(e).lower()
                        if (
                            "cannot add middleware" in error_msg
                            or "middleware" in error_msg
                        ):
                            logger.debug(
                                f"CORS middleware not added for {slug_id} - "
                                f"app middleware stack already initialized. "
                                f"This is normal when using lifespan context managers."
                            )
                        else:
                            logger.warning(
                                f"Could not set up CORS middleware for {slug_id}: {e}"
                            )
                else:
                    logger.warning(
                        f"CORS middleware not added for {slug_id} - app already started"
                    )
        except (AttributeError, TypeError, ValueError, RuntimeError, ImportError) as e:
            logger.warning(f"Could not set up CORS middleware for {slug_id}: {e}")

    # Add stale session cleanup middleware if auth.users is enabled
    auth = config.get("auth", {})
    users_config = auth.get("users", {})
    if users_config.get("enabled", False):
        try:
            from .middleware import StaleSessionMiddleware

            try:
                app.add_middleware(
                    StaleSessionMiddleware, slug_id=slug_id, engine=engine
                )
                logger.info(f"Stale session cleanup middleware added for {slug_id}")
            except (RuntimeError, ValueError) as e:
                error_msg = str(e).lower()
                if "cannot add middleware" in error_msg or "middleware" in error_msg:
                    logger.debug(
                        f"Stale session middleware not added for {slug_id} - "
                        f"app middleware stack already initialized. "
                        f"This is normal when using lifespan context managers."
                    )
                else:
                    logger.warning(
                        f"Could not set up stale session middleware for {slug_id}: {e}"
                    )
        except (AttributeError, TypeError, ValueError, RuntimeError, ImportError) as e:
            logger.warning(
                f"Could not set up stale session middleware for {slug_id}: {e}"
            )


async def setup_auth_from_manifest(app: FastAPI, engine, slug_id: str) -> bool:
    """
    Set up authentication features from manifest configuration.

    This function:
    1. Reads auth.policy and token_management config from manifest
    2. Auto-creates authorization provider (default: Casbin) if configured
    3. Initializes TokenBlacklist and SessionManager if enabled
    4. Sets up security middleware if configured
    5. Links auth.users to authorization if enabled
    6. Stores config in app.state for easy access

    Args:
        app: FastAPI application instance
        engine: MongoDBEngine instance
        slug_id: App slug identifier

    Returns:
        True if setup was successful, False otherwise
    """
    try:
        # Get auth config
        config = await get_auth_config(slug_id, engine)
        token_management = config.get("token_management", {})

        # Set up authorization provider
        await _setup_authorization_provider(app, engine, slug_id, config)

        # Set up demo users
        await _setup_demo_users(app, engine, slug_id, config)

        # Check if token management is enabled
        if not token_management.get("enabled", True):
            logger.info(f"Token management disabled for {slug_id}")
            return False

        # Store config in app state for easy access
        merged_token_config = merge_config_with_defaults(
            token_management, TOKEN_MANAGEMENT_DEFAULTS
        )
        app.state.token_management_config = merged_token_config
        app.state.auth_config = config

        # Extract and store security policy config with defaults merged
        security_config = token_management.get("security", {})
        app.state.security_config = merge_config_with_defaults(
            security_config, SECURITY_CONFIG_DEFAULTS
        )

        # Initialize token management
        await _setup_token_management(app, engine, slug_id, token_management)

        # Set up security middleware
        await _setup_security_middleware(app, slug_id, security_config)

        # Set up CORS and observability
        await _setup_cors_and_observability(app, engine, slug_id, config)

        logger.info(f"Auth setup completed for {slug_id}")
        return True

    except (
        ValueError,
        TypeError,
        AttributeError,
        RuntimeError,
        ImportError,
        KeyError,
        ConnectionError,
    ) as e:
        logger.error(
            f"Error setting up auth from manifest for {slug_id}: {e}", exc_info=True
        )
        return False
