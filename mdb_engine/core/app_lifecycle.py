"""
App Lifecycle Mixin

Provides methods for manifest validation, app registration, and app management.
"""

import logging
import secrets
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .app_registration import AppRegistrationManager
    from .app_secrets import AppSecretsManager
    from .index_management import IndexManager
    from .reconciler import Reconciler
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
    _reconciler: "Reconciler | None"

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
                # Inject node_types and categories for auto-scaffold fallback
                graph_config = manifest.get("graph_config", {})
                memory_config = manifest.get("memory_config", {})
                enriched = dict(osi_config)
                enriched["_graph_node_types"] = graph_config.get("node_types", [])
                categories_config = memory_config.get("categories", {})
                if isinstance(categories_config, dict):
                    enriched["_memory_categories"] = categories_config.get("custom_categories", [])

                # Resolve models_path relative to the manifest file's directory
                # so OSI loader/watcher/store/scaffold all use the correct location.
                source_path = manifest.get("_source_path")
                if source_path:
                    manifest_dir = Path(source_path).parent
                    models_path = enriched.get("models_path")
                    if models_path and not Path(models_path).is_absolute():
                        enriched["models_path"] = str((manifest_dir / models_path).resolve())
                    elif not models_path:
                        # Auto-discover semantic_models/ alongside the manifest
                        from ..osi.loader import auto_discover_models

                        discovered = auto_discover_models(source_path)
                        if discovered:
                            enriched["models_path"] = discovered

                await self._service_initializer.initialize_osi_service(slug, enriched)

        async def ensure_shared_services_callback(slug: str, _manifest: dict[str, Any]) -> None:
            """Pre-create LLM and embedding services so graph + memory share them."""
            if self._service_initializer:
                raw_mem = _manifest.get("memory_config")
                self._service_initializer._ensure_shared_services(  # noqa: SLF001
                    slug,
                    llm_config=_manifest.get("llm_config"),
                    embedding_config=_manifest.get("embedding_config"),
                    memory_config=raw_mem if isinstance(raw_mem, dict) else None,
                )

        async def initialize_graph_callback(slug: str, graph_config: dict[str, Any]) -> None:
            if self._service_initializer:
                llm_config = manifest.get("llm_config")
                await self._service_initializer.initialize_graph_service(slug, graph_config, llm_config=llm_config)

        async def initialize_memory_callback(slug: str, memory_config: Any) -> None:
            if self._service_initializer:
                # Pass llm_config from manifest so services can inherit the LLM model
                # memory_config may be True, a preset string, or a dict
                llm_config = manifest.get("llm_config")
                raw_config = manifest.get("memory_config")
                await self._service_initializer.initialize_memory_service(
                    slug, raw_config if raw_config is not None else memory_config, llm_config=llm_config
                )

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

        async def reconciler_callback(
            slug: str,
            desired_manifest: dict[str, Any],
            prev_manifest: dict[str, Any] | None,
        ) -> dict[str, Any]:
            """Run the manifest reconciler before any service callbacks fire.

            The reconciler persists ``apps_config`` inside its per-slug
            lock when ``persist_manifest=True`` (default), so the ledger,
            revision history, and ``apps_config._applied_hash`` all
            advance atomically. The caller inspects
            ``result["persisted_apps_config"]`` to know whether it
            still needs to persist on its own.
            """
            reconciler = getattr(self, "_reconciler", None)
            if reconciler is None:
                return {"status": "skipped", "reason": "reconciler_disabled"}
            prev_hash = None
            if prev_manifest:
                prev_hash = prev_manifest.get("_applied_hash")
            try:
                plan = await reconciler.plan(
                    slug,
                    desired_manifest,
                    prev_hash=prev_hash,
                    prev_manifest=prev_manifest,
                )
            except ValueError as e:
                # Reserved-name / invalid-rename guard tripped.
                logger.exception(f"[{slug}] Reconciler: manifest rejected: {e}")
                return {"status": "invalid_manifest", "reason": str(e), "reconciled_indexes": False}
            result = await reconciler.apply(
                plan,
                manifest=desired_manifest,
                applied_by="register_app",
                persist_manifest=True,
            )
            # Mirror the applied metadata on the in-memory dict so
            # downstream callbacks see the fresh hash/revision. The
            # durable copy on disk was already written inside the lock.
            desired_manifest["_applied_hash"] = plan.to_hash
            desired_manifest["_applied_schema_hash"] = plan.schema_hash
            if result.get("revision") and isinstance(result["revision"], dict):
                desired_manifest["_applied_revision"] = result["revision"].get("revision")
            result["reconciled_indexes"] = not plan.is_noop
            # Inform the registration manager that apps_config has
            # already been persisted atomically alongside the revision.
            result["persisted_apps_config"] = result.get("status") in ("applied", "noop")
            return result

        # Register app first (this validates and stores the manifest)
        result = await self._app_registration_manager.register_app(
            manifest=manifest,
            create_indexes_callback=create_indexes_callback if create_indexes else None,
            seed_data_callback=seed_data_callback,
            initialize_osi_callback=initialize_osi_callback,
            ensure_shared_services_callback=ensure_shared_services_callback,
            initialize_graph_callback=initialize_graph_callback,
            initialize_memory_callback=initialize_memory_callback,
            initialize_profile_callback=initialize_profile_callback,
            register_websockets_callback=register_websockets_callback,
            setup_observability_callback=setup_observability_callback,
            reconciler_callback=reconciler_callback,
        )

        # Initialize Perfect Brain (nested inside memory_config)
        slug_for_brain = manifest.get("slug")
        mem_cfg = manifest.get("memory_config")
        perfect_brain_config = mem_cfg.get("perfect_brain") if isinstance(mem_cfg, dict) else None
        if (
            self._service_initializer
            and slug_for_brain
            and isinstance(perfect_brain_config, dict)
            and perfect_brain_config.get("enabled", False)
        ):
            try:
                await self._service_initializer.initialize_perfect_brain(slug_for_brain, perfect_brain_config)
            except (ImportError, AttributeError, TypeError, ValueError, RuntimeError) as e:
                logger.warning(f"Failed to initialize PerfectBrain for '{slug_for_brain}': {e}")

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

    # ------------------------------------------------------------------
    # Manifest reconciler API (manifest history, trash, replay)
    # ------------------------------------------------------------------

    def _require_reconciler(self):
        reconciler = getattr(self, "_reconciler", None)
        if reconciler is None:
            raise RuntimeError(
                "Manifest reconciler is not available. Ensure the engine was "
                "initialized successfully and pymongo is installed."
            )
        return reconciler

    def admin_router(self, cfg: "dict[str, Any] | None" = None):
        """Return a freshly-built admin plane FastAPI router.

        Useful for custom mounts (e.g. mounting under a non-default
        prefix on a multi-app host). ``create_app`` auto-mounts this
        router at ``/__mdb`` when the manifest's top-level
        ``admin_api.enabled`` is true (default: false).

        Pass ``cfg`` to override per-module enable/scope flags;
        defaults come from the manifest that was used when building
        this engine's :class:`AdminSurface`.
        """
        surface = self.admin_surface(cfg)  # type: ignore[attr-defined]
        return surface.build_router()

    async def reconcile(
        self,
        slug: str,
        manifest: "ManifestDict | None" = None,
        *,
        dry_run: bool = False,
        force: bool = False,
        confirm: bool = False,
        expected_head: str | None = None,
        caused_by_commit: str | None = None,
        caused_by_user: str | None = None,
    ) -> dict[str, Any]:
        """Run the manifest reconciler for a slug.

        Args:
            slug: App slug to reconcile.
            manifest: Optional manifest to reconcile against. When ``None``,
                the currently-persisted manifest in ``apps_config`` is used.
            dry_run: When True, returns the computed plan without applying.
            force: When True, runs reconciliation even if the manifest hash
                matches the last-applied hash (useful for recovery).
            confirm: When True, bypass ``manifest_tracking.confirm_if``
                gates. Equivalent to ``MDB_CONFIRM=1`` / CLI ``--yes``.
            expected_head: Optional revision hash the caller expects to
                still be HEAD. When supplied and the stored HEAD does
                not match, returns ``status="drift"`` and refuses to
                apply (TOCTOU guard for GitOps pipelines).
            caused_by_commit: Optional git SHA to record on the revision
                and on tombstones for any destructive ops.
            caused_by_user: Optional operator identity for the revision.

        Returns:
            Dict with keys ``status`` (``applied``, ``noop``, ``dry_run``,
            ``locked``, ``drift``, ``confirmation_required``, or
            ``invalid_manifest``), ``plan``, ``revision`` (if applied).
        """
        reconciler = self._require_reconciler()
        if not self._app_registration_manager:
            raise RuntimeError("MongoDBEngine not initialized. Call initialize() first.")

        db = self._app_registration_manager._mongo_db  # noqa: SLF001
        prev = await db.apps_config.find_one({"slug": slug})
        desired = manifest if manifest is not None else prev
        if desired is None:
            raise ValueError(f"No manifest available for slug={slug!r}")

        if expected_head is not None:
            head = await reconciler.head_revision(slug)
            actual = (head or {}).get("hash") if head else (prev or {}).get("_applied_hash")
            if actual != expected_head:
                return {
                    "status": "drift",
                    "expected_head": expected_head,
                    "actual_head": actual,
                }

        prev_hash = None if force else (prev or {}).get("_applied_hash")
        try:
            plan = await reconciler.plan(slug, desired, prev_hash=prev_hash, prev_manifest=prev)
        except ValueError as e:
            return {"status": "invalid_manifest", "reason": str(e)}
        return await reconciler.apply(
            plan,
            manifest=desired,
            applied_by="engine.reconcile",
            dry_run=dry_run,
            confirm=confirm,
            caused_by_commit=caused_by_commit,
            caused_by_user=caused_by_user,
        )

    async def manifest_history(self, slug: str, *, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent manifest revisions for an app, newest first."""
        reconciler = self._require_reconciler()
        return await reconciler.get_history(slug, limit=limit)

    async def manifest_head(self, slug: str) -> dict[str, Any] | None:
        """Return the most recent revision document for a slug, or ``None``."""
        reconciler = self._require_reconciler()
        return await reconciler.head_revision(slug)

    async def manifest_diff(self, slug: str) -> dict[str, Any]:
        """Return the plan that would be applied right now against the current manifest."""
        reconciler = self._require_reconciler()
        if not self._app_registration_manager:
            raise RuntimeError("MongoDBEngine not initialized. Call initialize() first.")
        db = self._app_registration_manager._mongo_db  # noqa: SLF001
        prev = await db.apps_config.find_one({"slug": slug})
        if prev is None:
            raise ValueError(f"No persisted manifest for slug={slug!r}")
        plan = await reconciler.plan(
            slug,
            prev,
            prev_hash=prev.get("_applied_hash"),
            prev_manifest=prev,
        )
        return plan.to_dict()

    async def manifest_adopt(self, slug: str) -> dict[str, Any]:
        """Seed the reconciler ledger from existing ``<slug>_*`` collections.

        Use this once when upgrading an app that predates the
        reconciler so subsequent reconciles treat pre-existing state
        as the baseline rather than re-adding everything.
        """
        reconciler = self._require_reconciler()
        return await reconciler.adopt(slug)

    async def trash_list(self, slug: str) -> list[dict[str, Any]]:
        reconciler = self._require_reconciler()
        return await reconciler.trash_list(slug)

    async def trash_list_all(self) -> list[dict[str, Any]]:
        """Return trash entries for every slug (admin view)."""
        reconciler = self._require_reconciler()
        return await reconciler.trash_list(None)

    async def trash_summary(self) -> list[dict[str, Any]]:
        """Return per-slug trash roll-ups: count + estimated doc totals."""
        reconciler = self._require_reconciler()
        return await reconciler.trash_summary()

    async def trash_restore(
        self,
        slug: str,
        trash_id: Any,
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Restore a trash entry. When ``dry_run=True``, only previews the restore."""
        reconciler = self._require_reconciler()
        return await reconciler.trash_restore(slug, trash_id, dry_run=dry_run)

    async def trash_restore_plan(self, slug: str, trash_id: Any) -> dict[str, Any]:
        """Return the reconciler's view of whether a trash entry is restorable."""
        reconciler = self._require_reconciler()
        return await reconciler.trash_restore_plan(slug, trash_id)

    async def trash_purge(
        self,
        slug: str,
        *,
        expired_only: bool = True,
        ids: list[Any] | None = None,
    ) -> int:
        reconciler = self._require_reconciler()
        return await reconciler.trash_purge(slug, expired_only=expired_only, ids=ids)

    async def watch_revisions(
        self,
        slug: str,
        callback: "Callable[[dict[str, Any]], Any]",
        *,
        resume_after: Any = None,
    ) -> None:
        """Tail ``_mdb_manifest_revisions`` for a slug.

        ``callback`` may be sync or async; it receives the newly
        inserted revision doc. Blocks until cancelled. Requires the
        database to be a replica set (change streams are not available
        on standalone mongod). See :mod:`mdb_engine.core.reconciler`.
        """
        reconciler = self._require_reconciler()
        await reconciler.watch_revisions(slug, callback, resume_after=resume_after)
