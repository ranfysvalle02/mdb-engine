"""
Unit tests for LLMService (standalone LLM Service module)

Tests the LLM service using native SDK mocks (OpenAI AsyncOpenAI and Google GenAI),
replacing the former litellm dependency. Coverage includes:

- Default provider initialization (backward compatibility)
- Named providers initialization
- Provider selection and isolation
- Chat completion with named providers
- Streaming with named providers
- Error handling when the OpenAI SDK is unavailable
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mdb_engine.llm import service as llm_service_module
from mdb_engine.llm.service import (
    DoneEvent,
    GroundedCompletion,
    GroundingEvent,
    GroundingUnsupportedError,
    LLMService,
    LLMServiceError,
    ReasoningDelta,
    TextDelta,
    _aiohttp_readline_rejects_max_line_length,
    _build_gemini_tools,
    _build_thinking_config,
    _clamp_thinking_budget,
    _domain_from_citation,
    _extract_finish_reason,
    _extract_grounding_citations,
    _is_thinking_config_error,
    _is_tools_config_error,
    get_llm_service,
)


class _FakeGoogleSearch:
    """Stand-in for ``google.genai.types.GoogleSearch`` in unit tests."""


class _FakeTool:
    """Stand-in for ``google.genai.types.Tool`` that records its kwargs.

    A real class (not a lambda) so ``isinstance(x, genai_types.Tool)`` works
    inside ``_build_gemini_tools``.
    """

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _make_citation_response(text: str, uris: list[tuple[str, str]]):
    """Build a Gemini-like response carrying grounding metadata.

    ``uris`` is a list of ``(uri, title)`` tuples. Returns a ``SimpleNamespace``
    that mimics the attribute graph ``_extract_grounding_citations`` walks.
    """
    grounding_chunks = [SimpleNamespace(web=SimpleNamespace(uri=uri, title=title)) for uri, title in uris]
    candidate = SimpleNamespace(
        content=SimpleNamespace(parts=[SimpleNamespace(thought=False, text=text)]),
        grounding_metadata=SimpleNamespace(grounding_chunks=grounding_chunks),
    )
    return SimpleNamespace(text=text, candidates=[candidate])


@pytest.fixture
def mock_openai_sdk():
    """Patch OpenAI SDK as available and stub ``AsyncOpenAI`` with a mock client.

    ``AsyncOpenAI`` is replaced with a callable that returns a client whose
    ``chat.completions.create`` is an ``AsyncMock`` returning a response with
    ``choices[0].message.content == "Test response"`` and ``model == "test-model"``.

    Google GenAI is also patched for multi-provider tests that use Gemini.
    """
    mock_create = AsyncMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Test response"
    mock_response.model = "test-model"
    mock_create.return_value = mock_response

    mock_client = MagicMock()
    mock_client.chat = MagicMock()
    mock_client.chat.completions = MagicMock()
    mock_client.chat.completions.create = mock_create

    mock_openai_ctor = MagicMock(return_value=mock_client)

    mock_gemini_generate = AsyncMock()
    mock_gemini_response = MagicMock()
    mock_gemini_response.text = "Test response"
    mock_gemini_generate.return_value = mock_gemini_response

    mock_gemini_stream = AsyncMock()

    mock_gemini_client = MagicMock()
    mock_gemini_client.aio = MagicMock()
    mock_gemini_client.aio.models = MagicMock()
    mock_gemini_client.aio.models.generate_content = mock_gemini_generate
    mock_gemini_client.aio.models.generate_content_stream = mock_gemini_stream

    mock_genai_client_ctor = MagicMock(return_value=mock_gemini_client)

    # genai may be None when google-genai is not installed, so we patch the
    # whole module-level name with a stub that has a .Client attribute.
    mock_genai_module = MagicMock()
    mock_genai_module.Client = mock_genai_client_ctor

    # genai_types.GenerateContentConfig must store kwargs as attributes so
    # tests can assert on e.g. config.temperature after the call.
    mock_genai_types = MagicMock()
    mock_genai_types.GenerateContentConfig = lambda **kw: SimpleNamespace(**kw)
    mock_genai_types.ThinkingConfig = lambda **kw: SimpleNamespace(**kw)
    # Tool/GoogleSearch are real classes so isinstance() works in the tool builder.
    mock_genai_types.Tool = _FakeTool
    mock_genai_types.GoogleSearch = _FakeGoogleSearch

    with (
        patch("mdb_engine.llm.service.OPENAI_SDK_AVAILABLE", True),
        patch("mdb_engine.llm.service.GENAI_AVAILABLE", True),
        patch("mdb_engine.llm.service.AsyncOpenAI", mock_openai_ctor),
        patch("mdb_engine.llm.service.genai", mock_genai_module),
        patch("mdb_engine.llm.service.genai_types", mock_genai_types),
    ):
        yield SimpleNamespace(
            openai_create=mock_create,
            gemini_generate_content=mock_gemini_generate,
            gemini_generate_content_stream=mock_gemini_stream,
        )


@pytest.fixture
def llm_config_single():
    """Config with single default_model."""
    return {"default_model": "openai/gpt-4o"}


@pytest.fixture
def llm_config_multiple():
    """Config with providers dict."""
    return {
        "providers": {
            "chat": "openai/gpt-4o",
            "analysis": "gemini/gemini-3-flash-preview",
            "code": "anthropic/claude-sonnet-4-20250514",
        },
    }


class TestLLMServiceInitialization:
    """Test LLMService initialization."""

    def test_default_provider_initialization_raises_without_providers(self, mock_openai_sdk, llm_config_single):
        """Test that config with only default_model (no providers dict) raises LLMServiceError."""
        with pytest.raises(LLMServiceError, match="providers"):
            LLMService(config=llm_config_single)

    def test_named_providers_initialization(self, mock_openai_sdk, llm_config_multiple):
        """Test named providers initialization from config."""
        service = LLMService(config=llm_config_multiple)

        assert len(service.providers) == 3
        assert "chat" in service.providers
        assert "analysis" in service.providers
        assert "code" in service.providers
        assert service.providers["chat"].default_model == "openai/gpt-4o"
        assert service.providers["analysis"].default_model == "gemini/gemini-3-flash-preview"
        assert service.providers["code"].default_model == "anthropic/claude-sonnet-4-20250514"

    def test_empty_providers_dict(self, mock_openai_sdk):
        """Test empty providers dict raises LLMServiceError."""
        config = {"providers": {}}
        with pytest.raises(LLMServiceError, match="providers"):
            LLMService(config=config)

    def test_invalid_provider_config_non_string(self, mock_openai_sdk):
        """Test invalid provider config with non-string values raises LLMServiceError."""
        config = {
            "providers": {"chat": 123, "analysis": "gemini/gemini-3-flash-preview"},
        }

        with pytest.raises(LLMServiceError, match="must map to a model string"):
            LLMService(config=config)

    def test_openai_provider_raises_when_sdk_unavailable(self):
        """Named OpenAI-style provider fails fast if the OpenAI SDK is not installed."""
        config = {"providers": {"chat": "openai/gpt-4o"}}
        with patch("mdb_engine.llm.service.OPENAI_SDK_AVAILABLE", False):
            with pytest.raises(LLMServiceError, match="OpenAI SDK not available"):
                LLMService(config=config)


class TestLLMServiceProviderSelection:
    """Test provider selection logic."""

    def test_get_provider_none_raises(self, mock_openai_sdk, llm_config_multiple):
        """Test _get_provider(None) raises LLMServiceError (no default provider)."""
        service = LLMService(config=llm_config_multiple)

        with pytest.raises(LLMServiceError, match="not found"):
            service.get_provider(None)

    def test_get_provider_valid_name(self, mock_openai_sdk, llm_config_multiple):
        """Test _get_provider() with valid provider name."""
        service = LLMService(config=llm_config_multiple)

        provider = service.get_provider("analysis")

        assert provider is service.providers["analysis"]
        assert provider.default_model == "gemini/gemini-3-flash-preview"

    def test_get_provider_invalid_name(self, mock_openai_sdk, llm_config_multiple):
        """Test _get_provider() with invalid provider name raises error."""
        service = LLMService(config=llm_config_multiple)

        with pytest.raises(LLMServiceError) as exc_info:
            service.get_provider("nonexistent")

        assert "Provider 'nonexistent' not found" in str(exc_info.value)
        assert "Available providers" in str(exc_info.value)

    def test_get_provider_invalid_name_with_valid_config(self, mock_openai_sdk, llm_config_multiple):
        """Test _get_provider() with invalid name when providers are configured."""
        service = LLMService(config=llm_config_multiple)

        with pytest.raises(LLMServiceError) as exc_info:
            service.get_provider("nonexistent_provider")

        assert "Provider 'nonexistent_provider' not found" in str(exc_info.value)
        assert "Available providers" in str(exc_info.value)

    def test_provider_isolation(self, mock_openai_sdk, llm_config_multiple):
        """Test provider isolation (different providers have different configs)."""
        service = LLMService(config=llm_config_multiple)

        chat_provider = service.providers["chat"]
        analysis_provider = service.providers["analysis"]

        assert chat_provider.default_model != analysis_provider.default_model
        assert chat_provider is not analysis_provider


class TestLLMServiceChatCompletion:
    """Test chat_completion method."""

    @pytest.mark.asyncio
    async def test_default_provider_name(self, mock_openai_sdk, llm_config_multiple):
        """Test chat_completion uses default provider when provider_name is omitted."""
        service = LLMService(config=llm_config_multiple)
        messages = [{"role": "user", "content": "Hello"}]

        # Should use default_provider_name (which is "chat" when "chat" key exists)
        response = await service.chat_completion(messages=messages)

        assert response == "Test response"
        assert service.default_provider_name == "chat"

    @pytest.mark.asyncio
    async def test_default_provider_name_first_key_fallback(self, mock_openai_sdk):
        """Test default_provider_name falls back to first key when 'chat' not present."""
        config = {"providers": {"analysis": "gemini/gemini-3-flash-preview", "code": "openai/gpt-4o"}}
        service = LLMService(config=config)

        assert service.default_provider_name == "analysis"

    @pytest.mark.asyncio
    async def test_provider_usage_requires_provider_name(self, mock_openai_sdk, llm_config_multiple):
        """Test chat completion with explicit provider_name."""
        service = LLMService(config=llm_config_multiple)
        messages = [{"role": "user", "content": "Hello"}]

        response = await service.chat_completion(messages=messages, provider_name="chat")

        assert response == "Test response"
        mock_openai_sdk.openai_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_named_provider_usage(self, mock_openai_sdk, llm_config_multiple):
        """Test named provider usage via provider_name."""
        service = LLMService(config=llm_config_multiple)
        messages = [{"role": "user", "content": "Analyze this"}]

        response = await service.chat_completion(messages=messages, provider_name="analysis")

        assert response == "Test response"
        mock_openai_sdk.gemini_generate_content.assert_called_once()

    @pytest.mark.asyncio
    async def test_model_override_with_named_provider(self, mock_openai_sdk, llm_config_multiple):
        """Test model override still works with named providers."""
        service = LLMService(config=llm_config_multiple)
        messages = [{"role": "user", "content": "Hello"}]

        response = await service.chat_completion(
            messages=messages,
            provider_name="chat",
            model="openai/gpt-4o-mini",
        )

        assert response == "Test response"
        call_kwargs = mock_openai_sdk.openai_create.call_args.kwargs
        assert call_kwargs["model"] == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_temperature_defaults_from_provider(self, mock_openai_sdk, llm_config_multiple):
        """Test temperature defaults from selected provider (Gemini enforces 1.0)."""
        service = LLMService(config=llm_config_multiple)
        service.providers["analysis"].default_temperature = 0.5
        messages = [{"role": "user", "content": "Hello"}]

        await service.chat_completion(messages=messages, provider_name="analysis")

        call_kwargs = mock_openai_sdk.gemini_generate_content.call_args.kwargs
        assert call_kwargs["config"].temperature == 1.0

    @pytest.mark.asyncio
    async def test_temperature_explicit_override(self, mock_openai_sdk, llm_config_multiple):
        """Test explicit temperature override (Gemini still enforces 1.0)."""
        service = LLMService(config=llm_config_multiple)
        service.providers["analysis"].default_temperature = 0.5
        messages = [{"role": "user", "content": "Hello"}]

        await service.chat_completion(messages=messages, provider_name="analysis", temperature=0.9)

        call_kwargs = mock_openai_sdk.gemini_generate_content.call_args.kwargs
        assert call_kwargs["config"].temperature == 1.0

    @pytest.mark.asyncio
    async def test_provider_not_found_error(self, mock_openai_sdk, llm_config_multiple):
        """Test error handling when provider not found."""
        service = LLMService(config=llm_config_multiple)
        messages = [{"role": "user", "content": "Hello"}]

        with pytest.raises(LLMServiceError) as exc_info:
            await service.chat_completion(messages=messages, provider_name="invalid")

        assert "Provider 'invalid' not found" in str(exc_info.value)


class TestLLMServiceChatCompletionStream:
    """Test chat_completion_stream method."""

    @pytest.mark.asyncio
    async def test_streaming_with_provider_name(self, mock_openai_sdk, llm_config_multiple):
        """Test streaming requires explicit provider_name."""
        service = LLMService(config=llm_config_multiple)
        messages = [{"role": "user", "content": "Hello"}]

        async def mock_stream():
            mock_chunk = MagicMock()
            mock_delta = MagicMock()
            mock_delta.content = "chunk"
            mock_delta.reasoning_content = None
            mock_choice = MagicMock()
            mock_choice.delta = mock_delta
            mock_chunk.choices = [mock_choice]
            yield mock_chunk

        mock_openai_sdk.openai_create.return_value = mock_stream()

        chunks = []
        async for chunk in service.chat_completion_stream(messages=messages, provider_name="chat"):
            chunks.append(chunk)

        assert len(chunks) > 0
        assert chunks[0] == "chunk"
        mock_openai_sdk.openai_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_streaming_named_provider(self, mock_openai_sdk, llm_config_multiple):
        """Test streaming with named provider (Gemini)."""
        service = LLMService(config=llm_config_multiple)
        messages = [{"role": "user", "content": "Analyze"}]

        async def mock_gemini_stream():
            mock_part = MagicMock()
            mock_part.thought = False
            mock_part.text = "chunk"
            mock_content = MagicMock()
            mock_content.parts = [mock_part]
            mock_candidate = MagicMock()
            mock_candidate.content = mock_content
            mock_chunk = MagicMock()
            mock_chunk.candidates = [mock_candidate]
            yield mock_chunk

        mock_openai_sdk.gemini_generate_content_stream.return_value = mock_gemini_stream()

        chunks = []
        async for chunk in service.chat_completion_stream(messages=messages, provider_name="analysis"):
            chunks.append(chunk)

        assert len(chunks) > 0
        assert chunks[0] == "chunk"
        mock_openai_sdk.gemini_generate_content_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_streaming_provider_not_found(self, mock_openai_sdk, llm_config_multiple):
        """Test streaming error when provider not found."""
        service = LLMService(config=llm_config_multiple)
        messages = [{"role": "user", "content": "Hello"}]

        with pytest.raises(LLMServiceError):
            async for _ in service.chat_completion_stream(messages=messages, provider_name="invalid"):
                pass


class TestLLMServiceIntegration:
    """Test integration scenarios."""

    @pytest.mark.asyncio
    async def test_multiple_providers_same_instance(self, mock_openai_sdk, llm_config_multiple):
        """Test multiple providers in same service instance."""
        service = LLMService(config=llm_config_multiple)
        messages = [{"role": "user", "content": "Test"}]

        await service.chat_completion(messages=messages, provider_name="chat")
        await service.chat_completion(messages=messages, provider_name="analysis")
        await service.chat_completion(messages=messages, provider_name="code")

        assert mock_openai_sdk.openai_create.call_count == 2
        assert mock_openai_sdk.gemini_generate_content.call_count == 1

    @pytest.mark.asyncio
    async def test_provider_config_isolation(self, mock_openai_sdk, llm_config_multiple):
        """Test provider config isolation."""
        service = LLMService(config=llm_config_multiple)

        messages = [{"role": "user", "content": "Test"}]

        await service.chat_completion(messages=messages, provider_name="chat")
        call1_model = mock_openai_sdk.openai_create.call_args.kwargs["model"]

        await service.chat_completion(messages=messages, provider_name="analysis")
        call2_model = mock_openai_sdk.gemini_generate_content.call_args.kwargs["model"]

        assert call1_model == "gpt-4o"
        assert call2_model == "gemini-3-flash-preview"


class TestGetLLMService:
    """Test get_llm_service factory function."""

    def test_get_llm_service_with_providers(self, mock_openai_sdk, llm_config_multiple):
        """Test get_llm_service with providers config."""
        service = get_llm_service(config=llm_config_multiple)

        assert isinstance(service, LLMService)
        assert len(service.providers) == 3

    def test_get_llm_service_single_provider_raises(self, mock_openai_sdk, llm_config_single):
        """Test get_llm_service with only default_model (no providers) raises LLMServiceError."""
        with pytest.raises(LLMServiceError, match="providers"):
            get_llm_service(config=llm_config_single)


@pytest.fixture
def stub_genai_types():
    """Patch ``genai_types`` so ``ThinkingConfig`` just records its kwargs.

    Lets us exercise thinking-config logic without the optional ``google-genai``
    dependency (which is not installed in the unit-test environment).
    """
    stub = SimpleNamespace(
        ThinkingConfig=lambda **kw: SimpleNamespace(**kw),
        GenerateContentConfig=lambda **kw: SimpleNamespace(**kw),
        Tool=_FakeTool,
        GoogleSearch=_FakeGoogleSearch,
    )
    with patch.object(llm_service_module, "genai_types", stub):
        yield stub


class TestClampThinkingBudget:
    """Test _clamp_thinking_budget model-aware clamping."""

    def test_dynamic_budget_passes_through(self):
        assert _clamp_thinking_budget("gemini-2.5-pro", -1) == -1
        assert _clamp_thinking_budget("gemini-2.5-flash", -1) == -1

    def test_pro_cannot_disable_thinking(self):
        # Pro has a minimum of 128 and cannot be set to 0.
        assert _clamp_thinking_budget("gemini-2.5-pro", 0) == 128
        assert _clamp_thinking_budget("gemini-2.5-pro", 50) == 128

    def test_pro_caps_at_max(self):
        assert _clamp_thinking_budget("gemini-2.5-pro", 99999) == 32768

    def test_flash_allows_zero_and_caps(self):
        assert _clamp_thinking_budget("gemini-2.5-flash", 0) == 0
        assert _clamp_thinking_budget("gemini-2.5-flash", 99999) == 24576
        assert _clamp_thinking_budget("gemini-2.5-flash", 1024) == 1024


class TestIsThinkingConfigError:
    """Test the heuristic that detects thinking-config errors."""

    @pytest.mark.parametrize(
        "message",
        [
            "400 INVALID_ARGUMENT: thinkingBudget is out of range",
            "thinking_level is not supported for this model",
            "Unknown field: thinking_config",
            "thoughts are not available",
        ],
    )
    def test_thinking_errors_detected(self, message):
        assert _is_thinking_config_error(ValueError(message)) is True

    @pytest.mark.parametrize(
        "message",
        [
            "401 Unauthorized: invalid API key",
            "429 RESOURCE_EXHAUSTED: quota exceeded",
            "connection reset by peer",
        ],
    )
    def test_unrelated_errors_ignored(self, message):
        assert _is_thinking_config_error(RuntimeError(message)) is False


class TestBuildThinkingConfig:
    """Test _build_thinking_config provider-correct mapping."""

    def test_no_effort_returns_none(self, stub_genai_types):
        assert _build_thinking_config("gemini-2.5-flash", None) is None

    def test_sdk_unavailable_returns_none(self):
        with patch.object(llm_service_module, "genai_types", None):
            assert _build_thinking_config("gemini-2.5-flash", "high") is None

    def test_gemini_25_uses_budget_not_level(self, stub_genai_types):
        cfg = _build_thinking_config("gemini-2.5-flash", "low")
        assert cfg.thinking_budget == 1024
        assert cfg.include_thoughts is True
        assert not hasattr(cfg, "thinking_level")

    def test_gemini_25_none_disables_thinking_and_thoughts(self, stub_genai_types):
        cfg = _build_thinking_config("gemini-2.5-flash", "none")
        assert cfg.thinking_budget == 0
        # Must not request thought summaries when thinking is disabled.
        assert cfg.include_thoughts is False

    def test_gemini_25_pro_none_clamped_to_minimum(self, stub_genai_types):
        cfg = _build_thinking_config("gemini-2.5-pro", "none")
        assert cfg.thinking_budget == 128
        assert cfg.include_thoughts is True

    def test_gemini_25_medium_is_dynamic(self, stub_genai_types):
        cfg = _build_thinking_config("gemini-2.5-flash", "medium")
        assert cfg.thinking_budget == -1
        assert cfg.include_thoughts is True

    def test_gemini_3_uses_level_not_budget(self, stub_genai_types):
        cfg = _build_thinking_config("gemini-3-flash-preview", "high")
        assert cfg.thinking_level == "HIGH"
        assert cfg.include_thoughts is True
        assert not hasattr(cfg, "thinking_budget")

    def test_gemini_3_pro_none_uses_low(self, stub_genai_types):
        # Pro does not support "minimal", so "none" maps to the lowest level.
        cfg = _build_thinking_config("gemini-3-pro", "none")
        assert cfg.thinking_level == "LOW"

    def test_gemini_3_flash_none_uses_minimal(self, stub_genai_types):
        cfg = _build_thinking_config("gemini-3-flash", "none")
        assert cfg.thinking_level == "MINIMAL"

    def test_construction_error_returns_none(self):
        def _raising(**_kwargs):
            raise TypeError("ThinkingConfig got an unexpected keyword argument")

        stub = SimpleNamespace(ThinkingConfig=_raising)
        with patch.object(llm_service_module, "genai_types", stub):
            assert _build_thinking_config("gemini-2.5-flash", "high") is None


class TestGeminiThinkingGracefulFallback:
    """Test that a rejected thinking config degrades gracefully (retry without it).

    These exercise ``_call_gemini`` directly so the resilience layer (which
    wraps the public ``chat_completion``) doesn't add retries/backoff.
    """

    @pytest.mark.asyncio
    async def test_retries_without_thinking_config_on_thinking_error(self, mock_openai_sdk, llm_config_multiple):
        service = LLMService(config=llm_config_multiple)
        provider = service.get_provider("analysis")
        _ptype, client = provider._client_for_model("gemini/gemini-3-flash-preview")  # noqa: SLF001

        recovered = MagicMock()
        recovered.text = "Recovered answer"
        # First call rejects the thinking config; second (without it) succeeds.
        mock_openai_sdk.gemini_generate_content.side_effect = [
            ValueError("400 INVALID_ARGUMENT: thinkingBudget not supported"),
            recovered,
        ]

        response = await provider._call_gemini(  # noqa: SLF001
            client,
            "gemini/gemini-3-flash-preview",
            [{"role": "user", "content": "Think hard"}],
            0.7,
            None,
            reasoning_effort="high",
        )

        assert response is recovered
        assert mock_openai_sdk.gemini_generate_content.call_count == 2
        # First attempt carried the thinking config; the retry dropped it.
        first_config = mock_openai_sdk.gemini_generate_content.call_args_list[0].kwargs["config"]
        retry_config = mock_openai_sdk.gemini_generate_content.call_args_list[1].kwargs["config"]
        assert hasattr(first_config, "thinking_config")
        assert not hasattr(retry_config, "thinking_config")

    @pytest.mark.asyncio
    async def test_non_thinking_error_propagates_without_retry(self, mock_openai_sdk, llm_config_multiple):
        service = LLMService(config=llm_config_multiple)
        provider = service.get_provider("analysis")
        _ptype, client = provider._client_for_model("gemini/gemini-3-flash-preview")  # noqa: SLF001

        mock_openai_sdk.gemini_generate_content.side_effect = ValueError("401 Unauthorized: bad key")

        with pytest.raises(ValueError, match="Unauthorized"):
            await provider._call_gemini(  # noqa: SLF001
                client,
                "gemini/gemini-3-flash-preview",
                [{"role": "user", "content": "Think hard"}],
                0.7,
                None,
                reasoning_effort="high",
            )

        # Unrelated errors must not trigger the thinking-config retry.
        assert mock_openai_sdk.gemini_generate_content.call_count == 1

    @pytest.mark.asyncio
    async def test_reasoning_effort_threads_into_non_streaming(self, mock_openai_sdk, llm_config_multiple):
        """The non-streaming path must forward reasoning_effort to Gemini's thinking config."""
        service = LLMService(config=llm_config_multiple)

        await service.chat_completion(
            messages=[{"role": "user", "content": "Analyze"}],
            provider_name="analysis",
            reasoning_effort="high",
        )

        config = mock_openai_sdk.gemini_generate_content.call_args.kwargs["config"]
        assert hasattr(config, "thinking_config")
        assert config.thinking_config.thinking_level == "HIGH"


