"""
Unit tests for PerfectBrain container class.
"""

from unittest.mock import MagicMock

import pytest

from mdb_engine.memory.perfect_brain import PerfectBrain


@pytest.fixture
def mock_scoped_db():
    db = MagicMock()
    db.get_collection = MagicMock(return_value=MagicMock())
    return db


class TestPerfectBrainInit:
    """Tests for PerfectBrain initialization."""

    def test_disabled_by_default(self, mock_scoped_db):
        brain = PerfectBrain(slug="test", scoped_db=mock_scoped_db, config={})
        assert brain.active_components == []

    def test_enabled_false_no_components(self, mock_scoped_db):
        brain = PerfectBrain(slug="test", scoped_db=mock_scoped_db, config={"enabled": False})
        assert brain.active_components == []

    def test_memory_veto_initialized(self, mock_scoped_db):
        brain = PerfectBrain(
            slug="test",
            scoped_db=mock_scoped_db,
            config={"enabled": True, "memory_veto": True},
        )
        assert brain.memory_veto is not None
        assert "memory_veto" in brain.active_components

    def test_timeline_service_initialized(self, mock_scoped_db):
        brain = PerfectBrain(
            slug="test",
            scoped_db=mock_scoped_db,
            config={"enabled": True, "timeline_service": True},
        )
        assert brain.timeline_service is not None

    def test_memory_versioning_initialized(self, mock_scoped_db):
        brain = PerfectBrain(
            slug="test",
            scoped_db=mock_scoped_db,
            config={"enabled": True, "memory_versioning": True},
        )
        assert brain.memory_versioning is not None

    def test_multiple_components(self, mock_scoped_db):
        brain = PerfectBrain(
            slug="test",
            scoped_db=mock_scoped_db,
            config={
                "enabled": True,
                "memory_veto": True,
                "timeline_service": True,
                "memory_versioning": True,
            },
        )
        assert len(brain.active_components) == 3

    def test_consolidator_requires_services(self, mock_scoped_db):
        brain = PerfectBrain(
            slug="test",
            scoped_db=mock_scoped_db,
            config={"enabled": True, "consolidator": {"enabled": True}},
        )
        assert brain.consolidator is None

    def test_consolidator_with_services(self, mock_scoped_db):
        brain = PerfectBrain(
            slug="test",
            scoped_db=mock_scoped_db,
            embedding_service=MagicMock(),
            llm_service=MagicMock(),
            config={"enabled": True, "consolidator": {"enabled": True}},
        )
        assert brain.consolidator is not None

    def test_disabled_component_is_none(self, mock_scoped_db):
        brain = PerfectBrain(
            slug="test",
            scoped_db=mock_scoped_db,
            config={"enabled": True, "memory_veto": True},
        )
        assert brain.shared_memory is None
        assert brain.cognitive_memory is None
        assert brain.prospective_memory is None


class TestPerfectBrainProperties:
    """Tests for PerfectBrain property accessors."""

    def test_all_properties_none_when_disabled(self, mock_scoped_db):
        brain = PerfectBrain(slug="test", scoped_db=mock_scoped_db, config={})
        assert brain.shared_memory is None
        assert brain.reflective_memory is None
        assert brain.predictive_memory is None
        assert brain.memory_veto is None
        assert brain.prospective_memory is None
        assert brain.cognitive_memory is None
        assert brain.timeline_service is None
        assert brain.memory_versioning is None
        assert brain.consolidator is None
        assert brain.reflection_service is None

    def test_active_components_list(self, mock_scoped_db):
        brain = PerfectBrain(
            slug="test",
            scoped_db=mock_scoped_db,
            config={"enabled": True, "memory_veto": True, "timeline_service": True},
        )
        components = brain.active_components
        assert isinstance(components, list)
        assert "memory_veto" in components
        assert "timeline_service" in components
        assert "shared_memory" not in components
