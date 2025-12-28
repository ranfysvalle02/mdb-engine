"""
Mem0 Memory Service Implementation

This module provides a wrapper around Mem0.ai for intelligent memory management.
It integrates seamlessly with mdb-engine's MongoDB connection.
mem0 handles embeddings and LLM via environment variables (.env).
"""

import logging
import os
import tempfile
from typing import Any, Dict, List, Optional, Union

# Set MEM0_DIR environment variable early to avoid permission issues
# mem0 tries to create .mem0 directory at import time, so we set this before any import
if "MEM0_DIR" not in os.environ:
    # Use /tmp/.mem0 which should be writable in most environments
    mem0_dir = os.path.join(tempfile.gettempdir(), ".mem0")
    try:
        os.makedirs(mem0_dir, exist_ok=True)
        os.environ["MEM0_DIR"] = mem0_dir
    except OSError:
        # Fallback: try user's home directory
        try:
            home_dir = os.path.expanduser("~")
            mem0_dir = os.path.join(home_dir, ".mem0")
            os.makedirs(mem0_dir, exist_ok=True)
            os.environ["MEM0_DIR"] = mem0_dir
        except OSError:
            # Last resort: current directory (may fail but won't crash import)
            os.environ["MEM0_DIR"] = os.path.join(os.getcwd(), ".mem0")

# Try to import mem0 (optional dependency)
# Import is lazy to avoid permission issues at module load time
MEM0_AVAILABLE = None
Memory = None


def _check_mem0_available():
    """Lazy check if mem0 is available."""
    global MEM0_AVAILABLE, Memory
    if MEM0_AVAILABLE is None:
        try:
            from mem0 import Memory

            MEM0_AVAILABLE = True
        except ImportError:
            MEM0_AVAILABLE = False
            Memory = None
        except OSError as e:
            logger.warning(
                f"Failed to set up mem0 directory: {e}. Memory features may be limited."
            )
            MEM0_AVAILABLE = False
            Memory = None

    return MEM0_AVAILABLE


logger = logging.getLogger(__name__)


def _detect_provider_from_env() -> str:
    """
    Detect provider from environment variables.

    Returns:
        "azure" if Azure OpenAI credentials are present, otherwise "openai"
    """
    if os.getenv("AZURE_OPENAI_API_KEY") and os.getenv("AZURE_OPENAI_ENDPOINT"):
        return "azure"
    elif os.getenv("OPENAI_API_KEY"):
        return "openai"
    else:
        # Default to openai if nothing is configured
        return "openai"


def _detect_embedding_dimensions(model_name: str) -> Optional[int]:
    """
    Auto-detect embedding dimensions from model name.

    Args:
        model_name: Embedding model name (e.g., "text-embedding-3-small")

    Returns:
        Number of dimensions, or None if unknown (should use config/default)

    Examples:
        >>> _detect_embedding_dimensions("text-embedding-3-small")
        1536
    """
    # Normalize model name (remove provider prefix)
    normalized = model_name.lower()
    if "/" in normalized:
        normalized = normalized.split("/", 1)[1]

    # OpenAI models
    if "text-embedding-3-small" in normalized:
        return 1536
    elif "text-embedding-3-large" in normalized:
        return 3072
    elif "text-embedding-ada-002" in normalized or "ada-002" in normalized:
        return 1536
    elif "text-embedding-ada" in normalized:
        return 1536

    # Cohere models (common ones)
    if "embed-english-v3" in normalized:
        return 1024
    elif "embed-multilingual-v3" in normalized:
        return 1024

    # Unknown model - return None to use config/default
    return None


class Mem0MemoryServiceError(Exception):
    """
    Base exception for all Mem0 Memory Service failures.
    """

    pass


def _build_vector_store_config(
    db_name: str, collection_name: str, mongo_uri: str, embedding_model_dims: int
) -> Dict[str, Any]:
    """Build vector store configuration for mem0."""
    return {
        "vector_store": {
            "provider": "mongodb",
            "config": {
                "db_name": db_name,
                "collection_name": collection_name,
                "mongo_uri": mongo_uri,
                "embedding_model_dims": embedding_model_dims,
            },
        }
    }


