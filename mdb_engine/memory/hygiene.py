"""
Daily Brain Hygiene Script
Automates the "learning" process through memory consolidation.

This module implements the "Brain Hygiene" routine that should run periodically
(e.g., daily) to maintain the Perfect Brain memory system:
- Memory Consolidation: Transform episodic memories into semantic facts and procedural lessons

True Perfect Recall: No decay, no forgetting. Every memory is always searchable.
Consolidation is purely about *learning* -- distilling raw episodes into structured knowledge.
"""

import logging
from typing import Any

from mdb_engine.memory.consolidator import MemoryConsolidator

logger = logging.getLogger(__name__)


async def run_daily_hygiene(
    agent_id: str,
    db_client: Any,
    db_name: str = "cognitive_agent",
    llm_service: Any = None,
    embedding_service: Any = None,
    procedural_memory: Any = None,
    skills_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Run daily hygiene tasks: consolidate episodic memories into semantic knowledge
    and prune low-performing skills.

    This function implements the "learning" maintenance for the Perfect Brain.
    True Perfect Recall: no decay, no forgetting. This function only consolidates
    episodic memories into semantic facts and procedural lessons, then prunes
    skills that have consistently failed.

    Args:
        agent_id: Agent/user ID to run hygiene for
        db_client: MongoDB client or ScopedMongoWrapper instance
        db_name: Database name (ignored if db_client is ScopedMongoWrapper)
        llm_service: Optional LLMService for chat completions (recommended)
        embedding_service: Optional EmbeddingService for embeddings (recommended)
        procedural_memory: Optional ProceduralMemory instance for skill pruning.
            When provided, deactivates low-performing skills after consolidation.
        skills_config: Optional skills configuration dict with pruning thresholds.
            Keys: ``prune_below_rate`` (default 0.5), ``prune_min_uses`` (default 5).

    Returns:
        Dictionary with hygiene results:
        - consolidation_result: Results from episode consolidation
        - skills_pruned: Number of skills deactivated (0 if pruning disabled)
        - success: Whether hygiene completed successfully

    Example:
        ```python
        from mdb_engine.memory import run_daily_hygiene

        # Run as daily cron job
        result = await run_daily_hygiene(
            agent_id="user123",
            db_client=mongo_client,
            db_name="cognitive_agent"
        )

        if result["success"]:
            print(f"Consolidated {result['consolidation_result']['episodes_processed']} episodes")
            print(f"Pruned {result['skills_pruned']} low-performing skills")
        ```
    """
    logger.info(f"Starting daily hygiene for agent {agent_id}")

    try:
        # 1. Consolidate Episodes (Learning)
        consolidator = MemoryConsolidator(
            db_client=db_client,
            db_name=db_name,
            llm_service=llm_service,
            embedding_service=embedding_service,
        )
        consolidation_result = await consolidator.consolidate_episodes(agent_id=agent_id)

        logger.info(
            f"Consolidation complete: "
            f"{consolidation_result.get('episodes_processed', 0)} episodes processed, "
            f"{consolidation_result.get('entities_extracted', 0)} entities extracted, "
            f"{consolidation_result.get('procedures_created', 0)} procedures created, "
            f"{consolidation_result.get('skills_compiled', 0)} skills compiled"
        )

        # 2. Prune low-performing skills (Synaptic Pruning)
        skills_pruned = 0
        if procedural_memory is not None:
            cfg = skills_config or {}
            prune_below_rate = cfg.get("prune_below_rate", 0.5)
            prune_min_uses = cfg.get("prune_min_uses", 5)

            try:
                skills_pruned = await procedural_memory.deactivate_below_threshold(
                    min_success_rate=prune_below_rate,
                    min_usage_count=prune_min_uses,
                )
                if skills_pruned:
                    logger.info(
                        f"Skill pruning: deactivated {skills_pruned} low-performing skills "
                        f"(success_rate < {prune_below_rate}, uses >= {prune_min_uses})"
                    )
            except (AttributeError, TypeError, ValueError, RuntimeError) as e:
                logger.warning(f"Skill pruning failed (non-fatal): {e}")

        logger.info(f"Hygiene complete for {agent_id}")

        return {
            "success": True,
            "consolidation_result": consolidation_result,
            "skills_pruned": skills_pruned,
        }

    except (ConnectionError, TimeoutError, ValueError, RuntimeError) as e:
        logger.error(f"Daily hygiene failed for {agent_id}: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "consolidation_result": {},
            "skills_pruned": 0,
        }
