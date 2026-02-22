"""
Unit tests for graph service robustness: lazy retry, _ensure_shared_services,
and graph_routes.
"""

from unittest.mock import MagicMock, patch

import pytest

from mdb_engine.core.service_initialization import ServiceInitializer


@pytest.fixture
def mock_get_scoped_db_fn():
    async def get_scoped_db(slug: str):
        mock_db = MagicMock()
        mock_db._read_scopes = [slug]
        mock_db._write_scope = slug
        return mock_db

    return get_scoped_db


@pytest.fixture
def service_initializer(mock_get_scoped_db_fn):
    return ServiceInitializer(
        mongo_uri="mongodb://localhost:27017",
        db_name="test_db",
        get_scoped_db_fn=mock_get_scoped_db_fn,
    )


class TestEnsureSharedServices:
    """Tests for _ensure_shared_services()."""

    def test_creates_llm_service(self, service_initializer):
        with patch("mdb_engine.core.service_initialization.contextual_logger"):
            with patch(
                "mdb_engine.llm.service.get_llm_service",
                return_value=MagicMock(),
            ) as mock_create:
                llm, emb = service_initializer._ensure_shared_services(
                    "test_app",
                    llm_config={"providers": {"chat": "openai/gpt-4o"}},
                )

                assert llm is not None
                assert service_initializer._llm_services.get("test_app") is llm
                mock_create.assert_called_once()

    def test_creates_embedding_service(self, service_initializer):
        with patch("mdb_engine.core.service_initialization.contextual_logger"):
            with patch(
                "mdb_engine.embeddings.service.get_embedding_service",
                return_value=MagicMock(),
            ) as mock_create:
                llm, emb = service_initializer._ensure_shared_services(
                    "test_app",
                    embedding_config={"default_embedding_model": "text-embedding-3-small"},
                )

                assert emb is not None
                assert service_initializer._embedding_services.get("test_app") is emb

    def test_reuses_existing_services(self, service_initializer):
        existing_llm = MagicMock()
        existing_emb = MagicMock()
        service_initializer._llm_services["test_app"] = existing_llm
        service_initializer._embedding_services["test_app"] = existing_emb

        llm, emb = service_initializer._ensure_shared_services(
            "test_app",
            llm_config={"providers": {"chat": "openai/gpt-4o"}},
        )

        assert llm is existing_llm
        assert emb is existing_emb

    def test_no_config_returns_none(self, service_initializer):
        llm, emb = service_initializer._ensure_shared_services("test_app")
        assert llm is None
        assert emb is None

    def test_derives_embedding_from_memory_config(self, service_initializer):
        with patch("mdb_engine.core.service_initialization.contextual_logger"):
            with patch(
                "mdb_engine.embeddings.service.get_embedding_service",
                return_value=MagicMock(),
            ) as mock_create:
                service_initializer._ensure_shared_services(
                    "test_app",
                    memory_config={"embedding_model": "text-embedding-3-large"},
                )

                call_kwargs = mock_create.call_args
                config = call_kwargs.kwargs.get("config") or call_kwargs[1].get("config")
                assert config["default_embedding_model"] == "text-embedding-3-large"


class TestLazyGraphRetry:
    """Tests for lazy graph retry in get_graph_service."""

    def test_returns_none_when_no_service(self, service_initializer):
        result = service_initializer.get_graph_service("nonexistent")
        assert result is None

    def test_returns_existing_service(self, service_initializer):
        mock_service = MagicMock()
        service_initializer._graph_services["test_app"] = mock_service

        result = service_initializer.get_graph_service("test_app")
        assert result is mock_service

    def test_schedules_retry_on_failed_config(self, service_initializer):
        service_initializer._failed_graph_configs["test_app"] = (
            {"enabled": True},
            {"providers": {"chat": "openai/gpt-4o"}},
        )

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_loop.return_value.is_running.return_value = True
            mock_loop.return_value.create_task = MagicMock()

            result = service_initializer.get_graph_service("test_app")

            assert result is None
            mock_loop.return_value.create_task.assert_called_once()
            assert "test_app" not in service_initializer._failed_graph_configs

    def test_failed_config_consumed_on_retry(self, service_initializer):
        service_initializer._failed_graph_configs["test_app"] = (
            {"enabled": True},
            None,
        )

        with patch("asyncio.get_event_loop", side_effect=RuntimeError):
            service_initializer.get_graph_service("test_app")

        assert "test_app" not in service_initializer._failed_graph_configs


class TestClearServices:
    """Verify clear_services clears all registries."""

    def test_clears_all_dicts(self, service_initializer):
        service_initializer._graph_services["a"] = MagicMock()
        service_initializer._memory_services["a"] = MagicMock()
        service_initializer._embedding_services["a"] = MagicMock()
        service_initializer._llm_services["a"] = MagicMock()
        service_initializer._perfect_brain_services["a"] = MagicMock()
        service_initializer._failed_graph_configs["a"] = ({}, None)

        service_initializer.clear_services()

        assert len(service_initializer._graph_services) == 0
        assert len(service_initializer._memory_services) == 0
        assert len(service_initializer._embedding_services) == 0
        assert len(service_initializer._llm_services) == 0
        assert len(service_initializer._perfect_brain_services) == 0
        assert len(service_initializer._failed_graph_configs) == 0
