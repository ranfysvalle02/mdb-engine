"""
Casbin Provider Factory

Provides helper functions to auto-initialize Casbin authorization provider
from manifest configuration.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

from .casbin_models import DEFAULT_RBAC_MODEL, SIMPLE_ACL_MODEL

if TYPE_CHECKING:
    import casbin

    from .provider import CasbinAdapter

logger = logging.getLogger(__name__)


def get_casbin_model(model_type: str = "rbac") -> str:
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
        model_path = Path(model_type)
        if model_path.exists():
            return model_path.read_text()
        else:
            logger.warning(
                f"Casbin model file not found: {model_type}, using default RBAC model"
            )
            return DEFAULT_RBAC_MODEL


async def create_casbin_enforcer(
    db,
    model: str = "rbac",
    policies_collection: str = "casbin_policies",
    default_roles: Optional[list] = None,
) -> casbin.AsyncEnforcer:
    """
    Create a Casbin AsyncEnforcer with MongoDB adapter.

    Args:
        db: MongoDB database instance (Motor AsyncIOMotorDatabase)
        model: Casbin model type ("rbac", "acl") or path to model file
        policies_collection: MongoDB collection name for policies
        default_roles: List of default roles to create (optional)

    Returns:
        Configured Casbin AsyncEnforcer instance

    Raises:
        ImportError: If casbin or casbin-motor-adapter is not installed
    """
    try:
        import casbin
        from casbin_motor_adapter import MotorAdapter
    except ImportError as e:
        raise ImportError(
            "Casbin dependencies not installed. Install with: pip install mdb-engine[casbin]"
        ) from e

    # Get model string
    model_str = get_casbin_model(model)

    # Create MongoDB adapter
    adapter = MotorAdapter(db, policies_collection)

    # Create enforcer with model and adapter
    enforcer = casbin.AsyncEnforcer()
    await enforcer.set_model(casbin.new_model_from_string(model_str))
    enforcer.set_adapter(adapter)

    # Load policies from database
    await enforcer.load_policy()

    # Create default roles if specified
    if default_roles:
        await _create_default_roles(enforcer, default_roles)

    logger.info(
        f"Casbin enforcer created with model '{model}' and "
        f"policies collection '{policies_collection}'"
    )

    return enforcer


async def _create_default_roles(enforcer: "casbin.AsyncEnforcer", roles: list) -> None:
    """
    Create default roles in Casbin (as grouping rules).

    Args:
        enforcer: Casbin AsyncEnforcer instance
        roles: List of role names to create
    """
    for role in roles:
        # Create a grouping rule: role -> role (self-reference for role existence)
        # This ensures the role exists in the system
        # Actual user-role assignments will be added when users are created
        try:
            # Check if role already exists
            existing = await enforcer.get_roles_for_user(role)
            if not existing:
                # Add role as a self-grouping rule to ensure it exists
                # This is a common pattern to "register" roles
                await enforcer.add_grouping_policy(role, role)
                logger.debug(f"Created default Casbin role: {role}")
        except (AttributeError, TypeError, ValueError, RuntimeError) as e:
            logger.warning(f"Error creating default role '{role}': {e}")


async def initialize_casbin_from_manifest(
    engine, app_slug: str, auth_config: Dict[str, Any]
) -> Optional["CasbinAdapter"]:
    """
    Initialize Casbin provider from manifest configuration.

    Args:
        engine: MongoDBEngine instance
        app_slug: App slug identifier
        auth_config: Auth configuration dict from manifest (contains auth_policy)

    Returns:
        CasbinAdapter instance if successfully created, None otherwise
    """
    try:
        from .provider import CasbinAdapter

        auth_policy = auth_config.get("auth_policy", {})
        provider = auth_policy.get("provider", "casbin")

        # Only proceed if provider is casbin
        if provider != "casbin":
            return None

        # Get authorization config
        authorization = auth_policy.get("authorization", {})
        model = authorization.get("model", "rbac")
        policies_collection = authorization.get(
            "policies_collection", "casbin_policies"
        )
        default_roles = authorization.get("default_roles", [])

        # Get database from engine
        db = engine.get_database()

        # Create enforcer
        enforcer = await create_casbin_enforcer(
            db=db,
            model=model,
            policies_collection=policies_collection,
            default_roles=default_roles,
        )

        # Create adapter
        adapter = CasbinAdapter(enforcer)

        logger.info(f"Casbin provider initialized for app '{app_slug}'")

        return adapter

    except ImportError as e:
        logger.warning(
            f"Casbin not available for app '{app_slug}': {e}. "
            "Install with: pip install mdb-engine[casbin]"
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
        logger.error(
            f"Error initializing Casbin provider for app '{app_slug}': {e}",
            exc_info=True,
        )
        return None
