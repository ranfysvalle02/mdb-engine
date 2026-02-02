"""
Mem0 Memory Service Implementation
Production-ready wrapper for Mem0.ai with strict metadata schema for MongoDB.

v0.7.4: Enhanced with hybrid update pattern and direct MongoDB access for reliable
memory operations. Properly handles Mem0's MongoDB structure (_id, payload).

v0.7.5: Added inject() method for manual memory insertion without LLM inference,
and enhanced delete functionality with comprehensive documentation.
"""

import logging
import os
import tempfile
from datetime import datetime
from typing import Any

from .base import BaseMemoryService, MemoryServiceError

# Required: Direct PyMongo access
try:
    from pymongo import MongoClient
    from pymongo.errors import (
        ConfigurationError,
        ConnectionFailure,
        InvalidURI,
        PyMongoError,
        ServerSelectionTimeoutError,
    )
except ImportError:
    MongoClient = None
    ConnectionFailure = None
    ConfigurationError = None
    ServerSelectionTimeoutError = None
    InvalidURI = None
    PyMongoError = None

# Set MEM0_DIR environment variable early to avoid permission issues
if "MEM0_DIR" not in os.environ:
    mem0_dir = os.path.join(tempfile.gettempdir(), ".mem0")
    try:
        os.makedirs(mem0_dir, exist_ok=True)
        os.environ["MEM0_DIR"] = mem0_dir
    except OSError:
        # Fallback: current directory
        os.environ["MEM0_DIR"] = os.path.join(os.getcwd(), ".mem0")

# Lazy Import
MEM0_AVAILABLE = None
Memory = None


def _check_mem0_available():
    global MEM0_AVAILABLE, Memory
    if MEM0_AVAILABLE is None:
        try:
            from mem0 import Memory

            MEM0_AVAILABLE = True
        except ImportError:
            MEM0_AVAILABLE = False
            Memory = None
    return MEM0_AVAILABLE


logger = logging.getLogger(__name__)


class Mem0MemoryServiceError(MemoryServiceError):
    """Exception raised by Mem0MemoryService operations."""

    pass


