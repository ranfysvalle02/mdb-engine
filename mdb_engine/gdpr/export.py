"""
Data Export Service

Exports user data in various formats for GDPR compliance (Right to Access).

This module is part of MDB_ENGINE - MongoDB Engine.
"""

import logging
from datetime import datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import PyMongoError

from .discovery import DataDiscoveryService
from .helpers import serialize_document

logger = logging.getLogger(__name__)


class DataExportService:
    """
    Service for exporting user data in various formats.

    Supports JSON export (primary) with optional CSV and report formats.
    """

    def __init__(self, db: AsyncIOMotorDatabase):
        """
        Initialize data export service.

        Args:
            db: MongoDB database instance
        """
        self.db = db
        self.discovery_service = DataDiscoveryService(db)

    async def export_user_data(
        self,
        user_identifier: str,
        identifier_type: str = "email",
        app_slug: str | None = None,
        format: str = "json",
    ) -> dict[str, Any]:
        """
        Export all user data.

        Args:
            user_identifier: User email or user_id
            identifier_type: Type of identifier ("email" or "user_id")
            app_slug: Optional app slug to scope export
            format: Export format ("json", "csv", "report")

        Returns:
            Dictionary with exported data and metadata
        """
        # Discover collections with user data
        collections = await self.discovery_service.discover_user_collections(
            user_identifier, identifier_type, app_slug
        )

        export_data = {}
        total_documents = 0

        # Build query
        from .helpers import build_user_query

        query = build_user_query(user_identifier, identifier_type)

        # Export data from each collection
        for collection_info in collections:
            collection_name = collection_info["name"]
            try:
                collection = self.db[collection_name]

                # Find all matching documents
                cursor = collection.find(query)
                documents = await cursor.to_list(length=None)

                # Serialize documents for JSON
                serialized_docs = [serialize_document(doc) for doc in documents]

                export_data[collection_name] = serialized_docs
                total_documents += len(serialized_docs)

                logger.debug(
                    f"Exported {len(serialized_docs)} documents from "
                    f"collection '{collection_name}'"
                )
            except PyMongoError:
                logger.exception(f"Error exporting from collection '{collection_name}'")
                export_data[collection_name] = []
                continue

        # Format export based on requested format
        if format == "json":
            return {
                "data": export_data,
                "metadata": {
                    "export_date": datetime.utcnow().isoformat(),
                    "format": "json",
                    "collections": list(export_data.keys()),
                    "total_documents": total_documents,
                    "user_identifier": user_identifier,
                    "identifier_type": identifier_type,
                    "app_slug": app_slug,
                },
            }
        elif format == "csv":
            # CSV format - convert each collection to CSV
            csv_data = {}
            for collection_name, documents in export_data.items():
                if not documents:
                    csv_data[collection_name] = ""
                    continue

                # Get all unique keys from all documents
                all_keys = set()
                for doc in documents:
                    all_keys.update(doc.keys())

                # Create CSV header
                headers = sorted(all_keys)
                csv_lines = [",".join(headers)]

                # Create CSV rows
                for doc in documents:
                    row = [str(doc.get(key, "")) for key in headers]
                    csv_lines.append(",".join(row))

                csv_data[collection_name] = "\n".join(csv_lines)

            return {
                "data": csv_data,
                "metadata": {
                    "export_date": datetime.utcnow().isoformat(),
                    "format": "csv",
                    "collections": list(csv_data.keys()),
                    "total_documents": total_documents,
                    "user_identifier": user_identifier,
                    "identifier_type": identifier_type,
                    "app_slug": app_slug,
                },
            }
        else:  # report format - human-readable
            report_lines = [
                "GDPR Data Export Report",
                f"Generated: {datetime.utcnow().isoformat()}",
                f"User Identifier: {user_identifier} ({identifier_type})",
                f"App Slug: {app_slug or 'All Apps'}",
                "",
                f"Total Collections: {len(export_data)}",
                f"Total Documents: {total_documents}",
                "",
                "=" * 80,
                "",
            ]

            for collection_name, documents in export_data.items():
                report_lines.append(f"Collection: {collection_name}")
                report_lines.append(f"  Documents: {len(documents)}")
                report_lines.append("")

                # Show sample of first document
                if documents:
                    report_lines.append("  Sample Document:")
                    import json

                    sample = json.dumps(documents[0], indent=2)
                    for line in sample.split("\n"):
                        report_lines.append(f"    {line}")
                    report_lines.append("")

            report_text = "\n".join(report_lines)

            return {
                "data": export_data,
                "report": report_text,
                "metadata": {
                    "export_date": datetime.utcnow().isoformat(),
                    "format": "report",
                    "collections": list(export_data.keys()),
                    "total_documents": total_documents,
                    "user_identifier": user_identifier,
                    "identifier_type": identifier_type,
                    "app_slug": app_slug,
                },
            }
