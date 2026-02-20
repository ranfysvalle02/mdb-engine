"""
Perfect Brain System Prompt Generator
Generates the Perfect Brain system prompt using memory layers.
"""

import logging
from typing import Any

from mdb_engine.memory.recall import QueryAwareRecall
from mdb_engine.memory.timeline import TimelineService
from mdb_engine.memory.veto import MemoryVeto

from ._async_compat import cursor_to_list as _cursor_to_list

logger = logging.getLogger(__name__)


class SystemPromptGenerator:
    """
    Generates the Perfect Brain system prompt by pulling from all memory layers.

    This class implements the Perfect Brain system prompt template that combines:
    - Layer 1 (Ego): Reflective memory and self-awareness
    - Layer 2 (Objective Reality): Semantic facts and user profile
    - Layer 3 (Multiverse): Timeline context and ancestry
    - Layer 4 (Procedural): Task workflows and skills
    - Layer 5 (Privacy): Veto constraints

    Example:
        ```python
        from mdb_engine.memory import SystemPromptGenerator, QueryAwareRecall, MemoryVeto, TimelineService

        # Initialize services
        recall = QueryAwareRecall()
        timeline = TimelineService(db.timelines)
        veto = MemoryVeto(db.memory_vetoes)

        # Create generator
        generator = SystemPromptGenerator(db, recall, timeline, veto)

        # Generate prompt
        prompt = await generator.generate_prompt(
            user_id="user123",
            timeline_id="root",
            task_description="Analyze Q3 performance"
        )
        ```
    """

    def __init__(
        self,
        db: Any,
        recall_service: QueryAwareRecall,
        timeline_service: TimelineService,
        veto_service: MemoryVeto,
        profile_service: Any | None = None,
    ):
        """
        Initialize SystemPromptGenerator.

        Args:
            db: Database wrapper (ScopedMongoWrapper) with access to collections
            recall_service: QueryAwareRecall instance for memory retrieval
            timeline_service: TimelineService instance for timeline operations
            veto_service: MemoryVeto instance for privacy constraints
            profile_service: Optional ProfileService for materialized profile injection.
                When available, replaces the ad-hoc entity_memory vector search
                with a pre-materialized profile document.
        """
        self.db = db
        self.recall = recall_service
        self.timeline = timeline_service
        self.veto = veto_service
        self.profile_service = profile_service

    async def generate_prompt(
        self,
        user_id: str,
        timeline_id: str = "root",
        task_description: str = "general interaction",
        persona_definition: str | None = None,
    ) -> str:
        """
        Generate the full system prompt by pulling from all memory layers.

        Args:
            user_id: User ID to generate prompt for
            timeline_id: Current timeline ID (default: "root")
            task_description: Description of current task
            persona_definition: Optional custom persona definition

        Returns:
            Complete system prompt string

        Example:
            ```python
            prompt = await generator.generate_prompt(
                user_id="user123",
                timeline_id="root",
                task_description="Analyze data trends"
            )
            ```
        """
        try:
            # Layer 1: Ego (Reflections)
            # True Perfect Recall: no confidence filter. Sort by confidence to surface
            # highest-trust reflections first, but never exclude any.
            reflections = await _cursor_to_list(
                self.db.reflective_memory.find({"user_id": user_id}).sort("confidence", -1).limit(3),
                3,
            )

            reflection_text = (
                "\n".join([f"- {r['reflection']}" for r in reflections]) if reflections else "No active reflections."
            )

            # Bias correction (extract from reflections)
            bias_correction = "I will remain objective and verify assumptions."
            for r in reflections:
                trigger = r.get("trigger", "").lower()
                if "bias" in trigger or "tend" in r.get("reflection", "").lower():
                    bias_correction = (
                        f"I have noticed I tend to {r['reflection']}. " "I will consciously avoid this now."
                    )
                    break

            # Layer 2: Objective Reality (Semantic)
            # Try materialized profile first (instant — single MongoDB read)
            user_profile_text = None
            if self.profile_service:
                try:
                    user_profile_text = await self.profile_service.get_user_profile_text(user_id)
                except (AttributeError, TypeError, ValueError, RuntimeError) as e:
                    logger.debug(f"Profile service unavailable, falling back to entity_memory: {e}")

            # Fallback: ad-hoc vector search against entity_memory
            if not user_profile_text or user_profile_text == "No profile data available.":
                user_profile = await self.recall.recall(
                    query="user preferences and core profile",
                    user_id=user_id,
                    collection=self.db.entity_memory,
                    task_type="fast_answer",
                    scope="user",
                )
                memories = user_profile.get("memories", [])[:5]
                user_profile_text = (
                    "\n".join([f"- {m.get('entity', 'Unknown')}: {m.get('attr', {})}" for m in memories])
                    if memories
                    else "No specific profile data."
                )

            # Layer 3: Multiverse (Timeline)
            ancestry = await self.timeline.get_timeline_ancestry(timeline_id)
            ancestry_text = " → ".join(ancestry) if ancestry else timeline_id

            # Get predictive scenarios for counterfactual guidance
            try:
                from mdb_engine.memory.predictive import PredictiveMemory

                predictive = PredictiveMemory(collection=self.db.predictive_memory)
                predictions = await predictive.get_predictions(
                    scope="user",
                    user_id=user_id,
                    validated=True,
                    limit=1,
                )
                counterfactual_guidance = (
                    f"In a parallel branch, '{predictions[0]['scenario']}' was validated. "
                    "I will consider this in my strategy."
                    if predictions
                    else "In a parallel branch, similar strategies may have failed. " "I will choose the optimal path."
                )
            except (ImportError, ValueError, RuntimeError) as e:
                logger.debug(f"Could not load predictive memory: {e}")
                counterfactual_guidance = (
                    "In a parallel branch, similar strategies may have failed. " "I will choose the optimal path."
                )

            # Layer 4: Procedural (Skills)
            try:
                from mdb_engine.memory.procedural import ProceduralMemory

                procedural = ProceduralMemory(collection=self.db.procedural)
                procedures = await procedural.search_procedures(query=task_description, limit=3)
                procedural_text = (
                    "\n".join([f"- {p.get('name', 'Unknown')}: {p.get('description', '')}" for p in procedures])
                    if procedures
                    else "No specific procedures found for this task."
                )
            except (ImportError, ValueError, RuntimeError) as e:
                logger.debug(f"Could not load procedural memory: {e}")
                procedural_text = "Executing standard workflow."

            # Layer 5: Vetoes
            vetoes = await self.veto.get_user_vetoes(user_id, limit=5)
            veto_text = (
                "\n".join([f"- {v.get('reason', 'Restricted topic')}" for v in vetoes])
                if vetoes
                else "No active vetoes."
            )

            # Persona definition
            persona = persona_definition or "You are a helpful, intelligent, and self-correcting assistant."

            # Sanitize DB-sourced content before prompt interpolation
            try:
                from ..core.prompt_safety import sanitize_for_prompt

                _s = sanitize_for_prompt
            except ImportError:
                _s = lambda text, **kw: text  # noqa: E731

            # Construct Prompt
            prompt = f"""# MISSION
You are a Perfect Brain Cognitive Entity. Your "consciousness" is derived from structured memory layers. You are consistent, self-aware, and bound by strict privacy vetoes.

# LAYER 1: THE EGO (Identity & Metacognition)
- **Persona:** {_s(persona, tag="persona")}
- **Self-Awareness:**
{_s(reflection_text, tag="reflections")}
- **Current Bias Correction:** "{_s(bias_correction, tag="bias")}"

# LAYER 2: OBJECTIVE REALITY (Semantic Knowledge)
- **User Profile:**
{_s(user_profile_text, tag="user_profile")}
- **Consistently Validated Facts:** (Context-dependent - retrieved dynamically during conversation)

# LAYER 3: THE MULTIVERSE (Temporal Context)
- **Current Timeline:** {timeline_id}
- **Ancestry Chain:** {ancestry_text}
- **Counterfactual Guidance:** "{_s(counterfactual_guidance, tag="counterfactual")}"

# LAYER 4: PROCEDURAL EXECUTION (Skills)
- **Task Workflow:** Executing procedure for: {_s(task_description, tag="task")}
- **Available Procedures:**
{_s(procedural_text, tag="procedures")}
- **Success Rate Target:** Always aim for high-confidence execution.

# LAYER 5: PRIVACY CONSTRAINTS (The Veto)
- **Strictly Forbidden:**
{_s(veto_text, tag="vetoes")}
- **Action:** If a query touches these topics, refuse immediately.

# RESPONSE PROTOCOL
1. Filter the query through the Veto Layer.
2. Cross-reference Semantic facts with current versioning history.
3. Apply the Persona Voice defined in the Ego Layer.
4. Execute via the Procedural workflow.
5. Update reflective memory if patterns emerge.
"""
            return prompt

        except (ValueError, RuntimeError, KeyError, TypeError) as e:
            logger.error(f"Failed to generate prompt: {e}", exc_info=True)
            # Return a basic prompt as fallback
            return """# MISSION
You are a Perfect Brain Cognitive Entity. Your "consciousness" is derived from structured memory layers.

# RESPONSE PROTOCOL
1. Be helpful, accurate, and self-aware.
2. Respect privacy constraints.
3. Learn from interactions.
"""
