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
from dataclasses import dataclass, field
from typing import Any, TypeVar
from urllib.parse import urlparse

from ..exceptions import MongoDBEngineError
from ..observability.tracing import create_span
from .capabilities import ModelCapabilities, default_grounding_model, filter_registry, resolve_capabilities
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

# Keywords used to detect when an API error is caused by an unsupported or
# invalid tools/grounding configuration, so we can transparently retry without
# the tools (e.g. a model that doesn't support Google Search grounding).
_TOOLS_ERROR_KEYWORDS = (
    "tool",
    "google_search",
    "googlesearch",
    "grounding",
    "function_declarations",
)


def _is_thinking_config_error(exc: Exception) -> bool:
    """Heuristic check: did this error originate from the thinking config?

    Used to decide whether a failed Gemini request is worth retrying without
    the thinking config (vs. a genuine failure like auth/quota that should
    propagate to the resilience layer).
    """
    msg = str(exc).lower()
    return any(keyword in msg for keyword in _THINKING_ERROR_KEYWORDS)


def _is_tools_config_error(exc: Exception) -> bool:
    """Heuristic check: did this error originate from the tools/grounding config?

    Used to decide whether a failed Gemini request is worth retrying without
    the tools (e.g. an older model that doesn't support Google Search
    grounding) vs. a genuine failure like auth/quota that should propagate to
    the resilience layer.
    """
    msg = str(exc).lower()
    return any(keyword in msg for keyword in _TOOLS_ERROR_KEYWORDS)


@dataclass(frozen=True)
class GroundedCompletion:
    """Structured result for a (optionally) grounded chat completion.

    Returned by ``chat_completion`` when ``return_metadata=True`` so callers
    can access grounding citations alongside the generated text without
    breaking the default ``str`` return type.

    Attributes:
        text: The generated response text.
        citations: Grounding sources as
            ``[{"title", "uri", "domain", "redirect_uri"}, ...]``.
            Empty when the response was not grounded.
        grounded: ``True`` when at least one grounding citation was returned.
        model_used: The model that actually produced this answer. Differs from
            the requested model when ``grounding_policy="auto"`` routed the turn
            to a grounding-capable model.
        finish_reason: The provider finish reason (e.g. ``"STOP"``,
            ``"MAX_TOKENS"``, ``"SAFETY"``) when available, else ``None``.
    """

    text: str
    citations: list[dict[str, str]] = field(default_factory=list)
    grounded: bool = False
    model_used: str | None = None
    finish_reason: str | None = None


# ---------------------------------------------------------------------------
# Typed streaming events (alongside the legacy ``__REASONING__:`` /
# ``__GROUNDING__:`` string sentinels emitted by ``chat_completion_stream``).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StreamEvent:
    """Base class for typed streaming events yielded by ``LLMService.stream``."""


@dataclass(frozen=True)
class TextDelta(StreamEvent):
    """A chunk of visible answer text."""

    text: str


@dataclass(frozen=True)
class ReasoningDelta(StreamEvent):
    """A chunk of model reasoning / thinking (render as a collapsible trace)."""

    text: str


@dataclass(frozen=True)
class GroundingEvent(StreamEvent):
    """Grounding citations for the response (emitted once, near the end)."""

    citations: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class DoneEvent(StreamEvent):
    """Terminal event with summary metadata."""

    grounded: bool = False
    model_used: str | None = None
    citations: list[dict[str, str]] = field(default_factory=list)


