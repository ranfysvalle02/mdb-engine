"""
LLM Prompts for GraphRAG Community Summaries and Graph Extraction.

This module contains prompts for generating community summaries at different
hierarchical levels following Microsoft Research's GraphRAG architecture,
and the graph extraction system/user prompts with optional OSI context injection.
"""

from __future__ import annotations

# Entity Summary Prompt (for individual nodes)
ENTITY_SUMMARY_PROMPT = """You are summarizing an entity from a knowledge graph.

Entity Information:
- ID: {entity_id}
- Type: {entity_type}
- Name: {entity_name}
- Properties: {properties}
- Relationships: {relationships}

Generate a concise summary (1-2 sentences) that captures:
1. What this entity is
2. Key characteristics or attributes
3. Important relationships

Summary:"""

# Local Community Summary Prompt
LOCAL_COMMUNITY_SUMMARY_PROMPT = """You are summarizing a local community from a knowledge graph.

A local community is a densely connected group of entities that share strong relationships.

Community Information:
- Size: {size} entities
- Entities: {entity_summaries}

Generate a comprehensive summary (2-3 sentences) that captures:
1. The theme or commonality that connects these entities
2. Key relationships and interactions within the community
3. What makes this community distinct

Summary:"""

# Regional Community Summary Prompt
REGIONAL_COMMUNITY_SUMMARY_PROMPT = """You are summarizing a regional community from a knowledge graph.

A regional community is a broader grouping that encompasses multiple local communities.

Regional Community Information:
- Size: {size} local communities
- Local Communities: {local_summaries}

Generate a comprehensive summary (3-4 sentences) that captures:
1. The overarching theme connecting these local communities
2. Patterns and relationships across the communities
3. Broader insights and themes

Summary:"""

# Global Community Summary Prompt
GLOBAL_COMMUNITY_SUMMARY_PROMPT = """You are summarizing a global community from a knowledge graph.

A global community represents the highest-level themes and patterns across the entire knowledge graph.

Global Community Information:
- Size: {size} regional communities
- Regional Communities: {regional_summaries}

Generate a comprehensive summary (4-5 sentences) that captures:
1. The highest-level themes and patterns
2. Major insights and relationships across the entire dataset
3. Holistic understanding of the knowledge domain

Summary:"""

# Community Summary System Prompt
COMMUNITY_SUMMARY_SYSTEM_PROMPT = """You are a knowledge graph summarization expert.
Your task is to generate concise, informative summaries of communities at different hierarchical levels.

Guidelines:
- Be specific and factual
- Focus on relationships and connections
- Highlight what makes each community unique
- Use clear, natural language
- Avoid redundancy

Return ONLY the summary text, no additional commentary."""


# ============================================================================
# Graph Extraction Prompts
# ============================================================================

# Default node types (matches GraphService.DEFAULT_NODE_TYPES + extras from prompt)
_DEFAULT_NODE_TYPES = [
    "person",
    "organization",
    "project",
    "event",
    "location",
    "concept",
    "document",
    "interest",
    "product",
]

_DEFAULT_RELATIONSHIP_TYPES = (
    "knows, likes, dislikes, loves, hates, works_at, lives_in, located_in, "
    "member_of, part_of, belongs_to, parent_of, child_of, sibling_of, spouse_of, friend_of, "
    "attended, participated_in, created, owns, interested_in, skilled_at, studies, "
    "allergic_to, has_condition, takes_medication, cares_for, manages, assigned_to"
)

# ---------------------------------------------------------------------------
# Definition-detection prompt fragment (appended when OSI is enabled)
# ---------------------------------------------------------------------------

_DEFINITION_DETECTION_BLOCK = """

DEFINITION DETECTION:
When a user DEFINES a concept with conditions or logic (signals: "X means...",
"X is when...", temporal constraints, thresholds), extract it as:
{{
  "id": "concept:<name>", "type": "concept", "name": "<Name>",
  "properties": {{
    "is_definition": true, "definition": "<text>", "created_by": "person:user",
    "status": "provisional",
    "extracted_logic": {{
      "entities": [...], "temporal_constraints": [...], "conditions": [...]
    }}
  }}
}}
For mentions (not definitions), extract as a normal concept node without extracted_logic."""


