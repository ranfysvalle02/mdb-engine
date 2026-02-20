"""
Schemas for the Parallax platform.
Defines contracts for SALES, MARKETING, and PRODUCT analysis of call transcripts.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class ParallaxReport(BaseModel):
    """The complete multi-angle analysis"""

    call_id: Optional[str] = None  # Call identifier (for call transcripts)
    repo_id: str  # Repository/call identifier (for backward compatibility)
    repo_name: str  # Repository name or call description
    repo_owner: str  # Repository owner or customer name
    stars: int = 0  # Number of stars (0 for calls)
    file_found: str = "transcript"  # Which file was found or "transcript" for calls
    original_title: str  # Display title
    url: str = ""  # Repository URL (empty for calls)
    marketing: Optional[Dict[str, Any]] = None  # Dynamic schema - MARKETING lens
    sales: Optional[Dict[str, Any]] = None  # Dynamic schema - SALES lens
    product: Optional[Dict[str, Any]] = None  # Dynamic schema - PRODUCT lens
    relevance: Optional[Dict[str, Any]] = None  # Legacy field (not used for calls)
    # relevant_snippets removed - using memory-only approach, no RAG
    timestamp: str
    matched_keywords: List[str] = []  # Keywords from watchlist that matched
    # Additional metadata (optional for calls)
    pull_requests_count: Optional[int] = None
    issues_count: Optional[int] = None
    last_updated: Optional[str] = None
    last_commit_message: Optional[str] = None
    last_commit_date: Optional[str] = None
    forks_count: Optional[int] = None
    watchers_count: Optional[int] = None
    is_archived: Optional[bool] = None
    is_fork: Optional[bool] = None
    primary_language: Optional[str] = None
