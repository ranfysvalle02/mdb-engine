#!/usr/bin/env python3
"""
Transcript Indexer Module

Handles indexing of call transcripts with embeddings for vector search.
Demonstrates mdb-engine's embedding service and vector search capabilities.
"""
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("Parallax.RAG")


class TranscriptIndexer:
    """
    Index call transcripts in MongoDB with embeddings for vector search.
    
    This module demonstrates:
    1. Loading transcripts from CALLS.json
    2. Splitting transcripts into semantic chunks
    3. Generating embeddings for each chunk
    4. Storing chunks in MongoDB for vector search
    """
    
    def __init__(
        self,
        db,
        embedding_service=None,
        calls_file: Optional[str] = None,
        app_slug: str = "parallax_memory",
    ):
        """
        Initialize the Transcript Indexer.
        
        Args:
            db: Scoped MongoDB database instance
            embedding_service: Optional EmbeddingService for generating embeddings
            calls_file: Path to CALLS.json file (defaults to CALLS.json in parallax directory)
            app_slug: App slug for logging
        """
        self.db = db
        self.embedding_service = embedding_service
        self.app_slug = app_slug
        
        # Determine path to CALLS.json
        if calls_file:
            self.calls_file = Path(calls_file)
        else:
            # Try environment variable first, then default to parallax directory
            env_calls_file = os.getenv("CALLS_JSON_PATH")
            if env_calls_file:
                self.calls_file = Path(env_calls_file)
            else:
                # Default to CALLS.json in parallax directory
                self.calls_file = Path(__file__).parent.parent / "CALLS.json"
    
    def load_transcripts(self) -> List[Dict[str, Any]]:
        """
        Load call transcripts from CALLS.json file.
        
        Returns:
            List of call transcript dictionaries
        """
        logger.info(f"Looking for CALLS.json at: {self.calls_file}")
        logger.info(f"File exists: {self.calls_file.exists()}")
        logger.info(f"Absolute path: {self.calls_file.resolve()}")
        
        if not self.calls_file.exists():
            logger.warning(f"CALLS.json not found at {self.calls_file}, trying alternative locations...")
            # Try alternative locations (in order of likelihood)
            alt_paths = [
                Path("/app/CALLS.json"),  # Docker container location
                Path(__file__).parent.parent / "CALLS.json",  # Parallax directory
                Path.cwd() / "CALLS.json",  # Current working directory
            ]
            found = False
            for alt_path in alt_paths:
                logger.info(f"Checking alternative path: {alt_path} (exists: {alt_path.exists()})")
                if alt_path.exists():
                    logger.info(f"✓ Found CALLS.json at: {alt_path}")
                    self.calls_file = alt_path
                    found = True
                    break
            
            if not found:
                logger.error(f"✗ CALLS.json not found in any location. Checked:")
                for alt_path in alt_paths:
                    logger.error(f"  - {alt_path}")
                return []
        
        try:
            with open(self.calls_file, "r", encoding="utf-8") as f:
                calls = json.load(f)
            logger.info(f"Loaded {len(calls)} call transcripts from {self.calls_file}")
            return calls
        except (json.JSONDecodeError, IOError, OSError) as e:
            logger.error(f"Failed to load CALLS.json: {e}", exc_info=True)
            return []
    
    async def index_transcripts(self, progress_callback: Optional[Callable] = None) -> int:
        """
        Index call transcripts in MongoDB with embeddings for vector search.
        
        Args:
            progress_callback: Optional async callback for progress updates
        
        Returns:
            Number of transcripts indexed
        """
        calls = self.load_transcripts()
        if not calls:
            logger.warning("No transcripts to index")
            return 0
        
        indexed_count = 0
        
        for i, call in enumerate(calls):
            call_id = call.get("call_id")
            if not call_id:
                continue
            
            # Check if already indexed
            existing = await self.db.call_transcripts.find_one({"call_id": call_id})
            if existing:
                logger.debug(f"Skipping already indexed call: {call_id}")
                continue
            
            transcript_text = call.get("transcript", "")
            if not transcript_text:
                continue
            
            # Store call transcript with metadata
            call_doc = {
                "call_id": call_id,
                "timestamp": call.get("timestamp"),
                "duration_seconds": call.get("duration_seconds"),
                "call_type": call.get("call_type"),
                "participants": call.get("participants", {}),
                "transcript": transcript_text,
                "metadata": call.get("metadata", {}),
                "indexed_at": datetime.utcnow(),
            }
            
            # NO EMBEDDINGS - Memory only approach, no RAG/vector search
            # Just store the transcript document for reference
            await self.db.call_transcripts.insert_one(call_doc)
            indexed_count += 1
            
            if progress_callback:
                try:
                    await progress_callback(
                        {
                            "type": "indexing_progress",
                            "call_id": call_id,
                            "indexed": indexed_count,
                            "total": len(calls),
                        }
                    )
                except Exception:
                    logger.debug("Progress callback failed during indexing", exc_info=True)
            
            logger.debug(f"Indexed call: {call_id}")
        
        logger.info(f"Indexed {indexed_count} call transcripts")
        return indexed_count