def _build_embedder_config(
    provider: str, embedding_model: str, app_slug: str
) -> Dict[str, Any]:
    """Build embedder configuration for mem0."""
    clean_embedding_model = embedding_model.replace("azure/", "").replace("openai/", "")
    if provider == "azure":
        azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        azure_api_key = os.getenv("AZURE_OPENAI_API_KEY")
        azure_api_version = os.getenv(
            "AZURE_OPENAI_API_VERSION",
            os.getenv("OPENAI_API_VERSION", "2024-02-15-preview"),
        )

        if not azure_endpoint or not azure_api_key:
            raise Mem0MemoryServiceError(
                "Azure OpenAI requires AZURE_OPENAI_ENDPOINT and "
                "AZURE_OPENAI_API_KEY environment variables"
            )

        config = {
            "provider": "azure_openai",
            "config": {
                "model": clean_embedding_model,
                "azure_kwargs": {
                    "azure_deployment": clean_embedding_model,
                    "api_version": azure_api_version,
                    "azure_endpoint": azure_endpoint,
                    "api_key": azure_api_key,
                },
            },
        }
    else:
        config = {
            "provider": "openai",
            "config": {"model": clean_embedding_model},
        }

    provider_name = "Azure OpenAI" if provider == "azure" else "OpenAI"
    logger.info(
        f"Configuring mem0 embedder ({provider_name}): "
        f"provider='{config['provider']}', "
        f"model='{clean_embedding_model}'",
        extra={
            "app_slug": app_slug,
            "embedding_model": embedding_model,
            "embedder_provider": config["provider"],
            "provider": provider,
        },
    )
    return config


def _build_llm_config(
    provider: str, chat_model: str, temperature: float, app_slug: str
) -> Dict[str, Any]:
    """Build LLM configuration for mem0."""
    clean_chat_model = chat_model.replace("azure/", "").replace("openai/", "")
    if provider == "azure":
        deployment_name = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME") or clean_chat_model
        clean_chat_model = deployment_name

        azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        azure_api_key = os.getenv("AZURE_OPENAI_API_KEY")
        azure_api_version = os.getenv(
            "AZURE_OPENAI_API_VERSION",
            os.getenv("OPENAI_API_VERSION", "2024-02-15-preview"),
        )

        if not azure_endpoint or not azure_api_key:
            raise Mem0MemoryServiceError(
                "Azure OpenAI LLM requires AZURE_OPENAI_ENDPOINT and "
                "AZURE_OPENAI_API_KEY environment variables"
            )

        config = {
            "provider": "azure_openai",
            "config": {
                "model": clean_chat_model,
                "temperature": temperature,
                "azure_kwargs": {
                    "azure_deployment": clean_chat_model,
                    "api_version": azure_api_version,
                    "azure_endpoint": azure_endpoint,
                    "api_key": azure_api_key,
                },
            },
        }
    else:
        config = {
            "provider": "openai",
            "config": {"model": clean_chat_model, "temperature": temperature},
        }

    llm_provider_name = "Azure OpenAI" if provider == "azure" else "OpenAI"
    logger.info(
        f"Configuring mem0 LLM ({llm_provider_name}): "
        f"provider='{config['provider']}', "
        f"model='{clean_chat_model}'",
        extra={
            "app_slug": app_slug,
            "original_model": chat_model,
            "llm_provider": config["provider"],
            "llm_provider_type": provider,
            "temperature": temperature,
        },
    )
    return config


def _initialize_memory_instance(mem0_config: Dict[str, Any], app_slug: str) -> tuple:
    """Initialize Mem0 Memory instance and return (instance, init_method)."""
    logger.debug(
        "Initializing Mem0 Memory with config structure",
        extra={
            "app_slug": app_slug,
            "config_keys": list(mem0_config.keys()),
            "vector_store_provider": mem0_config.get("vector_store", {}).get(
                "provider"
            ),
            "embedder_provider": mem0_config.get("embedder", {}).get("provider"),
            "llm_provider": (
                mem0_config.get("llm", {}).get("provider")
                if mem0_config.get("llm")
                else None
            ),
            "full_config": mem0_config,
        },
    )

    init_method = None
    try:
        if hasattr(Memory, "from_config"):
            memory_instance = Memory.from_config(mem0_config)
            init_method = "Memory.from_config()"
        else:
            try:
                from mem0.config import Config

                config_obj = Config(**mem0_config)
                memory_instance = Memory(config_obj)
                init_method = "Memory(Config())"
            except (ImportError, TypeError) as config_error:
                logger.warning(
                    f"Could not create Config object, trying dict: {config_error}",
                    extra={"app_slug": app_slug},
                )
                memory_instance = Memory(mem0_config)
                init_method = "Memory(dict)"
    except (
        ImportError,
        AttributeError,
        TypeError,
        ValueError,
        RuntimeError,
        KeyError,
    ) as init_error:
        error_msg = str(init_error)
        logger.error(
            f"Failed to initialize Memory instance: {error_msg}",
            exc_info=True,
            extra={
                "app_slug": app_slug,
                "error": error_msg,
                "error_type": type(init_error).__name__,
                "config_keys": (
                    list(mem0_config.keys())
                    if isinstance(mem0_config, dict)
                    else "not_dict"
                ),
            },
        )
        raise Mem0MemoryServiceError(
            f"Failed to initialize Memory instance: {error_msg}. "
            f"Ensure mem0ai is installed and Azure OpenAI environment "
            f"variables are set correctly."
        ) from init_error

    return memory_instance, init_method


