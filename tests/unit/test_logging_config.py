"""Tests for mdb_engine.observability.logging — auto-configured logging."""

import json
import logging


class TestConfigureLogging:
    """Tests for configure_logging()."""

    def setup_method(self):
        import mdb_engine.observability.logging as log_mod

        log_mod._logging_configured = False

    def test_idempotent(self):
        from mdb_engine.observability.logging import configure_logging

        configure_logging()
        handler_count = len(logging.getLogger().handlers)
        configure_logging()
        assert len(logging.getLogger().handlers) == handler_count

    def test_human_formatter_in_dev(self, monkeypatch):
        from mdb_engine.observability.logging import _HumanFormatter, configure_logging

        monkeypatch.setenv("ENVIRONMENT", "development")
        import mdb_engine.observability.logging as log_mod

        log_mod._logging_configured = False

        configure_logging()
        root = logging.getLogger()
        assert any(isinstance(h.formatter, _HumanFormatter) for h in root.handlers)

    def test_json_formatter_in_production(self, monkeypatch):
        from mdb_engine.observability.logging import _JSONFormatter, configure_logging

        monkeypatch.setenv("ENVIRONMENT", "production")
        import mdb_engine.observability.logging as log_mod

        log_mod._logging_configured = False

        configure_logging()
        root = logging.getLogger()
        assert any(isinstance(h.formatter, _JSONFormatter) for h in root.handlers)

    def test_explicit_json_output(self):
        from mdb_engine.observability.logging import _JSONFormatter, configure_logging

        configure_logging(json_output=True)
        root = logging.getLogger()
        assert any(isinstance(h.formatter, _JSONFormatter) for h in root.handlers)


class TestJSONFormatter:
    """Tests for _JSONFormatter output."""

    def test_output_is_valid_json(self):
        from mdb_engine.observability.logging import _JSONFormatter

        formatter = _JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello world",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["message"] == "hello world"
        assert parsed["level"] == "INFO"
        assert "timestamp" in parsed


class TestGetAppLogger:
    """Tests for get_app_logger."""

    def test_returns_contextual_adapter(self):
        from mdb_engine.observability.logging import ContextualLoggerAdapter, get_app_logger

        logger = get_app_logger("my-app")
        assert isinstance(logger, ContextualLoggerAdapter)
