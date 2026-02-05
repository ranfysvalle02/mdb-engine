#!/usr/bin/env python3
"""
Redaction Service Demo - MDB-Engine Redaction Features

This example demonstrates the standalone redaction service with:
- REGEXP provider (default, pattern-based)
- Presidio provider (ML-based entity detection)
- Comparison between providers
- Test endpoints without modifying text

Run with:
    uvicorn app:app --reload --port 8000
"""

import logging
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from mdb_engine.redaction import (
    PresidioRedactionService,
    RedactionServiceError,
    RegexpRedactionService,
    get_redaction_service,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("redaction_demo")

# Create FastAPI app
app = FastAPI(
    title="Redaction Service Demo",
    description="Demonstrates standalone redaction service with REGEXP and Presidio providers",
    version="1.0.0",
)

# =============================================================================
# Pydantic Models for Request Bodies
# =============================================================================


class RedactRequest(BaseModel):
    """Request body for redacting text."""

    text: str
    provider: str = "regexp"  # "regexp" or "presidio"
    config: dict[str, Any] | None = None


class TestRedactionRequest(BaseModel):
    """Request body for testing redaction without modifying text."""

    text: str
    provider: str = "regexp"
    config: dict[str, Any] | None = None


class CompareRequest(BaseModel):
    """Request body for comparing providers."""

    text: str
    regexp_config: dict[str, Any] | None = None
    presidio_config: dict[str, Any] | None = None


# =============================================================================
# Helper Functions
# =============================================================================


def get_redactor(provider: str, config: dict[str, Any] | None = None):
    """Get a redaction service instance."""
    if config is None:
        config = {}

    config["provider"] = provider
    try:
        return get_redaction_service(config=config)
    except RedactionServiceError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


# =============================================================================
# HEALTH & INFO ENDPOINTS
# =============================================================================


@app.get("/", response_class=JSONResponse)
async def root():
    """Welcome endpoint with API overview."""
    return {
        "app": "Redaction Service Demo",
        "description": "Demonstrates standalone redaction service with REGEXP and Presidio providers",
        "endpoints": {
            "redact": "POST /redact - Redact text with specified provider",
            "test": "POST /test - Test redaction without modifying text",
            "providers": "GET /providers - List available providers",
            "stats": "GET /stats - Get redaction service statistics",
            "compare": "POST /compare - Compare REGEXP vs Presidio on same text",
        },
        "providers": {
            "regexp": {
                "description": "Pattern-based redaction using compiled regex patterns",
                "features": ["Fast", "Lightweight", "Configurable patterns"],
                "patterns": [
                    "ssn",
                    "credit_card",
                    "phone",
                    "email",
                    "ip_address",
                    "api_key",
                    "password",
                    "bearer_token",
                    "aws_key",
                    "generic_secret",
                ],
            },
            "presidio": {
                "description": "ML-based redaction using Microsoft Presidio",
                "features": ["More accurate", "Entity-based detection", "ML-powered"],
                "requires": "pip install presidio-analyzer presidio-anonymizer",
                "entities": [
                    "NAME",
                    "PHONE_NUMBER",
                    "EMAIL_ADDRESS",
                    "CREDIT_CARD",
                    "SSN",
                    "IP_ADDRESS",
                    "DATE_TIME",
                    "LOCATION",
                ],
            },
        },
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "providers": {
            "regexp": "available",
            "presidio": "available" if PresidioRedactionService else "not installed",
        },
    }


# =============================================================================
# REDACTION OPERATIONS
# =============================================================================


@app.post("/redact", response_class=JSONResponse)
async def redact_text(request: RedactRequest):
    """
    Redact sensitive information from text.

    Supports both REGEXP and Presidio providers.
    """
    try:
        redactor = get_redactor(request.provider, request.config)
        redacted = redactor.redact(request.text)

        return {
            "success": True,
            "provider": request.provider,
            "original": request.text,
            "redacted": redacted,
            "changed": request.text != redacted,
        }
    except Exception as e:
        logger.exception("Redaction failed")
        raise HTTPException(status_code=500, detail=f"Redaction failed: {str(e)}") from e


@app.post("/test", response_class=JSONResponse)
async def test_redaction(request: TestRedactionRequest):
    """
    Test redaction on text without modifying it.

    Returns what would be redacted without actually redacting.
    """
    try:
        redactor = get_redactor(request.provider, request.config)
        test_results = redactor.test_redaction(request.text)

        return {
            "success": True,
            "provider": request.provider,
            "text": request.text,
            "would_redact": test_results.get("would_redact", False),
            "matches": test_results.get("matches", []),
            "pattern_matches": test_results.get("pattern_matches", {}),
            "entity_matches": test_results.get("entity_matches", {}),
            "redacted_preview": test_results.get("redacted_preview", request.text),
        }
    except Exception as e:
        logger.exception("Test redaction failed")
        raise HTTPException(status_code=500, detail=f"Test redaction failed: {str(e)}") from e


@app.get("/providers", response_class=JSONResponse)
async def list_providers():
    """List available redaction providers."""
    providers = {
        "regexp": {
            "name": "RegexpRedactionService",
            "description": "Pattern-based redaction using compiled regex patterns",
            "available": True,
            "features": [
                "Fast and lightweight",
                "Configurable patterns",
                "Custom pattern support",
                "Allow list support",
            ],
            "default_config": {
                "provider": "regexp",
                "enabled": True,
                "replacement": "[REDACTED]",
                "patterns": {
                    "ssn": True,
                    "credit_card": True,
                    "phone": False,
                    "email": False,
                },
            },
        },
    }

    # Check if Presidio is available
    try:
        from mdb_engine.redaction import PresidioRedactionService

        providers["presidio"] = {
            "name": "PresidioRedactionService",
            "description": "ML-based redaction using Microsoft Presidio",
            "available": True,
            "features": [
                "More accurate detection",
                "Entity-based detection",
                "ML-powered",
                "Supports many entity types",
            ],
            "default_config": {
                "provider": "presidio",
                "enabled": True,
                "replacement": "[REDACTED_PII]",
                "entities": ["NAME", "PHONE_NUMBER", "EMAIL_ADDRESS"],
                "language": "en",
            },
        }
    except ImportError:
        providers["presidio"] = {
            "name": "PresidioRedactionService",
            "description": "ML-based redaction using Microsoft Presidio",
            "available": False,
            "install": "pip install presidio-analyzer presidio-anonymizer",
        }

    return {"success": True, "providers": providers}


@app.get("/stats", response_class=JSONResponse)
async def get_stats(provider: str = "regexp", config: dict[str, Any] | None = None):
    """Get redaction service statistics."""
    try:
        redactor = get_redactor(provider, config)
        stats = redactor.get_stats()

        return {
            "success": True,
            "provider": provider,
            "stats": stats,
        }
    except Exception as e:
        logger.exception("Failed to get stats")
        raise HTTPException(status_code=500, detail=f"Failed to get stats: {str(e)}") from e


@app.post("/compare", response_class=JSONResponse)
async def compare_providers(request: CompareRequest):
    """
    Compare REGEXP vs Presidio providers on the same text.

    Shows how each provider handles the same input.
    """
    results = {
        "success": True,
        "original_text": request.text,
        "providers": {},
    }

    # Test REGEXP provider
    try:
        regexp_config = request.regexp_config or {}
        regexp_config["provider"] = "regexp"
        regexp_redactor = get_redaction_service(config=regexp_config)
        regexp_redacted = regexp_redactor.redact(request.text)
        regexp_test = regexp_redactor.test_redaction(request.text)

        results["providers"]["regexp"] = {
            "available": True,
            "redacted": regexp_redacted,
            "changed": request.text != regexp_redacted,
            "matches": regexp_test.get("matches", []),
            "pattern_matches": regexp_test.get("pattern_matches", {}),
            "stats": regexp_redactor.get_stats(),
        }
    except Exception as e:
        results["providers"]["regexp"] = {
            "available": False,
            "error": str(e),
        }

    # Test Presidio provider
    try:
        presidio_config = request.presidio_config or {}
        presidio_config["provider"] = "presidio"
        presidio_redactor = get_redaction_service(config=presidio_config)
        presidio_redacted = presidio_redactor.redact(request.text)
        presidio_test = presidio_redactor.test_redaction(request.text)

        results["providers"]["presidio"] = {
            "available": True,
            "redacted": presidio_redacted,
            "changed": request.text != presidio_redacted,
            "matches": presidio_test.get("matches", []),
            "entity_matches": presidio_test.get("entity_matches", {}),
            "stats": presidio_redactor.get_stats(),
        }
    except Exception as e:
        results["providers"]["presidio"] = {
            "available": False,
            "error": str(e),
        }

    return results


# =============================================================================
# EXAMPLE ENDPOINTS
# =============================================================================


@app.get("/examples/regexp", response_class=JSONResponse)
async def example_regexp():
    """Example: REGEXP provider with common PII."""
    sample_text = (
        "John Doe's SSN is 123-45-6789. "
        "His credit card is 4242-4242-4242-4242. "
        "Contact him at john@example.com or call 555-123-4567."
    )

    config = {
        "provider": "regexp",
        "enabled": True,
        "replacement": "[REDACTED]",
        "patterns": {
            "ssn": True,
            "credit_card": True,
            "phone": True,
            "email": True,
        },
    }

    redactor = get_redaction_service(config=config)
    redacted = redactor.redact(sample_text)
    test_results = redactor.test_redaction(sample_text)

    return {
        "example": "REGEXP Provider",
        "original": sample_text,
        "redacted": redacted,
        "test_results": test_results,
        "config": config,
    }


@app.get("/examples/presidio", response_class=JSONResponse)
async def example_presidio():
    """Example: Presidio provider with entity detection."""
    sample_text = (
        "John Doe met with Jane Smith. "
        "John's phone number is 123-456-7890 and his email is john@example.com."
    )

    try:
        config = {
            "provider": "presidio",
            "enabled": True,
            "replacement": "[REDACTED_PII]",
            "entities": ["NAME", "PHONE_NUMBER", "EMAIL_ADDRESS"],
            "language": "en",
        }

        redactor = get_redaction_service(config=config)
        redacted = redactor.redact(sample_text)
        test_results = redactor.test_redaction(sample_text)

        return {
            "example": "Presidio Provider",
            "original": sample_text,
            "redacted": redacted,
            "test_results": test_results,
            "config": config,
        }
    except RedactionServiceError as e:
        return {
            "example": "Presidio Provider",
            "error": str(e),
            "install": "pip install presidio-analyzer presidio-anonymizer",
        }


# =============================================================================
# RUN WITH UVICORN
# =============================================================================

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
