"""
Optional Ray Integration for MDB Engine.

Provides optional Ray support for distributed processing with app isolation.
This module gracefully degrades if Ray is not installed.

Usage:
    # Check if Ray is available
    from mdb_engine.core.ray_integration import RAY_AVAILABLE

    if RAY_AVAILABLE:
        from mdb_engine.core.ray_integration import (
            AppRayActor,
            get_ray_actor_handle,
            ray_actor_decorator,
        )

    # Or use the helper that handles availability
    actor = await get_ray_actor_handle("my_app")
    if actor:
        result = await actor.process.remote(data)

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import logging
import os
from typing import TYPE_CHECKING, Any, Callable, Optional, TypeVar

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# --- Ray Availability Check ---
# This flag allows code to check Ray availability without try/except
RAY_AVAILABLE = False
ray = None

try:
    import ray as _ray

    ray = _ray
    RAY_AVAILABLE = True
    logger.debug("Ray is available")
except ImportError:
    logger.debug("Ray not installed - Ray features will be disabled")


# Type variable for decorator
T = TypeVar("T")


class AppRayActor:
    """
    Base Ray actor class for app-specific isolated environments.

    Each app can have its own Ray actor with:
    - Isolated MongoDB connection
    - App-specific configuration
    - Isolated state

    Subclass this to create app-specific actors:

        @ray.remote
        class MyAppActor(AppRayActor):
            async def process(self, data):
                db = await self.get_app_db()
                # Process with isolated DB access
                return result

    Attributes:
        app_slug: Application identifier
        mongo_uri: MongoDB connection URI
        db_name: Database name
        use_in_memory_fallback: Whether to use in-memory DB if MongoDB unavailable
    """

    def __init__(
        self,
        app_slug: str,
        mongo_uri: str,
        db_name: str,
        use_in_memory_fallback: bool = False,
    ) -> None:
        """
        Initialize app-specific Ray actor.

        Args:
            app_slug: Application identifier
            mongo_uri: MongoDB connection URI
            db_name: Database name
            use_in_memory_fallback: Use in-memory DB if MongoDB unavailable
        """
        self.app_slug = app_slug
        self.mongo_uri = mongo_uri
        self.db_name = db_name
        self.use_in_memory_fallback = use_in_memory_fallback

        # Lazy initialization for engine
        self._engine = None
        self._initialized = False

        logger.info(f"AppRayActor created for '{app_slug}' " f"(fallback={use_in_memory_fallback})")

    async def _ensure_initialized(self) -> None:
        """
        Ensure the actor is initialized with database connection.

        Called lazily on first database access.
        """
        if self._initialized:
            return

        try:
            from .engine import MongoDBEngine

            self._engine = MongoDBEngine(
                mongo_uri=self.mongo_uri,
                db_name=self.db_name,
            )
            await self._engine.initialize()
            self._initialized = True
            logger.info(f"AppRayActor engine initialized for '{self.app_slug}'")

        except (ConnectionError, ValueError, RuntimeError, OSError) as e:
            logger.exception(f"Error initializing engine in Ray actor for '{self.app_slug}': {e}")
            if self.use_in_memory_fallback:
                logger.warning(f"Using in-memory fallback for '{self.app_slug}'")
                self._engine = None
                self._initialized = True
            else:
                raise

    async def get_app_db(self, app_token: Optional[str] = None) -> Any:
        """
        Get scoped database for this app.

        Args:
            app_token: Optional app token for authentication

        Returns:
            ScopedMongoWrapper for this app

        Raises:
            RuntimeError: If engine not available and no fallback
        """
        await self._ensure_initialized()

        if not self._engine:
            if self.use_in_memory_fallback:
                raise RuntimeError(f"In-memory fallback not yet implemented for '{self.app_slug}'")
            raise RuntimeError(f"Engine not available for '{self.app_slug}'")

        # Try to get app token from environment if not provided
        if not app_token:
            env_var_name = f"{self.app_slug.upper()}_SECRET"
            app_token = os.getenv(env_var_name)

        return self._engine.get_scoped_db(self.app_slug, app_token=app_token)

    def get_app_slug(self) -> str:
        """Get the app slug."""
        return self.app_slug

    async def health_check(self) -> dict:
        """
        Health check for the actor.

        Returns:
            Dict with health status information
        """
        await self._ensure_initialized()

        return {
            "app_slug": self.app_slug,
            "initialized": self._initialized,
            "engine_available": self._engine is not None,
            "status": "healthy" if self._initialized else "initializing",
        }

    async def shutdown(self) -> None:
        """Shutdown the actor and release resources."""
        if self._engine:
            await self._engine.shutdown()
            self._engine = None
        self._initialized = False
        logger.info(f"AppRayActor shutdown for '{self.app_slug}'")


async def get_ray_actor_handle(
    app_slug: str,
    namespace: str = "modular_labs",
    mongo_uri: Optional[str] = None,
    db_name: Optional[str] = None,
    create_if_missing: bool = True,
    actor_class: Optional[type] = None,
) -> Optional[Any]:
    """
    Get or create a Ray actor handle for an app.

    This function:
    1. Checks if Ray is available
    2. Initializes Ray if not already done
    3. Tries to get existing actor by name
    4. Creates new actor if missing and create_if_missing=True

    Args:
        app_slug: Application identifier
        namespace: Ray namespace (default: "modular_labs")
        mongo_uri: MongoDB URI (default: from MONGODB_URI env var)
        db_name: Database name (default: from MONGODB_DB env var)
        create_if_missing: Create actor if it doesn't exist (default: True)
        actor_class: Custom actor class to use (default: AppRayActor)

    Returns:
        Ray actor handle, or None if Ray unavailable or actor not found

    Example:
        # Get or create actor
        actor = await get_ray_actor_handle("my_app")
        if actor:
            result = await actor.process.remote(data)

        # Get existing actor only
        actor = await get_ray_actor_handle("my_app", create_if_missing=False)
    """
    if not RAY_AVAILABLE:
        logger.debug(f"Ray not available - cannot get actor for '{app_slug}'")
        return None

    actor_name = f"{app_slug}-actor"

    # Ensure Ray is initialized
    if not ray.is_initialized():
        ray_address = os.getenv("RAY_ADDRESS")
        if ray_address:
            try:
                ray.init(address=ray_address, namespace=namespace)
                logger.info(f"Ray initialized with address: {ray_address}")
            except (RuntimeError, ConnectionError) as e:
                logger.exception(f"Failed to initialize Ray: {e}")
                return None
        else:
            try:
                # Initialize local Ray instance
                ray.init(namespace=namespace, ignore_reinit_error=True)
                logger.info("Ray initialized locally")
            except RuntimeError as e:
                logger.exception(f"Failed to initialize local Ray: {e}")
                return None

    # Try to get existing actor
    try:
        handle = ray.get_actor(actor_name, namespace=namespace)
        logger.debug(f"Found existing Ray actor: {actor_name}")
        return handle
    except ValueError:
        # Actor doesn't exist
        if not create_if_missing:
            logger.debug(f"Ray actor '{actor_name}' not found")
            return None

    # Create new actor
    try:
        # Use provided class or default to AppRayActor
        cls = actor_class or AppRayActor

        # Make it a Ray remote class if not already
        if not hasattr(cls, "remote"):
            cls = ray.remote(cls)

        # Get connection parameters
        uri = mongo_uri or os.getenv("MONGODB_URI", "mongodb://localhost:27017")
        db = db_name or os.getenv("MONGODB_DB", "mdb_runtime")

        handle = cls.options(
            name=actor_name,
            namespace=namespace,
            lifetime="detached",
            get_if_exists=True,
        ).remote(
            app_slug=app_slug,
            mongo_uri=uri,
            db_name=db,
        )

        logger.info(f"Created Ray actor '{actor_name}' in namespace '{namespace}'")
        return handle

    except ray.exceptions.RayError as e:
        logger.error(f"Error creating Ray actor for '{app_slug}': {e}", exc_info=True)
        return None


def ray_actor_decorator(
    app_slug: Optional[str] = None,
    namespace: str = "modular_labs",
    isolated: bool = True,
    lifetime: str = "detached",
    max_restarts: int = -1,
) -> Callable[[type], type]:
    """
    Decorator for creating Ray actors with app isolation.

    This decorator:
    1. Converts a class to a Ray remote class
    2. Adds app-specific namespace (if isolated=True)
    3. Provides a convenient spawn() class method

    Args:
        app_slug: Application identifier. If None, derived from class name
        namespace: Base Ray namespace (default: "modular_labs")
        isolated: Use app-specific namespace for isolation (default: True)
        lifetime: Actor lifetime ("detached" or "non_detached")
        max_restarts: Max automatic restarts (-1 for unlimited)

    Returns:
        Decorated class

    Example:
        @ray_actor_decorator(app_slug="my_app", isolated=True)
        class MyAppActor(AppRayActor):
            async def process(self, data):
                db = await self.get_app_db()
                return await db.items.find_one({"id": data["id"]})

        # Spawn the actor
        actor = MyAppActor.spawn()
        result = await actor.process.remote({"id": "123"})

    Note:
        If Ray is not available, returns the class unchanged (no-op decorator).
    """
    if not RAY_AVAILABLE:

        def noop_decorator(cls: type) -> type:
            logger.debug(f"Ray not available - {cls.__name__} not converted to Ray actor")
            return cls

        return noop_decorator

    def decorator(cls: type) -> type:
        # Determine app slug from class name if not provided
        actor_app_slug = app_slug
        if actor_app_slug is None:
            # Convert class name to slug: MyAppActor -> my_app
            name = cls.__name__
            if name.endswith("Actor"):
                name = name[:-5]
            # Convert CamelCase to snake_case
            import re

            actor_app_slug = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()

        # Determine namespace
        actor_namespace = f"{namespace}_{actor_app_slug}" if isolated else namespace

        # Convert to Ray remote class
        ray_cls = ray.remote(cls)

        # Store metadata on the class
        ray_cls._app_slug = actor_app_slug
        ray_cls._namespace = actor_namespace
        ray_cls._isolated = isolated

        # Add spawn class method
        @classmethod
        def spawn(
            cls,
            *args,
            mongo_uri: Optional[str] = None,
            db_name: Optional[str] = None,
            **kwargs,
        ):
            """
            Spawn an instance of this actor.

            Args:
                *args: Positional arguments for actor __init__
                mongo_uri: MongoDB URI (default: from env)
                db_name: Database name (default: from env)
                **kwargs: Keyword arguments for actor __init__

            Returns:
                Ray actor handle
            """
            actor_name = f"{actor_app_slug}-{cls.__name__.lower()}"

            # Get connection parameters if not in kwargs
            if "mongo_uri" not in kwargs:
                kwargs["mongo_uri"] = mongo_uri or os.getenv(
                    "MONGODB_URI", "mongodb://localhost:27017"
                )
            if "db_name" not in kwargs:
                kwargs["db_name"] = db_name or os.getenv("MONGODB_DB", "mdb_runtime")
            if "app_slug" not in kwargs:
                kwargs["app_slug"] = actor_app_slug

            return cls.options(
                name=actor_name,
                namespace=actor_namespace,
                lifetime=lifetime,
                max_restarts=max_restarts,
                get_if_exists=True,
            ).remote(*args, **kwargs)

        ray_cls.spawn = spawn

        logger.debug(
            f"Created Ray actor class '{cls.__name__}' "
            f"(app_slug={actor_app_slug}, namespace={actor_namespace})"
        )

        return ray_cls

    return decorator


# Convenience aliases
if RAY_AVAILABLE:
    # Export ray module for convenience
    get_actor = ray.get_actor
    init_ray = ray.init
    is_initialized = ray.is_initialized
else:
    # Stub functions when Ray not available
    def get_actor(*args, **kwargs):
        raise RuntimeError("Ray is not available")

    def init_ray(*args, **kwargs):
        raise RuntimeError("Ray is not available")

    def is_initialized():
        return False
