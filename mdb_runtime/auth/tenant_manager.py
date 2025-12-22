"""
Tenant Management for Multi-Tenant Applications

Provides comprehensive tenant lifecycle management including:
- Automatic tenant creation and validation
- Tenant metadata management
- Integration with sub_auth for seamless provisioning
- Tenant lifecycle hooks for customization
- Tenant existence validation

This module is part of MDB_RUNTIME - MongoDB Multi-Tenant Runtime Engine.
"""
import logging
import asyncio
from typing import Optional, Dict, Any, List, Callable, Awaitable, Set
from datetime import datetime
from fastapi import Request, HTTPException, status

logger = logging.getLogger(__name__)


class TenantManager:
    """
    Manages tenant lifecycle for multi-tenant applications.
    
    Handles:
    - Tenant creation and validation
    - Tenant metadata storage
    - Integration with authentication flows
    - Tenant lifecycle hooks
    """
    
    def __init__(
        self,
        app_slug: str,
        config: Dict[str, Any],
        db,
        engine
    ):
        """
        Initialize tenant manager for an app.
        
        Args:
            app_slug: App slug
            config: Multi-tenant configuration from manifest
            db: Database wrapper (for tenant collection access)
            engine: RuntimeEngine instance (for accessing other apps' data if needed)
        """
        self.app_slug = app_slug
        self.config = config
        self.db = db
        self.engine = engine
        
        # Extract configuration
        self.enabled = config.get("enabled", False)
        self.tenant_collection_name = config.get("tenant_collection", "tenants")
        self.require_tenant = config.get("require_tenant", True)
        self.auto_create = config.get("auto_create_tenant", True)
        self.auto_validate = config.get("auto_validate_tenant", True)
        
        # Tenant lifecycle hooks
        self._before_create_hooks: List[Callable[[str, Dict[str, Any]], Awaitable[Optional[Dict[str, Any]]]]] = []
        self._after_create_hooks: List[Callable[[str, Dict[str, Any]], Awaitable[None]]] = []
        self._before_validate_hooks: List[Callable[[str], Awaitable[bool]]] = []
        
        # Cache for tenant existence (to avoid repeated DB queries)
        self._tenant_cache: Set[str] = set()
        self._cache_lock = asyncio.Lock()
    
    def register_before_create_hook(
        self,
        hook: Callable[[str, Dict[str, Any]], Awaitable[Optional[Dict[str, Any]]]]
    ):
        """
        Register a hook to be called before tenant creation.
        
        Hook signature: async def hook(tenant_id: str, metadata: Dict) -> Optional[Dict]
        - If hook returns None, tenant creation is aborted
        - If hook returns a dict, it's merged into tenant metadata before creation
        
        Args:
            hook: Async function that receives tenant_id and metadata, returns modified metadata or None
        """
        self._before_create_hooks.append(hook)
        logger.debug(f"Registered before_create hook for app '{self.app_slug}'")
    
    def register_after_create_hook(
        self,
        hook: Callable[[str, Dict[str, Any]], Awaitable[None]]
    ):
        """
        Register a hook to be called after tenant creation.
        
        Hook signature: async def hook(tenant_id: str, tenant_doc: Dict) -> None
        
        Args:
            hook: Async function that receives tenant_id and created tenant document
        """
        self._after_create_hooks.append(hook)
        logger.debug(f"Registered after_create hook for app '{self.app_slug}'")
    
    def register_before_validate_hook(
        self,
        hook: Callable[[str], Awaitable[bool]]
    ):
        """
        Register a hook to customize tenant validation.
        
        Hook signature: async def hook(tenant_id: str) -> bool
        - Return True if tenant is valid, False otherwise
        
        Args:
            hook: Async function that validates tenant_id
        """
        self._before_validate_hooks.append(hook)
        logger.debug(f"Registered before_validate hook for app '{self.app_slug}'")
    
    async def ensure_tenant_exists(
        self,
        tenant_id: str,
        metadata: Optional[Dict[str, Any]] = None,
        request: Optional[Request] = None
    ) -> Dict[str, Any]:
        """
        Ensure a tenant exists, creating it if necessary.
        
        This is the main entry point for tenant lifecycle management.
        It:
        1. Validates tenant_id format
        2. Checks if tenant exists
        3. Creates tenant if it doesn't exist (if auto_create is enabled)
        4. Returns tenant document
        
        Args:
            tenant_id: Tenant identifier
            metadata: Optional metadata to include in tenant document
            request: Optional request object (for hooks that need request context)
        
        Returns:
            Tenant document dictionary
        
        Raises:
            HTTPException: If tenant_id is invalid or creation fails
        """
        if not self.enabled:
            raise ValueError(f"Multi-tenant is not enabled for app '{self.app_slug}'")
        
        # Validate tenant_id format
        if not tenant_id or not isinstance(tenant_id, str):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="tenant_id must be a non-empty string"
            )
        
        # Sanitize tenant_id (basic validation)
        tenant_id = tenant_id.strip().lower()
        if not tenant_id or len(tenant_id) > 100:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="tenant_id must be between 1 and 100 characters"
            )
        
        # Check if tenant exists
        tenant = await self.get_tenant(tenant_id)
        if tenant:
            return tenant
        
        # Tenant doesn't exist - create if auto_create is enabled
        if not self.auto_create:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tenant '{tenant_id}' not found and auto-creation is disabled"
            )
        
        # Create tenant
        return await self.create_tenant(tenant_id, metadata, request)
    
    async def get_tenant(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        """
        Get tenant document by tenant_id.
        
        Args:
            tenant_id: Tenant identifier
        
        Returns:
            Tenant document or None if not found
        """
        if not self.enabled:
            return None
        
        # Check cache first
        async with self._cache_lock:
            if tenant_id not in self._tenant_cache:
                # Not in cache, check database
                collection = getattr(self.db, self.tenant_collection_name)
                tenant = await collection.find_one({"tenant_id": tenant_id})
                
                if tenant:
                    # Add to cache
                    self._tenant_cache.add(tenant_id)
                    return tenant
                return None
            else:
                # In cache, fetch from database
                collection = getattr(self.db, self.tenant_collection_name)
                return await collection.find_one({"tenant_id": tenant_id})
    
    async def create_tenant(
        self,
        tenant_id: str,
        metadata: Optional[Dict[str, Any]] = None,
        request: Optional[Request] = None
    ) -> Dict[str, Any]:
        """
        Create a new tenant.
        
        Args:
            tenant_id: Tenant identifier
            metadata: Optional metadata to include in tenant document
            request: Optional request object (for hooks)
        
        Returns:
            Created tenant document
        
        Raises:
            HTTPException: If tenant creation fails
        """
        if not self.enabled:
            raise ValueError(f"Multi-tenant is not enabled for app '{self.app_slug}'")
        
        # Check if tenant already exists
        existing = await self.get_tenant(tenant_id)
        if existing:
            logger.debug(f"Tenant '{tenant_id}' already exists for app '{self.app_slug}'")
            return existing
        
        # Prepare tenant document
        tenant_doc: Dict[str, Any] = {
            "tenant_id": tenant_id,
            "app_slug": self.app_slug,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
            "status": "active"
        }
        
        # Add metadata if provided
        if metadata:
            tenant_doc.update(metadata)
        
        # Run before_create hooks
        for hook in self._before_create_hooks:
            try:
                result = await hook(tenant_id, tenant_doc.copy(), request)
                if result is None:
                    # Hook aborted creation
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Tenant creation aborted by hook for '{tenant_id}'"
                    )
                # Merge hook result into tenant_doc
                if isinstance(result, dict):
                    tenant_doc.update(result)
            except HTTPException:
                raise
            except Exception as e:
                logger.error(
                    f"Error in before_create hook for tenant '{tenant_id}': {e}",
                    exc_info=True
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Error in tenant creation hook: {str(e)}"
                )
        
        # Insert tenant document
        try:
            collection = getattr(self.db, self.tenant_collection_name)
            result = await collection.insert_one(tenant_doc)
            tenant_doc["_id"] = result.inserted_id
            
            # Add to cache
            async with self._cache_lock:
                self._tenant_cache.add(tenant_id)
            
            logger.info(f"Created tenant '{tenant_id}' for app '{self.app_slug}'")
            
            # Run after_create hooks
            for hook in self._after_create_hooks:
                try:
                    await hook(tenant_id, tenant_doc.copy(), request)
                except Exception as e:
                    logger.error(
                        f"Error in after_create hook for tenant '{tenant_id}': {e}",
                        exc_info=True
                    )
                    # Don't fail tenant creation if after_create hook fails
            
            return tenant_doc
            
        except Exception as e:
            logger.error(
                f"Error creating tenant '{tenant_id}' for app '{self.app_slug}': {e}",
                exc_info=True
            )
            # Check if it's a duplicate key error (race condition)
            if "duplicate" in str(e).lower() or "E11000" in str(e):
                # Tenant was created by another request, fetch it
                existing = await self.get_tenant(tenant_id)
                if existing:
                    return existing
            
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create tenant: {str(e)}"
            )
    
    async def validate_tenant(
        self,
        tenant_id: str,
        request: Optional[Request] = None
    ) -> bool:
        """
        Validate that a tenant exists and is active.
        
        Args:
            tenant_id: Tenant identifier
            request: Optional request object (for hooks)
        
        Returns:
            True if tenant is valid, False otherwise
        """
        if not self.enabled:
            return True  # If multi-tenant is disabled, validation passes
        
        if not tenant_id:
            return False
        
        # Run before_validate hooks
        for hook in self._before_validate_hooks:
            try:
                is_valid = await hook(tenant_id, request)
                if not is_valid:
                    return False
            except Exception as e:
                logger.error(
                    f"Error in before_validate hook for tenant '{tenant_id}': {e}",
                    exc_info=True
                )
                # Continue with default validation if hook fails
        
        # Default validation: check if tenant exists and is active
        tenant = await self.get_tenant(tenant_id)
        if not tenant:
            return False
        
        # Check if tenant is active
        status_value = tenant.get("status", "active")
        return status_value == "active"
    
    async def update_tenant_metadata(
        self,
        tenant_id: str,
        metadata: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """
        Update tenant metadata.
        
        Args:
            tenant_id: Tenant identifier
            metadata: Metadata to update
        
        Returns:
            Updated tenant document or None if tenant not found
        """
        if not self.enabled:
            return None
        
        collection = getattr(self.db, self.tenant_collection_name)
        
        # Prepare update document
        update_doc = {
            "$set": {
                **metadata,
                "updated_at": datetime.utcnow()
            }
        }
        
        result = await collection.find_one_and_update(
            {"tenant_id": tenant_id},
            update_doc,
            return_document=True
        )
        
        if result:
            logger.debug(f"Updated metadata for tenant '{tenant_id}' in app '{self.app_slug}'")
        
        return result
    
    async def list_tenants(
        self,
        limit: int = 100,
        skip: int = 0,
        filter_query: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        List tenants for this app.
        
        Args:
            limit: Maximum number of tenants to return
            skip: Number of tenants to skip
            filter_query: Optional MongoDB filter query
        
        Returns:
            List of tenant documents
        """
        if not self.enabled:
            return []
        
        collection = getattr(self.db, self.tenant_collection_name)
        
        query = {"app_slug": self.app_slug}
        if filter_query:
            query.update(filter_query)
        
        cursor = collection.find(query).skip(skip).limit(limit)
        tenants = await cursor.to_list(length=limit)
        
        return tenants
    
    def invalidate_cache(self, tenant_id: Optional[str] = None):
        """
        Invalidate tenant cache.
        
        Args:
            tenant_id: Specific tenant_id to remove from cache, or None to clear all
        """
        async with self._cache_lock:
            if tenant_id:
                self._tenant_cache.discard(tenant_id)
            else:
                self._tenant_cache.clear()


# Global registry for tenant managers per app
_tenant_managers: Dict[str, TenantManager] = {}


def register_tenant_manager(
    app_slug: str,
    config: Dict[str, Any],
    db,
    engine
) -> TenantManager:
    """
    Register a tenant manager for an app.
    
    Args:
        app_slug: App slug
        config: Multi-tenant configuration from manifest
        db: Database wrapper
        engine: RuntimeEngine instance
    
    Returns:
        TenantManager instance
    """
    manager = TenantManager(app_slug, config, db, engine)
    _tenant_managers[app_slug] = manager
    logger.info(f"Registered tenant manager for app '{app_slug}'")
    return manager


def get_tenant_manager(app_slug: str) -> Optional[TenantManager]:
    """
    Get tenant manager for an app.
    
    Args:
        app_slug: App slug
    
    Returns:
        TenantManager instance or None if not registered
    """
    return _tenant_managers.get(app_slug)


async def ensure_tenant_for_request(
    request: Request,
    app_slug: str,
    tenant_id: Optional[str] = None,
    auto_create: bool = True
) -> Optional[str]:
    """
    Ensure tenant exists for a request, creating it if necessary.
    
    This is a convenience function that:
    1. Resolves tenant_id from request if not provided
    2. Validates tenant exists
    3. Creates tenant if it doesn't exist (if auto_create is True)
    4. Returns tenant_id
    
    Args:
        request: FastAPI Request object
        app_slug: App slug
        tenant_id: Optional tenant_id (if not provided, resolves from request)
        auto_create: Whether to auto-create tenant if it doesn't exist
    
    Returns:
        Tenant ID or None if tenant is not required/available
    """
    from .tenant_resolution import get_tenant_id
    
    # Resolve tenant_id if not provided
    if not tenant_id:
        tenant_id = await get_tenant_id(request, app_slug)
    
    if not tenant_id:
        # Check if tenant is required
        manager = get_tenant_manager(app_slug)
        if manager and manager.require_tenant:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="tenant_id is required but not found in request"
            )
        return None
    
    # Get tenant manager
    manager = get_tenant_manager(app_slug)
    if not manager:
        # Multi-tenant not enabled or manager not registered
        return tenant_id
    
    # Ensure tenant exists
    if auto_create:
        try:
            await manager.ensure_tenant_exists(tenant_id, request=request)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error ensuring tenant exists: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error ensuring tenant exists: {str(e)}"
            )
    else:
        # Just validate
        is_valid = await manager.validate_tenant(tenant_id, request)
        if not is_valid:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tenant '{tenant_id}' not found or inactive"
            )
    
    return tenant_id


async def ensure_tenant_for_user_operation(
    request: Request,
    app_slug: str,
    user_data: Optional[Dict[str, Any]] = None
) -> Optional[str]:
    """
    Ensure tenant exists for user operations (signin/login/register).
    
    This is a specialized function for authentication flows that:
    1. Resolves tenant_id from request or user data
    2. Automatically creates tenant if it doesn't exist
    3. Enriches tenant metadata with user information if available
    
    This function is designed to be called during signin/login/register
    operations to seamlessly provision tenants on-demand.
    
    Args:
        request: FastAPI Request object
        app_slug: App slug
        user_data: Optional user data (email, name, etc.) to include in tenant metadata
    
    Returns:
        Tenant ID or None if tenant is not required/available
    """
    from .tenant_resolution import get_tenant_id
    
    # Resolve tenant_id from request or user data
    tenant_id = await get_tenant_id(request, app_slug, user=user_data)
    
    if not tenant_id:
        # Check if tenant is required
        manager = get_tenant_manager(app_slug)
        if manager and manager.require_tenant:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="tenant_id is required but not found in request"
            )
        return None
    
    # Get tenant manager
    manager = get_tenant_manager(app_slug)
    if not manager:
        # Multi-tenant not enabled or manager not registered
        return tenant_id
    
    # Prepare tenant metadata from user data
    tenant_metadata = {}
    if user_data:
        # Extract relevant user information for tenant metadata
        if "email" in user_data:
            tenant_metadata["created_by_email"] = user_data["email"]
        if "name" in user_data:
            tenant_metadata["created_by_name"] = user_data["name"]
        if "user_id" in user_data:
            tenant_metadata["created_by_user_id"] = str(user_data["user_id"])
    
    # Ensure tenant exists (will create if needed)
    try:
        await manager.ensure_tenant_exists(tenant_id, metadata=tenant_metadata, request=request)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error ensuring tenant exists for user operation: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error ensuring tenant exists: {str(e)}"
        )
    
    return tenant_id

