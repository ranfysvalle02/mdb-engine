"""
Unit tests for LLMService (standalone LLM Service module)

Tests the LLM service functionality including:
- Default provider initialization (backward compatibility)
- Named providers initialization
- Provider selection and isolation
- Chat completion with named providers
- Streaming with named providers
- Error handling
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mdb_engine.llm.service import LLMService, LLMServiceError, get_llm_service


@pytest.fixture
def mock_litellm():
    """Mock litellm module."""
    with (
        patch("mdb_engine.llm.service.LITELLM_AVAILABLE", True),
        patch("mdb_engine.llm.service.litellm") as mock_litellm,
        patch("mdb_engine.llm.service.acompletion", new_callable=AsyncMock) as mock_acompletion,
    ):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Test response"
        mock_response.model = "test-model"
        mock_acompletion.return_value = mock_response
        yield mock_acompletion


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


@pytest.fixture
def llm_config_with_shared():
    """Config with providers and shared litellm_config."""
    return {
        "litellm_config": {"num_retries": 2, "request_timeout": 60},
        "providers": {
            "chat": "openai/gpt-4o",
            "analysis": "gemini/gemini-3-flash-preview",
        },
    }


class TestLLMServiceInitialization:
    """Test LLMService initialization."""

    def test_default_provider_initialization_raises_without_providers(self, mock_litellm, llm_config_single):
        """Test that config with only default_model (no providers dict) raises LLMServiceError."""
        with pytest.raises(LLMServiceError, match="providers"):
            LLMService(config=llm_config_single)

    def test_named_providers_initialization(self, mock_litellm, llm_config_multiple):
        """Test named providers initialization from config."""
        service = LLMService(config=llm_config_multiple)

        assert len(service.providers) == 3
        assert "chat" in service.providers
        assert "analysis" in service.providers
        assert "code" in service.providers
        assert service.providers["chat"].default_model == "openai/gpt-4o"
        assert service.providers["analysis"].default_model == "gemini/gemini-3-flash-preview"
        assert service.providers["code"].default_model == "anthropic/claude-sonnet-4-20250514"

    def test_shared_config_propagation(self, mock_litellm, llm_config_with_shared):
        """Test shared config (litellm_config) propagation to named providers."""
        service = LLMService(config=llm_config_with_shared)

        assert service.providers["chat"].litellm_config == {
            "num_retries": 2,
            "request_timeout": 60,
        }
        assert service.providers["analysis"].litellm_config == {
            "num_retries": 2,
            "request_timeout": 60,
        }

    def test_empty_providers_dict(self, mock_litellm):
        """Test empty providers dict raises LLMServiceError."""
        config = {"providers": {}}
        with pytest.raises(LLMServiceError, match="providers"):
            LLMService(config=config)

    def test_invalid_provider_config_non_string(self, mock_litellm):
        """Test invalid provider config with non-string values raises LLMServiceError."""
        config = {
            "providers": {"chat": 123, "analysis": "gemini/gemini-3-flash-preview"},
        }

        with pytest.raises(LLMServiceError, match="must map to a LiteLLM model string"):
            LLMService(config=config)


class TestLLMServiceProviderSelection:
    """Test provider selection logic."""

    def test_get_provider_none_raises(self, mock_litellm, llm_config_multiple):
        """Test _get_provider(None) raises LLMServiceError (no default provider)."""
        service = LLMService(config=llm_config_multiple)

        with pytest.raises(LLMServiceError, match="not found"):
            service.get_provider(None)

    def test_get_provider_valid_name(self, mock_litellm, llm_config_multiple):
        """Test _get_provider() with valid provider name."""
        service = LLMService(config=llm_config_multiple)

        provider = service.get_provider("analysis")

        assert provider is service.providers["analysis"]
        assert provider.default_model == "gemini/gemini-3-flash-preview"

    def test_get_provider_invalid_name(self, mock_litellm, llm_config_multiple):
        """Test _get_provider() with invalid provider name raises error."""
        service = LLMService(config=llm_config_multiple)

        with pytest.raises(LLMServiceError) as exc_info:
            service.get_provider("nonexistent")

        assert "Provider 'nonexistent' not found" in str(exc_info.value)
        assert "Available providers" in str(exc_info.value)

    def test_get_provider_invalid_name_with_valid_config(self, mock_litellm, llm_config_multiple):
        """Test _get_provider() with invalid name when providers are configured."""
        service = LLMService(config=llm_config_multiple)

        with pytest.raises(LLMServiceError) as exc_info:
            service.get_provider("nonexistent_provider")

        assert "Provider 'nonexistent_provider' not found" in str(exc_info.value)
        assert "Available providers" in str(exc_info.value)

    def test_provider_isolation(self, mock_litellm, llm_config_multiple):
        """Test provider isolation (different providers have different configs)."""
        service = LLMService(config=llm_config_multiple)

        chat_provider = service.providers["chat"]
        analysis_provider = service.providers["analysis"]

        assert chat_provider.default_model != analysis_provider.default_model
        assert chat_provider is not analysis_provider


class TestLLMServiceChatCompletion:
    """Test chat_completion method."""

    @pytest.mark.asyncio
    async def test_default_provider_name(self, mock_litellm, llm_config_multiple):
        """Test chat_completion uses default provider when provider_name is omitted."""
        service = LLMService(config=llm_config_multiple)
        messages = [{"role": "user", "content": "Hello"}]

        # Should use default_provider_name (which is "chat" when "chat" key exists)
        response = await service.chat_completion(messages=messages)

        assert response == "Test response"
        assert service.default_provider_name == "chat"

    @pytest.mark.asyncio
    async def test_default_provider_name_first_key_fallback(self, mock_litellm):
        """Test default_provider_name falls back to first key when 'chat' not present."""
        config = {"providers": {"analysis": "gemini/gemini-3-flash-preview", "code": "openai/gpt-4o"}}
        service = LLMService(config=config)

        assert service.default_provider_name == "analysis"

    @pytest.mark.asyncio
    async def test_provider_usage_requires_provider_name(self, mock_litellm, llm_config_multiple):
        """Test chat completion with explicit provider_name."""
        service = LLMService(config=llm_config_multiple)
        messages = [{"role": "user", "content": "Hello"}]

        response = await service.chat_completion(messages=messages, provider_name="chat")

        assert response == "Test response"
        mock_litellm.assert_called_once()

    @pytest.mark.asyncio
    async def test_named_provider_usage(self, mock_litellm, llm_config_multiple):
        """Test named provider usage via provider_name."""
        service = LLMService(config=llm_config_multiple)
        messages = [{"role": "user", "content": "Analyze this"}]

        response = await service.chat_completion(messages=messages, provider_name="analysis")

        assert response == "Test response"
        mock_litellm.assert_called_once()

    @pytest.mark.asyncio
    async def test_model_override_with_named_provider(self, mock_litellm, llm_config_multiple):
        """Test model override still works with named providers."""
        service = LLMService(config=llm_config_multiple)
        messages = [{"role": "user", "content": "Hello"}]

        response = await service.chat_completion(
            messages=messages,
            provider_name="chat",
            model="openai/gpt-4o-mini",
        )

        assert response == "Test response"
        call_kwargs = mock_litellm.call_args[1]
        assert call_kwargs["model"] == "openai/gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_temperature_defaults_from_provider(self, mock_litellm, llm_config_multiple):
        """Test temperature defaults from selected provider."""
        service = LLMService(config=llm_config_multiple)
        service.providers["analysis"].default_temperature = 0.5
        messages = [{"role": "user", "content": "Hello"}]

        await service.chat_completion(messages=messages, provider_name="analysis")

        call_kwargs = mock_litellm.call_args[1]
        assert call_kwargs["temperature"] == 1.0

    @pytest.mark.asyncio
    async def test_temperature_explicit_override(self, mock_litellm, llm_config_multiple):
        """Test explicit temperature override."""
        service = LLMService(config=llm_config_multiple)
        service.providers["analysis"].default_temperature = 0.5
        messages = [{"role": "user", "content": "Hello"}]

        await service.chat_completion(messages=messages, provider_name="analysis", temperature=0.9)

        call_kwargs = mock_litellm.call_args[1]
        assert call_kwargs["temperature"] == 1.0

    @pytest.mark.asyncio
    async def test_provider_not_found_error(self, mock_litellm, llm_config_multiple):
        """Test error handling when provider not found."""
        service = LLMService(config=llm_config_multiple)
        messages = [{"role": "user", "content": "Hello"}]

        with pytest.raises(LLMServiceError) as exc_info:
            await service.chat_completion(messages=messages, provider_name="invalid")

        assert "Provider 'invalid' not found" in str(exc_info.value)


class TestLLMServiceChatCompletionStream:
    """Test chat_completion_stream method."""

    @pytest.mark.asyncio
    async def test_streaming_with_provider_name(self, mock_litellm, llm_config_multiple):
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

        mock_litellm.return_value = mock_stream()

        chunks = []
        async for chunk in service.chat_completion_stream(messages=messages, provider_name="chat"):
            chunks.append(chunk)

        assert len(chunks) > 0
        assert chunks[0] == "chunk"
        mock_litellm.assert_called_once()

    @pytest.mark.asyncio
    async def test_streaming_named_provider(self, mock_litellm, llm_config_multiple):
        """Test streaming with named provider."""
        service = LLMService(config=llm_config_multiple)
        messages = [{"role": "user", "content": "Analyze"}]

        async def mock_stream():
            mock_chunk = MagicMock()
            mock_delta = MagicMock()
            mock_delta.content = "chunk"
            mock_delta.reasoning_content = None
            mock_choice = MagicMock()
            mock_choice.delta = mock_delta
            mock_chunk.choices = [mock_choice]
            yield mock_chunk

        mock_litellm.return_value = mock_stream()

        chunks = []
        async for chunk in service.chat_completion_stream(messages=messages, provider_name="analysis"):
            chunks.append(chunk)

        assert len(chunks) > 0
        assert chunks[0] == "chunk"
        mock_litellm.assert_called_once()

    @pytest.mark.asyncio
    async def test_streaming_provider_not_found(self, mock_litellm, llm_config_multiple):
        """Test streaming error when provider not found."""
        service = LLMService(config=llm_config_multiple)
        messages = [{"role": "user", "content": "Hello"}]

        with pytest.raises(LLMServiceError):
            async for _ in service.chat_completion_stream(messages=messages, provider_name="invalid"):
                pass


class TestLLMServiceIntegration:
    """Test integration scenarios."""

    @pytest.mark.asyncio
    async def test_multiple_providers_same_instance(self, mock_litellm, llm_config_multiple):
        """Test multiple providers in same service instance."""
        service = LLMService(config=llm_config_multiple)
        messages = [{"role": "user", "content": "Test"}]

        await service.chat_completion(messages=messages, provider_name="chat")
        await service.chat_completion(messages=messages, provider_name="analysis")
        await service.chat_completion(messages=messages, provider_name="code")

        assert mock_litellm.call_count == 3

    @pytest.mark.asyncio
    async def test_provider_config_isolation(self, mock_litellm, llm_config_multiple):
        """Test provider config isolation."""
        service = LLMService(config=llm_config_multiple)

        messages = [{"role": "user", "content": "Test"}]

        await service.chat_completion(messages=messages, provider_name="chat")
        call1_model = mock_litellm.call_args_list[0][1]["model"]

        await service.chat_completion(messages=messages, provider_name="analysis")
        call2_model = mock_litellm.call_args_list[1][1]["model"]

        assert call1_model == "openai/gpt-4o"
        assert call2_model == "gemini/gemini-3-flash-preview"


class TestGetLLMService:
    """Test get_llm_service factory function."""

    def test_get_llm_service_with_providers(self, mock_litellm, llm_config_multiple):
        """Test get_llm_service with providers config."""
        service = get_llm_service(config=llm_config_multiple)

        assert isinstance(service, LLMService)
        assert len(service.providers) == 3

    def test_get_llm_service_single_provider_raises(self, mock_litellm, llm_config_single):
        """Test get_llm_service with only default_model (no providers) raises LLMServiceError."""
        with pytest.raises(LLMServiceError, match="providers"):
            get_llm_service(config=llm_config_single)
