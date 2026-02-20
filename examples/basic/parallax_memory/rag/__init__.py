"""
RAG Module - Retrieval-Augmented Generation for Parallax

Demonstrates mdb-engine's vector search capabilities:
- Transcript indexing with embeddings
- Vector search for relevant snippet extraction
"""

from .snippet_extractor import SnippetExtractor
from .transcript_indexer import TranscriptIndexer

__all__ = ["TranscriptIndexer", "SnippetExtractor"]
