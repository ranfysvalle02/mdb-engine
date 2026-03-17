"""Tests for manifest-driven scheduled jobs."""

from __future__ import annotations

from datetime import datetime, timezone

from mdb_engine.jobs.scheduler import (
    _compute_time_offset,
    _resolve_time_vars,
    parse_cron_to_seconds,
)


class TestParseCronToSeconds:
    def test_hourly_shortcut(self):
        assert parse_cron_to_seconds("@hourly") == 3600

    def test_daily_shortcut(self):
        assert parse_cron_to_seconds("@daily") == 86400

    def test_weekly_shortcut(self):
        assert parse_cron_to_seconds("@weekly") == 604800

    def test_interval_minutes(self):
        assert parse_cron_to_seconds("30m") == 1800

    def test_interval_hours(self):
        assert parse_cron_to_seconds("6h") == 21600

    def test_interval_days(self):
        assert parse_cron_to_seconds("1d") == 86400

    def test_cron_specific_hour(self):
        assert parse_cron_to_seconds("0 3 * * *") == 86400

    def test_cron_every_hour(self):
        assert parse_cron_to_seconds("30 * * * *") == 3600

    def test_cron_wildcard_defaults_daily(self):
        assert parse_cron_to_seconds("* * * * *") == 86400


class TestResolveTimeVars:
    def test_now(self):
        now = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        result = _resolve_time_vars("$$NOW", now)
        assert result == now

    def test_now_minus_90d(self):
        now = datetime(2024, 4, 15, 12, 0, 0, tzinfo=timezone.utc)
        result = _resolve_time_vars("$$NOW_MINUS_90D", now)
        assert result.year == 2024
        assert result.month == 1

    def test_nested_dict(self):
        now = datetime(2024, 1, 15, tzinfo=timezone.utc)
        obj = {"updated_at": {"$lt": "$$NOW_MINUS_30D"}}
        result = _resolve_time_vars(obj, now)
        assert isinstance(result["updated_at"]["$lt"], datetime)

    def test_plain_string_unchanged(self):
        now = datetime(2024, 1, 1, tzinfo=timezone.utc)
        assert _resolve_time_vars("hello", now) == "hello"

    def test_list(self):
        now = datetime(2024, 1, 1, tzinfo=timezone.utc)
        result = _resolve_time_vars(["$$NOW", "plain"], now)
        assert result[0] == now
        assert result[1] == "plain"


class TestComputeTimeOffset:
    def test_days(self):
        now = datetime(2024, 4, 15, 12, 0, 0, tzinfo=timezone.utc)
        result = _compute_time_offset("$$NOW_MINUS_90D", now)
        delta = now - result
        assert delta.days == 90

    def test_hours(self):
        now = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        result = _compute_time_offset("$$NOW_MINUS_24H", now)
        delta = now - result
        assert delta.total_seconds() == 86400

    def test_minutes(self):
        now = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        result = _compute_time_offset("$$NOW_MINUS_30M", now)
        delta = now - result
        assert delta.total_seconds() == 1800
