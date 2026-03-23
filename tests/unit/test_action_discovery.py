"""Tests for mdb_engine.actions.discovery — action discovery and mounting."""

import textwrap
from pathlib import Path
from unittest.mock import MagicMock


class TestDiscoverActions:
    """Tests for discover_actions file scanning."""

    def _write_action(self, tmp_path: Path, name: str, content: str) -> Path:
        actions_dir = tmp_path / "actions"
        actions_dir.mkdir(exist_ok=True)
        f = actions_dir / f"{name}.py"
        f.write_text(textwrap.dedent(content))
        return actions_dir

    def test_discovers_handler(self, tmp_path):
        actions_dir = self._write_action(
            tmp_path,
            "greet",
            """\
            async def handler(ctx):
                return {"hello": "world"}
        """,
        )

        from mdb_engine.actions.discovery import discover_actions

        defs = discover_actions(actions_dir, slug="test")
        assert len(defs) == 1
        assert defs[0].name == "greet"
        assert defs[0].trigger == "http"
        assert defs[0].method == "POST"

    def test_skips_underscore_prefixed(self, tmp_path):
        actions_dir = self._write_action(
            tmp_path,
            "_internal",
            """\
            async def handler(ctx):
                pass
        """,
        )
        # Also add a valid one
        (actions_dir / "valid.py").write_text("async def handler(ctx): pass")

        from mdb_engine.actions.discovery import discover_actions

        defs = discover_actions(actions_dir, slug="test")
        assert len(defs) == 1
        assert defs[0].name == "valid"

    def test_skips_missing_handler(self, tmp_path):
        actions_dir = self._write_action(
            tmp_path,
            "no-handler",
            """\
            def helper():
                pass
        """,
        )

        from mdb_engine.actions.discovery import discover_actions

        defs = discover_actions(actions_dir, slug="test")
        assert len(defs) == 0

    def test_skips_sync_handler(self, tmp_path):
        actions_dir = self._write_action(
            tmp_path,
            "sync-bad",
            """\
            def handler(ctx):
                pass
        """,
        )

        from mdb_engine.actions.discovery import discover_actions

        defs = discover_actions(actions_dir, slug="test")
        assert len(defs) == 0

    def test_reads_module_metadata(self, tmp_path):
        actions_dir = self._write_action(
            tmp_path,
            "cleanup",
            """\
            __trigger__ = "schedule"
            __interval_seconds__ = 3600
            __timeout__ = 30

            async def handler(ctx):
                pass
        """,
        )

        from mdb_engine.actions.discovery import discover_actions

        defs = discover_actions(actions_dir, slug="test")
        assert len(defs) == 1
        assert defs[0].trigger == "schedule"
        assert defs[0].interval_seconds == 3600
        assert defs[0].timeout == 30

    def test_manifest_config_overrides_module(self, tmp_path):
        actions_dir = self._write_action(
            tmp_path,
            "notify",
            """\
            __trigger__ = "http"
            __method__ = "GET"

            async def handler(ctx):
                pass
        """,
        )

        config = {
            "notify": {
                "trigger": "http",
                "method": "POST",
                "auth": {"required": True, "roles": ["admin"]},
                "timeout": 60,
            }
        }

        from mdb_engine.actions.discovery import discover_actions

        defs = discover_actions(actions_dir, config, slug="test")
        assert defs[0].method == "POST"
        assert defs[0].auth_required is True
        assert defs[0].auth_roles == ["admin"]
        assert defs[0].timeout == 60

    def test_timeout_clamped(self, tmp_path):
        actions_dir = self._write_action(
            tmp_path,
            "slow",
            """\
            __timeout__ = 9999

            async def handler(ctx):
                pass
        """,
        )

        from mdb_engine.actions.discovery import discover_actions

        defs = discover_actions(actions_dir, slug="test")
        assert defs[0].timeout == 300  # MAX_TIMEOUT

    def test_skips_syntax_error(self, tmp_path):
        actions_dir = self._write_action(
            tmp_path,
            "broken",
            """\
            def this is broken syntax!!!
        """,
        )

        from mdb_engine.actions.discovery import discover_actions

        defs = discover_actions(actions_dir, slug="test")
        assert len(defs) == 0

    def test_multiple_files_sorted(self, tmp_path):
        actions_dir = tmp_path / "actions"
        actions_dir.mkdir()
        for name in ["zebra", "alpha", "mid"]:
            (actions_dir / f"{name}.py").write_text("async def handler(ctx): pass")

        from mdb_engine.actions.discovery import discover_actions

        defs = discover_actions(actions_dir, slug="test")
        names = [d.name for d in defs]
        assert names == ["alpha", "mid", "zebra"]


