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

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mdb_engine.llm import service as llm_service_module
from mdb_engine.llm.service import (
    LLMService,
    LLMServiceError,
    _build_thinking_config,
    _clamp_thinking_budget,
    _is_thinking_config_error,
    get_llm_service,
)


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