class TestBuildGeminiTools:
    """Test _build_gemini_tools translation of framework tool requests."""

    def test_enable_web_search_builds_one_grounding_tool(self, stub_genai_types):
        tools = _build_gemini_tools(True, None)
        assert tools is not None
        assert len(tools) == 1
        assert isinstance(tools[0], _FakeTool)
        assert isinstance(tools[0].google_search, _FakeGoogleSearch)

    def test_legacy_camelcase_dict_translates(self, stub_genai_types):
        tools = _build_gemini_tools(False, [{"googleSearch": {}}])
        assert tools is not None
        assert len(tools) == 1
        assert isinstance(tools[0].google_search, _FakeGoogleSearch)

    def test_legacy_snakecase_dict_translates(self, stub_genai_types):
        tools = _build_gemini_tools(False, [{"google_search": {}}])
        assert tools is not None
        assert len(tools) == 1

    def test_prebuilt_tool_passthrough(self, stub_genai_types):
        prebuilt = _FakeTool(google_search=_FakeGoogleSearch())
        tools = _build_gemini_tools(False, [prebuilt])
        assert tools == [prebuilt]

    def test_unknown_dict_is_skipped(self, stub_genai_types):
        assert _build_gemini_tools(False, [{"unknownTool": {}}]) is None

    def test_nothing_requested_returns_none(self, stub_genai_types):
        assert _build_gemini_tools(False, None) is None

    def test_sdk_unavailable_returns_none(self):
        with patch.object(llm_service_module, "genai_types", None):
            assert _build_gemini_tools(True, None) is None