class TestMountHttpActions:
    """Tests for mount_http_actions route creation."""

    def test_mounts_http_action(self, tmp_path):
        actions_dir = tmp_path / "actions"
        actions_dir.mkdir()
        (actions_dir / "ping.py").write_text("async def handler(ctx): return {'pong': True}")

        from mdb_engine.actions.discovery import discover_actions, mount_http_actions

        defs = discover_actions(actions_dir, slug="test")

        app = MagicMock()
        engine = MagicMock()

        count = mount_http_actions(app, defs, engine, "test")
        assert count == 1
        app.add_api_route.assert_called_once()
        call_kwargs = app.add_api_route.call_args
        assert call_kwargs[0][0] == "/actions/v1/ping"
        assert call_kwargs[1]["methods"] == ["POST"]

    def test_skips_non_http_actions(self, tmp_path):
        actions_dir = tmp_path / "actions"
        actions_dir.mkdir()
        (actions_dir / "cleanup.py").write_text(
            '__trigger__ = "schedule"\n__interval_seconds__ = 60\nasync def handler(ctx): pass'
        )

        from mdb_engine.actions.discovery import discover_actions, mount_http_actions

        defs = discover_actions(actions_dir, slug="test")
        app = MagicMock()

        count = mount_http_actions(app, defs, MagicMock(), "test")
        assert count == 0
        app.add_api_route.assert_not_called()

    def test_auth_dependencies_added(self, tmp_path):
        actions_dir = tmp_path / "actions"
        actions_dir.mkdir()
        (actions_dir / "secure.py").write_text("async def handler(ctx): pass")

        config = {"secure": {"auth": {"required": True, "roles": ["admin"]}}}

        from mdb_engine.actions.discovery import discover_actions, mount_http_actions

        defs = discover_actions(actions_dir, config, slug="test")
        app = MagicMock()

        mount_http_actions(app, defs, MagicMock(), "test")
        call_kwargs = app.add_api_route.call_args[1]
        assert len(call_kwargs["dependencies"]) == 1


class TestEventActionRegistration:
    """Tests for event action registration and injection."""

    def setup_method(self):
        from mdb_engine.actions.discovery import _clear_registry

        _clear_registry()

    def test_register_event_actions(self, tmp_path):
        actions_dir = tmp_path / "actions"
        actions_dir.mkdir()
        (actions_dir / "on-signup.py").write_text(
            '__trigger__ = "event"\n__event__ = "after_create"\n'
            '__collection__ = "users"\nasync def handler(ctx): pass'
        )

        from mdb_engine.actions.discovery import discover_actions, get_registered_action, register_event_actions

        defs = discover_actions(actions_dir, slug="test")
        engine = MagicMock()
        count = register_event_actions(defs, engine, "test")

        assert count == 1
        adef, eng, slug = get_registered_action("on-signup")
        assert adef is not None
        assert adef.event == "after_create"
        assert adef.collection == "users"
        assert eng is engine
        assert slug == "test"

    def test_get_registered_action_not_found(self):
        from mdb_engine.actions.discovery import get_registered_action

        adef, eng, slug = get_registered_action("nonexistent")
        assert adef is None
        assert eng is None
        assert slug == ""

    def test_inject_event_actions_into_collections(self, tmp_path):
        actions_dir = tmp_path / "actions"
        actions_dir.mkdir()
        (actions_dir / "on-order.py").write_text(
            '__trigger__ = "event"\n__event__ = "after_create"\n'
            '__collection__ = "orders"\nasync def handler(ctx): pass'
        )

        from mdb_engine.actions.discovery import discover_actions, inject_event_actions_into_collections

        defs = discover_actions(actions_dir, slug="test")

        collections_config = {"orders": {"auto_crud": True}}
        inject_event_actions_into_collections(defs, collections_config)

        hooks = collections_config["orders"]["hooks"]
        assert "after_create" in hooks
        assert len(hooks["after_create"]) == 1
        assert hooks["after_create"][0]["action"] == "run_action"
        assert hooks["after_create"][0]["action_name"] == "on-order"

    def test_inject_creates_collection_if_missing(self, tmp_path):
        actions_dir = tmp_path / "actions"
        actions_dir.mkdir()
        (actions_dir / "on-payment.py").write_text(
            '__trigger__ = "event"\n__event__ = "after_update"\n'
            '__collection__ = "payments"\nasync def handler(ctx): pass'
        )

        from mdb_engine.actions.discovery import discover_actions, inject_event_actions_into_collections

        defs = discover_actions(actions_dir, slug="test")

        collections_config = {}
        inject_event_actions_into_collections(defs, collections_config)

        assert "payments" in collections_config
        hooks = collections_config["payments"]["hooks"]
        assert "after_update" in hooks
