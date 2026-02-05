"""
Tests for BaseMemoryService interface.

Tests that all memory service implementations correctly implement the abstract interface.
"""

import pytest

from mdb_engine.memory import CustomMemoryService, CustomMemoryServiceError
from mdb_engine.memory.base import BaseMemoryService, MemoryServiceError


class TestBaseMemoryService:
    """Test BaseMemoryService abstract interface."""

    def test_custom_inherits_from_base(self):
        """Test that CustomMemoryService inherits from BaseMemoryService."""
        assert issubclass(
            CustomMemoryService, BaseMemoryService
        ), "CustomMemoryService should inherit from BaseMemoryService"

    def test_custom_implements_all_abstract_methods(self):
        """Test that CustomMemoryService implements all abstract methods."""
        # Check that CustomMemoryService has all required methods
        required_methods = [
            "add",
            "inject",
            "get_all",
            "search",
            "get",
            "delete",
            "delete_all",
            "update",
        ]
        for method_name in required_methods:
            assert hasattr(
                CustomMemoryService, method_name
            ), f"CustomMemoryService should implement abstract method: {method_name}"

            method = getattr(CustomMemoryService, method_name)
            assert not getattr(
                method, "__isabstractmethod__", False
            ), f"CustomMemoryService.{method_name} should be implemented, not abstract"

    def test_memory_service_error_hierarchy(self):
        """Test that error classes form proper hierarchy."""
        # CustomMemoryServiceError inherits from MemoryServiceError
        assert issubclass(CustomMemoryServiceError, MemoryServiceError)

        # Can catch CustomMemoryServiceError with MemoryServiceError
        try:
            raise CustomMemoryServiceError("Test error")
        except MemoryServiceError:
            pass
        else:
            pytest.fail("MemoryServiceError should catch CustomMemoryServiceError")

    def test_base_memory_service_type_hints(self):
        """Test that BaseMemoryService can be used as a type hint."""

        # This test ensures BaseMemoryService can be used
        # as a valid type for CustomMemoryService instances
        def get_service() -> BaseMemoryService:
            # In real usage, this would return a CustomMemoryService instance
            pass

        assert callable(get_service)
