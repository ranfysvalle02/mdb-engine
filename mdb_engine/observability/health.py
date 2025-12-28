"""
Health check utilities for MDB_ENGINE.

Provides health check functions for monitoring system status.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from pymongo.errors import (ConnectionFailure, OperationFailure,
                            ServerSelectionTimeoutError)

logger = logging.getLogger(__name__)


class HealthStatus(str, Enum):
    """Health status enumeration."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class HealthCheckResult:
    """Result of a health check."""

    name: str
    status: HealthStatus
    message: str
    details: Optional[Dict[str, Any]] = None
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "status": self.status.value,
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp.isoformat(),
        }


class HealthChecker:
    """
    Health checker for MDB_ENGINE components.
    """

    def __init__(self):
        """Initialize the health checker."""
        self._checks: List[callable] = []

    def register_check(self, check_func: Callable) -> None:
        """
        Register a health check function.

        Args:
            check_func: Async function that returns HealthCheckResult
        """
        self._checks.append(check_func)

    async def check_all(self) -> Dict[str, Any]:
        """
        Run all registered health checks.

        Returns:
            Dictionary with overall status and individual check results
        """
        results: List[HealthCheckResult] = []

        for check_func in self._checks:
            try:
                result = await check_func()
                results.append(result)
            except (
                RuntimeError,
                ValueError,
                TypeError,
                AttributeError,
                ConnectionError,
                OSError,
            ) as e:
                logger.error(
                    f"Health check {check_func.__name__} failed: {e}", exc_info=True
                )
                results.append(
                    HealthCheckResult(
                        name=check_func.__name__,
                        status=HealthStatus.UNKNOWN,
                        message=f"Check failed: {str(e)}",
                    )
                )

        # Determine overall status
        statuses = [r.status for r in results]
        if HealthStatus.UNHEALTHY in statuses:
            overall_status = HealthStatus.UNHEALTHY
        elif HealthStatus.DEGRADED in statuses:
            overall_status = HealthStatus.DEGRADED
        elif all(s == HealthStatus.HEALTHY for s in statuses):
            overall_status = HealthStatus.HEALTHY
        else:
            overall_status = HealthStatus.UNKNOWN

        return {
            "status": overall_status.value,
            "timestamp": datetime.now().isoformat(),
            "checks": [r.to_dict() for r in results],
        }


async def check_mongodb_health(
    mongo_client: Optional[Any], timeout_seconds: float = 5.0
) -> HealthCheckResult:
    """
    Check MongoDB connection health.

    Args:
        mongo_client: MongoDB client instance
        timeout_seconds: Timeout for health check

    Returns:
        HealthCheckResult
    """
    if mongo_client is None:
        return HealthCheckResult(
            name="mongodb",
            status=HealthStatus.UNHEALTHY,
            message="MongoDB client not initialized",
        )

    try:
        # Try to ping MongoDB with timeout
        await asyncio.wait_for(
            mongo_client.admin.command("ping"), timeout=timeout_seconds
        )

        return HealthCheckResult(
            name="mongodb",
            status=HealthStatus.HEALTHY,
            message="MongoDB connection is healthy",
            details={"timeout_seconds": timeout_seconds},
        )
    except asyncio.TimeoutError:
        return HealthCheckResult(
            name="mongodb",
            status=HealthStatus.UNHEALTHY,
            message=f"MongoDB ping timed out after {timeout_seconds}s",
        )
    except (
        ConnectionFailure,
        OperationFailure,
        ServerSelectionTimeoutError,
        AttributeError,
        TypeError,
    ) as e:
        return HealthCheckResult(
            name="mongodb",
            status=HealthStatus.UNHEALTHY,
            message=f"MongoDB health check failed: {str(e)}",
        )


async def check_engine_health(engine: Optional[Any]) -> HealthCheckResult:
    """
    Check MongoDB Engine health.

    Args:
        engine: MongoDBEngine instance

    Returns:
        HealthCheckResult
    """
    if engine is None:
        return HealthCheckResult(
            name="engine",
            status=HealthStatus.UNHEALTHY,
            message="MongoDBEngine not initialized",
        )

    if not engine._initialized:
        return HealthCheckResult(
            name="engine",
            status=HealthStatus.UNHEALTHY,
            message="MongoDBEngine not initialized",
        )

    # Check registered apps
    app_count = len(engine._apps)

    return HealthCheckResult(
        name="engine",
        status=HealthStatus.HEALTHY,
        message="MongoDBEngine is healthy",
        details={
            "initialized": True,
            "app_count": app_count,
        },
    )


async def check_pool_health(
    get_pool_metrics_func: Optional[Callable[[], Any]] = None
) -> HealthCheckResult:
    """
    Check connection pool health.

    Args:
        get_pool_metrics_func: Function to get pool metrics

    Returns:
        HealthCheckResult
    """
    if get_pool_metrics_func is None:
        return HealthCheckResult(
            name="connection_pool",
            status=HealthStatus.UNKNOWN,
            message="Pool metrics function not available",
        )

    try:
        # Call the function to get metrics (handles both sync and async)
        if asyncio.iscoroutinefunction(get_pool_metrics_func):
            metrics = await get_pool_metrics_func()
        else:
            metrics = get_pool_metrics_func()

        status_value = metrics.get("status")
        if status_value != "connected":
            # "no_client" means shared client not initialized, but engine might use its own client
            # This is not a critical failure, just means pool metrics aren't available
            if status_value == "no_client":
                return HealthCheckResult(
                    name="connection_pool",
                    status=HealthStatus.UNKNOWN,
                    message="Pool metrics not available (engine using dedicated client)",
                    details=metrics,
                )
            # "error" means we couldn't get metrics, but the client might still be working
            # This is not a critical failure - the engine and MongoDB checks already
            # verify connectivity
            if status_value == "error":
                return HealthCheckResult(
                    name="connection_pool",
                    status=HealthStatus.UNKNOWN,
                    message=f"Pool metrics unavailable: {metrics.get('error', 'Unknown error')}",
                    details=metrics,
                )
            # Other non-connected statuses are still unhealthy
            return HealthCheckResult(
                name="connection_pool",
                status=HealthStatus.UNHEALTHY,
                message=f"Pool status: {status_value}",
                details=metrics,
            )

        # Client is connected - check usage if available
        usage_percent = metrics.get("pool_usage_percent")

        # If we have usage data, check it
        if usage_percent is not None:
            if usage_percent > 90:
                status = HealthStatus.UNHEALTHY
                message = f"Connection pool usage is critical: {usage_percent:.1f}%"
            elif usage_percent > 80:
                status = HealthStatus.DEGRADED
                message = f"Connection pool usage is high: {usage_percent:.1f}%"
            else:
                status = HealthStatus.HEALTHY
                message = f"Connection pool is healthy: {usage_percent:.1f}% usage"
        else:
            # No usage data available, but client is connected
            # This is fine - detailed metrics might not be available but client works
            status = HealthStatus.HEALTHY
            message = metrics.get(
                "note", "Connection pool is operational (detailed metrics unavailable)"
            )

        return HealthCheckResult(
            name="connection_pool",
            status=status,
            message=message,
            details=metrics,
        )
    except (AttributeError, TypeError, ValueError, KeyError, RuntimeError) as e:
        return HealthCheckResult(
            name="connection_pool",
            status=HealthStatus.UNKNOWN,
            message=f"Failed to check pool health: {str(e)}",
        )
