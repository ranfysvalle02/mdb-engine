"""
OSO Provider Factory

Provides helper functions to auto-initialize OSO Cloud authorization provider
from manifest configuration.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from .provider import OsoAdapter

logger = logging.getLogger(__name__)


async def create_oso_cloud_client(
    api_key: Optional[str] = None,
    url: Optional[str] = None,
    max_retries: int = 3,
    retry_delay: float = 2.0,
) -> Any:
    """
    Create an OSO Cloud client instance with retry logic for Dev Server.

    Args:
        api_key: OSO Cloud API key. If None, reads from OSO_AUTH env var.
        url: OSO Cloud URL. If None, reads from OSO_URL env var.
            Defaults to production cloud.osohq.com if not set.
        max_retries: Maximum number of retry attempts (default: 3)
        retry_delay: Delay between retries in seconds (default: 2.0)

    Returns:
        OSO Cloud client instance

    Raises:
        ImportError: If oso-cloud is not installed
        ValueError: If API key is not provided
    """
    import asyncio

    # Import OSO Cloud SDK - the class is named "Oso"
    try:
        from oso_cloud import Oso

        logger.debug("✅ Imported Oso from oso_cloud")
    except ImportError:
        raise ImportError(
            "OSO Cloud SDK not installed. Install with: pip install oso-cloud"
        )

    # Get API key from parameter or environment
    if not api_key:
        api_key = os.getenv("OSO_AUTH")

    if not api_key:
        raise ValueError(
            "OSO Cloud API key not provided. "
            "Set OSO_AUTH environment variable or provide api_key parameter."
        )

    # Get URL from parameter or environment
    if not url:
        url = os.getenv("OSO_URL")

    # Initialize OSO Cloud client with explicit parameters
    # The Oso() constructor requires api_key parameter
    # For Dev Server, we may need to retry if it's not ready yet
    last_error = None
    for attempt in range(max_retries):
        try:
            if url:
                oso_client = Oso(api_key=api_key, url=url)
            else:
                oso_client = Oso(api_key=api_key)

            # Note: OSO client creation doesn't actually connect to the server
            # The connection happens on first API call, so we'll catch errors then
            logger.info(
                f"✅ OSO Cloud client created successfully (URL: {url or 'default'})"
            )
            if url:
                logger.info(f"   Using OSO Dev Server at: {url}")
            return oso_client

        except (
            ValueError,
            TypeError,
            AttributeError,
            RuntimeError,
            ConnectionError,
        ) as e:
            last_error = e
            if attempt < max_retries - 1:
                logger.warning(
                    f"Failed to create OSO Cloud client "
                    f"(attempt {attempt + 1}/{max_retries}): {e}. "
                    f"Retrying in {retry_delay} seconds..."
                )
                await asyncio.sleep(retry_delay)
            else:
                logger.error(
                    f"Failed to create OSO Cloud client after {max_retries} attempts: {e}",
                    exc_info=True,
                )
                raise

    # Should never reach here, but just in case
    if last_error:
        raise last_error
    raise RuntimeError("Failed to create OSO Cloud client")


async def setup_initial_oso_facts(
    authz_provider: OsoAdapter,
    initial_roles: Optional[List[Dict[str, Any]]] = None,
    initial_policies: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """
    Set up initial roles and policies in OSO Cloud.

    Args:
        authz_provider: OsoAdapter instance
        initial_roles: List of role assignments, e.g.:
            [{"user": "alice@example.com", "role": "admin", "resource": "documents"}]
            If "resource" is not specified, defaults to "documents" for backward compatibility
        initial_policies: List of permission policies, e.g.:
            [{"role": "admin", "resource": "documents", "action": "read"}]
    """
    if initial_roles:
        for role_assignment in initial_roles:
            try:
                user = role_assignment.get("user")
                role = role_assignment.get("role")
                resource = role_assignment.get(
                    "resource", "documents"
                )  # Default to "documents"

                if user and role:
                    # For OSO Cloud, we add has_role facts with resource context
                    # This supports resource-based authorization
                    await authz_provider.add_role_for_user(user, role, resource)
                    logger.debug(
                        f"Added role '{role}' for user '{user}' on resource '{resource}'"
                    )
            except (ValueError, TypeError, AttributeError, RuntimeError) as e:
                logger.warning(
                    f"Failed to add initial role assignment {role_assignment}: {e}"
                )

    # Note: initial_policies are not used - we use has_role facts instead
    # The policy derives permissions from roles, not from explicit grants_permission facts


async def initialize_oso_from_manifest(
    engine, app_slug: str, auth_config: Dict[str, Any]
) -> Optional[OsoAdapter]:
    """
    Initialize OSO Cloud provider from manifest configuration.

    Args:
        engine: MongoDBEngine instance
        app_slug: App slug identifier
        auth_config: Auth configuration dict from manifest (contains auth_policy)

    Returns:
        OsoAdapter instance if successfully created, None otherwise
    """
    try:
        from .provider import OsoAdapter

        # Support both old (auth_policy) and new (auth.policy) structures
        auth = auth_config.get("auth", {})
        auth_policy = auth.get("policy", {}) or auth_config.get("auth_policy", {})
        provider = auth_policy.get("provider", "casbin")

        # Only proceed if provider is oso
        if provider != "oso":
            logger.debug(
                f"Provider is '{provider}', not 'oso' - skipping OSO initialization"
            )
            return None

        logger.info(f"Initializing OSO Cloud provider for app '{app_slug}'...")

        # Get authorization config
        authorization = auth_policy.get("authorization", {})

        # Get API key from config or environment
        api_key = authorization.get("api_key") or os.getenv("OSO_AUTH")
        url = authorization.get("url") or os.getenv("OSO_URL")

        logger.debug(
            f"OSO config - API key: {'***' if api_key else 'NOT SET'}, "
            f"URL: {url or 'NOT SET (using default)'}"
        )

        if not api_key:
            logger.error(
                f"❌ OSO Cloud API key not found for app '{app_slug}'. "
                "Set OSO_AUTH environment variable or provide api_key in manifest."
            )
            return None

        # Create OSO Cloud client
        logger.debug("Creating OSO Cloud client...")
        try:
            # More retries for Dev Server which might take time to start
            oso_client = await create_oso_cloud_client(
                api_key=api_key,
                url=url,
                max_retries=5,
                retry_delay=3.0,  # 3 second delay between retries
            )
            logger.info("✅ OSO Cloud client created successfully")

            # Test connection by trying a simple authorize call
            # This will fail if the Dev Server isn't ready or policy isn't loaded
            try:
                import asyncio

                from oso_cloud import Value

                # Try a simple test authorization to verify connection
                test_actor = Value("User", "test")
                test_resource = Value("Document", "test")
                # This might fail, but it tests if the server is responding
                await asyncio.to_thread(
                    oso_client.authorize, test_actor, "read", test_resource
                )
                logger.debug("✅ OSO Dev Server connection test successful")
            except (TimeoutError, OSError, RuntimeError) as test_error:
                # Type 2: Recoverable - connection test failed, check if it's a connection error
                error_str = str(test_error).lower()
                if (
                    "connection" in error_str
                    or "refused" in error_str
                    or "timeout" in error_str
                ):
                    logger.warning(
                        f"⚠️  OSO Dev Server connection test failed - "
                        f"server might not be ready: {test_error}"
                    )
                    logger.warning(
                        "   This is OK if the Dev Server is still starting up. "
                        "Will retry on first real request."
                    )
                else:
                    # Other errors (like authorization denied) are expected and OK
                    logger.debug(
                        f"OSO connection test completed "
                        f"(authorization denied is expected): {test_error}"
                    )
        except (
            ImportError,
            AttributeError,
            TypeError,
            ValueError,
            RuntimeError,
            ConnectionError,
        ) as e:
            logger.error(f"❌ Failed to create OSO Cloud client: {e}", exc_info=True)
            return None

        # Create adapter
        logger.debug("Creating OsoAdapter...")
        adapter = OsoAdapter(oso_client)
        logger.info("✅ OsoAdapter created successfully")

        # Set up initial facts if configured
        initial_roles = authorization.get("initial_roles", [])
        initial_policies = authorization.get("initial_policies", [])

        if initial_roles or initial_policies:
            logger.info(
                f"Setting up initial OSO facts: {len(initial_roles)} roles, "
                f"{len(initial_policies)} policies"
            )
            try:
                await setup_initial_oso_facts(
                    adapter,
                    initial_roles=initial_roles,
                    initial_policies=initial_policies,
                )
                logger.info("✅ Initial OSO facts set up successfully")
            except (ValueError, TypeError, AttributeError, RuntimeError) as e:
                logger.warning(
                    f"⚠️  Failed to set up initial OSO facts: {e}", exc_info=True
                )
                # Continue anyway - adapter is still usable

        logger.info(f"✅ OSO Cloud provider initialized for app '{app_slug}'")

        return adapter

    except ImportError as e:
        logger.error(
            f"❌ OSO Cloud SDK not available for app '{app_slug}': {e}. "
            "Install with: pip install oso-cloud"
        )
        return None
    except ValueError as e:
        logger.error(f"❌ OSO Cloud configuration error for app '{app_slug}': {e}")
        return None
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
            f"❌ Error initializing OSO Cloud provider for app '{app_slug}': {e}",
            exc_info=True,
        )
        return None
