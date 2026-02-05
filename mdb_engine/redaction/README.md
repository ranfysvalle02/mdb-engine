# Redaction Service

Standalone redaction service for protecting sensitive data (PII) in text before it reaches LLMs or is stored in memory systems.

## Features

- **Standalone Operation**: Can be used independently of memory service
- **Multiple Providers**: REGEXP (default) and Microsoft Presidio
- **Base Class Architecture**: Extensible for custom providers
- **Configurable**: Pattern-based (regexp) or entity-based (presidio) detection
- **Memory Integration**: Optional integration with memory service (disabled by default)

## Quick Start

### Standalone Usage

```python
from mdb_engine.redaction import get_redaction_service

# Default regexp provider
redactor = get_redaction_service(config={
    "enabled": True,
    "replacement": "[REDACTED]",
    "patterns": {
        "ssn": True,
        "credit_card": True,
        "email": False,
        "phone": True
    }
})

text = "My SSN is 123-45-6789 and my email is john@example.com"
redacted = redactor.redact(text)
# Returns: "My SSN is [REDACTED] and my email is john@example.com"
```

### Presidio Provider

```python
from mdb_engine.redaction import get_redaction_service

# Presidio provider (requires: pip install presidio-analyzer presidio-anonymizer)
redactor = get_redaction_service(config={
    "provider": "presidio",
    "replacement": "[REDACTED_PII]",
    "entities": ["NAME", "PHONE_NUMBER", "EMAIL_ADDRESS"],
    "language": "en"
})

text = "John Doe met with Jane Smith. John's phone number is 123-456-7890 and his email is john@example.com."
redacted = redactor.redact(text)
# Returns: "[REDACTED_PII] met with [REDACTED_PII]. [REDACTED_PII]'s phone number is [REDACTED_PII] and his email is [REDACTED_PII]."
```

## Providers

### RegexpRedactionService (Default)

Pattern-based redaction using compiled regex patterns. Fast and lightweight.

**Built-in Patterns:**
- `ssn`: Social Security Numbers (XXX-XX-XXXX)
- `credit_card`: Credit card numbers (13-16 digits)
- `phone`: US phone numbers (various formats)
- `email`: Email addresses
- `ip_address`: IPv4 addresses
- `api_key`: Common API key patterns
- `password`: Password assignments
- `bearer_token`: Bearer tokens
- `aws_key`: AWS Access Keys
- `generic_secret`: Generic secrets

**Configuration:**
```python
{
    "provider": "regexp",  # or omit for default
    "enabled": True,
    "replacement": "[REDACTED]",
    "patterns": {
        "ssn": True,
        "credit_card": True,
        "phone": False,
        "email": False,
        "custom": ["\\bpassword\\s*[:=]\\s*\\S+"]
    },
    "allow_list": ["support@company.com"],
    "log_redactions": True
}
```

### PresidioRedactionService

ML-based redaction using Microsoft Presidio. More accurate but requires additional dependencies.

**Supported Entities:**
- `NAME`: Person names
- `PHONE_NUMBER`: Phone numbers
- `EMAIL_ADDRESS`: Email addresses
- `CREDIT_CARD`: Credit card numbers
- `SSN`: Social Security Numbers
- `IP_ADDRESS`: IP addresses
- `DATE_TIME`: Dates and times
- `LOCATION`: Geographic locations
- And many more (see Presidio documentation)

**Installation:**
```bash
pip install presidio-analyzer presidio-anonymizer
```

**Configuration:**
```python
{
    "provider": "presidio",
    "enabled": True,
    "replacement": "[REDACTED_PII]",
    "entities": ["NAME", "PHONE_NUMBER", "EMAIL_ADDRESS"],
    "language": "en",
    "log_redactions": True
}
```

## API Reference

### BaseRedactionService

Abstract base class that all redaction providers implement.

**Methods:**

- `redact(text: str) -> str`: Redact sensitive information from text
- `redact_dict(data: dict, fields: list[str] | None = None) -> dict`: Redact sensitive information from dictionary values
- `test_redaction(text: str) -> dict`: Test redaction without modifying text
- `get_stats() -> dict`: Get redaction service statistics

### Factory Function

```python
get_redaction_service(config: dict[str, Any] | None = None) -> BaseRedactionService
```

Creates a redaction service instance based on configuration. Auto-selects provider.

## Memory Service Integration

Redaction can be optionally integrated with memory service (disabled by default).

**Enable in manifest.json:**
```json
{
  "memory_config": {
    "redaction": {
      "enabled": true,
      "provider": "regexp",
      "replacement": "[REDACTED]",
      "patterns": {
        "ssn": true,
        "credit_card": true
      }
    }
  }
}
```

When enabled, all text processed by memory service (before LLM fact extraction) will be redacted.

## Extending the Service

Create custom redaction providers by extending `BaseRedactionService`:

```python
from mdb_engine.redaction.base import BaseRedactionService

class CustomRedactionService(BaseRedactionService):
    def __init__(self, config: dict[str, Any] | None = None):
        super().__init__(config)
        # Initialize your provider

    def redact(self, text: str) -> str:
        # Implement redaction logic
        return redacted_text

    def test_redaction(self, text: str) -> dict[str, Any]:
        # Return test results
        return {"matches": [], "would_redact": False}

    def get_stats(self) -> dict[str, Any]:
        # Return service statistics
        return {"provider": "custom"}
```

## Examples

See `examples/basic/redaction_demo/` for a complete demo application.
