"""
LLM Service for Chat Completions and Text Generation

This module provides a unified interface for LLM providers using LiteLLM.
Supports 100+ LLM providers through LiteLLM's unified interface.

Key Features:
- Auto-detection of provider from environment variables or manifest config
- Unified API across all providers (OpenAI-compatible)
- Support for chat completions and streaming
- Configurable via manifest.json
- FastAPI dependency injection support
- Powered by LiteLLM for maximum provider support

Dependencies:
    pip install litellm

Supported Providers (via LiteLLM):
- OpenAI, Azure OpenAI, Anthropic, Google Gemini, Cohere, HuggingFace,
  Bedrock, Vertex AI, Ollama, Groq, Together AI, and 90+ more
  See: https://docs.litellm.ai/docs/providers
"""

import logging
import os
import time
from typing import Any, TypeVar

# LiteLLM import
try:
    import litellm
    from litellm import acompletion, completion

    LITELLM_AVAILABLE = True
except ImportError:
    LITELLM_AVAILABLE = False
    litellm = None
    acompletion = None
    completion = None

# Pydantic import for structured output
try:
    from pydantic import BaseModel

    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False
    BaseModel = None

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger(__name__)


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

    # Handle markdown code blocks (```json ... ``` or ``` ... ```)
    # Some LLMs wrap JSON responses in markdown code blocks
    import re

    json_match = re.search(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL)
    if json_match:
        content = json_match.group(1).strip()
        logger.debug("Extracted JSON from markdown code block")

    # Parse JSON
    try:
        import json

        data = json.loads(content)
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse JSON response: {e}. Content preview: {content[:200]}...")
        raise ValueError(f"Invalid JSON response: {e}") from e

    # Validate with Pydantic if model provided
    if pydantic_model and PYDANTIC_AVAILABLE and BaseModel is not None:
        try:
            # Use model_validate for dict, or model_validate_json for string
            if isinstance(data, dict):
                return pydantic_model.model_validate(data)
            else:
                # If data is not a dict, try parsing as JSON string again
                return pydantic_model.model_validate_json(content)
        except (ValueError, TypeError, AttributeError) as e:
            logger.warning(
                f"Pydantic validation failed: {e}. "
                f"Data type: {type(data)}, preview: {str(data)[:200] if data else 'None'}"
            )
            # Re-raise to let caller handle fallback
            raise ValueError(f"Pydantic validation failed: {e}") from e

    return data


class LLMServiceError(Exception):
    """Base exception for LLM service failures."""

    pass


def _detect_provider_from_env() -> str:
    """
    Detect provider from environment variables for LiteLLM.

    Returns:
        LiteLLM model string
        (e.g., "openai/gpt-4o", "azure/gpt-4o", "gemini/gemini-3-flash-preview")
    """
    if not LITELLM_AVAILABLE:
        return "openai/gpt-4o"  # Default fallback

    # Check for Azure OpenAI
    if os.getenv("AZURE_OPENAI_API_KEY") and os.getenv("AZURE_OPENAI_ENDPOINT"):
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o")
        return f"azure/{deployment}"
    # Check for Gemini
    elif os.getenv("GEMINI_API_KEY"):
        return "gemini/gemini-3-flash-preview"
    # Check for OpenAI
    elif os.getenv("OPENAI_API_KEY"):
        return "openai/gpt-4o"
    else:
        # Default to OpenAI format
        return "openai/gpt-4o"


