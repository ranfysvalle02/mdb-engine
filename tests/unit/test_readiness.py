"""
Unit tests for the readiness probe logic.

These tests verify the readiness decision logic (MongoDB health + engine
initialization) without spinning up the full multi-app stack.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from mdb_engine.observability.health import HealthStatus


def _ready_decision(mongo_status: str, engine_initialized: bool) -> tuple[bool, int]:
    """Replicate the /ready endpoint decision logic."""
    is_ready = mongo_status == "healthy" and engine_initialized
    return is_ready, 200 if is_ready else 503


class TestReadinessDecision:
    """Test the readiness probe decision logic."""

    def test_ready_when_healthy_and_initialized(self):
        is_ready, status = _ready_decision("healthy", engine_initialized=True)
        assert is_ready is True
        assert status == 200

    def test_not_ready_when_mongo_unhealthy(self):
        is_ready, status = _ready_decision("unhealthy", engine_initialized=True)
        assert is_ready is False
        assert status == 503

    def test_not_ready_when_not_initialized(self):
        is_ready, status = _ready_decision("healthy", engine_initialized=False)
        assert is_ready is False
        assert status == 503

    def test_not_ready_when_both_bad(self):
        is_ready, status = _ready_decision("unhealthy", engine_initialized=False)
        assert is_ready is False
        assert status == 503

    def test_not_ready_when_mongo_degraded(self):
        is_ready, status = _ready_decision("degraded", engine_initialized=True)
        assert is_ready is False
        assert status == 503


class TestReadinessWithHealthCheck:
    """Test using the actual check_mongodb_health function (mocked client)."""

    @pytest.mark.asyncio
    async def test_healthy_mongo_client(self):
        from mdb_engine.observability.health import check_mongodb_health

        mock_client = AsyncMock()
        mock_client.admin.command = AsyncMock(return_value={"ok": 1})
        mock_client.server_info = AsyncMock(return_value={"version": "7.0"})

        result = await check_mongodb_health(mock_client)
        assert result.status is HealthStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_unhealthy_mongo_client(self):
        from pymongo.errors import ConnectionFailure

        from mdb_engine.observability.health import check_mongodb_health

        mock_client = AsyncMock()
        mock_client.admin.command = AsyncMock(side_effect=ConnectionFailure("refused"))

        result = await check_mongodb_health(mock_client)
        assert result.status is HealthStatus.UNHEALTHY

    @pytest.mark.asyncio
    async def test_none_client_returns_unhealthy(self):
        from mdb_engine.observability.health import check_mongodb_health

        result = await check_mongodb_health(None)
        assert result.status is HealthStatus.UNHEALTHY