class TestIsToolsConfigError:
    """Test the heuristic that detects tools/grounding-config errors."""

    @pytest.mark.parametrize(
        "message",
        [
            "400 INVALID_ARGUMENT: Tool use is not supported for this model",
            "google_search is not enabled for this project",
            "grounding is unavailable in this region",
            "Unknown field: function_declarations",
        ],
    )
    def test_tools_errors_detected(self, message):
        assert _is_tools_config_error(ValueError(message)) is True

    @pytest.mark.parametrize(
        "message",
        [
            "401 Unauthorized: invalid API key",
            "429 RESOURCE_EXHAUSTED: quota exceeded",
            "connection reset by peer",
        ],
    )
    def test_unrelated_errors_ignored(self, message):
        assert _is_tools_config_error(RuntimeError(message)) is False


class TestExtractGroundingCitations:
    """Test _extract_grounding_citations metadata parsing."""

    def test_extracts_and_dedupes_by_uri(self):
        response = _make_citation_response(
            "answer",
            [
                ("https://a.example/1", "A"),
                ("https://b.example/2", "B"),
                ("https://a.example/1", "A dup"),  # duplicate uri -> ignored
            ],
        )
        citations = _extract_grounding_citations(response)
        assert citations == [
            {
                "title": "A",
                "uri": "https://a.example/1",
                "domain": "a.example",
                "redirect_uri": "https://a.example/1",
            },
            {
                "title": "B",
                "uri": "https://b.example/2",
                "domain": "b.example",
                "redirect_uri": "https://b.example/2",
            },
        ]

    def test_uri_used_as_title_when_missing(self):
        candidate = SimpleNamespace(
            grounding_metadata=SimpleNamespace(
                grounding_chunks=[SimpleNamespace(web=SimpleNamespace(uri="https://x.example", title=""))]
            )
        )
        response = SimpleNamespace(candidates=[candidate])
        # With no title, the clean domain is used as the display title.
        assert _extract_grounding_citations(response) == [
            {
                "title": "x.example",
                "uri": "https://x.example",
                "domain": "x.example",
                "redirect_uri": "https://x.example",
            }
        ]

    def test_no_candidates_returns_empty(self):
        assert _extract_grounding_citations(SimpleNamespace(candidates=[])) == []

    def test_missing_grounding_metadata_returns_empty(self):
        response = SimpleNamespace(candidates=[SimpleNamespace(grounding_metadata=None)])
        assert _extract_grounding_citations(response) == []


