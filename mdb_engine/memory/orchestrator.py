"""
Memory Orchestrator
Integrates Short-Term Memory (Chat History) and Long-Term Memory (Vector Store).

This module provides a complete cognitive architecture:
- Short-Term Memory (STM): Raw chat history for immediate context
- Long-Term Memory (LTM): Vector store for semantic retrieval of facts
- Cognitive Engine: Orchestrates both to provide context-aware responses
- LLM Provider Abstraction: Flexible support for multiple LLM providers (OpenAI, Gemini, etc.)
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from .base import BaseMemoryService
from .cognitive import CognitiveMemoryService, CognitiveMemoryServiceError

try:
    from pymongo import ASCENDING, DESCENDING
    from pymongo.errors import PyMongoError
except ImportError:
    raise ImportError("pip install pymongo") from None

logger = logging.getLogger(__name__)


# ============================================================================
# LLM Provider Abstraction
# ============================================================================


class LLMProvider(ABC):
    """
    Abstract base class for LLM providers.

    This abstraction allows CognitiveEngine to work with any LLM provider
    (OpenAI, Azure OpenAI, Google Gemini, Anthropic Claude, etc.) by implementing
    a simple, consistent interface.
    """

    @abstractmethod
    def generate_chat_completion(
        self, messages: list[dict[str, str]], model: str | None = None, **kwargs
    ) -> str:
        """
        Generate a chat completion response.

        Args:
            messages: List of message dicts with 'role' and 'content' keys.
                     Format: [{"role": "system", "content": "..."},
                              {"role": "user", "content": "..."}, ...]
            model: Optional model name/identifier (provider-specific)
            **kwargs: Additional provider-specific parameters

        Returns:
            str: The generated response text

        Raises:
            Exception: If the LLM call fails
        """
        pass


class OpenAIProvider(LLMProvider):
    """
    Provider for OpenAI and Azure OpenAI.

    Supports both standard OpenAI and Azure OpenAI clients.
    """

    def __init__(self, client):
        """
        Initialize OpenAI provider.

        Args:
            client: OpenAI or AzureOpenAI client instance
        """
        self.client = client
        self.is_azure = "Azure" in str(type(client))

    def generate_chat_completion(
        self, messages: list[dict[str, str]], model: str | None = None, **kwargs
    ) -> str:
        """Generate chat completion using OpenAI/Azure OpenAI."""
        if self.is_azure:
            # Azure OpenAI uses deployment name
            deployment_name = kwargs.get("deployment_name", model or "gpt-4o")
            response = self.client.chat.completions.create(
                model=deployment_name,
                messages=messages,
                **{k: v for k, v in kwargs.items() if k not in ["model", "deployment_name"]},
            )
        else:
            # Standard OpenAI uses model name
            model_name = model or kwargs.get("model", "gpt-4o")
            response = self.client.chat.completions.create(
                model=model_name,
                messages=messages,
                **{k: v for k, v in kwargs.items() if k != "model"},
            )

        return response.choices[0].message.content


class GeminiProvider(LLMProvider):
    """
    Provider for Google Gemini.

    Converts OpenAI message format to Gemini format and handles the Gemini API.
    Supports both single-turn and multi-turn conversations.
    """

    def __init__(self, gemini_client, default_model: str = "gemini-3-flash-preview"):
        """
        Initialize Gemini provider.

        Args:
            gemini_client: genai.Client instance from google.genai
            default_model: Default model name (e.g., "gemini-3-flash-preview")
        """
        self.client = gemini_client
        self.default_model = default_model

    def generate_chat_completion(
        self, messages: list[dict[str, str]], model: str | None = None, **kwargs
    ) -> str:
        """
        Generate chat completion using Google Gemini.

        Converts OpenAI-style messages to Gemini format:
        - System messages are prepended to the prompt
        - User and assistant messages are formatted as a conversation
        - Supports temperature and other kwargs via Gemini API

        Args:
            messages: List of message dicts with 'role' and 'content' keys
            model: Optional model name (defaults to default_model)
            **kwargs: Additional parameters (temperature, max_tokens, etc.)

        Returns:
            str: Generated response text

        Raises:
            Exception: If Gemini API call fails
        """
        # Convert OpenAI messages format to Gemini format
        system_parts = []
        conversation_parts = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "system":
                system_parts.append(content)
            elif role == "user":
                conversation_parts.append(content)
            elif role == "assistant":
                # For assistant messages, we include them in the conversation
                # Gemini will use this as context for the response
                conversation_parts.append(f"Assistant: {content}")

        # Build the full prompt
        full_prompt = ""
        if system_parts:
            # Combine all system messages
            system_instruction = "\n".join(system_parts)
            full_prompt = f"System: {system_instruction}\n\n"

        # Add conversation history
        if conversation_parts:
            full_prompt += "\n".join(conversation_parts)

        # Call Gemini API
        model_name = model or self.default_model

        # Extract Gemini-specific parameters
        config = {}
        if "temperature" in kwargs:
            temperature = kwargs["temperature"]
            # Gemini models always require temperature=1.0
            if model_name and model_name.lower().startswith("gemini/"):
                if temperature != 1.0:
                    logger.info(
                        f"⚠️  Enforcing temperature=1.0 for Gemini model '{model_name}'. "
                        f"Gemini models require temperature=1.0. "
                        f"Requested temperature ({temperature}) was adjusted."
                    )
                    temperature = 1.0
            config["temperature"] = temperature
        if "max_tokens" in kwargs or "max_output_tokens" in kwargs:
            config["max_output_tokens"] = kwargs.get("max_output_tokens") or kwargs.get(
                "max_tokens"
            )

        try:
            response = self.client.models.generate_content(
                model=model_name, contents=full_prompt, config=config if config else None
            )

            # Extract text from response
            if hasattr(response, "text"):
                return response.text
            elif hasattr(response, "candidates") and response.candidates:
                # Fallback: try to get text from candidates
                candidate = response.candidates[0]
                if hasattr(candidate, "content") and hasattr(candidate.content, "parts"):
                    return candidate.content.parts[0].text if candidate.content.parts else ""

            # Last resort: string representation
            return str(response)

        except (
            AttributeError,
            TypeError,
            ValueError,
            RuntimeError,
            ConnectionError,
            OSError,
            KeyError,
        ) as e:
            logger.error(f"Gemini API call failed: {e}", exc_info=True)
            raise


# ============================================================================
# Chat History Service
# ============================================================================


class ChatHistoryService:
    """
    Manages Short-Term Memory (The active context window).

    Stores raw messages in MongoDB: {session_id, role, content, created_at}
    This is for immediate context - "what did I just say 5 seconds ago?"
    """

    def __init__(self, collection: Any, collection_name: str = "chat_history"):
        """
        Initialize Chat History Service.

        Args:
            collection: PyMongo Collection instance (REQUIRED - must be from
                       MDB-Engine connection pool)
            collection_name: Name of the collection for chat history (for logging)
        """
        if collection is None:
            raise ValueError(
                "Collection is REQUIRED. ChatHistoryService must use MDB-Engine's connection pool. "
                "Pass a PyMongo Collection instance obtained from MDB-Engine's connection manager."
            )

        self.collection = collection
        logger.info(f"✅ Chat History Service using MDB-Engine collection: {collection_name}")

        # Index for fast retrieval of sessions
        try:
            self.collection.create_index([("session_id", ASCENDING), ("created_at", DESCENDING)])
            self.collection.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])

            # TTL index for working memory (Cognitive Blueprint v2.0)
            # Default: 24 hours (86400 seconds)
            try:
                ttl_seconds = 24 * 3600  # 24 hours default
                self.collection.create_index(
                    [("created_at", ASCENDING)],
                    name="working_memory_ttl_idx",
                    expireAfterSeconds=ttl_seconds,
                    background=True,
                )
                logger.info("✅ Working memory TTL index created (24h expiration)")
            except (PyMongoError, AttributeError, TypeError) as e:
                logger.debug(f"TTL index creation: {e}")

            logger.info(f"✅ Chat history indexes created for {collection_name}")
        except PyMongoError as e:
            logger.warning(f"Failed to create chat history indexes: {e}")

    def get_message_count(self, session_id: str, user_id: str | None = None) -> int:
        """
        Get the count of messages for a session.

        Args:
            session_id: Session identifier
            user_id: Optional user ID for filtering

        Returns:
            Number of messages in the session
        """
        query = {"session_id": session_id}
        if user_id:
            query["user_id"] = str(user_id)
        return self.collection.count_documents(query)

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        """
        Adds a message to the chat history.

        Args:
            session_id: Unique session identifier
            role: Message role ("user", "assistant", "system")
            content: Message content
            user_id: Optional user ID for filtering
            metadata: Optional metadata dictionary
            metadata: Optional metadata to store with the message
        """
        doc = {
            "session_id": session_id,
            "role": role,
            "content": content,
            "created_at": datetime.now(timezone.utc),
            "memory_type": "working",  # Cognitive Blueprint v2.0
        }

        if user_id:
            doc["user_id"] = str(user_id)

        if metadata:
            doc["metadata"] = metadata

        self.collection.insert_one(doc)
        logger.debug(f"Added {role} message to session {session_id}")

    def get_context(
        self,
        session_id: str,
        limit: int = 10,
        user_id: str | None = None,
    ) -> list[dict[str, str]]:
        """
        Retrieves the most recent messages (Short-Term Memory).

        Returns them in chronological order (Oldest -> Newest) for the LLM.

        Args:
            session_id: Session identifier
            limit: Maximum number of messages to retrieve
            user_id: Optional user ID for filtering

        Returns:
            List of message dicts with 'role' and 'content' keys
        """
        query = {"session_id": session_id}
        if user_id:
            query["user_id"] = str(user_id)

        cursor = self.collection.find(query).sort("created_at", ASCENDING).limit(limit)

        # Return in chronological order [Msg 1, Msg 2, Msg 3...]
        history = list(cursor)

        return [{"role": h["role"], "content": h["content"]} for h in history]

    def get_recent_messages(
        self,
        session_id: str,
        limit: int = 10,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Retrieves the most recent messages in reverse chronological order.

        Useful for displaying recent messages in UI.

        Args:
            session_id: Session identifier
            limit: Maximum number of messages to retrieve
            user_id: Optional user ID for filtering

        Returns:
            List of full message documents
        """
        query = {"session_id": session_id}
        if user_id:
            query["user_id"] = str(user_id)

        cursor = self.collection.find(query).sort("created_at", DESCENDING).limit(limit)
        return list(cursor)

    def get_session_count(self, session_id: str, user_id: str | None = None) -> int:
        """Get the number of messages in a session."""
        query = {"session_id": session_id}
        if user_id:
            query["user_id"] = str(user_id)
        return self.collection.count_documents(query)

    def clear_session(self, session_id: str, user_id: str | None = None):
        """
        Wipes Short-Term memory for a session.

        Args:
            session_id: Session identifier
            user_id: Optional user ID for security filtering
        """
        query = {"session_id": session_id}
        if user_id:
            query["user_id"] = str(user_id)
        result = self.collection.delete_many(query)
        logger.info(f"Cleared {result.deleted_count} messages from session {session_id}")

    def delete_old_messages(
        self,
        session_id: str,
        keep_count: int = 10,
        user_id: str | None = None,
    ) -> int:
        """
        Deletes old messages from a session, keeping only the most recent ones.

        Useful for managing context window size.

        Args:
            session_id: Session identifier
            keep_count: Number of recent messages to keep
            user_id: Optional user ID for security filtering

        Returns:
            Number of messages deleted
        """
        query = {"session_id": session_id}
        if user_id:
            query["user_id"] = str(user_id)

        # Get IDs of messages to keep
        keep_messages = self.collection.find(query).sort("created_at", DESCENDING).limit(keep_count)
        keep_ids = [msg["_id"] for msg in keep_messages]

        # Delete all others
        delete_query = {"session_id": session_id, "_id": {"$nin": keep_ids}}
        if user_id:
            delete_query["user_id"] = str(user_id)

        result = self.collection.delete_many(delete_query)
        logger.info(f"Deleted {result.deleted_count} old messages from session {session_id}")
        return result.deleted_count


