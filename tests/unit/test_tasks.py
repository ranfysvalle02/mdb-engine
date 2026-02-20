"""Tests for mdb_engine.tasks — managed recurring background tasks."""

import asyncio


class TestRecurringTaskDecorator:
    """Tests for @recurring_task registration."""

    def setup_method(self):
        import mdb_engine.tasks as mod

        mod._registry.clear()

    def test_decorator_registers_task(self):
        from mdb_engine.tasks import recurring_task

        @recurring_task(interval_seconds=60, name="test-task")
        async def my_task():
            pass

        from mdb_engine.tasks import _registry

        assert len(_registry) == 1
        assert _registry[0].name == "test-task"
        assert _registry[0].interval_seconds == 60

    def test_decorator_uses_function_name_as_default(self):
        from mdb_engine.tasks import _registry, recurring_task

        @recurring_task(interval_seconds=30)
        async def cleanup_job():
            pass

        assert _registry[0].name == "cleanup_job"

    def test_multiple_registrations(self):
        from mdb_engine.tasks import _registry, recurring_task

        @recurring_task(interval_seconds=10)
        async def task_a():
            pass

        @recurring_task(interval_seconds=20)
        async def task_b():
            pass

        assert len(_registry) == 2


class TestGetTaskStatuses:
    """Tests for get_task_statuses."""

    def setup_method(self):
        import mdb_engine.tasks as mod

        mod._registry.clear()

    def test_empty_registry(self):
        from mdb_engine.tasks import get_task_statuses

        assert get_task_statuses() == []

    def test_status_fields(self):
        from mdb_engine.tasks import get_task_statuses, recurring_task

        @recurring_task(interval_seconds=60, name="check")
        async def check():
            pass

        statuses = get_task_statuses()
        assert len(statuses) == 1
        s = statuses[0]
        assert s["name"] == "check"
        assert s["interval_seconds"] == 60
        assert s["run_count"] == 0
        assert s["error_count"] == 0
        assert s["running"] is False
        assert s["last_run"] is None
        assert s["last_error"] is None


class TestStartAndStopTasks:
    """Tests for start_all_tasks and stop_all_tasks."""

    def setup_method(self):
        import mdb_engine.tasks as mod

        mod._registry.clear()

    async def test_start_creates_asyncio_tasks(self):
        from mdb_engine.tasks import recurring_task, start_all_tasks, stop_all_tasks

        call_count = 0

        @recurring_task(interval_seconds=0.05, name="counter")
        async def counter():
            nonlocal call_count
            call_count += 1

        handles = start_all_tasks()
        assert len(handles) == 1
        assert not handles[0].done()

        await asyncio.sleep(0.15)
        await stop_all_tasks()

        assert call_count >= 1

    async def test_stop_cancels_running_tasks(self):
        from mdb_engine.tasks import recurring_task, start_all_tasks, stop_all_tasks

        @recurring_task(interval_seconds=100, name="long")
        async def long_task():
            await asyncio.sleep(100)

        handles = start_all_tasks()
        await stop_all_tasks()
        assert handles[0].done()

    async def test_backoff_on_failure(self):
        from mdb_engine.tasks import get_task_statuses, recurring_task, start_all_tasks, stop_all_tasks

        @recurring_task(interval_seconds=0.01, name="failer")
        async def failer():
            raise RuntimeError("boom")

        start_all_tasks()
        await asyncio.sleep(0.1)
        await stop_all_tasks()

        statuses = get_task_statuses()
        assert statuses[0]["error_count"] >= 1
        assert statuses[0]["last_error"] == "boom"
