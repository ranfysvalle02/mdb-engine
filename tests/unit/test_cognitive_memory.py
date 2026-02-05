"""
Tests for CognitiveMemoryService and CognitiveMath.

Tests:
- CognitiveMath datetime handling (timezone-aware vs naive)
- Entity store initialization (enabled by default)
"""

from datetime import datetime, timedelta, timezone

from mdb_engine.memory.cognitive import CognitiveMath


class TestCognitiveMath:
    """Test CognitiveMath calculations."""

    def test_get_current_strength_with_timezone_aware_datetime(self):
        """Test strength calculation with timezone-aware datetime."""
        # Create a document with a timezone-aware datetime
        doc = {
            "importance": 0.8,
            "stability": 24.0,  # 24 hour half-life
            "last_accessed": datetime.now(timezone.utc) - timedelta(hours=24),
        }

        strength = CognitiveMath.get_current_strength(doc)

        # After 24 hours with 24h stability, strength should be around 0.8 * exp(-1) ≈ 0.29
        assert 0.2 < strength < 0.4

    def test_get_current_strength_with_timezone_naive_datetime(self):
        """Test strength calculation with timezone-naive datetime (from MongoDB)."""
        # MongoDB stores datetimes as naive UTC
        naive_dt = datetime.utcnow() - timedelta(hours=24)

        doc = {
            "importance": 0.8,
            "stability": 24.0,
            "last_accessed": naive_dt,  # Naive datetime
        }

        # This should NOT raise "can't subtract offset-naive and offset-aware datetimes"
        strength = CognitiveMath.get_current_strength(doc)

        # After 24 hours with 24h stability, strength should be around 0.8 * exp(-1) ≈ 0.29
        assert 0.2 < strength < 0.4

    def test_get_current_strength_with_iso_string(self):
        """Test strength calculation with ISO format string."""
        past_time = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()

        doc = {
            "importance": 0.8,
            "stability": 24.0,
            "last_accessed": past_time,
        }

        strength = CognitiveMath.get_current_strength(doc)
        assert 0.2 < strength < 0.4

    def test_get_current_strength_with_iso_string_z_suffix(self):
        """Test strength calculation with ISO format string with Z suffix."""
        past_time = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime(
            "%Y-%m-%dT%H:%M:%S.%f"
        ) + "Z"

        doc = {
            "importance": 0.8,
            "stability": 24.0,
            "last_accessed": past_time,
        }

        strength = CognitiveMath.get_current_strength(doc)
        assert 0.2 < strength < 0.4

    def test_get_current_strength_no_last_accessed(self):
        """Test strength calculation when last_accessed is None."""
        doc = {
            "importance": 0.8,
            "stability": 24.0,
            "last_accessed": None,
        }

        strength = CognitiveMath.get_current_strength(doc)
        # Should return full importance when no last_accessed
        assert strength == 0.8

    def test_get_current_strength_fresh_memory(self):
        """Test strength calculation for a very recent memory."""
        doc = {
            "importance": 0.9,
            "stability": 24.0,
            "last_accessed": datetime.now(timezone.utc) - timedelta(seconds=10),
        }

        strength = CognitiveMath.get_current_strength(doc)
        # Fresh memory should have strength close to importance
        assert 0.85 < strength <= 0.9

    def test_get_current_strength_default_values(self):
        """Test strength calculation with default/missing values."""
        doc = {}  # Empty document

        strength = CognitiveMath.get_current_strength(doc)
        # Should use defaults: importance=0.5, stability=24, no last_accessed
        assert strength == 0.5
