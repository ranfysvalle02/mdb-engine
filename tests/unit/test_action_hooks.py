"""Tests for run_action hook integration in HookExecutor."""

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestRunActionHook:
    """Tests for the run_action hook type in HookExecutor."""

    def setup_method(self):
        from mdb_engine.actions.discovery import _clear_registry

        _clear_registry()

    @pytest.mark.asyncio
    async def test_run_action_invokes_handler(self):
        from mdb_engine.actions.discovery import ActionDef, _action_registry
        from mdb_engine.routing._hooks import HookExecutor

        handler = AsyncMock()
        adef = ActionDef(
            name="on-test",
            handler=handler,
            trigger="event",
            event="after_create",
            collection="items",
        )
        engine = MagicMock()
        engine.get_scoped_db = AsyncMock()
        _action_registry["on-test"] = (adef, engine, "test-app")

        hooks_config = {
            "after_create": [
                {
                    "action": "run_action",
                    "action_name": "on-test",
                    "collection": "items",
                }
            ]
        }
        executor = HookExecutor(hooks_config)

        doc = {"_id": "doc1", "title": "Test"}
        user = {"_id": "u1"}
        db = MagicMock()

        await executor.run("after_create", doc, user, db)

        handler.assert_awaited_once()
        ctx_arg = handler.call_args[0][0]
        assert ctx_arg.event_doc == doc
        assert ctx_arg.user == user
        assert ctx_arg.slug == "test-app"

    @pytest.mark.asyncio
    async def test_run_action_missing_action_name(self):
        from mdb_engine.routing._hooks import HookExecutor

        hooks_config = {"after_create": [{"action": "run_action", "collection": "items"}]}
        executor = HookExecutor(hooks_config)

        # Should not raise, just log a warning
        await executor.run("after_create", {"_id": "1"}, None, MagicMock())

    @pytest.mark.asyncio
    async def test_run_action_unregistered_action(self):
        from mdb_engine.routing._hooks import HookExecutor

        hooks_config = {
            "after_create": [
                {
                    "action": "run_action",
                    "action_name": "nonexistent",
                    "collection": "items",
                }
            ]
        }
        executor = HookExecutor(hooks_config)

        # Should not raise, just log a warning
        await executor.run("after_create", {"_id": "1"}, None, MagicMock())

    @pytest.mark.asyncio
    async def test_run_action_handler_exception_logged(self):
        from mdb_engine.actions.discovery import ActionDef, _action_registry
        from mdb_engine.routing._hooks import HookExecutor

        async def failing_handler(ctx):
            raise ValueError("handler exploded")

        adef = ActionDef(
            name="failing",
            handler=failing_handler,
            trigger="event",
            event="after_create",
            collection="items",
        )
        _action_registry["failing"] = (adef, MagicMock(), "test-app")

        hooks_config = {
            "after_create": [
                {
                    "action": "run_action",
                    "action_name": "failing",
                    "collection": "items",
                }
            ]
        }
        executor = HookExecutor(hooks_config)

        # Should not propagate (fire-and-forget)
        await executor.run("after_create", {"_id": "1"}, None, MagicMock())

    @pytest.mark.asyncio
    async def test_run_action_with_condition(self):
        from mdb_engine.actions.discovery import ActionDef, _action_registry
        from mdb_engine.routing._hooks import HookExecutor

        handler = AsyncMock()
        adef = ActionDef(
            name="conditional",
            handler=handler,
            trigger="event",
            event="after_update",
            collection="items",
        )
        _action_registry["conditional"] = (adef, MagicMock(), "test-app")

        hooks_config = {
            "after_update": [
                {
                    "action": "run_action",
                    "action_name": "conditional",
                    "collection": "items",
                    "if": {"status": "published"},
                }
            ]
        }
        executor = HookExecutor(hooks_config)

        # Should NOT invoke — condition not met
        await executor.run("after_update", {"_id": "1", "status": "draft"}, None, MagicMock())
        handler.assert_not_awaited()

        # Should invoke — condition met
        await executor.run("after_update", {"_id": "1", "status": "published"}, None, MagicMock())
        handler.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_action_receives_prev_doc(self):
        from mdb_engine.actions.discovery import ActionDef, _action_registry
        from mdb_engine.routing._hooks import HookExecutor

        handler = AsyncMock()
        adef = ActionDef(
            name="with-prev",
            handler=handler,
            trigger="event",
            event="after_update",
            collection="items",
        )
        _action_registry["with-prev"] = (adef, MagicMock(), "test-app")

        hooks_config = {
            "after_update": [
                {
                    "action": "run_action",
                    "action_name": "with-prev",
                    "collection": "items",
                }
            ]
        }
        executor = HookExecutor(hooks_config)

        doc = {"_id": "1", "status": "done"}
        prev = {"_id": "1", "status": "pending"}

        await executor.run("after_update", doc, None, MagicMock(), prev=prev)

        ctx_arg = handler.call_args[0][0]
        assert ctx_arg.event_doc == doc
        assert ctx_arg.event_prev == prev