class CognitiveEngine:
    """
    The Brain: Combines LTM (Vectors) and STM (Chat History) to generate responses.

    This orchestrator provides a complete RAG pipeline:
    1. Save user message to STM
    2. Search LTM for relevant facts
    3. Fetch STM context (last K messages)
    4. Generate LLM response
    5. Save AI response to STM
    6. Extract new facts to LTM (async)
    """

    def __init__(
        self,
        app_slug: str,
        memory_service: BaseMemoryService | None = None,
        chat_history_collection: Any = None,
        memory_collection: Any = None,
        stm_context_limit: int = 10,
        ltm_search_limit: int = 5,
        auto_summarize_threshold: int = 20,
        llm_client=None,
        llm_provider: LLMProvider | None = None,
        *,
        graph_service: Any = None,
        enable_context_engineering: bool = True,
        stm_raw_window: int = 5,
        enable_entity_extraction: bool = True,
        enable_dynamic_persona: bool = True,
    ):
        """
        Initialize the Cognitive Engine.

        Args:
            app_slug: Application slug (required)
            memory_service: Optional BaseMemoryService instance (CognitiveMemoryService).
                           Will create CognitiveMemoryService if None.
            chat_history_collection: PyMongo Collection instance (REQUIRED - must be from
                                    MDB-Engine connection pool)
            memory_collection: PyMongo Collection instance (REQUIRED if memory_service not provided
                              - must be from MDB-Engine connection pool)
            stm_context_limit: Number of recent messages to include in context
            ltm_search_limit: Number of relevant memories to retrieve from LTM
            auto_summarize_threshold: Number of messages before auto-summarization kicks in
            llm_client: Optional LLM client (OpenAI, AzureOpenAI, or Gemini client)
                       Will auto-detect provider type and create appropriate LLMProvider
            llm_provider: Optional LLMProvider instance (takes precedence over llm_client)
                         Use this for custom providers or explicit control
            graph_service: Optional GraphService instance for GraphRAG functionality.
                          If provided, enables knowledge graph traversal via $graphLookup.
                          Use mdb_engine.graph.GraphService for standalone graph operations.
            enable_context_engineering: Enable Context Engineering features (default: True).
                                       When enabled, integrates PersonaEngine, entity extraction,
                                       and dynamic persona adaptation.
            stm_raw_window: Number of recent STM messages to keep raw before summarizing
                           (default: 5). Older messages are summarized to optimize tokens.
            enable_entity_extraction: Enable entity fact extraction from memories (default: True).
                                     Extracts key-value facts like Name, OS, Language, Expertise.
            enable_dynamic_persona: Enable dynamic persona adaptation based on user context
                                   (default: True). Adjusts persona tone based on expertise,
                                   emotion, and retrieved memories.
        """
        if chat_history_collection is None:
            raise ValueError(
                "chat_history_collection is REQUIRED. CognitiveEngine must use "
                "MDB-Engine's connection pool. "
                "Pass a PyMongo Collection instance obtained from "
                "MDB-Engine's connection manager."
            )

        self.app_slug = app_slug
        self.stm_context_limit = stm_context_limit
        self.ltm_search_limit = ltm_search_limit
        self.auto_summarize_threshold = auto_summarize_threshold

        # Context Engineering configuration
        self.enable_context_engineering = enable_context_engineering
        self.stm_raw_window = stm_raw_window
        self.enable_entity_extraction = enable_entity_extraction
        self.enable_dynamic_persona = enable_dynamic_persona

        # Initialize Short-Term Memory
        self.stm = ChatHistoryService(collection=chat_history_collection)

        # Initialize Long-Term Memory
        if memory_service:
            self.ltm = memory_service
            ltm_id = id(memory_service)  # Memory address for debugging
            logger.info(
                f"🔧 [CognitiveEngine] Using provided memory service (id={ltm_id}): "
                f"db={getattr(memory_service, 'db_name', 'unknown')}, "
                f"collection={getattr(memory_service, 'collection_name', 'unknown')}, "
                f"app_slug={getattr(memory_service, 'app_slug', 'unknown')}"
            )
        else:
            if memory_collection is None:
                raise ValueError(
                    "memory_collection is REQUIRED when memory_service is not provided. "
                    "CognitiveEngine must use MDB-Engine's connection pool. "
                    "Pass a PyMongo Collection instance obtained from "
                    "MDB-Engine's connection manager."
                )
            logger.info("Creating new CognitiveMemoryService using MDB-Engine connection")
            self.ltm = CognitiveMemoryService(
                app_slug=self.app_slug,
                collection=memory_collection,
            )
            ltm_id = id(self.ltm)
            logger.info(
                f"🔧 [CognitiveEngine] Created new memory service (id={ltm_id}): "
                f"collection={getattr(self.ltm, 'collection_name', 'unknown')}"
            )

        # Initialize LLM Provider
        if llm_provider:
            # Use provided provider
            self.llm_provider = llm_provider
            logger.info("✅ Using provided LLMProvider")
        elif llm_client:
            # Auto-detect provider type from client
            self.llm_provider = self._create_provider_from_client(llm_client)
            logger.info(f"✅ Auto-detected LLM provider: {type(self.llm_provider).__name__}")
        else:
            # Try to get from memory service
            memory_service_client = getattr(self.ltm, "llm_client", None)
            if memory_service_client:
                self.llm_provider = self._create_provider_from_client(memory_service_client)
                logger.info(
                    f"✅ Using LLM provider from memory service: {type(self.llm_provider).__name__}"
                )
            else:
                self.llm_provider = None
                logger.warning("⚠️ No LLM provider available. Fact extraction will be disabled.")

        # Store GraphService for GraphRAG functionality
        # Priority: explicit graph_service > memory service's _graph_service
        if graph_service:
            self._graph_service = graph_service
            logger.info("✅ Using provided GraphService for GraphRAG")
        else:
            # Fall back to memory service's graph service
            self._graph_service = getattr(self.ltm, "_graph_service", None)
            if self._graph_service:
                logger.info("✅ Using GraphService from memory service")
            else:
                logger.debug("GraphService not available - GraphRAG disabled")

    def _create_provider_from_client(self, client) -> LLMProvider:
        """
        Auto-detect and create appropriate LLMProvider from client instance.

        Args:
            client: LLM client instance (OpenAI, AzureOpenAI, or Gemini)

        Returns:
            LLMProvider instance
        """
        client_type_str = str(type(client))

        # Check for Gemini
        if "genai" in client_type_str.lower() or "google" in client_type_str.lower():
            return GeminiProvider(client)

        # Check for OpenAI/AzureOpenAI
        try:
            from openai import AzureOpenAI, OpenAI

            if isinstance(client, OpenAI | AzureOpenAI):
                return OpenAIProvider(client)
        except ImportError:
            pass

        # Default: assume OpenAI-compatible interface
        logger.warning(f"⚠️ Unknown client type {client_type_str}, assuming OpenAI-compatible")
        return OpenAIProvider(client)

    def _extract_entity_facts(
        self, user_id: str, relevant_memories: list[dict[str, Any]]
    ) -> dict[str, str]:
        """
        Extract entity facts from retrieved memories.

        Filters memories by category="biographical" and extracts key-value pairs
        like Name, OS, Language, Expertise level, etc.

        Args:
            user_id: User identifier
            relevant_memories: List of memory dicts from LTM search

        Returns:
            Dict of entity facts (e.g., {"Name": "Alice", "OS": "Ubuntu 22.04"})
        """
        if not self.enable_entity_extraction:
            return {}

        entity_facts = {}

        # Extract from biographical category memories
        for mem in relevant_memories:
            category = mem.get("category", "")
            memory_text = mem.get("memory", "") or mem.get("text", "")

            if category == "biographical" and memory_text:
                # Simple extraction patterns
                memory_lower = memory_text.lower()

                # Extract name patterns
                if "name is" in memory_lower or "called" in memory_lower:
                    # Try to extract name
                    parts = memory_text.split()
                    for i, part in enumerate(parts):
                        if part.lower() in ["is", "called", "named"] and i + 1 < len(parts):
                            name = parts[i + 1].strip(".,!?")
                            if name and len(name) > 1:
                                entity_facts["Name"] = name
                                break

                # Extract OS patterns
                if "ubuntu" in memory_lower or "linux" in memory_lower:
                    if "ubuntu" in memory_lower:
                        entity_facts["OS"] = "Ubuntu"
                    elif "linux" in memory_lower:
                        entity_facts["OS"] = "Linux"
                elif "windows" in memory_lower or "macos" in memory_lower or "mac" in memory_lower:
                    if "windows" in memory_lower:
                        entity_facts["OS"] = "Windows"
                    elif "macos" in memory_lower or "mac" in memory_lower:
                        entity_facts["OS"] = "macOS"

                # Extract language patterns
                if "python" in memory_lower:
                    entity_facts["Language"] = "Python"
                elif "javascript" in memory_lower or "typescript" in memory_lower:
                    entity_facts["Language"] = "JavaScript/TypeScript"
                elif "java" in memory_lower:
                    entity_facts["Language"] = "Java"
                elif "rust" in memory_lower:
                    entity_facts["Language"] = "Rust"
                elif "go" in memory_lower:
                    entity_facts["Language"] = "Go"

                # Extract expertise level
                if any(
                    word in memory_lower for word in ["expert", "senior", "experienced", "advanced"]
                ):
                    entity_facts["Expertise"] = "expert"
                elif any(
                    word in memory_lower
                    for word in ["beginner", "learning", "new to", "just started"]
                ):
                    entity_facts["Expertise"] = "beginner"
                elif any(
                    word in memory_lower for word in ["intermediate", "moderate", "some experience"]
                ):
                    entity_facts["Expertise"] = "intermediate"

        # Also check preferences category for preferences
        for mem in relevant_memories:
            category = mem.get("category", "")
            memory_text = mem.get("memory", "") or mem.get("text", "")

            if category == "preferences" and memory_text:
                # Store preferences as entity facts
                if "prefers" in memory_text.lower() or "likes" in memory_text.lower():
                    # Extract preference
                    if "dark mode" in memory_text.lower() or "dark theme" in memory_text.lower():
                        entity_facts["UI_Preference"] = "dark mode"
                    elif (
                        "light mode" in memory_text.lower() or "light theme" in memory_text.lower()
                    ):
                        entity_facts["UI_Preference"] = "light mode"

        return entity_facts

    def _build_dynamic_persona(
        self,
        persona: dict[str, Any] | None,
        entity_facts: dict[str, str],
        relevant_memories: list[dict[str, Any]],
    ) -> str:
        """
        Build dynamic persona instructions based on user context.

        Analyzes entity facts and retrieved memories to generate dynamic
        instructions that adapt the persona's behavior.

        Args:
            persona: Persona document from PersonaEngine (or None)
            entity_facts: Extracted entity facts
            relevant_memories: Retrieved memories from LTM

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
                "User is an expert. Be concise and technical. Skip basic explanations. "
                "Assume advanced knowledge."
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

        # Trait-based adaptation (from persona)
        if persona and persona.get("traits"):
            traits = persona.get("traits", {})

            # Adjust based on persona traits
            if traits.get("humor", 0) > 0.6:
                instructions.append(
                    "Use appropriate humor when relevant. Be friendly and engaging."
                )

            if traits.get("formality", 0) > 0.7:
                instructions.append("Maintain a professional and formal tone.")
            elif traits.get("formality", 0) < 0.4:
                instructions.append("Use a casual and conversational tone.")

            if traits.get("empathy", 0) > 0.7:
                instructions.append("Show high empathy and emotional intelligence.")

            if traits.get("technical_focus", 0) > 0.7:
                instructions.append("Focus on technical accuracy and precision.")

        return " ".join(instructions) if instructions else ""

    def _optimize_stm_context(
        self,
        stm_context: list[dict[str, str]],
        session_id: str,
        user_id: str,
    ) -> tuple[list[dict[str, str]], str | None]:
        """
        Optimize STM context using sliding window + summary pattern.

        Keeps the last N messages raw (immediate context) and summarizes
        older messages to optimize token usage.

        Args:
            stm_context: Full STM context list
            session_id: Session identifier
            user_id: User identifier

        Returns:
            Tuple of (recent_messages, summary)
            - recent_messages: Last N messages to keep raw
            - summary: Summary of older messages (None if not needed)
        """
        if not self.enable_context_engineering or len(stm_context) <= self.stm_raw_window:
            return (stm_context, None)

        # Split into recent (raw) and older (to summarize)
        recent_messages = stm_context[-self.stm_raw_window :]
        older_messages = stm_context[: -self.stm_raw_window]

        # For now, return recent messages and a simple summary placeholder
        # Full summarization would require LLM call (can be async/background)
        summary = f"Previous conversation context ({len(older_messages)} messages): "
        summary += " ".join([msg.get("content", "")[:50] for msg in older_messages[:3]])
        if len(older_messages) > 3:
            summary += f" ... ({len(older_messages) - 3} more messages)"

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

        Args:
            entity_facts: Dict of entity facts

        Returns:
            Formatted entity memory string (empty if no facts)
        """
        if not entity_facts:
            return ""

        facts_list = [f"{k}: {v}" for k, v in entity_facts.items()]
        return f"[USER CONTEXT]\nKnown Facts: {', '.join(facts_list)}\n\n"

    def _format_memory_layer(self, ltm_context: str, graph_context: str) -> str:
        """
        Format LTM and Graph context sections.

        Args:
            ltm_context: Formatted LTM context string
            graph_context: Formatted Graph context string

        Returns:
            Combined memory layer string
        """
        sections = []

        if graph_context:
            sections.append(f"[GRAPH CONTEXT]\n{graph_context}")

        if ltm_context:
            sections.append(f"[RELEVANT MEMORY]\n{ltm_context}")

        return "\n\n".join(sections) if sections else ""

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

    def _construct_context_engineered_prompt(
        self,
        persona: dict[str, Any] | None,
        entity_facts: dict[str, str],
        ltm_context: str,
        graph_context: str,
        dynamic_instructions: str,
        stm_summary: str | None,
    ) -> str:
        """
        Construct context-engineered system prompt.

        Assembles all context layers according to Context Engineering principles:
        Context = P_static + M_relevant + Q_current

        Args:
            persona: Persona document (P_static)
            entity_facts: Extracted entity facts
            ltm_context: Formatted LTM context
            graph_context: Formatted Graph context
            dynamic_instructions: Dynamic persona instructions
            stm_summary: Summary of older STM messages

        Returns:
            Complete system prompt string
        """
        sections = []

        # 1. Persona Layer (P_static)
        persona_layer = self._format_persona_layer(persona)
        if persona_layer:
            sections.append(persona_layer)

        # 2. Dynamic Instructions (META-INSTRUCTIONS)
        if dynamic_instructions:
            sections.append(f"[META-INSTRUCTIONS]\n{dynamic_instructions}\n")

        # 3. Entity Memory (USER CONTEXT)
        entity_layer = self._format_entity_memory(entity_facts)
        if entity_layer:
            sections.append(entity_layer)

        # 4. Memory Layer (M_relevant: LTM + Graph)
        memory_layer = self._format_memory_layer(ltm_context, graph_context)
        if memory_layer:
            sections.append(memory_layer)

        # 5. STM Summary (PREVIOUS CONTEXT)
        if stm_summary:
            sections.append(f"[PREVIOUS CONTEXT]\n{stm_summary}\n")

        # 6. Instructions for using context
        sections.append(
            "Use the Chat History to maintain conversation flow. "
            "Use the context above to provide accurate and relevant responses."
        )

        return "\n".join(sections)

    async def chat(
        self,
        user_id: str,
        session_id: str,
        user_query: str,
        system_prompt: str | None = None,
        extract_facts: bool = True,
        bucket_id: str | None = None,
        bucket_type: str | None = None,
        search_filters: dict[str, Any] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """
        Full RAG Pipeline: Combines STM and LTM to generate a response.

        This method orchestrates the complete RAG pipeline:
        1. Saves user message to Short-Term Memory (STM)
        2. Searches Long-Term Memory (LTM) for relevant memories
        3. Retrieves STM context (recent conversation history)
        4. Generates LLM response with combined context
        5. Saves AI response to STM
        6. Optionally extracts facts from conversation to LTM

        Args:
            user_id: User identifier for scoping memory operations
            session_id: Session identifier for grouping related messages
            user_query: User's message/query
            system_prompt: Optional custom system prompt. If None, uses default.
            extract_facts: Whether to extract facts to LTM (default: True).
                          When True, uses LLM to extract factual information from
                          the user's message and stores it as long-term memories.
            bucket_id: Optional bucket ID for memory isolation. When provided:
                      - LTM search is filtered to only return memories from this bucket
                      - New memories are stored with this bucket_id
                      Enables "bucket awareness" where memories in 'work' bucket won't
                      appear when searching from 'personal' bucket.
            bucket_type: Optional bucket type (e.g., "conversation", "category", "file").
                        Used in conjunction with bucket_id for memory organization.
            search_filters: Optional additional filters for LTM search.
                           Example: {"metadata": {"category": "work"}}
            **kwargs: Additional arguments passed to LLM provider (e.g., model, temperature)

        Returns:
            Dict[str, Any] with the following keys:
                - response (str): AI-generated response text
                - stm_context (List[Dict]): Short-term memory context used for generation
                - ltm_memories (List[Dict]): Long-term memories retrieved and used
                - graph_context (Dict|None): GraphRAG context with entry_nodes and graph_context
                                            (related nodes via $graphLookup traversal)
                - session_message_count (int): Number of messages in the session
                - memories_stored (List[Dict]): New memories stored during this chat call.
                                              Empty list if extract_facts=False or no memories
                                              extracted. Useful for triggering UI updates via
                                              WebSocket.
                - persona_used (Dict|None): Persona document used
                    (if Context Engineering enabled)
                - entity_facts (Dict): Entity facts extracted and used
                    (if Context Engineering enabled)
                - dynamic_instructions (str): Dynamic persona instructions applied
                    (if Context Engineering enabled)
                - stm_summary (str|None): Summary of older STM messages
                    (if Context Engineering enabled and summary created)

        Raises:
            CognitiveMemoryServiceError: If LLM provider is not available or memory
                                        operations fail

        Example:
            ```python
            # Basic usage (no bucket filtering - searches all memories)
            result = await cognitive_engine.chat(
                user_id="user123",
                session_id="session456",
                user_query="I love chocolate",
                extract_facts=True
            )

            # Bucket-aware usage (only searches/stores in 'work' bucket)
            result = await cognitive_engine.chat(
                user_id="user123",
                session_id="session456",
                user_query="What meetings do I have?",
                bucket_id="category:work:user123",
                bucket_type="category",
                extract_facts=True
            )
            print(result["response"])  # AI response
            # [{"id": "...", "memory": "User loves chocolate", ...}]
            print(result["memories_stored"])
            ```
        """
        # --- Step 1: Ingest User Message (STM) ---
        self.stm.add_message(
            session_id=session_id,
            role="user",
            content=user_query,
            user_id=user_id,
            metadata={"source": "cognitive_engine"},
        )

        # --- Step 2 & 3: Retrieve LTM and STM in PARALLEL ---
        # These are independent operations, so we run them concurrently for ~2x speedup
        ltm_collection = getattr(self.ltm, "collection_name", "unknown")
        ltm_db = getattr(self.ltm, "db_name", "unknown")
        ltm_id = id(self.ltm)
        logger.info(
            f"⚡ [CognitiveEngine] Parallel fetch: LTM (service_id={ltm_id}) + STM "
            f"(user_id={user_id}, session_id={session_id})"
        )

        async def _fetch_ltm():
            """Fetch relevant memories from LTM with optional bucket filtering."""
            try:
                # Build filters for bucket-aware search
                ltm_filters = dict(search_filters) if search_filters else {}

                # Add bucket filter if provided (bucket awareness)
                # Use associated_bucket_id to find BOTH:
                # - Conversation memories (where associated_bucket_id = bucket_id)
                # - File memories (where associated_bucket_id links to category bucket)
                # This ensures file memories in a category bucket are included in search
                if bucket_id:
                    if "metadata" not in ltm_filters:
                        ltm_filters["metadata"] = {}
                    ltm_filters["metadata"]["associated_bucket_id"] = bucket_id
                    logger.info(
                        f"🪣 [CognitiveEngine] Bucket-aware search: "
                        f"associated_bucket_id={bucket_id}, bucket_type={bucket_type}"
                    )

                return await asyncio.to_thread(
                    self.ltm.search,
                    query=user_query,
                    user_id=user_id,
                    limit=self.ltm_search_limit,
                    filters=ltm_filters if ltm_filters else None,
                )
            except (ValueError, RuntimeError, AttributeError) as e:
                logger.warning(
                    f"⚠️ LTM search failed for query '{user_query}': {e}. "
                    "Continuing without LTM context.",
                    exc_info=True,
                )
                return []

        async def _fetch_stm():
            """Fetch chat context from STM."""
            full_context = await asyncio.to_thread(
                self.stm.get_context,
                session_id=session_id,
                limit=self.stm_context_limit,
                user_id=user_id,
            )
            # Optimize STM context with sliding window + summary
            if self.enable_context_engineering:
                recent_messages, summary = self._optimize_stm_context(
                    full_context, session_id, user_id
                )
                return recent_messages, summary
            return full_context, None

        async def _fetch_graph():
            """Fetch graph context for GraphRAG."""
            if not self._graph_service:
                return None
            try:
                return await asyncio.to_thread(
                    self._graph_service.hybrid_search,
                    query=user_query,
                    user_id=user_id,
                    max_depth=2,
                    vector_limit=3,
                )
            except (ValueError, RuntimeError, AttributeError) as e:
                logger.warning(f"⚠️ Graph search failed: {e}")
                return None

        # Run all fetches in parallel (LTM, STM, and optionally Graph)
        if self._graph_service:
            results = await asyncio.gather(
                _fetch_ltm(),
                _fetch_stm(),
                _fetch_graph(),
            )
            relevant_memories = results[0]
            stm_result = results[1]
            graph_results = results[2]
        else:
            results = await asyncio.gather(
                _fetch_ltm(),
                _fetch_stm(),
            )
            relevant_memories = results[0]
            stm_result = results[1]
            graph_results = None

        # Unpack STM result (may be tuple if optimized)
        if isinstance(stm_result, tuple):
            stm_context, stm_summary = stm_result
        else:
            stm_context = stm_result
            stm_summary = None

        ltm_count = len(relevant_memories) if relevant_memories else 0
        stm_count = len(stm_context) if stm_context else 0
        graph_count = graph_results.get("total_nodes", 0) if graph_results else "disabled"
        logger.info(
            f"⚡ [Parallel Fetch] LTM: {ltm_count} memories, "
            f"STM: {stm_count} messages, "
            f"Graph: {graph_count}"
        )
        if relevant_memories:
            for i, mem in enumerate(relevant_memories):
                logger.info(
                    f"  Memory {i+1}: {mem.get('memory', '')[:100]}... "
                    f"(score: {mem.get('score', 'N/A')})"
                )

        # Format LTM for the prompt
        ltm_context = ""
        if relevant_memories:
            ltm_context = "RELEVANT FACTS FROM LONG-TERM MEMORY:\n"
            for mem in relevant_memories:
                memory_text = mem.get("memory", "")
                if memory_text:
                    ltm_context += f"- {memory_text}\n"
            ltm_context += "\n"
            logger.info(f"✅ Formatted LTM context with {len(relevant_memories)} memories")
        else:
            logger.warning(f"⚠️ No relevant memories found for query: '{user_query}'")

        # Format Graph context for GraphRAG
        graph_context = ""
        if graph_results and (
            graph_results.get("entry_nodes") or graph_results.get("graph_context")
        ):
            if self._graph_service:
                graph_context = self._graph_service.format_graph_context(
                    graph_results,
                    max_nodes=8,
                    include_edges=True,
                )
                if graph_context:
                    graph_context += "\n\n"
                    graph_node_count = graph_results.get("total_nodes", 0)
                    logger.info(f"✅ Formatted graph context with {graph_node_count} nodes")

        session_message_count = self.stm.get_session_count(session_id, user_id)

        # --- Step 4: Check for Auto-Summarization ---
        if session_message_count > self.auto_summarize_threshold:
            logger.info(
                f"Session {session_id} has {session_message_count} messages, "
                f"considering summarization"
            )
            # This will be handled asynchronously to not block the response

        # --- Step 5: Construct Context-Engineered System Prompt ---
        persona_used = None
        entity_facts = {}
        dynamic_instructions = ""

        if not system_prompt:
            if self.enable_context_engineering:
                # Get Persona from PersonaEngine (if available)
                persona_engine = getattr(self.ltm, "persona_engine", None)
                if persona_engine:
                    try:
                        persona_used = persona_engine.get_persona()
                        role = persona_used.get("role", "Unknown") if persona_used else "None"
                        logger.info(f"✅ Retrieved persona: {role}")
                    except (PyMongoError, AttributeError, TypeError) as e:
                        logger.warning(f"⚠️ Failed to get persona: {e}")
                        persona_used = None

                # Extract entity facts
                if self.enable_entity_extraction:
                    entity_facts = self._extract_entity_facts(user_id, relevant_memories)
                    if entity_facts:
                        logger.info(
                            f"✅ Extracted {len(entity_facts)} entity facts: "
                            f"{list(entity_facts.keys())}"
                        )

                # Build dynamic persona instructions
                if self.enable_dynamic_persona:
                    dynamic_instructions = self._build_dynamic_persona(
                        persona_used, entity_facts, relevant_memories
                    )
                    if dynamic_instructions:
                        logger.info("✅ Generated dynamic persona instructions")

                # Construct context-engineered prompt
                system_prompt = self._construct_context_engineered_prompt(
                    persona=persona_used,
                    entity_facts=entity_facts,
                    ltm_context=ltm_context,
                    graph_context=graph_context,
                    dynamic_instructions=dynamic_instructions,
                    stm_summary=stm_summary,
                )

                logger.info(
                    f"✅ Context-engineered system prompt constructed: "
                    f"persona={'yes' if persona_used else 'no'}, "
                    f"entities={len(entity_facts)}, "
                    f"dynamic_instructions={'yes' if dynamic_instructions else 'no'}, "
                    f"stm_summary={'yes' if stm_summary else 'no'}"
                )
            else:
                # Fallback to original behavior
                context_sections = []

                if graph_context:
                    context_sections.append(graph_context)

                if ltm_context:
                    context_sections.append(ltm_context)

                if context_sections:
                    system_prompt = (
                        "You are a helpful AI assistant.\n"
                        "Use the following context to answer if relevant.\n"
                        "Use the Chat History to maintain conversation flow.\n\n"
                        + "".join(context_sections)
                    )
                    logger.info(
                        f"✅ System prompt includes {len(relevant_memories)} memories"
                        + (
                            f", {graph_results.get('total_nodes', 0)} graph nodes"
                            if graph_results
                            else ""
                        )
                    )
                else:
                    system_prompt = (
                        "You are a helpful AI assistant.\n"
                        "Use the Chat History to maintain conversation flow."
                    )
                    logger.warning(
                        "⚠️ System prompt created WITHOUT memory context (no memories found)"
                    )

        # Prepare messages for LLM
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(stm_context)  # Append STM (includes the latest user query we just added)

        logger.debug(
            f"📝 Prepared {len(messages)} messages for LLM "
            f"(1 system + {len(stm_context)} chat history)"
        )

        # --- Step 6: Generate Response ---
        if not self.llm_provider:
            raise CognitiveMemoryServiceError("No LLM provider available for chat generation")

        # Get model from kwargs or use default
        chat_model = kwargs.get("model", None)

        # Generate response using LLM provider abstraction
        def _generate_response():
            return self.llm_provider.generate_chat_completion(
                messages=messages, model=chat_model, **kwargs
            )

        ai_response = await asyncio.to_thread(_generate_response)

        # --- Step 7: Save AI Response (STM) ---
        self.stm.add_message(
            session_id=session_id,
            role="assistant",
            content=ai_response,
            user_id=user_id,
            metadata={"source": "cognitive_engine"},
        )

        # --- Step 8: Consolidate Memories (LTM) ---
        # CRITICAL: We don't want to save "Hello" or "Thanks" to LTM.
        # We use the existing 'add' method which has the LLM extraction logic built-in!
        # We only run this on the USER'S input to extract facts about THEM.
        # Use asyncio.to_thread for synchronous memory service methods
        memories_stored = []  # Track stored memories for return value
        logger.info(
            f"💾 [CognitiveEngine] Step 8: extract_facts={extract_facts}, "
            f"ltm service available: {self.ltm is not None}"
        )
        if extract_facts:
            if not self.ltm:
                logger.error(
                    "❌ [CognitiveEngine] Cannot store memories: ltm (memory service) is None!"
                )
            else:
                try:
                    ltm_collection = getattr(self.ltm, "collection_name", "unknown")
                    ltm_db = getattr(self.ltm, "db_name", "unknown")
                    ltm_id = id(self.ltm)

                    # Use provided bucket_id/bucket_type for bucket awareness,
                    # otherwise fall back to session-based bucket
                    storage_bucket_id = bucket_id if bucket_id else f"session:{session_id}"
                    storage_bucket_type = bucket_type if bucket_type else "conversation"

                    # Build metadata with associated_bucket_id for unified search
                    # associated_bucket_id allows finding both conversation and file memories
                    # in the same bucket when searching
                    storage_metadata = {
                        "source": "chat_session",
                        "session_id": session_id,
                        "associated_bucket_id": storage_bucket_id,  # For unified bucket search
                    }

                    query_preview = user_query[:50]
                    logger.info(
                        f"💾 [CognitiveEngine] Storing memory (service_id={ltm_id}): "
                        f"user_id={user_id}, query='{query_preview}...', "
                        f"session_id={session_id}, bucket_id={storage_bucket_id}, "
                        f"bucket_type={storage_bucket_type}, "
                        f"collection={ltm_collection}, db={ltm_db}"
                    )
                    stored = await asyncio.to_thread(
                        self.ltm.add,
                        messages=user_query,
                        user_id=user_id,
                        metadata=storage_metadata,
                        bucket_id=storage_bucket_id,
                        bucket_type=storage_bucket_type,
                    )
                    logger.info(
                        f"💾 [CognitiveEngine] Memory storage SUCCESS: "
                        f"{len(stored) if stored else 0} memories stored "
                        f"in collection={ltm_collection}, db={ltm_db}"
                    )
                    if stored:
                        memories_stored = stored  # Store for return value
                        for i, mem in enumerate(stored):
                            logger.info(
                                f"  Memory {i+1}: id={mem.get('id', 'unknown')}, "
                                f"text='{mem.get('memory', '')[:50]}...'"
                            )
                    else:
                        logger.warning(
                            "⚠️ [CognitiveEngine] Storage returned empty list - "
                            "no memories extracted!"
                        )
                except (
                    PyMongoError,
                    CognitiveMemoryServiceError,
                    ValueError,
                    TypeError,
                    RuntimeError,
                    ConnectionError,
                    OSError,
                ) as e:
                    logger.error(
                        f"❌ [CognitiveEngine] Failed to extract facts to LTM: {e}", exc_info=True
                    )
        else:
            logger.info("💾 [CognitiveEngine] Skipping memory storage (extract_facts=False)")

        result = {
            "response": ai_response,
            "stm_context": stm_context,
            "ltm_memories": relevant_memories,
            "graph_context": graph_results,  # GraphRAG context (nodes and relationships)
            "session_message_count": session_message_count,
            "memories_stored": memories_stored,  # New memories stored during this chat
        }

        # Add Context Engineering metadata if enabled
        if self.enable_context_engineering:
            result["persona_used"] = persona_used
            result["entity_facts"] = entity_facts
            result["dynamic_instructions"] = dynamic_instructions
            result["stm_summary"] = stm_summary

        return result

    async def summarize_session(
        self,
        session_id: str,
        user_id: str,
        messages_to_summarize: int = 10,
    ) -> str | None:
        """
        Summarizes old messages in a session and stores the summary in LTM.

        This implements "Medium-Term Memory" - converting STM chunks into LTM facts.

        Args:
            session_id: Session identifier
            user_id: User identifier
            messages_to_summarize: Number of oldest messages to summarize

        Returns:
            Summary text if successful, None otherwise
        """
        if not self.llm_client:
            logger.warning("No LLM client available for summarization")
            return None

        # Get oldest messages
        query = {"session_id": session_id, "user_id": str(user_id)}
        old_messages = list(
            self.stm.collection.find(query)
            .sort("created_at", ASCENDING)
            .limit(messages_to_summarize)
        )

        if not old_messages:
            return None

        # Format messages for summarization
        conversation_text = "\n".join([f"{msg['role']}: {msg['content']}" for msg in old_messages])

        summary_prompt = (
            "Summarize the following conversation into 2-3 key facts or insights "
            "that would be useful to remember long-term. Focus on user preferences, "
            "important information, or decisions made.\n\n"
            f"Conversation:\n{conversation_text}\n\n"
            "Summary:"
        )

        try:
            # Detect if using Azure
            chat_model = "gpt-4o"

            def _generate_summary():
                if isinstance(self.llm_client, type) and "Azure" in str(type(self.llm_client)):
                    import os

                    deployment_name = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", chat_model)
                    return self.llm_client.chat.completions.create(
                        model=deployment_name,
                        messages=[{"role": "user", "content": summary_prompt}],
                        temperature=0.3,
                    )
                else:
                    return self.llm_client.chat.completions.create(
                        model=chat_model,
                        messages=[{"role": "user", "content": summary_prompt}],
                        temperature=0.3,
                    )

            # Run LLM call in thread pool
            response = await asyncio.to_thread(_generate_summary)
            summary = response.choices[0].message.content

            # Store summary in LTM (use asyncio.to_thread for synchronous method)
            await asyncio.to_thread(
                self.ltm.inject,
                memory=summary,
                user_id=user_id,
                metadata={
                    "type": "session_summary",
                    "session_id": session_id,
                    "source": "auto_summarization",
                },
                bucket_id=f"session:{session_id}",
                bucket_type="summary",
            )

            # Delete summarized messages from STM
            message_ids = [msg["_id"] for msg in old_messages]
            self.stm.collection.delete_many({"_id": {"$in": message_ids}})

            logger.info(f"Summarized {len(old_messages)} messages from session {session_id}")
            return summary

        except (
            AttributeError,
            TypeError,
            ValueError,
            RuntimeError,
            ConnectionError,
            OSError,
            KeyError,
            PyMongoError,
            CognitiveMemoryServiceError,
        ) as e:
            logger.exception(f"Failed to summarize session {session_id}: {e}")
            return None

    def get_full_context(
        self,
        user_id: str,
        session_id: str,
        query: str | None = None,
    ) -> dict[str, Any]:
        """
        Retrieves full context (STM + LTM) for a session.

        Useful for debugging or manual context inspection.

        Args:
            user_id: User identifier
            session_id: Session identifier
            query: Optional query for LTM search (uses session_id if None)

        Returns:
            Dict with stm_context and ltm_memories
        """
        stm_context = self.stm.get_context(
            session_id=session_id,
            limit=self.stm_context_limit,
            user_id=user_id,
        )

        search_query = query or f"session {session_id}"
        ltm_memories = self.ltm.search(
            query=search_query,
            user_id=user_id,
            limit=self.ltm_search_limit,
            filters={"metadata.session_id": session_id},
        )

        return {
            "stm_context": stm_context,
            "ltm_memories": ltm_memories,
            "session_id": session_id,
            "user_id": user_id,
        }

    def inject_thought(
        self,
        user_id: str,
        thought: str,
        session_id: str | None = None,
        visibility: str = "private",
        metadata: dict[str, Any] | None = None,
    ):
        """
        Stores an internal "thought" or reasoning trace in LTM.

        Useful for storing AI reasoning that the user didn't see.

        Args:
            user_id: User identifier
            thought: The thought/reasoning to store
            session_id: Optional session identifier
            visibility: Visibility level ("private", "shared", etc.)
            metadata: Additional metadata
        """
        final_metadata = metadata or {}
        final_metadata.update(
            {
                "type": "internal_thought",
                "visibility": visibility,
            }
        )
        if session_id:
            final_metadata["session_id"] = session_id

        self.ltm.inject(
            memory=thought,
            user_id=user_id,
            metadata=final_metadata,
            bucket_type="thought",
        )
        logger.debug(f"Injected thought for user {user_id}")
