"""
Casbin Provider Factory

Provides helper functions to auto-initialize Casbin authorization provider
from manifest configuration.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .casbin_models import DEFAULT_RBAC_MODEL, SIMPLE_ACL_MODEL

if TYPE_CHECKING:
    import casbin  # type: ignore

    from .provider import CasbinAdapter

logger = logging.getLogger(__name__)


async def get_casbin_model(model_type: str = "rbac") -> str:
    """
    Get Casbin model string by type or path.

    Args:
        model_type: Model type ("rbac", "acl") or path to model file

    Returns:
        Casbin model string
    """
    if model_type == "rbac":
        return DEFAULT_RBAC_MODEL
    elif model_type == "acl":
        return SIMPLE_ACL_MODEL
    else:
        # Assume it's a file path
        # Try async file reading first, fallback to sync if not available
        model_path = Path(model_type)
        if model_path.exists():
            try:
                # Try async file reading (non-blocking)
                import aiofiles

                async with aiofiles.open(model_path) as f:
                    content = await f.read()
                    logger.debug(f"Read model file asynchronously: {model_path}")
                    return content
            except ImportError:
                # Fallback to sync read if aiofiles not available
                # Offloaded to thread to avoid blocking the event loop
                logger.debug("aiofiles not available, using threaded sync file read")
                return await asyncio.to_thread(model_path.read_text)
        else:
            logger.warning(f"Casbin model file not found: {model_type}, using default RBAC model")
            return DEFAULT_RBAC_MODEL


async def create_casbin_enforcer(
    mongo_uri: str,
    db_name: str,
    model: str = "rbac",
    policies_collection: str = "casbin_policies",
    default_roles: list | None = None,
) -> casbin.AsyncEnforcer:
    """
    Create a Casbin AsyncEnforcer with MongoDB adapter.

    Args:
        mongo_uri: MongoDB connection URI string
        db_name: MongoDB database name
        model: Casbin model type ("rbac", "acl") or path to model file
        policies_collection: MongoDB collection name for policies (will be app-scoped)
        default_roles: List of default roles to create (optional)

    Returns:
        Configured Casbin AsyncEnforcer instance

    Raises:
        ImportError: If casbin or casbin-motor-adapter is not installed
    """
    try:
        import casbin  # type: ignore
        from casbin_motor_adapter import Adapter  # type: ignore
    except ImportError as e:
        raise ImportError("Casbin dependencies not installed. Install with: pip install mdb-engine[casbin]") from e

    # Get model string (async)
    model_str = await get_casbin_model(model)

    # Create MongoDB adapter
    # Try to pass policies_collection if supported by the adapter
    logger.debug(
        f"Creating Casbin MotorAdapter with URI: {mongo_uri[:50]}..., "
        f"db_name: {db_name}, collection: {policies_collection}"
    )
    try:
        # Try passing collection name as third parameter
        try:
            adapter = Adapter(mongo_uri, db_name, policies_collection)
            logger.debug(f"Casbin MotorAdapter created successfully with custom " f"collection '{policies_collection}'")
        except TypeError:
            # Fallback: adapter doesn't support collection parameter, use default
            logger.warning("Adapter doesn't support custom collection name, " "using default collection")
            adapter = Adapter(mongo_uri, db_name)
            logger.debug("Casbin MotorAdapter created successfully (using default collection)")
    except (RuntimeError, ValueError, AttributeError, TypeError, ConnectionError):
        logger.exception("Failed to create Casbin MotorAdapter")
        raise

    # Create enforcer with model and adapter
    # AsyncEnforcer can accept model string or Model object
    logger.debug("Creating Casbin AsyncEnforcer with model and adapter...")
    try:
        logger.debug(f"Model string length: {len(model_str)} chars")
        logger.debug(f"Adapter type: {type(adapter)}")

        # Create Model object from string
        model = casbin.Model()
        model.load_model_from_text(model_str)
        logger.debug(f"Model object created successfully, type: {type(model)}")

        # Create enforcer with Model object and adapter
        # AsyncEnforcer auto-loads by default (auto_load=True), so we don't need manual load
        logger.debug("Calling casbin.AsyncEnforcer(model, adapter)...")
        enforcer = casbin.AsyncEnforcer(model, adapter)
        logger.debug("Enforcer created successfully")

        # Check if policies were auto-loaded
        # If auto_load is enabled (default), policies are already loaded
        # Only manually load if auto_load was disabled
        if not getattr(enforcer, "auto_load", True):
            logger.debug("Auto-load disabled, manually loading policies...")
            await enforcer.load_policy()
            logger.debug("Policies loaded successfully")
        else:
            logger.debug("Policies auto-loaded by AsyncEnforcer constructor")
    except (RuntimeError, ValueError, AttributeError, TypeError, OSError):
        logger.exception("Failed to create or configure Casbin enforcer")
        # Try alternative: use temp file approach
        logger.info("Attempting alternative: using temporary model file...")
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False) as f:
                f.write(model_str)
                temp_model_path = f.name
            try:
                enforcer = casbin.AsyncEnforcer(temp_model_path, adapter)
                logger.info("Enforcer created successfully using temp model file")
                # Clean up temp file
                os.unlink(temp_model_path)
            except (RuntimeError, ValueError, AttributeError, TypeError, OSError) as e2:
                if os.path.exists(temp_model_path):
                    os.unlink(temp_model_path)
                logger.exception("Alternative approach also failed")
                raise RuntimeError("Failed to create Casbin enforcer") from e2
        except (OSError, RuntimeError) as e2:
            logger.exception("Failed to try alternative approach")
            raise RuntimeError("Failed to create Casbin enforcer") from e2

    # Note: Removed default_roles creation - roles exist implicitly when assigned to users

    logger.info(f"Casbin enforcer created with model '{model}' and " f"policies collection '{policies_collection}'")

    return enforcer


# Removed _create_default_roles function - roles exist implicitly when assigned to users
# No need to "create" roles beforehand with self-referencing policies


async def initialize_casbin_from_manifest(engine, app_slug: str, auth_config: dict[str, Any]) -> CasbinAdapter | None:
    """
    Initialize Casbin provider from manifest configuration.

    Args:
        engine: MongoDBEngine instance
        app_slug: App slug identifier
        auth_config: Full manifest config dict (contains auth.policy or auth_policy)

    Returns:
        CasbinAdapter instance if successfully created, None otherwise
    """
    try:
        from .provider import CasbinAdapter

        # Support both old (auth_policy) and new (auth.policy) structures
        auth = auth_config.get("auth", {})
        auth_policy = auth.get("policy", {}) or auth_config.get("auth_policy", {})
        provider = auth_policy.get("provider", "casbin")

        # Only proceed if provider is casbin
        if provider != "casbin":
            logger.debug(f"Provider is '{provider}', not 'casbin' - skipping Casbin initialization")
            return None

        logger.info(f"Initializing Casbin provider for app '{app_slug}'...")

        # Get authorization config
        authorization = auth_policy.get("authorization", {})
        model = authorization.get("model", "rbac")
        policies_collection = authorization.get("policies_collection", "casbin_policies")
        default_roles = authorization.get("default_roles", [])
        initial_policies = authorization.get("initial_policies", [])
        initial_roles = authorization.get("initial_roles", [])

        # Create enforcer with MongoDB connection info from engine
        logger.debug(f"Creating Casbin enforcer with URI: {engine.mongo_uri[:50]}..., " f"db: {engine.db_name}")
        try:
            enforcer = await create_casbin_enforcer(
                mongo_uri=engine.mongo_uri,
                db_name=engine.db_name,
                model=model,
                policies_collection=policies_collection,
                default_roles=default_roles,
            )
            logger.debug("Casbin enforcer created successfully")
        except (
            RuntimeError,
            ValueError,
            AttributeError,
            TypeError,
            ConnectionError,
            ImportError,
        ):
            logger.exception(f"Failed to create Casbin enforcer for '{app_slug}'")
            return None

        # Create adapter
        try:
            adapter = CasbinAdapter(enforcer)
            logger.info("CasbinAdapter created successfully")
        except (RuntimeError, ValueError, AttributeError, TypeError):
            logger.exception(f"Failed to create CasbinAdapter for '{app_slug}'")
            return None

        # Set up initial policies if configured
        if initial_policies:
            logger.info(f"Setting up {len(initial_policies)} initial policies...")
            for policy in initial_policies:
                if isinstance(policy, list | tuple) and len(policy) >= 3:
                    role, resource, action = policy[0], policy[1], policy[2]
                    try:
                        # Check if policy already exists
                        exists = await adapter.has_policy(role, resource, action)
                        if exists:
                            logger.debug(f"  Policy already exists: {role} -> {resource}:{action}")
                        else:
                            added = await adapter.add_policy(role, resource, action)
                            if added:
                                logger.debug(f"  Added policy: {role} -> {resource}:{action}")
                            else:
                                logger.warning(f"  Failed to add policy: {role} -> " f"{resource}:{action}")
                    except (ValueError, TypeError, RuntimeError, AttributeError) as e:
                        logger.warning(f"  Failed to add policy {policy}: {e}", exc_info=True)

        # Set up initial role assignments if configured
        # Disable auto_save during bulk operations for better performance
        if initial_roles:
            logger.info(f"Setting up {len(initial_roles)} initial role assignments...")
            # Temporarily disable auto_save to avoid writing on every iteration
            original_auto_save = getattr(enforcer, "auto_save", True)
            try:
                enforcer.auto_save = False
                logger.debug("Disabled auto_save for bulk role assignment")
            except AttributeError:
                # Some enforcer implementations don't support auto_save attribute
                logger.debug("auto_save attribute not available, proceeding with default behavior")

            for role_assignment in initial_roles:
                if isinstance(role_assignment, dict):
                    user = role_assignment.get("user")
                    role = role_assignment.get("role")
                    if user and role:
                        try:
                            # Check if role assignment already exists
                            exists = await adapter.has_role_for_user(user, role)
                            logger.debug(f"  Checking role assignment: {user} -> {role}, " f"exists={exists}")
                            if exists:
                                logger.info(f"  Role assignment already exists: {user} -> {role}")
                            else:
                                logger.info(f"  Adding role assignment: {user} -> {role}")
                                # Use enforcer directly with add_grouping_policy
                                added = await enforcer.add_grouping_policy(user, role)
                                if added:
                                    logger.info(f"  Successfully assigned role '{role}' " f"to user '{user}'")
                                else:
                                    logger.warning(
                                        f"  Failed to assign role '{role}' to "
                                        f"user '{user}' - add_grouping_policy returned False"
                                    )
                        except (ValueError, TypeError, RuntimeError, AttributeError):
                            logger.exception(f"  Exception assigning role {role_assignment}")

            # Restore auto_save setting
            if hasattr(enforcer, "auto_save"):
                enforcer.auto_save = original_auto_save
                logger.debug("Restored auto_save setting")

        # Verify that policies and roles were set up correctly
        if initial_policies:
            verified = 0
            for policy in initial_policies:
                if isinstance(policy, list | tuple) and len(policy) >= 3:
                    role, resource, action = policy[0], policy[1], policy[2]
                    if await adapter.has_policy(role, resource, action):
                        verified += 1
            logger.info(f"Verified {verified}/{len(initial_policies)} policies exist in memory")

        if initial_roles:
            verified = 0
            for role_assignment in initial_roles:
                if isinstance(role_assignment, dict):
                    user = role_assignment.get("user")
                    role = role_assignment.get("role")
                    if user and role and await adapter.has_role_for_user(user, role):
                        verified += 1
            logger.info(f"Verified {verified}/{len(initial_roles)} role assignments " f"exist in memory")

        # Save policies to persist them to database
        # Only save if auto_save was disabled (to avoid double-saving)
        if not getattr(enforcer, "auto_save", True):
            saved = await adapter.save_policy()
            if saved:
                logger.debug("Policies saved to database successfully")
            else:
                logger.warning("Failed to save policies to database - they may not " "persist across restarts")
        else:
            logger.debug("Skipping manual save_policy() - auto_save is enabled, " "policies already persisted")

        logger.info(f"Casbin provider initialized for app '{app_slug}'")
        logger.info(f"CasbinAdapter ready for use - type: {type(adapter).__name__}")

        return adapter

    except ImportError as e:
        # ImportError is expected if Casbin is not installed - use warning, not error
        logger.warning(
            f"Casbin not available for app '{app_slug}': {e}. " "Install with: pip install mdb-engine[casbin]"
        )
        return None
    except (
        ImportError,
        AttributeError,
        TypeError,
        ValueError,
        RuntimeError,
        KeyError,
    ) as e:
        logger.exception(f"Error initializing Casbin provider for app '{app_slug}': {e}")
        # Informational message, not exception logging
        logger.error(  # noqa: TRY400
            f"Casbin provider initialization FAILED for '{app_slug}' - "
            "check logs above for detailed error information"
        )
        return None
