"""Profile document schemas for MDB-Engine.

Defines the structure of materialized user profiles and community
profiles stored in MongoDB.  These are TypedDicts (not Pydantic) to
stay consistent with the rest of MDB-Engine's type system.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, TypedDict

# ---------------------------------------------------------------------------
# User Profile
# ---------------------------------------------------------------------------


class IdentityDict(TypedDict, total=False):
    """Identity layer — synthesized from biographical memories."""

    name: str | None
    demographics: dict[str, Any]  # age, location, occupation, etc.
    expertise_level: str  # expert / intermediate / beginner
    personality_signals: list[str]  # observed traits


class PreferencesDict(TypedDict, total=False):
    """Preferences layer — synthesized from preference memories."""

    likes: list[str]
    dislikes: list[str]
    affinities: dict[str, float]  # topic → strength 0-1


class RelationshipDict(TypedDict, total=False):
    """A single relationship entry."""

    entity: str  # e.g. "son", "daughter", "wife"
    relation: str  # e.g. "parent_of", "married_to"
    attributes: dict[str, Any]  # e.g. {"likes": "cherry", "allergies": ["peanuts"]}


class ActiveContextDict(TypedDict, total=False):
    """Active context layer — from temporal memories."""

    current_goals: list[str]
    recent_topics: list[str]
    active_projects: list[str]


class SafetyAlertDict(TypedDict, total=False):
    """A single safety-critical alert."""

    person: str  # who it applies to (e.g. "daughter", "self")
    concern: str  # e.g. "peanut allergy"
    severity: str  # "critical" | "warning" | "info"


class SafetyDict(TypedDict, total=False):
    """Safety-critical layer — always surfaced regardless of recency."""

    allergies: list[SafetyAlertDict]
    medical: list[SafetyAlertDict]
    warnings: list[str]


class UserProfileDict(TypedDict, total=False):
    """Full user profile document stored in ``{app}_user_profiles``."""

    _id: Any  # ObjectId or str
    user_id: str
    app_slug: str

    # Cognitive layers
    identity: IdentityDict
    preferences: PreferencesDict
    relationships: list[RelationshipDict]
    active_context: ActiveContextDict
    safety: SafetyDict

    # LLM-generated narrative for direct prompt injection
    narrative: str

    # Metadata
    version: int
    memory_count_at_build: int
    last_full_rebuild: datetime | None
    last_incremental: datetime | None
    source_memory_ids: list[str]
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Community Profile
# ---------------------------------------------------------------------------


class PopulationDict(TypedDict, total=False):
    """Population statistics for the app."""

    total_users: int
    active_users_30d: int


class InterestStatDict(TypedDict, total=False):
    """A single interest with usage statistics."""

    item: str
    user_count: int
    pct: float  # 0.0 - 1.0


class CommonPreferencesDict(TypedDict, total=False):
    """Aggregated anonymous preference statistics."""

    top_interests: list[InterestStatDict]
    trending: list[InterestStatDict]


class EntityStatDict(TypedDict, total=False):
    """A popular entity from the knowledge graph."""

    name: str
    type: str  # node type
    user_count: int


class RelationPatternDict(TypedDict, total=False):
    """A common relationship pattern in the graph."""

    source_type: str
    relation: str
    target_type: str
    count: int


class SharedKnowledgeDict(TypedDict, total=False):
    """Shared knowledge extracted from the graph."""

    popular_entities: list[EntityStatDict]
    common_relations: list[RelationPatternDict]


class MemoryLandscapeDict(TypedDict, total=False):
    """Statistical landscape of memories across users."""

    avg_memories_per_user: float
    category_distribution: dict[str, float]  # category → proportion


class CommunityProfileDict(TypedDict, total=False):
    """Community profile document stored in ``{app}_community_profile``."""

    _id: Any
    app_slug: str

    population: PopulationDict
    common_preferences: CommonPreferencesDict
    shared_knowledge: SharedKnowledgeDict
    memory_landscape: MemoryLandscapeDict

    # Metadata
    version: int
    last_rebuild: datetime | None
    user_count_at_build: int
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Profile Config (TypedDict for manifest integration)
# ---------------------------------------------------------------------------


class UserProfileConfigDict(TypedDict, total=False):
    """User profile configuration in manifest."""

    enabled: bool
    rebuild_interval_hours: int
    incremental_on_memory_add: bool
    collection_name: str


class CommunityProfileConfigDict(TypedDict, total=False):
    """Community profile configuration in manifest."""

    enabled: bool
    rebuild_interval_hours: int
    min_users_for_aggregation: int
    collection_name: str


class ProfileConfigDict(TypedDict, total=False):
    """Top-level profile configuration for manifest."""

    enabled: bool
    user_profiles: UserProfileConfigDict
    community_profile: CommunityProfileConfigDict
