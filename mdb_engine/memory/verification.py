"""
Verification Mixin for CognitiveMemoryService.

Provides Just-in-Time (JIT) verification of memory citations to ensure
stored knowledge is not stale.
"""

import asyncio
import hashlib
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class VerificationMixin:
    """
    Mixin that provides citation verification methods for CognitiveMemoryService.

    Verifies that source code citations (file paths and content hashes)
    stored in memories match the current state of the filesystem.
    """

    def __init__(self, *args, **kwargs):
        # Initialize verification config with defaults
        # These will be overwritten by _apply_resolved if used in CognitiveMemoryService
        self.verification_enabled = False
        self.verification_max_file_size_kb = 100
        super().__init__(*args, **kwargs)

    async def verify_memories(self, memories: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Batch verify a list of memories.

        Adds a 'verification_status' field to each memory:
        - 'verified': Citation checks out
        - 'stale': File changed or deleted
        - 'unverified': No citation or verification disabled/failed
        - 'skipped': File too large or other skip condition

        Args:
            memories: List of memory documents

        Returns:
            List of memories with 'verification_status' and potentially updated metadata
        """
        if not self.verification_enabled:
            return memories

        # Process in parallel
        tasks = []
        for memory in memories:
            tasks.append(self._verify_single_memory(memory))

        return await asyncio.gather(*tasks)

    async def _verify_single_memory(self, memory: dict[str, Any]) -> dict[str, Any]:
        """Verify a single memory's citations."""
        metadata = memory.get("metadata", {})
        citations = metadata.get("citations", [])

        if not citations:
            memory["verification_status"] = "unverified"
            return memory

        # If any citation is invalid, the whole memory is considered stale
        is_valid = True
        for citation in citations:
            file_path = citation.get("file_path")
            stored_hash = citation.get("content_hash")

            # Optional: check specific lines if provided, otherwise whole file
            # For now, we'll implement whole-file or line-range hashing
            line_start = citation.get("line_start")
            line_end = citation.get("line_end")

            if not file_path or not stored_hash:
                continue

            try:
                current_hash = await self._compute_file_hash(file_path, line_start=line_start, line_end=line_end)

                if current_hash is None:
                    # File missing or too large
                    is_valid = False
                    break

                if current_hash != stored_hash:
                    is_valid = False
                    break

            except (OSError, ValueError) as e:
                logger.warning(f"Verification failed for {file_path}: {e}")
                is_valid = False
                break

        memory["verification_status"] = "verified" if is_valid else "stale"
        return memory

    async def _compute_file_hash(
        self, file_path: str, line_start: int | None = None, line_end: int | None = None
    ) -> str | None:
        """
        Compute hash of a file or file section.
        Runs in thread pool to avoid blocking event loop.
        """
        return await asyncio.to_thread(self._compute_file_hash_sync, file_path, line_start, line_end)

    def _compute_file_hash_sync(
        self, file_path: str, line_start: int | None = None, line_end: int | None = None
    ) -> str | None:
        """Synchronous implementation of file hashing."""
        path = Path(file_path)

        if not path.exists() or not path.is_file():
            return None

        # Check file size limit
        try:
            size_kb = path.stat().st_size / 1024
            if size_kb > self.verification_max_file_size_kb:
                logger.debug(f"Skipping verification for large file: {file_path} ({size_kb:.1f}KB)")
                return None
        except OSError:
            return None

        try:
            content = path.read_text(encoding="utf-8")

            # Extract specific lines if requested
            if line_start is not None and line_end is not None:
                lines = content.splitlines()
                # Adjust for 1-based indexing
                start_idx = max(0, line_start - 1)
                end_idx = min(len(lines), line_end)

                if start_idx >= len(lines):
                    return None  # Lines don't exist

                selected_content = "\n".join(lines[start_idx:end_idx])
                return self._hash_string(selected_content)

            return self._hash_string(content)

        except (UnicodeDecodeError, OSError) as e:
            logger.warning(f"Failed to read file for verification {file_path}: {e}")
            return None

    @staticmethod
    def _hash_string(content: str) -> str:
        """Compute SHA-256 hash of string content."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    async def generate_citation(
        self, file_path: str, line_start: int | None = None, line_end: int | None = None
    ) -> dict[str, Any] | None:
        """
        Generate a citation object for a file path.
        Useful when creating new memories.
        """
        content_hash = await self._compute_file_hash(file_path, line_start, line_end)

        if not content_hash:
            return None

        return {
            "file_path": file_path,
            "line_start": line_start,
            "line_end": line_end,
            "content_hash": content_hash,
            "timestamp": "now",  # Should be replaced with actual timestamp by caller
        }
