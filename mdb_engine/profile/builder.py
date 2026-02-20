"""Profile builder — LLM-powered synthesis of user profiles.

Handles both **full rebuild** (from all memories + graph) and
**incremental update** (from newly-stored memories only).

The builder treats each extraction as a structured JSON task:
the LLM receives memories and graph context and returns a
JSON profile object that maps directly to ``UserProfileDict``.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

FULL_BUILD_SYSTEM_PROMPT = """\
You are a profile synthesis engine. Given a user's memories and knowledge-graph \
relationships, produce a single JSON object that captures everything known about \
this user.

Return ONLY valid JSON with this exact structure (omit keys with no data):

{
  "identity": {
    "name": "<name or null>",
    "demographics": {"<key>": "<value>"},
    "expertise_level": "<expert|intermediate|beginner|unknown>",
    "personality_signals": ["<trait>"]
  },
  "preferences": {
    "likes": ["<item>"],
    "dislikes": ["<item>"],
    "affinities": {"<topic>": <0.0-1.0>}
  },
  "relationships": [
    {
      "entity": "<person/thing>",
      "relation": "<relation_type>",
      "attributes": {"<key>": "<value>"}
    }
  ],
  "active_context": {
    "current_goals": ["<goal>"],
    "recent_topics": ["<topic>"],
    "active_projects": ["<project>"]
  },
  "safety": {
    "allergies": [{"person": "<who>", "concern": "<allergen>", "severity": "critical"}],
    "medical": [{"person": "<who>", "concern": "<condition>", "severity": "<level>"}],
    "warnings": ["<free-text warning>"]
  },
  "narrative": "<2-4 sentence natural-language summary of who this user is, \
their key relationships, preferences, and any safety-critical information>"
}

Rules:
- Extract ALL factual information from the memories and graph context.
- The "narrative" must mention safety-critical facts (allergies, medical).
- Relationships should capture family, friends, pets, colleagues — anyone mentioned.
- Affinities are 0.0-1.0 strength scores based on how strongly the user expressed preference.
- Do NOT invent information not present in the source data.
- If a field has no data, omit the key entirely.
"""

INCREMENTAL_SYSTEM_PROMPT = """\
You are a profile update engine. You will receive:
1. An EXISTING user profile (JSON)
2. NEW memories that were just stored

Your job is to MERGE the new information into the existing profile.

Return the COMPLETE updated profile as valid JSON with the same structure.