def _build_gemini_tools(enable_web_search: bool, raw_tools: list | None) -> list | None:
    """Translate framework-level tool requests into ``google-genai`` Tool objects.

    Args:
        enable_web_search: When ``True``, append the Google Search grounding
            tool (Gemini 2.x / 3.x ``Tool(google_search=GoogleSearch())``).
        raw_tools: Optional list of already-built ``Tool`` objects or legacy
            dicts (e.g. ``{"googleSearch": {}}``) to translate.

    Returns:
        A list of ``Tool`` objects, or ``None`` when the SDK is unavailable or
        nothing usable was requested.
    """
    if genai_types is None:
        return None

    tools: list = []
    if enable_web_search:
        # Gemini 2.x / 3.x grounding tool. (1.5 used google_search_retrieval,
        # which is intentionally out of scope here.)
        tools.append(genai_types.Tool(google_search=genai_types.GoogleSearch()))

    for tool in raw_tools or []:
        if isinstance(tool, genai_types.Tool):
            tools.append(tool)
        elif isinstance(tool, dict) and ("googleSearch" in tool or "google_search" in tool):
            # Legacy alias: translate the dead {"googleSearch": {}} dict into a
            # real grounding tool instead of silently dropping it.
            tools.append(genai_types.Tool(google_search=genai_types.GoogleSearch()))
        else:
            logger.debug(f"Skipping unrecognized Gemini tool spec: {tool!r}")

    return tools or None


def _domain_from_citation(title: str, uri: str) -> str:
    """Best-effort clean domain for a grounding citation.

    Google Search grounding returns ``web.uri`` as a
    ``vertexaisearch.cloud.google.com/grounding-api-redirect/...`` *redirect*,
    with the real publisher in ``web.title`` (often already a bare domain like
    ``"livemint.com"``). Prefer the title when it looks like a domain, else fall
    back to parsing the URI's host.
    """
    candidate = (title or "").strip().lower()
    # A title like "livemint.com" or "www.bbc.co.uk" is itself the domain.
    if candidate and " " not in candidate and "." in candidate and "/" not in candidate:
        return candidate.removeprefix("www.")
    host = urlparse(uri or "").netloc.lower()
    return host.removeprefix("www.") if host else candidate


def _normalize_citation(web: Any) -> dict[str, str] | None:
    """Normalize a single ``grounding_chunks[*].web`` into a citation dict.

    Returns ``{title, uri, domain, redirect_uri}`` or ``None`` when there is no
    usable URI. ``uri`` is kept as the (redirect) link for backwards
    compatibility; ``domain`` is the clean publisher for display.
    """
    uri = getattr(web, "uri", None)
    if not uri:
        return None
    title = getattr(web, "title", "") or ""
    domain = _domain_from_citation(title, uri)
    return {
        "title": title or domain or uri,
        "uri": uri,
        "domain": domain,
        "redirect_uri": uri,
    }


def _extract_grounding_citations(response_or_chunk: Any) -> list[dict[str, str]]:
    """Pull normalized grounding citations from a Gemini response/chunk.

    Reads ``candidate.grounding_metadata.grounding_chunks[*].web`` and dedupes
    by URI. Each citation is ``{title, uri, domain, redirect_uri}``. Fully
    ``getattr``-guarded so it tolerates partial chunks and responses without any
    grounding metadata.
    """
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for cand in getattr(response_or_chunk, "candidates", None) or []:
        gm = getattr(cand, "grounding_metadata", None)
        for gc in getattr(gm, "grounding_chunks", None) or []:
            citation = _normalize_citation(getattr(gc, "web", None))
            if citation and citation["uri"] not in seen:
                seen.add(citation["uri"])
                out.append(citation)
    return out


def _extract_finish_reason(response: Any) -> str | None:
    """Return the first candidate's ``finish_reason`` as a string, if present.

    Surfaces ``STOP`` / ``MAX_TOKENS`` / ``SAFETY`` / ``RECITATION`` so callers
    can react (e.g. "answer truncated — raise the token budget") instead of
    seeing a generic failure. Handles both enum and string finish reasons.
    """
    for cand in getattr(response, "candidates", None) or []:
        reason = getattr(cand, "finish_reason", None)
        if reason is None:
            continue
        return getattr(reason, "name", None) or str(reason)
    return None


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


class GroundingUnsupportedError(LLMServiceError):
    """Raised when grounding is required but the resolved model can't ground.

    Only raised under ``grounding_policy="require"``. Lets apps that *must*
    return cited answers fail loudly instead of silently shipping zero
    citations.
    """


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


def _pkg_version(name: str) -> str | None:
    """Return an installed package version, or ``None`` if not installed."""
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version(name)
    except PackageNotFoundError:
        return None