class TestGeminiWebSearchGrounding:
    """Test enable_web_search / grounding behavior end-to-end (mocked SDK)."""

    @pytest.mark.asyncio
    async def test_enable_web_search_attaches_tools_non_streaming(self, mock_openai_sdk, llm_config_multiple):
        service = LLMService(config=llm_config_multiple)

        await service.chat_completion(
            messages=[{"role": "user", "content": "What happened this week?"}],
            provider_name="analysis",
            enable_web_search=True,
        )

        config = mock_openai_sdk.gemini_generate_content.call_args.kwargs["config"]
        assert hasattr(config, "tools")
        assert len(config.tools) == 1
        assert isinstance(config.tools[0].google_search, _FakeGoogleSearch)

    @pytest.mark.asyncio
    async def test_response_format_conflict_drops_grounding(self, mock_openai_sdk, llm_config_multiple, caplog):
        service = LLMService(config=llm_config_multiple)
        provider = service.get_provider("analysis")
        _ptype, client = provider._client_for_model("gemini/gemini-3-flash-preview")  # noqa: SLF001

        with caplog.at_level("WARNING"):
            await provider._call_gemini(  # noqa: SLF001
                client,
                "gemini/gemini-3-flash-preview",
                [{"role": "user", "content": "Extract"}],
                0.7,
                None,
                response_format={"response_schema": {"type": "object"}},
                enable_web_search=True,
            )

        config = mock_openai_sdk.gemini_generate_content.call_args.kwargs["config"]
        assert not hasattr(config, "tools")
        assert config.response_mime_type == "application/json"
        assert any("response_format" in rec.message for rec in caplog.records)

    @pytest.mark.asyncio
    async def test_return_metadata_returns_grounded_completion(self, mock_openai_sdk, llm_config_multiple):
        service = LLMService(config=llm_config_multiple)
        mock_openai_sdk.gemini_generate_content.return_value = _make_citation_response(
            "Grounded answer", [("https://news.example/a", "News A")]
        )

        result = await service.chat_completion(
            messages=[{"role": "user", "content": "What happened this week?"}],
            provider_name="analysis",
            enable_web_search=True,
            return_metadata=True,
        )

        assert isinstance(result, GroundedCompletion)
        assert result.text == "Grounded answer"
        assert result.grounded is True
        assert result.citations == [
            {
                "title": "News A",
                "uri": "https://news.example/a",
                "domain": "news.example",
                "redirect_uri": "https://news.example/a",
            }
        ]
        assert result.model_used is not None

    @pytest.mark.asyncio
    async def test_return_metadata_without_grounding(self, mock_openai_sdk, llm_config_multiple):
        service = LLMService(config=llm_config_multiple)
        mock_openai_sdk.gemini_generate_content.return_value = SimpleNamespace(text="Plain answer", candidates=[])

        result = await service.chat_completion(
            messages=[{"role": "user", "content": "Hi"}],
            provider_name="analysis",
            return_metadata=True,
        )

        assert isinstance(result, GroundedCompletion)
        assert result.text == "Plain answer"
        assert result.grounded is False
        assert result.citations == []

    @pytest.mark.asyncio
    async def test_default_return_type_unchanged(self, mock_openai_sdk, llm_config_multiple):
        """Without return_metadata, the return type stays a plain str."""
        service = LLMService(config=llm_config_multiple)

        result = await service.chat_completion(
            messages=[{"role": "user", "content": "Hi"}],
            provider_name="analysis",
            enable_web_search=True,
        )

        assert isinstance(result, str)
        assert result == "Test response"

    @pytest.mark.asyncio
    async def test_enable_web_search_warns_for_openai(self, mock_openai_sdk, llm_config_multiple, caplog):
        service = LLMService(config=llm_config_multiple)

        with caplog.at_level("WARNING"):
            await service.chat_completion(
                messages=[{"role": "user", "content": "Hi"}],
                provider_name="chat",  # openai
                enable_web_search=True,
            )

        # Capability-aware negotiation logs (best_effort) instead of silently no-op'ing.
        assert any("does not surface web-search grounding" in rec.message for rec in caplog.records)
        # No grounding tool should leak into the OpenAI call.
        assert "tools" not in mock_openai_sdk.openai_create.call_args.kwargs

    @pytest.mark.asyncio
    async def test_streaming_emits_grounding_event(self, mock_openai_sdk, llm_config_multiple):
        service = LLMService(config=llm_config_multiple)

        async def mock_gemini_stream():
            part = SimpleNamespace(thought=False, text="chunk")
            candidate = SimpleNamespace(
                content=SimpleNamespace(parts=[part]),
                grounding_metadata=SimpleNamespace(
                    grounding_chunks=[SimpleNamespace(web=SimpleNamespace(uri="https://src.example", title="Src"))]
                ),
            )
            yield SimpleNamespace(candidates=[candidate])

        mock_openai_sdk.gemini_generate_content_stream.return_value = mock_gemini_stream()

        chunks = []
        async for chunk in service.chat_completion_stream(
            messages=[{"role": "user", "content": "What happened?"}],
            provider_name="analysis",
            enable_web_search=True,
        ):
            chunks.append(chunk)

        assert "chunk" in chunks
        grounding_events = [c for c in chunks if c.startswith("__GROUNDING__:")]
        assert len(grounding_events) == 1
        payload = json.loads(grounding_events[0][len("__GROUNDING__:") :])
        assert payload["citations"] == [
            {
                "title": "Src",
                "uri": "https://src.example",
                "domain": "src.example",
                "redirect_uri": "https://src.example",
            }
        ]

    @pytest.mark.asyncio
    async def test_streaming_without_grounding_emits_no_event(self, mock_openai_sdk, llm_config_multiple):
        """A non-grounded Gemini stream must not emit a __GROUNDING__: event."""
        service = LLMService(config=llm_config_multiple)

        async def mock_gemini_stream():
            part = SimpleNamespace(thought=False, text="chunk")
            candidate = SimpleNamespace(content=SimpleNamespace(parts=[part]))
            yield SimpleNamespace(candidates=[candidate])

        mock_openai_sdk.gemini_generate_content_stream.return_value = mock_gemini_stream()

        chunks = []
        async for chunk in service.chat_completion_stream(
            messages=[{"role": "user", "content": "Hi"}],
            provider_name="analysis",
        ):
            chunks.append(chunk)

        assert chunks == ["chunk"]
        assert not any(c.startswith("__GROUNDING__:") for c in chunks)


