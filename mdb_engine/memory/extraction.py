"""
Extraction Mixin for CognitiveMemoryService.

Provides LLM-based fact extraction (basic, categorized, and cognitive with emotion),
memory type detection, and intra-message deduplication.
"""

import json
import logging
from typing import Any

from .constants import CATEGORY_PRIORITY
from .embedding import cosine_similarity

try:
    from pydantic import BaseModel, Field

    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False
    BaseModel = None
    Field = None

from .base import MemoryServiceError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic models for structured LLM output
# ---------------------------------------------------------------------------

if PYDANTIC_AVAILABLE:

    class FactExtractionResponse(BaseModel):
        """Structured response for fact extraction (no categories)."""

        facts: list[str] = Field(
            description=("List of extracted facts as strings. " "Each fact should be standalone and meaningful.")
        )

    class CategorizedFact(BaseModel):
        """A fact with its category."""

        text: str = Field(description="The extracted fact about the user.")
        category: str = Field(
            description=(
                "Category of the fact. One of: biographical, preferences, "
                "temporal, relational. Every fact MUST be assigned to one of these categories."
            )
        )

    class CategorizedFactExtractionResponse(BaseModel):
        """Structured response for fact extraction with categories."""

        facts: list[CategorizedFact] = Field(
            description=("List of extracted facts with categories. " "Each fact should be standalone and meaningful.")
        )

    class CognitiveFact(BaseModel):
        """A fact with category, emotional intensity, and emotion type."""

        text: str = Field(description="The extracted fact about the user.")
        category: str = Field(
            description=(
                "Category of the fact. One of: biographical, preferences, "
                "temporal, relational. Every fact MUST be assigned to one of these categories."
            )
        )
        emotion: float = Field(
            ge=0.0,
            le=1.0,
            default=0.3,
            description=(
                "Emotional intensity of this fact (0.0 to 1.0). "
                "High values (>0.7) indicate significant life events, "
                "strong preferences, or impactful information. "
                "Low values (<0.3) indicate mundane facts."
            ),
        )
        emotion_type: str = Field(
            default="neutral",
            description=(
                "Type of emotional significance. One of: "
                "novelty (surprising, contradicts expectations, pattern-breaking), "
                "stakes (urgent, consequential, deadline-related, safety-critical), "
                "resonance (identity-connected, values-aligned, personally meaningful), "
                "neutral (routine, mundane, no special emotional charge)."
            ),
        )

    class CognitiveFactExtractionResponse(BaseModel):
        """Structured response for cognitive fact extraction with emotion tagging."""

        facts: list[CognitiveFact] = Field(
            description=(
                "List of extracted facts with categories and emotional intensity. "
                "Each fact should be standalone and meaningful."
            )
        )


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Memory categories
# Note: "general" is NOT a memory category - it's only used for bucket_type filtering
MEMORY_CATEGORIES = {
    "biographical": "Personal info: name, age, occupation, family, location, education",
    "preferences": "Likes, dislikes, preferences, brand loyalties, favorites",
    "temporal": "Current projects, deadlines, short-term goals, recent events",
    "relational": "Relationships, feelings about others, communication preferences",
    "procedural": "How-to workflows, code snippets, step-by-step instructions, recipes",
}

# Memory types (Cognitive Blueprint v2.0)
MEMORY_TYPES = {
    "semantic": "General knowledge, rules, facts (permanent)",
    "entity": "Structured facts about users/objects (permanent)",
    "episodic": "Historical logs, chat transcripts (1-2 year retention)",
    "working": "Active context, topic tracking (session-based, TTL)",
}


# Shared prompt template for memory-type classification (used by sync + async)
_MEMORY_TYPE_PROMPT = """Classify this memory into one of these types:
- semantic: General knowledge, rules, facts
  (e.g., "User prefers dark mode", "Python is a programming language")
- entity: Facts about specific people, projects, objects
  (e.g., "Project Phoenix is active", "Alice is the lead")
- procedural: How-to workflows, code snippets, step-by-step instructions
  (e.g., "To clean data: load CSV, drop nulls, export")
- episodic: Historical events, chat transcripts, temporal logs
  (e.g., "User said X yesterday", conversation logs)

Memory: {fact_text}

Return ONLY the type name (semantic, entity, procedural, or episodic)."""

