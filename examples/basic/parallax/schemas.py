"""
Schemas for the Parallax platform.
Defines contracts for Relevance and Technical analysis.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class ParallaxReport(BaseModel):
    """The complete multi-angle analysis"""

    repo_id: str  # Repository identifier (nameWithOwner format: "owner/repo")
    repo_name: str  # Repository name
    repo_owner: str  # Repository owner
    stars: int  # Number of stars
    file_found: str  # Which file was found: "AGENTS.md" or "LLMs.md"
    original_title: str  # Repository name for display (same as repo_name)
    url: str  # Repository URL
    marketing: Dict[str, Any]  # Dynamic schema - loaded from config
    sales: Dict[str, Any]  # Dynamic schema - loaded from config
    product: Dict[str, Any]  # Dynamic schema - loaded from config (Technical lens)
    relevance: Optional[Dict[str, Any]] = (
        None  # Dynamic schema - loaded from config (Relevance lens)
    )
    timestamp: str
    matched_keywords: List[str] = []  # Keywords from watchlist that matched this repo
    # Additional repository metadata
    pull_requests_count: Optional[int] = None  # Number of open pull requests
    issues_count: Optional[int] = None  # Number of open issues
    last_updated: Optional[str] = None  # Last updated date (ISO format)
    last_commit_message: Optional[str] = None  # Last commit message
    last_commit_date: Optional[str] = None  # Last commit date (ISO format)
    forks_count: Optional[int] = None  # Number of forks
    watchers_count: Optional[int] = None  # Number of watchers
    is_archived: Optional[bool] = None  # Whether repository is archived
    is_fork: Optional[bool] = None  # Whether repository is a fork
    primary_language: Optional[str] = None  # Primary programming language