class TestGeminiToolsGracefulFallback:
    """Test that a rejected tools/grounding config degrades gracefully."""

    @pytest.mark.asyncio
    async def test_retries_without_tools_on_tools_error(self, mock_openai_sdk, llm_config_multiple):
        service = LLMService(config=llm_config_multiple)
        provider = service.get_provider("analysis")
        _ptype, client = provider._client_for_model("gemini/gemini-3-flash-preview")  # noqa: SLF001

        recovered = SimpleNamespace(text="Ungrounded answer", candidates=[])
        mock_openai_sdk.gemini_generate_content.side_effect = [
            ValueError("400 INVALID_ARGUMENT: Tool use is not supported for this model"),
            recovered,
        ]

        response = await provider._call_gemini(  # noqa: SLF001
            client,
            "gemini/gemini-3-flash-preview",
            [{"role": "user", "content": "What happened?"}],
            0.7,
            None,
            enable_web_search=True,
        )

        assert response is recovered
        assert mock_openai_sdk.gemini_generate_content.call_count == 2
        first_config = mock_openai_sdk.gemini_generate_content.call_args_list[0].kwargs["config"]
        retry_config = mock_openai_sdk.gemini_generate_content.call_args_list[1].kwargs["config"]
        assert hasattr(first_config, "tools")
        assert not hasattr(retry_config, "tools")

    @pytest.mark.asyncio
    async def test_non_tools_error_propagates_without_retry(self, mock_openai_sdk, llm_config_multiple):
        service = LLMService(config=llm_config_multiple)
        provider = service.get_provider("analysis")
        _ptype, client = provider._client_for_model("gemini/gemini-3-flash-preview")  # noqa: SLF001

        mock_openai_sdk.gemini_generate_content.side_effect = ValueError("401 Unauthorized: bad key")

        with pytest.raises(ValueError, match="Unauthorized"):
            await provider._call_gemini(  # noqa: SLF001
                client,
                "gemini/gemini-3-flash-preview",
                [{"role": "user", "content": "What happened?"}],
                0.7,
                None,
                enable_web_search=True,
            )

        assert mock_openai_sdk.gemini_generate_content.call_count == 1


