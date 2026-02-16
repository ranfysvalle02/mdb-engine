"""
GDPR Operations Mixin

Provides methods for GDPR compliance: data export, deletion, and rectification.
"""

import logging
from typing import TYPE_CHECKING, Any

from pymongo.errors import PyMongoError

if TYPE_CHECKING:
    from .connection import ConnectionManager

logger = logging.getLogger(__name__)


class GDPRMixin:
    """Mixin providing GDPR compliance operations.

    Expects the following attributes from the host class (MongoDBEngine):
        _connection_manager: Database connection manager.
    """

    # -- Attributes provided by MongoDBEngine --
    _connection_manager: "ConnectionManager"

    async def export_user_data(
        self,
        user_identifier: str,
        identifier_type: str = "email",
        app_slug: str | None = None,
        format: str = "json",
    ) -> dict[str, Any]:
        """
        Export all user data for GDPR compliance (Right to Access - Article 15).

        Discovers and exports all user data across collections including:
        - User collections (users, shared_users, user_sessions, token_blacklist)
        - Application data (chat_history, memories, custom collections)
        - Authorization data (casbin_policies, oso_facts, WebSocket sessions)

        Args:
            user_identifier: User email or user_id
            identifier_type: Type of identifier ("email" or "user_id")
            app_slug: Optional app slug to scope export to specific app
            format: Export format ("json", "csv", or "report")

        Returns:
            Dictionary with exported data and metadata:
            - data: Dictionary mapping collection names to lists of documents
            - metadata: Export metadata (date, format, collections, counts)
            - report: Human-readable report (if format="report")

        Example:
            ```python
            # Export user data in JSON format
            export_data = await engine.export_user_data(
                user_identifier="user@example.com",
                identifier_type="email",
                format="json"
            )

            # Export scoped to specific app
            export_data = await engine.export_user_data(
                user_identifier="user123",
                identifier_type="user_id",
                app_slug="my_app"
            )
            ```
        """
        if not self._connection_manager or not self._connection_manager.initialized:
            raise RuntimeError("MongoDBEngine not initialized. Call initialize() first.")

        from ..gdpr import DataExportService

        db = self._connection_manager.mongo_db
        export_service = DataExportService(db)

        return await export_service.export_user_data(
            user_identifier=user_identifier,
            identifier_type=identifier_type,
            app_slug=app_slug,
            format=format,
        )

    async def delete_user_data(
        self,
        user_identifier: str,
        identifier_type: str = "email",
        app_slug: str | None = None,
        anonymize: bool = False,
        soft_delete: bool = False,
    ) -> dict[str, Any]:
        """
        Delete user data for GDPR compliance (Right to Erasure - Article 17).

        Deletes or anonymizes user data across all collections and services.
        Supports three deletion strategies:
        - Hard delete: Permanently removes data (default - GDPR compliant)
        - Soft delete: Marks data as deleted (for legal retention)
        - Anonymization: Replaces identifiers with anonymous values

        Args:
            user_identifier: User email or user_id
            identifier_type: Type of identifier ("email" or "user_id")
            app_slug: Optional app slug to scope deletion to specific app
            anonymize: If True, anonymize instead of delete
            soft_delete: If True, mark as deleted (for legal retention).
                Default is False (hard delete) for GDPR compliance.
                Use True only when legal retention requirements apply.
                Ignored if anonymize=True.

        Returns:
            Dictionary with deletion results:
            - collections_processed: List of collections processed
            - documents_deleted: Count of hard-deleted documents
            - documents_anonymized: Count of anonymized documents
            - documents_soft_deleted: Count of soft-deleted documents
            - errors: List of errors encountered
            - deleted_at: Timestamp of deletion

        Example:
            ```python
            # Hard delete user data (default - GDPR compliant)
            result = await engine.delete_user_data(
                user_identifier="user@example.com",
                identifier_type="email"
            )

            # Soft delete user data (for legal retention requirements)
            result = await engine.delete_user_data(
                user_identifier="user123",
                identifier_type="user_id",
                soft_delete=True
            )

            # Anonymize user data (replace with anonymous identifiers)
            result = await engine.delete_user_data(
                user_identifier="user@example.com",
                anonymize=True
            )
            ```
        """
        if not self._connection_manager or not self._connection_manager.initialized:
            raise RuntimeError("MongoDBEngine not initialized. Call initialize() first.")

        from ..gdpr import DataDeletionService

        db = self._connection_manager.mongo_db
        deletion_service = DataDeletionService(db)

        # Get memory service if app_slug provided
        memory_service = None
        if app_slug:
            memory_service = self.get_memory_service(app_slug)

        # Get chat history collection if app_slug provided
        # Note: ScopedCollectionWrapper implements the same interface as AsyncIOMotorCollection
        # so we can use it directly. The deletion service checks with `is not None` to avoid
        # truthiness issues with collection objects.
        chat_history_collection = None
        if app_slug:
            try:
                scoped_db = await self.get_scoped_db(app_slug)
                # Try to get chat_history collection from scoped db
                # The wrapper works fine - it implements the same interface
                if hasattr(scoped_db, "chat_history"):
                    chat_history_collection = scoped_db.chat_history
                elif hasattr(scoped_db, "__getitem__"):
                    # Fallback to __getitem__ if attribute access doesn't work
                    chat_history_collection = scoped_db["chat_history"]
            except (PyMongoError, AttributeError, TypeError, KeyError) as e:
                logger.warning(f"Could not get chat_history collection: {e}")
                chat_history_collection = None

        return await deletion_service.delete_user_data(
            user_identifier=user_identifier,
            identifier_type=identifier_type,
            app_slug=app_slug,
            anonymize=anonymize,
            soft_delete=soft_delete,
            memory_service=memory_service,
            chat_history_collection=chat_history_collection,
        )

    async def update_user_data(
        self,
        user_identifier: str,
        updates: dict[str, Any],
        identifier_type: str = "email",
        app_slug: str | None = None,
    ) -> dict[str, Any]:
        """
        Update user data for GDPR compliance (Right to Rectification - Article 16).

        Updates user data across all collections containing user information.
        Validates updates before applying and adds audit fields.

        Args:
            user_identifier: User email or user_id
            updates: Dictionary of field updates to apply
            identifier_type: Type of identifier ("email" or "user_id")
            app_slug: Optional app slug to scope updates to specific app

        Returns:
            Dictionary with update results:
            - collections_processed: List of collections updated
            - documents_updated: Count of updated documents
            - errors: List of errors encountered
            - updated_at: Timestamp of update

        Example:
            ```python
            # Update user email across all collections
            result = await engine.update_user_data(
                user_identifier="old@example.com",
                updates={"email": "new@example.com"},
                identifier_type="email"
            )

            # Update multiple fields
            result = await engine.update_user_data(
                user_identifier="user123",
                updates={
                    "name": "New Name",
                    "role": "admin"
                },
                identifier_type="user_id"
            )
            ```
        """
        if not self._connection_manager or not self._connection_manager.initialized:
            raise RuntimeError("MongoDBEngine not initialized. Call initialize() first.")

        from ..gdpr import DataRectificationService

        db = self._connection_manager.mongo_db
        rectification_service = DataRectificationService(db)

        return await rectification_service.update_user_data(
            user_identifier=user_identifier,
            updates=updates,
            identifier_type=identifier_type,
            app_slug=app_slug,
        )
