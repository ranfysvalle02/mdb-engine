"""
Performance tests for ScopedMongoWrapper filter injection and query validation.

Ensures that the scoping and security overhead added by ScopedCollectionWrapper
does not introduce unacceptable latency on hot-path operations.
"""

import time

import pytest

from mdb_engine.database.query_validator import QueryValidator


class TestFilterInjectionPerformance:
    """Benchmark the overhead of _inject_read_filter-style scoping."""

    @staticmethod
    def _inject_read_filter(
        read_scopes: list[str],
        user_filter: dict | None = None,
    ) -> dict:
        """Replicates ScopedCollectionWrapper._inject_read_filter logic."""
        scope_filter = {"app_id": {"$in": read_scopes}}
        if not user_filter:
            return scope_filter
        return {"$and": [user_filter, scope_filter]}

    def test_empty_filter_injection_performance(self):
        """Empty filter injection should be < 0.01ms per call."""
        scopes = ["app_1"]
        iterations = 10_000

        start = time.perf_counter()
        for _ in range(iterations):
            self._inject_read_filter(scopes, None)
        elapsed_ms = (time.perf_counter() - start) * 1000

        avg_us = (elapsed_ms / iterations) * 1000
        assert avg_us < 10, f"Empty filter injection avg {avg_us:.2f}µs, target < 10µs"

    def test_simple_filter_injection_performance(self):
        """Simple filter injection should be < 0.01ms per call."""
        scopes = ["app_1"]
        user_filter = {"status": "active", "priority": {"$gte": 5}}
        iterations = 10_000

        start = time.perf_counter()
        for _ in range(iterations):
            self._inject_read_filter(scopes, user_filter)
        elapsed_ms = (time.perf_counter() - start) * 1000

        avg_us = (elapsed_ms / iterations) * 1000
        assert avg_us < 10, f"Simple filter injection avg {avg_us:.2f}µs, target < 10µs"

    def test_multi_scope_filter_injection_performance(self):
        """Multi-scope filter injection (cross-app reads) should be < 0.01ms."""
        scopes = [f"app_{i}" for i in range(20)]
        user_filter = {"type": "document"}
        iterations = 10_000

        start = time.perf_counter()
        for _ in range(iterations):
            self._inject_read_filter(scopes, user_filter)
        elapsed_ms = (time.perf_counter() - start) * 1000

        avg_us = (elapsed_ms / iterations) * 1000
        assert avg_us < 10, f"Multi-scope filter injection avg {avg_us:.2f}µs, target < 10µs"

    def test_complex_filter_injection_performance(self):
        """Complex nested filter injection should be < 0.05ms."""
        scopes = ["app_1", "app_2"]
        user_filter = {
            "$or": [
                {"status": "active", "tags": {"$in": ["urgent", "critical"]}},
                {"priority": {"$gte": 8}, "assigned_to": {"$exists": True}},
            ],
            "created_at": {"$gte": "2024-01-01", "$lte": "2024-12-31"},
        }
        iterations = 10_000

        start = time.perf_counter()
        for _ in range(iterations):
            self._inject_read_filter(scopes, user_filter)
        elapsed_ms = (time.perf_counter() - start) * 1000

        avg_us = (elapsed_ms / iterations) * 1000
        assert avg_us < 50, f"Complex filter injection avg {avg_us:.2f}µs, target < 50µs"

    def test_write_scope_injection_performance(self):
        """Document write-scope injection (dict spread) should be < 0.01ms."""
        write_scope = "app_1"
        document = {
            "title": "Test Document",
            "content": "Lorem ipsum dolor sit amet",
            "tags": ["test", "performance"],
            "metadata": {"source": "benchmark", "version": 1},
        }
        iterations = 10_000

        start = time.perf_counter()
        for _ in range(iterations):
            _ = {**document, "app_id": write_scope}
        elapsed_ms = (time.perf_counter() - start) * 1000

        avg_us = (elapsed_ms / iterations) * 1000
        assert avg_us < 10, f"Write scope injection avg {avg_us:.2f}µs, target < 10µs"


class TestQueryValidatorPerformance:
    """Benchmark query validation overhead."""

    @pytest.fixture
    def validator(self):
        return QueryValidator()

    def test_simple_filter_validation_performance(self, validator):
        """Simple filter validation should be < 0.1ms per call."""
        simple_filter = {"status": "active", "user_id": "user_123"}
        iterations = 5_000

        start = time.perf_counter()
        for _ in range(iterations):
            validator.validate_filter(simple_filter)
        elapsed_ms = (time.perf_counter() - start) * 1000

        avg_us = (elapsed_ms / iterations) * 1000
        assert avg_us < 100, f"Simple validation avg {avg_us:.2f}µs, target < 100µs"

    def test_nested_filter_validation_performance(self, validator):
        """Nested filter validation should be < 0.5ms per call."""
        nested_filter = {
            "$and": [
                {"status": {"$in": ["active", "pending"]}},
                {
                    "$or": [
                        {"priority": {"$gte": 5}},
                        {"tags": {"$all": ["urgent"]}},
                    ]
                },
                {"created_at": {"$gte": "2024-01-01"}},
            ]
        }
        iterations = 5_000

        start = time.perf_counter()
        for _ in range(iterations):
            validator.validate_filter(nested_filter)
        elapsed_ms = (time.perf_counter() - start) * 1000

        avg_us = (elapsed_ms / iterations) * 1000
        assert avg_us < 500, f"Nested validation avg {avg_us:.2f}µs, target < 500µs"

    def test_aggregation_pipeline_validation_performance(self, validator):
        """Pipeline validation should be < 1ms per call."""
        pipeline = [
            {"$match": {"status": "active"}},
            {"$group": {"_id": "$category", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 10},
        ]
        iterations = 2_000

        start = time.perf_counter()
        for _ in range(iterations):
            validator.validate_pipeline(pipeline)
        elapsed_ms = (time.perf_counter() - start) * 1000

        avg_us = (elapsed_ms / iterations) * 1000
        assert avg_us < 1000, f"Pipeline validation avg {avg_us:.2f}µs, target < 1000µs"

    def test_batch_filter_validation_performance(self, validator):
        """Batch of 100 filter validations should complete in < 50ms."""
        filters = [{"field_" + str(i): {"$eq": f"value_{i}"}} for i in range(100)]

        start = time.perf_counter()
        for f in filters:
            validator.validate_filter(f)
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < 50, f"Batch validation took {elapsed_ms:.2f}ms, target < 50ms"