def _format_response_format_for_provider(response_format: Any, model: str | None = None) -> Any:
    """
    Format response_format based on provider for optimal structured output.

    Args:
        response_format: Pydantic BaseModel class or dict
        model: LiteLLM model string (e.g., "gemini/gemini-3-flash-preview", "openai/gpt-4o")

    Returns:
        Formatted response_format for the provider

    Behavior:
        - Gemini: Uses {"type": "json_object", "response_schema": schema}
          for strict schema validation
        - OpenAI/Azure OpenAI: Uses Pydantic models directly
          (LiteLLM converts to function calling)
        - Other providers: Uses Pydantic models directly
          (LiteLLM handles conversion)

    Note:
        For Gemini, we use response_schema explicitly for production-grade structured output.
        This ensures Gemini adheres to the exact schema structure.
        For OpenAI/Azure, Pydantic models are converted to function calling format automatically.
    """
    if not response_format:
        return None

    # If it's already a dict (e.g., {"type": "json_object"}), pass through
    if isinstance(response_format, dict):
        return response_format

    # If it's a Pydantic model, format based on provider
    if (
        PYDANTIC_AVAILABLE
        and BaseModel is not None
        and isinstance(response_format, type)
        and issubclass(response_format, BaseModel)
    ):
        # Detect provider from model string
        model_lower = (model or "").lower()

        # For Gemini models, use response_schema format for strict validation
        # This is the recommended approach for production use with Gemini
        # Format: {"type": "json_object", "response_schema": schema}
        # This ensures Gemini adheres to the exact schema structure
        if "gemini" in model_lower or model_lower.startswith("vertex_ai/"):
            try:
                # Generate JSON schema from Pydantic model using model_json_schema()
                # This is the recommended way for Gemini structured output
                schema = response_format.model_json_schema()
                logger.debug(
                    f"Using Gemini response_schema format for model '{model}': "
                    f"schema with {len(schema.get('properties', {}))} properties"
                )
                # For Gemini, use response_schema format for strict validation
                # Optionally add enforce_validation for schema validation (LiteLLM v1.40.1+)
                response_format_dict = {"type": "json_object", "response_schema": schema}
                # Note: enforce_validation can be added for strict schema validation
                # but may cause errors if LLM doesn't perfectly match schema
                # For now, we rely on Pydantic validation after parsing
                return response_format_dict
            except (AttributeError, TypeError, ValueError) as e:
                logger.warning(
                    f"Failed to generate JSON schema from Pydantic model for Gemini, "
                    f"falling back to direct model: {e}"
                )
                # Fallback: pass Pydantic model directly (LiteLLM will handle it)
                return response_format

        # For OpenAI/Azure OpenAI, pass Pydantic model directly
        # LiteLLM converts to function calling format (structured outputs)
        # This works well for OpenAI's structured outputs feature
        elif model_lower.startswith("openai/") or model_lower.startswith("azure/"):
            logger.debug(
                f"Using OpenAI/Azure function calling format for model '{model}' "
                f"(Pydantic model passed directly)"
            )
            return response_format

        # For other providers, pass Pydantic model directly
        # LiteLLM handles conversion automatically
        else:
            logger.debug(
                f"Using default Pydantic model format for model '{model}' "
                f"(provider-specific conversion handled by LiteLLM)"
            )
            return response_format

    # Fallback: pass through as-is
    return response_format


