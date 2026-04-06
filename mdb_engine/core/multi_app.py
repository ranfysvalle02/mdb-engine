"""
Multi-App Mixin

Provides methods for creating multi-app FastAPI deployments, app discovery,
manifest validation, route importing, and shared user pool initialization.
"""

import asyncio
import importlib.util
import json
import logging
import os
import sys
import time
import warnings
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from fastapi import FastAPI

    from .connection import ConnectionManager
    from .engine import MongoDBEngine

try:
    from openai import OpenAIError
except ImportError:

    class OpenAIError(Exception):  # type: ignore[no-redef]
        """Placeholder when openai is not installed — will never match engine errors."""


logger = logging.getLogger(__name__)


def _read_json_file(path: str | Path) -> dict:
    """Read and parse a JSON file (sync). Intended to be called via asyncio.to_thread()."""
    with open(path) as f:
        return json.load(f)


class MultiAppMixin:
    """Mixin providing multi-app deployment and shared user pool management.

    Expects the following attributes from the host class (MongoDBEngine):
        _connection_manager: Database connection manager.
        _websocket_ticket_store: WebSocket ticket store instance (optional).
        _websocket_session_manager: WebSocket session manager instance (optional).
        _shared_user_pool_lock: Async lock for thread-safe shared user pool init.
        _shared_user_pool_initializing: Flag indicating shared pool init in progress.
    """

    # -- Attributes provided by MongoDBEngine --
    _connection_manager: "ConnectionManager"
    _websocket_ticket_store: Any
    _websocket_session_manager: Any
    _shared_user_pool_lock: "asyncio.Lock"
    _shared_user_pool_initializing: bool

    def _validate_path_prefixes(self, apps: list[dict[str, Any]]) -> tuple[bool, list[str]]:
        """
        Validate path prefixes for multi-app mounting.

        Checks:
        - All prefixes start with '/'
        - No prefix is a prefix of another (e.g., '/app' conflicts with '/app/v2')
        - No conflicts with reserved paths ('/health', '/docs', '/openapi.json', '/_mdb')
        - Slug matches manifest slug (if manifest is readable)

        Args:
            apps: List of app configs with 'path_prefix' keys

        Returns:
            Tuple of (is_valid, list_of_errors)
        """

        errors: list[str] = []
        reserved_paths = {"/health", "/docs", "/openapi.json", "/redoc", "/_mdb"}

        # Extract path prefixes
        path_prefixes: list[str] = []
        for app_config in apps:
            slug = app_config.get("slug", "unknown")
            path_prefix = app_config.get("path_prefix", f"/{slug}")

            if not path_prefix.startswith("/"):
                errors.append(f"Path prefix '{path_prefix}' must start with '/' (app: '{slug}')")
                continue

            # Check for common mistakes
            if path_prefix.endswith("/") and path_prefix != "/":
                logger.warning(
                    f"Path prefix '{path_prefix}' ends with '/'. " f"Consider removing trailing slash for app '{slug}'"
                )

            path_prefixes.append(path_prefix)

        # Check for conflicts with reserved paths
        for prefix in path_prefixes:
            if prefix in reserved_paths:
                errors.append(
                    f"Path prefix '{prefix}' conflicts with reserved path. "
                    "Reserved paths: /health, /docs, /openapi.json, /redoc, /_mdb"
                )

        # Check for prefix conflicts (one prefix being a prefix of another)
        path_prefixes_sorted = sorted(path_prefixes)
        for i, prefix1 in enumerate(path_prefixes_sorted):
            for prefix2 in path_prefixes_sorted[i + 1 :]:
                # Normalize by ensuring both end with / for comparison
                p1_norm = prefix1 if prefix1.endswith("/") else prefix1 + "/"
                p2_norm = prefix2 if prefix2.endswith("/") else prefix2 + "/"

                if p1_norm.startswith(p2_norm) or p2_norm.startswith(p1_norm):
                    # Find which apps these belong to for better error message
                    app1_slug = next(
                        (a.get("slug", "unknown") for a in apps if a.get("path_prefix") == prefix1),
                        "unknown",
                    )
                    app2_slug = next(
                        (a.get("slug", "unknown") for a in apps if a.get("path_prefix") == prefix2),
                        "unknown",
                    )
                    errors.append(
                        f"Path prefix conflict: '{prefix1}' (app: '{app1_slug}') and "
                        f"'{prefix2}' (app: '{app2_slug}') overlap. "
                        "One cannot be a prefix of another."
                    )

        # Check for duplicates
        if len(path_prefixes) != len(set(path_prefixes)):
            seen = {}
            for app_config in apps:
                prefix = app_config.get("path_prefix")
                slug = app_config.get("slug", "unknown")
                if prefix in seen:
                    first_slug = seen[prefix]
                    errors.append(f"Duplicate path prefix: '{prefix}' used by both " f"'{first_slug}' and '{slug}'")
                else:
                    seen[prefix] = slug

        return len(errors) == 0, errors

    def _discover_apps_from_directory(
        self,
        apps_dir: Path,
        path_prefix_template: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Auto-discover apps by scanning directory for manifest.json files.

        Args:
            apps_dir: Directory to scan for apps
            path_prefix_template: Template for path prefixes (e.g., "/app-{index}")

        Returns:
            List of app configurations
        """
        apps_dir = Path(apps_dir)
        if not apps_dir.exists():
            raise ValueError(f"Apps directory does not exist: {apps_dir}")

        discovered_apps = []
        manifest_files = list(apps_dir.rglob("manifest.json"))

        if not manifest_files:
            raise ValueError(f"No manifest.json files found in {apps_dir}")

        for idx, manifest_path in enumerate(sorted(manifest_files), start=1):
            try:
                with open(manifest_path) as f:
                    manifest_data = json.load(f)

                slug = manifest_data.get("slug")
                if not slug:
                    logger.warning(f"Skipping manifest without slug: {manifest_path}")
                    continue

                # Generate path prefix
                if path_prefix_template:
                    path_prefix = path_prefix_template.format(index=idx, slug=slug)
                else:
                    path_prefix = f"/{slug}"

                discovered_apps.append(
                    {
                        "slug": slug,
                        "manifest": manifest_path,
                        "path_prefix": path_prefix,
                    }
                )
                logger.info(f"Discovered app '{slug}' at {manifest_path} " f"(will mount at {path_prefix})")
            except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Failed to read manifest at {manifest_path}: {e}")
                continue

        if not discovered_apps:
            raise ValueError(f"No valid apps discovered in {apps_dir}")

        return discovered_apps

    def _validate_manifests_sync(self, apps: list[dict[str, Any]], strict: bool) -> list[str]:
        """Validate all app manifests synchronously (for use in sync create_multi_app)."""
        from jsonschema import ValidationError as JValidationError
        from jsonschema import validate as jvalidate

        from .manifest import get_schema_for_version, get_schema_version

        logger.info("Validating all manifests before mounting...")
        validation_errors = []
        for app_config in apps:
            slug = app_config.get("slug", "unknown")
            manifest_path = app_config.get("manifest")
            try:
                manifest_data = _read_json_file(manifest_path)

                version = get_schema_version(manifest_data)
                schema = get_schema_for_version(version)
                jvalidate(instance=manifest_data, schema=schema)
                is_valid, error_msg, error_paths = True, None, None
            except JValidationError as e:
                is_valid = False
                error_msg = e.message
                error_paths = [".".join(str(p) for p in e.absolute_path)] if e.absolute_path else ["root"]

                if not is_valid:
                    error_detail = f"App '{slug}' at {manifest_path}: {error_msg}"
                    if error_paths:
                        error_detail += f" (paths: {', '.join(error_paths)})"
                    validation_errors.append(error_detail)
                    if strict:
                        raise ValueError(f"Manifest validation failed for app '{slug}': {error_msg}") from None

                # Validate slug matches manifest slug
                manifest_slug = manifest_data.get("slug")
                if manifest_slug and manifest_slug != slug:
                    error_msg = (
                        f"Slug mismatch: config slug '{slug}' does not match "
                        f"manifest slug '{manifest_slug}' in {manifest_path}"
                    )
                    validation_errors.append(error_msg)
                    if strict:
                        raise ValueError(error_msg) from None
            except FileNotFoundError as e:
                error_msg = f"Manifest file not found for app '{slug}': {manifest_path}"
                validation_errors.append(error_msg)
                if strict:
                    raise ValueError(error_msg) from e
            except json.JSONDecodeError as e:
                error_msg = f"Invalid JSON in manifest for app '{slug}' at {manifest_path}: {e}"
                validation_errors.append(error_msg)
                if strict:
                    raise ValueError(error_msg) from e

        return validation_errors

    async def _validate_manifests(self, apps: list[dict[str, Any]], strict: bool) -> list[str]:
        """Validate all app manifests."""
        logger.info("Validating all manifests before mounting...")
        validation_errors = []
        for app_config in apps:
            slug = app_config.get("slug", "unknown")
            manifest_path = app_config.get("manifest")
            try:
                manifest_data = await asyncio.to_thread(_read_json_file, manifest_path)

                # Validate manifest
                from .manifest import validate_manifest

                is_valid, error_msg, error_paths = await validate_manifest(manifest_data)

                if not is_valid:
                    error_detail = f"App '{slug}' at {manifest_path}: {error_msg}"
                    if error_paths:
                        error_detail += f" (paths: {', '.join(error_paths)})"
                    validation_errors.append(error_detail)
                    if strict:
                        raise ValueError(f"Manifest validation failed for app '{slug}': {error_msg}") from None

                # Validate slug matches manifest slug
                manifest_slug = manifest_data.get("slug")
                if manifest_slug and manifest_slug != slug:
                    error_msg = (
                        f"Slug mismatch: config slug '{slug}' does not match "
                        f"manifest slug '{manifest_slug}' in {manifest_path}"
                    )
                    validation_errors.append(error_msg)
                    if strict:
                        raise ValueError(error_msg) from None
            except FileNotFoundError as e:
                error_msg = f"Manifest file not found for app '{slug}': {manifest_path}"
                validation_errors.append(error_msg)
                if strict:
                    raise ValueError(error_msg) from e
            except json.JSONDecodeError as e:
                error_msg = f"Invalid JSON in manifest for app '{slug}' at {manifest_path}: {e}"
                validation_errors.append(error_msg)
                if strict:
                    raise ValueError(error_msg) from e

        return validation_errors

    def _import_app_routes(self, child_app: "FastAPI", manifest_path: Path, slug: str) -> Any:
        """
        Automatically discover and import route modules for a child app.

        This method looks for route modules (web.py, routes.py) in the same directory
        as the manifest and imports them so that route decorators are executed and
        routes are registered on the child app.

        Args:
            child_app: The FastAPI child app to register routes on
            manifest_path: Path to the manifest.json file
            slug: App slug for logging

        The method tries multiple strategies:
        1. Look for 'web.py' in the manifest directory
        2. Look for 'routes.py' in the manifest directory
        3. Check manifest for explicit 'routes_module' field (future support)

        When importing, the method ensures that route decorators in the imported module
        reference the child_app by temporarily injecting it into the module namespace.
        """
        manifest_dir = manifest_path.parent

        # Try to find route modules in order of preference
        route_module_paths = [
            manifest_dir / "web.py",
            manifest_dir / "routes.py",
        ]

        # Also check for routes_module in manifest (future support)
        try:
            with open(manifest_path) as f:
                manifest_data = json.load(f)
            routes_module = manifest_data.get("routes_module")
            if routes_module:
                # Support both relative (to manifest dir) and absolute paths
                if routes_module.startswith("/"):
                    route_module_paths.insert(0, Path(routes_module))
                else:
                    route_module_paths.insert(0, manifest_dir / routes_module)
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            pass

        imported = False
        module_name = None
        route_module = None
        manifest_dir_str = None
        path_inserted = False

        for route_module_path in route_module_paths:
            if not route_module_path.exists():
                continue

            # Create a unique module name to avoid conflicts
            module_name = f"mdb_engine_imported_routes_{slug}_{id(child_app)}"

            try:
                # Validate file is actually a Python file
                if not route_module_path.suffix == ".py":
                    logger.debug(f"Skipping non-Python file '{route_module_path}' for app '{slug}'")
                    continue

                # Load the module spec
                spec = importlib.util.spec_from_file_location(module_name, route_module_path)
                if spec is None or spec.loader is None:
                    logger.warning(f"Could not create spec for route module '{route_module_path}' " f"for app '{slug}'")
                    continue

                route_module = importlib.util.module_from_spec(spec)

                # CRITICAL: Inject child_app into module namespace BEFORE loading
                # This ensures that @app.get(), @app.post(), etc. decorators in the
                # imported module will reference our child_app instead of creating a new one
                route_module.app = child_app
                route_module.engine = self  # Also provide engine reference for dependencies

                # Add to sys.modules temporarily to handle relative imports
                # Use a try-finally to ensure cleanup even on exceptions
                sys.modules[module_name] = route_module

                # Store route count before import
                routes_before = len(child_app.routes)

                # Add manifest directory to Python path temporarily for relative imports
                # This allows route modules to import sibling modules
                manifest_dir_str = str(manifest_dir.resolve())
                path_inserted = manifest_dir_str not in sys.path
                if path_inserted:
                    sys.path.insert(0, manifest_dir_str)

                try:
                    # Execute the module (runs route decorators with injected app)
                    spec.loader.exec_module(route_module)
                except SyntaxError as e:
                    logger.warning(
                        f"Syntax error in route module '{route_module_path}' "
                        f"for app '{slug}': {e}. Skipping this module."
                    )
                    continue
                except ImportError as e:
                    # ImportError might be due to missing dependencies - log but don't fail
                    logger.debug(
                        f"Import error in route module '{route_module_path}' "
                        f"for app '{slug}': {e}. "
                        "This may be OK if dependencies are optional."
                    )
                    # Check if it's a critical import (like FastAPI) vs optional dependency
                    error_str = str(e).lower()
                    if "fastapi" in error_str or "starlette" in error_str:
                        logger.warning(
                            f"Route module '{route_module_path}' for app '{slug}' "
                            "requires FastAPI/Starlette but they're not available. "
                            "Routes will not be registered."
                        )
                    continue
                finally:
                    # Remove from path only if we added it
                    if path_inserted and manifest_dir_str and manifest_dir_str in sys.path:
                        try:
                            sys.path.remove(manifest_dir_str)
                        except ValueError:
                            # Path might have been removed already - ignore
                            pass

                # Check if module overwrote app (shouldn't happen in well-structured modules)
                module_app = getattr(route_module, "app", None)
                if module_app is not None and module_app is not child_app:
                    warning_msg = (
                        f"Route module '{route_module_path.name}' for app '{slug}' "
                        "created its own app instance. Routes defined before app creation "
                        "are registered, but routes defined after may not be. "
                        "Consider restructuring the module to use the injected 'app' variable."
                    )
                    logger.warning(warning_msg)
                    warnings.warn(warning_msg, UserWarning, stacklevel=2)

                routes_after = len(child_app.routes)
                routes_added = routes_after - routes_before

                if routes_added > 0:
                    logger.info(
                        f"Auto-imported routes from '{route_module_path.name}' "
                        f"for app '{slug}'. Added {routes_added} route(s) "
                        f"(total: {routes_after})"
                    )
                else:
                    logger.debug(
                        f"Route module '{route_module_path.name}' for app '{slug}' "
                        "was imported but no new routes were registered. "
                        "This may be expected if routes are registered conditionally."
                    )

                imported = True
                # Return the module so caller can detect on_startup
                _returned_module = route_module
                break

            except (ValueError, TypeError, AttributeError, RuntimeError, OSError) as e:
                logger.warning(
                    f"Unexpected error importing route module '{route_module_path}' " f"for app '{slug}': {e}",
                    exc_info=True,
                )
                continue
            finally:
                # Clean up temporary module from sys.modules
                if module_name and module_name in sys.modules:
                    try:
                        del sys.modules[module_name]
                    except KeyError:
                        # Already removed - ignore
                        pass
                # Ensure path is cleaned up even if exception occurred
                if path_inserted and manifest_dir_str and manifest_dir_str in sys.path:
                    try:
                        sys.path.remove(manifest_dir_str)
                    except ValueError:
                        pass

        if not imported:
            logger.debug(
                f"No route modules found for app '{slug}' in {manifest_dir}. "
                "Routes may be defined elsewhere or app may not have HTTP routes."
            )
            return None

        return _returned_module if imported else None

    def create_multi_app(  # noqa: C901
        self,
        apps: list[dict[str, Any]] | None = None,
        multi_app_manifest: Path | None = None,
        apps_dir: Path | None = None,
        path_prefix_template: str | None = None,
        validate: bool = False,
        strict: bool = False,
        title: str = "Multi-App API",
        root_path: str = "",
        **fastapi_kwargs: Any,
    ) -> "FastAPI":
        """
        Create a parent FastAPI app that mounts multiple child apps.

        Each child app is mounted at a path prefix (e.g., /auth-hub, /pwd-zero) and
        maintains its own routes, middleware, and state while sharing the engine instance.

        Args:
            apps: List of app configurations. Each dict should have:
                - slug: App slug (required)
                - manifest: Path to manifest.json (required)
                - path_prefix: Optional path prefix (defaults to /{slug})
                - on_startup: Optional startup callback function
                - on_shutdown: Optional shutdown callback function
            multi_app_manifest: Path to a multi-app manifest.json that defines all apps.
                Format:
                {
                    "multi_app": {
                        "enabled": true,
                        "apps": [
                            {
                                "slug": "app1",
                                "manifest": "./app1/manifest.json",
                                "path_prefix": "/app1",
                            }
                        ]
                    }
                }
            apps_dir: Directory to scan for apps (auto-discovery). If provided and
                apps is None, will recursively scan for manifest.json files and
                auto-discover apps. Takes precedence over multi_app_manifest.
            path_prefix_template: Template for auto-generated path prefixes when using
                apps_dir. Use {index} for app index and {slug} for app slug.
                Example: "/app-{index}" or "/{slug}"
            validate: If True, validate all manifests before mounting (default: False)
            strict: If True, fail fast on any validation error (default: False).
                Only used when validate=True.
            title: Title for the parent FastAPI app
            root_path: Root path prefix for all mounted apps (optional)
            **fastapi_kwargs: Additional arguments passed to FastAPI()

        Returns:
            Parent FastAPI application with all child apps mounted

        Raises:
            ValueError: If configuration is invalid or path prefixes conflict
            RuntimeError: If engine is not initialized

        Features:
            - Built-in app context helpers: Each mounted app has access to:
              - request.state.app_base_path: Path prefix (e.g., "/app-1")
              - request.state.auth_hub_url: Auth hub URL from manifest or env
              - request.state.app_slug: App slug
              - request.state.mounted_apps: Dict of all mounted apps with paths
              - request.state.engine: MongoDBEngine instance
              - request.state.manifest: App's manifest.json
            - Unified health check: GET /health aggregates health from all apps
            - Route introspection: GET /_mdb/routes lists all routes from all apps
            - OpenAPI aggregation: /docs combines docs from all apps
            - Per-app docs: /docs/{app_slug} for individual app documentation

        Example:
            # Programmatic approach
            engine = MongoDBEngine(mongo_uri=..., db_name=...)
            app = engine.create_multi_app(
                apps=[
                    {
                        "slug": "auth-hub",
                        "manifest": Path("./auth-hub/manifest.json"),
                        "path_prefix": "/auth-hub",
                    },
                    {
                        "slug": "pwd-zero",
                        "manifest": Path("./pwd-zero/manifest.json"),
                        "path_prefix": "/pwd-zero",
                    },
                ]
            )

            # Manifest-based approach
            app = engine.create_multi_app(
                multi_app_manifest=Path("./multi_app_manifest.json")
            )

            # Auto-discovery approach
            app = engine.create_multi_app(
                apps_dir=Path("./apps"),
                path_prefix_template="/app-{index}",
                validate=True,
            )

            # Access app context in routes
            @app.get("/my-route")
            async def my_route(request: Request):
                base_path = request.state.app_base_path  # "/app-1"
                auth_url = request.state.auth_hub_url    # "/auth-hub"
                slug = request.state.app_slug            # "my-app"
                all_apps = request.state.mounted_apps     # Dict of all apps
        """
        from fastapi import FastAPI

        engine = self

        # Auto-discovery: if apps_dir is provided and apps is None, discover apps
        if apps_dir and apps is None:
            logger.info(f"Auto-discovering apps from directory: {apps_dir}")
            apps = self._discover_apps_from_directory(
                apps_dir=apps_dir,
                path_prefix_template=path_prefix_template,
            )

        # Load configuration from manifest or apps parameter
        if multi_app_manifest:
            manifest_path = Path(multi_app_manifest)
            multi_app_config = _read_json_file(manifest_path)

            multi_app_section = multi_app_config.get("multi_app", {})
            if not multi_app_section.get("enabled", False):
                raise ValueError("multi_app.enabled must be True in multi_app_manifest to use multi-app mode")

            apps_config = multi_app_section.get("apps", [])
            if not apps_config:
                raise ValueError("multi_app.apps must contain at least one app")

            # Resolve manifest paths relative to multi_app_manifest location
            manifest_dir = manifest_path.parent
            apps = []
            for app_config in apps_config:
                manifest_rel_path = app_config.get("manifest")
                if not manifest_rel_path:
                    raise ValueError(f"App '{app_config.get('slug')}' missing 'manifest' field")

                # Resolve relative to multi_app_manifest location
                manifest_full_path = (manifest_dir / manifest_rel_path).resolve()
                slug = app_config.get("slug")
                path_prefix = app_config.get("path_prefix", f"/{slug}")

                apps.append(
                    {
                        "slug": slug,
                        "manifest": manifest_full_path,
                        "path_prefix": path_prefix,
                    }
                )

        elif apps is not None:
            apps_config = apps
            # Convert Path objects to Path if they're strings
            apps = []
            for app_config in apps_config:
                manifest = app_config.get("manifest")
                if isinstance(manifest, str):
                    manifest = Path(manifest)
                apps.append(
                    {
                        "slug": app_config.get("slug"),
                        "manifest": manifest,
                        "path_prefix": app_config.get("path_prefix", f"/{app_config.get('slug')}"),
                        "on_startup": app_config.get("on_startup"),
                        "on_shutdown": app_config.get("on_shutdown"),
                    }
                )
        else:
            raise ValueError("Either 'apps', 'multi_app_manifest', or 'apps_dir' must be provided")

        if not apps:
            raise ValueError("At least one app must be configured")

        # Validate manifests if requested (basic sync validation here,
        # full async validation deferred to lifespan)
        if validate:
            validation_errors = self._validate_manifests_sync(apps, strict)
            if validation_errors:
                logger.warning("Manifest validation found issues:\n" + "\n".join(f"  - {e}" for e in validation_errors))
                if strict:
                    raise ValueError(
                        "Manifest validation failed (strict mode):\n" + "\n".join(f"  - {e}" for e in validation_errors)
                    )

        # Validate path prefixes (enhanced)
        is_valid, errors = self._validate_path_prefixes(apps)
        if not is_valid:
            raise ValueError("Path prefix validation failed:\n" + "\n".join(f"  - {e}" for e in errors))

        # Check if any app uses shared auth and collect public routes for CSRF exemption
        # Also collect ticket TTL values from websocket configs
        has_shared_auth = False
        all_public_routes = [
            "/health",
            "/docs",
            "/openapi.json",
            "/_mdb/routes",
        ]  # Base exempt routes
        ticket_ttl_values: list[int] = []  # Collect ticket TTLs from all apps
        for app_config in apps:
            try:
                manifest_path = app_config["manifest"]
                path_prefix = app_config.get("path_prefix", f"/{app_config.get('slug')}")
                app_manifest_pre = _read_json_file(manifest_path)
                auth_config = app_manifest_pre.get("auth", {})
                if auth_config.get("mode") == "shared":
                    has_shared_auth = True
                # Collect public routes with path prefix for CSRF exemption
                child_public_routes = list(auth_config.get("public_routes", []))
                # Auto-add SSO routes for shared-auth apps
                if auth_config.get("mode") == "shared":
                    for _sso_route in ["/auth/callback", "/logout"]:
                        if _sso_route not in child_public_routes:
                            child_public_routes.append(_sso_route)
                for route in child_public_routes:
                    # Add path prefix to make route absolute on parent app
                    if route.startswith("/"):
                        prefixed_route = f"{path_prefix.rstrip('/')}{route}"
                    else:
                        prefixed_route = f"{path_prefix.rstrip('/')}/{route}"
                    if prefixed_route not in all_public_routes:
                        all_public_routes.append(prefixed_route)

                # Collect ticket TTL from websocket configs
                websockets_config = app_manifest_pre.get("websockets", {})
                if websockets_config:
                    for endpoint_config in websockets_config.values():
                        if isinstance(endpoint_config, dict):
                            ticket_ttl = endpoint_config.get("ticket_ttl_seconds")
                            if ticket_ttl is not None:
                                ticket_ttl_values.append(ticket_ttl)
            except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
                logger.warning(f"Could not check auth mode for app '{app_config.get('slug')}': {e}")

        # Determine ticket TTL: use minimum from app configs (most secure), or default
        from ..auth.websocket_tickets import DEFAULT_TICKET_TTL_SECONDS

        if ticket_ttl_values:
            configured_ticket_ttl = min(ticket_ttl_values)  # Use minimum for maximum security
            logger.info(
                f"Ticket TTL configured from app manifests: {configured_ticket_ttl}s "
                f"(found values: {ticket_ttl_values}, using minimum)"
            )
        else:
            configured_ticket_ttl = DEFAULT_TICKET_TTL_SECONDS
            logger.debug(f"No ticket TTL specified in app manifests, using default: {configured_ticket_ttl}s")

        # Reinitialize ticket store with configured TTL if different from current
        if self._websocket_ticket_store is None or self._websocket_ticket_store.ticket_ttl != configured_ticket_ttl:
            from ..auth.websocket_tickets import WebSocketTicketStore

            self._websocket_ticket_store = WebSocketTicketStore(ticket_ttl_seconds=configured_ticket_ttl)
            logger.info(f"WebSocket ticket store initialized with TTL: {configured_ticket_ttl}s")

        # Validate hooks before creating lifespan (fail fast)
        for app_config in apps:
            slug = app_config.get("slug", "unknown")
            on_startup = app_config.get("on_startup")
            on_shutdown = app_config.get("on_shutdown")

            if on_startup is not None and not callable(on_startup):
                raise ValueError(
                    f"on_startup hook for app '{slug}' must be callable, " f"got {type(on_startup).__name__}"
                )
            if on_shutdown is not None and not callable(on_shutdown):
                raise ValueError(
                    f"on_shutdown hook for app '{slug}' must be callable, " f"got {type(on_shutdown).__name__}"
                )

        # State for parent app
        # Build initial mounted_apps metadata synchronously so get_mounted_apps() works
        # immediately after create_multi_app() returns (before lifespan runs)
        mounted_apps: list[dict[str, Any]] = [
            {
                "slug": app_config["slug"],
                "path_prefix": app_config["path_prefix"],
                "status": "pending",  # Will be updated in lifespan to "mounted" or "failed"
                "manifest_path": str(app_config["manifest"]),
            }
            for app_config in apps
        ]
        shared_user_pool_initialized = False

        def _find_mounted_app_entry(slug: str) -> dict[str, Any] | None:
            """Find mounted app entry by slug."""
            for entry in mounted_apps:
                if entry.get("slug") == slug:
                    return entry
            return None

        async def _merge_cors_config_to_parent(
            parent_app: "FastAPI",
            child_app: "FastAPI",
            child_manifest: dict[str, Any],
            slug: str,
        ) -> None:
            """Merge CORS config from child app to parent app."""
            from ..auth.cors_utils import validate_cors_config

            child_cors = None
            try:
                if hasattr(child_app.state, "cors_config"):
                    child_cors = child_app.state.cors_config
                else:
                    cors_config_from_manifest = child_manifest.get("cors", {})
                    if cors_config_from_manifest:
                        from ..auth.config_helpers import (
                            CORS_DEFAULTS,
                            merge_config_with_defaults,
                        )

                        child_cors = merge_config_with_defaults(cors_config_from_manifest, CORS_DEFAULTS)
                        child_app.state.cors_config = child_cors
            except (AttributeError, TypeError, KeyError, ValueError) as e:
                logger.warning(
                    f"Error reading CORS config from child app '{slug}': {e}",
                    exc_info=True,
                )
                return

            if not child_cors:
                return

            try:
                validate_cors_config(child_cors, app_slug=slug)
            except ValueError:
                logger.exception(f"CORS config validation failed for '{slug}'")
                raise

            if hasattr(parent_app.state, "cors_config"):
                parent_cors = parent_app.state.cors_config
                child_origins = child_cors.get("allow_origins", [])
                parent_origins = parent_cors.get("allow_origins", [])

                child_enabled = child_cors.get("enabled", False)
                parent_enabled = parent_cors.get("enabled", False)
                merged_enabled = child_enabled or parent_enabled

                child_requires_credentials = child_cors.get("allow_credentials", False)
                parent_allows_credentials = parent_cors.get("allow_credentials", False)
                merged_allow_credentials = child_requires_credentials or parent_allows_credentials

                # Merge origins: if child requires credentials,
                # we must use specific origins (not wildcard)
                # If child has wildcard, use wildcard
                # (but validation will fail if credentials=True)
                # If parent has wildcard but child has specific origins,
                # prefer child's origins if credentials required
                if "*" in child_origins:
                    merged_origins = ["*"]
                elif "*" in parent_origins:
                    # If child requires credentials, we must use specific origins
                    # (wildcard + credentials is invalid)
                    if child_requires_credentials and child_origins:
                        merged_origins = child_origins
                    else:
                        merged_origins = ["*"]
                else:
                    merged_origins = list(set(parent_origins + child_origins))
                    if not merged_origins:
                        merged_origins = ["*"]

                # Build merged config: start with parent, then overlay child
                # (excluding keys we merge specially)
                # Exclude allow_credentials and allow_origins from child_cors spread
                # since we merge them specially
                child_cors_filtered = {
                    k: v for k, v in child_cors.items() if k not in ("allow_credentials", "allow_origins", "enabled")
                }

                merged_config = {
                    **parent_cors,
                    **child_cors_filtered,
                    "enabled": merged_enabled,
                    "allow_origins": merged_origins,
                    "allow_credentials": merged_allow_credentials,
                }

                try:
                    validate_cors_config(merged_config, app_slug=slug)
                except ValueError:
                    logger.exception(f"CORS config merge validation failed for '{slug}'")
                    raise

                parent_app.state.cors_config = merged_config
            else:
                parent_app.state.cors_config = child_cors

            logger.info(
                f"Merged CORS config from '{slug}': "
                f"origins={parent_app.state.cors_config.get('allow_origins')}, "
                f"credentials={parent_app.state.cors_config.get('allow_credentials')}, "
                f"enabled={parent_app.state.cors_config.get('enabled')}"
            )

        async def _register_websocket_routes(
            parent_app: "FastAPI",
            child_manifest: dict[str, Any],
            slug: str,
            path_prefix: str,
        ) -> None:
            """Register WebSocket routes on parent app for a child app."""
            websockets_config = child_manifest.get("websockets")
            if not websockets_config:
                logger.debug(f"No WebSocket configuration found for app '{slug}'")
                return

            # CRITICAL: Check if session manager is required and available
            # Some endpoints require session keys (csrf_required=True), which need
            # session manager
            requires_session_manager = False
            for _endpoint_name, endpoint_config in websockets_config.items():
                auth_config = endpoint_config.get("auth", {})
                if isinstance(auth_config, dict):
                    csrf_required = auth_config.get("csrf_required", True)  # Default to True
                    if csrf_required:
                        requires_session_manager = True
                        break

            if requires_session_manager and not engine.websocket_session_manager:
                error_msg = (
                    f"WebSocket routes cannot be registered for app '{slug}': "
                    "websocket_session_manager is not available. "
                    "WebSocket endpoints with csrf_required=True require "
                    "session manager to be initialized. "
                    "Set MDB_ENGINE_MASTER_KEY environment variable to enable "
                    "session manager."
                )
                logger.error(error_msg)
                raise RuntimeError(error_msg)

            # CRITICAL: Ensure websocket_ticket_store is available
            # Ticket authentication is required for WebSocket connections
            if not engine.websocket_ticket_store:
                error_msg = (
                    f"WebSocket routes cannot be registered for app '{slug}': "
                    "websocket_ticket_store is not available. "
                    "WebSocket authentication requires ticket store to be initialized."
                )
                logger.error(error_msg)
                raise RuntimeError(error_msg)

            # Store WebSocket config in parent app state for CSRF middleware to access
            if not hasattr(parent_app.state, "websocket_configs"):
                parent_app.state.websocket_configs = {}
            parent_app.state.websocket_configs[slug] = websockets_config
            logger.info(
                f"Stored WebSocket config for '{slug}' in parent app state " f"({len(websockets_config)} endpoint(s))"
            )

            try:
                from fastapi import APIRouter

                from ..routing.websockets import create_websocket_endpoint

                registered_count = 0
                failed_count = 0

                for endpoint_name, endpoint_config in websockets_config.items():
                    ws_path = endpoint_config.get("path", f"/{endpoint_name}")
                    # Combine mount prefix with WebSocket path
                    full_ws_path = f"{path_prefix.rstrip('/')}{ws_path}"

                    # Handle auth configuration
                    auth_config = endpoint_config.get("auth", {})
                    if isinstance(auth_config, dict) and "required" in auth_config:
                        require_auth = auth_config.get("required", True)
                    elif "require_auth" in endpoint_config:
                        require_auth = endpoint_config.get("require_auth", True)
                    else:
                        # Use app's auth_policy if available
                        if "auth_policy" in child_manifest:
                            require_auth = child_manifest["auth_policy"].get("required", True)
                        else:
                            require_auth = True

                    ping_interval = endpoint_config.get("ping_interval", 30)

                    try:
                        # Create WebSocket handler
                        handler = create_websocket_endpoint(
                            app_slug=slug,
                            path=ws_path,
                            endpoint_name=endpoint_name,
                            handler=None,
                            require_auth=require_auth,
                            ping_interval=ping_interval,
                        )

                        # Register on parent app with full path using FastAPI's
                        # proper WebSocket registration
                        # We register BEFORE mounting apps to ensure WebSocket
                        # routes are checked first
                        try:
                            # Use FastAPI's APIRouter approach (same as single-app mode)
                            # This maintains FastAPI features
                            # (dependency injection, OpenAPI docs, etc.)
                            ws_router = APIRouter()
                            ws_router.websocket(full_ws_path)(handler)

                            # Include router BEFORE mounting child app to ensure route priority
                            parent_app.include_router(ws_router)

                            logger.info(
                                f"Registered WebSocket route '{full_ws_path}' "
                                f"using FastAPI APIRouter "
                                f"(registered before app mount to ensure priority)"
                            )
                        except (
                            ValueError,
                            RuntimeError,
                            AttributeError,
                            TypeError,
                        ) as fastapi_error:
                            logger.error(
                                f"Failed to register WebSocket route "
                                f"'{full_ws_path}' with FastAPI: {fastapi_error}",
                                exc_info=True,
                            )
                            raise

                        logger.info(
                            f"Registered WebSocket route '{full_ws_path}' "
                            f"for mounted app '{slug}' (mounted at '{path_prefix}', "
                            f"auth: {require_auth}, ping: {ping_interval}s)"
                        )

                        # Verify route was actually registered
                        registered_routes = [
                            r
                            for r in parent_app.routes
                            if hasattr(r, "path") and full_ws_path in str(getattr(r, "path", ""))
                        ]

                        # CRITICAL: Log all WebSocket routes to verify registration
                        # FastAPI APIRouter creates routes of type 'APIWebSocketRoute'
                        all_ws_routes = [
                            (r.path, type(r).__name__)
                            for r in parent_app.routes
                            if hasattr(r, "path") and ("ws" in str(r.path).lower() or hasattr(r, "endpoint"))
                        ]
                        logger.info(f"[ROUTE VERIFICATION] All WebSocket-like routes: {all_ws_routes}")

                        if registered_routes:
                            registered_count += 1
                            logger.debug(f"Verified WebSocket route '{full_ws_path}' " f"registered for '{slug}'")
                        else:
                            failed_count += 1
                            logger.warning(
                                f"WebSocket route '{full_ws_path}' not found after registration "
                                f"for '{slug}' - route may not be accessible"
                            )
                    except (ValueError, TypeError, AttributeError, RuntimeError) as e:
                        failed_count += 1
                        logger.error(
                            f"Failed to register WebSocket route '{full_ws_path}' " f"for mounted app '{slug}': {e}",
                            exc_info=True,
                        )

                # Summary logging
                total_routes = len(websockets_config)
                if registered_count > 0:
                    logger.info(
                        f"WebSocket registration summary for '{slug}': "
                        f"{registered_count}/{total_routes} routes registered successfully"
                    )
                if failed_count > 0:
                    logger.warning(
                        f"WebSocket registration issues for '{slug}': "
                        f"{failed_count}/{total_routes} routes failed to register"
                    )
            except ImportError:
                logger.warning(
                    f"WebSocket support not available - skipping WebSocket routes " f"for mounted app '{slug}'"
                )
            except (ValueError, TypeError, AttributeError, RuntimeError) as e:
                logger.error(
                    f"Failed to register WebSocket routes for mounted app '{slug}': {e}",
                    exc_info=True,
                )

        @asynccontextmanager
        async def lifespan(app: FastAPI):  # noqa: C901
            """Lifespan context manager for parent app."""
            nonlocal mounted_apps, shared_user_pool_initialized

            # Auto-configure logging before anything else
            from ..observability.logging import configure_logging

            configure_logging()

            # Register SIGTERM handler so the signal arrival is logged before
            # uvicorn triggers lifespan shutdown.  signal.signal() only works
            # from the main thread; skip gracefully in test / worker threads.
            import signal
            import threading

            if threading.current_thread() is threading.main_thread():

                def _handle_sigterm(signum: int, frame: Any) -> None:
                    logger.info("[Engine] SIGTERM received -- initiating graceful shutdown")

                signal.signal(signal.SIGTERM, _handle_sigterm)
            else:
                logger.debug("Skipping SIGTERM handler (not main thread)")

            # Initialize engine
            await engine.initialize()

            # Register WebSocket ticket endpoint AFTER initialization
            # (ticket store is now available)
            if engine.websocket_ticket_store:
                app.state.websocket_ticket_store = engine.websocket_ticket_store
                logger.info("WebSocket ticket store stored in parent app state")

                # Set global ticket store for WebSocket authentication (works with routers)
                from ..routing.websockets import set_global_websocket_ticket_store

                set_global_websocket_ticket_store(engine.websocket_ticket_store)
                logger.info("Global WebSocket ticket store set for multi-app authentication")

                # Register WebSocket ticket endpoint on parent app
                from ..auth.websocket_tickets import create_websocket_ticket_endpoint

                ticket_endpoint = create_websocket_ticket_endpoint(engine.websocket_ticket_store)
                app.post("/auth/ticket")(ticket_endpoint)
                logger.info("WebSocket ticket endpoint registered at /auth/ticket")

            # Register WebSocket session endpoint AFTER initialization
            # (session manager is now available)
            if engine.websocket_session_manager:
                app.state.websocket_session_manager = engine.websocket_session_manager
                logger.info("WebSocket session manager stored in parent app state")

                # Register WebSocket session endpoint on parent app
                from ..auth.websocket_sessions import create_websocket_session_endpoint

                session_endpoint = create_websocket_session_endpoint(engine.websocket_session_manager)
                app.get("/auth/websocket-session")(session_endpoint)
                logger.info("WebSocket session endpoint registered at /auth/websocket-session")

            # Initialize shared user pool once if any app uses shared auth
            if has_shared_auth:
                logger.info("Initializing shared user pool for multi-app deployment")
                # Find first app with shared auth to get manifest for initialization
                for app_config in apps:
                    try:
                        manifest_path = app_config["manifest"]
                        app_manifest_pre = await asyncio.to_thread(_read_json_file, manifest_path)
                        auth_config = app_manifest_pre.get("auth", {})
                        if auth_config.get("mode") == "shared":
                            await self._initialize_shared_user_pool(app, app_manifest_pre)
                            shared_user_pool_initialized = True
                            logger.info("Shared user pool initialized for multi-app deployment")
                            break
                    except (FileNotFoundError, json.JSONDecodeError, KeyError) as e:
                        app_slug = app_config.get("slug", "unknown")
                        logger.warning(f"Could not initialize shared user pool from app '{app_slug}': {e}")

            # Mount each child app
            for app_config in apps:
                slug = app_config["slug"]
                manifest_path = app_config["manifest"]
                path_prefix = app_config["path_prefix"]
                on_startup = app_config.get("on_startup")
                on_shutdown = app_config.get("on_shutdown")

                try:
                    # Load manifest for context helpers
                    try:
                        app_manifest_data = await asyncio.to_thread(_read_json_file, manifest_path)
                    except (FileNotFoundError, json.JSONDecodeError) as e:
                        raise ValueError(f"Failed to load manifest for app '{slug}' at {manifest_path}: {e}") from e

                    # Validate manifest (mandatory — catches config errors early)
                    try:
                        from .manifest import validate_manifest

                        _v_ok, _v_err, _v_paths = await validate_manifest(app_manifest_data)
                        if not _v_ok:
                            _detail = f"Manifest validation failed for '{slug}': {_v_err}"
                            if _v_paths:
                                _detail += "\n  Paths: " + ", ".join(_v_paths)
                            logger.error(_detail)
                            if strict:
                                raise ValueError(_detail)
                    except (ImportError, RuntimeError) as _ve:
                        logger.warning(f"Manifest validation skipped for '{slug}': {_ve}")

                    # Log app configuration
                    auth_config = app_manifest_data.get("auth", {})
                    auth_mode = auth_config.get("mode", "app")
                    public_routes = auth_config.get("public_routes", [])
                    logger.info(
                        f"Mounting app '{slug}' at '{path_prefix}': "
                        f"auth_mode={auth_mode}, "
                        f"public_routes={len(public_routes)} routes"
                    )
                    if public_routes:
                        logger.debug(f"  Public routes for '{slug}': {public_routes}")
                    else:
                        logger.warning(
                            f"  App '{slug}' has no public routes configured. "
                            "All routes will require authentication."
                        )

                    # Create child app as sub-app (shares engine and lifecycle)
                    child_app = engine.create_app(
                        slug=slug,
                        manifest=manifest_path,
                        is_sub_app=True,  # Important: marks as sub-app
                        on_startup=on_startup,
                        on_shutdown=on_shutdown,
                    )

                    # CRITICAL: Set engine state BEFORE importing routes
                    # Routes may use dependencies that need request.app.state.engine
                    # This must be set before route decorators execute
                    child_app.state.engine = engine
                    child_app.state.app_slug = slug

                    # Automatically import routes from app module
                    # This discovers and imports route modules (web.py, routes.py, etc.)
                    # so that route decorators are executed and routes are registered
                    route_module = None
                    try:
                        route_module = await asyncio.to_thread(self._import_app_routes, child_app, manifest_path, slug)
                    except (
                        ValueError,
                        TypeError,
                        AttributeError,
                        RuntimeError,
                        ImportError,
                        SyntaxError,
                        OSError,
                    ) as e:
                        logger.warning(
                            f"Failed to auto-import routes for app '{slug}': {e}. "
                            "Routes may need to be imported manually.",
                            exc_info=True,
                        )

                    # --- authz provider + role_hierarchy for child app ----------
                    child_app.state.role_hierarchy = auth_config.get("users", {}).get("role_hierarchy")
                    try:
                        await self._initialize_auth_provider(
                            engine,
                            child_app,
                            slug,
                            app_manifest_data,
                            auth_config,
                        )
                    except (
                        ValueError,
                        TypeError,
                        RuntimeError,
                        AttributeError,
                        KeyError,
                        ConnectionError,
                    ):
                        logger.exception(f"Failed to initialize authz provider for child '{slug}'")

                    # Share user_pool with child app if shared auth is enabled
                    if shared_user_pool_initialized and hasattr(app.state, "user_pool"):
                        child_app.state.user_pool = app.state.user_pool
                        if hasattr(app.state, "audit_log"):
                            child_app.state.audit_log = app.state.audit_log
                        logger.debug(f"Shared user_pool with child app '{slug}'")

                    # Auto-register /auth/callback and /logout for shared-auth apps
                    if auth_mode == "shared":
                        from ..auth.sso_routes import register_sso_routes

                        register_sso_routes(child_app, auth_config)
                        for _auto_route in ["/auth/callback", "/logout"]:
                            if _auto_route not in public_routes:
                                public_routes.append(_auto_route)

                    # Share WebSocket session manager with child app
                    if hasattr(app.state, "websocket_session_manager"):
                        child_app.state.websocket_session_manager = app.state.websocket_session_manager
                        logger.debug(f"Shared WebSocket session manager with child app '{slug}'")

                    # Share WebSocket ticket store with child app
                    if hasattr(app.state, "websocket_ticket_store"):
                        child_app.state.websocket_ticket_store = app.state.websocket_ticket_store
                        logger.debug(f"Shared WebSocket ticket store with child app '{slug}'")

                    # Add middleware for app context helpers
                    from starlette.middleware.base import BaseHTTPMiddleware
                    from starlette.requests import Request

                    # Get auth_hub_url from manifest or env
                    auth_hub_url = None
                    if auth_config.get("mode") == "shared":
                        auth_hub_url = auth_config.get("auth_hub_url")
                    if not auth_hub_url:
                        auth_hub_url = os.getenv("AUTH_HUB_URL", "/auth-hub")

                    # Store parent app reference and current app info for middleware
                    # Note: engine and app_slug are already set above (before route import)
                    child_app.state.parent_app = app
                    child_app.state.app_base_path = path_prefix
                    child_app.state.app_auth_hub_url = auth_hub_url
                    child_app.state.app_manifest = app_manifest_data

                    # Auto-inject template globals (base_path, auth_hub_url, app_slug)
                    # so child templates can use {{ base_path }} without handlers passing it.
                    # Also add the framework templates dir to the Jinja2 loader
                    # so apps can {% extends "mdb_base.html" %}.
                    if route_module:
                        child_templates = getattr(route_module, "templates", None)
                        if child_templates and hasattr(child_templates, "env"):
                            child_templates.env.globals["base_path"] = path_prefix
                            child_templates.env.globals["auth_hub_url"] = auth_hub_url
                            child_templates.env.globals["app_slug"] = slug

                            try:
                                from mdb_engine.routing._ssr import _make_markdown_filter

                                _md = _make_markdown_filter()
                                if _md is not None:
                                    child_templates.env.filters["markdown"] = _md
                            except ImportError:
                                pass

                            try:
                                from jinja2 import ChoiceLoader, FileSystemLoader

                                _fw_templates = str(Path(__file__).resolve().parent.parent / "templates")
                                _fw_exists = Path(_fw_templates).exists()
                                existing_loader = child_templates.env.loader
                                if existing_loader is not None:
                                    child_templates.env.loader = ChoiceLoader(
                                        [existing_loader, FileSystemLoader(_fw_templates)]
                                    )
                                else:
                                    child_templates.env.loader = FileSystemLoader(_fw_templates)

                                from jinja2.exceptions import TemplateNotFound

                                try:
                                    child_templates.env.get_template("mdb_base.html")
                                    logger.info("Framework templates registered for '%s'", slug)
                                except TemplateNotFound:
                                    logger.warning(
                                        "Framework template patching FAILED for '%s': "
                                        "mdb_base.html not found after ChoiceLoader setup "
                                        "(dir=%s, exists=%s)",
                                        slug,
                                        _fw_templates,
                                        _fw_exists,
                                    )
                            except (ImportError, TypeError, ValueError, OSError) as exc:
                                logger.warning(
                                    "Could not add framework templates to loader for '%s': %s",
                                    slug,
                                    exc,
                                )

                            logger.info("Injected Jinja2 template globals for app '%s'", slug)

                    # Create middleware factory to properly capture loop variables
                    def create_app_context_middleware(
                        app_slug: str,
                        app_path_prefix: str,
                        app_auth_hub_url_val: str,
                        app_manifest_data_val: dict[str, Any],
                    ) -> type[BaseHTTPMiddleware]:
                        """Create middleware class with captured variables."""

                        class _AppContextMiddleware(BaseHTTPMiddleware):
                            """Middleware that sets app context helpers on request.state."""

                            async def dispatch(self, request: Request, call_next):
                                # Get parent app from child app state
                                parent_app = getattr(request.app.state, "parent_app", None)

                                # Set app context helpers
                                request.state.app_base_path = getattr(
                                    request.app.state,
                                    "app_base_path",
                                    app_path_prefix,
                                )
                                request.state.auth_hub_url = getattr(
                                    request.app.state,
                                    "app_auth_hub_url",
                                    app_auth_hub_url_val,
                                )
                                request.state.app_slug = getattr(request.app.state, "app_slug", app_slug)
                                request.state.engine = engine
                                request.state.manifest = getattr(
                                    request.app.state,
                                    "app_manifest",
                                    app_manifest_data_val,
                                )

                                # Get mounted apps from parent app state
                                if parent_app and hasattr(parent_app.state, "mounted_apps"):
                                    mounted_apps_list = parent_app.state.mounted_apps
                                    request.state.mounted_apps = {
                                        ma["slug"]: {
                                            "slug": ma["slug"],
                                            "path_prefix": ma.get("path_prefix"),
                                            "status": ma.get("status", "unknown"),
                                        }
                                        for ma in mounted_apps_list
                                    }
                                else:
                                    # Fallback: create minimal dict with current app
                                    request.state.mounted_apps = {
                                        app_slug: {
                                            "slug": app_slug,
                                            "path_prefix": app_path_prefix,
                                            "status": "mounted",
                                        }
                                    }

                                response = await call_next(request)

                                # Auto-rewrite redirect URLs for mounted apps.
                                # If a child app returns RedirectResponse("/login"),
                                # rewrite to "/auth-hub/login" (prepend mount prefix).
                                # Skip if already prefixed (cross-app redirects like
                                # "/auth-hub/login" from another app).
                                if 300 <= response.status_code < 400:
                                    location = response.headers.get("location", "")
                                    if (
                                        location.startswith("/")
                                        and not location.startswith(app_path_prefix + "/")
                                        and location != app_path_prefix
                                    ):
                                        response.headers["location"] = f"{app_path_prefix}{location}"

                                return response

                        return _AppContextMiddleware

                    middleware_class = create_app_context_middleware(slug, path_prefix, auth_hub_url, app_manifest_data)
                    child_app.add_middleware(middleware_class)
                    logger.debug(f"Added AppContextMiddleware to child app '{slug}'")

                    await _register_websocket_routes(app, app_manifest_data, slug, path_prefix)

                    # Auto-register CRUD endpoints for collections defined in manifest
                    _collections_cfg = app_manifest_data.get("collections", {})
                    if _collections_cfg:
                        from ..routing.auto_crud import mount_auto_crud_routes

                        mount_auto_crud_routes(child_app, _collections_cfg)

                    # Pre-create shared LLM/embedding services for this app
                    # so graph and memory share the same instances.
                    if engine._service_initializer:  # noqa: SLF001
                        raw_mem = app_manifest_data.get("memory_config")
                        engine._service_initializer._ensure_shared_services(  # noqa: SLF001
                            slug,
                            llm_config=app_manifest_data.get("llm_config"),
                            embedding_config=app_manifest_data.get("embedding_config"),
                            memory_config=raw_mem if isinstance(raw_mem, dict) else None,
                        )

                    # Initialize Graph service if configured (MUST complete before memory)
                    graph_config = app_manifest_data.get("graph_config", {})
                    if graph_config.get("enabled", True) and engine._service_initializer:  # noqa: SLF001
                        try:
                            await engine._service_initializer.initialize_graph_service(  # noqa: SLF001
                                slug,
                                graph_config,
                                llm_config=app_manifest_data.get("llm_config"),
                            )
                            logger.info(f"Graph service initialized for mounted app '{slug}'")
                        except (ImportError, AttributeError, TypeError, ValueError, RuntimeError) as e:
                            logger.warning(
                                f"Graph service initialization failed for mounted app '{slug}': {e}",
                                extra={"app_slug": slug},
                            )

                    from .manifest import is_config_enabled

                    raw_memory_config = app_manifest_data.get("memory_config")
                    if is_config_enabled(raw_memory_config):
                        if engine._service_initializer:  # noqa: SLF001
                            try:
                                await engine._service_initializer.initialize_memory_service(  # noqa: SLF001
                                    slug, raw_memory_config, llm_config=app_manifest_data.get("llm_config")
                                )
                                logger.info(
                                    f"Memory service initialized for mounted app '{slug}' " f"in multi-app context"
                                )
                            except OpenAIError as e:
                                logger.warning(
                                    f"Memory service initialization skipped for mounted app "
                                    f"'{slug}': OpenAI API error. {e}",
                                    extra={"app_slug": slug, "error": str(e)},
                                )
                            except (
                                ImportError,
                                AttributeError,
                                TypeError,
                                ValueError,
                                RuntimeError,
                                ConnectionError,
                                OSError,
                            ) as e:
                                error_msg = str(e).lower()
                                error_type = type(e).__name__
                                is_api_key_error = (
                                    "api_key" in error_msg or "api key" in error_msg or "openai" in error_type.lower()
                                )
                                if is_api_key_error:
                                    logger.warning(
                                        f"Memory service initialization skipped for mounted app "
                                        f"'{slug}': Missing API key or configuration. {e}",
                                        extra={"app_slug": slug, "error": str(e)},
                                    )
                                else:
                                    logger.error(
                                        f"Failed to initialize memory service for mounted app " f"'{slug}': {e}",
                                        exc_info=True,
                                        extra={"app_slug": slug, "error": str(e)},
                                    )

                    # Initialize Perfect Brain (nested inside memory_config)
                    _mem = app_manifest_data.get("memory_config")
                    _pb_cfg = _mem.get("perfect_brain") if isinstance(_mem, dict) else None
                    if (
                        isinstance(_pb_cfg, dict) and _pb_cfg.get("enabled", False) and engine._service_initializer  # noqa: SLF001
                    ):
                        try:
                            await engine._service_initializer.initialize_perfect_brain(  # noqa: SLF001
                                slug, _pb_cfg
                            )
                        except (ImportError, AttributeError, TypeError, ValueError, RuntimeError) as e:
                            logger.warning(f"Failed to initialize PerfectBrain for '{slug}': {e}")

                    # Auto-detect and call on_startup from imported module or app config
                    module_on_startup = getattr(route_module, "on_startup", None) if route_module else None
                    config_on_startup = on_startup  # from app_config.get("on_startup")
                    startup_fn = config_on_startup or module_on_startup

                    if startup_fn and callable(startup_fn):
                        try:
                            await startup_fn(child_app, engine, app_manifest_data)
                            logger.info(f"on_startup callback executed for app '{slug}'")
                        except (ValueError, TypeError, AttributeError, RuntimeError, OSError) as e:
                            logger.error(
                                f"on_startup failed for app '{slug}': {e}",
                                exc_info=True,
                            )

                    # Store initialized services on child app state
                    if engine.initialized:
                        child_app.state.memory_service = engine.get_memory_service(slug)
                        child_app.state.graph_service = engine.get_graph_service(slug)
                        child_app.state.embedding_service = engine.get_embedding_service(slug)
                        child_app.state.llm_service = engine.get_llm_service(slug)

                    # Mount child app at path prefix (AFTER WebSocket routes are registered)
                    app.mount(path_prefix, child_app)

                    # CRITICAL FIX: Merge CORS config from child app to parent app
                    await _merge_cors_config_to_parent(app, child_app, app_manifest_data, slug)

                    # Update existing entry instead of appending
                    entry = _find_mounted_app_entry(slug)
                    if entry:
                        entry.update(
                            {
                                "status": "mounted",
                                "manifest": app_manifest_data,
                                "app": child_app,
                            }
                        )
                    else:
                        # Fallback: append if entry not found (shouldn't happen)
                        mounted_apps.append(
                            {
                                "slug": slug,
                                "path_prefix": path_prefix,
                                "status": "mounted",
                                "manifest": app_manifest_data,
                                "app": child_app,
                            }
                        )
                    logger.info(f"Mounted app '{slug}' at path prefix '{path_prefix}'")

                except FileNotFoundError as e:
                    error_msg = (
                        f"Failed to mount app '{slug}' at {path_prefix}: " f"manifest.json not found at {manifest_path}"
                    )
                    logger.error(error_msg, exc_info=True)
                    # Update existing entry instead of appending
                    entry = _find_mounted_app_entry(slug)
                    if entry:
                        entry.update(
                            {
                                "status": "failed",
                                "error": error_msg,
                            }
                        )
                    else:
                        # Fallback: append if entry not found (shouldn't happen)
                        mounted_apps.append(
                            {
                                "slug": slug,
                                "path_prefix": path_prefix,
                                "status": "failed",
                                "error": error_msg,
                                "manifest_path": str(manifest_path),
                            }
                        )
                    if strict:
                        raise ValueError(error_msg) from e
                    continue
                except json.JSONDecodeError as e:
                    error_msg = (
                        f"Failed to mount app '{slug}' at {path_prefix}: "
                        f"Invalid JSON in manifest.json at {manifest_path}: {e}"
                    )
                    logger.error(error_msg, exc_info=True)
                    # Update existing entry instead of appending
                    entry = _find_mounted_app_entry(slug)
                    if entry:
                        entry.update(
                            {
                                "status": "failed",
                                "error": error_msg,
                            }
                        )
                    else:
                        # Fallback: append if entry not found (shouldn't happen)
                        mounted_apps.append(
                            {
                                "slug": slug,
                                "path_prefix": path_prefix,
                                "status": "failed",
                                "error": error_msg,
                                "manifest_path": str(manifest_path),
                            }
                        )
                    if strict:
                        raise ValueError(error_msg) from e
                    continue
                except ValueError as e:
                    error_msg = f"Failed to mount app '{slug}' at {path_prefix}: {e}"
                    logger.error(error_msg, exc_info=True)
                    # Update existing entry instead of appending
                    entry = _find_mounted_app_entry(slug)
                    if entry:
                        entry.update(
                            {
                                "status": "failed",
                                "error": error_msg,
                            }
                        )
                    else:
                        # Fallback: append if entry not found (shouldn't happen)
                        mounted_apps.append(
                            {
                                "slug": slug,
                                "path_prefix": path_prefix,
                                "status": "failed",
                                "error": error_msg,
                                "manifest_path": str(manifest_path),
                            }
                        )
                    if strict:
                        raise ValueError(error_msg) from e
                    continue
                except (KeyError, RuntimeError) as e:
                    error_msg = f"Failed to mount app '{slug}' at {path_prefix}: {e}"
                    logger.error(error_msg, exc_info=True)
                    # Update existing entry instead of appending
                    entry = _find_mounted_app_entry(slug)
                    if entry:
                        entry.update(
                            {
                                "status": "failed",
                                "error": error_msg,
                            }
                        )
                    else:
                        # Fallback: append if entry not found (shouldn't happen)
                        mounted_apps.append(
                            {
                                "slug": slug,
                                "path_prefix": path_prefix,
                                "status": "failed",
                                "error": error_msg,
                                "manifest_path": str(manifest_path),
                            }
                        )
                    # Always re-raise RuntimeError for critical failures
                    # (like missing session manager)
                    # These are configuration errors that should fail fast
                    if isinstance(e, RuntimeError) and "websocket_session_manager" in str(e):
                        raise RuntimeError(error_msg) from e
                    if strict:
                        raise RuntimeError(error_msg) from e
                    continue
                except (OSError, PermissionError, ImportError, AttributeError, TypeError) as e:
                    error_msg = f"Unexpected error mounting app '{slug}' at {path_prefix}: {e}"
                    logger.error(error_msg, exc_info=True)
                    # Update existing entry instead of appending
                    entry = _find_mounted_app_entry(slug)
                    if entry:
                        entry.update(
                            {
                                "status": "failed",
                                "error": error_msg,
                            }
                        )
                    else:
                        # Fallback: append if entry not found (shouldn't happen)
                        mounted_apps.append(
                            {
                                "slug": slug,
                                "path_prefix": path_prefix,
                                "status": "failed",
                                "error": error_msg,
                                "manifest_path": str(manifest_path),
                            }
                        )
                    if strict:
                        raise RuntimeError(error_msg) from e
                    continue

            # Update app.state.mounted_apps with final status (entries already updated in place)
            # This ensures the state reflects the final mounted_apps list
            app.state.mounted_apps = mounted_apps

            # VERIFICATION: Log final configuration state
            logger.info("=" * 60)
            logger.info("MDB-Engine Multi-App Configuration Verification")
            logger.info("=" * 60)

            # Auto-add localhost dev origins when not in production
            cors_config = getattr(app.state, "cors_config", None)
            if cors_config and cors_config.get("enabled"):
                env_name = os.getenv("ENVIRONMENT", "development").lower()
                if env_name != "production":
                    origins = cors_config.get("allow_origins", [])
                    dev_origins = [f"http://localhost:{p}" for p in range(8000, 8010)]
                    for dev_o in dev_origins:
                        if dev_o not in origins:
                            origins.append(dev_o)
                    cors_config["allow_origins"] = origins
                    logger.debug("Auto-added localhost dev CORS origins")

            # Verify CORS config
            if cors_config:
                logger.info(
                    f"CORS Config: enabled={cors_config.get('enabled')}, "
                    f"origins={cors_config.get('allow_origins')}, "
                    f"credentials={cors_config.get('allow_credentials')}"
                )
            else:
                logger.warning("No CORS config found on parent app")

            # Verify WebSocket routes
            ws_routes = [r for r in app.routes if hasattr(r, "path") and "/ws" in str(getattr(r, "path", ""))]
            if ws_routes:
                logger.info(f"Found {len(ws_routes)} WebSocket route(s):")
                for route in ws_routes:
                    route_path = getattr(route, "path", "unknown")
                    logger.info(f"   {route_path}")
            else:
                logger.warning("No WebSocket routes found - check manifest.json websockets config")

            logger.info("=" * 60)

            # Start shared realtime Change Stream watcher for all apps
            await self._start_multi_app_realtime(app, engine, apps, mounted_apps)

            yield

            # Stop realtime watcher before shutdown
            await self._stop_multi_app_realtime(app)

            # Shutdown is handled by parent app
            await engine.shutdown()

        # Create parent FastAPI app
        parent_app = FastAPI(title=title, lifespan=lifespan, root_path=root_path, **fastapi_kwargs)

        # Register structured error handlers for MongoDBEngineError hierarchy
        from ..observability.error_handlers import register_error_handlers

        register_error_handlers(parent_app)

        # Set mounted_apps immediately so get_mounted_apps() works before lifespan runs
        parent_app.state.mounted_apps = mounted_apps
        parent_app.state.is_multi_app = True
        parent_app.state.engine = engine

        # Set default CORS config on parent app for WebSocket origin validation
        # This ensures CSRF middleware can validate WebSocket origins even if child apps
        # don't configure CORS
        # NOTE: allow_credentials defaults to False here, but will be set to True
        # during merge if any child app requires it (essential for SSO cookie-based auth)
        from ..auth.config_helpers import CORS_DEFAULTS

        parent_app.state.cors_config = CORS_DEFAULTS.copy()
        parent_app.state.cors_config["enabled"] = True
        parent_app.state.cors_config["allow_origins"] = ["*"]  # Default to allow all for WebSocket
        # Keep allow_credentials as False initially - will be merged from child apps
        logger.debug("Set default CORS config on parent app for WebSocket origin validation")

        # Store app reference in engine for get_mounted_apps()
        self._multi_app_instance = parent_app

        # Add diagnostic ASGI middleware FIRST (outermost - runs before everything)
        # This will catch WebSocket upgrades before any other middleware
        from starlette.middleware.base import BaseHTTPMiddleware

        class DiagnosticMiddleware(BaseHTTPMiddleware):
            """Diagnostic middleware to log ALL requests, especially WebSocket upgrades."""

            async def dispatch(self, request, call_next):
                path = request.url.path
                method = request.method
                upgrade_header = request.headers.get("upgrade", "").lower()
                connection_header = request.headers.get("connection", "").lower()
                origin_header = request.headers.get("origin")

                # Log WebSocket upgrade attempts IMMEDIATELY
                if upgrade_header == "websocket" or "websocket" in path.lower():
                    logger.debug(
                        f"[DIAGNOSTIC MIDDLEWARE] WebSocket upgrade detected: "
                        f"{method} {path}, upgrade={upgrade_header}, "
                        f"connection={connection_header}, origin={origin_header}"
                    )

                try:
                    response = await call_next(request)

                    # Log response for WebSocket upgrades
                    if upgrade_header == "websocket" or "websocket" in path.lower():
                        logger.debug(
                            f"[DIAGNOSTIC MIDDLEWARE] WebSocket response: " f"{method} {path} -> {response.status_code}"
                        )

                    return response
                except (RuntimeError, ConnectionError, ValueError, AttributeError) as e:
                    if upgrade_header == "websocket" or "websocket" in path.lower():
                        logger.error(
                            f"[DIAGNOSTIC MIDDLEWARE] WebSocket exception: "
                            f"{method} {path} -> {type(e).__name__}: {e}",
                            exc_info=True,
                        )
                    raise

        parent_app.add_middleware(DiagnosticMiddleware)
        logger.debug("DiagnosticMiddleware added for parent app (outermost layer)")

        # Add request scope middleware
        from ..di import ScopeManager

        class RequestScopeMiddleware(BaseHTTPMiddleware):
            """Middleware that manages request-scoped DI instances."""

            async def dispatch(self, request, call_next):
                ScopeManager.begin_request()
                try:
                    response = await call_next(request)
                    return response
                finally:
                    ScopeManager.end_request()

        parent_app.add_middleware(RequestScopeMiddleware)
        logger.debug("RequestScopeMiddleware added for parent app")

        # CRITICAL: Add CSRF middleware to parent app if any child app uses shared auth
        # WebSocket routes are registered on parent app, so parent app middleware runs first
        # CSRF middleware on parent app validates WebSocket origin using parent app's CORS config
        if has_shared_auth:
            from ..auth.csrf import create_csrf_middleware

            # Create CSRF middleware with default config (will use parent app's CORS config)
            # Exempt routes that don't need CSRF (health checks, public routes
            # from child apps)
            # all_public_routes includes base routes + child app public routes
            # with path prefixes
            # Add WebSocket session and ticket endpoints to public routes
            # (they handle their own auth)
            public_routes_with_websocket_endpoints = list(all_public_routes) + [
                "/auth/websocket-session",
                "/auth/ticket",
            ]
            parent_csrf_config = {
                "csrf_protection": True,
                "public_routes": public_routes_with_websocket_endpoints,
            }
            csrf_middleware = create_csrf_middleware(parent_csrf_config)
            parent_app.add_middleware(csrf_middleware)

            # NOTE: WebSocket ticket and session endpoint registrations are moved to lifespan
            # context manager (after engine.initialize()) because they're only available after
            # initialization. The CSRF middleware still needs to know about these routes, so
            # they're added to public_routes_with_websocket_endpoints above.

            logger.info("CSRFMiddleware added to parent app for WebSocket origin validation")

        # Add shared CORS middleware if configured
        # NOTE: We create a dynamic CORS middleware that reads from app.state.cors_config
        # This allows the config to be updated after child apps are mounted and merged
        try:
            from starlette.middleware.base import BaseHTTPMiddleware
            from starlette.requests import Request
            from starlette.responses import Response

            class DynamicCORSMiddleware(BaseHTTPMiddleware):
                """
                Dynamic CORS middleware that reads config from app.state.cors_config.

                This allows CORS config to be updated after child apps are mounted
                and their configs are merged, which is essential for SSO multi-app
                setups where allow_credentials must be True for cookie-based auth.
                """

                async def dispatch(self, request: Request, call_next):
                    from ..auth.cors_utils import is_origin_allowed

                    cors_config = getattr(request.app.state, "cors_config", {})

                    if not cors_config.get("enabled", False):
                        return await call_next(request)

                    origin = request.headers.get("origin")
                    allowed_origins = cors_config.get("allow_origins", ["*"])
                    allow_credentials = cors_config.get("allow_credentials", False)

                    origin_allowed = is_origin_allowed(origin, allowed_origins)

                    if request.method == "OPTIONS":
                        if origin_allowed:
                            headers = {
                                "Access-Control-Allow-Methods": ", ".join(cors_config.get("allow_methods", ["*"])),
                                "Access-Control-Allow-Headers": ", ".join(cors_config.get("allow_headers", ["*"])),
                                "Access-Control-Max-Age": str(cors_config.get("max_age", 3600)),
                            }
                            if allow_credentials:
                                headers["Access-Control-Allow-Credentials"] = "true"
                            if origin:
                                headers["Access-Control-Allow-Origin"] = origin

                            expose_headers = cors_config.get("expose_headers", [])
                            if expose_headers:
                                headers["Access-Control-Expose-Headers"] = ", ".join(expose_headers)

                            return Response(status_code=200, headers=headers)
                        return Response(status_code=403)

                    response = await call_next(request)

                    if origin_allowed:
                        if origin:
                            response.headers["Access-Control-Allow-Origin"] = origin
                        if allow_credentials:
                            response.headers["Access-Control-Allow-Credentials"] = "true"

                        expose_headers = cors_config.get("expose_headers", [])
                        if expose_headers:
                            response.headers["Access-Control-Expose-Headers"] = ", ".join(expose_headers)

                    return response

            parent_app.add_middleware(DynamicCORSMiddleware)
            logger.debug("Dynamic CORS middleware added for parent app (reads from app.state.cors_config)")
        except ImportError:
            logger.warning("CORS middleware not available")

        # Add unified health check endpoint
        @parent_app.get("/health")
        async def health_check():
            """Unified health check for all mounted apps."""
            from ..observability import check_engine_health, check_mongodb_health

            # Both are async functions
            start_time = time.time()
            engine_health = await check_engine_health(engine)
            mongo_health = await check_mongodb_health(engine.mongo_client)
            engine_response_time = int((time.time() - start_time) * 1000)

            # Check each mounted app's status
            mounted_status = {}
            for mounted_app_info in mounted_apps:
                app_slug = mounted_app_info["slug"]
                path_prefix = mounted_app_info["path_prefix"]
                status = mounted_app_info["status"]

                app_status = {
                    "path_prefix": path_prefix,
                    "status": status,
                }

                if "error" in mounted_app_info:
                    app_status["error"] = mounted_app_info["error"]
                    app_status["status"] = "unhealthy"
                elif status == "mounted":
                    # App is mounted successfully
                    app_status["status"] = "healthy"
                    # Try to get response time by checking if app has routes
                    try:
                        # Find the mounted app and check its route count
                        for route in parent_app.routes:
                            if hasattr(route, "path") and route.path == path_prefix:
                                if hasattr(route, "app"):
                                    mounted_app = route.app
                                    route_count = len(mounted_app.routes)
                                    app_status["route_count"] = route_count
                            break
                    except (AttributeError, TypeError, KeyError):
                        pass

                mounted_status[app_slug] = app_status

            # Determine overall status
            all_healthy = (
                engine_health.status.value == "healthy"
                and mongo_health.status.value == "healthy"
                and all(app_info.get("status") in ("healthy", "mounted") for app_info in mounted_status.values())
            )

            overall_status = "healthy" if all_healthy else "unhealthy"

            return {
                "status": overall_status,
                "engine": {
                    "status": engine_health.status.value,
                    "message": engine_health.message,
                    "response_time_ms": engine_response_time,
                },
                "mongodb": {
                    "status": mongo_health.status.value,
                    "message": mongo_health.message,
                },
                "apps": mounted_status,
            }

        # Lightweight readiness probe (Kubernetes-style)
        @parent_app.get("/ready")
        async def readiness_check():
            """Lightweight readiness probe -- can this instance accept traffic?"""
            from starlette.responses import JSONResponse

            from ..observability import check_mongodb_health

            result = await check_mongodb_health(engine.mongo_client)
            is_ready = result.status.value == "healthy" and engine.initialized
            status_code = 200 if is_ready else 503
            return JSONResponse(
                status_code=status_code,
                content={
                    "ready": is_ready,
                    "mongodb": result.status.value,
                    "engine_initialized": engine.initialized,
                },
            )

        # Add metrics endpoint (exposes in-memory operation metrics)
        @parent_app.get("/metrics")
        async def metrics_endpoint():
            """Return collected operation metrics (counts, durations, error rates)."""
            try:
                from ..observability.metrics import get_metrics_collector

                collector = get_metrics_collector()
                return collector.get_summary()
            except (ImportError, RuntimeError, AttributeError) as e:
                return {"error": f"Metrics unavailable: {e}", "operations": {}}

        # Add route introspection endpoint
        @parent_app.get("/_mdb/routes")
        async def list_routes():
            """List all routes from all mounted apps."""
            routes_info = {
                "parent_app": {
                    "routes": [],
                },
                "mounted_apps": {},
            }

            # Get parent app routes
            for route in parent_app.routes:
                route_info = {
                    "path": getattr(route, "path", str(route)),
                    "methods": list(getattr(route, "methods", set())),
                    "name": getattr(route, "name", None),
                }
                routes_info["parent_app"]["routes"].append(route_info)

            # Get routes from mounted apps
            for mounted_app_info in mounted_apps:
                app_slug = mounted_app_info["slug"]
                path_prefix = mounted_app_info["path_prefix"]
                status = mounted_app_info["status"]

                if status != "mounted":
                    routes_info["mounted_apps"][app_slug] = {
                        "path_prefix": path_prefix,
                        "status": status,
                        "routes": [],
                        "error": mounted_app_info.get("error"),
                    }
                    continue

                # Find the mounted app
                app_routes = []
                for route in parent_app.routes:
                    # Check if this route belongs to the mounted app
                    # Mounted apps appear as Mount routes
                    if hasattr(route, "path") and route.path == path_prefix:
                        # This is the mount point
                        if hasattr(route, "app"):
                            # Get routes from the mounted app
                            mounted_app = route.app
                            for child_route in mounted_app.routes:
                                route_path = getattr(child_route, "path", str(child_route))
                                # Prepend path prefix
                                full_path = f"{path_prefix}{route_path}" if route_path != "/" else path_prefix

                                route_info = {
                                    "path": full_path,
                                    "relative_path": route_path,
                                    "methods": list(getattr(child_route, "methods", set())),
                                    "name": getattr(child_route, "name", None),
                                }
                                app_routes.append(route_info)

                routes_info["mounted_apps"][app_slug] = {
                    "path_prefix": path_prefix,
                    "status": status,
                    "routes": app_routes,
                    "route_count": len(app_routes),
                }

            return routes_info

        # Aggregate OpenAPI docs from all mounted apps
        def custom_openapi():
            """Generate aggregated OpenAPI schema from all mounted apps."""
            from fastapi.openapi.utils import get_openapi

            if parent_app.openapi_schema:
                return parent_app.openapi_schema

            # Get base schema from parent app
            openapi_schema = get_openapi(
                title=title,
                version=fastapi_kwargs.get("version", "1.0.0"),
                description=fastapi_kwargs.get("description", ""),
                routes=parent_app.routes,
            )

            # Aggregate schemas from mounted apps
            for mounted_app_info in mounted_apps:
                if mounted_app_info.get("status") != "mounted":
                    continue

                app_slug = mounted_app_info["slug"]
                path_prefix = mounted_app_info["path_prefix"]

                # Find the mounted app
                for route in parent_app.routes:
                    if hasattr(route, "path") and route.path == path_prefix:
                        if hasattr(route, "app"):
                            mounted_app = route.app
                            try:
                                # Get OpenAPI schema from mounted app
                                child_schema = get_openapi(
                                    title=getattr(mounted_app, "title", app_slug),
                                    version=getattr(mounted_app, "version", "1.0.0"),
                                    description=getattr(mounted_app, "description", ""),
                                    routes=mounted_app.routes,
                                )

                                # Merge paths with prefix
                                if "paths" in child_schema:
                                    for path, methods in child_schema["paths"].items():
                                        # Prepend path prefix
                                        prefixed_path = f"{path_prefix}{path}" if path != "/" else path_prefix
                                        openapi_schema["paths"][prefixed_path] = methods

                                # Merge components/schemas
                                if "components" in child_schema:
                                    if "components" not in openapi_schema:
                                        openapi_schema["components"] = {}
                                    if "schemas" in child_schema["components"]:
                                        if "schemas" not in openapi_schema["components"]:
                                            openapi_schema["components"]["schemas"] = {}
                                        openapi_schema["components"]["schemas"].update(
                                            child_schema["components"]["schemas"]
                                        )

                                logger.debug(f"Aggregated OpenAPI schema from app '{app_slug}'")
                            except (AttributeError, TypeError, KeyError, ValueError) as e:
                                logger.warning(f"Failed to aggregate OpenAPI schema from app '{app_slug}': {e}")
                        break

            parent_app.openapi_schema = openapi_schema
            return openapi_schema

        parent_app.openapi = custom_openapi

        # Add per-app docs endpoint
        @parent_app.get("/docs/{app_slug}")
        async def app_docs(app_slug: str):
            """Get OpenAPI docs for a specific app."""
            from fastapi.openapi.docs import get_swagger_ui_html

            # Find the app
            mounted_app = None
            path_prefix = None
            for mounted_app_info in mounted_apps:
                if mounted_app_info["slug"] == app_slug:
                    path_prefix = mounted_app_info["path_prefix"]
                    # Find the mounted app
                    for route in parent_app.routes:
                        if hasattr(route, "path") and route.path == path_prefix:
                            if hasattr(route, "app"):
                                mounted_app = route.app
                                break
                    break

            if not mounted_app:
                from fastapi import HTTPException

                raise HTTPException(404, f"App '{app_slug}' not found or not mounted")

            # Generate OpenAPI JSON for this app
            from fastapi.openapi.utils import get_openapi

            openapi_schema = get_openapi(
                title=getattr(mounted_app, "title", app_slug),
                version=getattr(mounted_app, "version", "1.0.0"),
                description=getattr(mounted_app, "description", ""),
                routes=mounted_app.routes,
            )

            # Modify paths to include prefix
            if "paths" in openapi_schema:
                new_paths = {}
                for path, methods in openapi_schema["paths"].items():
                    prefixed_path = f"{path_prefix}{path}" if path != "/" else path_prefix
                    new_paths[prefixed_path] = methods
                openapi_schema["paths"] = new_paths

            # Return Swagger UI HTML
            openapi_url = f"/_mdb/openapi/{app_slug}.json"

            # Store schema temporarily for the JSON endpoint
            if not hasattr(parent_app.state, "app_openapi_schemas"):
                parent_app.state.app_openapi_schemas = {}
            parent_app.state.app_openapi_schemas[app_slug] = openapi_schema

            return get_swagger_ui_html(
                openapi_url=openapi_url,
                title=f"{app_slug} - API Documentation",
            )

        @parent_app.get("/_mdb/openapi/{app_slug}.json")
        async def app_openapi_json(app_slug: str):
            """Get OpenAPI JSON for a specific app."""
            from fastapi import HTTPException

            if not hasattr(parent_app.state, "app_openapi_schemas"):
                raise HTTPException(404, f"OpenAPI schema for '{app_slug}' not found")

            schema = parent_app.state.app_openapi_schemas.get(app_slug)
            if not schema:
                raise HTTPException(404, f"OpenAPI schema for '{app_slug}' not found")

            return schema

        logger.info(f"Multi-app parent created with {len(apps)} app(s) configured")

        return parent_app

    def get_mounted_apps(self, app: Optional["FastAPI"] = None) -> list[dict[str, Any]]:
        """
        Get metadata about all mounted apps.

        Args:
            app: FastAPI app instance (optional, will use engine's tracked app if available)

        Returns:
            List of dicts with app metadata:
            - slug: App slug
            - path_prefix: Path prefix where app is mounted
            - status: Mount status ("mounted", "failed", etc.)
            - manifest: App manifest (if available)
            - error: Error message (if status is "failed")

        Example:
            mounted_apps = engine.get_mounted_apps(app)
            for app_info in mounted_apps:
                print(f"App {app_info['slug']} at {app_info['path_prefix']}")
        """
        if app is None:
            # Try to get from engine state if available
            if hasattr(self, "_multi_app_instance"):
                app = self._multi_app_instance
            else:
                raise ValueError("App instance required. Pass app parameter or use " "app.state.mounted_apps directly.")

        mounted_apps = getattr(app.state, "mounted_apps", [])
        return mounted_apps

    # ------------------------------------------------------------------
    # Realtime (Change Stream → WebSocket) for multi-app
    # ------------------------------------------------------------------

    async def _start_multi_app_realtime(
        self,
        parent_app: "FastAPI",
        engine: "MongoDBEngine",
        apps: list[dict[str, Any]],
        mounted_apps: list[dict[str, Any]],
    ) -> None:
        """Start a shared Change Stream watcher across all mounted apps."""
        try:
            from ..realtime import (
                ChangeStreamWatcher,
                RealtimeManager,
                register_realtime,
            )
        except ImportError:
            return

        collection_map: dict[str, tuple[str, str]] = {}
        watched: dict[str, set[str]] = {}

        for ma in mounted_apps:
            if ma.get("status") != "mounted":
                continue
            slug = ma["slug"]
            manifest_dict = ma.get("manifest", {})
            collections_cfg = manifest_dict.get("collections", {})
            for name, cfg in collections_cfg.items():
                if cfg.get("realtime", False):
                    physical = f"{slug}_{name}"
                    collection_map[physical] = (slug, name)
                    watched.setdefault(slug, set()).add(physical)

        if not collection_map:
            return

        manager = RealtimeManager(collection_map)
        watcher = ChangeStreamWatcher(
            db=engine.connection_manager.mongo_db,
            manager=manager,
            watched_collections=watched,
        )

        parent_app.state.realtime_manager = manager
        parent_app.state.realtime_watcher = watcher

        # Register /ws/realtime on each child app that has realtime collections
        for ma in mounted_apps:
            if ma.get("status") != "mounted":
                continue
            slug = ma["slug"]
            manifest_dict = ma.get("manifest", {})
            collections_cfg = manifest_dict.get("collections", {})
            child_app = ma.get("app")
            if child_app:
                register_realtime(child_app, slug, collections_cfg, manager)

        await watcher.start()
        logger.info(
            "Multi-app realtime watcher started for %d collection(s)",
            len(collection_map),
        )

    @staticmethod
    async def _stop_multi_app_realtime(parent_app: "FastAPI") -> None:
        """Stop the shared realtime watcher."""
        watcher = getattr(parent_app.state, "realtime_watcher", None)
        if watcher is not None:
            try:
                await watcher.stop()
            except (OSError, RuntimeError) as e:
                logger.warning(f"Error stopping multi-app realtime watcher: {e}")

    async def _initialize_shared_user_pool(
        self,
        app: "FastAPI",
        manifest: dict[str, Any] | None = None,
    ) -> None:
        """
        Initialize shared user pool, audit log, and set them on app.state.

        Called during lifespan startup for apps using "shared" auth mode.
        The lazy middleware (added at app creation time) will read the
        user_pool from app.state at request time.

        Security Features:
        - JWT secret required (fails fast if not configured)
        - allow_insecure_dev mode for local development only
        - Audit logging for compliance and forensics

        Args:
            app: FastAPI application instance
            manifest: Optional manifest dict for seeding demo users
        """
        from ..auth.audit import AuthAuditLog
        from ..auth.shared_users import SharedUserPool

        # Determine if we're in development mode
        # Development = allow insecure auto-generated JWT secret
        is_dev = (
            os.getenv("MDB_ENGINE_ENV", "").lower() in ("dev", "development", "local")
            or os.getenv("ENVIRONMENT", "").lower() in ("dev", "development", "local")
            or os.getenv("DEBUG", "").lower() in ("true", "1", "yes")
        )

        # Thread-safe initialization with async lock to prevent race conditions
        async with self._shared_user_pool_lock:
            # Check if another coroutine is initializing
            if self._shared_user_pool_initializing:
                # Wait for other initialization to complete
                while self._shared_user_pool_initializing:
                    await asyncio.sleep(0.01)  # Small delay to avoid busy-waiting
                # After waiting, check if pool was initialized
                if hasattr(self, "_shared_user_pool") and self._shared_user_pool is not None:
                    app.state.user_pool = self._shared_user_pool
                    return

            # Check if already initialized (double-check pattern)
            if hasattr(self, "_shared_user_pool") and self._shared_user_pool is not None:
                app.state.user_pool = self._shared_user_pool
                return

            # Mark as initializing
            self._shared_user_pool_initializing = True
            try:
                # Create shared user pool
                self._shared_user_pool = SharedUserPool(
                    self._connection_manager.mongo_db,
                    allow_insecure_dev=is_dev,
                    websocket_session_manager=self._websocket_session_manager,
                )
                await self._shared_user_pool.ensure_indexes()
                logger.info("SharedUserPool initialized")

                # Expose user pool on app.state for middleware to access
                app.state.user_pool = self._shared_user_pool
            finally:
                # Always clear the initializing flag
                self._shared_user_pool_initializing = False

        # Seed demo users to SharedUserPool if configured in manifest
        if manifest:
            auth_config = manifest.get("auth", {})
            users_config = auth_config.get("users", {})
            demo_users = users_config.get("demo_users", [])

            if demo_users and users_config.get("demo_user_seed_strategy", "auto") != "disabled":
                for demo in demo_users:
                    try:
                        email = demo.get("email")
                        password = demo.get("password")
                        app_roles = demo.get("app_roles", {})

                        existing = await self._shared_user_pool.get_user_by_email(email)

                        if not existing:
                            await self._shared_user_pool.create_user(
                                email=email,
                                password=password,
                                app_roles=app_roles,
                            )
                            logger.info(f"Created shared demo user: {email}")
                        else:
                            logger.debug(f"Shared demo user exists: {email}")
                    except (ValueError, TypeError, RuntimeError, AttributeError, KeyError) as e:
                        logger.warning(f"Failed to create shared demo user {demo.get('email')}: {e}")

        # Initialize audit logging if enabled
        auth_config = (manifest or {}).get("auth", {})
        audit_config = auth_config.get("audit", {})
        audit_enabled = audit_config.get("enabled", True)  # Default: enabled for shared auth

        if audit_enabled:
            retention_days = audit_config.get("retention_days", 90)
            if not hasattr(self, "_auth_audit_log") or self._auth_audit_log is None:
                self._auth_audit_log = AuthAuditLog(
                    self._connection_manager.mongo_db,
                    retention_days=retention_days,
                )
                await self._auth_audit_log.ensure_indexes()
                logger.info(f"AuthAuditLog initialized (retention: {retention_days} days)")

            app.state.audit_log = self._auth_audit_log

        logger.info("SharedUserPool and AuditLog attached to app.state")
