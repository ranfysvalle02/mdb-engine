"""
Redaction Service Factory

Factory function to create redaction service instances based on configuration.
Auto-selects the appropriate provider (regexp or presidio).
"""

import logging
from typing import Any

from .base import BaseRedactionService, RedactionServiceError
from .presidio import PresidioRedactionService
from .regexp import RegexpRedactionService

logger = logging.getLogger(__name__)


def get_redaction_service(config: dict[str, Any] | None = None) -> BaseRedactionService:
    """
    Factory function to create a redaction service instance.

    Auto-selects provider based on configuration:
    - "presidio": Uses PresidioRedactionService (requires presidio-analyzer, presidio-anonymizer)
    - "regexp" or default: Uses RegexpRedactionService (default, backward compatible)

    Args:
        config: Redaction configuration dictionary with:
            - provider: str - Provider name ("regexp" or "presidio", default: "regexp")
            - enabled: bool - Enable/disable redaction (default: True)
            - Other provider-specific configuration

    Returns:
        BaseRedactionService instance (RegexpRedactionService or PresidioRedactionService)

    Raises:
        RedactionServiceError: If provider is not supported or initialization fails

    Example:
        # Default regexp provider
        redactor = get_redaction_service(config={
            "enabled": True,
            "replacement": "[REDACTED]",
            "patterns": {"ssn": True, "credit_card": True}
        })

        # Presidio provider
        redactor = get_redaction_service(config={
            "provider": "presidio",
            "entities": ["NAME", "PHONE_NUMBER", "EMAIL_ADDRESS"]
        })

        # Use standalone
        text = "John Doe's phone is 123-456-7890"
        redacted = redactor.redact(text)
    """
    if config is None:
        config = {}

    provider = config.get("provider", "regexp").lower()

    if provider == "presidio":
        try:
            return PresidioRedactionService(config=config)
        except RedactionServiceError:
            logger.exception("Failed to create PresidioRedactionService")
            raise
        except (
            AttributeError,
            TypeError,
            ValueError,
            RuntimeError,
            OSError,
            ImportError,
        ) as e:
            raise RedactionServiceError(
                f"Unexpected error creating PresidioRedactionService: {e}"
            ) from e

    elif provider == "regexp":
        return RegexpRedactionService(config=config)

    else:
        raise RedactionServiceError(
            f"Unsupported redaction provider: {provider}. " f"Supported providers: regexp, presidio"
        )


# Backward compatibility alias
def create_redaction_service(config: dict[str, Any] | None = None) -> BaseRedactionService:
    """
    Factory function to create a RedactionService (backward compatibility).

    This is an alias for get_redaction_service() maintained for backward compatibility.

    Args:
        config: Redaction configuration from manifest

    Returns:
        Configured BaseRedactionService instance
    """
    return get_redaction_service(config=config)
