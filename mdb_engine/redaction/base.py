"""
Base Redaction Service Interface

Abstract base class for redaction service implementations.
This allows for extensibility with different redaction providers (regexp, presidio, etc.)
while maintaining a consistent API.

This module is part of MDB_ENGINE - MongoDB Engine.
"""

from abc import ABC, abstractmethod
from typing import Any


class RedactionServiceError(Exception):
    """Base exception for all redaction service errors."""

    pass


class BaseRedactionService(ABC):
    """
    Abstract base class for redaction service implementations.

    This class defines the interface that all redaction service implementations must follow.
    Concrete implementations (e.g., RegexpRedactionService, PresidioRedactionService)
    inherit from this class and implement the abstract methods.

    All redaction operations are designed to protect sensitive data before it reaches
    LLMs for fact extraction or is stored in memory systems.
    """

    def __init__(self, config: dict[str, Any] | None = None):
        """
        Initialize the base redaction service.

        Args:
            config: Configuration dictionary (provider-specific)
        """
        self.config = config or {}
        self.enabled = self.config.get("enabled", True)

    @abstractmethod
    def redact(self, text: str) -> str:
        """
        Redact sensitive information from text.

        Args:
            text: Input text to redact

        Returns:
            Text with sensitive information replaced
        """
        pass

    def redact_dict(self, data: dict[str, Any], fields: list[str] | None = None) -> dict[str, Any]:
        """
        Redact sensitive information from dictionary values.

        Default implementation that recursively processes dictionaries and lists.
        Subclasses can override for provider-specific optimizations.

        Args:
            data: Dictionary to redact
            fields: Specific fields to redact (None = all string fields)

        Returns:
            Dictionary with sensitive information redacted
        """
        if not self.enabled:
            return data

        result = {}
        for key, value in data.items():
            if fields is not None and key not in fields:
                result[key] = value
            elif isinstance(value, str):
                result[key] = self.redact(value)
            elif isinstance(value, dict):
                result[key] = self.redact_dict(value, fields)
            elif isinstance(value, list):
                result[key] = [
                    self.redact(item)
                    if isinstance(item, str)
                    else self.redact_dict(item, fields)
                    if isinstance(item, dict)
                    else item
                    for item in value
                ]
            else:
                result[key] = value

        return result

    @abstractmethod
    def test_redaction(self, text: str) -> dict[str, Any]:
        """
        Test redaction on text without modifying it.

        Args:
            text: Text to test

        Returns:
            Dictionary with test results including what would be redacted
        """
        pass

    @abstractmethod
    def get_stats(self) -> dict[str, Any]:
        """
        Get redaction service statistics.

        Returns:
            Dictionary with service stats
        """
        pass