class LLMProvider:
    """
    Standalone LLM provider wrapper using LiteLLM.

    Auto-detects provider from environment variables or config.
    Supports 100+ LLM providers through LiteLLM.

    Example:
        # Auto-detects from environment variables
        provider = LLMProvider()

        # Or configure via manifest
        provider = LLMProvider(config={"default_model": "gemini/gemini-3-flash-preview"})
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
    ):
        """
        Initialize LLM Provider using LiteLLM.

        Args:
            config: Optional dict with LLM configuration (from manifest.json llm_config)
                   Supports:
                   - default_model: LiteLLM model string
                     (e.g., "openai/gpt-4o", "gemini/gemini-3-flash-preview")
                   - fallbacks: List of fallback models
                     (e.g., ["gpt-4o-mini", "claude-3-5-haiku-20241022"])
                   - tools: List of tool definitions for function calling/grounding
                     (e.g., [{"google_search": {}}] for Gemini Google Search)
                   - litellm_config: Dict of LiteLLM configuration options
                     (e.g., {"request_timeout": 60, "num_retries": 2})
                   - provider: Legacy format (e.g., "openai", "azure", "gemini")
                     - will be converted to LiteLLM format

        Raises:
            LLMServiceError: If LiteLLM is not available
        """
        if not LITELLM_AVAILABLE:
            raise LLMServiceError("LiteLLM not available. Install with: pip install litellm")

        config = config or {}

        # Handle legacy provider format or use LiteLLM model format
        default_model = config.get("default_model")
        provider = config.get("provider")

        if default_model:
            # Use provided model (should be in LiteLLM format: "provider/model")
            # For Azure, this should be the deployment name, not model name
            self.default_model = default_model
        elif provider:
            # Convert legacy provider format to LiteLLM format
            model_name = config.get("model_name", "gpt-4o")
            if provider == "azure":
                # Azure uses deployment names, not model names
                # Check if model_name looks like a deployment name or model name
                deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", model_name)
                self.default_model = f"azure/{deployment}"
                logger.warning(
                    f"⚠️ Using Azure deployment name '{deployment}'. "
                    f"Make sure this matches your Azure AI Studio deployment name, "
                    f"not the underlying model name."
                )
            elif provider == "gemini":
                self.default_model = f"gemini/{model_name}"
            else:
                self.default_model = f"{provider}/{model_name}"
        else:
            # Auto-detect from environment
            self.default_model = _detect_provider_from_env()

        # Extract fallbacks (list of model strings)
        self.fallbacks = config.get("fallbacks", [])
        if self.fallbacks:
            logger.info(f"✅ LLM Provider configured with fallbacks: {self.fallbacks}")

        # Extract tools (list of tool definitions)
        self.tools = config.get("tools", [])
        if self.tools:
            logger.info(f"✅ LLM Provider configured with tools: " f"{len(self.tools)} tool(s)")

        # Extract LiteLLM config (passed directly to LiteLLM)
        self.litellm_config = config.get("litellm_config", {})
        if self.litellm_config:
            logger.info(
                f"✅ LLM Provider configured with LiteLLM options: "
                f"{list(self.litellm_config.keys())}"
            )
            # Apply LiteLLM config globally if needed
            # Some configs like request_timeout, num_retries can be set globally
            for key, value in self.litellm_config.items():
                if hasattr(litellm, key):
                    setattr(litellm, key, value)
                    logger.debug(f"Set LiteLLM.{key} = {value}")

        # Extract temperature and persona from config
        self.default_temperature = config.get("temperature", 0.7)
        self.default_persona = config.get("persona", "helpful assistant")

        logger.info(f"✅ LLM Provider initialized (model: {self.default_model})")
        self.config = config

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        response_format: Any | None = None,
        **kwargs,
    ) -> str:
        """
        Generate a chat completion response using LiteLLM.

        Args:
            messages: List of message dicts with 'role' and 'content' keys
            model: Optional LiteLLM model string
                   (e.g., "openai/gpt-4o", "gemini/gemini-3-flash-preview")
                   Overrides default_model if provided
            temperature: Sampling temperature (0.0-2.0)
            max_tokens: Maximum tokens to generate
            response_format: Optional response format. Can be:
                           - Pydantic BaseModel class (for structured output)
                           - dict with "type": "json_object" (for JSON mode)
                           - None (for free-form text)
            **kwargs: Additional provider-specific parameters

        Returns:
            str: The generated response text

        Example:
            ```python
            # Free-form text
            response = await provider.chat_completion(
                messages=[{"role": "user", "content": "Hello!"}],
                model="openai/gpt-4o"
            )

            # JSON mode
            response = await provider.chat_completion(
                messages=[{"role": "user", "content": "Extract data"}],
                response_format={"type": "json_object"}
            )

            # Structured output with Pydantic
            from pydantic import BaseModel
            class Movie(BaseModel):
                title: str
                year: int

            response = await provider.chat_completion(
                messages=[{"role": "user", "content": "Extract movie info"}],
                response_format=Movie
            )
            ```
        """
        start_time = time.time()
        model_to_use = model or self.default_model

        try:
            # Inject persona as system message if no system message exists
            messages_to_use = messages.copy()
            has_system_message = any(msg.get("role") == "system" for msg in messages_to_use)
            if not has_system_message and self.default_persona:
                messages_to_use.insert(0, {"role": "system", "content": self.default_persona})

            # Format response_format based on provider for optimal structured output
            # - Gemini: Pydantic models → response_schema (via LiteLLM)
            # - OpenAI/Azure: Pydantic models → function calling format
            #   (structured outputs via LiteLLM)
            formatted_response_format = _format_response_format_for_provider(
                response_format, model_to_use
            )
            if formatted_response_format:
                kwargs["response_format"] = formatted_response_format

            # Add fallbacks if configured
            if self.fallbacks:
                kwargs["fallbacks"] = self.fallbacks
                logger.debug(f"Using fallback models: {self.fallbacks}")

            # Add tools if configured (allow override via explicit kwargs)
            if self.tools and "tools" not in kwargs:
                kwargs["tools"] = self.tools
                logger.debug(f"Using configured tools: {len(self.tools)} tool(s)")

            # Merge LiteLLM config into kwargs (overrides any explicit kwargs)
            # LiteLLM config takes precedence for things like num_retries, request_timeout, etc.
            litellm_kwargs = self.litellm_config.copy()
            # Don't override explicit parameters passed to the method
            for key, value in litellm_kwargs.items():
                if key not in kwargs:
                    kwargs[key] = value

            # Use config temperature as default if using default parameter value
            requested_temperature = temperature if temperature != 0.7 else self.default_temperature

            # Enforce provider-specific temperature rules
            # Gemini models: Always use temperature=1.0
            # Azure OpenAI and OpenAI: Always use temperature=0.3
            model_lower = (model_to_use or "").lower()
            if model_lower.startswith("gemini/"):
                # Gemini: Always use 1.0
                final_temperature = 1.0
                if requested_temperature != 1.0:
                    logger.info(
                        f"⚠️  Enforcing temperature=1.0 for Gemini model '{model_to_use}' "
                        f"(config: {requested_temperature})"
                    )
            elif model_lower.startswith("azure/") or model_lower.startswith("openai/"):
                # Azure OpenAI and OpenAI: Always use 0.3
                final_temperature = 0.3
                if requested_temperature != 0.3:
                    logger.info(
                        f"⚠️  Enforcing temperature=0.3 for OpenAI/Azure model '{model_to_use}' "
                        f"(config: {requested_temperature})"
                    )
            else:
                final_temperature = requested_temperature

            # Use LiteLLM's async completion
            response = await acompletion(
                model=model_to_use,
                messages=messages_to_use,
                temperature=final_temperature,
                max_tokens=max_tokens,
                **kwargs,
            )

            # Extract text from LiteLLM response (OpenAI-compatible format)
            if hasattr(response, "choices") and response.choices:
                content = response.choices[0].message.content
                if content:
                    duration = time.time() - start_time
                    # Log which model was actually used
                    # (may differ from model_to_use if fallback was used)
                    actual_model = getattr(response, "model", model_to_use)
                    logger.info(
                        "LLM_COMPLETION_SUCCESS",
                        extra={
                            "latency_sec": round(duration, 3),
                            "requested_model": model_to_use,
                            "actual_model": actual_model,
                        },
                    )
                    if actual_model != model_to_use:
                        logger.info(
                            f"✅ Used fallback model: {actual_model} (requested: {model_to_use})"
                        )
                    return content

            # Fallback extraction
            if hasattr(response, "content"):
                return str(response.content)

            return str(response)

        except (
            ValueError,
            TypeError,
            AttributeError,
            RuntimeError,
            ConnectionError,
            TimeoutError,
        ) as e:
            # Catch specific exceptions that can occur during LLM completion
            # ValueError: Invalid parameters
            # TypeError: Type errors
            # AttributeError: Missing attributes
            # RuntimeError: Runtime issues
            # ConnectionError: Network errors
            # TimeoutError: Timeout errors
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
        Generate a streaming chat completion response using LiteLLM.

        Yields chunks of the response as they are generated, providing
        real-time feedback for better UX.

        Reasoning/thinking content from models like Gemini 3 is streamed as
        SEPARATE events (not mixed with content) so the frontend can render
        them as expandable "AI Thinking" bubbles.

        Args:
            messages: List of message dicts with 'role' and 'content' keys
            model: Optional LiteLLM model string
            temperature: Sampling temperature (0.0-2.0)
            max_tokens: Maximum tokens to generate
            reasoning_effort: Reasoning effort level for Gemini 3+ models.
                             Options: "none", "low", "medium", "high"
                             - "none": Disables thinking (cheapest, fastest)
                             - "low": Minimizes latency and cost
                             - "medium": Balanced reasoning
                             - "high": Maximizes reasoning depth
                             LiteLLM maps this to thinking_level for Gemini 3.
            stream_reasoning: If True (default), yields reasoning as separate
                             chunks prefixed with __REASONING__: for the frontend
                             to render as expandable thinking bubbles.
                             If False, reasoning is only logged (not streamed).
            **kwargs: Additional provider-specific parameters

        Yields:
            str: Content chunks (plain text) OR reasoning chunks (prefixed with __REASONING__:)
                 The frontend should parse these to render:
                 - Plain text -> main response bubble
                 - __REASONING__:... -> collapsible "AI Thinking" bubble

        Example:
            async for chunk in provider.chat_completion_stream(
                messages=[{"role": "user", "content": "Hello!"}],
                reasoning_effort="low",  # For Gemini 3 models
                stream_reasoning=True,   # Enable thinking bubbles
            ):
                if chunk.startswith("__REASONING__:"):
                    # Handle as thinking bubble
                    reasoning = chunk[14:]  # Strip prefix
                else:
                    # Handle as main response content
                    print(chunk, end="", flush=True)
        """
        model_to_use = model or self.default_model
        reasoning_buffer = []  # Capture full reasoning for audit trail
        reasoning_started = False  # Track if we've sent the reasoning start marker

        try:
            # Inject persona as system message if no system message exists
            messages_to_use = messages.copy()
            has_system_message = any(msg.get("role") == "system" for msg in messages_to_use)
            if not has_system_message and self.default_persona:
                messages_to_use.insert(0, {"role": "system", "content": self.default_persona})

            # Add fallbacks if configured
            if self.fallbacks:
                kwargs["fallbacks"] = self.fallbacks

            # Add tools if configured (allow override via explicit kwargs)
            if self.tools and "tools" not in kwargs:
                kwargs["tools"] = self.tools
                logger.debug(f"Using configured tools: {len(self.tools)} tool(s)")

            # Merge LiteLLM config into kwargs
            litellm_kwargs = self.litellm_config.copy()
            for key, value in litellm_kwargs.items():
                if key not in kwargs:
                    kwargs[key] = value

            # Use config temperature as default if using default parameter value
            requested_temperature = temperature if temperature != 0.7 else self.default_temperature

            # Enforce provider-specific temperature rules
            # Gemini models: Always use temperature=1.0
            # Azure OpenAI and OpenAI: Always use temperature=0.3
            model_lower = (model_to_use or "").lower()
            if model_lower.startswith("gemini/"):
                # Gemini: Always use 1.0
                final_temperature = 1.0
                if requested_temperature != 1.0:
                    logger.info(
                        f"⚠️  Enforcing temperature=1.0 for Gemini model '{model_to_use}' "
                        f"(config: {requested_temperature})"
                    )
            elif model_lower.startswith("azure/") or model_lower.startswith("openai/"):
                # Azure OpenAI and OpenAI: Always use 0.3
                final_temperature = 0.3
                if requested_temperature != 0.3:
                    logger.info(
                        f"⚠️  Enforcing temperature=0.3 for OpenAI/Azure model '{model_to_use}' "
                        f"(config: {requested_temperature})"
                    )
            else:
                final_temperature = requested_temperature

            # Add reasoning_effort for Gemini 3+ models (maps to thinking_level)
            if reasoning_effort and model_to_use:
                # LiteLLM handles mapping reasoning_effort to thinking_level for Gemini 3
                kwargs["reasoning_effort"] = reasoning_effort
                logger.debug(
                    f"Using reasoning_effort='{reasoning_effort}' for model '{model_to_use}'"
                )

            # Use LiteLLM's async streaming completion
            response = await acompletion(
                model=model_to_use,
                messages=messages_to_use,
                temperature=final_temperature,
                max_tokens=max_tokens,
                stream=True,  # Enable streaming
                **kwargs,
            )

            # Yield chunks as they arrive
            async for chunk in response:
                if hasattr(chunk, "choices") and chunk.choices:
                    delta = chunk.choices[0].delta

                    # Handle reasoning_content FIRST (comes before main content)
                    # Stream as separate event for frontend to render as thinking bubble
                    if hasattr(delta, "reasoning_content") and delta.reasoning_content:
                        reasoning_buffer.append(delta.reasoning_content)
                        if stream_reasoning:
                            # Send start marker on first reasoning chunk
                            if not reasoning_started:
                                yield "__REASONING_START__"
                                reasoning_started = True
                            # Stream reasoning content (frontend renders as thinking bubble)
                            yield f"__REASONING__:{delta.reasoning_content}"

                    # Yield main content to user (separate from reasoning)
                    if hasattr(delta, "content") and delta.content:
                        # If we were streaming reasoning, signal end before content starts
                        if reasoning_started and stream_reasoning:
                            yield "__REASONING_END__"
                            reasoning_started = False
                        yield delta.content

            # Send final reasoning end marker if needed
            if reasoning_started and stream_reasoning:
                yield "__REASONING_END__"

            # Log reasoning for audit trail (if any was captured)
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
            logger.exception(f"LLM_STREAM_FAILED: {str(e)}")
            raise LLMServiceError(f"LLM streaming failed: {str(e)}") from e


