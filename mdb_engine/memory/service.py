"""
Mem0 Memory Service Implementation
Production-ready wrapper for Mem0.ai with strict metadata schema for MongoDB.
"""

import logging
import os
import tempfile
from typing import Any

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


class Mem0MemoryServiceError(Exception):
    pass


class Mem0MemoryService:
    """
    Production-ready Mem0 Memory Service with MongoDB integration.

    Features:
    - In-place memory updates preserving IDs and timestamps
    - Automatic embedding recomputation on content changes
    - Knowledge graph support (if enabled in Mem0 config)
    - Comprehensive error handling and logging
    - Backward compatibility with existing code

    All operations go through Mem0's API to ensure proper state management,
    graph updates, and relationship handling.
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

        # Merge metadata
        final_metadata = dict(metadata) if metadata else {}

        # CRITICAL: Database indexing relies on these fields being in metadata
        if bucket_id:
            final_metadata["bucket_id"] = bucket_id
            final_metadata["context_id"] = bucket_id  # Backwards compatibility

        if bucket_type:
            final_metadata["bucket_type"] = bucket_type

        # Store raw_content in metadata if provided (metadata convenience)
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
        Update an existing memory in-place with production-grade error handling.

        Updates the memory content and/or metadata while preserving:
        - Original memory ID (never changes)
        - Creation timestamp (created_at) - preserved
        - Other existing fields - preserved unless explicitly updated

        If content is updated, the embedding vector is automatically recomputed.

        Args:
            memory_id: The ID of the memory to update (required)
            user_id: The user ID who owns the memory (for scoping and security)
            memory: New memory content as a string (optional)
            data: Alternative parameter name for memory content (backward compatibility).
                  Can be a string or dict with 'memory'/'text'/'content' key.
            messages: Alternative way to provide content as messages (optional).
                      Can be a string or list of dicts with 'content' key.
            metadata: Metadata updates to merge with existing metadata (optional).
                     Merged, not replaced - existing keys are preserved unless overridden.
            **kwargs: Additional arguments passed to Mem0 operations

        Returns:
            Updated memory object with same ID, or None if memory not found

        Raises:
            Mem0MemoryServiceError: If update operation fails
            ValueError: If memory_id is invalid or empty

        Example:
            ```python
            # Update content and metadata
            updated = memory_service.update(
                memory_id="04f78986-dfad-46fe-8381-034bbee9a2fc",
                user_id="user123",
                memory="I love Python programming",
                metadata={"category": "technical", "updated": True}
            )

            # Update only metadata (content unchanged)
            updated = memory_service.update(
                memory_id="04f78986-dfad-46fe-8381-034bbee9a2fc",
                user_id="user123",
                metadata={"category": "updated"}
            )
            ```
        """
        # Input validation
        if not memory_id or not isinstance(memory_id, str) or not memory_id.strip():
            raise ValueError("memory_id is required and must be a non-empty string")

        try:
            # Normalize data parameter (backward compatibility)
            normalized_memory = self._normalize_content_input(memory, data, messages)
            normalized_metadata = self._normalize_metadata_input(metadata, data)

            # Verify memory exists before attempting update
            existing_memory = self.get(memory_id=memory_id, user_id=user_id, **kwargs)
            if not existing_memory:
                logger.warning(
                    f"Memory {memory_id} not found for update",
                    extra={"memory_id": memory_id, "user_id": user_id},
                )
                return None

            # Use Mem0's built-in update method
            # Mem0's Memory class update method handles:
            # - In-place updates preserving memory ID
            # - Automatic embedding recomputation
            # - Metadata merging
            # - User scoping
            # - Knowledge graph updates (if enabled)
            # - Relationship management
            if not hasattr(self.memory, "update") or not callable(self.memory.update):
                raise Mem0MemoryServiceError(
                    "Mem0 update method not available. "
                    "Please ensure you're using a compatible version of mem0ai "
                    "that supports updates. Install with: pip install --upgrade mem0ai"
                )

            result = self._update_via_mem0(
                memory_id=memory_id,
                user_id=user_id,
                memory=normalized_memory,
                metadata=normalized_metadata,
                **kwargs,
            )

            if result is None:
                logger.warning(
                    f"Mem0 update returned None for memory {memory_id}",
                    extra={"memory_id": memory_id, "user_id": user_id},
                )
                return None

            logger.info(
                f"Successfully updated memory {memory_id} using Mem0 update method",
                extra={
                    "memory_id": memory_id,
                    "content_updated": bool(normalized_memory),
                    "metadata_updated": bool(normalized_metadata),
                },
            )
            return result

        except ValueError:
            # Re-raise validation errors as-is
            raise
        except (AttributeError, TypeError, ValueError, KeyError) as e:
            logger.exception(
                f"Error updating memory {memory_id}",
                extra={"memory_id": memory_id, "user_id": user_id},
            )
            raise Mem0MemoryServiceError(f"Update failed: {e}") from e

    def _normalize_content_input(
        self,
        memory: str | None,
        data: str | dict[str, Any] | None,
        messages: str | list[dict[str, str]] | None,
    ) -> str | None:
        """
        Normalize content input from various parameter formats.

        Priority: memory > data > messages
        """
        # Already have memory content
        if memory:
            if not isinstance(memory, str):
                raise TypeError("memory parameter must be a string")
            return memory.strip() if memory.strip() else None

        # Check data parameter
        if data:
            if isinstance(data, str):
                return data.strip() if data.strip() else None
            elif isinstance(data, dict):
                content = data.get("memory") or data.get("text") or data.get("content")
                if content and isinstance(content, str):
                    return content.strip() if content.strip() else None

        # Check messages parameter
        if messages:
            if isinstance(messages, str):
                return messages.strip() if messages.strip() else None
            elif isinstance(messages, list):
                content_parts = []
                for msg in messages:
                    if isinstance(msg, dict) and "content" in msg:
                        content = msg["content"]
                        if isinstance(content, str) and content.strip():
                            content_parts.append(content.strip())
                if content_parts:
                    return " ".join(content_parts)

        return None

    def _normalize_metadata_input(
        self, metadata: dict[str, Any] | None, data: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Normalize metadata input, extracting from data dict if needed."""
        if metadata is not None and not isinstance(metadata, dict):
            raise TypeError("metadata must be a dict or None")

        # If metadata provided directly, use it
        if metadata is not None:
            return metadata

        # Check if metadata is in data dict
        if isinstance(data, dict) and "metadata" in data:
            data_metadata = data.get("metadata")
            if isinstance(data_metadata, dict):
                return data_metadata

        return None

    def _update_via_mem0(
        self,
        memory_id: str,
        user_id: str | None,
        memory: str | None,
        metadata: dict[str, Any] | None,
        **kwargs,
    ) -> dict[str, Any] | None:
        """
        Update memory using Mem0's built-in update method.

        This is the primary update path. Mem0's update method handles:
        - In-place updates preserving memory ID and created_at timestamp
        - Automatic embedding recomputation when content changes
        - Metadata merging
        - User scoping for security

        Args:
            memory_id: Memory ID to update
            user_id: User ID for scoping
            memory: New memory content (normalized)
            metadata: Metadata to merge (normalized)
            **kwargs: Additional arguments passed to Mem0

        Returns:
            Updated memory dict or None if not found

        Raises:
            Various exceptions from Mem0 if update fails
        """
        # Build update parameters matching Mem0's API
        # Mem0's update method signature:
        # update(memory_id, text=None, metadata=None, user_id=None, **kwargs)
        update_kwargs: dict[str, Any] = {"memory_id": memory_id}

        # Add user_id for scoping (Mem0 supports this)
        if user_id:
            update_kwargs["user_id"] = str(user_id)

        # Add text/content if provided
        # Mem0 uses "text" parameter for content
        if memory:
            update_kwargs["text"] = memory

        # Add metadata if provided
        # Mem0 merges metadata automatically
        if metadata is not None:
            update_kwargs["metadata"] = metadata

        # Pass through any additional kwargs
        update_kwargs.update(kwargs)

        logger.debug(
            f"Calling mem0.update() for memory_id={memory_id}",
            extra={
                "memory_id": memory_id,
                "has_content": bool(memory),
                "has_metadata": bool(metadata),
                "user_id": user_id,
            },
        )

        # Call Mem0's update method directly
        # This handles all the complexity: embedding recomputation, ID preservation, etc.
        result = self.memory.update(**update_kwargs)

        # Normalize result format
        # Mem0 may return dict, list, or other formats
        if isinstance(result, dict):
            return result
        elif isinstance(result, list) and len(result) > 0:
            # If list, return first item
            return result[0] if isinstance(result[0], dict) else None
        else:
            # If result is None or unexpected format, return None to trigger fallback
            logger.debug(
                f"Mem0 update returned unexpected format: {type(result)}",
                extra={"memory_id": memory_id},
            )
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


def get_memory_service(mongo_uri, db_name, app_slug, config=None):
    return Mem0MemoryService(mongo_uri, db_name, app_slug, config)
