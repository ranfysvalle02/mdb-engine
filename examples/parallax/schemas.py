"""
Schemas for the Parallax platform.
Defines contracts for Relevance and Technical analysis.
"""
from typing import List, Optional, Literal
from pydantic import BaseModel, Field

# --- Viewpoint 1: The Marketer ---
class MarketingView(BaseModel):
    """The 'Hype' Angle"""
    headline_remix: str = Field(description="Rewrite the headline to be click-baity for LinkedIn")
    virality_score: int = Field(description="1-100 score of how likely this is to trend", ge=1, le=100)
    hashtags: List[str] = Field(description="Top 3 hashtags to piggyback on this trend", min_length=0, max_length=3)
    brand_safety: Literal["Safe", "Risky", "Toxic"] = Field(description="Is this safe to post about?")

# --- Viewpoint 2: The Seller ---
class SalesView(BaseModel):
    """The 'Money' Angle"""
    prospect_profile: str = Field(description="Job title of the person who cares about this (e.g. 'CTO', 'DevOps Lead')")
    pain_point_identified: str = Field(description="The specific problem this technology solves")
    cold_outreach_opener: str = Field(description="A 1-sentence icebreaker mentioning this news")
    competitor_detected: bool = Field(description="Is a direct competitor mentioned?")

# --- Viewpoint 3: The Builder (Retention) ---
class ProductView(BaseModel):
    """The 'Tech' Angle"""
    feature_gap: str = Field(description="What feature does this imply we are missing?")
    migration_risk: Literal["Low", "Medium", "High"] = Field(description="Risk of users switching to this")
    technical_debt_warning: str = Field(description="Does this make our current stack look obsolete?")

# --- The Parallax Container ---
# Note: ParallaxReport uses Dict[str, Any] for lens views to support dynamic schemas
from typing import Dict, Any

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
    relevance: Optional[Dict[str, Any]] = None  # Dynamic schema - loaded from config (Relevance lens)
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

# Legacy schemas for backward compatibility (if needed)
AgentType = Literal["Researcher", "RadarScanner", "Coder", "FormExtractor"]

class Task(BaseModel):
    """A single unit of work defined by the Planner"""
    id: int = Field(description="Unique incremental ID for the task")
    description: str = Field(description="Clear instruction for the agent")
    assigned_agent: AgentType = Field(description="The specialist agent best suited for this task")
    context_required: Optional[str] = Field(default="", description="Specific data points needed from previous steps")

class ExecutionPlan(BaseModel):
    """The Master Plan generated to answer the user's question"""
    goal: str = Field(description="The user's high-level objective")
    tasks: List[Task] = Field(description="List of ordered tasks to execute")
    reasoning: str = Field(description="Why this plan was chosen")

class AgentResult(BaseModel):
    """The output from a worker agent"""
    task_id: int
    agent: str
    output: str
    timestamp: str

class FinalSynthesis(BaseModel):
    """The final response to the user"""
    answer: str = Field(description="The comprehensive answer")
    sources: List[str] = Field(description="List of sources or agents used")
    confidence_score: float = Field(description="Confidence 0-1", ge=0.0, le=1.0)
