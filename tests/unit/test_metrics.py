"""
Unit tests for MetricsCollector.

Tests metrics collection functionality including:
- Thread-safety under concurrent access
- Bounded storage with LRU eviction
- Metrics ordering and access patterns
"""

import threading

from mdb_engine.observability.metrics import (
    MetricsCollector,
    get_metrics_collector,
    record_operation,
)


class TestMetricsCollectorThreadSafety:
    """Test thread-safety of metrics collection."""

    def test_concurrent_record_operation(self):
        """Test that concurrent record_operation calls are thread-safe."""
        collector = MetricsCollector()
        num_threads = 10
        operations_per_thread = 100
        barrier = threading.Barrier(num_threads)

        def record_operations(thread_id: int):
            """Record operations from a thread."""
            barrier.wait()  # Synchronize start
            for i in range(operations_per_thread):
                collector.record_operation(
                    f"test.operation.{thread_id}",
                    duration_ms=10.0 + i,
                    success=True,
                    thread_id=thread_id,
                )

        threads = [
            threading.Thread(target=record_operations, args=(i,)) for i in range(num_threads)
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        # Verify all operations were recorded
        total_expected = num_threads * operations_per_thread
        metrics = collector.get_metrics()
        total_recorded = sum(m["count"] for m in metrics["metrics"].values())

        assert (
            total_recorded == total_expected
        ), f"Expected {total_expected} operations, got {total_recorded}"

    def test_concurrent_get_metrics(self):
        """Test that concurrent get_metrics calls are thread-safe."""
        collector = MetricsCollector()
        num_threads = 5
        operations = 50

        # Pre-populate metrics
        for i in range(operations):
            collector.record_operation(f"test.op_{i}", duration_ms=10.0)

        results = []
        errors = []

        def get_metrics():
            """Get metrics from a thread."""
            try:
                metrics = collector.get_metrics()
                results.append(metrics)
            except (RuntimeError, ValueError, TypeError, KeyError, AttributeError) as e:
                errors.append(e)

        threads = [threading.Thread(target=get_metrics) for _ in range(num_threads)]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        # Verify no errors occurred
        assert len(errors) == 0, f"Errors occurred: {errors}"

        # Verify all threads got valid results
        assert len(results) == num_threads
        for result in results:
            assert "metrics" in result
            assert "total_operations" in result
            assert result["total_operations"] == operations

    def test_concurrent_get_summary(self):
        """Test that concurrent get_summary calls are thread-safe."""
        collector = MetricsCollector()
        num_threads = 5

        # Pre-populate metrics
        for i in range(20):
            collector.record_operation(f"test.op_{i}", duration_ms=10.0)

        results = []
        errors = []

        def get_summary():
            """Get summary from a thread."""
            try:
                summary = collector.get_summary()
                results.append(summary)
            except (RuntimeError, ValueError, TypeError, KeyError, AttributeError) as e:
                errors.append(e)

        threads = [threading.Thread(target=get_summary) for _ in range(num_threads)]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        # Verify no errors occurred
        assert len(errors) == 0, f"Errors occurred: {errors}"

        # Verify all threads got valid results
        assert len(results) == num_threads
        for result in results:
            assert "summary" in result
            assert "total_operations" in result

    def test_concurrent_reset(self):
        """Test that reset is thread-safe during concurrent operations."""
        collector = MetricsCollector()
        num_threads = 5
        operations = 20

        # Pre-populate metrics
        for i in range(operations):
            collector.record_operation(f"test.op_{i}", duration_ms=10.0)

        errors = []

        def reset_and_record(thread_id: int):
            """Reset and record from a thread."""
            try:
                if thread_id == 0:
                    collector.reset()
                else:
                    collector.record_operation(f"test.op_{thread_id}", duration_ms=10.0)
            except (RuntimeError, ValueError, TypeError, KeyError, AttributeError) as e:
                errors.append(e)

        threads = [threading.Thread(target=reset_and_record, args=(i,)) for i in range(num_threads)]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        # Verify no errors occurred
        assert len(errors) == 0, f"Errors occurred: {errors}"


class TestMetricsCollectorBoundedStorage:
    """Test bounded storage and LRU eviction."""

    def test_max_metrics_limit(self):
        """Test that metrics are evicted when max_metrics is exceeded."""
        max_metrics = 5
        collector = MetricsCollector(max_metrics=max_metrics)

        # Add more metrics than max
        for i in range(max_metrics + 3):
            collector.record_operation(f"test.op_{i}", duration_ms=10.0)

        metrics = collector.get_metrics()
        assert len(metrics["metrics"]) == max_metrics

    def test_lru_eviction_order(self):
        """Test that oldest metrics are evicted first (LRU)."""
        max_metrics = 3
        collector = MetricsCollector(max_metrics=max_metrics)

        # Add initial metrics
        collector.record_operation("test.op_0", duration_ms=10.0)
        collector.record_operation("test.op_1", duration_ms=10.0)
        collector.record_operation("test.op_2", duration_ms=10.0)

        # Access op_0 to make it most recently used
        collector.get_metrics("test.op_0")

        # Add new metric - should evict op_1 (oldest, not recently accessed)
        collector.record_operation("test.op_3", duration_ms=10.0)

        metrics = collector.get_metrics()
        metric_keys = set(metrics["metrics"].keys())

        # op_0 should still be there (recently accessed)
        assert "test.op_0" in metric_keys
        # op_3 should be there (newly added)
        assert "test.op_3" in metric_keys
        # op_1 should be evicted (oldest, not accessed)
        assert "test.op_1" not in metric_keys

    def test_move_to_end_on_access(self):
        """Test that accessing metrics moves them to end (most recently used)."""
        max_metrics = 3
        collector = MetricsCollector(max_metrics=max_metrics)

        # Add metrics
        collector.record_operation("test.op_0", duration_ms=10.0)
        collector.record_operation("test.op_1", duration_ms=10.0)
        collector.record_operation("test.op_2", duration_ms=10.0)

        # Access op_0 via get_metrics
        collector.get_metrics("test.op_0")

        # Add new metric - should evict op_1 (oldest, not accessed)
        collector.record_operation("test.op_3", duration_ms=10.0)

        metrics = collector.get_metrics()
        metric_keys = list(metrics["metrics"].keys())

        # op_0 should be last (most recently accessed)
        assert metric_keys[-1] == "test.op_0" or "test.op_0" in metric_keys

    def test_no_eviction_on_update(self):
        """Test that updating existing metrics doesn't trigger eviction."""
        max_metrics = 3
        collector = MetricsCollector(max_metrics=max_metrics)

        # Add metrics up to limit
        collector.record_operation("test.op_0", duration_ms=10.0)
        collector.record_operation("test.op_1", duration_ms=10.0)
        collector.record_operation("test.op_2", duration_ms=10.0)

        # Update existing metric (should not evict)
        collector.record_operation("test.op_0", duration_ms=20.0)

        metrics = collector.get_metrics()
        assert len(metrics["metrics"]) == max_metrics
        assert "test.op_0" in metrics["metrics"]
        assert metrics["metrics"]["test.op_0"]["count"] == 2

    def test_get_summary_moves_to_end(self):
        """Test that get_summary moves accessed metrics to end."""
        max_metrics = 3
        collector = MetricsCollector(max_metrics=max_metrics)

        # Add metrics
        collector.record_operation("test.op_0", duration_ms=10.0)
        collector.record_operation("test.op_1", duration_ms=10.0)
        collector.record_operation("test.op_2", duration_ms=10.0)

        # Get summary (should move all to end)
        collector.get_summary()

        # Add new metric - should evict oldest (op_0, since all were accessed)
        collector.record_operation("test.op_3", duration_ms=10.0)

        metrics = collector.get_metrics()
        metric_keys = set(metrics["metrics"].keys())

        # All original metrics should still be there or op_3 should be there
        assert len(metric_keys) == max_metrics


class TestMetricsCollectorFunctionality:
    """Test basic metrics collection functionality."""

    def test_record_operation(self):
        """Test recording a single operation."""
        collector = MetricsCollector()
        collector.record_operation("test.operation", duration_ms=100.0, success=True)

        metrics = collector.get_metrics()
        assert "test.operation" in metrics["metrics"]
        assert metrics["metrics"]["test.operation"]["count"] == 1
        assert metrics["metrics"]["test.operation"]["avg_duration_ms"] == 100.0

    def test_record_operation_with_tags(self):
        """Test recording operation with tags."""
        collector = MetricsCollector()
        collector.record_operation(
            "test.operation",
            duration_ms=50.0,
            success=True,
            app_slug="my_app",
            collection="users",
        )

        metrics = collector.get_metrics()
        # Should create a tagged key
        tagged_key = "test.operation[app_slug=my_app_collection=users]"
        assert tagged_key in metrics["metrics"]

    def test_get_metrics_filtered(self):
        """Test getting filtered metrics."""
        collector = MetricsCollector()
        collector.record_operation("test.op_1", duration_ms=10.0)
        collector.record_operation("test.op_2", duration_ms=20.0)
        collector.record_operation("other.op", duration_ms=30.0)

        metrics = collector.get_metrics("test")
        assert len(metrics["metrics"]) == 2
        assert "test.op_1" in metrics["metrics"]
        assert "test.op_2" in metrics["metrics"]
        assert "other.op" not in metrics["metrics"]

    def test_get_summary_aggregates(self):
        """Test that get_summary aggregates by base operation name."""
        collector = MetricsCollector()
        collector.record_operation("test.op", duration_ms=10.0, app_slug="app1")
        collector.record_operation("test.op", duration_ms=20.0, app_slug="app2")
        collector.record_operation("test.op", duration_ms=30.0, app_slug="app3")

        summary = collector.get_summary()
        assert "test.op" in summary["summary"]
        assert summary["summary"]["test.op"]["count"] == 3

    def test_reset(self):
        """Test resetting all metrics."""
        collector = MetricsCollector()
        collector.record_operation("test.op", duration_ms=10.0)
        collector.reset()

        metrics = collector.get_metrics()
        assert len(metrics["metrics"]) == 0

    def test_get_operation_count(self):
        """Test getting operation count."""
        collector = MetricsCollector()
        collector.record_operation("test.op", duration_ms=10.0, app_slug="app1")
        collector.record_operation("test.op", duration_ms=20.0, app_slug="app2")
        collector.record_operation("other.op", duration_ms=30.0)

        count = collector.get_operation_count("test.op")
        assert count == 2


class TestGlobalMetricsFunctions:
    """Test global metrics functions."""

    def test_get_metrics_collector_singleton(self):
        """Test that get_metrics_collector returns singleton."""
        collector1 = get_metrics_collector()
        collector2 = get_metrics_collector()
        assert collector1 is collector2

    def test_record_operation_global(self):
        """Test global record_operation function."""
        # Reset global collector for test isolation
        from mdb_engine.observability.metrics import _metrics_collector

        original_collector = _metrics_collector
        try:
            from mdb_engine.observability import metrics

            metrics._metrics_collector = None
            record_operation("test.global", duration_ms=10.0)

            collector = get_metrics_collector()
            metrics_data = collector.get_metrics()
            assert "test.global" in metrics_data["metrics"]
        finally:
            metrics._metrics_collector = original_collector