def _aiohttp_readline_rejects_max_line_length() -> bool:
    """Detect the known ``google-genai`` x ``aiohttp`` streaming incompatibility.

    ``google-genai`` >= 2.4 calls ``StreamReader.readline(max_line_length=...)``,
    a kwarg only accepted by ``aiohttp`` >= 3.14. On older aiohttp this raises
    ``TypeError`` mid-stream. Returns ``True`` when aiohttp is installed *and*
    its ``readline`` does NOT accept ``max_line_length`` (i.e. streaming via the
    aiohttp transport is unsafe and we should force httpx).

    Returns ``False`` when aiohttp is absent (genai already uses httpx) or when
    aiohttp is new enough — both safe.
    """
    try:
        import inspect

        import aiohttp  # type: ignore[import-untyped]

        sig = inspect.signature(aiohttp.StreamReader.readline)
        return "max_line_length" not in sig.parameters
    except ImportError:
        return False
    except (ValueError, TypeError):
        # Couldn't introspect; assume safe rather than forcing a transport swap.
        return False


_SDK_SELFCHECK_DONE = False


def _llm_sdk_versions() -> dict[str, str | None]:
    """Snapshot of the relevant SDK versions for diagnostics."""
    return {
        "mdb-engine": _pkg_version("mdb-engine"),
        "google-genai": _pkg_version("google-genai"),
        "aiohttp": _pkg_version("aiohttp"),
        "openai": _pkg_version("openai"),
        "httpx": _pkg_version("httpx"),
    }