def build_extraction_system_prompt(
    osi_context: str | None = None,
    custom_node_types: list[str] | None = None,
    enable_definition_detection: bool = False,
) -> str:
    """Build the graph extraction system prompt, optionally enriched with OSI context.

    Args:
        osi_context: Formatted OSI semantic model context to append.
        custom_node_types: Additional node types from config or OSI models.
        enable_definition_detection: Whether to include the definition
            detection prompt block for OSI semantic discovery.

    Returns:
        Complete system prompt string.
    """
    if custom_node_types:
        all_types = list(dict.fromkeys(_DEFAULT_NODE_TYPES + custom_node_types))
    else:
        all_types = list(_DEFAULT_NODE_TYPES)
    types_str = ", ".join(all_types)

    prompt = f"""You are a Knowledge Graph extraction engine. Extract ALL entities and relationships.

RULES:
- Node ID format: `type:snake_case_name` (e.g., `person:alex_smith`). Types: {types_str}.
- Edge format: snake_case relation (e.g., `loves`, `parent_of`, `allergic_to`).
  Relation types: {_DEFAULT_RELATIONSHIP_TYPES}.
- "I"/"my" -> use `person:user` as source. "my daughter" -> also create `parent_of` edge.
- Extract EVERY relationship. Each sentence = at least 1 edge. Include implicit ones.

EXAMPLE:
Input: "I love chocolate. My daughter is allergic to peanuts. My son loves PB&J."
Output:
{{
  "nodes": [
    {{"id": "person:user", "type": "person", "name": "User", "properties": {{}}}},
    {{"id": "interest:chocolate", "type": "interest", "name": "Chocolate", "properties": {{}}}},
    {{
        "id": "family_member:users_daughter",
        "type": "family_member",
        "name": "User's Daughter",
        "properties": {{"relation": "daughter"}}
    }},
    {{
        "id": "allergy:peanuts",
        "type": "allergy",
        "name": "Peanut Allergy",
        "properties": {{"allergen": "peanuts", "severity": "unknown"}}
    }},
    {{
        "id": "family_member:users_son",
        "type": "family_member",
        "name": "User's Son",
        "properties": {{"relation": "son"}}
    }},
    {{"id": "interest:pbj", "type": "interest", "name": "PB&J", "properties": {{}}}}
  ],
  "edges": [
    {{"source": "person:user", "relation": "loves", "target": "interest:chocolate"}},
    {{"source": "person:user", "relation": "parent_of", "target": "family_member:users_daughter"}},
    {{"source": "family_member:users_daughter", "relation": "allergic_to", "target": "allergy:peanuts"}},
    {{"source": "person:user", "relation": "parent_of", "target": "family_member:users_son"}},
    {{"source": "family_member:users_son", "relation": "loves", "target": "interest:pbj"}}
  ]
}}

IMPORTANT: For family members (children, spouse, parents, siblings), prefer `family_member` over `person`.
For allergies, use `allergy` type. For medical issues, use `medical_condition`. For medicines, use `medication`.
Always use the most specific type available rather than generic `concept` or `person`."""

    if enable_definition_detection:
        prompt += _DEFINITION_DETECTION_BLOCK

    if osi_context:
        prompt += f"""

SEMANTIC MODELS (prefer these types and synonyms):
{osi_context}
Map entities to these dataset types. Use relationship definitions for edge creation."""

    return prompt


GRAPH_EXTRACTION_USER_PROMPT = """Extract ALL entities and ALL relationships from this text into a knowledge graph.
Every sentence = at least 1 edge. Include implicit relationships ("my daughter" = parent_of).

TEXT: {text}
USER_ID: {user_id}

Return ONLY valid JSON with "nodes" and "edges" arrays. Use "person:user" for "I"/"my"."""