class TestDomainFromCitation:
    """Test clean-domain extraction from grounding citations."""

    def test_title_that_is_a_domain_wins(self):
        assert (
            _domain_from_citation(
                "livemint.com",
                "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc",
            )
            == "livemint.com"
        )

    def test_www_prefix_stripped(self):
        assert _domain_from_citation("www.bbc.co.uk", "https://anything/x") == "bbc.co.uk"

    def test_falls_back_to_uri_host_when_title_has_spaces(self):
        assert _domain_from_citation("Some Article Title", "https://example.com/path") == "example.com"

    def test_empty_title_uses_uri_host(self):
        assert _domain_from_citation("", "https://news.example.org/a") == "news.example.org"


class TestExtractFinishReason:
    """Test finish_reason extraction (enum, string, missing)."""

    def test_enum_finish_reason(self):
        resp = SimpleNamespace(candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name="MAX_TOKENS"))])
        assert _extract_finish_reason(resp) == "MAX_TOKENS"

    def test_string_finish_reason(self):
        resp = SimpleNamespace(candidates=[SimpleNamespace(finish_reason="STOP")])
        assert _extract_finish_reason(resp) == "STOP"

    def test_no_candidates_returns_none(self):
        assert _extract_finish_reason(SimpleNamespace(candidates=[])) is None

    def test_missing_finish_reason_returns_none(self):
        assert _extract_finish_reason(SimpleNamespace(candidates=[SimpleNamespace(finish_reason=None)])) is None


