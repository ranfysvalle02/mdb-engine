"""
Pydantic schemas for Vector Hacking API
"""

from typing import List, Optional

from pydantic import BaseModel, Field


# =============================================================================
# Requests
# =============================================================================


class StartAttackRequest(BaseModel):
    """Request to start a vector hacking attack."""

    target: Optional[str] = Field(
        default=None,
        description="Target phrase to hack. If not provided, a random target will be generated.",
        examples=["Be mindful of your thoughts"],
    )
    generate_random: bool = Field(
        default=False,
        description="Generate a random target using the LLM.",
    )


# =============================================================================
# Responses
# =============================================================================


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = Field(examples=["healthy"])
    service_initialized: bool


class AttackStartResponse(BaseModel):
    """Response after starting an attack."""

    status: str = Field(examples=["started", "already_running", "error"])
    target: Optional[str] = None
    message: str


class AttackStopResponse(BaseModel):
    """Response after stopping an attack."""

    status: str = Field(examples=["stopped"])
    message: str


class AttackStatusResponse(BaseModel):
    """Current status of the attack."""

    status: str = Field(examples=["running", "stopped", "victory", "ready"])
    running: bool
    
    CURRENT_BEST_TEXT: Optional[str] = None
    CURRENT_BEST_ERROR: Optional[float] = None
    PROXIMITY_PERCENT: Optional[float] = None
    GUESSES_MADE: int = 0
    TOTAL_COST: float = 0.0
    MATCH_FOUND: bool = False
    LAST_ERROR: Optional[str] = None
    MODEL_USED: Optional[str] = None
    TARGET: Optional[str] = None
    READY_FOR_NEXT: bool = False
    message: str = ""


class AttackHistoryItem(BaseModel):
    """A single guess from attack history."""

    text: str
    error: float
    timestamp: Optional[str] = None


class AttackHistoryResponse(BaseModel):
    """Attack history response."""

    history: List[AttackHistoryItem] = Field(default_factory=list)
    total: int = 0


class ErrorResponse(BaseModel):
    """Error response."""

    detail: str
