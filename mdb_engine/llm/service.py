"""
LLM Service for Chat Completions and Text Generation

This module provides a unified interface for LLM providers using native SDKs.
Supports OpenAI, Azure OpenAI, and Google Gemini.

Key Features:
- Auto-detection of provider from environment variables or manifest config
- Unified API across all providers
- Support for chat completions and streaming
- Configurable via manifest.json
- FastAPI dependency injection support

Dependencies:
    pip install openai google-genai
"""

import json
import logging
import os
import re
import time
from typing import Any, TypeVar

from ..exceptions import MongoDBEngineError
from ..observability.tracing import create_span
from .temperature import adjust_temperature_for_model

# OpenAI SDK import (covers both OpenAI and Azure OpenAI)
try:
    from openai import AsyncAzureOpenAI, AsyncOpenAI

    OPENAI_SDK_AVAILABLE = True
except ImportError:
    OPENAI_SDK_AVAILABLE = False
    AsyncOpenAI = None  # type: ignore[assignment,misc]
    AsyncAzureOpenAI = None  # type: ignore[assignment,misc]

# Google GenAI SDK import
try:
    from google import genai
    from google.genai import types as genai_types

    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False
    genai = None  # type: ignore[assignment]
    genai_types = None  # type: ignore[assignment]

# Pydantic import for structured output
try:
    from pydantic import BaseModel

    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False
    BaseModel = None  # type: ignore[assignment,misc]

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger(__name__)

# Keywords used to detect when an API error is caused by an unsupported or
# invalid thinking configuration, so we can transparently retry without it.
_THINKING_ERROR_KEYWORDS = ("thinking", "thinkingbudget", "thinkinglevel", "thought")


def _is_thinking_config_error(exc: Exception) -> bool:
    """Heuristic check: did this error originate from the thinking config?

    Used to decide whether a failed Gemini request is worth retrying without
    the thinking config (vs. a genuine failure like auth/quota that should
    propagate to the resilience layer).
    """
    msg = str(exc).lower()
    return any(keyword in msg for keyword in _THINKING_ERROR_KEYWORDS)


def _clamp_thinking_budget(model_name: str, budget: int) -> int:
    """Clamp a Gemini 2.5 ``thinking_budget`` into the model's valid range.

    Per Google's docs: 2.5 Pro accepts ``128``-``32768`` and *cannot* disable
    thinking (a budget of ``0`` is invalid); Flash / Flash-Lite and other 2.5
    models accept ``0``-``24576``. A budget of ``-1`` (dynamic thinking) is
    always passed through untouched.
    """
    if budget == -1:
        return budget
    name = model_name.lower()
    if "pro" in name:
        # Pro cannot disable thinking; floor at the minimum supported budget.
        return max(128, min(budget, 32768))
    return max(0, min(budget, 24576))


def _build_thinking_config(model_name: str, reasoning_effort: str | None) -> Any | None:
    """Build a provider-correct ``ThinkingConfig`` for a Gemini model.

    - Gemini 3.x uses ``thinking_level`` (``minimal``/``low``/``medium``/``high``).
      Pro models do not support ``minimal`` and default to ``high``, so
      ``"none"`` maps to the lowest level the model actually supports.
    - Gemini 2.5 uses ``thinking_budget`` (a token ceiling), clamped to the
      model's valid range.

    Thought summaries (``include_thoughts``) are only requested when thinking is
    actually enabled, because asking for thoughts while thinking is disabled
    (budget ``0``) is contradictory and rejected by the API.

    Returns ``None`` when no thinking config should be applied (no effort given,
    SDK unavailable, or the config could not be constructed).
    """
    if not reasoning_effort or genai_types is None:
        return None

    effort = reasoning_effort.lower()
    name = model_name.lower()

    try:
        if "gemini-3" in name:
            is_pro = "pro" in name
            level_map = {
                "none": "LOW" if is_pro else "MINIMAL",
                "low": "LOW",
                "medium": "MEDIUM",
                "high": "HIGH",
            }
            level = level_map.get(effort, "MEDIUM")
            return genai_types.ThinkingConfig(thinking_level=level, include_thoughts=True)

        # Gemini 2.5 family (and any other non-3.x thinking model) -> token budget.
        budget_map = {"none": 0, "low": 1024, "medium": -1, "high": 8192}
        budget = _clamp_thinking_budget(name, budget_map.get(effort, -1))
        return genai_types.ThinkingConfig(thinking_budget=budget, include_thoughts=budget != 0)
    except (TypeError, ValueError) as e:
        logger.warning(
            f"Could not build thinking config for model '{model_name}' "
            f"(reasoning_effort={reasoning_effort!r}): {e}. Proceeding without it."
        )
        return None


