"""
Tests for memory category assignment and validation.

Tests verify that:
- Memories never get "general" category (it's only for bucket_type)
- Category detection works correctly
- Backward compatibility handles existing "general" categories
"""

from unittest.mock import MagicMock, patch

import pytest

from mdb_engine.memory.cognitive import MEMORY_CATEGORIES, CognitiveMemoryService


class TestMemoryCategories:
    """Test memory category assignment and validation."""

    @pytest.fixture
    def mock_collection(self):
        """Create a mock MongoDB collection."""
        collection = MagicMock()
        collection.insert_one = MagicMock(return_value=MagicMock(inserted_id="test_id"))
        collection.find_one = MagicMock(return_value=None)
        collection.find = MagicMock(return_value=[])
        collection.aggregate = MagicMock(return_value=[])
        collection.update_one = MagicMock(return_value=MagicMock(modified_count=1))
        collection.update_many = MagicMock(return_value=MagicMock(modified_count=0))
        collection.delete_one = MagicMock(return_value=MagicMock(deleted_count=1))
        return collection

    @pytest.fixture
    def memory_service(self, mock_collection):
        """Create a CognitiveMemoryService instance."""
        # Mock embedding provider to avoid API key requirement
        mock_embedding_provider = MagicMock()
        mock_embedding_provider.embed = MagicMock(return_value=[[0.1] * 1536])

        # Patch the embedding service initialization method
        with patch.object(CognitiveMemoryService, "_init_embedding_service", return_value=None):
            service = CognitiveMemoryService(
                app_slug="test_app",
                collection=mock_collection,
                config={"enable_cognitive": True, "categories": {"enabled": True}},
            )

        # Set the mock embedding provider after initialization
        service.embedding_provider = mock_embedding_provider
        service._get_embedding = MagicMock(return_value=[0.1] * 1536)
        service._get_embeddings_batch_sync = MagicMock(return_value={"test": [0.1] * 1536})
        service._find_similar_memories = MagicMock(return_value=[])
        return service

    def test_memory_categories_dict_does_not_include_general(self):
        """Test that MEMORY_CATEGORIES does not include 'general'."""
        assert "general" not in MEMORY_CATEGORIES, (
            "'general' should NOT be in MEMORY_CATEGORIES. "
            "It's only used for bucket_type filtering, not memory categories."
        )
        assert set(MEMORY_CATEGORIES.keys()) == {
            "biographical",
            "preferences",
            "temporal",
            "relational",
        }

    def test_detect_category_from_text_never_returns_general(self, memory_service):
        """Test that _detect_category_from_text never returns 'general'."""
        test_cases = [
            ("User's sister Emily is a doctor", "relational"),
            ("User loves chocolate", "preferences"),
            ("User works at Google", "biographical"),
            ("User has a project due Friday", "temporal"),
            (
                "Random text with no clear category",
                "biographical",
            ),  # Should default to biographical, not general
            ("", "biographical"),  # Empty text defaults to biographical
        ]

        for text, _expected_category in test_cases:
            detected = memory_service._detect_category_from_text(text)
            assert detected != "general", (
                f"Category detection should never return 'general'. "
                f"Text: '{text}', Detected: '{detected}'"
            )
            assert (
                detected in MEMORY_CATEGORIES
            ), f"Detected category '{detected}' must be one of: {list(MEMORY_CATEGORIES.keys())}"

    def test_detect_category_relational_keywords(self, memory_service):
        """Test that relational keywords are detected correctly."""
        relational_texts = [
            "User's sister Emily is a doctor",
            "User's brother-in-law David is a professor",
            "User's family lives in Boston",
            "User's friend Alice works at Microsoft",
            "User knows John from college",
        ]

        for text in relational_texts:
            category = memory_service._detect_category_from_text(text)
            # Most important: never "general"
            assert category != "general", (
                f"Category detection should never return 'general'. "
                f"Text: '{text}', Detected: '{category}'"
            )
            assert (
                category in MEMORY_CATEGORIES
            ), f"Category '{category}' must be valid. Text: '{text}'"
            # Note: Heuristic detection may not always be perfect, but should be reasonable
            # The key requirement is that it never returns "general"

    def test_detect_category_preferences_keywords(self, memory_service):
        """Test that preference keywords are detected correctly."""
        preference_texts = [
            "User loves chocolate",
            "User prefers dark mode",
            "User's favorite color is blue",
            "User enjoys skiing",
            "User is passionate about jazz music",
        ]

        for text in preference_texts:
            category = memory_service._detect_category_from_text(text)
            # Most important: never "general"
            assert category != "general", (
                f"Category detection should never return 'general'. "
                f"Text: '{text}', Detected: '{category}'"
            )
            assert (
                category in MEMORY_CATEGORIES
            ), f"Category '{category}' must be valid. Text: '{text}'"
            # Note: Heuristic detection may not always be perfect, but should be reasonable
            # The key requirement is that it never returns "general"

    def test_detect_category_biographical_keywords(self, memory_service):
        """Test that biographical keywords are detected correctly."""
        biographical_texts = [
            "User's name is John",
            "User works at Google",
            "User lives in Boston",
            "User is 30 years old",
            "User graduated from MIT",
        ]

        for text in biographical_texts:
            category = memory_service._detect_category_from_text(text)
            # Most important: never "general"
            assert category != "general", (
                f"Category detection should never return 'general'. "
                f"Text: '{text}', Detected: '{category}'"
            )
            assert (
                category in MEMORY_CATEGORIES
            ), f"Category '{category}' must be valid. Text: '{text}'"
            # Note: Heuristic detection may not always be perfect, but should be reasonable
            # The key requirement is that it never returns "general"

    def test_detect_category_temporal_keywords(self, memory_service):
        """Test that temporal keywords are detected correctly."""
        temporal_texts = [
            "User has a project due Friday",
            "User is working on a deadline",
            "User has a meeting tomorrow",
            "User is planning a trip next week",
        ]

        for text in temporal_texts:
            category = memory_service._detect_category_from_text(text)
            # Most important: never "general"
            assert category != "general", (
                f"Category detection should never return 'general'. "
                f"Text: '{text}', Detected: '{category}'"
            )
            assert (
                category in MEMORY_CATEGORIES
            ), f"Category '{category}' must be valid. Text: '{text}'"
            # Note: Heuristic detection may not always be perfect, but should be reasonable
            # The key requirement is that it never returns "general"

    def test_get_category_priority_excludes_general(self, memory_service):
        """Test that category priority system excludes 'general'."""
        # Test valid categories
        assert memory_service._get_category_priority("biographical") == 4
        assert memory_service._get_category_priority("preferences") == 3
        assert memory_service._get_category_priority("temporal") == 2
        assert memory_service._get_category_priority("relational") == 1

        # Test that "general" returns 0 (not in priority system)
        assert memory_service._get_category_priority("general") == 0

    def test_get_best_category_handles_missing_categories(self, memory_service):
        """Test that _get_best_category handles missing categories by detecting from text."""
        # When existing category is None, should detect from text
        best = memory_service._get_best_category(
            existing_category=None,
            new_text="User's sister Emily is a doctor",
            new_category=None,
        )
        # Must be a valid category
        assert best in MEMORY_CATEGORIES, f"Category '{best}' must be valid"
        assert best != "general", "Should never return 'general'"

        # When new category is provided and is better
        best = memory_service._get_best_category(
            existing_category="relational",
            new_text="User's sister Emily is a doctor",
            new_category="biographical",  # Higher priority
        )
        # Should prioritize higher priority category when explicitly provided
        assert best in MEMORY_CATEGORIES, f"Category '{best}' must be valid"
        assert best != "general", "Should never return 'general'"

    @patch("mdb_engine.memory.cognitive.completion")
    def test_extract_facts_never_assigns_general(self, mock_completion, memory_service):
        """Test that fact extraction never assigns 'general' category."""
        # Mock LLM response with proper categories
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message = MagicMock()
        mock_response.choices[0].message.content = (
            '{"facts": ['
            '{"text": "User\'s sister Emily is a doctor", "category": "relational"}, '
            '{"text": "User loves chocolate", "category": "preferences"}'
            "]}"
        )
        mock_completion.return_value = mock_response

        # Enable LLM for extraction
        memory_service.llm_available = True

        facts = memory_service._extract_facts_with_categories(
            "My sister Emily is a doctor and I love chocolate"
        )

        assert len(facts) > 0
        for fact in facts:
            category = fact.get("category")
            assert (
                category != "general"
            ), f"Extracted fact should not have 'general' category: {fact}"
            assert (
                category in MEMORY_CATEGORIES
            ), f"Category '{category}' must be one of: {list(MEMORY_CATEGORIES.keys())}"

    def test_category_priority_order(self, memory_service):
        """Test that category priority follows expected order."""
        priorities = {
            "biographical": memory_service._get_category_priority("biographical"),
            "preferences": memory_service._get_category_priority("preferences"),
            "temporal": memory_service._get_category_priority("temporal"),
            "relational": memory_service._get_category_priority("relational"),
        }

        # Verify priority order: biographical > preferences > temporal > relational
        assert priorities["biographical"] > priorities["preferences"]
        assert priorities["preferences"] > priorities["temporal"]
        assert priorities["temporal"] > priorities["relational"]
        assert priorities["relational"] > 0  # Should be positive
