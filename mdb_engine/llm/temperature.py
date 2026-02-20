"""
Shared temperature adjustment helpers for LLM providers.

Gemini models require temperature=1.0.
Azure OpenAI and OpenAI are enforced to 0.3 for consistency.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def adjust_temperature_for_model(
    model: str | None,
    requested_temperature: float,
    *,
    log: logging.Logger | None = None,
    enforce_openai_azure: bool = True,
    gemini_temperature: float = 1.0,
    openai_temperature: float = 0.3,
) -> float:
    """Return temperature adjusted for provider-specific requirements."""
    model_lower = (model or "").lower()
    log = log or logger

    if model_lower.startswith("gemini"):
        if requested_temperature != gemini_temperature:
            log.info(
                f"Enforcing temperature={gemini_temperature} for Gemini model "
                f"'{model}'. Requested temperature ({requested_temperature}) was adjusted."
            )
        return gemini_temperature

    if enforce_openai_azure and (model_lower.startswith("azure/") or model_lower.startswith("openai/")):
        if requested_temperature != openai_temperature:
            log.info(
                f"Enforcing temperature={openai_temperature} for OpenAI/Azure model "
                f"'{model}'. Requested temperature ({requested_temperature}) was adjusted."
            )
        return openai_temperature

    return requested_temperature
