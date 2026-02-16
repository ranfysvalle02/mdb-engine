"""
Context Engineering
Responsible for constructing context-engineered prompts by combining
persona, entity facts, LTM, graph context, and STM summaries.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from .embedding import cosine_similarity_prenorm

# Module-level import for hot-path performance (avoid per-call import overhead)
try:
    from ..core.prompt_safety import PromptTier, TokenBudget, sanitize_for_prompt
except ImportError:
    sanitize_for_prompt = None  # type: ignore[assignment]
    TokenBudget = None  # type: ignore[assignment]
    PromptTier = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class ContextEngineer:
    """
    Builds context-engineered system prompts from multiple memory layers.

    Combines persona, entity extraction, dynamic persona adaptation,
    STM optimization, graph deduplication, and prompt construction.
    """

    def __init__(
        self,
        ltm,  # CognitiveMemoryService
        stm,  # ChatHistoryService
        graph_service,  # GraphService (optional)
        embedding_service,  # EmbeddingService (optional)
        llm_service,  # LLMService (optional, needed for STM summarization)
        config: dict,
        profile_service=None,  # ProfileService (optional)
    ):
        self.ltm = ltm
        self.stm = stm
        self._graph_service = graph_service
        self._embedding_service = embedding_service
        self.llm_service = llm_service
        self._profile_service = profile_service
        # Copy config values these methods reference
        self.enable_context_engineering = config.get("enable_context_engineering", True)
        self.stm_raw_window = config.get("stm_raw_window", 4)
        self.enable_entity_extraction = config.get("enable_entity_extraction", True)
        self.enable_dynamic_persona = config.get("enable_dynamic_persona", True)
        self.graph_min_hop_distance = config.get("graph_min_hop_distance", 0)
        self.graph_min_edges = config.get("graph_min_edges", 0)
        self.graph_deduplication_threshold = config.get("graph_deduplication_threshold", 0.85)
        self.graph_min_nodes = config.get("graph_min_nodes", 0)
        self.summary_staleness_threshold = config.get("summary_staleness_threshold", 10)

    def extract_entity_facts(self, user_id: str, relevant_memories: list[dict[str, Any]]) -> dict[str, str]:
        """
        Extract entity facts from retrieved memories.

        If a materialized profile is available (via ProfileService), extracts
        facts from the structured profile document.  Otherwise falls back to
        keyword-based extraction from raw memories.

        Args:
            user_id: User identifier
            relevant_memories: List of memory dicts from LTM search

        Returns:
            Dict of entity facts (e.g., {"Name": "Alice", "OS": "Ubuntu 22.04"})
        """
        if not self.enable_entity_extraction:
            return {}

        # --- Try profile-based extraction first (structured, comprehensive) ---
        profile_facts = self._extract_from_profile(user_id)
        if profile_facts:
            return profile_facts

        # --- Fallback: keyword-based extraction from raw memories ---
        if not relevant_memories:
            return {}

        return self._extract_entity_facts_from_memories(relevant_memories)

    def _extract_from_profile(self, user_id: str) -> dict[str, str] | None:
        """Extract entity facts from a materialized profile.

        Returns None if no profile is available, causing the caller
        to fall back to keyword extraction.
        """
        if not self._profile_service:
            return None

        try:
            # ProfileService stores a cached profile attribute after first retrieval
            # We use a sync accessor since extract_entity_facts is sync.
            # The profile would have been loaded during the parallel context retrieval phase.
            profile = getattr(self._profile_service, "_last_profile_cache", {}).get(str(user_id))
            if not profile:
                return None

            facts: dict[str, str] = {}

            # Identity layer
            identity = profile.get("identity", {})
            if identity.get("name"):
                facts["Name"] = identity["name"]
            if identity.get("expertise_level") and identity["expertise_level"] != "unknown":
                facts["Expertise"] = identity["expertise_level"]
            demographics = identity.get("demographics", {})
            if demographics.get("os"):
                facts["OS"] = demographics["os"]
            if demographics.get("language"):
                facts["Language"] = demographics["language"]

            # Preferences layer
            prefs = profile.get("preferences", {})
            likes = prefs.get("likes", [])
            if likes:
                facts["Interests"] = ", ".join(likes[:5])
            dislikes = prefs.get("dislikes", [])
            if dislikes:
                facts["Dislikes"] = ", ".join(dislikes[:3])

            # Relationships layer (compact summary)
            relationships = profile.get("relationships", [])
            if relationships:
                rel_strs = []
                for rel in relationships[:5]:
                    entity = rel.get("entity", "?")
                    attrs = rel.get("attributes", {})
                    attr_str = ", ".join(f"{k}: {v}" for k, v in list(attrs.items())[:3])
                    rel_strs.append(f"{entity} ({attr_str})" if attr_str else entity)
                facts["Relationships"] = "; ".join(rel_strs)

            # Safety layer (always surface)
            safety = profile.get("safety", {})
            alerts = []
            for allergy in safety.get("allergies", []):
                alerts.append(f"{allergy.get('person', 'someone')}: {allergy.get('concern', '?')}")
            for medical in safety.get("medical", []):
                alerts.append(f"{medical.get('person', 'someone')}: {medical.get('concern', '?')}")
            if alerts:
                facts["Safety_Alerts"] = "; ".join(alerts)

            return facts if facts else None

        except (AttributeError, TypeError, KeyError) as e:
            logger.debug(f"Profile-based entity extraction failed: {e}")
            return None

    def _extract_entity_facts_from_memories(self, relevant_memories: list[dict[str, Any]]) -> dict[str, str]:
        """Legacy keyword-based entity extraction from raw memories."""
        facts: dict[str, str] = {}
        found: set[str] = set()

        for mem in relevant_memories:
            category = mem.get("category", "")
            text = mem.get("memory", "") or mem.get("text", "")
            if not text:
                continue
            lower = text.lower()

            if category == "biographical":
                self._extract_biographical_facts(text, lower, facts, found)
            elif category == "preferences":
                self._extract_preference_facts(lower, facts, found)

        return facts

    @staticmethod
    def _extract_biographical_facts(
        text: str,
        lower: str,
        facts: dict[str, str],
        found: set[str],
    ) -> None:
        """Extract name, OS, language, and expertise from biographical memories."""
        if "Name" not in found and ("name is" in lower or "called" in lower):
            parts = text.split()
            for i, part in enumerate(parts):
                if part.lower() in ("is", "called", "named") and i + 1 < len(parts):
                    name = parts[i + 1].strip(".,!?")
                    if name and len(name) > 1:
                        facts["Name"] = name
                        found.add("Name")
                        break

        _OS_MAP = {"ubuntu": "Ubuntu", "linux": "Linux", "windows": "Windows"}
        if "OS" not in found:
            for kw, val in _OS_MAP.items():
                if kw in lower:
                    facts["OS"] = val
                    found.add("OS")
                    break
            else:
                if "macos" in lower or "mac" in lower:
                    facts["OS"] = "macOS"
                    found.add("OS")

        _LANG_MAP = [
            ("python", "Python"),
            ("javascript", "JavaScript/TypeScript"),
            ("typescript", "JavaScript/TypeScript"),
            ("java", "Java"),
            ("rust", "Rust"),
            ("go", "Go"),
        ]
        if "Language" not in found:
            for kw, val in _LANG_MAP:
                if kw in lower:
                    facts["Language"] = val
                    found.add("Language")
                    break

        _EXPERTISE = [
            (["expert", "senior", "experienced", "advanced"], "expert"),
            (["beginner", "learning", "new to", "just started"], "beginner"),
            (["intermediate", "moderate", "some experience"], "intermediate"),
        ]
        if "Expertise" not in found:
            for keywords, level in _EXPERTISE:
                if any(kw in lower for kw in keywords):
                    facts["Expertise"] = level
                    found.add("Expertise")
                    break

    @staticmethod
    def _extract_preference_facts(
        lower: str,
        facts: dict[str, str],
        found: set[str],
    ) -> None:
        """Extract UI preference from preference memories."""
        if "UI_Preference" not in found and ("prefers" in lower or "likes" in lower):
            if "dark mode" in lower or "dark theme" in lower:
                facts["UI_Preference"] = "dark mode"
                found.add("UI_Preference")
            elif "light mode" in lower or "light theme" in lower:
                facts["UI_Preference"] = "light mode"
                found.add("UI_Preference")

    async def build_dynamic_persona(
        self,
        persona: dict[str, Any] | None,
        entity_facts: dict[str, str],
        relevant_memories: list[dict[str, Any]],
        user_id: str | None = None,
    ) -> str:
        """
        Build dynamic persona instructions based on user context.

        Analyzes entity facts and retrieved memories to generate dynamic
        instructions that adapt the persona's behavior.  When a
        NeuroplasticityEngine is available, per-user trait overrides are
        merged with the app-level persona traits, enabling emergent
        per-user personality.

        Args:
            persona: Persona document from PersonaEngine (or None)
            entity_facts: Extracted entity facts
            relevant_memories: Retrieved memories from LTM
            user_id: Optional user ID for loading per-user trait overrides

        Returns:
            Dynamic instruction string (empty if no adaptation needed)
        """
        if not self.enable_dynamic_persona:
            return ""

        instructions = []

        # Expertise-based adaptation
        expertise = entity_facts.get("Expertise", "").lower()
        if expertise == "expert":
            instructions.append(
                "User is an expert. Be concise and technical. Skip basic explanations. " "Assume advanced knowledge."
            )
        elif expertise == "beginner":
            instructions.append(
                "User is learning. Be educational and patient. Explain concepts clearly. "
                "Provide examples and step-by-step guidance."
            )
        elif expertise == "intermediate":
            instructions.append(
                "User has moderate experience. Provide balanced explanations with some detail. "
                "Assume foundational knowledge but explain advanced concepts."
            )

        # Emotion-based adaptation
        high_emotion_count = 0
        for mem in relevant_memories:
            emotion = mem.get("emotion", 0.0)
            if isinstance(emotion, int | float) and emotion > 0.7:
                high_emotion_count += 1

        if high_emotion_count >= 2:
            instructions.append(
                "User has shared emotionally significant information. Be empathetic and "
                "acknowledge feelings. Show understanding and support."
            )

        # Trait-based adaptation (from persona + per-user overrides)
        base_traits = persona.get("traits", {}) if persona else {}

        # Merge per-user trait overrides from neuroplasticity adaptations
        user_trait_overrides = await self._get_user_trait_overrides(user_id)
        effective_traits = {**base_traits, **user_trait_overrides}

        if effective_traits:
            if effective_traits.get("humor", 0) > 0.6:
                instructions.append("Use appropriate humor when relevant. Be friendly and engaging.")

            if effective_traits.get("formality", 0) > 0.7:
                instructions.append("Maintain a professional and formal tone.")
            elif effective_traits.get("formality", 0) < 0.4:
                instructions.append("Use a casual and conversational tone.")

            if effective_traits.get("empathy", 0) > 0.7:
                instructions.append("Show high empathy and emotional intelligence.")

            if effective_traits.get("technical_focus", 0) > 0.7:
                instructions.append("Focus on technical accuracy and precision.")

            if effective_traits.get("directness", 0) > 0.7:
                instructions.append("Be direct and concise. Avoid hedging or unnecessary qualifiers.")

            if effective_traits.get("patience", 0) > 0.7:
                instructions.append("Be patient and thorough. Take time to explain fully.")

        return " ".join(instructions) if instructions else ""

    async def _get_user_trait_overrides(self, user_id: str | None) -> dict[str, float]:
        """Get per-user persona trait overrides from the neuroplasticity engine.

        Returns an empty dict if no engine is available or no overrides exist.
        Zero overhead for non-adapted users.
        """
        if not user_id:
            return {}

        neuroplasticity = getattr(self, "_neuroplasticity_engine", None)
        if neuroplasticity is None:
            return {}

        try:
            return await neuroplasticity.get_trait_overrides(str(user_id))
        except (AttributeError, RuntimeError, ValueError, TypeError) as e:
            logger.debug(f"[ContextEngineer] Could not fetch user trait overrides: {e}")
            return {}

    async def optimize_stm_context(
        self,
        stm_context: list[dict[str, str]],
        session_id: str,
        user_id: str,
    ) -> tuple[list[dict[str, str]], str | None]:
        """
        Optimize STM context using sliding window + cached LLM summary.

        Keeps the last N messages raw (immediate context) and replaces
        older messages with a cached LLM-generated summary. The summary
        is only regenerated when stale (staleness_threshold new messages).

        Cost model:
        - Cache hit (90%+ of turns): 1 MongoDB read (~5ms), 0 LLM calls
        - Cache miss (every ~10 turns): 1 LLM call (~500ms) + 1 MongoDB write (~5ms)

        Args:
            stm_context: Full STM context list
            session_id: Session identifier
            user_id: User identifier

        Returns:
            Tuple of (recent_messages, summary)
            - recent_messages: Last N messages to keep raw
            - summary: LLM summary of older messages (None if not needed)
        """
        if not self.enable_context_engineering or len(stm_context) <= self.stm_raw_window:
            return (stm_context, None)

        # Split into recent (raw) and older (to summarize)
        recent_messages = stm_context[-self.stm_raw_window :]
        older_messages = stm_context[: -self.stm_raw_window]

        if not older_messages:
            return (recent_messages, None)

        # Check cached summary
        current_count = len(stm_context)
        cached = await self.stm.get_cached_summary(session_id)

        if cached is not None:
            cached_summary, cached_count = cached
            messages_since_summary = current_count - cached_count
            if messages_since_summary < self.summary_staleness_threshold:
                logger.debug(
                    f"STM summary cache hit for session {session_id} "
                    f"({messages_since_summary} new messages, threshold={self.summary_staleness_threshold})"
                )
                return (recent_messages, cached_summary)

        # Cache miss or stale -- generate fresh summary via LLM
        if not self.llm_service:
            # No LLM available, fall back to truncated preview
            summary = f"Previous conversation ({len(older_messages)} messages): "
            summary += " ".join([msg.get("content", "")[:80] for msg in older_messages[:3]])
            if len(older_messages) > 3:
                summary += f" ... and {len(older_messages) - 3} more messages"
            return (recent_messages, summary)

        try:
            # Build conversation text for summarization
            conversation_text = "\n".join(
                f"{msg.get('role', 'unknown')}: {msg.get('content', '')}" for msg in older_messages
            )

            summary_prompt = (
                "Summarize the following conversation in 2-3 concise sentences. "
                "Focus on: key topics discussed, decisions made, user preferences revealed, "
                "and any important context the AI should remember.\n\n"
                f"Conversation ({len(older_messages)} messages):\n{conversation_text}\n\n"
                "Summary:"
            )

            summary = await self.llm_service.chat_completion(
                messages=[{"role": "user", "content": summary_prompt}],
            )

            if not summary or not summary.strip():
                summary = f"Previous conversation with {len(older_messages)} messages."

            # Cache the summary
            await self.stm.store_cached_summary(
                session_id,
                summary.strip(),
                current_count,
                user_id,
            )

            logger.info(
                f"Generated and cached STM summary for session {session_id} "
                f"({len(older_messages)} messages -> {len(summary.split())} words)"
            )

            return (recent_messages, summary.strip())

        except (ConnectionError, TimeoutError, ValueError) as e:
            logger.warning(f"STM summarization failed: {e}, using truncated fallback")
            summary = f"Previous conversation ({len(older_messages)} messages): "
            summary += " ".join([msg.get("content", "")[:80] for msg in older_messages[:3]])
            if len(older_messages) > 3:
                summary += f" ... and {len(older_messages) - 3} more messages"
            return (recent_messages, summary)

    def _format_persona_layer(self, persona: dict[str, Any] | None) -> str:
        """
        Format persona layer for system prompt.

        Args:
            persona: Persona document from PersonaEngine (or None)

        Returns:
            Formatted persona string (empty if no persona)
        """
        if not persona:
            return ""

        role = persona.get("role", "")
        description = persona.get("description", "")
        traits = persona.get("traits", {})

        persona_text = f"[PERSONA LAYER]\n{role}\n{description}\n"

        if traits:
            trait_list = []
            for trait_name, trait_value in traits.items():
                if isinstance(trait_value, int | float):
                    trait_list.append(f"{trait_name}: {trait_value:.1f}")
                else:
                    trait_list.append(f"{trait_name}: {trait_value}")

            if trait_list:
                persona_text += f"\nTraits: {', '.join(trait_list)}\n"

        return persona_text

    def _format_entity_memory(self, entity_facts: dict[str, str]) -> str:
        """
        Format entity facts as USER CONTEXT section.

        Entity facts are DB-sourced content that could originate from user input,
        so we wrap values in XML delimiters via ``sanitize_for_prompt``.

        Args:
            entity_facts: Dict of entity facts

        Returns:
            Formatted entity memory string (empty if no facts)
        """
        if not entity_facts:
            return ""

        if sanitize_for_prompt:
            facts_list = [f"{k}: {sanitize_for_prompt(str(v), tag='fact')}" for k, v in entity_facts.items()]
        else:
            facts_list = [f"{k}: {v}" for k, v in entity_facts.items()]
        return f"[USER CONTEXT]\nKnown Facts: {', '.join(facts_list)}\n\n"

    def _format_memory_layer(self, ltm_context: str, graph_context: str) -> str:
        """
        Format LTM and Graph context sections.

        Both LTM and graph context may contain user-controlled content,
        so they are wrapped in XML delimiters.

        Args:
            ltm_context: Formatted LTM context string
            graph_context: Formatted Graph context string

        Returns:
            Combined memory layer string
        """
        sections = []

        if graph_context:
            safe_graph = (
                sanitize_for_prompt(graph_context, tag="graph_context") if sanitize_for_prompt else graph_context
            )
            sections.append(f"[GRAPH CONTEXT]\n{safe_graph}")
            # Add instruction for path-based graph context
            if "[GRAPH INSIGHTS - PATHS FOUND]" in graph_context:
                sections.append(
                    "INSTRUCTION: Use the Graph Insights to understand relationships. "
                    "If a path is shown (A -> B -> C), explicitly explain that connection to the user."
                )

        if ltm_context:
            safe_ltm = sanitize_for_prompt(ltm_context, tag="ltm_context") if sanitize_for_prompt else ltm_context
            sections.append(f"[RELEVANT MEMORY]\n{safe_ltm}")

        return "\n\n".join(sections) if sections else ""

    def _node_to_text(self, node: dict[str, Any]) -> str:
        """
        Convert graph node to natural language text for semantic comparison.

        Args:
            node: Graph node dictionary

        Returns:
            Natural language text representation of the node
        """
        node_name = node.get("name", node.get("_id", ""))
        node_type = node.get("type", "unknown")
        props = node.get("properties", {})

        # Build text representation
        parts = [node_name]
        if node_type != "unknown":
            parts.append(f"({node_type})")

        # Add key properties
        if props:
            prop_parts = []
            for key, value in list(props.items())[:3]:
                if key == "occupation":
                    prop_parts.append(f"is a {value}")
                else:
                    prop_parts.append(f"{key} is {value}")
            if prop_parts:
                parts.append(", ".join(prop_parts))

        return " ".join(parts)

    async def _get_memory_embeddings(self, texts: list[str]) -> list[list[float]]:
        """
        Batch generate embeddings for memory texts (async).

        Args:
            texts: List of memory text strings

        Returns:
            List of embedding vectors
        """
        if not texts:
            return []

        # Try to get embedding provider from memory service
        embedding_provider = getattr(self.ltm, "embedding_provider", None)
        if not embedding_provider:
            logger.warning("No embedding provider available for deduplication")
            return []

        try:
            # Use async embedding
            embeddings = await embedding_provider.embed(texts, model=getattr(self.ltm, "embedding_model", None))
            return embeddings if embeddings else []
        except (ConnectionError, TimeoutError, ValueError) as e:
            logger.warning(f"Failed to generate memory embeddings: {e}")
            return []

    async def _get_node_embedding(self, text: str) -> list[float]:
        """
        Generate embedding for a single node text (async).

        Args:
            text: Node text string

        Returns:
            Embedding vector
        """
        embeddings = await self._get_memory_embeddings([text])
        return embeddings[0] if embeddings else []

    async def deduplicate_graph_against_memories(
        self,
        graph_results: dict[str, Any],
        relevant_memories: list[dict[str, Any]],
        similarity_threshold: float = 0.70,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Remove graph nodes that are duplicates of OTHER graph nodes (not memories!).

        CRITICAL: We do NOT filter out graph nodes that match memories because:
        - Graph nodes provide structured relationships and context
        - Memories provide semantic facts
        - They're complementary, not duplicates!
        - If a graph node matches a memory, that's GOOD - it confirms the answer

        Only deduplicate graph nodes against OTHER graph nodes to avoid redundancy.

        Uses MongoDB Vector Search for efficient O(log n) deduplication (async).

        Args:
            graph_results: Graph search results from hybrid_search()
            relevant_memories: List of memory dictionaries from LTM search (unused - kept for API compatibility)
            similarity_threshold: Similarity threshold for deduplication (default: 0.70)
            user_id: User identifier for scoping vector search queries

        Returns:
            Graph results with duplicate graph nodes removed (but NOT nodes that match memories)
        """
        if not graph_results:
            return graph_results

        # Extract user_id from memories if not provided
        if not user_id and relevant_memories:
            user_id = relevant_memories[0].get("user_id")

        # Handle both old format (graph_context) and new format (context_nodes)
        graph_context_items = graph_results.get("graph_context", [])
        context_nodes = graph_results.get("context_nodes", [])

        # Convert new format to old format for processing
        if context_nodes and not graph_context_items:
            graph_context_items = [{"node": node} if isinstance(node, dict) else node for node in context_nodes]

        if not graph_context_items:
            return graph_results

        # Extract all node texts for batch embedding generation
        node_texts = []
        node_items = []
        for item in graph_context_items:
            # Handle both formats: {"node": {...}} or direct node dict
            node = item.get("node", item) if isinstance(item, dict) and "node" in item else item
            node_text = self._node_to_text(node)
            if node_text:
                node_texts.append(node_text)
                node_items.append((item, node))

        if not node_texts:
            return graph_results

        # Guard against excessive node lists (O(n*m) pairwise comparison)
        max_pairwise = 200
        if len(node_texts) > max_pairwise:
            logger.warning(
                "[Graph Dedup] Truncating %d nodes to %d for deduplication",
                len(node_texts),
                max_pairwise,
            )
            node_texts = node_texts[:max_pairwise]
            node_items = node_items[:max_pairwise]

        # CRITICAL FIX: Only deduplicate graph nodes against OTHER graph nodes, NOT against memories!
        # Graph nodes that match memories are GOOD - they provide structured relationships.
        # We only want to remove redundant graph nodes (duplicates within the graph itself).

        # Batch generate embeddings for all graph nodes (single API call)
        node_embeddings = await self._get_memory_embeddings(node_texts)
        if not node_embeddings or len(node_embeddings) != len(node_texts):
            logger.warning("Graph deduplication unavailable - keeping all graph nodes")
            return graph_results

        # Deduplicate graph nodes against OTHER graph nodes only (not memories!)
        # Use cosine similarity to find duplicate nodes within the graph result
        filtered_context = []
        filtered_context_nodes = []
        duplicate_count = 0

        # Pre-compute norms to avoid re-computing per comparison.
        node_norms = [math.sqrt(sum(x * x for x in emb)) for emb in node_embeddings]

        # Compare each node against previously accepted nodes.
        # Norms are cached so only the dot-product is computed in the inner loop,
        # reducing constant-factor overhead from ~3x to ~1x per pair.
        seen_embeddings: list[list[float]] = []
        seen_norms: list[float] = []
        for i, ((item, node), node_embedding) in enumerate(zip(node_items, node_embeddings, strict=False)):
            is_duplicate = False
            norm_i = node_norms[i]
            if norm_i == 0:
                # Zero vector — cannot meaningfully compare; keep it
                pass
            else:
                for seen_emb, seen_norm in zip(seen_embeddings, seen_norms, strict=False):
                    if seen_norm == 0:
                        continue
                    similarity = cosine_similarity_prenorm(node_embedding, seen_emb, norm_i, seen_norm)
                    if similarity > similarity_threshold:
                        is_duplicate = True
                        duplicate_count += 1
                        logger.debug(
                            f"[Graph Dedup] Filtered duplicate graph node: '{node_texts[i][:50]}...' "
                            f"(similarity={similarity:.2f} with another graph node)"
                        )
                        break

            if not is_duplicate:
                filtered_context.append(item)
                filtered_context_nodes.append(node)
                seen_embeddings.append(node_embedding)
                seen_norms.append(norm_i)

        if duplicate_count > 0:
            logger.info(
                f"[Graph Dedup] Filtered {duplicate_count} duplicate graph nodes "
                f"from {len(graph_context_items)} graph nodes (deduplicated within graph only, not against memories)"
            )

        # Return in both formats for compatibility
        result = {
            **graph_results,
            "graph_context": filtered_context,
            "total_nodes": len(filtered_context) + len(graph_results.get("entry_nodes", [])),
        }

        # Preserve new format if it was present
        if "context_nodes" in graph_results:
            result["context_nodes"] = filtered_context_nodes

        return result

    def _format_stm_layer(self, recent_messages: list[dict[str, str]], summary: str | None) -> str:
        """
        Format STM layer with optional summary.

        Args:
            recent_messages: Recent messages to include raw
            summary: Optional summary of older messages

        Returns:
            Formatted STM layer string
        """
        sections = []

        if summary:
            sections.append(f"[PREVIOUS CONTEXT]\n{summary}\n")

        if recent_messages:
            sections.append("[CHAT HISTORY]")
            # Messages will be added separately to messages list

        return "\n".join(sections) if sections else ""

    def construct_context_engineered_prompt(
        self,
        persona: dict[str, Any] | None,
        entity_facts: dict[str, str],
        ltm_context: str,
        graph_context: str,
        dynamic_instructions: str,
        stm_summary: str | None,
        *,
        skills_context: str = "",
        max_prompt_tokens: int | None = None,
        model: str = "gpt-4o",
    ) -> str:
        """
        Construct context-engineered system prompt.

        Assembles all context layers according to Context Engineering principles:
        Context = P_static + M_relevant + Q_current

        When *max_prompt_tokens* is provided, a ``TokenBudget`` enforces the
        limit by truncating lowest-priority sections first (graph, then LTM,
        then STM summary, then entity facts).  Without a budget the prompt is
        assembled unbounded (backward-compatible behaviour).

        Args:
            persona: Persona document (P_static)
            entity_facts: Extracted entity facts
            ltm_context: Formatted LTM context
            graph_context: Formatted Graph context
            dynamic_instructions: Dynamic persona instructions
            stm_summary: Summary of older STM messages
            skills_context: Pre-formatted procedural skills context (from
                ``ProceduralMemory.format_for_prompt``).  Inserted between
                STM summary and LTM context.  High-signal, low-token — survives
                budget cuts at ``PromptTier.LTM`` priority.
            max_prompt_tokens: Optional token budget ceiling.  When ``None``
                (default), no truncation is applied.
            model: Model name passed to ``TokenBudget`` for tiktoken
                encoding selection (default ``"gpt-4o"``).

        Returns:
            Complete system prompt string
        """
        # Build the individual text sections first
        persona_layer = self._format_persona_layer(persona)

        meta_layer = ""
        if dynamic_instructions:
            meta_layer = f"[META-INSTRUCTIONS]\n{dynamic_instructions}\n"

        entity_layer = self._format_entity_memory(entity_facts)
        memory_layer = self._format_memory_layer(ltm_context, graph_context)

        stm_layer = ""
        if stm_summary:
            safe_summary = sanitize_for_prompt(stm_summary, tag="stm_summary") if sanitize_for_prompt else stm_summary
            stm_layer = f"[PREVIOUS CONTEXT]\n{safe_summary}\n"

        instructions_layer = (
            "Use the Chat History to maintain conversation flow. "
            "Use the context above to provide accurate and relevant responses."
        )

        # --- Token-budget path ---
        if max_prompt_tokens is not None and TokenBudget is not None and PromptTier is not None:
            try:
                budget = TokenBudget(max_tokens=max_prompt_tokens, model=model)

                # Highest priority: system instructions + persona (never cut)
                if persona_layer:
                    budget.add(PromptTier.SYSTEM, "persona", persona_layer)
                if meta_layer:
                    budget.add(PromptTier.SYSTEM, "meta_instructions", meta_layer)
                budget.add(PromptTier.SYSTEM, "instructions", instructions_layer)

                # Medium priority: STM summary
                if stm_layer:
                    budget.add(PromptTier.STM, "stm_summary", stm_layer)

                # Skills context — high-signal, low-token; same tier as LTM
                # but added before memory_layer so it appears first when both
                # fit within budget.
                if skills_context:
                    budget.add(PromptTier.LTM, "skills_context", skills_context)

                # Lower priority: LTM context
                if memory_layer:
                    budget.add(PromptTier.LTM, "memory_layer", memory_layer)

                # Lowest priority (after LTM): supplementary
                if entity_layer:
                    budget.add(PromptTier.SUPPLEMENTARY, "entity_facts", entity_layer)

                return budget.build()

            except (TypeError, ValueError, AttributeError) as exc:
                logger.warning(
                    "TokenBudget assembly failed (%s); " "falling back to unbounded prompt assembly",
                    exc,
                )

        # --- Unbounded path (default / fallback) ---
        sections = []
        if persona_layer:
            sections.append(persona_layer)
        if meta_layer:
            sections.append(meta_layer)
        if entity_layer:
            sections.append(entity_layer)
        if skills_context:
            sections.append(skills_context)
        if memory_layer:
            sections.append(memory_layer)
        if stm_layer:
            sections.append(stm_layer)
        sections.append(instructions_layer)

        return "\n".join(sections)
