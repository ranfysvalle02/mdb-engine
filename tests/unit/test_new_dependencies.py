"""
Unit tests for new FastAPI dependencies and RequestContext properties.

Covers:
- get_llm_service
- get_perfect_brain
- get_graph_service_optional
- RequestContext.llm_service
- RequestContext.embedding_service (cached path)
"""

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from mdb_engine.dependencies import (
    RequestContext,
    get_graph_service_optional,
    get_llm_service,
    get_perfect_brain,
)


@pytest.fixture
def mock_request():
    request = MagicMock()
    request.app = MagicMock()
    request.app.state = MagicMock()
    request.state = MagicMock()
    return request


@pytest.fixture
def mock_engine():
    engine = MagicMock()
    engine.initialized = True
    engine.get_app = MagicMock(return_value={"slug": "test"})
    engine.get_memory_service = MagicMock(return_value=None)
    engine.get_graph_service = MagicMock(return_value=None)
    engine.get_embedding_service = MagicMock(return_value=None)
    engine.get_llm_service = MagicMock(return_value=None)
    engine.get_perfect_brain = MagicMock(return_value=None)
    return engine


class TestGetLlmService:
    """Tests for get_llm_service dependency."""

    @pytest.mark.asyncio
    async def test_returns_cached_service(self, mock_request, mock_engine):
        mock_service = MagicMock()
        mock_engine.get_llm_service.return_value = mock_service
        mock_request.app.state.engine = mock_engine
        mock_request.app.state.app_slug = "test"

        result = await get_llm_service(mock_request)
        assert result is mock_service

    @pytest.mark.asyncio
    async def test_raises_503_when_not_available(self, mock_request, mock_engine):
        mock_engine.get_llm_service.return_value = None
        mock_request.app.state.engine = mock_engine
        mock_request.app.state.app_slug = "test"

        with pytest.raises(HTTPException) as exc_info:
            await get_llm_service(mock_request)
        assert exc_info.value.status_code == 503


class TestGetPerfectBrain:
    """Tests for get_perfect_brain dependency."""

    @pytest.mark.asyncio
    async def test_returns_brain(self, mock_request, mock_engine):
        mock_brain = MagicMock()
        mock_engine.get_perfect_brain.return_value = mock_brain
        mock_request.app.state.engine = mock_engine
        mock_request.app.state.app_slug = "test"

        result = await get_perfect_brain(mock_request)
        assert result is mock_brain

    @pytest.mark.asyncio
    async def test_raises_503_when_not_configured(self, mock_request, mock_engine):
        mock_engine.get_perfect_brain.return_value = None
        mock_request.app.state.engine = mock_engine
        mock_request.app.state.app_slug = "test"

        with pytest.raises(HTTPException) as exc_info:
            await get_perfect_brain(mock_request)
        assert exc_info.value.status_code == 503


class TestGetGraphServiceOptional:
    """Tests for get_graph_service_optional dependency."""

    @pytest.mark.asyncio
    async def test_returns_service_when_available(self, mock_request, mock_engine):
        mock_service = MagicMock()
        mock_engine.get_graph_service.return_value = mock_service
        mock_request.app.state.engine = mock_engine
        mock_request.app.state.app_slug = "test"

        result = await get_graph_service_optional(mock_request)
        assert result is mock_service

    @pytest.mark.asyncio
    async def test_returns_none_when_not_available(self, mock_request, mock_engine):
        mock_engine.get_graph_service.return_value = None
        mock_request.app.state.engine = mock_engine
        mock_request.app.state.app_slug = "test"

        result = await get_graph_service_optional(mock_request)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_engine(self, mock_request):
        mock_request.app.state.engine = None

        result = await get_graph_service_optional(mock_request)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_slug(self, mock_request, mock_engine):
        mock_request.app.state.engine = mock_engine
        mock_request.app.state.app_slug = None

        result = await get_graph_service_optional(mock_request)
        assert result is None


class TestRequestContextLlmService:
    """Tests for RequestContext.llm_service property."""

    def test_returns_cached_llm_service(self, mock_request, mock_engine):
        mock_service = MagicMock()
        mock_engine.get_llm_service.return_value = mock_service
        mock_request.app.state.engine = mock_engine
        mock_request.app.state.app_slug = "test"

        ctx = RequestContext(mock_request)
        assert ctx.llm_service is mock_service
        assert ctx.llm_service is mock_service

    def test_returns_none_when_not_available(self, mock_request, mock_engine):
        mock_engine.get_llm_service.return_value = None
        mock_request.app.state.engine = mock_engine
        mock_request.app.state.app_slug = "test"

        ctx = RequestContext(mock_request)
        assert ctx.llm_service is None


class TestRequestContextEmbeddingService:
    """Tests for RequestContext.embedding_service (uses shared service)."""

    def test_returns_shared_service(self, mock_request, mock_engine):
        mock_service = MagicMock()
        mock_engine.get_embedding_service.return_value = mock_service
        mock_request.app.state.engine = mock_engine
        mock_request.app.state.app_slug = "test"

        ctx = RequestContext(mock_request)
        assert ctx.embedding_service is mock_service

    def test_returns_none_when_not_available(self, mock_request, mock_engine):
        mock_engine.get_embedding_service.return_value = None
        mock_request.app.state.engine = mock_engine
        mock_request.app.state.app_slug = "test"

        ctx = RequestContext(mock_request)
        assert ctx.embedding_service is None