def _parse_structured_response(
    content: str | None, pydantic_model: type[BaseModel] | None = None
) -> dict[str, Any] | BaseModel:
    """
    Safely parse structured LLM response (JSON string) with error handling.

    Handles edge cases:
    - Markdown code blocks (```json ... ```)
    - Empty/null responses
    - Malformed JSON
    - Pydantic validation errors

    Args:
        content: Response content string (may be JSON or wrapped in markdown)
        pydantic_model: Optional Pydantic model class for validation

    Returns:
        Parsed dict or Pydantic model instance

    Raises:
        ValueError: If content is None/empty or parsing fails
    """
    if not content:
        raise ValueError("Response content is empty or None")

    content = content.strip()

    json_match = re.search(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL)
    if json_match:
        content = json_match.group(1).strip()
        logger.debug("Extracted JSON from markdown code block")

    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse JSON response: {e}. Content preview: {content[:200]}...")
        raise ValueError(f"Invalid JSON response: {e}") from e

    if pydantic_model and PYDANTIC_AVAILABLE and BaseModel is not None:
        try:
            if isinstance(data, dict):
                return pydantic_model.model_validate(data)
            else:
                return pydantic_model.model_validate_json(content)
        except (ValueError, TypeError, AttributeError) as e:
            logger.warning(
                f"Pydantic validation failed: {e}. "
                f"Data type: {type(data)}, preview: {str(data)[:200] if data else 'None'}"
            )
            raise ValueError(f"Pydantic validation failed: {e}") from e

    return data


class LLMServiceError(MongoDBEngineError):
    """Base exception for LLM service failures."""

    pass


def _detect_provider_from_env() -> str:
    """
    Detect provider from environment variables.

    Returns:
        Model string in ``provider/model`` format
        (e.g., ``"openai/gpt-4o"``, ``"azure/gpt-4o"``, ``"gemini/gemini-3-flash-preview"``)
    """
    azure_key = os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("AZURE_API_KEY")
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT") or os.getenv("AZURE_API_BASE")

    if azure_key and azure_endpoint:
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o")
        return f"azure/{deployment}"
    elif os.getenv("GEMINI_API_KEY"):
        return "gemini/gemini-3-flash-preview"
    elif os.getenv("OPENAI_API_KEY"):
        return "openai/gpt-4o"
    else:
        return "openai/gpt-4o"


def _provider_type_from_model(model: str) -> str:
    """Return ``"openai"``, ``"azure"``, or ``"gemini"`` from a model string."""
    lower = model.lower()
    if lower.startswith("azure/"):
        return "azure"
    if lower.startswith("gemini/") or lower.startswith("vertex_ai/"):
        return "gemini"
    return "openai"


def _strip_provider_prefix(model: str) -> str:
    """Strip the ``provider/`` prefix from a model string."""
    if "/" in model:
        return model.split("/", 1)[1]
    return model


def _format_response_format_for_provider(response_format: Any, model: str | None = None) -> Any:
    """
    Format response_format based on provider for optimal structured output.

    Args:
        response_format: Pydantic BaseModel class or dict
        model: Model string (e.g., ``"gemini/gemini-3-flash-preview"``, ``"openai/gpt-4o"``)

    Returns:
        Formatted response_format for the provider
    """
    if not response_format:
        return None

    if isinstance(response_format, dict):
        return response_format

    if (
        PYDANTIC_AVAILABLE
        and BaseModel is not None
        and isinstance(response_format, type)
        and issubclass(response_format, BaseModel)
    ):
        model_lower = (model or "").lower()

        if "gemini" in model_lower or model_lower.startswith("vertex_ai/"):
            try:
                schema = response_format.model_json_schema()
                logger.debug(
                    f"Using Gemini response_schema format for model '{model}': "
                    f"schema with {len(schema.get('properties', {}))} properties"
                )
                return {"type": "json_object", "response_schema": schema}
            except (AttributeError, TypeError, ValueError) as e:
                logger.warning(
                    f"Failed to generate JSON schema from Pydantic model for Gemini, "
                    f"falling back to direct model: {e}"
                )
                return response_format

        elif model_lower.startswith("openai/") or model_lower.startswith("azure/"):
            logger.debug(
                f"Using OpenAI/Azure structured output format for model '{model}' (Pydantic model passed directly)"
            )
            return response_format

        else:
            logger.debug(f"Using default Pydantic model format for model '{model}'")
            return response_format

    return response_format


