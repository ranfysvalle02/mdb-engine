"""
Shared resilience primitives for cross-service fault tolerance.

Provides:
- ``@resilient`` decorator  -- retry with exponential backoff, jitter, and timeout
- ``CircuitBreaker``         -- half-open / open / closed pattern
- ``ResiliencePolicy``       -- per-service configuration (loadable from manifest)
- ``TransientError`` / ``RateLimitError`` -- common exception types

Usage::

    from mdb_engine.core.resilience import resilient, ResiliencePolicy

    policy = ResiliencePolicy(max_retries=3, backoff_base=1.0, timeout=60)

    @resilient(policy)
    async def call_llm(messages):
        return await provider.chat_completion(messages)

All services (LLM, embeddings, graph) can use the same decorator so
retry behaviour, circuit-breaking, and observability are consistent.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeVar

from ..exceptions import MongoDBEngineError

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class TransientError(MongoDBEngineError):
    """Retryable transient failure (network blip, server 5xx, etc.)."""


class RateLimitError(TransientError):
    """Provider rate-limit hit (HTTP 429 / ResourceExhausted)."""

    def __init__(
        self,
        message: str,
        retry_after: float | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


# ---------------------------------------------------------------------------
# Default retryable exception set
# ---------------------------------------------------------------------------

#: Exceptions that are safe to retry automatically.
DEFAULT_RETRYABLE: tuple[type[BaseException], ...] = (
    TransientError,
    RateLimitError,
    ConnectionError,
    TimeoutError,
    OSError,
)

# ---------------------------------------------------------------------------
# ResiliencePolicy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResiliencePolicy:
    """Per-service resilience configuration.

    All values are optional and fall back to sensible defaults.
    """

    #: Maximum number of retry attempts (0 = no retries).
    max_retries: int = 3
    #: Base delay (seconds) for exponential backoff.  Actual delay is
    #: ``min(backoff_base * 2**attempt, backoff_max) + jitter``.
    backoff_base: float = 1.0
    #: Maximum backoff delay in seconds.
    backoff_max: float = 30.0
    #: Maximum random jitter added to backoff (seconds).
    jitter: float = 0.5
    #: Per-call timeout in seconds (``None`` = no timeout).
    timeout: float | None = 60.0
    #: Tuple of exception types considered retryable.
    retryable_exceptions: tuple[type[BaseException], ...] = field(
        default=DEFAULT_RETRYABLE,
    )
    #: Optional name used in log messages / metrics.
    name: str = "default"


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Simple circuit breaker for sustained failures.

    * **Closed** (normal) -- requests flow through.
    * **Open** -- requests are rejected immediately for ``recovery_window``
      seconds after ``failure_threshold`` consecutive failures.
    * **Half-open** -- one probe request is allowed; success resets, failure
      re-opens.

    Thread-safety: this class is **not** thread-safe.  In an async context
    all access runs on the event-loop thread, which is fine.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_window: float = 30.0,
        name: str = "circuit",
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_window = recovery_window
        self.name = name

        self._state = CircuitState.CLOSED
        self._failure_count: int = 0
        self._last_failure_time: float = 0.0
        self._total_trips: int = 0

    # -- public API ----------------------------------------------------------

    @property
    def state(self) -> CircuitState:
        if self._state is CircuitState.OPEN:
            if time.monotonic() - self._last_failure_time >= self.recovery_window:
                self._state = CircuitState.HALF_OPEN
                logger.info(
                    "CIRCUIT_HALF_OPEN",
                    extra={"circuit": self.name},
                )
        return self._state

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "failure_count": self._failure_count,
            "total_trips": self._total_trips,
        }

    def record_success(self) -> None:
        if self._state is not CircuitState.CLOSED:
            logger.info(
                "CIRCUIT_CLOSED",
                extra={"circuit": self.name, "previous_failures": self._failure_count},
            )
        self._failure_count = 0
        self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        if self._failure_count >= self.failure_threshold:
            if self._state is not CircuitState.OPEN:
                self._total_trips += 1
                logger.warning(
                    "CIRCUIT_OPEN",
                    extra={
                        "circuit": self.name,
                        "failures": self._failure_count,
                        "recovery_seconds": self.recovery_window,
                        "total_trips": self._total_trips,
                    },
                )
            self._state = CircuitState.OPEN

    def allow_request(self) -> bool:
        """Return ``True`` if a request should be allowed through."""
        state = self.state  # triggers half-open check
        if state is CircuitState.CLOSED:
            return True
        if state is CircuitState.HALF_OPEN:
            return True  # allow one probe
        return False


# ---------------------------------------------------------------------------
# @resilient decorator
# ---------------------------------------------------------------------------


def resilient(
    policy: ResiliencePolicy | None = None,
    *,
    circuit_breaker: CircuitBreaker | None = None,
) -> Callable[[F], F]:
    """Decorator that adds retry, backoff, timeout, and circuit-breaking.

    Works with **async** functions only (the primary use-case in mdb-engine).

    Args:
        policy: Resilience configuration.  Uses sensible defaults when ``None``.
        circuit_breaker: Optional circuit breaker instance shared across calls.

    Example::

        @resilient(ResiliencePolicy(max_retries=3, timeout=60, name="llm"))
        async def call_llm(messages):
            ...
    """
    if policy is None:
        policy = ResiliencePolicy()

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Circuit breaker gate
            if circuit_breaker and not circuit_breaker.allow_request():
                raise TransientError(
                    f"Circuit breaker '{circuit_breaker.name}' is open -- " f"rejecting request to {policy.name}"
                )

            # Optionally record resilience metrics
            _metrics = None
            try:
                from ..observability.metrics import get_metrics_collector

                _metrics = get_metrics_collector()
            except (ImportError, RuntimeError):
                pass

            last_exc: BaseException | None = None
            _start = time.monotonic()
            for attempt in range(policy.max_retries + 1):
                try:
                    if policy.timeout is not None:
                        result = await asyncio.wait_for(
                            fn(*args, **kwargs),
                            timeout=policy.timeout,
                        )
                    else:
                        result = await fn(*args, **kwargs)

                    if circuit_breaker:
                        circuit_breaker.record_success()
                    # Record success metric
                    if _metrics:
                        duration_ms = (time.monotonic() - _start) * 1000
                        _metrics.record_operation(
                            f"resilience.{policy.name}",
                            duration_ms,
                            success=True,
                        )
                    return result

                except policy.retryable_exceptions as exc:
                    last_exc = exc
                    if circuit_breaker:
                        circuit_breaker.record_failure()

                    is_last = attempt >= policy.max_retries
                    if is_last:
                        break

                    # Compute backoff delay
                    delay = min(
                        policy.backoff_base * (2**attempt),
                        policy.backoff_max,
                    )
                    # Honour Retry-After header if present
                    if isinstance(exc, RateLimitError) and exc.retry_after:
                        delay = max(delay, exc.retry_after)
                    # Add jitter
                    delay += random.uniform(0, policy.jitter)

                    logger.warning(
                        "RESILIENCE_RETRY",
                        extra={
                            "service": policy.name,
                            "attempt": attempt + 1,
                            "max_retries": policy.max_retries,
                            "delay_seconds": round(delay, 2),
                            "error": str(exc),
                        },
                    )
                    await asyncio.sleep(delay)

                except asyncio.TimeoutError as exc:
                    last_exc = exc
                    if circuit_breaker:
                        circuit_breaker.record_failure()

                    is_last = attempt >= policy.max_retries
                    if is_last:
                        break

                    delay = min(
                        policy.backoff_base * (2**attempt),
                        policy.backoff_max,
                    ) + random.uniform(0, policy.jitter)

                    logger.warning(
                        "RESILIENCE_TIMEOUT_RETRY",
                        extra={
                            "service": policy.name,
                            "attempt": attempt + 1,
                            "timeout": policy.timeout,
                            "delay_seconds": round(delay, 2),
                        },
                    )
                    await asyncio.sleep(delay)

            # All retries exhausted
            if _metrics:
                duration_ms = (time.monotonic() - _start) * 1000
                _metrics.record_operation(
                    f"resilience.{policy.name}",
                    duration_ms,
                    success=False,
                )
            logger.error(
                "RESILIENCE_EXHAUSTED",
                extra={
                    "service": policy.name,
                    "attempts": policy.max_retries + 1,
                    "final_error": str(last_exc),
                },
            )
            raise TransientError(f"{policy.name}: all {policy.max_retries + 1} attempts failed") from last_exc

        return wrapper  # type: ignore[return-value]

    return decorator


# ---------------------------------------------------------------------------
# Helper: build policy from manifest config
# ---------------------------------------------------------------------------


def policy_from_config(
    config: dict[str, Any],
    name: str = "default",
    *,
    default_retries: int = 3,
    default_backoff_base: float = 1.0,
    default_backoff_max: float = 30.0,
    default_timeout: float = 60.0,
    extra_retryable: tuple[type[BaseException], ...] = (),
) -> ResiliencePolicy:
    """Build a ``ResiliencePolicy`` from a manifest ``resilience`` config block.

    Example config::

        {
            "max_retries": 3,
            "backoff_base": 1.0,
            "backoff_max": 30.0,
            "timeout": 60
        }

    Args:
        config: Dict of resilience settings (e.g. ``manifest["resilience"]``).
        name: Logical name for log messages / metrics.
        default_retries: Fallback when ``max_retries`` is absent.
        default_backoff_base: Fallback for ``backoff_base``.
        default_backoff_max: Fallback for ``backoff_max``.
        default_timeout: Fallback for ``timeout``.
        extra_retryable: Additional exception types to add to the default
            retryable set (e.g. ``(LLMServiceError,)``).
    """
    retryable = (*DEFAULT_RETRYABLE, *extra_retryable) if extra_retryable else DEFAULT_RETRYABLE
    return ResiliencePolicy(
        max_retries=int(config.get("max_retries", default_retries)),
        backoff_base=float(config.get("backoff_base", default_backoff_base)),
        backoff_max=float(config.get("backoff_max", default_backoff_max)),
        jitter=float(config.get("jitter", 0.5)),
        timeout=float(config.get("timeout", default_timeout)) if config.get("timeout") is not None else default_timeout,
        retryable_exceptions=retryable,
        name=name,
    )


def circuit_breaker_from_config(
    config: dict[str, Any],
    name: str = "circuit",
) -> CircuitBreaker:
    """Build a ``CircuitBreaker`` from a manifest ``resilience`` config block.

    Args:
        config: Dict of resilience settings.
        name: Logical name for the circuit breaker.
    """
    return CircuitBreaker(
        failure_threshold=int(config.get("circuit_failure_threshold", 5)),
        recovery_window=float(config.get("circuit_recovery_window", 30.0)),
        name=name,
    )
