"""
Health check utilities for MDB_RUNTIME.

Provides health check functions for monitoring system status.
"""
import asyncio
import logging
from typing import Dict, Any, Optional, List, Callable
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum

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
    Health checker for MDB_RUNTIME components.
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
            except Exception as e:
                logger.error(f"Health check {check_func.__name__} failed: {e}", exc_info=True)
                results.append(HealthCheckResult(
                    name=check_func.__name__,
                    status=HealthStatus.UNKNOWN,
                    message=f"Check failed: {str(e)}",
                ))
        
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
    mongo_client: Optional[Any],
    timeout_seconds: float = 5.0
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
            mongo_client.admin.command("ping"),
            timeout=timeout_seconds
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
    except Exception as e:
        return HealthCheckResult(
            name="mongodb",
            status=HealthStatus.UNHEALTHY,
            message=f"MongoDB health check failed: {str(e)}",
        )


async def check_engine_health(engine: Optional[Any]) -> HealthCheckResult:
    """
    Check runtime engine health.
    
    Args:
        engine: RuntimeEngine instance
        
    Returns:
        HealthCheckResult
    """
    if engine is None:
        return HealthCheckResult(
            name="engine",
            status=HealthStatus.UNHEALTHY,
            message="RuntimeEngine not initialized",
        )
    
    if not engine._initialized:
        return HealthCheckResult(
            name="engine",
            status=HealthStatus.UNHEALTHY,
            message="RuntimeEngine not initialized",
        )
    
    # Check registered apps
    app_count = len(engine._apps)
    
    return HealthCheckResult(
        name="engine",
        status=HealthStatus.HEALTHY,
        message="RuntimeEngine is healthy",
        details={
            "initialized": True,
            "app_count": app_count,
        },
    )


async def check_pool_health(
    get_pool_metrics_func: Optional[Callable] = None
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
        metrics = await get_pool_metrics_func()
        
        if metrics.get("status") != "connected":
            return HealthCheckResult(
                name="connection_pool",
                status=HealthStatus.UNHEALTHY,
                message=f"Pool status: {metrics.get('status')}",
                details=metrics,
            )
        
        usage_percent = metrics.get("pool_usage_percent", 0)
        
        if usage_percent > 90:
            status = HealthStatus.UNHEALTHY
            message = f"Connection pool usage is critical: {usage_percent:.1f}%"
        elif usage_percent > 80:
            status = HealthStatus.DEGRADED
            message = f"Connection pool usage is high: {usage_percent:.1f}%"
        else:
            status = HealthStatus.HEALTHY
            message = f"Connection pool is healthy: {usage_percent:.1f}% usage"
        
        return HealthCheckResult(
            name="connection_pool",
            status=status,
            message=message,
            details=metrics,
        )
    except Exception as e:
        return HealthCheckResult(
            name="connection_pool",
            status=HealthStatus.UNKNOWN,
            message=f"Failed to check pool health: {str(e)}",
        )

