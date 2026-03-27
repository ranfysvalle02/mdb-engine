"""
Knowledge Conflict Detection

Standalone function for detecting contradictions between new facts and existing memories.
Extracted from CognitiveMemoryService.detect_knowledge_conflict for modularity.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from ..exceptions import (
    LLMAPIError as APIError,
)
from ..exceptions import (
    LLMAuthenticationError as AuthenticationError,
)
from ..exceptions import (
    LLMNotFoundError as NotFoundError,
)
from ..exceptions import (
    LLMRateLimitError as RateLimitError,
)
from ..llm.temperature import adjust_temperature_for_model

logger = logging.getLogger(__name__)


async def detect_knowledge_conflict(
    user_id: str,
    new_fact: str,
    similarity_threshold: float = 0.85,
    llm_model: str | None = None,
    *,
    get_embedding_fn: Callable[..., Coroutine[Any, Any, list[float] | None]],
    find_similar_fn: Callable[..., Coroutine[Any, Any, list[dict[str, Any]]]],
    llm_completion_fn: Callable[..., Coroutine[Any, Any, Any]],
    memory_llm_model: str,
) -> str | None:
    """
    Check if new information conflicts with existing knowledge.

    This implements the "Integrity Layer" that prevents the AI from developing
    "digital dementia" - holding contradictory facts as equally true.

    The check uses vector similarity to find related memories, then uses
    LLM reasoning to detect logical contradictions.

    Args:
        user_id: User ID to check conflicts for
        new_fact: The new fact/information to check
        similarity_threshold: Minimum similarity to consider related (default: 0.85)
        llm_model: Override LLM model for conflict detection
        get_embedding_fn: Async callable to generate embeddings
        find_similar_fn: Async callable to find similar memories
        llm_completion_fn: Async callable for LLM completions
        memory_llm_model: Default LLM model string for memory operations

    Returns:
        Conflict description if found, None if no conflict
    """
    try:
        # 1. Generate embedding for new fact
        fact_embedding = await get_embedding_fn(new_fact)
        if not fact_embedding:
            logger.warning("[Conflict Check] Could not generate embedding — skipping conflict detection")
            return None

        # 2. Find similar existing memories
        similar_memories = await find_similar_fn(
            user_id=user_id,
            embedding=fact_embedding,
            top_n=3,
        )

        if not similar_memories:
            return None

        # 3. Filter by similarity threshold
        relevant_memories = [m for m in similar_memories if m.get("similarity", 0) >= similarity_threshold]

        if not relevant_memories:
            return None

        # 4. Build context for LLM conflict check
        existing_context = "\n".join([f"- {m['memory']}" for m in relevant_memories])

        conflict_prompt = (
            "You are a logical consistency engine. Your job is to detect contradictions.\n\n"
            f"EXISTING KNOWLEDGE:\n{existing_context}\n\n"
            f"NEW INFORMATION:\n{new_fact}\n\n"
            "Does the 'NEW INFORMATION' logically contradict any of the "
            "'EXISTING KNOWLEDGE'?\n\n"
            "Rules:\n"
            "1. A contradiction means two statements cannot both be true at the same time.\n"
            "2. Updates to information are NOT contradictions (e.g., "
            '"User moved to NYC" doesn\'t contradict "User lives in LA" - it\'s an update).\n'
            "3. Different preferences at different times are NOT contradictions.\n"
            "4. Only flag clear logical contradictions.\n\n"
            "If you find a CONTRADICTION, explain it briefly in 1-2 sentences.\n"
            "If there is NO CONTRADICTION, respond with exactly: CLEAN"
        )

        # 5. Call LLM for consistency check
        model = llm_model or memory_llm_model

        # Prefer deterministic temperature for consistency checks.
        # Gemini still requires temperature=1.0.
        consistency_temp = adjust_temperature_for_model(
            model=model,
            requested_temperature=0,
            log=logger,
            enforce_openai_azure=False,
        )

        response = await llm_completion_fn(
            messages=[
                {"role": "system", "content": "You are a logical consistency engine."},
                {"role": "user", "content": conflict_prompt},
            ],
            model=model,
            temperature=consistency_temp,
        )

        result = response.choices[0].message.content.strip()

        # 6. Parse response
        if "CLEAN" in result.upper():
            logger.debug("[Conflict Check] No conflict detected")
            return None

        logger.warning(f"[Conflict Check] Conflict detected for: '{new_fact[:50]}...'\n" f"   Conflict: {result}")
        return result

    except (
        APIError,
        AuthenticationError,
        NotFoundError,
        RateLimitError,
        json.JSONDecodeError,
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
        RuntimeError,
        ConnectionError,
        OSError,
    ) as e:
        logger.error(f"[Conflict Check] Failed: {e}", exc_info=True)
        return None