Rules:
- Preserve ALL existing data unless contradicted by new information.
- If new data contradicts old data, prefer the new data (it's more recent).
- Add new preferences, relationships, safety info as discovered.
- Update the "narrative" to reflect the complete picture.
- Do NOT remove existing data unless it's explicitly contradicted.
- Do NOT invent information not in the source data.
"""


def _strip_markdown_fences(text: str) -> str:
    """Remove markdown code fences from LLM response."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def _safe_parse_json(raw: str) -> dict[str, Any]:
    """Parse JSON from LLM response, handling common issues."""
    raw = _strip_markdown_fences(raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract JSON object from response
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise


# ---------------------------------------------------------------------------
# Full Build
# ---------------------------------------------------------------------------


async def build_profile_from_memories(
    memories: list[dict[str, Any]],
    graph_nodes: list[dict[str, Any]] | None,
    llm_service: Any,
    user_id: str,
    llm_model: str | None = None,
) -> dict[str, Any]:
    """Build a complete user profile from all memories and graph nodes.

    Args:
        memories: All user memories (from ``memory_service.get_all``).
        graph_nodes: User's graph nodes (from ``graph_service.list_nodes``).
            Can be None if graph service is unavailable.
        llm_service: LLM service for synthesis.
        user_id: User ID for context.
        llm_model: Optional model override.

    Returns:
        Structured profile dict matching ``UserProfileDict`` cognitive layers.
    """
    if not memories:
        return _empty_profile(user_id)

    # Format memories as context
    memory_lines = []
    for mem in memories:
        text = mem.get("memory", "") or mem.get("text", "")
        category = mem.get("category", "") or mem.get("metadata", {}).get("category", "")
        importance = mem.get("importance", 0.5)
        if text:
            prefix = f"[{category}|imp={importance:.1f}]" if category else ""
            memory_lines.append(f"{prefix} {text}")

    memory_context = "\n".join(memory_lines[:200])  # Cap at 200 memories

    # Format graph context
    graph_context = ""
    if graph_nodes:
        graph_lines = []
        for node in graph_nodes[:50]:  # Cap at 50 nodes
            name = node.get("name", node.get("_id", "unknown"))
            node_type = node.get("type", "unknown")
            edges = node.get("edges", [])
            edge_strs = []
            for edge in edges[:5]:
                if edge.get("active", True):
                    edge_strs.append(f"{edge.get('relation', 'related')} -> {edge.get('target', '?')}")
            connections = ", ".join(edge_strs) if edge_strs else "no connections"
            graph_lines.append(f"- {name} ({node_type}): {connections}")
        graph_context = "\nKNOWLEDGE GRAPH:\n" + "\n".join(graph_lines)

    user_prompt = f"MEMORIES:\n{memory_context}{graph_context}"

    # Get provider
    try:
        provider_name = "extraction" if "extraction" in llm_service.providers else "chat"
        provider = llm_service.get_provider(provider_name)
        model = llm_model or provider.default_model
    except (AttributeError, KeyError):
        logger.warning("Could not get LLM provider for profile build")
        return _empty_profile(user_id)

    try:
        response = await llm_service.chat_completion(
            provider_name=provider_name,
            messages=[
                {"role": "system", "content": FULL_BUILD_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            model=model,
            response_format={"type": "json_object"},
            temperature=0.0,
        )

        profile_data = _safe_parse_json(response)
        logger.info(
            f"[Profile] Full build complete for user {user_id}: "
            f"{len(memories)} memories, {len(graph_nodes or [])} graph nodes"
        )
        return profile_data

    except (json.JSONDecodeError, ValueError, TypeError, RuntimeError) as e:
        logger.warning(f"[Profile] Full build LLM extraction failed: {e}")
        return _empty_profile(user_id)


# ---------------------------------------------------------------------------
# Incremental Update
# ---------------------------------------------------------------------------


async def incremental_update_profile(
    existing_profile: dict[str, Any],
    new_memories: list[dict[str, Any]],
    llm_service: Any,
    user_id: str,
    llm_model: str | None = None,
) -> dict[str, Any]:
    """Incrementally update an existing profile with new memories.

    This is cheaper than a full rebuild — only the new memories are
    sent alongside the existing profile for merging.

    Args:
        existing_profile: Current profile document.
        new_memories: Newly-stored memories to merge.
        llm_service: LLM service.
        user_id: User ID.
        llm_model: Optional model override.

    Returns:
        Updated profile dict with new information merged in.
    """
    if not new_memories:
        return existing_profile

    # Extract the cognitive layers from existing profile for context
    existing_data = {
        k: v
        for k, v in existing_profile.items()
        if k in ("identity", "preferences", "relationships", "active_context", "safety", "narrative")
    }

    # Format new memories
    memory_lines = []
    for mem in new_memories:
        text = mem.get("memory", "") or mem.get("text", "")
        if text:
            category = mem.get("category", "") or mem.get("metadata", {}).get("category", "")
            prefix = f"[{category}]" if category else ""
            memory_lines.append(f"{prefix} {text}")

    user_prompt = (
        f"EXISTING PROFILE:\n{json.dumps(existing_data, default=str, indent=2)}\n\n"
        f"NEW MEMORIES:\n" + "\n".join(memory_lines)
    )

    try:
        provider_name = "extraction" if "extraction" in llm_service.providers else "chat"
        provider = llm_service.get_provider(provider_name)
        model = llm_model or provider.default_model
    except (AttributeError, KeyError):
        logger.warning("Could not get LLM provider for profile update")
        return existing_profile

    try:
        response = await llm_service.chat_completion(
            provider_name=provider_name,
            messages=[
                {"role": "system", "content": INCREMENTAL_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            model=model,
            response_format={"type": "json_object"},
            temperature=0.0,
        )

        updated_data = _safe_parse_json(response)
        logger.info(f"[Profile] Incremental update for user {user_id}: merged {len(new_memories)} new memories")
        return updated_data

    except (json.JSONDecodeError, ValueError, TypeError, RuntimeError) as e:
        logger.warning(f"[Profile] Incremental update failed: {e}")
        return existing_profile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _empty_profile(user_id: str) -> dict[str, Any]:
    """Return a minimal empty profile structure."""
    return {
        "identity": {"name": None, "demographics": {}, "expertise_level": "unknown", "personality_signals": []},
        "preferences": {"likes": [], "dislikes": [], "affinities": {}},
        "relationships": [],
        "active_context": {"current_goals": [], "recent_topics": [], "active_projects": []},
        "safety": {"allergies": [], "medical": [], "warnings": []},
        "narrative": "",
    }


def format_profile_for_prompt(profile: dict[str, Any]) -> str:
    """Format a user profile for injection into an LLM system prompt.

    Produces a concise text block suitable for the ``[USER PROFILE]``
    section of a conversation system prompt.

    Args:
        profile: Profile document (or its cognitive layers).

    Returns:
        Formatted string for prompt injection.
    """
    sections = []

    # Narrative is the most concise representation
    narrative = profile.get("narrative", "")
    if narrative:
        sections.append(narrative)

    # Always surface safety concerns prominently
    safety = profile.get("safety", {})
    alerts = []
    for allergy in safety.get("allergies", []):
        person = allergy.get("person", "user")
        concern = allergy.get("concern", "unknown")
        alerts.append(f"ALLERGY: {person} — {concern}")
    for medical in safety.get("medical", []):
        person = medical.get("person", "user")
        concern = medical.get("concern", "unknown")
        alerts.append(f"MEDICAL: {person} — {concern}")
    for warning in safety.get("warnings", []):
        alerts.append(f"WARNING: {warning}")

    if alerts:
        sections.append("SAFETY ALERTS:\n" + "\n".join(f"  ⚠ {a}" for a in alerts))

    return "\n\n".join(sections) if sections else "No profile data available."


def format_community_for_prompt(community: dict[str, Any]) -> str:
    """Format a community profile for injection into an LLM system prompt.

    Args:
        community: Community profile document.

    Returns:
        Formatted string for prompt injection, or empty string if no data.
    """
    if not community:
        return ""

    sections = []

    population = community.get("population", {})
    total = population.get("total_users", 0)
    if total > 0:
        sections.append(f"Community: {total} users")

    prefs = community.get("common_preferences", {})
    top_interests = prefs.get("top_interests", [])[:5]
    if top_interests:
        items = [f"{i['item']} ({i['pct']:.0%})" for i in top_interests if i.get("pct")]
        if items:
            sections.append(f"Popular interests: {', '.join(items)}")

    return " | ".join(sections) if sections else ""
