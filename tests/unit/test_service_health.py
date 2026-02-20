"""Tests for mdb_engine.observability.health — service health checks."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from mdb_engine.observability.health import (
    HealthStatus,
    check_embedding_health,
    check_graph_health,
    check_llm_health,
)


def _make_circuit_breaker(state: str, failure_count: int = 0, total_trips: int = 0):
    """Create a mock circuit breaker with a given state."""
    cb = MagicMock()
    cb.stats = {
        "state": state,
        "failure_count": failure_count,
        "total_trips": total_trips,
    }
    return cb


# ---------------------------------------------------------------------------
# check_llm_health
# ---------------------------------------------------------------------------


class TestCheckLLMHealth:
    @pytest.mark.asyncio
    async def test_returns_unknown_when_service_is_none(self):
        result = await check_llm_health(None)
        assert result.status is HealthStatus.UNKNOWN
        assert result.name == "llm"

    @pytest.mark.asyncio
    async def test_healthy_when_no_circuit_breaker(self):
        service = SimpleNamespace()  # no _circuit_breaker attribute
        result = await check_llm_health(service)
        assert result.status is HealthStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_healthy_when_closed(self):
        service = SimpleNamespace(_circuit_breaker=_make_circuit_breaker("closed"))
        result = await check_llm_health(service)
        assert result.status is HealthStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_degraded_when_half_open(self):
        service = SimpleNamespace(_circuit_breaker=_make_circuit_breaker("half_open"))
        result = await check_llm_health(service)
        assert result.status is HealthStatus.DEGRADED

    @pytest.mark.asyncio
    async def test_unhealthy_when_open(self):
        service = SimpleNamespace(_circuit_breaker=_make_circuit_breaker("open", failure_count=5, total_trips=2))
        result = await check_llm_health(service)
        assert result.status is HealthStatus.UNHEALTHY
        assert "OPEN" in result.message
        assert result.details["failure_count"] == 5


# ---------------------------------------------------------------------------
# check_embedding_health
# ---------------------------------------------------------------------------


class TestCheckEmbeddingHealth:
    @pytest.mark.asyncio
    async def test_returns_unknown_when_provider_is_none(self):
        result = await check_embedding_health(None)
        assert result.status is HealthStatus.UNKNOWN

    @pytest.mark.asyncio
    async def test_healthy_when_no_circuit_breaker(self):
        provider = SimpleNamespace()
        result = await check_embedding_health(provider)
        assert result.status is HealthStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_healthy_when_closed(self):
        provider = SimpleNamespace(_circuit_breaker=_make_circuit_breaker("closed"))
        result = await check_embedding_health(provider)
        assert result.status is HealthStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_degraded_when_half_open(self):
        provider = SimpleNamespace(_circuit_breaker=_make_circuit_breaker("half_open"))
        result = await check_embedding_health(provider)
        assert result.status is HealthStatus.DEGRADED

    @pytest.mark.asyncio
    async def test_unhealthy_when_open(self):
        provider = SimpleNamespace(_circuit_breaker=_make_circuit_breaker("open", failure_count=3))
        result = await check_embedding_health(provider)
        assert result.status is HealthStatus.UNHEALTHY


# ---------------------------------------------------------------------------
# check_graph_health
# ---------------------------------------------------------------------------


class TestCheckGraphHealth:
    @pytest.mark.asyncio
    async def test_returns_unknown_when_service_is_none(self):
        result = await check_graph_health(None)
        assert result.status is HealthStatus.UNKNOWN

    @pytest.mark.asyncio
    async def test_healthy_when_disabled(self):
        """Graph service returns HEALTHY when explicitly disabled."""
        service = SimpleNamespace(_enabled=False)
        result = await check_graph_health(service)
        assert result.status is HealthStatus.HEALTHY
        assert "disabled" in result.message.lower()

    @pytest.mark.asyncio
    async def test_healthy_when_enabled_and_closed(self):
        service = SimpleNamespace(
            _enabled=True,
            _circuit_breaker=_make_circuit_breaker("closed"),
        )
        result = await check_graph_health(service)
        assert result.status is HealthStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_unhealthy_when_circuit_breaker_open(self):
        service = SimpleNamespace(
            _enabled=True,
            _circuit_breaker=_make_circuit_breaker("open", failure_count=5),
        )
        result = await check_graph_health(service)
        assert result.status is HealthStatus.UNHEALTHY

    @pytest.mark.asyncio
    async def test_degraded_when_half_open(self):
        service = SimpleNamespace(
            _enabled=True,
            _circuit_breaker=_make_circuit_breaker("half_open"),
        )
        result = await check_graph_health(service)
        assert result.status is HealthStatus.DEGRADED

    @pytest.mark.asyncio
    async def test_healthy_when_enabled_no_circuit_breaker(self):
        service = SimpleNamespace(_enabled=True)
        result = await check_graph_health(service)
        assert result.status is HealthStatus.HEALTHY