class Mem0MemoryService(BaseMemoryService):
    """
    Production-ready Mem0 Memory Service with MongoDB integration.

    Features:
    - Hybrid update pattern: Mem0 for embeddings, MongoDB for data persistence
    - Full metadata support via direct MongoDB access
    - In-place memory updates preserving IDs and timestamps
    - Automatic embedding recomputation on content changes
    - Knowledge graph support (if enabled in Mem0 config)
    - Comprehensive error handling and logging
    - Reliable return values fetched directly from MongoDB

    Update Architecture:
    - Content updates routed via Mem0 (triggers re-embedding)
    - Metadata updates routed via direct PyMongo (full control)
    - Final result always fetched from MongoDB (guaranteed structure)
    """

    def __init__(
        self,
        mongo_uri: str,
        db_name: str,
        app_slug: str,
        config: dict[str, Any] | None = None,
    ):
        if not _check_mem0_available():
            raise Mem0MemoryServiceError("Mem0 not installed. pip install mem0ai")

        if not mongo_uri or not db_name or not app_slug:
            raise Mem0MemoryServiceError("mongo_uri, db_name, and app_slug are required parameters")

        self.mongo_uri = mongo_uri
        self.db_name = db_name
        self.app_slug = app_slug
        self.collection_name = (config or {}).get("collection_name", f"{app_slug}_memories")
        self.infer = (config or {}).get("infer", True)

        # ---------------------------------------------------------
        # 1. SETUP DIRECT MONGODB ACCESS (The "Backdoor")
        # ---------------------------------------------------------
        if MongoClient is None:
            raise Mem0MemoryServiceError("pymongo is required. pip install pymongo")

        try:
            self._client = MongoClient(mongo_uri)
            self._db = self._client[db_name]
            self.memories_collection = self._db[self.collection_name]
            logger.info(f"✅ Direct MongoDB connection established for {self.collection_name}")
        except BaseException as e:
            # MongoDB connection may raise various exceptions. We catch BaseException
            # (not Exception) to ensure we always raise Mem0MemoryServiceError for
            # consistent error handling, but we re-raise KeyboardInterrupt and SystemExit
            # to allow proper shutdown.
            if isinstance(e, KeyboardInterrupt | SystemExit):
                raise
            raise Mem0MemoryServiceError(f"Failed to connect to MongoDB directly: {e}") from e

        # Ensure GOOGLE_API_KEY is set for mem0 compatibility
        # (mem0 expects GOOGLE_API_KEY, not GEMINI_API_KEY)
        # This ensures we use the DIRECT Gemini API
        # (generativelanguage.googleapis.com), NOT Vertex AI
        if os.getenv("GEMINI_API_KEY") and not os.getenv("GOOGLE_API_KEY"):
            os.environ["GOOGLE_API_KEY"] = os.getenv("GEMINI_API_KEY")
            logger.info(
                "Set GOOGLE_API_KEY from GEMINI_API_KEY for mem0 compatibility (direct Gemini API)"
            )

        # Verify we're NOT using Vertex AI (which would use GOOGLE_APPLICATION_CREDENTIALS)
        if os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
            logger.warning(
                "GOOGLE_APPLICATION_CREDENTIALS is set - this would use Vertex AI, "
                "not direct Gemini API"
            )

        # 1. Models & Config
        embedding_model = (config or {}).get("embedding_model") or os.getenv(
            "EMBEDDING_MODEL", "text-embedding-3-small"
        )
        chat_model = (config or {}).get("chat_model") or os.getenv("CHAT_MODEL", "gpt-4o")

        # 2. Build Mem0 Configuration
        embedding_dims = (config or {}).get(
            "embedding_model_dims"
        ) or 1536  # Default for text-embedding-3-small
        mem0_config = {
            "vector_store": {
                "provider": "mongodb",
                "config": {
                    "db_name": db_name,
                    "collection_name": self.collection_name,
                    "mongo_uri": mongo_uri,
                    "embedding_model_dims": embedding_dims,
                },
            },
            "embedder": self._build_provider_config("embedder", embedding_model),
            "llm": self._build_provider_config("llm", chat_model) if self.infer else None,
        }

        # Add custom prompts to make fact extraction less restrictive (for document processing)
        # The default mem0 prompts are too restrictive and filter out general facts
        if self.infer:
            # Long prompt string - using concatenation to avoid line length issues
            fact_extraction_prompt = (
                "You are a helpful assistant that extracts key facts, insights, "
                "and information from documents and conversations.\n\n"
                "Your task is to extract factual information, insights, and important details "
                "from the provided content. Extract facts that would be useful for future "
                "reference, including:\n"
                "- Key concepts, definitions, and explanations\n"
                "- Important dates, names, and entities\n"
                "- Processes, procedures, and methodologies\n"
                "- Insights, conclusions, and recommendations\n"
                "- Relationships between concepts\n"
                "- Any other factual information that would be valuable to remember\n\n"
                'Return your response as a JSON object with a "facts" array. '
                "Each fact should be a clear, standalone statement.\n\n"
                "Example:\n"
                'Input: "The Innovation Hub was established on August 14, 2024 by '
                "David Vainchenker and Todd O'Brien. It focuses on experimental AI projects." + "\n"
                'Output: {{"facts": ["The Innovation Hub was established on August 14, 2024", '
                '"The Innovation Hub was founded by David Vainchenker and Todd O\'Brien", '
                '"The Innovation Hub focuses on experimental AI projects"]}}' + "\n\n"
                "Now extract facts from the following content:"
            )
            mem0_config["prompts"] = {"fact_extraction": fact_extraction_prompt}

        # Filter None
        mem0_config = {k: v for k, v in mem0_config.items() if v is not None}

        # 3. Initialize
        try:
            if hasattr(Memory, "from_config"):
                self.memory = Memory.from_config(mem0_config)
            else:
                self.memory = Memory(mem0_config)
            logger.info(f"✅ Mem0 Service active: {self.collection_name}")
        except (
            ValueError,
            TypeError,
            ConnectionError,
            OSError,
            AttributeError,
            RuntimeError,
        ) as e:
            raise Mem0MemoryServiceError(f"Failed to init Mem0: {e}") from e

    def _build_provider_config(self, component, model_name):
        """
        Build provider configuration for embeddings or LLM.

        For embeddings: Always use Azure OpenAI if available, otherwise OpenAI
        For LLM: Detect provider from model name (gemini/google -> google_ai, else Azure/OpenAI)
        """
        clean_model = (
            model_name.replace("azure/", "")
            .replace("openai/", "")
            .replace("google/", "")
            .replace("gemini/", "")
        )

        # For embeddings, always prefer Azure if available
        if component == "embedder":
            provider = "azure_openai" if os.getenv("AZURE_OPENAI_API_KEY") else "openai"
            cfg = {"provider": provider, "config": {"model": clean_model}}

            if provider == "azure_openai":
                # Support both AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME and AZURE_EMBEDDING_DEPLOYMENT
                deployment_name = (
                    os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME")
                    or os.getenv("AZURE_EMBEDDING_DEPLOYMENT")
                    or clean_model
                )
                # Use API version from env or default
                api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01")
                cfg["config"]["azure_kwargs"] = {
                    "api_version": api_version,
                    "azure_deployment": deployment_name,
                    "azure_endpoint": os.getenv("AZURE_OPENAI_ENDPOINT"),
                    "api_key": os.getenv("AZURE_OPENAI_API_KEY"),
                }
                logger.info(
                    f"Using Azure OpenAI embedding provider with deployment: "
                    f"{deployment_name}, API version: {api_version}"
                )
            return cfg

        # For LLM, detect provider from model name or env vars
        model_lower = model_name.lower()
        # Mem0 uses "gemini" as provider name (not "google_ai" or "vertexai")
        # GOOGLE_API_KEY should already be set in __init__ if GEMINI_API_KEY was provided
        has_gemini_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if "gemini" in model_lower or "google" in model_lower or has_gemini_key:
            # Use Gemini provider for Mem0 (direct Gemini API, NOT Vertex AI)
            provider = "gemini"
            # Explicitly set API key in config to ensure direct Gemini API usage
            api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
            cfg = {
                "provider": provider,
                "config": {
                    "model": clean_model,
                    "api_key": api_key,  # Explicitly set to ensure direct API usage
                },
            }
            logger.info(f"Using Gemini LLM provider (direct API) with model: {clean_model}")
            return cfg
        else:
            # Use Azure OpenAI if available, otherwise OpenAI
            provider = "azure_openai" if os.getenv("AZURE_OPENAI_API_KEY") else "openai"
            cfg = {"provider": provider, "config": {"model": clean_model}}

            if provider == "azure_openai":
                deployment_name = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", clean_model)
                # Use API version from env or default (match .env default)
                api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01")
                cfg["config"]["azure_kwargs"] = {
                    "api_version": api_version,
                    "azure_deployment": deployment_name,
                    "azure_endpoint": os.getenv("AZURE_OPENAI_ENDPOINT"),
                    "api_key": os.getenv("AZURE_OPENAI_API_KEY"),
                }
                logger.info(
                    f"Using Azure OpenAI LLM provider with deployment: "
                    f"{deployment_name}, API version: {api_version}"
                )
            else:
                logger.info(f"Using OpenAI LLM provider with model: {clean_model}")
            return cfg

    # --- Core Operations ---

    def add(
        self,
        messages: str | list[dict[str, str]],
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        bucket_id: str | None = None,
        bucket_type: str | None = None,
        raw_content: str | None = None,
        **kwargs,
    ) -> list[dict[str, Any]]:
        """
        Add memories with user scoping and metadata convenience.
        All operations are scoped per user_id for safety.
        bucket_id and bucket_type are stored in metadata for filtering convenience.
        """
        if isinstance(messages, str):
            messages = [{"role": "user", "content": messages}]

        final_metadata = dict(metadata) if metadata else {}

        # CRITICAL: Database indexing relies on these fields being in metadata
        # Include user_id in metadata ONLY if provided (supports non-SSO use cases)
        if user_id:
            final_metadata["user_id"] = str(user_id)

        if bucket_id:
            final_metadata["bucket_id"] = bucket_id
            final_metadata["context_id"] = bucket_id  # Backwards compatibility

        if bucket_type:
            final_metadata["bucket_type"] = bucket_type

        if raw_content:
            final_metadata["raw_content"] = raw_content

        # Infer defaults to configured value unless overridden
        infer = kwargs.pop("infer", self.infer)

        try:
            logger.debug(
                f"Calling mem0.add() with infer={infer}, user_id={user_id}, bucket_id={bucket_id}"
            )
            result = self.memory.add(
                messages=messages,
                user_id=str(user_id) if user_id else None,
                metadata=final_metadata,
                infer=infer,
                **kwargs,
            )
            # Log raw result before normalization
            logger.debug(
                f"mem0.add() raw result: type={type(result)}, "
                f"value={str(result)[:500] if result else 'None'}"
            )
            normalized = self._normalize_result(result)
            logger.info(
                f"mem0.add() normalized to {len(normalized)} memories "
                f"(raw result type: {type(result)})"
            )
            if not normalized and infer:
                logger.warning(
                    f"⚠️ mem0.add() with infer=True returned empty result. Raw result: {result}"
                )
                # Try to understand why - check if it's a dict with empty results
                if isinstance(result, dict):
                    logger.warning(f"   Result dict keys: {list(result.keys())}")
                    if "results" in result:
                        logger.warning(f"   result['results']: {result['results']}")
                    if "data" in result:
                        logger.warning(f"   result['data']: {result['data']}")
            return normalized
        except (
            ValueError,
            TypeError,
            ConnectionError,
            OSError,
            AttributeError,
            RuntimeError,
            KeyError,
        ) as e:
            error_msg = str(e)
            # Handle rate limit errors gracefully - try storing without inference
            if (
                "429" in error_msg
                or "RESOURCE_EXHAUSTED" in error_msg
                or "rate limit" in error_msg.lower()
            ):
                logger.warning(
                    f"Rate limit hit during memory inference, storing without inference: "
                    f"{error_msg}"
                )
                # Retry without inference to at least store the raw content
                try:
                    result = self.memory.add(
                        messages=messages,
                        user_id=str(user_id) if user_id else None,
                        metadata=final_metadata,
                        infer=False,  # Disable inference to avoid rate limits
                        **kwargs,
                    )
                    logger.info("Successfully stored memory without inference due to rate limit")
                    return self._normalize_result(result)
                except (
                    ValueError,
                    TypeError,
                    ConnectionError,
                    OSError,
                    AttributeError,
                    RuntimeError,
                    KeyError,
                ) as retry_error:
                    logger.exception("Failed to store memory even without inference")
                    raise Mem0MemoryServiceError(
                        f"Add failed (rate limited, retry also failed): {retry_error}"
                    ) from retry_error
            else:
                logger.exception("Mem0 Add Failed")
                raise Mem0MemoryServiceError(f"Add failed: {e}") from e

    def inject(
        self,
        memory: str | dict[str, Any],
        user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        """
        Manually inject a memory without LLM inference.

        This method allows direct insertion of memories without going through
        the inference pipeline. Useful for manually adding facts, preferences,
        or other structured data.

        Args:
            memory: Memory content as a string or dict with memory/text/content key
            user_id: User ID for scoping (optional but recommended)
            metadata: Additional metadata to store with the memory
            **kwargs: Additional provider-specific arguments

        Returns:
            Created memory object with ID and metadata

        Raises:
            Mem0MemoryServiceError: If injection operation fails
            ValueError: If memory content is invalid or empty
        """
        # Normalize input: convert dict to string if needed
        if isinstance(memory, dict):
            # Extract memory content from dict (support multiple key formats)
            memory_content = (
                memory.get("memory") or memory.get("text") or memory.get("content") or str(memory)
            )
            if not memory_content or not isinstance(memory_content, str):
                raise ValueError(
                    "Memory dict must contain 'memory', 'text', or 'content' key with string value"
                )
            # Merge any metadata from the dict
            if "metadata" in memory and isinstance(memory["metadata"], dict):
                final_metadata = dict(metadata) if metadata else {}
                final_metadata.update(memory["metadata"])
                metadata = final_metadata
        elif isinstance(memory, str):
            memory_content = memory.strip()
            if not memory_content:
                raise ValueError("Memory content cannot be empty")
        else:
            raise TypeError(f"Memory must be a string or dict, got {type(memory).__name__}")

        # Convert to messages format for add() method
        messages = [{"role": "user", "content": memory_content}]

        try:
            # Call add() with infer=False to bypass LLM inference
            logger.debug(
                f"Injecting memory without inference for user_id={user_id}, "
                f"memory_length={len(memory_content)}"
            )
            result = self.add(
                messages=messages,
                user_id=user_id,
                metadata=metadata,
                infer=False,  # Explicitly disable inference
                **kwargs,
            )

            # Return the first created memory (normalized format)
            if result and isinstance(result, list) and len(result) > 0:
                injected_memory = result[0]
                logger.info(
                    f"Successfully injected memory with id={injected_memory.get('id')} "
                    f"for user_id={user_id}"
                )
                return injected_memory
            else:
                # This shouldn't happen, but handle gracefully
                logger.warning(
                    f"add() returned empty result for inject() call. "
                    f"user_id={user_id}, memory_length={len(memory_content)}"
                )
                raise Mem0MemoryServiceError("Failed to inject memory: add() returned empty result")
        except (ValueError, TypeError):
            # Re-raise validation errors as-is
            raise
        except (
            ConnectionError,
            OSError,
            AttributeError,
            RuntimeError,
            KeyError,
        ) as e:
            logger.exception("Mem0 inject failed")
            raise Mem0MemoryServiceError(f"Inject failed: {e}") from e

    def get_all(
        self,
        user_id: str | None = None,
        limit: int = 100,
        filters: dict[str, Any] | None = None,
        **kwargs,
    ) -> list[dict[str, Any]]:
        """
        Get all memories with direct database filtering.
        """
        try:
            call_kwargs = {"limit": limit}
            if user_id:
                call_kwargs["user_id"] = str(user_id)
            if filters:
                call_kwargs["filters"] = filters  # Passed to MongoDB $match

            call_kwargs.update(kwargs)

            return self._normalize_result(self.memory.get_all(**call_kwargs))
        except (
            ValueError,
            TypeError,
            ConnectionError,
            OSError,
            AttributeError,
            RuntimeError,
            KeyError,
        ):
            logger.exception("Mem0 get_all failed")
            return []

    def search(
        self,
        query: str,
        user_id: str | None = None,
        limit: int = 5,
        filters: dict[str, Any] | None = None,
        **kwargs,
    ) -> list[dict[str, Any]]:
        """
        Semantic search with metadata filters, scoped per user.
        """
        final_filters = filters or {}

        try:
            call_kwargs = {"limit": limit}
            if final_filters:
                call_kwargs["filters"] = final_filters

            return self._normalize_result(
                self.memory.search(
                    query=query, user_id=str(user_id) if user_id else None, **call_kwargs, **kwargs
                )
            )
        except (
            ValueError,
            TypeError,
            ConnectionError,
            OSError,
            AttributeError,
            RuntimeError,
            KeyError,
        ):
            logger.exception("Mem0 search failed")
            return []

    def get(self, memory_id: str, user_id: str | None = None, **kwargs) -> dict[str, Any]:
        """
        Get memory by ID using direct MongoDB access for reliability.

        Mem0 stores memories with _id as the MongoDB document ID.
        Memory content and metadata are stored in the 'payload' field.
        """
        try:
            # Mem0 uses _id as the MongoDB document ID
            doc = self.memories_collection.find_one({"_id": memory_id})
            if doc:
                # Extract payload (where Mem0 stores the actual memory data)
                payload = doc.get("payload", {})

                # Build normalized memory document
                memory_doc = {
                    "id": str(doc["_id"]),  # Convert _id to id for API consistency
                    "memory": payload.get("memory") or payload.get("text"),
                    "text": payload.get("text") or payload.get("memory"),
                    "metadata": payload.get("metadata", {}),
                    "user_id": payload.get("user_id") or payload.get("metadata", {}).get("user_id"),
                    "created_at": payload.get("created_at"),
                    "updated_at": payload.get("updated_at"),
                }

                # Add any other payload fields
                for key, value in payload.items():
                    if key not in [
                        "memory",
                        "text",
                        "metadata",
                        "user_id",
                        "created_at",
                        "updated_at",
                    ]:
                        memory_doc[key] = value

                # Optional: Filter by user_id if provided
                if user_id:
                    doc_user_id = memory_doc.get("user_id")
                    if doc_user_id and str(doc_user_id) != str(user_id):
                        return None

                return memory_doc
            return None
        except (
            ValueError,
            TypeError,
            ConnectionError,
            OSError,
            AttributeError,
            RuntimeError,
            KeyError,
        ):
            # Fallback to Mem0 if direct access fails
            try:
                return self.memory.get(memory_id, **kwargs)
            except (
                ValueError,
                TypeError,
                ConnectionError,
                OSError,
                AttributeError,
                RuntimeError,
                KeyError,
            ):
                return None

    def delete(self, memory_id: str, user_id: str | None = None, **kwargs) -> bool:
        try:
            self.memory.delete(memory_id, **kwargs)
            return True
        except (
            AttributeError,
            ValueError,
            RuntimeError,
            KeyError,
            TypeError,
            ConnectionError,
            OSError,
        ):
            return False

    def delete_all(self, user_id: str | None = None, **kwargs) -> bool:
        try:
            self.memory.delete_all(user_id=user_id, **kwargs)
            return True
        except (
            AttributeError,
            ValueError,
            RuntimeError,
            KeyError,
            TypeError,
            ConnectionError,
            OSError,
        ):
            return False

    def update(
        self,
        memory_id: str,
        user_id: str | None = None,
        memory: str | None = None,
        data: str | dict[str, Any] | None = None,
        messages: str | list[dict[str, str]] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs,
    ) -> dict[str, Any] | None:
        """
        Robust Hybrid Update Pattern:

        This method uses a hybrid approach that combines Mem0's embedding capabilities
        with direct MongoDB control for maximum flexibility and reliability.

        **Architecture:**
        1. **Content Updates** → Routed via Mem0 (triggers automatic re-embedding)
        2. **Metadata Updates** → Routed via direct PyMongo (full control, no API limitations)
        3. **Return Value** → Always fetched from MongoDB (guaranteed correct structure)

        **Why Hybrid?**
        - Mem0's update() API doesn't support metadata parameter
        - Mem0's return values can be inconsistent (dict, list, or status messages)
        - Direct MongoDB access gives us full control over data persistence
        - We use Mem0 purely as an "embedding utility" for content changes

        Updates the memory content and/or metadata while preserving:
        - Original memory ID (never changes)
        - Creation timestamp (created_at) - preserved
        - Other existing fields - preserved unless explicitly updated

        Args:
            memory_id: The ID of the memory to update (required)
            user_id: The user ID who owns the memory (for scoping and security)
            memory: New memory content as a string (optional)
            data: Alternative parameter name for memory content.
                  Can be a string or dict with 'memory'/'text'/'content' key.
            messages: Alternative way to provide content as messages (optional).
                      Can be a string or list of dicts with 'content' key.
            metadata: Metadata updates (FULLY SUPPORTED via direct MongoDB).
                     Can update any metadata field, not limited by Mem0 API.
            **kwargs: Additional arguments passed to Mem0 operations

        Returns:
            Updated memory object with same ID, fetched directly from MongoDB,
            or None if memory not found

        Raises:
            Mem0MemoryServiceError: If update operation fails
            ValueError: If memory_id is invalid or empty

        Example:
            ```python
            # Update content and metadata (hybrid approach)
            updated = memory_service.update(
                memory_id="04f78986-dfad-46fe-8381-034bbee9a2fc",
                user_id="user123",
                memory="I love Python programming",
                metadata={"category": "technical", "updated": True}
            )

            # Update only metadata (content unchanged) - FULLY SUPPORTED
            updated = memory_service.update(
                memory_id="04f78986-dfad-46fe-8381-034bbee9a2fc",
                user_id="user123",
                metadata={"category": "updated", "priority": "high"}
            )

            # Update only content (no metadata)
            updated = memory_service.update(
                memory_id="04f78986-dfad-46fe-8381-034bbee9a2fc",
                user_id="user123",
                memory="Updated content only"
            )
            ```
        """
        if not memory_id or not isinstance(memory_id, str) or not memory_id.strip():
            raise ValueError("memory_id is required and must be a non-empty string")

        # 1. Normalize Inputs
        normalized_memory = self._normalize_content_input(memory, data, messages)
        normalized_metadata = self._normalize_metadata_input(metadata, data)

        # 2. Check Existence (Fast check via ID)
        # Mem0 uses _id as the MongoDB document ID
        existing = self.memories_collection.find_one(
            {"_id": memory_id}, {"_id": 1, "payload.user_id": 1, "payload.metadata.user_id": 1}
        )
        if not existing:
            logger.warning(f"Memory {memory_id} not found.")
            return None

        # Optional: Security Scope Check
        # Check user_id in payload (Mem0 stores it there)
        if user_id:
            payload = existing.get("payload", {})
            existing_user_id = payload.get("user_id") or payload.get("metadata", {}).get("user_id")
            if existing_user_id and str(existing_user_id) != str(user_id):
                logger.warning(f"Unauthorized update attempt for {memory_id} by {user_id}")
                return None

        # Use _id directly (Mem0's format)
        actual_id = memory_id

        try:
            # -------------------------------------------------
            # STEP A: Content Update (Via Mem0 for Vectors)
            # -------------------------------------------------
            if normalized_memory:
                logger.info(f"📝 Updating content for {actual_id} (triggering re-embedding)")
                # We use Mem0 here specifically because it handles the embedding logic.
                # We do NOT care what it returns.
                try:
                    # Use the actual_id (which Mem0 recognizes)
                    self.memory.update(memory_id=actual_id, data=normalized_memory)
                except BaseException as e:
                    # Mem0 is a third-party library that may raise any exception.
                    # We catch BaseException (not Exception) to ensure we always raise
                    # Mem0MemoryServiceError for consistent error handling, but we
                    # re-raise KeyboardInterrupt and SystemExit to allow proper shutdown.
                    if isinstance(e, KeyboardInterrupt | SystemExit):
                        raise
                    # If Mem0 fails (e.g. LLM rate limit, API error), we should abort
                    # or fall back to just text update without vector (risky for search).
                    logger.exception(f"Mem0 embedding update failed: {e}")
                    raise Mem0MemoryServiceError(f"Content update failed: {e}") from e

            # -------------------------------------------------
            # STEP B: Metadata Update (Direct PyMongo)
            # -------------------------------------------------
            if normalized_metadata:
                logger.info(f"🏷️ Updating metadata for {actual_id}")

                # Ensure user_id is in metadata for consistency
                if user_id:
                    normalized_metadata["user_id"] = str(user_id)

                # Mem0 stores everything in the 'payload' field
                # We need to update payload.metadata and payload.user_id
                update_fields = {}

                # Handle metadata updates (nested under payload.metadata)
                for k, v in normalized_metadata.items():
                    if k == "user_id":
                        # user_id can be at payload.user_id or payload.metadata.user_id
                        update_fields["payload.user_id"] = v
                        update_fields["payload.metadata.user_id"] = v
                    else:
                        update_fields[f"payload.metadata.{k}"] = v

                # Add timestamp to payload
                update_fields["payload.updated_at"] = datetime.utcnow().isoformat()

                # Execute Atomic Update - Mem0 uses _id
                self.memories_collection.update_one({"_id": actual_id}, {"$set": update_fields})

            # -------------------------------------------------
            # STEP C: Return The Truth (Direct DB Fetch)
            # -------------------------------------------------
            # We completely ignore Mem0's return value (which might be {"message": "ok"})
            # and fetch the actual document from the database.
            final_doc_raw = self.memories_collection.find_one({"_id": actual_id})

            if final_doc_raw:
                # Extract payload (where Mem0 stores the actual memory data)
                payload = final_doc_raw.get("payload", {})

                # Build normalized memory document (same format as get() method)
                final_doc = {
                    "id": str(final_doc_raw["_id"]),  # Convert _id to id for API consistency
                    "memory": payload.get("memory") or payload.get("text"),
                    "text": payload.get("text") or payload.get("memory"),
                    "metadata": payload.get("metadata", {}),
                    "user_id": payload.get("user_id") or payload.get("metadata", {}).get("user_id"),
                    "created_at": payload.get("created_at"),
                    "updated_at": payload.get("updated_at"),
                }

                # Add any other payload fields
                for key, value in payload.items():
                    if key not in [
                        "memory",
                        "text",
                        "metadata",
                        "user_id",
                        "created_at",
                        "updated_at",
                    ]:
                        final_doc[key] = value

                # Ensure date objects are serialized if your downstream expects strings
                if isinstance(final_doc.get("created_at"), datetime):
                    final_doc["created_at"] = final_doc["created_at"].isoformat()
                if isinstance(final_doc.get("updated_at"), datetime):
                    final_doc["updated_at"] = final_doc["updated_at"].isoformat()
            else:
                final_doc = None

            logger.info(
                f"Successfully updated memory {memory_id}",
                extra={
                    "memory_id": memory_id,
                    "content_updated": bool(normalized_memory),
                    "metadata_updated": bool(normalized_metadata),
                },
            )
            return final_doc

        except ValueError:
            # Re-raise validation errors as-is
            raise
        except BaseException as e:
            # Catch any unexpected errors during update. We catch BaseException
            # (not Exception) to ensure we always raise Mem0MemoryServiceError for
            # consistent error handling, but we re-raise KeyboardInterrupt and SystemExit
            # to allow proper shutdown.
            if isinstance(e, KeyboardInterrupt | SystemExit):
                raise
            logger.exception(f"Critical error during memory update for {memory_id}")
            raise Mem0MemoryServiceError(f"Update failed: {e}") from e

    def _normalize_content_input(
        self,
        memory: str | None,
        data: str | dict[str, Any] | None,
        messages: str | list[dict[str, str]] | None,
    ) -> str | None:
        """Normalize content input from various parameter formats."""
        if memory is not None:
            if not isinstance(memory, str):
                raise TypeError(f"memory parameter must be a string, got {type(memory).__name__}")
            return memory.strip() if memory.strip() else None
        if data is not None:
            if isinstance(data, str):
                return data.strip() if data.strip() else None
            if isinstance(data, dict):
                return data.get("memory") or data.get("text") or data.get("content")
            raise TypeError(f"data parameter must be a string or dict, got {type(data).__name__}")
        if messages is not None:
            if isinstance(messages, str):
                return messages.strip() if messages.strip() else None
            if isinstance(messages, list):
                return " ".join([m.get("content", "") for m in messages if isinstance(m, dict)])
            raise TypeError(
                f"messages parameter must be a string or list, got {type(messages).__name__}"
            )
        return None

    def _normalize_metadata_input(
        self, metadata: dict[str, Any] | None, data: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Normalize metadata input, extracting from data dict if needed."""
        if metadata is not None:
            if not isinstance(metadata, dict):
                raise TypeError(f"metadata parameter must be a dict, got {type(metadata).__name__}")
            return metadata
        if data is not None and isinstance(data, dict):
            metadata_from_data = data.get("metadata")
            if metadata_from_data is not None and not isinstance(metadata_from_data, dict):
                raise TypeError(
                    f"metadata in data parameter must be a dict, "
                    f"got {type(metadata_from_data).__name__}"
                )
            return metadata_from_data
        return None

    def _normalize_result(self, result: Any) -> list[dict[str, Any]]:
        """Normalize Mem0's return type (dict vs list)."""
        if result is None:
            return []
        if isinstance(result, dict):
            if "results" in result:
                return result["results"]
            if "data" in result:
                return result["data"]
            return [result]
        if isinstance(result, list):
            return result
        return []


def get_memory_service(
    mongo_uri: str,
    db_name: str,
    app_slug: str,
    config: dict[str, Any] | None = None,
    provider: str = "mem0",
) -> BaseMemoryService:
    """
    Factory function to create a memory service instance.

    Args:
        mongo_uri: MongoDB connection URI
        db_name: Database name
        app_slug: Application slug for scoping
        config: Memory service configuration dictionary
        provider: Memory provider to use (default: "mem0")

    Returns:
        BaseMemoryService instance (concrete implementation based on provider)

    Raises:
        ValueError: If provider is not supported
        Mem0MemoryServiceError: If Mem0 provider fails to initialize
    """
    if provider == "mem0":
        return Mem0MemoryService(mongo_uri, db_name, app_slug, config)
    else:
        raise ValueError(
            f"Unsupported memory provider: {provider}. "
            f"Supported providers: mem0. "
            f"Future providers can be added by implementing BaseMemoryService."
        )