class LLMService:
    """
    Service for LLM chat completions and text generation using LiteLLM.

    This service provides a unified interface for 100+ LLM providers through LiteLLM:
    - OpenAI, Azure OpenAI, Anthropic, Google Gemini, Cohere, HuggingFace,
      Bedrock, Vertex AI, Ollama, Groq, Together AI, and 90+ more

    Example:
        from mdb_engine.llm import LLMService
        from pydantic import BaseModel

        # Initialize (auto-detects from environment variables or manifest config)
        llm_service = LLMService(config={"default_model": "openai/gpt-4o"})

        # Generate completion
        response = await llm_service.chat_completion(
            messages=[{"role": "user", "content": "Hello!"}]
        )

        # Structured output with Pydantic
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
        llm_provider: LLMProvider | None = None,
        config: dict[str, Any] | None = None,
    ):
        """
        Initialize LLM Service.

        Args:
            llm_provider: LLMProvider instance (optional, will create default if None)
            config: Optional configuration dict (from manifest.json llm_config)
                   Supports:
                   - default_model: LiteLLM model string
                     (e.g., "openai/gpt-4o", "gemini/gemini-3-flash-preview")
                   - tools: List of tool definitions for function calling/grounding
                     (e.g., [{"google_search": {}}] for Gemini Google Search)
                   - provider: Legacy format (e.g., "openai", "azure", "gemini")

        Raises:
            LLMServiceError: If LiteLLM is not available
        """
        if not LITELLM_AVAILABLE:
            raise LLMServiceError("LiteLLM not available. Install with: pip install litellm")

        # Create LLM provider if not provided
        if llm_provider is None:
            llm_provider = LLMProvider(config=config)

        self.llm_provider = llm_provider
        self.config = config or {}

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        response_format: Any | None = None,
        **kwargs,
    ) -> str:
        """
        Generate a chat completion response using LiteLLM.

        Args:
            messages: List of message dicts with 'role' and 'content' keys.
                     Format: [{"role": "system", "content": "..."},
                              {"role": "user", "content": "..."}, ...]
            model: Optional LiteLLM model string
                   (e.g., "openai/gpt-4o", "gemini/gemini-3-flash-preview")
                   Overrides default_model if provided
            temperature: Sampling temperature (0.0-2.0)
            max_tokens: Maximum tokens to generate
            response_format: Optional response format. Can be:
                           - Pydantic BaseModel class (for structured output)
                             * Gemini: Automatically converted to
                               {"type": "json_object", "response_schema": schema}
                               Uses model_json_schema() for strict schema validation
                               (production-grade)
                             * OpenAI/Azure: Converted to function calling format
                               (structured outputs)
                             * Other providers: LiteLLM handles conversion automatically
                           - dict with "type": "json_object" (for JSON mode without schema)
                           - dict with "type": "json_object", "response_schema": {...}
                             (for Gemini strict schema)
                           - None (for free-form text)
            **kwargs: Additional provider-specific parameters
                     (e.g., tools, tool_choice for function calling/grounding)

        Returns:
            str: The generated response text (JSON string for structured output)

        Example:
            # Free-form text
            response = await service.chat_completion(
                messages=[
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": "What is the capital of France?"}
                ],
                model="openai/gpt-4o"
            )

            # Structured output with Pydantic (provider-aware)
            from pydantic import BaseModel
            class Movie(BaseModel):
                title: str
                year: int

            response = await service.chat_completion(
                messages=[{"role": "user", "content": "Extract movie info"}],
                response_format=Movie
            )
            # For Gemini: Automatically uses
            # {"type": "json_object", "response_schema": Movie.model_json_schema()}
            # For OpenAI/Azure: Automatically uses function calling format
            # (structured outputs)

            # Parse response (works the same for all providers)
            movie = Movie.model_validate_json(response)
        """
        try:
            # Use provider's default temperature if not explicitly provided
            if temperature == 0.7:  # Default parameter value
                temperature = self.llm_provider.default_temperature

            response = await self.llm_provider.chat_completion(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
                **kwargs,
            )
            logger.info("Generated LLM completion successfully")
            return response
        except (
            LLMServiceError,
            ValueError,
            TypeError,
            RuntimeError,
            ConnectionError,
            TimeoutError,
        ) as e:
            # Catch specific exceptions that can occur during LLM completion
            # LLMServiceError: From the provider
            # ValueError: Invalid parameters
            # TypeError: Type errors
            # RuntimeError: Runtime issues
            # ConnectionError: Network errors
            # TimeoutError: Timeout errors
            logger.error(f"Error generating LLM completion: {e}", exc_info=True)
            raise LLMServiceError(f"LLM completion generation failed: {str(e)}") from e

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

        Yields chunks of the response as they are generated for real-time UX.

        Reasoning/thinking content from models like Gemini 3 is streamed as
        SEPARATE events so the frontend can render them as expandable
        "AI Thinking" bubbles - keeping reasoning visible but not mixed
        with the main response.

        Args:
            messages: List of message dicts with 'role' and 'content' keys
            model: Optional LiteLLM model string
            temperature: Sampling temperature (0.0-2.0)
            max_tokens: Maximum tokens to generate
            reasoning_effort: Reasoning effort level for Gemini 3+ models.
                             Options: "none", "low", "medium", "high"
                             Use "none" for cheapest/fastest responses.
            stream_reasoning: If True (default), streams reasoning as separate
                             chunks for frontend to render as thinking bubbles.
            **kwargs: Additional provider-specific parameters

        Yields:
            str: Content chunks OR reasoning chunks (with __REASONING__ prefix)

        Chunk Types:
            - Plain text: Main response content
            - __REASONING_START__: Signals start of thinking
            - __REASONING__:text: Reasoning content chunk
            - __REASONING_END__: Signals end of thinking

        Example:
            from fastapi.responses import StreamingResponse

            async def stream_generator():
                async for chunk in llm_service.chat_completion_stream(
                    messages=[{"role": "user", "content": "Tell me a story"}],
                    reasoning_effort="low",
                    stream_reasoning=True,
                ):
                    if chunk.startswith("__REASONING"):
                        # Send as reasoning event for thinking bubble
                        yield f"data: {json.dumps({'type': 'reasoning', 'content': chunk})}\\n\\n"
                    else:
                        yield f"data: {json.dumps({'type': 'chunk', 'content': chunk})}\\n\\n"

            return StreamingResponse(stream_generator(), media_type="text/event-stream")
        """
        try:
            # Use provider's default temperature if not explicitly provided
            if temperature == 0.7:  # Default parameter value
                temperature = self.llm_provider.default_temperature

            async for chunk in self.llm_provider.chat_completion_stream(
                messages=messages,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                reasoning_effort=reasoning_effort,
                stream_reasoning=stream_reasoning,
                **kwargs,
            ):
                yield chunk
        except (
            LLMServiceError,
            ValueError,
            TypeError,
            RuntimeError,
            ConnectionError,
            TimeoutError,
        ) as e:
            logger.error(f"Error in streaming LLM completion: {e}", exc_info=True)
            raise LLMServiceError(f"LLM streaming failed: {str(e)}") from e


# Dependency injection helper
def get_llm_service(
    config: dict[str, Any] | None = None,
) -> LLMService:
    """
    Create LLMService instance with auto-detected or configured LLM provider.

    Uses LiteLLM to support 100+ LLM providers.
    Auto-detects from environment variables or uses manifest config.
    Model format: "provider/model"
    (e.g., "openai/gpt-4o", "gemini/gemini-3-flash-preview")

    Args:
        config: Optional configuration dict (from manifest.json llm_config)
               Supports:
               - default_model: LiteLLM model string
                 (e.g., "openai/gpt-4o", "gemini/gemini-3-flash-preview")
               - tools: List of tool definitions for function calling/grounding
                 (e.g., [{"google_search": {}}] for Gemini Google Search)
               - provider: Legacy format (e.g., "openai", "azure", "gemini")
                 - will be converted

    Returns:
        LLMService instance

    Example:
        from mdb_engine.llm import get_llm_service

        # Auto-detects from environment variables
        llm_service = get_llm_service()

        # Or configure explicitly
        llm_service = get_llm_service(
            config={"default_model": "gemini/gemini-3-flash-preview"}
        )
    """
    return LLMService(config=config)