class TestRobustGeminiTextExtraction:
    """Empty/blocked/truncated candidates must not raise TypeError."""

    def _provider(self, mock_openai_sdk):
        service = LLMService(config={"providers": {"chat": "gemini/gemini-2.5-flash"}})
        return service.get_provider("chat")

    def test_none_parts_returns_empty_string(self, mock_openai_sdk):
        provider = self._provider(mock_openai_sdk)
        # Thinking model that spent its whole budget: parts is explicitly None.
        resp = SimpleNamespace(text=None, candidates=[SimpleNamespace(content=SimpleNamespace(parts=None))])
        assert provider._extract_gemini_text(resp) == ""  # noqa: SLF001

    def test_none_content_returns_empty_string(self, mock_openai_sdk):
        provider = self._provider(mock_openai_sdk)
        resp = SimpleNamespace(text=None, candidates=[SimpleNamespace(content=None)])
        assert provider._extract_gemini_text(resp) == ""  # noqa: SLF001

    def test_none_candidates_returns_empty_string(self, mock_openai_sdk):
        provider = self._provider(mock_openai_sdk)
        resp = SimpleNamespace(text=None, candidates=None)
        assert provider._extract_gemini_text(resp) == ""  # noqa: SLF001


class TestGroundingPolicy:
    """Capability-aware grounding negotiation: best_effort / require / auto."""

    def _latest_service(self):
        # gemini-flash-latest does NOT surface grounding (per the curated registry).
        return LLMService(config={"providers": {"chat": "gemini/gemini-flash-latest"}})

    @pytest.mark.asyncio
    async def test_best_effort_skips_grounding_on_non_grounding_model(self, mock_openai_sdk):
        service = self._latest_service()
        await service.chat_completion(
            messages=[{"role": "user", "content": "news?"}],
            enable_web_search=True,  # grounding_policy defaults to best_effort
        )
        config = mock_openai_sdk.gemini_generate_content.call_args.kwargs["config"]
        assert not hasattr(config, "tools")  # grounding not attached

    @pytest.mark.asyncio
    async def test_require_raises_on_non_grounding_model(self, mock_openai_sdk):
        service = self._latest_service()
        with pytest.raises(GroundingUnsupportedError):
            await service.chat_completion(
                messages=[{"role": "user", "content": "news?"}],
                enable_web_search=True,
                grounding_policy="require",
            )
        # The SDK must never be called when grounding is required but unsupported.
        assert mock_openai_sdk.gemini_generate_content.call_count == 0

    @pytest.mark.asyncio
    async def test_require_passes_on_grounding_model(self, mock_openai_sdk, llm_config_multiple):
        service = LLMService(config=llm_config_multiple)
        # analysis = gemini-3-flash-preview, which is grounding-capable.
        await service.chat_completion(
            messages=[{"role": "user", "content": "news?"}],
            provider_name="analysis",
            enable_web_search=True,
            grounding_policy="require",
        )
        config = mock_openai_sdk.gemini_generate_content.call_args.kwargs["config"]
        assert hasattr(config, "tools")

    @pytest.mark.asyncio
    async def test_auto_routes_to_grounding_capable_model(self, mock_openai_sdk):
        service = self._latest_service()
        await service.chat_completion(
            messages=[{"role": "user", "content": "news?"}],
            enable_web_search=True,
            grounding_policy="auto",
        )
        call = mock_openai_sdk.gemini_generate_content.call_args
        # Routed away from the -latest alias to the curated grounding model.
        assert call.kwargs["model"] == "gemini-2.5-flash"
        assert hasattr(call.kwargs["config"], "tools")

    @pytest.mark.asyncio
    async def test_auto_routes_via_configured_grounding_model(self, mock_openai_sdk):
        service = LLMService(
            config={
                "providers": {"chat": "gemini/gemini-flash-latest"},
                "grounding_model": "gemini/gemini-2.5-pro",
            }
        )
        await service.chat_completion(
            messages=[{"role": "user", "content": "news?"}],
            enable_web_search=True,
            grounding_policy="auto",
        )
        assert mock_openai_sdk.gemini_generate_content.call_args.kwargs["model"] == "gemini-2.5-pro"

    @pytest.mark.asyncio
    async def test_model_override_enables_grounding(self, mock_openai_sdk):
        # An app corrects the registry without an engine release.
        service = LLMService(
            config={
                "providers": {"chat": "gemini/gemini-flash-latest"},
                "model_overrides": {"gemini/gemini-flash-latest": {"web_search": True}},
            }
        )
        await service.chat_completion(
            messages=[{"role": "user", "content": "news?"}],
            enable_web_search=True,
            grounding_policy="require",
        )
        config = mock_openai_sdk.gemini_generate_content.call_args.kwargs["config"]
        assert hasattr(config, "tools")  # override made it grounding-capable


