"""
Comprehensive unit tests for memory verification (JIT citations).

Covers:
- VerificationMixin standalone (hash computation, line ranges, edge cases)
- verify_memories batch logic (all four status outcomes)
- generate_citation helper
- Config parsing through the builder
- CognitiveMemoryService integration (inject, add, search)
- Verification disabled path
"""

import hashlib
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mdb_engine.memory.verification import VerificationMixin

# ============================================================================
# Helpers
# ============================================================================


def _hash(text: str) -> str:
    """Shortcut: SHA-256 hex digest of *text*."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_temp(content: str) -> str:
    """Write *content* to a temp file and return its path (caller must unlink)."""
    fd, path = tempfile.mkstemp(suffix=".py")
    os.write(fd, content.encode("utf-8"))
    os.close(fd)
    return path


# ============================================================================
# Shared fixtures
# ============================================================================


@pytest.fixture
def mixin():
    """A bare VerificationMixin with verification enabled."""
    m = VerificationMixin()
    m.verification_enabled = True
    m.verification_max_file_size_kb = 100
    return m


@pytest.fixture
def mixin_disabled():
    """A VerificationMixin with verification disabled (default)."""
    return VerificationMixin()


@pytest.fixture
def temp_file():
    """Create a temp file with known content; clean up after test."""
    content = "line 1\nline 2\nline 3\nline 4\nline 5"
    path = _write_temp(content)
    yield path, content
    os.unlink(path)


@pytest.fixture
def mock_collection():
    """Mock Motor collection with all required async stubs."""
    collection = MagicMock()

    collection.insert_one = AsyncMock(return_value=MagicMock(inserted_id="test_id"))
    collection.update_many = AsyncMock()
    collection.create_index = AsyncMock()
    collection.find_one = AsyncMock(return_value=None)

    mock_db = MagicMock()
    mock_db.client = MagicMock()
    mock_db.name = "test_db"
    mock_db.get_collection = MagicMock(return_value=collection)
    collection.database = mock_db

    return collection


@pytest.fixture
def mock_llm_service():
    llm = MagicMock()
    llm.chat_completion = AsyncMock(
        return_value='{"facts": [{"text": "Extracted fact", "category": "biographical", "emotion": 0.5}]}'
    )
    return llm


@pytest.fixture
def mock_embedding_service():
    emb = MagicMock()

    async def _embed(texts, **kwargs):
        return [[0.1] * 1536 for _ in texts]

    emb.embed = MagicMock(side_effect=_embed)
    return emb


def _build_service(mock_collection, mock_llm_service, mock_embedding_service, *, verification_cfg=None, extra_cfg=None):
    """Build a CognitiveMemoryService with configurable verification settings."""
    from mdb_engine.memory.cognitive import CognitiveMemoryService

    cfg = {
        "enable_cognitive": False,
        **(extra_cfg or {}),
    }
    if verification_cfg is not None:
        cfg["verification"] = verification_cfg

    return CognitiveMemoryService(
        app_slug="test_app",
        config=cfg,
        collection=mock_collection,
        llm_service=mock_llm_service,
        embedding_service=mock_embedding_service,
    )


@pytest.fixture
def service_enabled(mock_collection, mock_llm_service, mock_embedding_service):
    """CognitiveMemoryService with verification **enabled**."""
    return _build_service(
        mock_collection,
        mock_llm_service,
        mock_embedding_service,
        verification_cfg={"enabled": True, "max_file_size_kb": 100},
    )


@pytest.fixture
def service_disabled(mock_collection, mock_llm_service, mock_embedding_service):
    """CognitiveMemoryService with verification **disabled** (default)."""
    return _build_service(
        mock_collection,
        mock_llm_service,
        mock_embedding_service,
    )


# ============================================================================
# 1. _hash_string
# ============================================================================


class TestHashString:
    def test_known_value(self):
        assert VerificationMixin._hash_string("hello") == _hash("hello")

    def test_empty_string(self):
        assert VerificationMixin._hash_string("") == _hash("")

    def test_unicode(self):
        assert VerificationMixin._hash_string("caf\u00e9") == _hash("caf\u00e9")


# ============================================================================
# 2. _compute_file_hash / _compute_file_hash_sync
# ============================================================================


class TestComputeFileHash:
    @pytest.mark.asyncio
    async def test_whole_file(self, mixin, temp_file):
        path, content = temp_file
        result = await mixin._compute_file_hash(path)
        assert result == _hash(content)

    @pytest.mark.asyncio
    async def test_line_range_single_line(self, mixin, temp_file):
        """line_start=2, line_end=2 should hash only 'line 2'."""
        path, _ = temp_file
        result = await mixin._compute_file_hash(path, line_start=2, line_end=2)
        assert result == _hash("line 2")

    @pytest.mark.asyncio
    async def test_line_range_multi_line(self, mixin, temp_file):
        """line_start=2, line_end=4 should hash 'line 2\\nline 3\\nline 4'."""
        path, _ = temp_file
        result = await mixin._compute_file_hash(path, line_start=2, line_end=4)
        assert result == _hash("line 2\nline 3\nline 4")

    @pytest.mark.asyncio
    async def test_first_line(self, mixin, temp_file):
        path, _ = temp_file
        result = await mixin._compute_file_hash(path, line_start=1, line_end=1)
        assert result == _hash("line 1")

    @pytest.mark.asyncio
    async def test_last_line(self, mixin, temp_file):
        path, _ = temp_file
        result = await mixin._compute_file_hash(path, line_start=5, line_end=5)
        assert result == _hash("line 5")

    @pytest.mark.asyncio
    async def test_nonexistent_file(self, mixin):
        result = await mixin._compute_file_hash("/no/such/file.py")
        assert result is None

    @pytest.mark.asyncio
    async def test_directory_returns_none(self, mixin):
        result = await mixin._compute_file_hash(tempfile.gettempdir())
        assert result is None

    @pytest.mark.asyncio
    async def test_line_range_past_end_of_file(self, mixin, temp_file):
        """Lines beyond EOF should return None (start_idx >= len(lines))."""
        path, _ = temp_file
        result = await mixin._compute_file_hash(path, line_start=999, line_end=1000)
        assert result is None

    @pytest.mark.asyncio
    async def test_file_exceeds_max_size(self, mixin):
        """Files larger than max_file_size_kb should return None."""
        mixin.verification_max_file_size_kb = 0  # anything > 0 bytes exceeds
        path = _write_temp("x")
        try:
            result = await mixin._compute_file_hash(path)
            assert result is None
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_binary_file_returns_none(self, mixin):
        """Non-UTF-8 file should return None (UnicodeDecodeError)."""
        fd, path = tempfile.mkstemp()
        os.write(fd, b"\x80\x81\x82\xff\xfe")
        os.close(fd)
        try:
            result = await mixin._compute_file_hash(path)
            assert result is None
        finally:
            os.unlink(path)


# ============================================================================
# 3. _verify_single_memory
# ============================================================================


class TestVerifySingleMemory:
    @pytest.mark.asyncio
    async def test_valid_citation(self, mixin, temp_file):
        path, content = temp_file
        mem = {
            "id": "m1",
            "metadata": {"citations": [{"file_path": path, "content_hash": _hash(content)}]},
        }
        result = await mixin._verify_single_memory(mem)
        assert result["verification_status"] == "verified"

    @pytest.mark.asyncio
    async def test_stale_citation(self, mixin, temp_file):
        path, _ = temp_file
        mem = {
            "id": "m2",
            "metadata": {"citations": [{"file_path": path, "content_hash": "wrong"}]},
        }
        result = await mixin._verify_single_memory(mem)
        assert result["verification_status"] == "stale"

    @pytest.mark.asyncio
    async def test_missing_file_citation(self, mixin):
        mem = {
            "id": "m3",
            "metadata": {"citations": [{"file_path": "/gone.py", "content_hash": "x"}]},
        }
        result = await mixin._verify_single_memory(mem)
        assert result["verification_status"] == "stale"

    @pytest.mark.asyncio
    async def test_no_citations(self, mixin):
        mem = {"id": "m4", "metadata": {"citations": []}}
        result = await mixin._verify_single_memory(mem)
        assert result["verification_status"] == "unverified"

    @pytest.mark.asyncio
    async def test_no_metadata(self, mixin):
        mem = {"id": "m5"}
        result = await mixin._verify_single_memory(mem)
        assert result["verification_status"] == "unverified"

    @pytest.mark.asyncio
    async def test_citation_missing_file_path(self, mixin):
        """Citation dict without file_path should be skipped, not crash."""
        mem = {"id": "m6", "metadata": {"citations": [{"content_hash": "abc"}]}}
        result = await mixin._verify_single_memory(mem)
        # All citations are skipped (no file_path), treated as unverified
        assert result["verification_status"] == "verified"  # no citation failed

    @pytest.mark.asyncio
    async def test_citation_missing_content_hash(self, mixin, temp_file):
        """Citation without content_hash should be skipped."""
        path, _ = temp_file
        mem = {"id": "m7", "metadata": {"citations": [{"file_path": path}]}}
        result = await mixin._verify_single_memory(mem)
        assert result["verification_status"] == "verified"  # skipped, nothing failed

    @pytest.mark.asyncio
    async def test_multiple_citations_one_stale(self, mixin, temp_file):
        """If any citation is stale, the whole memory is stale."""
        path, content = temp_file
        mem = {
            "id": "m8",
            "metadata": {
                "citations": [
                    {"file_path": path, "content_hash": _hash(content)},
                    {"file_path": path, "content_hash": "wrong_hash"},
                ]
            },
        }
        result = await mixin._verify_single_memory(mem)
        assert result["verification_status"] == "stale"

    @pytest.mark.asyncio
    async def test_multiple_citations_all_valid(self, mixin, temp_file):
        path, content = temp_file
        mem = {
            "id": "m9",
            "metadata": {
                "citations": [
                    {"file_path": path, "content_hash": _hash(content)},
                    {"file_path": path, "content_hash": _hash(content)},
                ]
            },
        }
        result = await mixin._verify_single_memory(mem)
        assert result["verification_status"] == "verified"

    @pytest.mark.asyncio
    async def test_line_range_citation_valid(self, mixin, temp_file):
        path, _ = temp_file
        mem = {
            "id": "m10",
            "metadata": {
                "citations": [
                    {
                        "file_path": path,
                        "line_start": 3,
                        "line_end": 3,
                        "content_hash": _hash("line 3"),
                    }
                ]
            },
        }
        result = await mixin._verify_single_memory(mem)
        assert result["verification_status"] == "verified"

    @pytest.mark.asyncio
    async def test_line_range_citation_stale(self, mixin, temp_file):
        path, _ = temp_file
        mem = {
            "id": "m11",
            "metadata": {
                "citations": [
                    {
                        "file_path": path,
                        "line_start": 3,
                        "line_end": 3,
                        "content_hash": _hash("something else"),
                    }
                ]
            },
        }
        result = await mixin._verify_single_memory(mem)
        assert result["verification_status"] == "stale"


# ============================================================================
# 4. verify_memories (batch)
# ============================================================================


class TestVerifyMemories:
    @pytest.mark.asyncio
    async def test_batch_all_statuses(self, mixin, temp_file):
        """Verify all four possible statuses in one batch call."""
        path, content = temp_file

        memories = [
            {"id": "valid", "metadata": {"citations": [{"file_path": path, "content_hash": _hash(content)}]}},
            {"id": "stale", "metadata": {"citations": [{"file_path": path, "content_hash": "bad"}]}},
            {"id": "unverified", "metadata": {"citations": []}},
            {"id": "gone", "metadata": {"citations": [{"file_path": "/nope.py", "content_hash": "x"}]}},
        ]

        results = await mixin.verify_memories(memories)

        assert results[0]["verification_status"] == "verified"
        assert results[1]["verification_status"] == "stale"
        assert results[2]["verification_status"] == "unverified"
        assert results[3]["verification_status"] == "stale"

    @pytest.mark.asyncio
    async def test_disabled_returns_unmodified(self, mixin_disabled):
        memories = [{"id": "x", "metadata": {"citations": [{"file_path": "/a.py", "content_hash": "z"}]}}]
        results = await mixin_disabled.verify_memories(memories)
        assert "verification_status" not in results[0]

    @pytest.mark.asyncio
    async def test_empty_list(self, mixin):
        results = await mixin.verify_memories([])
        assert results == []

    @pytest.mark.asyncio
    async def test_preserves_original_fields(self, mixin, temp_file):
        """verify_memories should not strip any existing fields from the memory dict."""
        path, content = temp_file
        mem = {
            "id": "keep",
            "text": "original text",
            "importance": 0.9,
            "metadata": {"citations": [{"file_path": path, "content_hash": _hash(content)}], "bucket_id": "b1"},
        }
        result = (await mixin.verify_memories([mem]))[0]
        assert result["text"] == "original text"
        assert result["importance"] == 0.9
        assert result["metadata"]["bucket_id"] == "b1"
        assert result["verification_status"] == "verified"


# ============================================================================
# 5. generate_citation
# ============================================================================


class TestGenerateCitation:
    @pytest.mark.asyncio
    async def test_whole_file(self, mixin, temp_file):
        path, content = temp_file
        citation = await mixin.generate_citation(path)
        assert citation is not None
        assert citation["file_path"] == path
        assert citation["content_hash"] == _hash(content)
        assert citation["line_start"] is None
        assert citation["line_end"] is None

    @pytest.mark.asyncio
    async def test_line_range(self, mixin, temp_file):
        path, _ = temp_file
        citation = await mixin.generate_citation(path, line_start=2, line_end=3)
        assert citation is not None
        assert citation["content_hash"] == _hash("line 2\nline 3")
        assert citation["line_start"] == 2
        assert citation["line_end"] == 3

    @pytest.mark.asyncio
    async def test_nonexistent_file(self, mixin):
        citation = await mixin.generate_citation("/does/not/exist.py")
        assert citation is None


# ============================================================================
# 6. Builder config parsing
# ============================================================================


class TestBuilderConfig:
    def test_defaults_when_verification_absent(self, mock_collection, mock_llm_service, mock_embedding_service):
        svc = _build_service(mock_collection, mock_llm_service, mock_embedding_service)
        assert svc.verification_enabled is False
        assert svc.verification_max_file_size_kb == 100

    def test_enabled_via_config(self, mock_collection, mock_llm_service, mock_embedding_service):
        svc = _build_service(
            mock_collection,
            mock_llm_service,
            mock_embedding_service,
            verification_cfg={"enabled": True, "max_file_size_kb": 50},
        )
        assert svc.verification_enabled is True
        assert svc.verification_max_file_size_kb == 50

    def test_disabled_explicitly(self, mock_collection, mock_llm_service, mock_embedding_service):
        svc = _build_service(
            mock_collection,
            mock_llm_service,
            mock_embedding_service,
            verification_cfg={"enabled": False},
        )
        assert svc.verification_enabled is False

    def test_partial_config_uses_defaults(self, mock_collection, mock_llm_service, mock_embedding_service):
        """If 'verification' key exists but 'max_file_size_kb' is absent, default is used."""
        svc = _build_service(
            mock_collection,
            mock_llm_service,
            mock_embedding_service,
            verification_cfg={"enabled": True},
        )
        assert svc.verification_enabled is True
        assert svc.verification_max_file_size_kb == 100


# ============================================================================
# 7. CognitiveMemoryService.inject with citations
# ============================================================================


class TestInjectCitations:
    @pytest.mark.asyncio
    async def test_citations_stored_in_metadata(self, service_enabled):
        citations = [{"file_path": "/a.py", "content_hash": "abc"}]
        await service_enabled.inject("Test fact", citations=citations)

        doc = service_enabled.collection.insert_one.call_args[0][0]
        assert doc["metadata"]["citations"] == citations

    @pytest.mark.asyncio
    async def test_no_citations_no_key(self, service_enabled):
        await service_enabled.inject("Test fact")

        doc = service_enabled.collection.insert_one.call_args[0][0]
        assert "citations" not in doc["metadata"]

    @pytest.mark.asyncio
    async def test_multiple_citations(self, service_enabled):
        citations = [
            {"file_path": "/a.py", "line_start": 1, "line_end": 5, "content_hash": "h1"},
            {"file_path": "/b.py", "content_hash": "h2"},
        ]
        await service_enabled.inject("Multi-cite fact", citations=citations)

        doc = service_enabled.collection.insert_one.call_args[0][0]
        assert len(doc["metadata"]["citations"]) == 2
        assert doc["metadata"]["citations"][0]["line_start"] == 1


# ============================================================================
# 8. CognitiveMemoryService.add with citations
# ============================================================================


class TestAddCitations:
    @pytest.mark.asyncio
    async def test_citations_forwarded_to_metadata(self, service_enabled):
        citations = [{"file_path": "/c.py", "content_hash": "xyz"}]

        with patch.object(
            service_enabled,
            "_extract_facts_with_categories",
            return_value=[{"text": "fact", "category": "general", "emotion": 0.3}],
        ):
            with patch.object(service_enabled, "_execute_async_actions") as mock_exec:
                mock_exec.return_value = [{"id": "1", "memory": "fact"}]

                await service_enabled.add("Input text", citations=citations)

                final_metadata = mock_exec.call_args[1]["final_metadata"]
                assert final_metadata["citations"] == citations

    @pytest.mark.asyncio
    async def test_no_citations_no_key_in_add(self, service_enabled):
        with patch.object(
            service_enabled,
            "_extract_facts_with_categories",
            return_value=[{"text": "fact", "category": "general", "emotion": 0.3}],
        ):
            with patch.object(service_enabled, "_execute_async_actions") as mock_exec:
                mock_exec.return_value = [{"id": "1", "memory": "fact"}]

                await service_enabled.add("Input text")

                final_metadata = mock_exec.call_args[1]["final_metadata"]
                assert "citations" not in final_metadata


# ============================================================================
# 9. CognitiveMemoryService.search verification integration
# ============================================================================


class TestSearchVerification:
    @pytest.mark.asyncio
    async def test_search_calls_verify_when_enabled(self, service_enabled):
        mock_memory = {"id": "1", "text": "Test", "metadata": {}}
        service_enabled._search = AsyncMock(return_value=[mock_memory])

        with patch.object(service_enabled, "verify_memories", new_callable=AsyncMock) as mock_verify:
            mock_verify.return_value = [{**mock_memory, "verification_status": "verified"}]

            results = await service_enabled.search("query", user_id="u1")

            mock_verify.assert_called_once_with([mock_memory])
            assert results[0]["verification_status"] == "verified"

    @pytest.mark.asyncio
    async def test_search_skips_verify_when_disabled(self, service_disabled):
        mock_memory = {"id": "1", "text": "Test", "metadata": {}}
        service_disabled._search = AsyncMock(return_value=[mock_memory])

        with patch.object(service_disabled, "verify_memories", new_callable=AsyncMock) as mock_verify:
            results = await service_disabled.search("query", user_id="u1")

            mock_verify.assert_not_called()
            assert "verification_status" not in results[0]

    @pytest.mark.asyncio
    async def test_search_empty_results_no_crash(self, service_enabled):
        service_enabled._search = AsyncMock(return_value=[])

        results = await service_enabled.search("query", user_id="u1")
        assert results == []


# ============================================================================
# 10. End-to-end: real file changes detected
# ============================================================================


class TestEndToEnd:
    @pytest.mark.asyncio
    async def test_file_change_detected_as_stale(self, mixin):
        """Write a file, generate citation, mutate file, verify -> stale."""
        path = _write_temp("original content")
        try:
            citation = await mixin.generate_citation(path)
            assert citation is not None

            mem = {"id": "e2e", "metadata": {"citations": [citation]}}

            # Initially verified
            result = (await mixin.verify_memories([mem]))[0]
            assert result["verification_status"] == "verified"

            # Mutate the file
            with open(path, "w") as f:
                f.write("modified content")

            # Now stale
            result = (await mixin.verify_memories([mem]))[0]
            assert result["verification_status"] == "stale"
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_file_deletion_detected_as_stale(self, mixin):
        """Delete a file after citation -> stale."""
        path = _write_temp("temp content")
        citation = await mixin.generate_citation(path)
        assert citation is not None

        mem = {"id": "e2e_del", "metadata": {"citations": [citation]}}

        # Verified while file exists
        result = (await mixin.verify_memories([mem]))[0]
        assert result["verification_status"] == "verified"

        # Delete
        os.unlink(path)

        # Now stale
        result = (await mixin.verify_memories([mem]))[0]
        assert result["verification_status"] == "stale"

    @pytest.mark.asyncio
    async def test_line_range_change_detected(self, mixin):
        """Only the cited lines changed -> stale."""
        path = _write_temp("keep\nchangeable\nkeep")
        try:
            citation = await mixin.generate_citation(path, line_start=2, line_end=2)
            assert citation is not None
            assert citation["content_hash"] == _hash("changeable")

            mem = {"id": "e2e_lr", "metadata": {"citations": [citation]}}

            result = (await mixin.verify_memories([mem]))[0]
            assert result["verification_status"] == "verified"

            # Change only line 2
            with open(path, "w") as f:
                f.write("keep\nDIFFERENT\nkeep")

            result = (await mixin.verify_memories([mem]))[0]
            assert result["verification_status"] == "stale"
        finally:
            os.unlink(path)
