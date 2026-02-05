"""
Redaction Service Module

Standalone redaction service for protecting sensitive data (PII) in text.
Supports multiple providers: regexp (default) and Microsoft Presidio.

Key Features:
- **Standalone Operation**: Can be used independently of memory service
- **Multiple Providers**: REGEXP (default) and Microsoft Presidio
- **Base Class Architecture**: Extensible for custom providers
- **Configurable**: Pattern-based (regexp) or entity-based (presidio) detection
- **Memory Integration**: Optional integration with memory service (disabled by default)

Usage:
    # Standalone usage
    from mdb_engine.redaction import get_redaction_service

    redactor = get_redaction_service(config={
        "provider": "presidio",
        "entities": ["NAME", "PHONE_NUMBER", "EMAIL_ADDRESS"]
    })

    text = "John Doe's phone is 123-456-7890 and email is john@example.com"
    redacted = redactor.redact(text)

    # With memory service (opt-in)
    # Configure in manifest.json:
    # {
    #   "memory_config": {
    #     "redaction": {
    #       "enabled": true,
    #       "provider": "regexp"
    #     }
    #   }
    # }
"""

# Import base classes
from .base import BaseRedactionService, RedactionServiceError

# Import implementations
from .presidio import PresidioRedactionService
from .regexp import RegexpRedactionService

# Import factory function
from .service import create_redaction_service, get_redaction_service

__all__ = [
    # Base classes
    "BaseRedactionService",
    "RedactionServiceError",
    # Implementations
    "RegexpRedactionService",
    "PresidioRedactionService",
    # Factory functions
    "get_redaction_service",
    "create_redaction_service",  # Backward compatibility alias
]
