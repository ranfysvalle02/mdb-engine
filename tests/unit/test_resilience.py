"""Tests for mdb_engine.core.resilience — retry, backoff, circuit breaker."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mdb_engine.core.resilience import (
    CircuitBreaker,
    CircuitState,
    RateLimitError,
    ResiliencePolicy,
    TransientError,
    circuit_breaker_from_config,
    policy_from_config,
    resilient,
)

# ---------------------------------------------------------------------------
# ResiliencePolicy
# ---------------------------------------------------------------------------


class TestResiliencePolicy:
    def test_defaults(self):
        p = ResiliencePolicy()
        assert p.max_retries == 3
        assert p.backoff_base == 1.0
        assert p.backoff_max == 30.0
        assert p.jitter == 0.5
        assert p.timeout == 60.0
        assert p.name == "default"

    def test_custom_values(self):
        p = ResiliencePolicy(max_retries=5, timeout=None, name="llm")
        assert p.max_retries == 5
        assert p.timeout is None
        assert p.name == "llm"


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    def test_starts_closed(self):
        cb = CircuitBreaker(failure_threshold=3)
        assert cb.state is CircuitState.CLOSED
        assert cb.allow_request() is True

    def test_opens_after_threshold(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_window=60.0)
        for _ in range(3):
            cb.record_failure()
        assert cb.state is CircuitState.OPEN
        assert cb.allow_request() is False

    def test_does_not_open_below_threshold(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.state is CircuitState.CLOSED
        assert cb.allow_request() is True

    def test_transitions_to_half_open(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_window=0.0)
        cb.record_failure()
        cb.record_failure()
        # With 0 recovery window, the property getter immediately transitions
        # from OPEN -> HALF_OPEN on access
        assert cb.state is CircuitState.HALF_OPEN
        assert cb.allow_request() is True

    def test_half_open_success_resets_to_closed(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_window=0.0)
        cb.record_failure()
        cb.record_failure()
        _ = cb.state  # trigger half-open
        cb.record_success()
        assert cb.state is CircuitState.CLOSED
        assert cb._failure_count == 0

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_window=0.0)
        cb.record_failure()
        cb.record_failure()
        _ = cb.state  # trigger half-open
        cb.record_failure()
        # Should go back to open
        assert cb._state is CircuitState.OPEN

    def test_stats(self):
        cb = CircuitBreaker(failure_threshold=2, name="test")
        cb.record_failure()
        stats = cb.stats
        assert stats["state"] == "closed"
        assert stats["failure_count"] == 1
        assert stats["total_trips"] == 0

    def test_total_trips_increments(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_window=0.0)
        cb.record_failure()
        assert cb._total_trips == 1
        cb.record_success()  # close
        cb.record_failure()  # open again
        assert cb._total_trips == 2


# ---------------------------------------------------------------------------
# RateLimitError
# ---------------------------------------------------------------------------


class TestRateLimitError:
    def test_is_transient(self):
        err = RateLimitError("rate limited")
        assert isinstance(err, TransientError)

    def test_retry_after(self):
        err = RateLimitError("rate limited", retry_after=5.0)
        assert err.retry_after == 5.0

    def test_retry_after_none(self):
        err = RateLimitError("rate limited")
        assert err.retry_after is None


# ---------------------------------------------------------------------------
# @resilient decorator
# ---------------------------------------------------------------------------


class TestResilient:
    @pytest.mark.asyncio
    async def test_returns_on_first_success(self):
        mock_fn = AsyncMock(return_value="ok")
        policy = ResiliencePolicy(max_retries=2, timeout=None, name="test")

        @resilient(policy)
        async def fn():
            return await mock_fn()

        result = await fn()
        assert result == "ok"
        assert mock_fn.call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_transient_error(self):
        mock_fn = AsyncMock(side_effect=[TransientError("fail"), "ok"])
        policy = ResiliencePolicy(max_retries=2, backoff_base=0.0, jitter=0.0, timeout=None, name="test")

        @resilient(policy)
        async def fn():
            return await mock_fn()

        result = await fn()
        assert result == "ok"
        assert mock_fn.call_count == 2

    @pytest.mark.asyncio
    async def test_respects_max_retries(self):
        mock_fn = AsyncMock(side_effect=TransientError("always fails"))
        policy = ResiliencePolicy(max_retries=2, backoff_base=0.0, jitter=0.0, timeout=None, name="test")

        @resilient(policy)
        async def fn():
            return await mock_fn()

        with pytest.raises(TransientError, match="all 3 attempts failed"):
            await fn()
        # 1 initial + 2 retries = 3 calls
        assert mock_fn.call_count == 3

    @pytest.mark.asyncio
    async def test_backoff_sleep_called(self):
        mock_fn = AsyncMock(side_effect=[TransientError("fail"), "ok"])
        policy = ResiliencePolicy(max_retries=2, backoff_base=1.0, jitter=0.0, timeout=None, name="test")

        with patch("mdb_engine.core.resilience.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:

            @resilient(policy)
            async def fn():
                return await mock_fn()

            await fn()
            # After first failure: delay = min(1.0 * 2**0, 30.0) + 0 = 1.0
            mock_sleep.assert_awaited_once()
            delay = mock_sleep.call_args[0][0]
            assert 0.9 <= delay <= 1.1  # approximately 1.0

    @pytest.mark.asyncio
    async def test_rate_limit_retry_after_honoured(self):
        mock_fn = AsyncMock(side_effect=[RateLimitError("rate limited", retry_after=10.0), "ok"])
        policy = ResiliencePolicy(max_retries=2, backoff_base=1.0, jitter=0.0, timeout=None, name="test")

        with patch("mdb_engine.core.resilience.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:

            @resilient(policy)
            async def fn():
                return await mock_fn()

            await fn()
            delay = mock_sleep.call_args[0][0]
            # delay = max(1.0 * 2**0, 10.0) + 0 = 10.0
            assert delay >= 10.0

    @pytest.mark.asyncio
    async def test_timeout_fires(self):
        async def slow_fn():
            await asyncio.sleep(10)

        policy = ResiliencePolicy(max_retries=0, timeout=0.01, name="test")

        @resilient(policy)
        async def fn():
            return await slow_fn()

        with pytest.raises(TransientError):
            await fn()

    @pytest.mark.asyncio
    async def test_circuit_breaker_rejects_when_open(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_window=60.0)
        cb.record_failure()
        assert cb.state is CircuitState.OPEN

        policy = ResiliencePolicy(max_retries=0, timeout=None, name="test")

        @resilient(policy, circuit_breaker=cb)
        async def fn():
            return "ok"

        with pytest.raises(TransientError, match="Circuit breaker"):
            await fn()

    @pytest.mark.asyncio
    async def test_circuit_breaker_records_success(self):
        cb = CircuitBreaker(failure_threshold=5)
        cb.record_failure()
        assert cb._failure_count == 1

        mock_fn = AsyncMock(return_value="ok")
        policy = ResiliencePolicy(max_retries=0, timeout=None, name="test")

        @resilient(policy, circuit_breaker=cb)
        async def fn():
            return await mock_fn()

        await fn()
        assert cb._failure_count == 0  # reset by success

    @pytest.mark.asyncio
    async def test_metrics_recorded_on_success(self):
        mock_metrics = MagicMock()
        mock_fn = AsyncMock(return_value="ok")
        policy = ResiliencePolicy(max_retries=0, timeout=None, name="test")

        with patch(
            "mdb_engine.observability.metrics.get_metrics_collector",
            return_value=mock_metrics,
            create=True,
        ):

            @resilient(policy)
            async def fn():
                return await mock_fn()

            await fn()
            mock_metrics.record_operation.assert_called_once()
            call_args = mock_metrics.record_operation.call_args
            assert call_args[0][0] == "resilience.test"
            assert call_args[1]["success"] is True

    @pytest.mark.asyncio
    async def test_metrics_recorded_on_failure(self):
        mock_metrics = MagicMock()
        mock_fn = AsyncMock(side_effect=TransientError("fail"))
        policy = ResiliencePolicy(max_retries=0, backoff_base=0.0, jitter=0.0, timeout=None, name="test")

        with patch(
            "mdb_engine.observability.metrics.get_metrics_collector",
            return_value=mock_metrics,
            create=True,
        ):

            @resilient(policy)
            async def fn():
                return await mock_fn()

            with pytest.raises(TransientError):
                await fn()
            mock_metrics.record_operation.assert_called_once()
            call_args = mock_metrics.record_operation.call_args
            assert call_args[1]["success"] is False


# ---------------------------------------------------------------------------
# policy_from_config / circuit_breaker_from_config
# ---------------------------------------------------------------------------


class TestPolicyFromConfig:
    def test_builds_from_dict(self):
        cfg = {
            "max_retries": 5,
            "backoff_base": 2.0,
            "backoff_max": 60.0,
            "timeout": 120,
        }
        policy = policy_from_config(cfg, name="llm")
        assert policy.max_retries == 5
        assert policy.backoff_base == 2.0
        assert policy.backoff_max == 60.0
        assert policy.timeout == 120.0
        assert policy.name == "llm"

    def test_uses_defaults_on_empty_dict(self):
        policy = policy_from_config({}, name="embed")
        assert policy.max_retries == 3
        assert policy.backoff_base == 1.0
        assert policy.name == "embed"

    def test_custom_defaults(self):
        policy = policy_from_config(
            {},
            name="graph",
            default_retries=2,
            default_backoff_base=0.5,
            default_timeout=45.0,
        )
        assert policy.max_retries == 2
        assert policy.backoff_base == 0.5
        assert policy.timeout == 45.0

    def test_extra_retryable(self):
        class MyError(Exception):
            pass

        policy = policy_from_config({}, extra_retryable=(MyError,))
        assert MyError in policy.retryable_exceptions

    def test_null_timeout(self):
        policy = policy_from_config({"timeout": None})
        # When timeout is None in config, should use default
        assert policy.timeout is not None


class TestCircuitBreakerFromConfig:
    def test_builds_from_dict(self):
        cfg = {"circuit_failure_threshold": 10, "circuit_recovery_window": 60}
        cb = circuit_breaker_from_config(cfg, name="llm")
        assert cb.failure_threshold == 10
        assert cb.recovery_window == 60.0
        assert cb.name == "llm"

    def test_uses_defaults_on_empty_dict(self):
        cb = circuit_breaker_from_config({})
        assert cb.failure_threshold == 5
        assert cb.recovery_window == 30.0


# ---------------------------------------------------------------------------
# Small coverage additions
# ---------------------------------------------------------------------------


class TestResilienceTimeout:
    """Cover asyncio.TimeoutError retry path and edge cases."""

    @pytest.mark.asyncio
    async def test_timeout_error_retries_then_fails(self):
        """asyncio.TimeoutError triggers retry with backoff, then raises."""
        call_count = 0

        async def always_timeout():
            nonlocal call_count
            call_count += 1
            raise asyncio.TimeoutError()

        policy = ResiliencePolicy(max_retries=1, backoff_base=0.0, jitter=0.0, timeout=None, name="timeout_test")

        with patch("mdb_engine.core.resilience.asyncio.sleep", new_callable=AsyncMock):

            @resilient(policy)
            async def fn():
                return await always_timeout()

            with pytest.raises(TransientError, match="all 2 attempts failed"):
                await fn()
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_none_policy_uses_defaults(self):
        """Passing None creates a default ResiliencePolicy."""
        mock_fn = AsyncMock(return_value="ok")

        @resilient(None)
        async def fn():
            return await mock_fn()

        result = await fn()
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_metrics_import_error_does_not_break(self):
        """ImportError from metrics module is silently ignored."""
        mock_fn = AsyncMock(return_value="ok")
        policy = ResiliencePolicy(max_retries=0, timeout=None, name="test")

        with patch(
            "mdb_engine.core.resilience.get_metrics_collector",
            side_effect=ImportError("no module"),
            create=True,
        ):

            @resilient(policy)
            async def fn():
                return await mock_fn()

            result = await fn()
            assert result == "ok"
