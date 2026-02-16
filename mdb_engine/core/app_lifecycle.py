"""
App Lifecycle Mixin

Provides methods for manifest validation, app registration, and app management.
"""

import logging
import secrets
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .app_registration import AppRegistrationManager
    from .app_secrets import AppSecretsManager
    from .index_management import IndexManager
    from .service_initialization import ServiceInitializer
    from .types import ManifestDict

logger = logging.getLogger(__name__)


class AppLifecycleMixin:
    """Mixin providing app lifecycle management methods.

    Expects the following attributes from the host class (MongoDBEngine):
        _app_registration_manager: Manages app manifest registration (optional).
        _index_manager: Manages MongoDB index creation (optional).
        _service_initializer: Initializes graph/memory/websocket services (optional).
        _app_read_scopes: Mapping of app slugs to authorized read scopes.
        _app_secrets_manager: App secrets manager (optional).
    """

    # -- Attributes provided by MongoDBEngine --
    _app_registration_manager: "AppRegistrationManager | None"
    _index_manager: "IndexManager | None"
    _service_initializer: "ServiceInitializer | None"
    _app_read_scopes: dict[str, list[str]]
    _app_secrets_manager: "AppSecretsManager | None"

    async def validate_manifest(self, manifest: "ManifestDict") -> tuple[bool, str | None, list[str] | None]:
        """
        Validate a manifest against the schema.

        Args:
            manifest: Manifest dictionary to validate. Must be a valid
                dictionary containing experiment configuration.

        Returns:
            Tuple of (is_valid, error_message, error_paths):
            - is_valid: True if manifest is valid, False otherwise
            - error_message: Human-readable error message if invalid, None if valid
            - error_paths: List of JSON paths with validation errors, None if valid
        """
        if not self._app_registration_manager:
            raise RuntimeError("MongoDBEngine not initialized. Call initialize() first.")
        return await self._app_registration_manager.validate_manifest(manifest)

    async def load_manifest(self, path: Path) -> "ManifestDict":
        """
        Load and validate a manifest from a file.

        Args:
            path: Path to manifest.json file

        Returns:
            Validated manifest dictionary

        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If validation fails
        """
        if not self._app_registration_manager:
            raise RuntimeError("MongoDBEngine not initialized. Call initialize() first.")
        return await self._app_registration_manager.load_manifest(path)

    async def register_app(self, manifest: "ManifestDict", create_indexes: bool = True) -> bool:
        """
        Register an app from its manifest.

        This method validates the manifest, stores the app configuration,
        and optionally creates managed indexes defined in the manifest.

        Args:
            manifest: Validated manifest dictionary containing app
                configuration. Must include 'slug' field.
            create_indexes: Whether to create managed indexes defined in
                the manifest. Defaults to True.

        Returns:
            True if registration successful, False otherwise.
            Returns False if manifest validation fails or slug is missing.

        Raises:
            RuntimeError: If engine is not initialized.
        """
        if not self._app_registration_manager:
            raise RuntimeError("MongoDBEngine not initialized. Call initialize() first.")

        # Create callbacks for service initialization
        async def create_indexes_callback(slug: str, manifest: "ManifestDict") -> None:
            if self._index_manager and create_indexes:
                await self._index_manager.create_app_indexes(slug, manifest)

        async def seed_data_callback(slug: str, initial_data: dict[str, Any]) -> None:
            if self._service_initializer:
                await self._service_initializer.seed_initial_data(slug, initial_data)

        async def initialize_osi_callback(slug: str, osi_config: dict[str, Any]) -> None:
            if self._service_initializer:
                await self._service_initializer.initialize_osi_service(slug, osi_config)

        async def initialize_graph_callback(slug: str, graph_config: dict[str, Any]) -> None:
            if self._service_initializer:
                # Pass llm_config from manifest so services can inherit the LLM model
                llm_config = manifest.get("llm_config")
                await self._service_initializer.initialize_graph_service(slug, graph_config, llm_config=llm_config)

        async def initialize_memory_callback(slug: str, memory_config: dict[str, Any]) -> None:
            if self._service_initializer:
                # Pass llm_config from manifest so services can inherit the LLM model
                llm_config = manifest.get("llm_config")
                await self._service_initializer.initialize_memory_service(slug, memory_config, llm_config=llm_config)

        async def initialize_profile_callback(slug: str, profile_config: dict[str, Any]) -> None:
            if self._service_initializer:
                llm_config = manifest.get("llm_config")
                await self._service_initializer.initialize_profile_service(slug, profile_config, llm_config=llm_config)

        async def register_websockets_callback(slug: str, websockets_config: dict[str, Any]) -> None:
            if self._service_initializer:
                await self._service_initializer.register_websockets(slug, websockets_config)

        async def setup_observability_callback(
            slug: str,
            manifest: "ManifestDict",
            observability_config: dict[str, Any],
        ) -> None:
            if self._service_initializer:
                await self._service_initializer.setup_observability(slug, manifest, observability_config)

        # Register app first (this validates and stores the manifest)
        result = await self._app_registration_manager.register_app(
            manifest=manifest,
            create_indexes_callback=create_indexes_callback if create_indexes else None,
            seed_data_callback=seed_data_callback,
            initialize_osi_callback=initialize_osi_callback,
            initialize_graph_callback=initialize_graph_callback,
            initialize_memory_callback=initialize_memory_callback,
            initialize_profile_callback=initialize_profile_callback,
            register_websockets_callback=register_websockets_callback,
            setup_observability_callback=setup_observability_callback,
        )

        # Configure prompt safety policy from manifest
        prompt_safety_config = manifest.get("prompt_safety")
        if prompt_safety_config:
            try:
                from .prompt_safety import configure as configure_prompt_safety
                from .prompt_safety import policy_from_manifest

                policy = policy_from_manifest(prompt_safety_config)
                configure_prompt_safety(policy)
            except (ImportError, ValueError, TypeError) as e:
                logger.warning(f"Failed to configure prompt safety: {e}")

        # Extract and store data_access configuration AFTER registration
        slug = manifest.get("slug")
        if slug:
            data_access = manifest.get("data_access", {})
            read_scopes = data_access.get("read_scopes")
            if read_scopes:
                self._app_read_scopes[slug] = read_scopes
            else:
                # Default to app_slug if not specified
                self._app_read_scopes[slug] = [slug]

            # Generate and store app secret if secrets manager is available
            if self._app_secrets_manager:
                # Check if secret already exists (don't overwrite)
                secret_exists = await self._app_secrets_manager.app_secret_exists(slug)
                if not secret_exists:
                    app_secret = secrets.token_urlsafe(32)
                    await self._app_secrets_manager.store_app_secret(slug, app_secret)
                    logger.info(
                        f"Generated and stored encrypted secret for app '{slug}'. "
                        "Store this secret securely and provide it as app_token in get_scoped_db()."
                    )
                    # Note: In production, the secret should be retrieved via rotation API
                    # For now, we log it (in production, this should be handled differently)

        return result

    async def reload_apps(self) -> int:
        """
        Reload all active apps from the database.

        This method fetches all apps with status "active" from the
        apps_config collection and registers them. Existing
        app registrations are cleared before reloading.

        Returns:
            Number of apps successfully registered.
            Returns 0 if an error occurs during reload.

        Raises:
            RuntimeError: If engine is not initialized.
        """
        if not self._app_registration_manager:
            raise RuntimeError("MongoDBEngine not initialized. Call initialize() first.")

        return await self._app_registration_manager.reload_apps(register_app_callback=self.register_app)

    def get_app(self, slug: str) -> Optional["ManifestDict"]:
        """
        Get app configuration by slug.

        Args:
            slug: App slug

        Returns:
            App manifest dict or None if not found
        """
        if not self._app_registration_manager:
            raise RuntimeError("MongoDBEngine not initialized. Call initialize() first.")
        return self._app_registration_manager.get_app(slug)

    async def get_manifest(self, slug: str) -> Optional["ManifestDict"]:
        """
        Get app manifest by slug (async alias for get_app).

        Args:
            slug: App slug

        Returns:
            App manifest dict or None if not found
        """
        if not self._app_registration_manager:
            raise RuntimeError("MongoDBEngine not initialized. Call initialize() first.")
        return await self._app_registration_manager.get_manifest(slug)

    def list_apps(self) -> list[str]:
        """
        List all registered app slugs.

        Returns:
            List of app slugs
        """
        if not self._app_registration_manager:
            raise RuntimeError("MongoDBEngine not initialized. Call initialize() first.")
        return self._app_registration_manager.list_apps()

    @property
    def apps(self) -> dict[str, Any]:
        """
        Get all registered apps.

        Returns:
            Dictionary of registered apps

        Raises:
            RuntimeError: If engine is not initialized
        """
        if not self._app_registration_manager:
            raise RuntimeError("MongoDBEngine not initialized. Call initialize() first.")
        return self._app_registration_manager.apps
