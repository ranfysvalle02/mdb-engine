"""
Resource limiting for MongoDB Engine.

This module provides resource limit enforcement to prevent resource exhaustion
and ensure fair resource usage across applications.

Features:
- Query timeout enforcement
- Result size limits
- Document size validation
- Connection limit tracking
"""

import logging
from typing import Any, Dict, Optional

from bson import encode as bson_encode
from bson.errors import InvalidDocument

from ..constants import (
    DEFAULT_MAX_TIME_MS,
    MAX_CURSOR_BATCH_SIZE,
    MAX_DOCUMENT_SIZE,
    MAX_QUERY_RESULT_SIZE,
    MAX_QUERY_TIME_MS,
)
from ..exceptions import ResourceLimitExceeded

logger = logging.getLogger(__name__)


class ResourceLimiter:
    """
    Enforces resource limits on MongoDB operations.

    This class provides resource limit enforcement to prevent:
    - Query timeouts
    - Excessive result sizes
    - Oversized documents
    - Resource exhaustion
    """

    def __init__(
        self,
        default_timeout_ms: int = DEFAULT_MAX_TIME_MS,
        max_timeout_ms: int = MAX_QUERY_TIME_MS,
        max_result_size: int = MAX_QUERY_RESULT_SIZE,
        max_batch_size: int = MAX_CURSOR_BATCH_SIZE,
        max_document_size: int = MAX_DOCUMENT_SIZE,
    ):
        """
        Initialize the resource limiter.

        Args:
            default_timeout_ms: Default query timeout in milliseconds
            max_timeout_ms: Maximum allowed query timeout in milliseconds
            max_result_size: Maximum number of documents in a result set
            max_batch_size: Maximum batch size for cursor operations
            max_document_size: Maximum document size in bytes
        """
        self.default_timeout_ms = default_timeout_ms
        self.max_timeout_ms = max_timeout_ms
        self.max_result_size = max_result_size
        self.max_batch_size = max_batch_size
        self.max_document_size = max_document_size

    def enforce_query_timeout(
        self, kwargs: Dict[str, Any], default_timeout: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Enforce query timeout by adding maxTimeMS if not present.

        Args:
            kwargs: Query keyword arguments
            default_timeout: Default timeout to use (defaults to self.default_timeout_ms)

        Returns:
            Updated kwargs with maxTimeMS added if needed
        """
        kwargs = dict(kwargs)  # Create a copy to avoid mutating original

        default = default_timeout if default_timeout is not None else self.default_timeout_ms

        # Check if maxTimeMS is already set
        if "maxTimeMS" in kwargs:
            user_timeout = kwargs["maxTimeMS"]
            # Validate user-provided timeout doesn't exceed maximum
            if user_timeout > self.max_timeout_ms:
                logger.warning(
                    f"Query timeout {user_timeout}ms exceeds maximum {self.max_timeout_ms}ms. "
                    f"Capping to {self.max_timeout_ms}ms"
                )
                kwargs["maxTimeMS"] = self.max_timeout_ms
        else:
            # Add default timeout
            kwargs["maxTimeMS"] = default

        return kwargs

    def enforce_result_limit(self, limit: Optional[int], max_limit: Optional[int] = None) -> int:
        """
        Enforce maximum result limit.

        Args:
            limit: Requested limit (None means no limit)
            max_limit: Maximum allowed limit (defaults to self.max_result_size)

        Returns:
            Enforced limit value (capped to maximum if needed)
        """
        max_allowed = max_limit if max_limit is not None else self.max_result_size

        if limit is None:
            # No limit requested, return max allowed
            return max_allowed

        if limit > max_allowed:
            logger.warning(
                f"Result limit {limit} exceeds maximum {max_allowed}. " f"Capping to {max_allowed}"
            )
            return max_allowed

        return limit

    def enforce_batch_size(self, batch_size: Optional[int], max_batch: Optional[int] = None) -> int:
        """
        Enforce maximum batch size for cursor operations.

        Args:
            batch_size: Requested batch size (None means use default)
            max_batch: Maximum allowed batch size (defaults to self.max_batch_size)

        Returns:
            Enforced batch size
        """
        max_allowed = max_batch if max_batch is not None else self.max_batch_size

        if batch_size is None:
            return max_allowed

        if batch_size > max_allowed:
            logger.warning(
                f"Batch size {batch_size} exceeds maximum {max_allowed}. "
                f"Capping to {max_allowed}"
            )
            return max_allowed

        return batch_size

    def validate_document_size(self, document: Dict[str, Any]) -> None:
        """
        Validate that a document doesn't exceed size limits.

        Uses actual BSON encoding for accurate size calculation.

        Args:
            document: Document to validate

        Raises:
            ResourceLimitExceeded: If document exceeds size limit
        """
        try:
            # Use actual BSON encoding for accurate size
            bson_bytes = bson_encode(document)
            actual_size = len(bson_bytes)

            if actual_size > self.max_document_size:
                raise ResourceLimitExceeded(
                    f"Document size {actual_size} bytes exceeds maximum "
                    f"{self.max_document_size} bytes",
                    limit_type="document_size",
                    limit_value=self.max_document_size,
                    actual_value=actual_size,
                )
        except ResourceLimitExceeded:
            # Re-raise our validation exceptions immediately
            raise
        except InvalidDocument as e:
            # If BSON encoding fails, log warning but don't fail
            # MongoDB will catch this anyway during actual insert
            logger.warning(f"Could not encode document as BSON for size validation: {e}")

    def validate_documents_size(self, documents: list[Dict[str, Any]]) -> None:
        """
        Validate that multiple documents don't exceed size limits.

        Args:
            documents: List of documents to validate

        Raises:
            ResourceLimitExceeded: If any document exceeds size limit
        """
        for idx, doc in enumerate(documents):
            try:
                self.validate_document_size(doc)
            except ResourceLimitExceeded as e:
                # Add document index to error context
                raise ResourceLimitExceeded(
                    f"{e.message} (document index: {idx})",
                    limit_type=e.limit_type,
                    limit_value=e.limit_value,
                    actual_value=e.actual_value,
                    context={**(e.context or {}), "document_index": idx},
                ) from e
