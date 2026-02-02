"""
Unit tests for BaseMemoryService abstract base class.

Tests the base class architecture and ensures:
- BaseMemoryService is properly abstract
- Mem0MemoryService correctly implements all abstract methods
- Type checking and inheritance work correctly
"""

import pytest

from mdb_engine.memory.base import BaseMemoryService, MemoryServiceError
from mdb_engine.memory.service import Mem0MemoryService, Mem0MemoryServiceError


class TestBaseMemoryService:
    """Test BaseMemoryService abstract base class."""

    def test_base_class_is_abstract(self):
        """Test that BaseMemoryService cannot be instantiated directly."""
        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            BaseMemoryService()

    def test_base_class_has_abstract_methods(self):
        """Test that BaseMemoryService defines all required abstract methods."""
        abstract_methods = BaseMemoryService.__abstractmethods__
        expected_methods = {
            "add",
            "inject",
            "get_all",
            "search",
            "get",
            "delete",
            "delete_all",
            "update",
        }
        assert abstract_methods == expected_methods, (
            f"BaseMemoryService should define abstract methods: {expected_methods}. "
            f"Got: {abstract_methods}"
        )

    def test_mem0_inherits_from_base(self):
        """Test that Mem0MemoryService inherits from BaseMemoryService."""
        assert issubclass(
            Mem0MemoryService, BaseMemoryService
        ), "Mem0MemoryService should inherit from BaseMemoryService"

    def test_mem0_implements_all_abstract_methods(self):
        """Test that Mem0MemoryService implements all abstract methods."""
        # Check that Mem0MemoryService has all required methods
        required_methods = BaseMemoryService.__abstractmethods__
        for method_name in required_methods:
            assert hasattr(
                Mem0MemoryService, method_name
            ), f"Mem0MemoryService should implement abstract method: {method_name}"
            # Verify it's not abstract (i.e., it's implemented)
            method = getattr(Mem0MemoryService, method_name)
            assert not getattr(
                method, "__isabstractmethod__", False
            ), f"Mem0MemoryService.{method_name} should be implemented, not abstract"

    def test_memory_service_error_hierarchy(self):
        """Test that exception hierarchy is correct."""
        # MemoryServiceError is the base exception
        assert issubclass(MemoryServiceError, Exception)

        # Mem0MemoryServiceError inherits from MemoryServiceError
        assert issubclass(Mem0MemoryServiceError, MemoryServiceError)

        # Can catch Mem0MemoryServiceError with MemoryServiceError
        try:
            raise Mem0MemoryServiceError("Test error")
        except MemoryServiceError:
            pass  # Should catch it
        except Exception:
            pytest.fail("MemoryServiceError should catch Mem0MemoryServiceError")

    def test_base_class_can_be_used_for_type_hints(self):
        """Test that BaseMemoryService can be used for type hints."""
        # This test verifies that the type system recognizes BaseMemoryService
        # as a valid type for Mem0MemoryService instances

        def get_service() -> BaseMemoryService:
            # In real usage, this would return a Mem0MemoryService instance
            # but typed as BaseMemoryService for extensibility
            pass

        # Verify the function signature is valid
        import inspect

        sig = inspect.signature(get_service)
        return_annotation = sig.return_annotation
        assert (
            return_annotation == BaseMemoryService
        ), "Function should return BaseMemoryService type"

    def test_custom_provider_can_inherit_from_base(self):
        """Test that a custom provider can inherit from BaseMemoryService."""

        class CustomMemoryService(BaseMemoryService):
            """Custom memory service implementation for testing."""

            def __init__(self):
                pass

            def add(self, messages, user_id=None, metadata=None, **kwargs):
                return []

            def inject(self, memory, user_id=None, metadata=None, **kwargs):
                return {"id": "test", "memory": memory}

            def get_all(self, user_id=None, limit=100, filters=None, **kwargs):
                return []

            def search(self, query, user_id=None, limit=5, filters=None, **kwargs):
                return []

            def get(self, memory_id, user_id=None, **kwargs):
                return None

            def delete(self, memory_id, user_id=None, **kwargs):
                return False

            def delete_all(self, user_id=None, **kwargs):
                return False

            def update(self, memory_id, user_id=None, memory=None, metadata=None, **kwargs):
                return None

        # Should be able to instantiate CustomMemoryService
        custom_service = CustomMemoryService()
        assert isinstance(custom_service, BaseMemoryService)
        assert isinstance(custom_service, CustomMemoryService)

    def test_incomplete_provider_cannot_be_instantiated(self):
        """Test that a provider missing abstract methods cannot be instantiated."""

        class IncompleteMemoryService(BaseMemoryService):
            """Incomplete implementation missing some methods."""

            def add(self, messages, user_id=None, metadata=None, **kwargs):
                return []

            # Missing other required methods

        # Should raise TypeError when trying to instantiate
        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            IncompleteMemoryService()

    def test_base_class_method_signatures(self):
        """Test that BaseMemoryService methods have correct signatures."""
        import inspect

        # Check add method signature
        add_sig = inspect.signature(BaseMemoryService.add)
        assert "messages" in add_sig.parameters
        assert "user_id" in add_sig.parameters
        assert "metadata" in add_sig.parameters

        # Check search method signature
        search_sig = inspect.signature(BaseMemoryService.search)
        assert "query" in search_sig.parameters
        assert "user_id" in search_sig.parameters
        assert "limit" in search_sig.parameters

        # Check inject method signature
        inject_sig = inspect.signature(BaseMemoryService.inject)
        assert "memory" in inject_sig.parameters
        assert "user_id" in inject_sig.parameters
        assert "metadata" in inject_sig.parameters

        # Check update method signature
        update_sig = inspect.signature(BaseMemoryService.update)
        assert "memory_id" in update_sig.parameters
        assert "user_id" in update_sig.parameters
        assert "memory" in update_sig.parameters
        assert "metadata" in update_sig.parameters
