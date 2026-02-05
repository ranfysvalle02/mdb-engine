# Redaction Service Demo

Demonstrates the standalone redaction service with REGEXP and Presidio providers.

## Features

- **Standalone Redaction**: Use redaction service independently of memory service
- **REGEXP Provider**: Pattern-based redaction (default, fast and lightweight)
- **Presidio Provider**: ML-based entity detection (requires presidio-analyzer, presidio-anonymizer)
- **Comparison**: Compare how different providers handle the same text
- **Test Mode**: See what would be redacted without modifying text

## Quick Start

### Install Dependencies

```bash
# Basic dependencies (REGEXP provider only)
pip install -r requirements.txt

# For Presidio provider (optional)
pip install presidio-analyzer presidio-anonymizer
```

### Run the Demo

```bash
uvicorn app:app --reload --port 8000
```

Visit http://localhost:8000 for API documentation.

## API Endpoints

### POST /redact
Redact sensitive information from text.

**Request:**
```json
{
  "text": "John Doe's phone is 123-456-7890 and email is john@example.com",
  "provider": "regexp",
  "config": {
    "replacement": "[REDACTED]",
    "patterns": {
      "phone": true,
      "email": true
    }
  }
}
```

**Response:**
```json
{
  "success": true,
  "provider": "regexp",
  "original": "John Doe's phone is 123-456-7890 and email is john@example.com",
  "redacted": "John Doe's phone is [REDACTED] and email is [REDACTED]",
  "changed": true
}
```

### POST /test
Test redaction without modifying text.

**Request:**
```json
{
  "text": "John Doe's phone is 123-456-7890",
  "provider": "presidio"
}
```

**Response:**
```json
{
  "success": true,
  "provider": "presidio",
  "would_redact": true,
  "matches": ["John Doe", "123-456-7890"],
  "entity_matches": {
    "NAME": [{"text": "John Doe", "start": 0, "end": 8, "score": 0.85}],
    "PHONE_NUMBER": [{"text": "123-456-7890", "start": 20, "end": 32, "score": 0.92}]
  }
}
```

### POST /compare
Compare REGEXP vs Presidio providers.

**Request:**
```json
{
  "text": "John Doe met with Jane Smith. John's phone is 123-456-7890 and email is john@example.com"
}
```

**Response:**
```json
{
  "success": true,
  "original_text": "...",
  "providers": {
    "regexp": {
      "available": true,
      "redacted": "...",
      "matches": ["123-456-7890", "john@example.com"],
      "pattern_matches": {...}
    },
    "presidio": {
      "available": true,
      "redacted": "...",
      "matches": ["John Doe", "Jane Smith", "123-456-7890", "john@example.com"],
      "entity_matches": {...}
    }
  }
}
```

### GET /providers
List available providers and their configurations.

### GET /stats
Get redaction service statistics.

### GET /examples/regexp
Example: REGEXP provider usage.

### GET /examples/presidio
Example: Presidio provider usage (if installed).

## Examples

### REGEXP Provider

```python
from mdb_engine.redaction import get_redaction_service

redactor = get_redaction_service(config={
    "provider": "regexp",
    "enabled": True,
    "replacement": "[REDACTED]",
    "patterns": {
        "ssn": True,
        "credit_card": True,
        "phone": True,
        "email": False
    }
})

text = "My SSN is 123-45-6789 and phone is 555-123-4567"
redacted = redactor.redact(text)
```

### Presidio Provider

```python
from mdb_engine.redaction import get_redaction_service

redactor = get_redaction_service(config={
    "provider": "presidio",
    "enabled": True,
    "replacement": "[REDACTED_PII]",
    "entities": ["NAME", "PHONE_NUMBER", "EMAIL_ADDRESS"],
    "language": "en"
})

text = "John Doe's phone is 123-456-7890 and email is john@example.com"
redacted = redactor.redact(text)
```

## Standalone Usage

The redaction service can be used independently:

```python
from mdb_engine.redaction import get_redaction_service

# No MDB-Engine initialization needed!
redactor = get_redaction_service(config={
    "provider": "regexp",
    "patterns": {"ssn": True}
})

text = "My SSN is 123-45-6789"
redacted = redactor.redact(text)
print(redacted)  # "My SSN is [REDACTED]"
```

## Memory Service Integration

Redaction can be optionally integrated with memory service (disabled by default):

```json
{
  "memory_config": {
    "redaction": {
      "enabled": true,
      "provider": "regexp",
      "patterns": {
        "ssn": true,
        "credit_card": true
      }
    }
  }
}
```

## Documentation

See `mdb_engine/redaction/README.md` for complete documentation.
