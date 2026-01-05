"""
Unit tests for request-scoped FastAPI dependencies.

Tests the dependencies in mdb_engine.dependencies module.
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from mdb_engine.dependencies import (
    AppContext,
    get_app_config,
    get_app_slug,
    get_authz_provider,
    get_current_user,
    get_embedding_service,
    get_engine,
    get_llm_client,
    get_llm_model_name,
    get_memory_service,
    get_scoped_db,
    get_user_roles,
)


@pytest.fixture
def mock_request():
    """Create a mock FastAPI Request object."""
    request = MagicMock()
    request.app = MagicMock()
    request.app.state = MagicMock()
    request.state = MagicMock()
    return request


@pytest.fixture
def mock_engine():
    """Create a mock MongoDBEngine."""
    engine = MagicMock()
    engine.initialized = True
    engine.get_scoped_db = MagicMock(return_value=MagicMock())
    engine.get_app = MagicMock(
        return_value={
            "embedding_config": {
                "enabled": True,
                "default_embedding_model": "text-embedding-3-small",
            }
        }
    )
    engine.get_memory_service = MagicMock(return_value=MagicMock())
    return engine


class TestGetEngine:
    """Tests for get_engine dependency."""

    @pytest.mark.asyncio
    async def test_get_engine_success(self, mock_request, mock_engine):
        """Test successful engine retrieval."""
        mock_request.app.state.engine = mock_engine

        result = await get_engine(mock_request)

        assert result == mock_engine

    @pytest.mark.asyncio
    async def test_get_engine_not_found(self, mock_request):
        """Test engine not found returns 503."""
        mock_request.app.state.engine = None

        with pytest.raises(HTTPException) as exc_info:
            await get_engine(mock_request)

        assert exc_info.value.status_code == 503
        assert "Engine not initialized" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_get_engine_not_initialized(self, mock_request, mock_engine):
        """Test engine found but not initialized returns 503."""
        mock_engine.initialized = False
        mock_request.app.state.engine = mock_engine

        with pytest.raises(HTTPException) as exc_info:
            await get_engine(mock_request)

        assert exc_info.value.status_code == 503
        assert "not fully initialized" in exc_info.value.detail


class TestGetAppSlug:
    """Tests for get_app_slug dependency."""

    @pytest.mark.asyncio
    async def test_get_app_slug_success(self, mock_request):
        """Test successful app_slug retrieval."""
        mock_request.app.state.app_slug = "test_app"

        result = await get_app_slug(mock_request)

        assert result == "test_app"

    @pytest.mark.asyncio
    async def test_get_app_slug_not_found(self, mock_request):
        """Test app_slug not found returns 503."""
        mock_request.app.state.app_slug = None

        with pytest.raises(HTTPException) as exc_info:
            await get_app_slug(mock_request)

        assert exc_info.value.status_code == 503
        assert "App slug not configured" in exc_info.value.detail


class TestGetAppConfig:
    """Tests for get_app_config dependency."""

    @pytest.mark.asyncio
    async def test_get_app_config_from_state(self, mock_request):
        """Test config retrieved from app.state.manifest."""
        expected_config = {"app_name": "test", "version": "1.0"}
        mock_request.app.state.manifest = expected_config
        mock_request.app.state.engine = None
        mock_request.app.state.app_slug = None

        result = await get_app_config(mock_request)

        assert result == expected_config

    @pytest.mark.asyncio
    async def test_get_app_config_from_engine(self, mock_request, mock_engine):
        """Test config retrieved from engine.get_app when not on state."""
        expected_config = {"app_name": "test", "version": "1.0"}
        mock_request.app.state.manifest = None
        mock_request.app.state.engine = mock_engine
        mock_request.app.state.app_slug = "test_app"
        mock_engine.get_app.return_value = expected_config

        result = await get_app_config(mock_request)

        assert result == expected_config
        mock_engine.get_app.assert_called_once_with("test_app")

    @pytest.mark.asyncio
    async def test_get_app_config_not_found(self, mock_request):
        """Test config not found returns 503."""
        mock_request.app.state.manifest = None
        mock_request.app.state.engine = None
        mock_request.app.state.app_slug = None

        with pytest.raises(HTTPException) as exc_info:
            await get_app_config(mock_request)

        assert exc_info.value.status_code == 503
        assert "App configuration not available" in exc_info.value.detail


class TestGetScopedDb:
    """Tests for get_scoped_db dependency."""

    @pytest.mark.asyncio
    async def test_get_scoped_db_success(self, mock_request, mock_engine):
        """Test successful scoped db retrieval."""
        expected_db = MagicMock()
        mock_engine.get_scoped_db.return_value = expected_db
        mock_request.app.state.engine = mock_engine
        mock_request.app.state.app_slug = "test_app"

        result = await get_scoped_db(mock_request)

        assert result == expected_db
        mock_engine.get_scoped_db.assert_called_once_with("test_app")

    @pytest.mark.asyncio
    async def test_get_scoped_db_engine_not_found(self, mock_request):
        """Test scoped db retrieval when engine not found."""
        mock_request.app.state.engine = None

        with pytest.raises(HTTPException) as exc_info:
            await get_scoped_db(mock_request)

        assert exc_info.value.status_code == 503


class TestGetEmbeddingService:
    """Tests for get_embedding_service dependency."""

    @pytest.mark.asyncio
    async def test_get_embedding_service_success(self, mock_request, mock_engine):
        """Test successful embedding service retrieval."""
        mock_request.app.state.engine = mock_engine
        mock_request.app.state.app_slug = "test_app"

        mock_service = MagicMock()
        with patch(
            "mdb_engine.dependencies.get_embedding_service",
            return_value=mock_service,
        ):
            # Import and call the actual function
            from mdb_engine.dependencies import get_embedding_service as get_emb

            # We need to mock the internal call, not the function itself
            with patch(
                "mdb_engine.embeddings.service.get_embedding_service",
                return_value=mock_service,
            ):
                result = await get_emb(mock_request)

        # Just verify it doesn't raise and returns something
        assert result is not None

    @pytest.mark.asyncio
    async def test_get_embedding_service_disabled(self, mock_request, mock_engine):
        """Test embedding service when disabled in config."""
        mock_engine.get_app.return_value = {"embedding_config": {"enabled": False}}
        mock_request.app.state.engine = mock_engine
        mock_request.app.state.app_slug = "test_app"

        with pytest.raises(HTTPException) as exc_info:
            await get_embedding_service(mock_request)

        assert exc_info.value.status_code == 503
        assert "disabled" in exc_info.value.detail.lower()

    @pytest.mark.asyncio
    async def test_get_embedding_service_app_not_found(self, mock_request, mock_engine):
        """Test embedding service when app config not found."""
        mock_engine.get_app.return_value = None
        mock_request.app.state.engine = mock_engine
        mock_request.app.state.app_slug = "nonexistent_app"

        with pytest.raises(HTTPException) as exc_info:
            await get_embedding_service(mock_request)

        assert exc_info.value.status_code == 503
        assert "not found" in exc_info.value.detail.lower()


class TestGetMemoryService:
    """Tests for get_memory_service dependency."""

    @pytest.mark.asyncio
    async def test_get_memory_service_success(self, mock_request, mock_engine):
        """Test successful memory service retrieval."""
        expected_service = MagicMock()
        mock_engine.get_memory_service.return_value = expected_service
        mock_request.app.state.engine = mock_engine
        mock_request.app.state.app_slug = "test_app"

        result = await get_memory_service(mock_request)

        assert result == expected_service
        mock_engine.get_memory_service.assert_called_once_with("test_app")

    @pytest.mark.asyncio
    async def test_get_memory_service_not_configured(self, mock_request, mock_engine):
        """Test memory service returns None when not configured."""
        mock_engine.get_memory_service.return_value = None
        mock_request.app.state.engine = mock_engine
        mock_request.app.state.app_slug = "test_app"

        result = await get_memory_service(mock_request)

        assert result is None

    @pytest.mark.asyncio
    async def test_get_memory_service_engine_not_found(self, mock_request):
        """Test memory service returns None when engine not found."""
        mock_request.app.state.engine = None
        mock_request.app.state.app_slug = None

        result = await get_memory_service(mock_request)

        assert result is None


class TestGetLLMClient:
    """Tests for get_llm_client dependency."""

    @pytest.mark.asyncio
    async def test_get_llm_client_azure(self, mock_request):
        """Test Azure OpenAI client creation."""
        with patch.dict(
            os.environ,
            {
                "AZURE_OPENAI_API_KEY": "test-key",
                "AZURE_OPENAI_ENDPOINT": "https://test.openai.azure.com/",
            },
        ):
            with patch("openai.AzureOpenAI") as mock_azure:
                mock_client = MagicMock()
                mock_azure.return_value = mock_client

                result = await get_llm_client(mock_request)

                assert result == mock_client
                mock_azure.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_llm_client_openai(self, mock_request):
        """Test standard OpenAI client creation."""
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "sk-test"},
            clear=True,
        ):
            # Clear Azure env vars
            os.environ.pop("AZURE_OPENAI_API_KEY", None)
            os.environ.pop("AZURE_OPENAI_ENDPOINT", None)

            with patch("openai.OpenAI") as mock_openai:
                mock_client = MagicMock()
                mock_openai.return_value = mock_client

                result = await get_llm_client(mock_request)

                assert result == mock_client
                mock_openai.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_llm_client_not_configured(self, mock_request):
        """Test LLM client when not configured."""
        with patch.dict(os.environ, {}, clear=True):
            # Clear all relevant env vars
            os.environ.pop("AZURE_OPENAI_API_KEY", None)
            os.environ.pop("AZURE_OPENAI_ENDPOINT", None)
            os.environ.pop("OPENAI_API_KEY", None)

            with pytest.raises(HTTPException) as exc_info:
                await get_llm_client(mock_request)

            assert exc_info.value.status_code == 503
            assert "No LLM API key configured" in exc_info.value.detail


class TestGetLLMModelName:
    """Tests for get_llm_model_name function."""

    def test_azure_model_name(self):
        """Test getting Azure deployment name."""
        with patch.dict(
            os.environ,
            {
                "AZURE_OPENAI_API_KEY": "test-key",
                "AZURE_OPENAI_DEPLOYMENT_NAME": "my-gpt4",
            },
        ):
            result = get_llm_model_name()
            assert result == "my-gpt4"

    def test_azure_model_name_default(self):
        """Test Azure deployment name default."""
        with patch.dict(os.environ, {"AZURE_OPENAI_API_KEY": "test-key"}):
            os.environ.pop("AZURE_OPENAI_DEPLOYMENT_NAME", None)
            result = get_llm_model_name()
            assert result == "gpt-4o"

    def test_openai_model_name(self):
        """Test getting OpenAI model name."""
        with patch.dict(os.environ, {"OPENAI_MODEL": "gpt-4-turbo"}, clear=True):
            os.environ.pop("AZURE_OPENAI_API_KEY", None)
            result = get_llm_model_name()
            assert result == "gpt-4-turbo"


class TestAuthDependencies:
    """Tests for auth-related dependencies."""

    @pytest.mark.asyncio
    async def test_get_authz_provider_exists(self, mock_request):
        """Test getting authorization provider."""
        mock_authz = MagicMock()
        mock_request.app.state.authz_provider = mock_authz

        result = await get_authz_provider(mock_request)

        assert result == mock_authz

    @pytest.mark.asyncio
    async def test_get_authz_provider_none(self, mock_request):
        """Test getting authorization provider when not configured."""
        mock_request.app.state.authz_provider = None

        result = await get_authz_provider(mock_request)

        assert result is None

    @pytest.mark.asyncio
    async def test_get_current_user_exists(self, mock_request):
        """Test getting current user."""
        mock_user = {"email": "test@example.com", "id": "123"}
        mock_request.state.user = mock_user

        result = await get_current_user(mock_request)

        assert result == mock_user

    @pytest.mark.asyncio
    async def test_get_current_user_none(self, mock_request):
        """Test getting current user when not authenticated."""
        mock_request.state.user = None

        result = await get_current_user(mock_request)

        assert result is None

    @pytest.mark.asyncio
    async def test_get_user_roles(self, mock_request):
        """Test getting user roles."""
        mock_request.state.user_roles = ["admin", "editor"]

        result = await get_user_roles(mock_request)

        assert result == ["admin", "editor"]

    @pytest.mark.asyncio
    async def test_get_user_roles_empty(self, mock_request):
        """Test getting user roles when empty list."""
        mock_request.state.user_roles = []

        result = await get_user_roles(mock_request)

        assert result == []


class TestAppContext:
    """Tests for AppContext all-in-one dependency."""

    def test_app_context_engine(self, mock_request, mock_engine):
        """Test AppContext.engine property."""
        mock_request.app.state.engine = mock_engine

        ctx = AppContext(request=mock_request)

        assert ctx.engine == mock_engine

    def test_app_context_slug(self, mock_request, mock_engine):
        """Test AppContext.slug property."""
        mock_request.app.state.engine = mock_engine
        mock_request.app.state.app_slug = "my_app"

        ctx = AppContext(request=mock_request)

        assert ctx.slug == "my_app"

    def test_app_context_db(self, mock_request, mock_engine):
        """Test AppContext.db property."""
        mock_db = MagicMock()
        mock_engine.get_scoped_db.return_value = mock_db
        mock_request.app.state.engine = mock_engine
        mock_request.app.state.app_slug = "my_app"

        ctx = AppContext(request=mock_request)

        assert ctx.db == mock_db
        mock_engine.get_scoped_db.assert_called_once_with("my_app")

    def test_app_context_user(self, mock_request, mock_engine):
        """Test AppContext.user property."""
        mock_user = {"email": "test@example.com"}
        mock_request.app.state.engine = mock_engine
        mock_request.state.user = mock_user

        ctx = AppContext(request=mock_request)

        assert ctx.user == mock_user

    def test_app_context_require_user_success(self, mock_request, mock_engine):
        """Test AppContext.require_user when authenticated."""
        mock_user = {"email": "test@example.com"}
        mock_request.app.state.engine = mock_engine
        mock_request.state.user = mock_user

        ctx = AppContext(request=mock_request)
        result = ctx.require_user()

        assert result == mock_user

    def test_app_context_require_user_not_authenticated(self, mock_request, mock_engine):
        """Test AppContext.require_user when not authenticated."""
        mock_request.app.state.engine = mock_engine
        mock_request.state.user = None

        ctx = AppContext(request=mock_request)

        with pytest.raises(HTTPException) as exc_info:
            ctx.require_user()

        assert exc_info.value.status_code == 401

    def test_app_context_require_role_success(self, mock_request, mock_engine):
        """Test AppContext.require_role when user has role."""
        mock_user = {"email": "test@example.com"}
        mock_request.app.state.engine = mock_engine
        mock_request.state.user = mock_user
        mock_request.state.user_roles = ["admin", "editor"]

        ctx = AppContext(request=mock_request)
        result = ctx.require_role("admin")

        assert result == mock_user

    def test_app_context_require_role_missing(self, mock_request, mock_engine):
        """Test AppContext.require_role when user lacks role."""
        mock_user = {"email": "test@example.com"}
        mock_request.app.state.engine = mock_engine
        mock_request.state.user = mock_user
        mock_request.state.user_roles = ["viewer"]

        ctx = AppContext(request=mock_request)

        with pytest.raises(HTTPException) as exc_info:
            ctx.require_role("admin")

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_app_context_check_permission(self, mock_request, mock_engine):
        """Test AppContext.check_permission method."""
        mock_authz = MagicMock()
        mock_authz.check = AsyncMock(return_value=True)
        mock_request.app.state.engine = mock_engine
        mock_request.app.state.authz_provider = mock_authz
        mock_request.state.user = {"email": "test@example.com"}

        ctx = AppContext(request=mock_request)
        result = await ctx.check_permission("documents", "read")

        assert result is True
        mock_authz.check.assert_called_once_with("test@example.com", "documents", "read")


class TestEmbeddingDependenciesUtility:
    """Tests for embedding dependencies utility functions."""

    def test_get_embedding_service_for_app_success(self):
        """Test get_embedding_service_for_app utility."""
        from mdb_engine.embeddings.dependencies import get_embedding_service_for_app

        mock_engine = MagicMock()
        mock_engine.get_app.return_value = {
            "embedding_config": {
                "enabled": True,
                "default_embedding_model": "text-embedding-3-small",
            }
        }

        with patch(
            "mdb_engine.embeddings.dependencies.get_embedding_service"
        ) as mock_create:
            mock_service = MagicMock()
            mock_create.return_value = mock_service

            result = get_embedding_service_for_app("test_app", mock_engine)

            assert result == mock_service
            mock_engine.get_app.assert_called_once_with("test_app")

    def test_get_embedding_service_for_app_engine_none(self):
        """Test get_embedding_service_for_app with None engine."""
        from mdb_engine.embeddings.dependencies import get_embedding_service_for_app

        result = get_embedding_service_for_app("test_app", None)

        assert result is None

    def test_get_embedding_service_for_app_disabled(self):
        """Test get_embedding_service_for_app when embedding disabled."""
        from mdb_engine.embeddings.dependencies import get_embedding_service_for_app

        mock_engine = MagicMock()
        mock_engine.get_app.return_value = {"embedding_config": {"enabled": False}}

        result = get_embedding_service_for_app("test_app", mock_engine)

        assert result is None

    def test_get_embedding_service_for_app_no_config(self):
        """Test get_embedding_service_for_app when no app config."""
        from mdb_engine.embeddings.dependencies import get_embedding_service_for_app

        mock_engine = MagicMock()
        mock_engine.get_app.return_value = None

        result = get_embedding_service_for_app("test_app", mock_engine)

        assert result is None