def _openai_messages_to_gemini_contents(
    messages: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], str | None]:
    """Convert OpenAI-style messages to Gemini ``contents`` + optional system instruction.

    Returns:
        ``(contents, system_instruction)``
    """
    system_instruction: str | None = None
    contents: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role", "user")
        text = msg.get("content", "")
        if role == "system":
            system_instruction = text
        else:
            gemini_role = "model" if role == "assistant" else "user"
            contents.append({"role": gemini_role, "parts": [{"text": text}]})
    return contents, system_instruction


class _LLMProvider:
    """Internal LLM provider wrapper using native SDKs.

    Not part of the public API.  Use ``LLMService`` instead, which
    manages one or more ``_LLMProvider`` instances keyed by name
    (e.g. ``"chat"``, ``"extraction"``).
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
    ):
        """
        Initialize LLM Provider with native SDK clients.

        Args:
            config: Optional dict with LLM configuration (from manifest.json llm_config)
                   Supports:
                   - default_model: Model string in ``provider/model`` format
                     (e.g., ``"openai/gpt-4o"``, ``"gemini/gemini-3-flash-preview"``)
                   - fallbacks: List of fallback model strings
                   - tools: List of tool definitions for function calling
                   - provider: Legacy format (e.g., ``"openai"``, ``"azure"``, ``"gemini"``)

        Raises:
            LLMServiceError: If the required SDK is not available
        """
        config = config or {}

        # --- Resolve default model -----------------------------------------------
        default_model = config.get("default_model")
        provider = config.get("provider")

        if default_model:
            self.default_model = default_model
        elif provider:
            model_name = config.get("model_name", "gpt-4o")
            if provider == "azure":
                deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", model_name)
                self.default_model = f"azure/{deployment}"
                logger.warning(
                    f"Using Azure deployment name '{deployment}'. "
                    f"Make sure this matches your Azure AI Studio deployment name, "
                    f"not the underlying model name."
                )
            elif provider == "gemini":
                self.default_model = f"gemini/{model_name}"
            else:
                self.default_model = f"{provider}/{model_name}"
        else:
            self.default_model = _detect_provider_from_env()

        self._provider_type = _provider_type_from_model(self.default_model)

        # --- Validate SDK availability -------------------------------------------
        if self._provider_type in ("openai", "azure") and not OPENAI_SDK_AVAILABLE:
            raise LLMServiceError("OpenAI SDK not available. Install with: pip install openai")
        if self._provider_type == "gemini" and not GENAI_AVAILABLE:
            raise LLMServiceError("Google GenAI SDK not available. Install with: pip install google-genai")

        # SDK clients are created lazily on first use so that provider init
        # succeeds even when API keys are absent (e.g. during manifest
        # validation or test environments without credentials).
        self._openai_client: AsyncOpenAI | None = None
        self._azure_client: AsyncAzureOpenAI | None = None
        self._gemini_client: Any | None = None

        # --- Config fields -------------------------------------------------------
        self.fallbacks: list[str] = config.get("fallbacks", [])
        if self.fallbacks:
            logger.info(f"LLM Provider configured with fallbacks: {self.fallbacks}")

        self.tools: list[Any] = config.get("tools", [])
        if self.tools:
            logger.info(f"LLM Provider configured with tools: {len(self.tools)} tool(s)")

        self.default_temperature: float = config.get("temperature", 0.7)
        self.default_persona: str = config.get("persona", "helpful assistant")
        self.config = config

        logger.info(f"LLM Provider initialized (model: {self.default_model}, sdk: {self._provider_type})")

        # --- Apply shared resilience (retry + backoff + circuit breaker) ----------
        try:
            from ..core.resilience import (
                circuit_breaker_from_config,
                policy_from_config,
                resilient,
            )

            resilience_cfg = config.get("resilience", {})
            _policy = policy_from_config(
                resilience_cfg,
                name="llm",
                default_retries=3,
                default_backoff_base=1.0,
                default_backoff_max=30.0,
                default_timeout=60.0,
                extra_retryable=(LLMServiceError,),
            )
            self._circuit_breaker = circuit_breaker_from_config(
                resilience_cfg,
                name=f"llm:{self.default_model}",
            )
            _original_chat = self.chat_completion

            @resilient(_policy, circuit_breaker=self._circuit_breaker)
            async def _resilient_chat(*args, **kwargs):
                return await _original_chat(*args, **kwargs)

            self.chat_completion = _resilient_chat  # type: ignore[assignment]
            logger.debug(
                f"LLM resilience enabled: retries={_policy.max_retries}, "
                f"timeout={_policy.timeout}s, circuit_threshold={self._circuit_breaker.failure_threshold}"
            )
        except ImportError:
            logger.debug("Resilience module not available; LLM calls will not be retried")

    # ------------------------------------------------------------------
    # SDK dispatch helpers
    # ------------------------------------------------------------------

    def _build_client(self, ptype: str) -> Any:
        """Build an SDK client for the given provider type.

        Raises ``LLMServiceError`` if the required API key is missing.
        """
        if ptype == "azure" and OPENAI_SDK_AVAILABLE:
            return AsyncAzureOpenAI(
                api_key=os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("AZURE_API_KEY"),
                azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT") or os.getenv("AZURE_API_BASE"),
                api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
            )
        if ptype == "gemini" and GENAI_AVAILABLE:
            return genai.Client(
                api_key=os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"),
            )
        if ptype == "openai" and OPENAI_SDK_AVAILABLE:
            return AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        raise LLMServiceError(f"No SDK available for provider type '{ptype}'")

    def _get_default_client(self) -> Any:
        """Return (and lazily create) the default SDK client."""
        if self._provider_type == "azure":
            if self._azure_client is None:
                self._azure_client = self._build_client("azure")
            return self._azure_client
        if self._provider_type == "gemini":
            if self._gemini_client is None:
                self._gemini_client = self._build_client("gemini")
            return self._gemini_client
        if self._openai_client is None:
            self._openai_client = self._build_client("openai")
        return self._openai_client

    def _client_for_model(self, model: str) -> tuple[str, Any]:
        """Return ``(provider_type, client)`` for the given model string.

        Uses the cached default client when the model prefix matches the
        provider's type.  Creates a one-off client for cross-provider
        fallback models.
        """
        ptype = _provider_type_from_model(model)
        if ptype == self._provider_type:
            return ptype, self._get_default_client()

        # Cross-provider fallback — build a throwaway client
        return ptype, self._build_client(ptype)

    async def _call_openai(
        self,
        client: Any,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int | None,
        stream: bool = False,
        **kwargs,
    ) -> Any:
        """Dispatch to OpenAI or Azure OpenAI SDK."""
        model_name = _strip_provider_prefix(model)
        call_kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
            "temperature": temperature,
            "stream": stream,
        }
        if max_tokens is not None:
            call_kwargs["max_tokens"] = max_tokens

        if "response_format" in kwargs:
            call_kwargs["response_format"] = kwargs.pop("response_format")
        if "tools" in kwargs:
            call_kwargs["tools"] = kwargs.pop("tools")
        if "tool_choice" in kwargs:
            call_kwargs["tool_choice"] = kwargs.pop("tool_choice")
        # Opt-in: only forwarded when the caller explicitly set it (reasoning models).
        if kwargs.get("reasoning_effort"):
            call_kwargs["reasoning_effort"] = kwargs.pop("reasoning_effort")

        return await client.chat.completions.create(**call_kwargs)

    async def _call_gemini(
        self,
        client: Any,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int | None,
        stream: bool = False,
        reasoning_effort: str | None = None,
        response_format: Any | None = None,
        **kwargs,
    ) -> Any:
        """Dispatch to Google GenAI SDK."""
        model_name = _strip_provider_prefix(model)
        contents, system_instruction = _openai_messages_to_gemini_contents(messages)

        config_kwargs: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            config_kwargs["max_output_tokens"] = max_tokens
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction

        # Structured output
        if response_format:
            if isinstance(response_format, dict):
                schema = response_format.get("response_schema")
                if schema:
                    config_kwargs["response_mime_type"] = "application/json"
                    config_kwargs["response_schema"] = schema
                else:
                    config_kwargs["response_mime_type"] = "application/json"
            elif (
                PYDANTIC_AVAILABLE
                and BaseModel is not None
                and isinstance(response_format, type)
                and issubclass(response_format, BaseModel)
            ):
                config_kwargs["response_mime_type"] = "application/json"
                config_kwargs["response_schema"] = response_format.model_json_schema()

        # Thinking / reasoning (model-aware: thinking_level for 3.x, budget for 2.5)
        thinking_config = _build_thinking_config(model_name, reasoning_effort)
        if thinking_config is not None:
            config_kwargs["thinking_config"] = thinking_config

        async def _dispatch(cfg_kwargs: dict[str, Any]) -> Any:
            gc_config = genai_types.GenerateContentConfig(**cfg_kwargs)
            if stream:
                return await client.aio.models.generate_content_stream(
                    model=model_name, contents=contents, config=gc_config
                )
            return await client.aio.models.generate_content(model=model_name, contents=contents, config=gc_config)

        try:  # nosemgrep - broad catch is intentional; non-thinking errors are re-raised below
            return await _dispatch(config_kwargs)
        except Exception as e:  # noqa: BLE001 - re-raised below unless it's a recoverable thinking-config error
            # Gracefully degrade: if the model rejected the thinking config
            # (e.g. an older model that doesn't support it), retry once without
            # it instead of failing the whole request. Genuine errors (auth,
            # quota, network) don't match and propagate to the resilience layer.
            if "thinking_config" in config_kwargs and _is_thinking_config_error(e):
                logger.warning(
                    f"Gemini model '{model_name}' rejected the thinking config "
                    f"({type(e).__name__}: {e}); retrying without it."
                )
                fallback_kwargs = {k: v for k, v in config_kwargs.items() if k != "thinking_config"}
                return await _dispatch(fallback_kwargs)
            raise

    def _extract_gemini_text(self, response: Any) -> str:
        """Extract text content from a Gemini response, skipping thought parts."""
        try:
            text = response.text
            if text:
                return text
        except (AttributeError, ValueError):
            pass
        # Manual fallback
        parts_text: list[str] = []
        for candidate in getattr(response, "candidates", []):
            for part in getattr(getattr(candidate, "content", None), "parts", []):
                if getattr(part, "thought", False):
                    continue
                if getattr(part, "text", None):
                    parts_text.append(part.text)
        return "".join(parts_text)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        response_format: Any | None = None,
        reasoning_effort: str | None = None,
        **kwargs,
    ) -> str:
        """
        Generate a chat completion response.

        Args:
            messages: List of message dicts with 'role' and 'content' keys
            model: Optional model string in ``provider/model`` format.
                   Overrides default_model if provided.
            temperature: Sampling temperature (0.0-2.0)
            max_tokens: Maximum tokens to generate
            response_format: Optional response format. Can be:
                           - Pydantic BaseModel class (for structured output)
                           - dict with ``"type": "json_object"`` (for JSON mode)
                           - None (for free-form text)
            reasoning_effort: Optional reasoning/thinking effort for reasoning
                           models (``"none"``, ``"low"``, ``"medium"``, ``"high"``).
                           Mapped to Gemini ``thinking_config`` and passed
                           through for OpenAI reasoning models.
            **kwargs: Additional provider-specific parameters

        Returns:
            str: The generated response text
        """
        start_time = time.time()
        model_to_use = model or self.default_model

        with create_span(
            "gen_ai.chat_completion",
            {
                "gen_ai.system": _provider_type_from_model(model_to_use),
                "gen_ai.request.model": model_to_use or "",
                "gen_ai.request.temperature": temperature,
                "gen_ai.request.max_tokens": max_tokens or 0,
            },
        ) as span:
            try:
                messages_to_use = messages.copy()
                has_system_message = any(msg.get("role") == "system" for msg in messages_to_use)
                if not has_system_message and self.default_persona:
                    messages_to_use.insert(0, {"role": "system", "content": self.default_persona})

                formatted_response_format = _format_response_format_for_provider(response_format, model_to_use)

                if self.tools and "tools" not in kwargs:
                    kwargs["tools"] = self.tools
                    logger.debug(f"Using configured tools: {len(self.tools)} tool(s)")

                requested_temperature = temperature if temperature != 0.7 else self.default_temperature
                final_temperature = adjust_temperature_for_model(
                    model=model_to_use,
                    requested_temperature=requested_temperature,
                    log=logger,
                )

                # Build ordered list of models to try
                models_to_try = [model_to_use] + list(self.fallbacks)

                last_error: Exception | None = None
                for try_model in models_to_try:
                    try:
                        ptype, client = self._client_for_model(try_model)

                        if ptype == "gemini":
                            response = await self._call_gemini(
                                client,
                                try_model,
                                messages_to_use,
                                final_temperature,
                                max_tokens,
                                reasoning_effort=reasoning_effort,
                                response_format=formatted_response_format,
                            )
                            content = self._extract_gemini_text(response)
                        else:
                            if formatted_response_format:
                                kwargs["response_format"] = formatted_response_format
                            if reasoning_effort and "reasoning_effort" not in kwargs:
                                kwargs["reasoning_effort"] = reasoning_effort
                            response = await self._call_openai(
                                client,
                                try_model,
                                messages_to_use,
                                final_temperature,
                                max_tokens,
                                **kwargs,
                            )
                            content = response.choices[0].message.content if response.choices else ""

                        # Record usage
                        usage = getattr(response, "usage", None) or getattr(response, "usage_metadata", None)
                        if usage:
                            span.set_attribute(
                                "gen_ai.usage.prompt_tokens",
                                getattr(usage, "prompt_tokens", 0) or getattr(usage, "prompt_token_count", 0),
                            )
                            span.set_attribute(
                                "gen_ai.usage.completion_tokens",
                                getattr(usage, "completion_tokens", 0) or getattr(usage, "candidates_token_count", 0),
                            )

                        actual_model = getattr(response, "model", try_model) or try_model
                        span.set_attribute("gen_ai.response.model", actual_model)

                        if content:
                            duration = time.time() - start_time
                            logger.info(
                                "LLM_COMPLETION_SUCCESS",
                                extra={
                                    "latency_sec": round(duration, 3),
                                    "requested_model": model_to_use,
                                    "actual_model": actual_model,
                                },
                            )
                            if try_model != model_to_use:
                                logger.info(f"Used fallback model: {try_model} (requested: {model_to_use})")
                            return content

                        return str(response)

                    except (
                        ValueError,
                        TypeError,
                        AttributeError,
                        RuntimeError,
                        ConnectionError,
                        TimeoutError,
                        OSError,
                    ) as e:
                        last_error = e
                        if try_model != models_to_try[-1]:
                            logger.warning(f"Model '{try_model}' failed ({e}), trying next fallback...")
                            continue
                        raise

                raise LLMServiceError(f"All models failed. Last error: {last_error}") from last_error

            except (
                ValueError,
                TypeError,
                AttributeError,
                RuntimeError,
                ConnectionError,
                TimeoutError,
                LLMServiceError,
            ) as e:
                span.set_attribute("error", True)
                logger.exception(f"LLM_COMPLETION_FAILED: {str(e)}")
                raise LLMServiceError(f"LLM completion failed: {str(e)}") from e

    async def chat_completion_stream(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        stream_reasoning: bool = True,
        **kwargs,
    ):
        """
        Generate a streaming chat completion response.

        Yields chunks of the response as they are generated, providing
        real-time feedback for better UX.

        Reasoning/thinking content from models like Gemini is streamed as
        SEPARATE events (not mixed with content) so the frontend can render
        them as expandable "AI Thinking" bubbles.

        Args:
            messages: List of message dicts with 'role' and 'content' keys
            model: Optional model string in ``provider/model`` format
            temperature: Sampling temperature (0.0-2.0)
            max_tokens: Maximum tokens to generate
            reasoning_effort: Reasoning effort level for Gemini models.
                             Options: ``"none"``, ``"low"``, ``"medium"``, ``"high"``
            stream_reasoning: If True (default), yields reasoning as separate
                             chunks prefixed with ``__REASONING__:``
            **kwargs: Additional provider-specific parameters

        Yields:
            str: Content chunks (plain text) OR reasoning chunks
        """
        model_to_use = model or self.default_model
        reasoning_buffer: list[str] = []
        reasoning_started = False

        with create_span(
            "gen_ai.chat_completion_stream",
            {
                "gen_ai.system": _provider_type_from_model(model_to_use),
                "gen_ai.request.model": model_to_use or "",
                "gen_ai.request.temperature": temperature,
                "gen_ai.request.max_tokens": max_tokens or 0,
                "gen_ai.request.stream": True,
            },
        ) as span:
            try:
                messages_to_use = messages.copy()
                has_system_message = any(msg.get("role") == "system" for msg in messages_to_use)
                if not has_system_message and self.default_persona:
                    messages_to_use.insert(0, {"role": "system", "content": self.default_persona})

                if self.tools and "tools" not in kwargs:
                    kwargs["tools"] = self.tools
                    logger.debug(f"Using configured tools: {len(self.tools)} tool(s)")

                requested_temperature = temperature if temperature != 0.7 else self.default_temperature
                final_temperature = adjust_temperature_for_model(
                    model=model_to_use,
                    requested_temperature=requested_temperature,
                    log=logger,
                )

                ptype, client = self._client_for_model(model_to_use)

                if ptype == "gemini":
                    stream = await self._call_gemini(
                        client,
                        model_to_use,
                        messages_to_use,
                        final_temperature,
                        max_tokens,
                        stream=True,
                        reasoning_effort=reasoning_effort,
                    )
                    async for chunk in stream:
                        for candidate in getattr(chunk, "candidates", []):
                            for part in getattr(getattr(candidate, "content", None), "parts", []):
                                if getattr(part, "thought", False) and getattr(part, "text", None):
                                    reasoning_buffer.append(part.text)
                                    if stream_reasoning:
                                        if not reasoning_started:
                                            yield "__REASONING_START__"
                                            reasoning_started = True
                                        yield f"__REASONING__:{part.text}"
                                elif getattr(part, "text", None):
                                    if reasoning_started and stream_reasoning:
                                        yield "__REASONING_END__"
                                        reasoning_started = False
                                    yield part.text

                else:
                    # OpenAI / Azure — use native streaming
                    if reasoning_effort and model_to_use:
                        kwargs["reasoning_effort"] = reasoning_effort
                    response = await self._call_openai(
                        client,
                        model_to_use,
                        messages_to_use,
                        final_temperature,
                        max_tokens,
                        stream=True,
                        **kwargs,
                    )
                    async for chunk in response:
                        if hasattr(chunk, "choices") and chunk.choices:
                            delta = chunk.choices[0].delta

                            if hasattr(delta, "reasoning_content") and delta.reasoning_content:
                                reasoning_buffer.append(delta.reasoning_content)
                                if stream_reasoning:
                                    if not reasoning_started:
                                        yield "__REASONING_START__"
                                        reasoning_started = True
                                    yield f"__REASONING__:{delta.reasoning_content}"

                            if hasattr(delta, "content") and delta.content:
                                if reasoning_started and stream_reasoning:
                                    yield "__REASONING_END__"
                                    reasoning_started = False
                                yield delta.content

                if reasoning_started and stream_reasoning:
                    yield "__REASONING_END__"

                if reasoning_buffer:
                    full_reasoning = "".join(reasoning_buffer)
                    logger.info(
                        "LLM_REASONING_CAPTURED",
                        extra={
                            "model": model_to_use,
                            "reasoning_effort": reasoning_effort,
                            "reasoning_length": len(full_reasoning),
                            "reasoning_preview": full_reasoning[:200] + "..."
                            if len(full_reasoning) > 200
                            else full_reasoning,
                        },
                    )

            except (
                ValueError,
                TypeError,
                AttributeError,
                RuntimeError,
                ConnectionError,
                TimeoutError,
            ) as e:
                span.set_attribute("error", True)
                logger.exception(f"LLM_STREAM_FAILED: {str(e)}")
                raise LLMServiceError(f"LLM streaming failed: {str(e)}") from e


class LLMService:
    """
    Service for LLM chat completions and text generation.

    Supports OpenAI, Azure OpenAI, and Google Gemini via native SDKs.

    Example:
        from mdb_engine.llm import LLMService
        from pydantic import BaseModel

        llm_service = LLMService(config={
            "providers": {"chat": "openai/gpt-4o"}
        })

        response = await llm_service.chat_completion(
            messages=[{"role": "user", "content": "Hello!"}]
        )

        class Movie(BaseModel):
            title: str
            year: int

        response_text = await llm_service.chat_completion(
            messages=[{"role": "user", "content": "Extract movie info"}],
            response_format=Movie
        )
        movie = Movie.model_validate_json(response_text)
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
    ):
        """
        Initialize LLM Service.

        Args:
            config: Configuration dict (from manifest.json llm_config)
                   Requires:
                   - providers: Dict mapping provider names to model strings
                     (e.g., ``{"chat": "openai/gpt-4o", "analysis": "gemini/gemini-3-flash-preview"}``)

        Raises:
            LLMServiceError: If required SDKs are not available or providers not configured
        """
        if not config:
            raise LLMServiceError("LLMService requires 'config' dict with 'providers' mapping")

        providers_config = config.get("providers", {})
        if not providers_config:
            raise LLMServiceError(
                "LLMService requires 'providers' dict in config. " "Example: {'providers': {'chat': 'openai/gpt-4o'}}"
            )

        self.config = config
        self.providers: dict[str, _LLMProvider] = {}

        for provider_name, model_string in providers_config.items():
            if not isinstance(model_string, str):
                raise LLMServiceError(
                    f"Provider '{provider_name}' must map to a model string, " f"got {type(model_string).__name__}"
                )
            provider_config = {"default_model": model_string}
            self.providers[provider_name] = _LLMProvider(config=provider_config)
            logger.info(f"Initialized named provider '{provider_name}' with model '{model_string}'")

        if "chat" in self.providers:
            self.default_provider_name: str = "chat"
        else:
            self.default_provider_name = next(iter(self.providers))

    def get_provider(self, provider_name: str) -> _LLMProvider:
        if provider_name not in self.providers:
            available = ", ".join(self.providers.keys())
            raise LLMServiceError(f"Provider '{provider_name}' not found. Available providers: {available}")
        return self.providers[provider_name]

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        provider_name: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        response_format: Any | None = None,
        reasoning_effort: str | None = None,
        **kwargs,
    ) -> str:
        """
        Generate a chat completion response.

        Args:
            messages: List of message dicts with 'role' and 'content' keys
            provider_name: Named provider to use (e.g. ``"chat"``, ``"extraction"``).
                          Defaults to ``self.default_provider_name`` (usually ``"chat"``).
            model: Optional model string in ``provider/model`` format.
                   Overrides default_model if provided.
            temperature: Sampling temperature (0.0-2.0)
            max_tokens: Maximum tokens to generate
            response_format: Optional response format (Pydantic model, dict, or None)
            reasoning_effort: Optional reasoning/thinking effort for reasoning
                          models (``"none"``, ``"low"``, ``"medium"``, ``"high"``).
            **kwargs: Additional provider-specific parameters

        Returns:
            str: The generated response text (JSON string for structured output)
        """
        try:
            provider_name = provider_name or self.default_provider_name
            provider = self.get_provider(provider_name)
            if temperature == 0.7:
                temperature = provider.default_temperature

            return await provider.chat_completion(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
                reasoning_effort=reasoning_effort,
                **kwargs,
            )
        except (
            LLMServiceError,
            ValueError,
            TypeError,
            RuntimeError,
            ConnectionError,
            TimeoutError,
        ) as e:
            logger.error(f"Error generating LLM completion: {e}", exc_info=True)
            raise LLMServiceError(f"LLM completion generation failed: {str(e)}") from e

    async def chat_completion_stream(
        self,
        messages: list[dict[str, str]],
        provider_name: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        stream_reasoning: bool = True,
        **kwargs,
    ):
        """
        Generate a streaming chat completion response.

        Args:
            messages: List of message dicts with 'role' and 'content' keys
            provider_name: Named provider to use
            model: Optional model string in ``provider/model`` format
            temperature: Sampling temperature (0.0-2.0)
            max_tokens: Maximum tokens to generate
            reasoning_effort: Reasoning effort level (``"none"``, ``"low"``, ``"medium"``, ``"high"``)
            stream_reasoning: If True, streams reasoning as separate chunks
            **kwargs: Additional provider-specific parameters

        Yields:
            str: Content chunks OR reasoning chunks (with ``__REASONING__`` prefix)
        """
        provider_name = provider_name or self.default_provider_name
        provider = self.get_provider(provider_name)
        if temperature == 0.7:
            temperature = provider.default_temperature

        async for chunk in provider.chat_completion_stream(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            stream_reasoning=stream_reasoning,
            **kwargs,
        ):
            yield chunk


def get_llm_service(
    config: dict[str, Any] | None = None,
) -> LLMService:
    """
    Create LLMService instance with auto-detected or configured LLM provider.

    Auto-detects from environment variables or uses manifest config.
    Model format: ``"provider/model"``
    (e.g., ``"openai/gpt-4o"``, ``"gemini/gemini-3-flash-preview"``)

    Args:
        config: Configuration dict (from manifest.json llm_config)
               Requires:
               - providers: Dict mapping provider names to model strings
                 (e.g., ``{"chat": "openai/gpt-4o"}``)

    Returns:
        LLMService instance
    """
    return LLMService(config=config)