class TestCapabilityAccessors:
    """LLMService.get_capabilities / list_models / supports."""

    def test_get_capabilities_default_model(self, mock_openai_sdk):
        service = LLMService(config={"providers": {"chat": "gemini/gemini-2.5-flash"}})
        caps = service.get_capabilities()
        assert caps.web_search is True
        assert caps.thinking is True
        assert caps.provider == "gemini"

    def test_get_capabilities_latest_alias_no_grounding(self, mock_openai_sdk):
        service = LLMService(config={"providers": {"chat": "gemini/gemini-2.5-flash"}})
        assert service.get_capabilities("gemini/gemini-flash-latest").web_search is False

    def test_supports_feature(self, mock_openai_sdk):
        service = LLMService(config={"providers": {"chat": "gemini/gemini-2.5-flash"}})
        assert service.supports("web_search", "gemini/gemini-2.5-flash") is True
        assert service.supports("web_search", "gemini/gemini-flash-latest") is False
        assert service.supports("thinking", "openai/gpt-4o") is False

    def test_list_models_filter_web_search(self, mock_openai_sdk):
        service = LLMService(config={"providers": {"chat": "gemini/gemini-2.5-flash"}})
        grounding = service.list_models(provider="gemini", web_search=True)
        assert grounding  # non-empty
        assert all(m.web_search and m.provider == "gemini" for m in grounding)
        assert all("latest" not in m.model for m in grounding)

    def test_list_models_honors_overrides(self, mock_openai_sdk):
        service = LLMService(
            config={
                "providers": {"chat": "gemini/gemini-2.5-flash"},
                "model_overrides": {"gemini/gemini-flash-latest": {"web_search": True}},
            }
        )
        grounded_ids = {m.model for m in service.list_models(provider="gemini", web_search=True)}
        assert "gemini/gemini-flash-latest" in grounded_ids


class TestTypedStream:
    """LLMService.stream() typed events."""

    @pytest.mark.asyncio
    async def test_stream_yields_typed_events(self, mock_openai_sdk):
        service = LLMService(config={"providers": {"chat": "gemini/gemini-2.5-flash"}})

        async def mock_gemini_stream():
            yield SimpleNamespace(
                candidates=[
                    SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(thought=True, text="thinking")]))
                ]
            )
            yield SimpleNamespace(
                candidates=[
                    SimpleNamespace(
                        content=SimpleNamespace(parts=[SimpleNamespace(thought=False, text="answer")]),
                        grounding_metadata=SimpleNamespace(
                            grounding_chunks=[SimpleNamespace(web=SimpleNamespace(uri="https://s.example", title="S"))]
                        ),
                    )
                ]
            )

        mock_openai_sdk.gemini_generate_content_stream.return_value = mock_gemini_stream()

        events = []
        async for ev in service.stream(
            messages=[{"role": "user", "content": "news?"}],
            enable_web_search=True,
        ):
            events.append(ev)

        assert any(isinstance(e, ReasoningDelta) and e.text == "thinking" for e in events)
        assert any(isinstance(e, TextDelta) and e.text == "answer" for e in events)
        grounding = [e for e in events if isinstance(e, GroundingEvent)]
        assert len(grounding) == 1
        assert grounding[0].citations[0]["domain"] == "s.example"
        assert isinstance(events[-1], DoneEvent)
        assert events[-1].grounded is True
        assert events[-1].model_used == "gemini/gemini-2.5-flash"

    @pytest.mark.asyncio
    async def test_stream_done_event_when_ungrounded(self, mock_openai_sdk):
        service = LLMService(config={"providers": {"chat": "gemini/gemini-2.5-flash"}})

        async def mock_gemini_stream():
            yield SimpleNamespace(
                candidates=[
                    SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(thought=False, text="hello")]))
                ]
            )

        mock_openai_sdk.gemini_generate_content_stream.return_value = mock_gemini_stream()

        events = [ev async for ev in service.stream(messages=[{"role": "user", "content": "hi"}])]
        assert [e for e in events if isinstance(e, TextDelta)]
        done = events[-1]
        assert isinstance(done, DoneEvent)
        assert done.grounded is False
        assert done.citations == []


class TestTransportDetection:
    """SDK transport-fallback detection (google-genai x aiohttp streaming cliff)."""

    def test_returns_bool(self):
        assert isinstance(_aiohttp_readline_rejects_max_line_length(), bool)

    def test_true_when_readline_lacks_kwarg(self, monkeypatch):
        import sys
        import types as _types

        fake = _types.ModuleType("aiohttp")

        class _SR:
            async def readline(self):  # no max_line_length kwarg -> incompatible
                return b""

        fake.StreamReader = _SR
        monkeypatch.setitem(sys.modules, "aiohttp", fake)
        assert _aiohttp_readline_rejects_max_line_length() is True

    def test_false_when_readline_accepts_kwarg(self, monkeypatch):
        import sys
        import types as _types

        fake = _types.ModuleType("aiohttp")

        class _SR:
            async def readline(self, max_line_length=None):  # compatible
                return b""

        fake.StreamReader = _SR
        monkeypatch.setitem(sys.modules, "aiohttp", fake)
        assert _aiohttp_readline_rejects_max_line_length() is False