def _warn_on_known_bad_sdk_combo() -> None:
    """Log SDK versions once and warn on combos known to break streaming."""
    global _SDK_SELFCHECK_DONE
    if _SDK_SELFCHECK_DONE:
        return
    _SDK_SELFCHECK_DONE = True
    versions = _llm_sdk_versions()
    logger.debug(f"LLM SDK versions: {versions}")
    genai_v = versions.get("google-genai")
    if genai_v and _aiohttp_readline_rejects_max_line_length():
        logger.warning(
            "Detected google-genai %s with an aiohttp (%s) whose StreamReader.readline "
            "does not accept 'max_line_length'; async streaming via aiohttp would crash. "
            "The engine will force the httpx transport for Gemini. To silence this, install "
            "aiohttp>=3.14 (e.g. pip install 'mdb-engine[ai]').",
            genai_v,
            versions.get("aiohttp"),
        )


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
        # Capability-registry overrides + grounding routing target (manifest-driven).
        self.model_overrides: dict[str, dict[str, Any]] = config.get("model_overrides", {}) or {}
        self.grounding_model: str | None = config.get("grounding_model")
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
            _warn_on_known_bad_sdk_combo()
            api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
            # Self-heal the google-genai x aiohttp streaming crash: when aiohttp
            # is installed but too old to accept readline(max_line_length=...),
            # force the httpx async transport so streaming doesn't TypeError.
            if _aiohttp_readline_rejects_max_line_length():
                try:  # nosemgrep - intentional broad fallback so transport setup never breaks streaming
                    import httpx  # google-genai's own dependency

                    return genai.Client(
                        api_key=api_key,
                        http_options=genai_types.HttpOptions(httpx_async_client=httpx.AsyncClient()),
                    )
                except Exception as e:  # noqa: BLE001 - fall back to default transport
                    logger.warning(
                        f"Could not force httpx transport for Gemini ({type(e).__name__}: {e}); "
                        f"using the default transport. Install aiohttp>=3.14 if streaming crashes."
                    )
            return genai.Client(api_key=api_key)
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
        enable_web_search: bool = False,
        tools: Any | None = None,
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

        # Web search grounding / tools. Gemini rejects tools together with a
        # JSON response_schema, so structured output wins and grounding is
        # dropped with a warning rather than failing the request.
        grounding_requested = enable_web_search or bool(tools)
        if grounding_requested and response_format:
            logger.warning(
                "Gemini does not support tools/grounding together with a JSON response_format; "
                "ignoring grounding for this structured-output call."
            )
            grounding_requested = False
        if grounding_requested:
            gemini_tools = _build_gemini_tools(enable_web_search, tools)
            if gemini_tools:
                config_kwargs["tools"] = gemini_tools

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

        try:  # nosemgrep - broad catch is intentional; non-recoverable errors are re-raised below
            return await _dispatch(config_kwargs)
        except Exception as e:  # noqa: BLE001 - re-raised below unless it's a recoverable config error
            # Gracefully degrade: if the model rejected the thinking config or
            # the tools/grounding config (e.g. an older model that doesn't
            # support them), retry once without the offending key instead of
            # failing the whole request. Genuine errors (auth, quota, network)
            # don't match and propagate to the resilience layer.
            fallback_kwargs = dict(config_kwargs)
            recovered = False
            if "thinking_config" in fallback_kwargs and _is_thinking_config_error(e):
                logger.warning(
                    f"Gemini model '{model_name}' rejected the thinking config "
                    f"({type(e).__name__}: {e}); retrying without it."
                )
                fallback_kwargs.pop("thinking_config")
                recovered = True
            if "tools" in fallback_kwargs and _is_tools_config_error(e):
                logger.warning(
                    f"Gemini model '{model_name}' rejected the tools/grounding config "
                    f"({type(e).__name__}: {e}); retrying without it (ungrounded)."
                )
                fallback_kwargs.pop("tools")
                recovered = True
            if recovered:
                return await _dispatch(fallback_kwargs)
            raise

    def _extract_gemini_text(self, response: Any) -> str:
        """Extract text content from a Gemini response, skipping thought parts.

        Robust against empty/blocked/truncated candidates: a thinking model that
        spends its whole token budget on thoughts returns a candidate whose
        ``content.parts`` is explicitly ``None``. ``getattr(..., "parts", [])``
        would return that ``None`` (the attribute exists) and crash on iteration,
        so we coerce ``None`` to an empty list and return ``""`` cleanly.
        """
        try:
            text = response.text
            if text:
                return text
        except (AttributeError, ValueError):
            pass
        # Manual fallback
        parts_text: list[str] = []
        for candidate in getattr(response, "candidates", None) or []:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", None) or []:
                if getattr(part, "thought", False):
                    continue
                if getattr(part, "text", None):
                    parts_text.append(part.text)
        return "".join(parts_text)

    # ------------------------------------------------------------------
    # Capability awareness + grounding negotiation
    # ------------------------------------------------------------------

    def capabilities(self, model: str | None = None) -> ModelCapabilities:
        """Resolve capabilities for ``model`` (or this provider's default)."""
        return resolve_capabilities(model or self.default_model, self.model_overrides)

    def _grounding_target(self, provider_type: str) -> str | None:
        """Return the configured/curated grounding-capable model for a provider."""
        if self.grounding_model:
            return self.grounding_model
        return default_grounding_model(provider_type)

    def negotiate_grounding(
        self,
        model: str,
        enable_web_search: bool,
        grounding_policy: str = "best_effort",
    ) -> tuple[str, bool]:
        """Reconcile a grounding request with the model's real capabilities.

        Returns ``(model_to_use, attach_grounding)``. ``enable_web_search`` is
        never a silent no-op:

        - ``best_effort`` (default): attach grounding if supported, else log + skip.
        - ``require``: raise :class:`GroundingUnsupportedError` if unsupported.
        - ``auto``: route this turn to a grounding-capable model for the same
          provider (recorded as ``model_used``); if none is available, log + skip.
        """
        if not enable_web_search:
            return model, False

        caps = self.capabilities(model)
        if caps.web_search:
            return model, True

        policy = (grounding_policy or "best_effort").lower()
        if policy == "require":
            raise GroundingUnsupportedError(
                f"Model '{model}' does not surface web-search grounding "
                f"(grounding_policy='require'). Use a grounding-capable model "
                f"(e.g. gemini/gemini-2.5-flash) or grounding_policy='auto'."
            )
        if policy == "auto":
            target = self._grounding_target(caps.provider)
            if target and resolve_capabilities(target, self.model_overrides).web_search:
                logger.info(f"grounding_policy=auto: routing grounded turn from '{model}' to '{target}'.")
                return target, True
            logger.warning(
                f"grounding_policy=auto: no grounding-capable model available to route "
                f"'{model}'; continuing ungrounded."
            )
            return model, False

        # best_effort
        logger.warning(
            f"Model '{model}' does not surface web-search grounding; continuing ungrounded "
            f"(grounding_policy='best_effort')."
        )
        return model, False

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
        enable_web_search: bool = False,
        return_metadata: bool = False,
        **kwargs,
    ) -> str | GroundedCompletion:
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
            enable_web_search: Provider-agnostic switch to enable Google Search
                           grounding for Gemini. Ignored (with a warning) for
                           non-Gemini providers. Cannot be combined with a JSON
                           ``response_format`` (grounding is dropped if so).
            return_metadata: When ``True``, return a :class:`GroundedCompletion`
                           with ``text``/``citations``/``grounded`` instead of a
                           plain ``str``. Defaults to ``False`` (unchanged API).
            **kwargs: Additional provider-specific parameters

        Returns:
            str: The generated response text (default), or a
            :class:`GroundedCompletion` when ``return_metadata=True``.
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
                web_search_warned = False
                for try_model in models_to_try:
                    citations: list[dict[str, str]] = []
                    finish_reason: str | None = None
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
                                enable_web_search=enable_web_search,
                                tools=kwargs.get("tools"),
                            )
                            content = self._extract_gemini_text(response)
                            finish_reason = _extract_finish_reason(response)
                            if enable_web_search or kwargs.get("tools") or return_metadata:
                                citations = _extract_grounding_citations(response)
                        else:
                            if enable_web_search and not web_search_warned:
                                logger.warning(
                                    f"enable_web_search=True is not supported for provider '{ptype}'; "
                                    f"continuing without grounding."
                                )
                                web_search_warned = True
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
                            if response.choices:
                                fr = getattr(response.choices[0], "finish_reason", None)
                                finish_reason = str(fr) if fr is not None else None

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
                        if finish_reason:
                            span.set_attribute("gen_ai.response.finish_reason", finish_reason)

                        # Grounding observability: answers "is grounding actually
                        # firing in prod?" as a dashboard query, not a manual dig.
                        if enable_web_search or kwargs.get("tools"):
                            span.set_attribute("gen_ai.grounding.enabled", True)
                            span.set_attribute("gen_ai.grounding.citation_count", len(citations))
                            span.set_attribute("gen_ai.grounding.model_used", actual_model)

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
                            if return_metadata:
                                return GroundedCompletion(
                                    text=content,
                                    citations=citations,
                                    grounded=bool(citations),
                                    model_used=actual_model,
                                    finish_reason=finish_reason,
                                )
                            return content

                        if return_metadata:
                            return GroundedCompletion(
                                text=str(response),
                                citations=citations,
                                grounded=bool(citations),
                                model_used=actual_model,
                                finish_reason=finish_reason,
                            )
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
        enable_web_search: bool = False,
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
            enable_web_search: Provider-agnostic switch to enable Google Search
                             grounding for Gemini. Ignored (with a warning) for
                             non-Gemini providers. When grounding citations are
                             found, a single trailing ``__GROUNDING__:{json}``
                             event is emitted before the stream ends.
            **kwargs: Additional provider-specific parameters

        Yields:
            str: Content chunks (plain text), reasoning chunks (``__REASONING__:``
            prefix), or a single trailing grounding event (``__GROUNDING__:``).
        """
        model_to_use = model or self.default_model
        reasoning_buffer: list[str] = []
        reasoning_started = False
        grounding_requested = bool(enable_web_search or kwargs.get("tools"))
        grounding_citations: dict[str, dict[str, str]] = {}

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
                        enable_web_search=enable_web_search,
                        tools=kwargs.get("tools"),
                    )
                    async for chunk in stream:
                        # Only inspect grounding metadata when grounding was
                        # requested, so non-grounded streams aren't affected.
                        if grounding_requested:
                            for citation in _extract_grounding_citations(chunk):
                                grounding_citations.setdefault(citation["uri"], citation)
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
                    if enable_web_search:
                        logger.warning(
                            f"enable_web_search=True is not supported for provider '{ptype}'; "
                            f"continuing without grounding."
                        )
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

                # Emit grounding citations (if any) as a single trailing event,
                # mirroring the __REASONING__: convention. Callers that don't
                # care can ignore it.
                if grounding_requested:
                    span.set_attribute("gen_ai.grounding.enabled", True)
                    span.set_attribute("gen_ai.grounding.citation_count", len(grounding_citations))
                    span.set_attribute("gen_ai.grounding.model_used", model_to_use or "")
                if grounding_requested and grounding_citations:
                    yield "__GROUNDING__:" + json.dumps({"citations": list(grounding_citations.values())})

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

        # Capability-registry overrides + grounding routing target apply to every
        # provider so apps configure them once in llm_config.
        self.model_overrides: dict[str, dict[str, Any]] = config.get("model_overrides", {}) or {}
        grounding_model = config.get("grounding_model")

        for provider_name, model_string in providers_config.items():
            if not isinstance(model_string, str):
                raise LLMServiceError(
                    f"Provider '{provider_name}' must map to a model string, " f"got {type(model_string).__name__}"
                )
            provider_config: dict[str, Any] = {
                "default_model": model_string,
                "model_overrides": self.model_overrides,
            }
            if grounding_model:
                provider_config["grounding_model"] = grounding_model
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
        enable_web_search: bool = False,
        grounding_policy: str = "best_effort",
        return_metadata: bool = False,
        **kwargs,
    ) -> str | GroundedCompletion:
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
            enable_web_search: Provider-agnostic switch to enable Google Search
                          grounding for Gemini.
            grounding_policy: How to handle ``enable_web_search`` when the resolved
                          model can't actually ground (see capability registry):
                          ``"best_effort"`` (default — log + continue ungrounded),
                          ``"require"`` (raise :class:`GroundingUnsupportedError`),
                          or ``"auto"`` (transparently route the turn to a
                          grounding-capable model for the same provider).
            return_metadata: When ``True``, return a :class:`GroundedCompletion`
                          (``text``/``citations``/``grounded``/``model_used``/
                          ``finish_reason``) instead of a plain ``str``.
            **kwargs: Additional provider-specific parameters

        Returns:
            str: The generated response text (JSON string for structured output),
            or a :class:`GroundedCompletion` when ``return_metadata=True``.

        Raises:
            GroundingUnsupportedError: If ``grounding_policy="require"`` and the
                resolved model does not surface web-search grounding.
        """
        try:
            provider_name = provider_name or self.default_provider_name
            provider = self.get_provider(provider_name)
            if temperature == 0.7:
                temperature = provider.default_temperature

            # Capability-aware grounding negotiation (never a silent no-op).
            # Done here, outside the provider's resilience wrapper, so a
            # ``require`` failure surfaces cleanly instead of being retried.
            requested_model = model or provider.default_model
            effective_model, attach_grounding = provider.negotiate_grounding(
                requested_model, enable_web_search, grounding_policy
            )

            return await provider.chat_completion(
                messages=messages,
                model=effective_model,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
                reasoning_effort=reasoning_effort,
                enable_web_search=attach_grounding,
                return_metadata=return_metadata,
                **kwargs,
            )
        except GroundingUnsupportedError:
            # Deliberate, deterministic signal — surface as-is (don't wrap/retry).
            raise
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
        enable_web_search: bool = False,
        grounding_policy: str = "best_effort",
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
            enable_web_search: Provider-agnostic switch to enable Google Search
                          grounding for Gemini. When citations are found, a
                          single trailing ``__GROUNDING__:{json}`` event is
                          emitted (ignored with a warning for non-Gemini).
            grounding_policy: ``"best_effort"`` | ``"require"`` | ``"auto"`` —
                          see :meth:`chat_completion`.
            **kwargs: Additional provider-specific parameters

        Yields:
            str: Content chunks, reasoning chunks (``__REASONING__`` prefix), or
            a single trailing grounding event (``__GROUNDING__:`` prefix).

        Raises:
            GroundingUnsupportedError: If ``grounding_policy="require"`` and the
                resolved model does not surface web-search grounding.
        """
        provider_name = provider_name or self.default_provider_name
        provider = self.get_provider(provider_name)
        if temperature == 0.7:
            temperature = provider.default_temperature

        requested_model = model or provider.default_model
        effective_model, attach_grounding = provider.negotiate_grounding(
            requested_model, enable_web_search, grounding_policy
        )

        async for chunk in provider.chat_completion_stream(
            messages=messages,
            model=effective_model,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            stream_reasoning=stream_reasoning,
            enable_web_search=attach_grounding,
            **kwargs,
        ):
            yield chunk

    async def stream(
        self,
        messages: list[dict[str, str]],
        provider_name: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        stream_reasoning: bool = True,
        enable_web_search: bool = False,
        grounding_policy: str = "best_effort",
        **kwargs,
    ):
        """Typed streaming: yields :class:`StreamEvent` objects.

        A structured alternative to :meth:`chat_completion_stream` that removes
        ``startswith("__REASONING__")`` / ``startswith("__GROUNDING__")`` string
        sniffing. Emits :class:`TextDelta`, :class:`ReasoningDelta`,
        :class:`GroundingEvent`, and a terminal :class:`DoneEvent`.

        Example::

            async for ev in llm.stream(messages=[...], enable_web_search=True):
                match ev:
                    case TextDelta(text): ...
                    case ReasoningDelta(text): ...
                    case GroundingEvent(citations): ...
                    case DoneEvent(grounded=g, model_used=m): ...
        """
        provider_name = provider_name or self.default_provider_name
        provider = self.get_provider(provider_name)
        requested_model = model or provider.default_model
        effective_model, _attach = provider.negotiate_grounding(requested_model, enable_web_search, grounding_policy)

        citations: list[dict[str, str]] = []
        async for chunk in self.chat_completion_stream(
            messages=messages,
            provider_name=provider_name,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            stream_reasoning=stream_reasoning,
            enable_web_search=enable_web_search,
            grounding_policy=grounding_policy,
            **kwargs,
        ):
            if chunk in ("__REASONING_START__", "__REASONING_END__"):
                continue
            if chunk.startswith("__REASONING__:"):
                yield ReasoningDelta(text=chunk[len("__REASONING__:") :])
            elif chunk.startswith("__GROUNDING__:"):
                try:
                    payload = json.loads(chunk[len("__GROUNDING__:") :])
                    citations = payload.get("citations", []) or []
                except (ValueError, TypeError):
                    citations = []
                yield GroundingEvent(citations=citations)
            else:
                yield TextDelta(text=chunk)

        yield DoneEvent(
            grounded=bool(citations),
            model_used=effective_model,
            citations=citations,
        )

    # ------------------------------------------------------------------
    # Capability registry accessors (single source of truth for apps)
    # ------------------------------------------------------------------

    def get_capabilities(self, model: str | None = None) -> ModelCapabilities:
        """Resolve :class:`ModelCapabilities` for a model.

        Args:
            model: A ``provider/model`` (or bare) id. Defaults to the default
                provider's configured model.
        """
        if model is None:
            model = self.get_provider(self.default_provider_name).default_model
        return resolve_capabilities(model, self.model_overrides)

    def list_models(
        self,
        *,
        provider: str | None = None,
        web_search: bool | None = None,
        thinking: bool | None = None,
        vision: bool | None = None,
    ) -> list[ModelCapabilities]:
        """List curated models (with manifest overrides) matching the filters.

        Apps build their model selector / web-search toggle from this instead of
        hardcoding capability flags. Example::

            grounding = service.list_models(provider="gemini", web_search=True)
        """
        return filter_registry(
            provider=provider,
            web_search=web_search,
            thinking=thinking,
            vision=vision,
            overrides=self.model_overrides,
        )

    def supports(self, feature: str, model: str | None = None) -> bool:
        """Return whether ``model`` supports ``feature``.

        ``feature`` is one of ``"thinking"``, ``"web_search"``, ``"vision"``,
        ``"structured_output"``.
        """
        caps = self.get_capabilities(model)
        return bool(getattr(caps, feature, False))


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