_MEMORY_TYPE_SYSTEM = "You are a memory classification engine. Return only the type name."
_VALID_MEMORY_TYPES = frozenset({"semantic", "entity", "episodic", "procedural"})


# ---------------------------------------------------------------------------
# Mixin
# ---------------------------------------------------------------------------


class ExtractionMixin:
    """Mixin that provides fact extraction methods for CognitiveMemoryService."""

    async def _extract_facts(self, text: str) -> list[str]:
        """
        Uses LLM to extract atomic, de-duplicated facts from raw text.
        """
        logger.info(f"[Fact Extraction] Starting extraction for text ({len(text)} chars)")

        if not self.llm_available:
            logger.error(
                "[Fact Extraction] FAILED: No LLM client available. "
                "Returning empty - NO FALLBACK storage. "
                "This needs to be fixed - extraction cannot proceed without LLM."
            )
            # NO FALLBACK: Missing LLM is a failure, not a fallback opportunity
            return []

        system_prompt = (
            "You are a memory extraction engine. Your goal is to extract key facts "
            "from the user input that are worth remembering long-term.\n"
            "Rules:\n"
            "1. Extract distinct, standalone facts about the USER (their preferences, "
            "facts about them, etc.).\n"
            "2. Only extract DECLARATIVE STATEMENTS where the user reveals something "
            "about themselves. DO NOT extract questions, requests, or queries.\n"
            "3. If the user expresses a preference or fact about themselves "
            "(e.g., 'I love chocolate', 'I like pizza'), extract it as a fact.\n"
            "4. Return an EMPTY array if the input contains no extractable facts "
            "(questions, greetings, requests, meta-conversation).\n"
            "5. NEVER describe what the user is doing (e.g., 'User is asking about X'). "
            "Only extract what the user IS or LIKES/DISLIKES.\n\n"
            "Example Input: 'I love chocolate'\n"
            'Example Output: {"facts": ["User loves chocolate"]}\n'
            "Example Input: 'My name is John and I love coding in Python.'\n"
            'Example Output: {"facts": ["User\'s name is John", "User loves coding in Python"]}\n'
            "Example Input: 'Do I like vanilla?'\n"
            'Example Output: {"facts": []}\n'
            "Example Input: 'What did I say about pizza?'\n"
            'Example Output: {"facts": []}\n'
            "Example Input: 'Hello, how are you?'\n"
            'Example Output: {"facts": []}'
        )

        try:
            logger.info(f"[Fact Extraction] Using model: {self.memory_llm_model}")
            logger.info(f"[Fact Extraction] Calling LLM with text length: {len(text)}")

            if PYDANTIC_AVAILABLE:
                from ..llm.service import _format_response_format_for_provider

                formatted_response_format = _format_response_format_for_provider(
                    FactExtractionResponse, self.memory_llm_model
                )

                # Sanitize user text before sending to LLM for extraction
                try:
                    from ..core.prompt_safety import sanitize_for_prompt

                    safe_text = sanitize_for_prompt(text, tag="user_message")
                except ImportError:
                    safe_text = text

                response = await self._llm_completion(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": safe_text},
                    ],
                    model=self.memory_llm_model,
                    response_format=formatted_response_format,
                )

                content = response.choices[0].message.content
                logger.debug(f"[Fact Extraction] LLM response ({len(content) if content else 0} chars)")

                # Safely parse structured response
                # (handles markdown code blocks, empty responses, etc.)
                try:
                    from ..llm.service import _parse_structured_response

                    extraction_result = _parse_structured_response(content, FactExtractionResponse)
                    if isinstance(extraction_result, FactExtractionResponse):
                        facts = extraction_result.facts
                    else:
                        # Fallback: try direct parsing if helper returned dict
                        extraction_result = FactExtractionResponse.model_validate(extraction_result)
                        facts = extraction_result.facts
                except (ValueError, TypeError, AttributeError, KeyError) as e:
                    logger.warning(f"[Fact Extraction] Failed to parse response: {e}")
                    # Try direct JSON parsing as alternative format
                    try:
                        data = json.loads(content.strip())
                        facts = data.get("facts", [])
                    except (json.JSONDecodeError, AttributeError, KeyError) as parse_error:
                        logger.error(
                            f"[Fact Extraction] Complete parse FAILURE: {parse_error}. "
                            f"NO FALLBACK storage. This failure needs to be addressed.",
                            exc_info=True,
                        )
                        # NO FALLBACK: Parsing failures are explicit - return empty so we can fix the root cause
                        return []
            else:
                # Fallback to JSON mode if Pydantic not available
                response = await self._llm_completion(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": safe_text},
                    ],
                    model=self.memory_llm_model,
                    response_format={"type": "json_object"},
                )

                content = response.choices[0].message.content
                logger.debug(f"[Fact Extraction] LLM response ({len(content) if content else 0} chars)")

                data = json.loads(content)
                facts = data.get("facts", [])

            facts_count = len(facts) if isinstance(facts, list) else 0
            logger.info(f"[Fact Extraction] Parsed facts: {facts_count} facts")
            if facts:
                logger.debug(f"[Fact Extraction] First {min(len(facts), 3)} fact IDs logged at DEBUG")

            # Sanity check
            if not isinstance(facts, list):
                logger.error(
                    f"[Fact Extraction] FAILED: LLM returned 'facts' but not as a list. "
                    f"Returning empty - NO FALLBACK storage. "
                    f"This failure needs to be addressed. Expected list, got: {type(facts)}"
                )
                return []

            filtered_facts = [f for f in facts if isinstance(f, str) and f.strip()]
            logger.info(f"[Fact Extraction] Final result: {len(filtered_facts)} facts after filtering")

            if not filtered_facts:
                logger.info(
                    f"[Fact Extraction] No valid facts extracted ({len(text)} chars input). "
                    f"This is expected for questions or non-factual content."
                )
                return []

            return filtered_facts

        except (
            MemoryServiceError,
            json.JSONDecodeError,
            AttributeError,
            KeyError,
            TypeError,
            ValueError,
            RuntimeError,
            ConnectionError,
            OSError,
        ) as e:
            logger.error(
                f"[Fact Extraction] FAILED: {e}. " f"Returning empty - NO FALLBACK storage. ({len(text)} chars input)",
                exc_info=True,
            )
            # NO FALLBACK: Failures are explicit - return empty so we can fix the root cause
            return []

    async def _detect_memory_type(self, fact_text: str) -> str:
        """Classify memory type via ``_llm_completion``."""
        if not self.memory_types_enabled or not self.auto_detect_memory_type:
            return self.default_memory_type
        if not self.llm_available:
            return self.default_memory_type

        try:
            response = await self._llm_completion(
                messages=[
                    {"role": "system", "content": _MEMORY_TYPE_SYSTEM},
                    {"role": "user", "content": _MEMORY_TYPE_PROMPT.format(fact_text=fact_text)},
                ],
                temperature=0.0,
            )

            if hasattr(response, "choices") and response.choices:
                result = response.choices[0].message.content.strip().lower()
            else:
                result = str(response).strip().lower()

            if result in _VALID_MEMORY_TYPES:
                logger.debug(f"[Memory Type] Classified as: {result}")
                return result

            logger.warning(f"[Memory Type] Invalid type '{result}', using default")
            return self.default_memory_type

        except (AttributeError, RuntimeError, ValueError, TypeError, KeyError) as e:
            logger.warning(f"[Memory Type] Detection failed: {e}, using default")
            return self.default_memory_type

    async def _extract_facts_with_categories(self, text: str) -> list[dict[str, str]]:
        """
        Uses LLM to extract atomic facts with categories from raw text.

        Categories: biographical, preferences, temporal, relational.

        Returns:
            List of dicts with 'text' and 'category' keys
        """
        logger.info(f"[Categorized Extraction] Starting for text ({len(text)} chars)")

        if not self.llm_available or not PYDANTIC_AVAILABLE:
            logger.error(
                "[Categorized Extraction] FAILED: LLM/Pydantic not available. "
                "Returning empty - NO FALLBACK storage. "
                "This needs to be fixed - extraction cannot proceed without LLM."
            )
            # NO FALLBACK: Missing dependencies are failures, not fallback opportunities
            return []

        # Build category descriptions including custom categories
        category_descriptions = "\n".join(f"- {cat}: {desc}" for cat, desc in MEMORY_CATEGORIES.items())
        if self.custom_categories:
            for custom_cat in self.custom_categories:
                category_descriptions += f"\n- {custom_cat}: Custom category"

        system_prompt = f"""You are a memory extraction engine that extracts and categorizes facts.

Your goal is to extract key facts from the user input and assign each a category.

CATEGORIES:
{category_descriptions}

RULES:
1. Extract distinct, standalone facts about the USER (preferences, facts about them).
2. Only extract DECLARATIVE STATEMENTS where the user reveals something about themselves.
3. DO NOT extract questions, requests, or queries.
4. Return an EMPTY array if no extractable facts (greetings, questions, requests).
5. NEVER describe what the user is doing. Only extract what the user IS or
   LIKES/DISLIKES.
6. Assign the most appropriate category to each fact. Every fact MUST be
   assigned to one of: biographical, preferences, temporal, or relational.
   NEVER use "general" as a category.

Example Input: 'My name is John and I work at Google. I love pizza.'
Example Output: {{"facts": [
    {{"text": "User's name is John", "category": "biographical"}},
    {{"text": "User works at Google", "category": "biographical"}},
    {{"text": "User loves pizza", "category": "preferences"}}
]}}

Example Input: 'I'm working on a project due Friday and I prefer Slack for communication'
Example Output: {{"facts": [
    {{"text": "User has a project due Friday", "category": "temporal"}},
    {{"text": "User prefers Slack for communication", "category": "relational"}}
]}}

Example Input: 'My sister Emily is a doctor at Mayo Clinic and my '
               'brother-in-law David is a professor at MIT'
Example Output: {{"facts": [
    {{"text": "User's sister Emily is a doctor at Mayo Clinic", "category": "relational"}},
    {{"text": "User's brother-in-law David is a professor at MIT", "category": "relational"}}
]}}

Example Input: 'My family lives in Boston and we own a vacation home in Vermont'
Example Output: {{"facts": [
    {{"text": "User's family lives in Boston", "category": "relational"}},
    {{"text": "User's family owns a vacation home in Vermont", "category": "relational"}}
]}}

Example Input: 'Hello, how are you?'
Example Output: {{"facts": []}}"""

        try:
            logger.info(f"[Categorized Extraction] Using model: {self.memory_llm_model}")

            from ..llm.service import _format_response_format_for_provider

            response = await self._llm_completion(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
                model=self.memory_llm_model,
                response_format=_format_response_format_for_provider(
                    CategorizedFactExtractionResponse, self.memory_llm_model
                ),
            )

            content = response.choices[0].message.content
            logger.debug(f"[Categorized Extraction] Response ({len(content) if content else 0} chars)")

            # Safely parse structured response (handles markdown code blocks, empty responses, etc.)
            try:
                from ..llm.service import _parse_structured_response

                extraction_result = _parse_structured_response(content, CategorizedFactExtractionResponse)
                if not isinstance(extraction_result, CategorizedFactExtractionResponse):
                    # Fallback: try direct parsing if helper returned dict
                    extraction_result = CategorizedFactExtractionResponse.model_validate(extraction_result)
            except (ValueError, TypeError, AttributeError, KeyError) as e:
                logger.warning(f"[Categorized Extraction] Failed to parse response: {e}")
                raise

            # Convert to list of dicts
            categorized_facts = [
                {"text": f.text, "category": f.category} for f in extraction_result.facts if f.text.strip()
            ]

            logger.info(f"[Categorized Extraction] Extracted {len(categorized_facts)} facts")
            for i, fact in enumerate(categorized_facts[:3]):
                logger.info(f"  Fact {i+1}: [{fact['category']}] '{fact['text'][:50]}...'")

            if not categorized_facts and text.strip() and len(text.strip()) > 5:
                # NO FALLBACK: If extraction found 0 facts, return empty
                # This is CORRECT - questions, non-factual text should NOT be stored
                logger.info(
                    f"[Categorized Extraction] No facts extracted. "
                    f"This is expected for questions or non-factual content. "
                    f"Text preview: '{text[:100]}...'"
                )
                return []

            return categorized_facts

        except (
            MemoryServiceError,
            json.JSONDecodeError,
            AttributeError,
            KeyError,
            TypeError,
            ValueError,
            RuntimeError,
            ConnectionError,
            OSError,
        ) as e:
            logger.error(
                f"[Categorized Extraction] FAILED: {e}. "
                f"Returning empty - NO FALLBACK storage. "
                f"This failure needs to be addressed. Text preview: '{text[:100]}...'",
                exc_info=True,
            )
            # NO FALLBACK: Failures are explicit - return empty so we can fix the root cause
            return []

    async def _extract_facts_cognitive(self, text: str) -> list[dict[str, Any]]:
        """
        Extract facts with categories AND emotional intensity (Flashbulb Memory support).

        If a custom ``ExtractionStrategy`` has been injected, delegates to it.
        Otherwise falls through to the built-in LLM-based extraction.

        This is the full cognitive extraction that includes:
        - Fact text
        - Category (biographical, preferences, temporal, relational)
        - Emotion (0.0 to 1.0) - for Flashbulb Memory effect

        Emotion is tracked for metadata but doesn't affect recall (Perfect Recall mode).

        Returns:
            List of dicts with 'text', 'category', and 'emotion' keys
        """
        # Delegate to pluggable strategy if one was injected
        extraction_strategy = getattr(self, "_extraction_strategy", None)
        if extraction_strategy is not None:
            from .strategies import ExtractionContext

            ctx = ExtractionContext(
                app_slug=getattr(self, "app_slug", ""),
                categories_enabled=getattr(self, "categories_enabled", True),
                custom_categories=getattr(self, "custom_categories", []),
                memory_types_enabled=getattr(self, "memory_types_enabled", True),
                auto_detect_memory_type=getattr(self, "auto_detect_memory_type", True),
            )
            try:
                extracted = await extraction_strategy.extract(text, ctx)
                return [
                    {
                        "text": f.text,
                        "category": f.category,
                        "emotion": f.emotion,
                        "emotion_type": f.emotion_type,
                    }
                    for f in extracted
                ]
            except (RuntimeError, ValueError, TypeError, AttributeError, OSError, ConnectionError) as e:
                logger.warning(f"[Cognitive Extraction] Custom strategy failed, " f"falling through to built-in: {e}")

        logger.info(f"[Cognitive Extraction] Starting for text ({len(text)} chars)")

        if not self.llm_available or not PYDANTIC_AVAILABLE:
            logger.error(
                "[Cognitive Extraction] FAILED: LLM/Pydantic not available. "
                "Returning empty - NO FALLBACK storage. "
                "This needs to be fixed - extraction cannot proceed without LLM."
            )
            # NO FALLBACK: Missing dependencies are failures, not fallback opportunities
            return []

        # Build category descriptions including custom categories
        category_descriptions = "\n".join(f"- {cat}: {desc}" for cat, desc in MEMORY_CATEGORIES.items())
        if self.custom_categories:
            for custom_cat in self.custom_categories:
                category_descriptions += f"\n- {custom_cat}: Custom category"

        system_prompt = (
            "You are a cognitive memory extraction engine that extracts "
            "and analyzes facts.\n\n"
            "Your goal is to extract key facts from the user input, "
            "categorize them, and assess their emotional intensity.\n\n"
            f"CATEGORIES:\n{category_descriptions}\n\n"
            "EMOTION SCALE (0.0 to 1.0):\n"
            "- 0.0-0.2: Mundane facts (routine, trivial information)\n"
            "- 0.3-0.5: Moderately important (preferences, regular habits)\n"
            "- 0.6-0.7: Significant (career changes, important relationships)\n"
            "- 0.8-1.0: Highly emotional (life events, trauma, major achievements, "
            "strong feelings)\n\n"
            "EMOTION TYPE (classify the *kind* of emotional significance):\n"
            "- novelty: Surprising or pattern-breaking (career switches, contradictions, "
            "unexpected revelations)\n"
            "- stakes: Urgent or consequential (deadlines, safety/health, allergies, "
            "medical conditions, financial decisions)\n"
            "- resonance: Identity or values-connected (lifelong dreams, family bonds, "
            "deep personal preferences, core beliefs)\n"
            "- neutral: Routine facts with no special emotional charge\n\n"
            "RULES:\n"
            "1. Extract distinct, standalone facts about the USER.\n"
            "2. Only extract DECLARATIVE STATEMENTS where the user reveals something "
            "about themselves.\n"
            "3. DO NOT extract questions, requests, or queries.\n"
            "4. Return an EMPTY array if no extractable facts.\n"
            "5. NEVER describe what the user is doing - only extract what the user "
            "IS or LIKES/DISLIKES.\n"
            "6. Assign the most appropriate category to each fact. Every fact "
            "MUST be assigned to one of: biographical, preferences, temporal, "
            'or relational. NEVER use "general" as a category.\n'
            "7. Assess emotional intensity based on the language and content.\n"
            "8. Classify the emotion_type based on *why* the fact matters: "
            "novelty (surprising), stakes (urgent/safety), resonance (identity/values), "
            "or neutral.\n"
            "9. CRITICAL: If the input contains both 'User:' and 'AI:' sections, "
            "ONLY extract facts from the USER section. NEVER extract from AI responses, "
            "advice, recommendations, or explanations. AI responses are conversational "
            "and should NOT be stored as memories.\n\n"
            "EXAMPLES:\n\n"
            "Input: \"I just got promoted to Senior Engineer! I've been working "
            'towards this for 3 years."\n'
            'Output: {{"facts": [\n'
            '    {{"text": "User got promoted to Senior Engineer", "category": "biographical", '
            '"emotion": 0.9, "emotion_type": "novelty"}},\n'
            '    {{"text": "User has been working towards promotion for 3 years", '
            '"category": "temporal", "emotion": 0.7, "emotion_type": "resonance"}}\n'
            "]}}\n\n"
            'Input: "My grandmother passed away last week."\n'
            'Output: {{"facts": [\n'
            '    {{"text": "User\'s grandmother passed away recently", "category": "biographical", '
            '"emotion": 0.95, "emotion_type": "resonance"}}\n'
            "]}}\n\n"
            'Input: "I usually drink coffee in the morning."\n'
            'Output: {{"facts": [\n'
            '    {{"text": "User usually drinks coffee in the morning", "category": "preferences", '
            '"emotion": 0.2, "emotion_type": "neutral"}}\n'
            "]}}\n\n"
            'Input: "My daughter is allergic to peanuts and we have a deadline Friday."\n'
            'Output: {{"facts": [\n'
            '    {{"text": "User\'s daughter is allergic to peanuts", '
            '"category": "relational", '
            '"emotion": 0.9, "emotion_type": "stakes"}},\n'
            '    {{"text": "User has a deadline on Friday", '
            '"category": "temporal", '
            '"emotion": 0.6, "emotion_type": "stakes"}}\n'
            "]}}\n\n"
            'Input: "My sister Emily is a doctor at Mayo Clinic and my '
            'brother-in-law David is a professor at MIT."\n'
            'Output: {{"facts": [\n'
            '    {{"text": "User\'s sister Emily is a doctor at Mayo Clinic", '
            '"category": "relational", '
            '"emotion": 0.5, "emotion_type": "resonance"}},\n'
            '    {{"text": "User\'s brother-in-law David is a professor at '
            'MIT", "category": "relational", '
            '"emotion": 0.5, "emotion_type": "resonance"}}\n'
            "]}}\n\n"
            'Input: "My family lives in Boston and we enjoy skiing and jazz music."\n'
            'Output: {{"facts": [\n'
            '    {{"text": "User\'s family lives in Boston", '
            '"category": "relational", "emotion": 0.4, "emotion_type": "resonance"}},\n'
            '    {{"text": "User\'s family enjoys skiing and jazz music", '
            '"category": "relational", '
            '"emotion": 0.4, "emotion_type": "resonance"}}\n'
            "]}}\n\n"
            'Input: "Hello, how are you?"\n'
            'Output: {{"facts": []}}'
        )

        try:
            # Use fast extraction provider if configured, otherwise use default model
            if self.extraction_provider and self._injected_llm_service:
                logger.info(f"[Cognitive Extraction] Using fast extraction provider: {self.extraction_provider}")
                content = await self._injected_llm_service.chat_completion(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": text},
                    ],
                    provider_name=self.extraction_provider,
                    response_format={"type": "json_object"},
                    temperature=self._get_adjusted_temperature(self.memory_llm_model),
                )
            else:
                logger.info(f"[Cognitive Extraction] Using model: {self.memory_llm_model}")

                from ..llm.service import _format_response_format_for_provider

                response = await self._llm_completion(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": text},
                    ],
                    model=self.memory_llm_model,
                    response_format=_format_response_format_for_provider(
                        CognitiveFactExtractionResponse, self.memory_llm_model
                    ),
                )

                content = response.choices[0].message.content
            logger.debug(f"[Cognitive Extraction] Response ({len(content) if content else 0} chars)")

            # Safely parse structured response (handles markdown code blocks, empty responses, etc.)
            try:
                from ..llm.service import _parse_structured_response

                extraction_result = _parse_structured_response(content, CognitiveFactExtractionResponse)
                if not isinstance(extraction_result, CognitiveFactExtractionResponse):
                    # Fallback: try direct parsing if helper returned dict
                    extraction_result = CognitiveFactExtractionResponse.model_validate(extraction_result)
            except (ValueError, TypeError, AttributeError, KeyError) as e:
                logger.warning(f"[Cognitive Extraction] Failed to parse response: {e}")
                raise

            # Valid emotion types
            _valid_emotion_types = frozenset({"novelty", "stakes", "resonance", "neutral"})

            # Convert to list of dicts
            cognitive_facts = [
                {
                    "text": f.text,
                    "category": f.category,
                    "emotion": f.emotion,
                    "emotion_type": f.emotion_type if f.emotion_type in _valid_emotion_types else "neutral",
                }
                for f in extraction_result.facts
                if f.text.strip()
            ]

            logger.info(f"[Cognitive Extraction] Extracted {len(cognitive_facts)} facts")
            for i, fact in enumerate(cognitive_facts[:3]):
                logger.info(
                    f"  Fact {i+1}: [{fact['category']}] "
                    f"(emotion: {fact['emotion']:.2f}, type: {fact['emotion_type']}) "
                    f"'{fact['text'][:50]}...'"
                )

            # NO FALLBACKS: If extraction found 0 facts, return empty
            # This is CORRECT behavior - questions, AI responses, and non-factual text should NOT be stored
            if not cognitive_facts:
                logger.info(
                    f"[Cognitive Extraction] No facts extracted from text. "
                    f"This is expected for questions, AI responses, or non-factual content. "
                    f"Text preview: '{text[:100]}...'"
                )
                return []

            return cognitive_facts

        except (
            MemoryServiceError,
            json.JSONDecodeError,
            AttributeError,
            KeyError,
            TypeError,
            ValueError,
            RuntimeError,
            ConnectionError,
            OSError,
        ) as e:
            logger.error(
                f"[Cognitive Extraction] FAILED: {e}. "
                f"Returning empty - NO FALLBACK storage. "
                f"This failure needs to be addressed. Text preview: '{text[:100]}...'",
                exc_info=True,
            )
            # NO FALLBACK: Failures are explicit - return empty so we can fix the root cause
            return []

    async def _deduplicate_extracted_facts(
        self,
        facts: list[dict[str, Any]],
        similarity_threshold: float = 0.85,
    ) -> list[dict[str, Any]]:
        """
        Deduplicate facts extracted from the same message using embeddings.

        Prevents storing semantically duplicate facts like:
        - "User loves chocolate"
        - "The user's favorite candy is chocolate"

        When multiple similar facts are found, merge them by keeping:
        - The longer/richer text
        - The higher emotion score
        - The more specific category (biographical > preferences > temporal > relational)

        Args:
            facts: List of fact dicts with 'text', 'category', 'emotion'
            similarity_threshold: Cosine similarity threshold for deduplication (default: 0.85)

        Returns:
            Deduplicated list of facts with merged attributes
        """
        if len(facts) <= 1:
            return facts

        def get_category_priority(cat: str) -> int:
            return CATEGORY_PRIORITY.get(cat, 0)

        def merge_facts(existing: dict, new: dict) -> dict:
            """Merge two similar facts, keeping the best attributes."""
            # Keep longer/richer text
            if len(new["text"]) > len(existing["text"]):
                merged_text = new["text"]
            else:
                merged_text = existing["text"]

            # Keep higher emotion
            merged_emotion = max(existing.get("emotion", 0.3), new.get("emotion", 0.3))

            # Keep the emotion_type from the fact with higher emotion (more significant tag wins)
            if new.get("emotion", 0.3) >= existing.get("emotion", 0.3):
                merged_emotion_type = new.get("emotion_type", "neutral")
            else:
                merged_emotion_type = existing.get("emotion_type", "neutral")

            # Keep more specific category
            # If category missing, detect from text
            existing_cat = existing.get("category") or self._detect_category_from_text(existing.get("text", ""))
            new_cat = new.get("category") or self._detect_category_from_text(new.get("text", ""))
            if get_category_priority(new_cat) > get_category_priority(existing_cat):
                merged_category = new_cat
            else:
                merged_category = existing_cat

            return {
                "text": merged_text,
                "category": merged_category,
                "emotion": merged_emotion,
                "emotion_type": merged_emotion_type,
            }

        # Guard against O(n^2) blowup with very large fact lists
        max_pairwise = 200
        if len(facts) > max_pairwise:
            logger.warning(
                "[Dedup] Truncating %d facts to %d for pairwise deduplication",
                len(facts),
                max_pairwise,
            )
            facts = facts[:max_pairwise]

        logger.info(f"[Dedup] Starting deduplication of {len(facts)} extracted facts")

        # Generate embeddings for all facts in a SINGLE BATCH (much faster!)
        fact_texts = [fact["text"] for fact in facts]
        embeddings_map = await self._get_embeddings_batch(fact_texts)

        facts_with_embeddings = []
        for fact in facts:
            embedding = embeddings_map.get(fact["text"])
            if embedding:
                facts_with_embeddings.append(
                    {
                        **fact,
                        "_embedding": embedding,
                    }
                )
            else:
                logger.warning(f"[Dedup] Failed to get embedding for: '{fact['text'][:50]}...'")

        if not facts_with_embeddings:
            return facts  # Fallback to original if all embeddings failed

        # Deduplicate using embeddings
        deduplicated: list[dict[str, Any]] = []

        for fact in facts_with_embeddings:
            merged = False
            fact_embedding = fact["_embedding"]

            for i, existing in enumerate(deduplicated):
                existing_embedding = existing["_embedding"]
                similarity = cosine_similarity(fact_embedding, existing_embedding)

                if similarity > similarity_threshold:
                    # Merge into existing fact
                    merged_fact = merge_facts(existing, fact)
                    # Keep the embedding of the longer text
                    if len(fact["text"]) > len(existing["text"]):
                        merged_fact["_embedding"] = fact_embedding
                    else:
                        merged_fact["_embedding"] = existing_embedding

                    deduplicated[i] = merged_fact
                    merged = True
                    logger.info(
                        f"[Dedup] Merged (sim={similarity:.2f}): "
                        f"'{fact['text'][:30]}...' → '{merged_fact['text'][:30]}...'"
                    )
                    break

            if not merged:
                deduplicated.append(fact)

        # Remove internal embeddings before returning
        result = [{k: v for k, v in f.items() if k != "_embedding"} for f in deduplicated]

        if len(result) < len(facts):
            logger.info(f"[Dedup] Reduced {len(facts)} facts → {len(result)} unique facts")
        else:
            logger.info(f"[Dedup] No duplicates found in {len(facts)} facts")

        return result