class Mem0MemoryService:
    """
    Service for managing user memories using Mem0.ai.

    This service provides intelligent memory management that:
    - Stores and retrieves memories in MongoDB (using mdb-engine's connection)
    - Uses mem0's embedder for embeddings (configured via environment variables)
    - Optionally extracts memories from conversations (requires LLM if infer: true)
    - Retrieves relevant memories for context-aware responses
    - Optionally builds knowledge graphs for entity relationships

    Embeddings and LLM are configured via environment variables (.env) and mem0 handles
    provider routing automatically.
    """

    def __init__(
        self,
        mongo_uri: str,
        db_name: str,
        app_slug: str,
        config: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize Mem0 Memory Service.

        Args:
            mongo_uri: MongoDB connection URI
            db_name: Database name
            app_slug: App slug (used for collection naming)
            config: Optional memory configuration dict (from manifest.json
                   memory_config)
                   Can include: collection_name, enable_graph, infer,
                   embedding_model, chat_model, temperature, etc.
                   Note: embedding_model_dims is auto-detected by embedding a
                   test string - no need to specify!
                   Embeddings and LLM are configured via environment variables
                   (.env).

        Raises:
            Mem0MemoryServiceError: If mem0 is not available or initialization fails
        """
        # Lazy check for mem0 availability
        if not _check_mem0_available():
            raise Mem0MemoryServiceError(
                "Mem0 dependencies not available. Install with: pip install mem0ai"
            )

        self.mongo_uri = mongo_uri
        self.db_name = db_name
        self.app_slug = app_slug

        # Extract config with defaults
        self.collection_name = (config or {}).get(
            "collection_name", f"{app_slug}_memories"
        )
        config_embedding_dims = (config or {}).get(
            "embedding_model_dims"
        )  # Optional - will be auto-detected
        self.enable_graph = (config or {}).get("enable_graph", False)
        self.infer = (config or {}).get("infer", True)
        self.async_mode = (config or {}).get("async_mode", True)

        # Get model names from config or environment
        # Default embedding model from config or env, fallback to common default
        embedding_model = (config or {}).get("embedding_model") or os.getenv(
            "EMBEDDING_MODEL", "text-embedding-3-small"
        )
        chat_model = (
            (config or {}).get("chat_model")
            or os.getenv("CHAT_MODEL")
            or os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o")
        )
        temperature = (config or {}).get(
            "temperature", float(os.getenv("LLM_TEMPERATURE", "0.0"))
        )

        # Detect provider from environment variables
        provider = _detect_provider_from_env()

        # Verify required environment variables are set
        if provider == "azure":
            if not os.getenv("AZURE_OPENAI_API_KEY") or not os.getenv(
                "AZURE_OPENAI_ENDPOINT"
            ):
                raise Mem0MemoryServiceError(
                    "Azure OpenAI provider requires AZURE_OPENAI_API_KEY and "
                    "AZURE_OPENAI_ENDPOINT environment variables to be set."
                )
        else:
            if not os.getenv("OPENAI_API_KEY"):
                raise Mem0MemoryServiceError(
                    "OpenAI provider requires OPENAI_API_KEY environment variable to be set."
                )

        try:
            # Detect embedding dimensions using model name (fallback method)
            detected_dims = _detect_embedding_dimensions(embedding_model)
            self.embedding_model_dims = (
                detected_dims
                if detected_dims is not None
                else (config_embedding_dims or 1536)
            )

            # Build mem0 config with MongoDB as vector store
            mem0_config = _build_vector_store_config(
                self.db_name,
                self.collection_name,
                self.mongo_uri,
                self.embedding_model_dims,
            )

            # Configure mem0 embedder
            mem0_config["embedder"] = _build_embedder_config(
                provider, embedding_model, app_slug
            )

            # Configure LLM for inference (if infer: true)
            if self.infer:
                mem0_config["llm"] = _build_llm_config(
                    provider, chat_model, temperature, app_slug
                )
        except (ValueError, TypeError, KeyError, AttributeError, ImportError) as e:
            logger.error(
                f"Failed to configure mem0: {e}",
                extra={"app_slug": app_slug, "error": str(e)},
            )
            raise Mem0MemoryServiceError(f"Failed to configure mem0: {e}") from e

        # Add graph store configuration if enabled
        if self.enable_graph:
            # Note: Graph store requires separate configuration (neo4j, memgraph, etc.)
            # For now, we just enable it - actual graph store config should come from manifest
            graph_config = (config or {}).get("graph_store")
            if graph_config:
                mem0_config["graph_store"] = graph_config
            else:
                logger.warning(
                    "Graph memory enabled but no graph_store config provided. "
                    "Graph features will not work. Configure graph_store in manifest.json",
                    extra={"app_slug": app_slug},
                )

        try:
            # Initialize Mem0 Memory instance
            self.memory, init_method = _initialize_memory_instance(
                mem0_config, app_slug
            )

            # Verify the memory instance has required methods
            if not hasattr(self.memory, "get_all"):
                logger.warning(
                    f"Memory instance missing 'get_all' method for app '{app_slug}'",
                    extra={"app_slug": app_slug, "init_method": init_method},
                )
            if not hasattr(self.memory, "add"):
                logger.warning(
                    f"Memory instance missing 'add' method for app '{app_slug}'",
                    extra={"app_slug": app_slug, "init_method": init_method},
                )

            logger.info(
                f"Mem0 Memory Service initialized using {init_method} for app '{app_slug}'",
                extra={
                    "app_slug": app_slug,
                    "init_method": init_method,
                    "collection_name": self.collection_name,
                    "db_name": self.db_name,
                    "enable_graph": self.enable_graph,
                    "infer": self.infer,
                    "has_get_all": hasattr(self.memory, "get_all"),
                    "has_add": hasattr(self.memory, "add"),
                    "embedder_provider": mem0_config.get("embedder", {}).get(
                        "provider"
                    ),
                    "embedder_model": mem0_config.get("embedder", {})
                    .get("config", {})
                    .get("model"),
                    "llm_provider": (
                        mem0_config.get("llm", {}).get("provider")
                        if self.infer
                        else None
                    ),
                    "llm_model": (
                        mem0_config.get("llm", {}).get("config", {}).get("model")
                        if self.infer
                        else None
                    ),
                },
            )
        except (
            ImportError,
            AttributeError,
            TypeError,
            ValueError,
            RuntimeError,
            KeyError,
        ) as e:
            logger.error(
                f"Failed to initialize Mem0 Memory Service for app '{app_slug}': {e}",
                exc_info=True,
                extra={"app_slug": app_slug, "error": str(e)},
            )
            raise Mem0MemoryServiceError(
                f"Failed to initialize Mem0 Memory Service: {e}"
            ) from e

    def add(
        self,
        messages: Union[str, List[Dict[str, str]]],
        user_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> List[Dict[str, Any]]:
        """
        Add memories from messages or text.

        This method intelligently extracts memories from conversations
        and stores them in MongoDB. Memories are processed asynchronously
        by default for better performance.

        Args:
            messages: Either a string or list of message dicts with 'role' and 'content'
            user_id: Optional user ID to associate memories with
            metadata: Optional metadata dict (e.g., {"category": "preferences"})
            **kwargs: Additional mem0.add() parameters:
                    - infer: Whether to infer memories (default: True)
                    Note: async_mode is not a valid parameter for Mem0's add()
                          method.
                          Mem0 processes memories asynchronously by default.
                          Graph features are configured at initialization via
                          enable_graph in config, not per-add call.

        Returns:
            List of memory events (each with 'id', 'event', 'data')

        Example:
            ```python
            memories = memory_service.add(
                messages=[
                    {"role": "user", "content": "I love sci-fi movies"},
                    {"role": "assistant", "content": "Noted! I'll remember that."}
                ],
                user_id="alice",
                metadata={"category": "preferences"}
            )
            ```
        """
        try:
            # Normalize messages format
            if isinstance(messages, str):
                messages = [{"role": "user", "content": messages}]

            # Prepare kwargs with defaults from config
            # async_mode is not a valid parameter for Mem0's add() method
            add_kwargs = {"infer": kwargs.pop("infer", self.infer), **kwargs}
            add_kwargs.pop("async_mode", None)

            # enable_graph is configured at initialization, not per-add call
            # Mem0 processes asynchronously by default
            # Log message content preview for debugging
            message_preview = []
            for i, msg in enumerate(messages[:5]):  # Show first 5 messages
                if isinstance(msg, dict):
                    role = msg.get("role", "unknown")
                    content = msg.get("content", "")
                    preview = content[:150] + "..." if len(content) > 150 else content
                    message_preview.append(f"{i+1}. {role}: {preview}")

            logger.info(
                f"🔵 CALLING mem0.add() - app_slug='{self.app_slug}', "
                f"user_id='{user_id}', messages={len(messages)}, "
                f"infer={add_kwargs.get('infer', 'N/A')}",
                extra={
                    "app_slug": self.app_slug,
                    "user_id": user_id,
                    "collection_name": self.collection_name,
                    "message_count": len(messages),
                    "message_preview": "\n".join(message_preview),
                    "infer": add_kwargs.get("infer"),
                    "metadata": metadata or {},
                    "add_kwargs": add_kwargs,
                },
            )

            result = self.memory.add(
                messages=messages,
                user_id=str(user_id),  # Ensure string - mem0 might be strict about this
                metadata=metadata or {},
                **add_kwargs,
            )

            # Normalize result format - mem0.add() may return different formats
            if isinstance(result, dict):
                # Some versions return {"results": [...]} or {"data": [...]}
                if "results" in result:
                    result = result["results"]
                elif "data" in result:
                    result = result["data"] if isinstance(result["data"], list) else []
                elif "memory" in result:
                    # Single memory object
                    result = [result]

            # Ensure result is always a list
            if not isinstance(result, list):
                result = [result] if result else []

            result_length = len(result) if isinstance(result, list) else 0
            logger.debug(
                f"Raw result from mem0.add(): type={type(result)}, "
                f"length={result_length}",
                extra={
                    "app_slug": self.app_slug,
                    "user_id": user_id,
                    "result_type": str(type(result)),
                    "is_list": isinstance(result, list),
                    "result_length": len(result) if isinstance(result, list) else 0,
                    "result_sample": (
                        result[0]
                        if result and isinstance(result, list) and len(result) > 0
                        else None
                    ),
                },
            )

            logger.info(
                f"Added {len(result)} memories for user '{user_id}'",
                extra={
                    "app_slug": self.app_slug,
                    "user_id": user_id,
                    "message_count": len(messages),
                    "memory_count": len(result) if isinstance(result, list) else 0,
                    "memory_ids": (
                        [
                            m.get("id") or m.get("_id")
                            for m in result
                            if isinstance(m, dict)
                        ]
                        if result
                        else []
                    ),
                    "infer_enabled": add_kwargs.get("infer", False),
                    "has_llm": (
                        hasattr(self.memory, "llm") and self.memory.llm is not None
                        if hasattr(self.memory, "llm")
                        else False
                    ),
                },
            )

            # If 0 memories and infer is enabled, log helpful info
            if len(result) == 0 and add_kwargs.get("infer", False):
                # Extract conversation content for analysis
                conversation_text = "\n".join(
                    [
                        f"{msg.get('role', 'unknown')}: {msg.get('content', '')[:100]}"
                        for msg in messages[:5]
                    ]
                )

                logger.info(
                    "ℹ️ mem0.add() returned 0 memories. This is normal if the "
                    "conversation doesn't contain extractable facts. "
                    "mem0 extracts personal preferences, facts, and details - "
                    "not generic greetings or small talk. "
                    "Try conversations like 'I love pizza' or 'I work as a "
                    "software engineer' to see memories extracted.",
                    extra={
                        "app_slug": self.app_slug,
                        "user_id": user_id,
                        "message_count": len(messages),
                        "infer": True,
                        "has_llm": (
                            hasattr(self.memory, "llm") and self.memory.llm is not None
                            if hasattr(self.memory, "llm")
                            else False
                        ),
                        "conversation_preview": conversation_text,
                    },
                )

            return result

        except (AttributeError, TypeError, ValueError, RuntimeError, KeyError) as e:
            logger.error(
                f"Failed to add memories: {e}",
                exc_info=True,
                extra={"app_slug": self.app_slug, "user_id": user_id, "error": str(e)},
            )
            raise Mem0MemoryServiceError(f"Failed to add memories: {e}") from e

    def get_all(
        self,
        user_id: Optional[str] = None,
        limit: Optional[int] = None,
        retry_on_empty: bool = True,
        max_retries: int = 2,
        retry_delay: float = 0.5,
        **kwargs,
    ) -> List[Dict[str, Any]]:
        """
        Get all memories for a user.

        Args:
            user_id: User ID to retrieve memories for
            limit: Optional limit on number of memories to return
            retry_on_empty: If True, retry if result is empty (handles async processing delay)
            max_retries: Maximum number of retries if result is empty
            retry_delay: Delay in seconds between retries
            **kwargs: Additional mem0.get_all() parameters

        Returns:
            List of memory dictionaries
        """
        import time

        try:
            # Verify memory instance is valid before calling
            if not hasattr(self, "memory") or self.memory is None:
                logger.error(
                    f"Memory instance is None or missing for app '{self.app_slug}'",
                    extra={"app_slug": self.app_slug, "user_id": user_id},
                )
                return []

            logger.info(
                f"🟢 CALLING mem0.get_all() - app_slug='{self.app_slug}', "
                f"user_id='{user_id}' (type: {type(user_id).__name__}), "
                f"collection='{self.collection_name}'",
                extra={
                    "app_slug": self.app_slug,
                    "user_id": user_id,
                    "user_id_type": type(user_id).__name__,
                    "user_id_repr": repr(user_id),
                    "collection_name": self.collection_name,
                    "limit": limit,
                    "kwargs": kwargs,
                },
            )

            result = None
            attempt = 0

            while attempt <= max_retries:
                if attempt > 0:
                    # Wait before retry to allow async processing to complete
                    time.sleep(retry_delay * attempt)  # Exponential backoff
                    logger.debug(
                        f"Retrying mem0.get_all (attempt {attempt + 1}/{max_retries + 1})",
                        extra={
                            "app_slug": self.app_slug,
                            "user_id": user_id,
                            "attempt": attempt + 1,
                        },
                    )

                # Call with safety - catch any exceptions from mem0
                try:
                    logger.debug(
                        f"🟢 EXECUTING: memory.get_all(user_id='{user_id}', "
                        f"limit={limit}, kwargs={kwargs})",
                        extra={
                            "app_slug": self.app_slug,
                            "user_id": user_id,
                            "collection_name": self.collection_name,
                            "attempt": attempt + 1,
                        },
                    )
                    result = self.memory.get_all(
                        user_id=str(user_id), limit=limit, **kwargs
                    )  # Ensure string
                    result_length = (
                        len(result) if isinstance(result, (list, dict)) else "N/A"
                    )
                    logger.debug(
                        f"🟢 RESULT RECEIVED: type={type(result).__name__}, "
                        f"length={result_length}",
                        extra={
                            "app_slug": self.app_slug,
                            "user_id": user_id,
                            "result_type": type(result).__name__,
                            "result_length": (
                                len(result) if isinstance(result, (list, dict)) else 0
                            ),
                            "attempt": attempt + 1,
                        },
                    )
                except AttributeError as attr_error:
                    logger.error(
                        f"Memory.get_all method not available: {attr_error}",
                        extra={
                            "app_slug": self.app_slug,
                            "user_id": user_id,
                            "error": str(attr_error),
                            "attempt": attempt + 1,
                        },
                    )
                    return []  # Return empty list instead of retrying
                # Type 4: Let other exceptions bubble up to framework handler

                logger.debug(
                    f"Raw result from mem0.get_all (attempt {attempt + 1}): type={type(result)}",
                    extra={
                        "app_slug": self.app_slug,
                        "user_id": user_id,
                        "attempt": attempt + 1,
                        "result_type": str(type(result)),
                        "is_dict": isinstance(result, dict),
                        "is_list": isinstance(result, list),
                        "result_length": (
                            len(result) if isinstance(result, (list, dict)) else 0
                        ),
                    },
                )

                # Handle Mem0 v2 API response format: {"results": [...], "total": N}
                if isinstance(result, dict):
                    if "results" in result:
                        result = result["results"]  # Extract results array
                        logger.debug(
                            "Extracted results from dict response",
                            extra={
                                "app_slug": self.app_slug,
                                "user_id": user_id,
                                "result_count": (
                                    len(result) if isinstance(result, list) else 0
                                ),
                            },
                        )
                    elif "data" in result:
                        # Alternative format: {"data": [...]}
                        result = (
                            result["data"] if isinstance(result["data"], list) else []
                        )

                # Ensure result is always a list for backward compatibility
                if not isinstance(result, list):
                    result = [result] if result else []

                # If we got results or retries are disabled, break
                if not retry_on_empty or len(result) > 0 or attempt >= max_retries:
                    break

                attempt += 1

            logger.info(
                f"Retrieved {len(result)} memories for user '{user_id}' "
                f"(after {attempt + 1} attempt(s))",
                extra={
                    "app_slug": self.app_slug,
                    "user_id": user_id,
                    "memory_count": len(result) if isinstance(result, list) else 0,
                    "attempts": attempt + 1,
                    "sample_memory": (
                        result[0]
                        if result and isinstance(result, list) and len(result) > 0
                        else None
                    ),
                },
            )

            return result

        except (AttributeError, TypeError, ValueError, RuntimeError, KeyError) as e:
            attempt_num = (
                attempt + 1 if "attempt" in locals() and attempt is not None else 1
            )
            logger.error(
                f"Failed to get memories: {e}",
                exc_info=True,
                extra={
                    "app_slug": self.app_slug,
                    "user_id": user_id,
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "attempt": attempt_num,
                },
            )
            raise Mem0MemoryServiceError(f"Failed to get memories: {e}") from e

    def search(
        self,
        query: str,
        user_id: Optional[str] = None,
        limit: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
        filters: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> List[Dict[str, Any]]:
        """
        Search for relevant memories using semantic search.

        Args:
            query: Search query string
            user_id: Optional user ID to scope search to
            limit: Optional limit on number of results
            metadata: Optional metadata dict to filter results
                     (e.g., {"category": "travel"})
                     Deprecated in favor of 'filters' parameter for Mem0 1.0.0+
            filters: Optional enhanced filters dict (Mem0 1.0.0+) with operators
                     like {"category": {"eq": "travel"}}
            **kwargs: Additional mem0.search() parameters

        Returns:
            List of relevant memory dictionaries

        Example:
            ```python
            # Simple metadata filter (backward compatible)
            results = memory_service.search(
                query="What are my travel plans?",
                user_id="alice",
                metadata={"category": "travel"}
            )

            # Enhanced filters (Mem0 1.0.0+)
            results = memory_service.search(
                query="high priority tasks",
                user_id="alice",
                filters={
                    "AND": [
                        {"category": "work"},
                        {"priority": {"gte": 5}}
                    ]
                }
            )
            ```
        """
        try:
            # Build search kwargs
            search_kwargs = {"limit": limit, **kwargs}

            # Prefer 'filters' parameter (Mem0 1.0.0+) over 'metadata' (legacy)
            if filters is not None:
                search_kwargs["filters"] = filters
            elif metadata:
                # Backward compatibility: convert simple metadata to filters format
                # Try 'filters' first, fallback to 'metadata' if it fails
                search_kwargs["filters"] = metadata

            # Call search - try with filters first, fallback to metadata if needed
            try:
                result = self.memory.search(
                    query=query, user_id=user_id, **search_kwargs
                )
            except (TypeError, ValueError) as e:
                # If filters parameter doesn't work, try with metadata (backward compatibility)
                if "filters" in search_kwargs and metadata:
                    logger.debug(
                        f"Filters parameter failed, trying metadata parameter: {e}",
                        extra={"app_slug": self.app_slug, "user_id": user_id},
                    )
                    search_kwargs.pop("filters", None)
                    search_kwargs["metadata"] = metadata
                    result = self.memory.search(
                        query=query, user_id=user_id, **search_kwargs
                    )
                else:
                    raise

            # Handle response format - search may return dict with "results" key
            if isinstance(result, dict):
                if "results" in result:
                    result = result["results"]
                elif "data" in result:
                    result = result["data"] if isinstance(result["data"], list) else []

            # Ensure result is always a list
            if not isinstance(result, list):
                result = [result] if result else []

            logger.debug(
                f"Searched memories for user '{user_id}'",
                extra={
                    "app_slug": self.app_slug,
                    "user_id": user_id,
                    "query": query,
                    "metadata_filter": metadata,
                    "filters": filters,
                    "result_count": len(result) if isinstance(result, list) else 0,
                },
            )

            return result

        except (AttributeError, TypeError, ValueError, RuntimeError, KeyError) as e:
            logger.error(
                f"Failed to search memories: {e}",
                exc_info=True,
                extra={
                    "app_slug": self.app_slug,
                    "user_id": user_id,
                    "query": query,
                    "metadata": metadata,
                    "filters": filters,
                    "error": str(e),
                },
            )
            raise Mem0MemoryServiceError(f"Failed to search memories: {e}") from e

    def get(
        self, memory_id: str, user_id: Optional[str] = None, **kwargs
    ) -> Dict[str, Any]:
        """
        Get a single memory by ID.

        Args:
            memory_id: Memory ID to retrieve
            user_id: Optional user ID for scoping
            **kwargs: Additional mem0.get() parameters

        Returns:
            Memory dictionary

        Example:
            ```python
            memory = memory_service.get(memory_id="mem_123", user_id="alice")
            ```
        """
        try:
            # Mem0's get() method doesn't accept user_id as a parameter
            # User scoping should be handled via metadata or filters if needed
            # For now, we just get by memory_id
            result = self.memory.get(memory_id=memory_id, **kwargs)

            # If user_id is provided, verify the memory belongs to that user
            # by checking metadata or user_id field in the result
            if user_id and isinstance(result, dict):
                result_user_id = result.get("user_id") or result.get(
                    "metadata", {}
                ).get("user_id")
                if result_user_id and result_user_id != user_id:
                    logger.warning(
                        f"Memory {memory_id} does not belong to user {user_id}",
                        extra={
                            "memory_id": memory_id,
                            "user_id": user_id,
                            "result_user_id": result_user_id,
                        },
                    )
                    raise Mem0MemoryServiceError(
                        f"Memory {memory_id} does not belong to user {user_id}"
                    )

            logger.debug(
                f"Retrieved memory '{memory_id}' for user '{user_id}'",
                extra={
                    "app_slug": self.app_slug,
                    "user_id": user_id,
                    "memory_id": memory_id,
                },
            )

            return result

        except (AttributeError, TypeError, ValueError, RuntimeError, KeyError) as e:
            logger.error(
                f"Failed to get memory: {e}",
                exc_info=True,
                extra={
                    "app_slug": self.app_slug,
                    "user_id": user_id,
                    "memory_id": memory_id,
                    "error": str(e),
                },
            )
            raise Mem0MemoryServiceError(f"Failed to get memory: {e}") from e

    def update(
        self,
        memory_id: str,
        data: Union[str, List[Dict[str, str]]],
        user_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Update a memory by ID with new data.

        Args:
            memory_id: Memory ID to update
            data: New data (string or list of message dicts with 'role' and 'content')
            user_id: Optional user ID for scoping
            metadata: Optional metadata dict to update
            **kwargs: Additional mem0.update() parameters

        Returns:
            Updated memory dictionary

        Example:
            ```python
            updated = memory_service.update(
                memory_id="mem_123",
                data="I am a software engineer using Python and FastAPI.",
                user_id="bob"
            )
            ```
        """
        try:
            # Normalize data format
            if isinstance(data, str):
                data = [{"role": "user", "content": data}]

            # Mem0's update() may not accept user_id directly
            # Pass it in metadata if user_id is provided
            update_metadata = metadata or {}
            if user_id:
                update_metadata["user_id"] = user_id

            # Try with user_id first, fall back without it if it fails
            try:
                result = self.memory.update(
                    memory_id=memory_id,
                    data=data,
                    user_id=user_id,
                    metadata=update_metadata,
                    **kwargs,
                )
            except TypeError as e:
                if "unexpected keyword argument 'user_id'" in str(e):
                    # Mem0 doesn't accept user_id, try without it
                    result = self.memory.update(
                        memory_id=memory_id,
                        data=data,
                        metadata=update_metadata,
                        **kwargs,
                    )
                else:
                    raise

            logger.info(
                f"Updated memory '{memory_id}' for user '{user_id}'",
                extra={
                    "app_slug": self.app_slug,
                    "user_id": user_id,
                    "memory_id": memory_id,
                },
            )

            return result

        except (AttributeError, TypeError, ValueError, RuntimeError, KeyError) as e:
            logger.error(
                f"Failed to update memory: {e}",
                exc_info=True,
                extra={
                    "app_slug": self.app_slug,
                    "user_id": user_id,
                    "memory_id": memory_id,
                    "error": str(e),
                },
            )
            raise Mem0MemoryServiceError(f"Failed to update memory: {e}") from e

    def delete(self, memory_id: str, user_id: Optional[str] = None, **kwargs) -> bool:
        """
        Delete a memory by ID.

        Args:
            memory_id: Memory ID to delete
            user_id: Optional user ID for scoping
            **kwargs: Additional mem0.delete() parameters

        Returns:
            True if deletion was successful
        """
        try:
            # Mem0's delete() may not accept user_id directly
            # Try with user_id first, fall back without it if it fails
            try:
                result = self.memory.delete(
                    memory_id=memory_id, user_id=user_id, **kwargs
                )
            except TypeError as e:
                if "unexpected keyword argument 'user_id'" in str(e):
                    # Mem0 doesn't accept user_id, try without it
                    # User scoping should be handled via metadata or filters
                    result = self.memory.delete(memory_id=memory_id, **kwargs)
                else:
                    raise

            logger.info(
                f"Deleted memory '{memory_id}' for user '{user_id}'",
                extra={
                    "app_slug": self.app_slug,
                    "user_id": user_id,
                    "memory_id": memory_id,
                },
            )

            return result

        except (AttributeError, TypeError, ValueError, RuntimeError, KeyError) as e:
            logger.error(
                f"Failed to delete memory: {e}",
                exc_info=True,
                extra={
                    "app_slug": self.app_slug,
                    "user_id": user_id,
                    "memory_id": memory_id,
                    "error": str(e),
                },
            )
            raise Mem0MemoryServiceError(f"Failed to delete memory: {e}") from e

    def delete_all(self, user_id: Optional[str] = None, **kwargs) -> bool:
        """
        Delete all memories for a user.

        Args:
            user_id: User ID to delete all memories for
            **kwargs: Additional mem0.delete_all() parameters

        Returns:
            True if deletion was successful

        Example:
            ```python
            success = memory_service.delete_all(user_id="alice")
            ```
        """
        try:
            result = self.memory.delete_all(user_id=user_id, **kwargs)

            logger.info(
                f"Deleted all memories for user '{user_id}'",
                extra={"app_slug": self.app_slug, "user_id": user_id},
            )

            return result

        except (AttributeError, TypeError, ValueError, RuntimeError, KeyError) as e:
            logger.error(
                f"Failed to delete all memories: {e}",
                exc_info=True,
                extra={"app_slug": self.app_slug, "user_id": user_id, "error": str(e)},
            )
            raise Mem0MemoryServiceError(f"Failed to delete all memories: {e}") from e


def get_memory_service(
    mongo_uri: str, db_name: str, app_slug: str, config: Optional[Dict[str, Any]] = None
) -> Mem0MemoryService:
    """
    Get or create a Mem0MemoryService instance (cached).

    Args:
        mongo_uri: MongoDB connection URI
        db_name: Database name
        app_slug: App slug
        config: Optional memory configuration dict

    Returns:
        Mem0MemoryService instance
    """
    # Lazy check for mem0 availability
    if not _check_mem0_available():
        raise Mem0MemoryServiceError(
            "Mem0 dependencies not available. Install with: pip install mem0ai"
        )

    return Mem0MemoryService(
        mongo_uri=mongo_uri, db_name=db_name, app_slug=app_slug, config=config
    )
