"""
LLM Service Implementation
"""

import logging
import time
from typing import Type, TypeVar, Union, List, Dict, Optional, Any
from functools import lru_cache
from enum import Enum

# --- Third Party Imports (Optional) ---
try:
    import instructor
    from litellm import acompletion, aembedding, exceptions as litellm_exceptions
    from pydantic import BaseModel
    from pydantic_settings import BaseSettings, SettingsConfigDict
    from tenacity import (
        retry,
        stop_after_attempt,
        wait_exponential,
        retry_if_exception_type,
        before_sleep_log
    )
    LLM_DEPENDENCIES_AVAILABLE = True
except ImportError as e:
    LLM_DEPENDENCIES_AVAILABLE = False
    # Create stubs for when dependencies are not available
    class BaseModel:
        pass
    class BaseSettings:
        pass
    class SettingsConfigDict:
        pass
    # These will raise ImportError if used without dependencies
    instructor = None
    acompletion = None
    aembedding = None
    litellm_exceptions = None
    retry = lambda **kwargs: lambda f: f
    stop_after_attempt = None
    wait_exponential = None
    retry_if_exception_type = None
    before_sleep_log = None

# --- 1. CONFIGURATION & LOGGING ---

# Configure module-level logger (Connect this to Datadog/Splunk in production)
logger = logging.getLogger("mdb_runtime.llm")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# Generic Type for Pydantic Models (allows method signatures to return the exact class passed in)
T = TypeVar("T", bound=BaseModel)


class LLMSettings(BaseSettings):
    """
    Application-level LLM Configuration.
    Reads from environment variables (e.g., .env file).
    """
    # API Keys (Automatically loaded from env vars like OPENAI_API_KEY)
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    voyage_api_key: Optional[str] = None
    cohere_api_key: Optional[str] = None
    
    # Defaults
    default_chat_model: str = "gpt-4o"
    default_embedding_model: str = "voyage/voyage-2"  # Prefixed for LiteLLM routing
    default_temperature: float = 0.0
    
    # Retry Settings
    max_retries: int = 4
    
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


# --- 2. CUSTOM EXCEPTIONS ---

class LLMServiceError(Exception):
    """
    Base exception for all LLM Service failures.
    Catch this in your FastAPI global exception handler to return HTTP 503.
    """
    pass


# --- 3. THE CORE SERVICE ---

class LLMService:
    """
    Singleton service for handling LLM interactions.
    Inject this into your FastAPI routes using `Depends(get_llm_service)`.
    """

    def __init__(self, settings: Optional[LLMSettings] = None, config: Optional[Dict[str, Any]] = None):
        """
        Initialize LLM Service.
        
        Args:
            settings: LLMSettings instance (optional, will create default if None)
            config: Optional dict with LLM configuration (from manifest.json llm_config)
                   Can override settings with: default_chat_model, default_embedding_model, 
                   default_temperature, max_retries
        """
        if not LLM_DEPENDENCIES_AVAILABLE:
            raise LLMServiceError(
                "LLM dependencies not available. Install with: "
                "pip install litellm instructor pydantic pydantic-settings tenacity"
            )
        
        self.settings = settings or LLMSettings()
        
        # Override settings with manifest config if provided
        if config:
            if "default_chat_model" in config:
                self.settings.default_chat_model = config["default_chat_model"]
            if "default_embedding_model" in config:
                self.settings.default_embedding_model = config["default_embedding_model"]
            if "default_temperature" in config:
                self.settings.default_temperature = float(config["default_temperature"])
            if "max_retries" in config:
                self.settings.max_retries = int(config["max_retries"])
        
        # Initialize the Instructor Async Client wrapping LiteLLM for structured extraction.
        # For raw chat, we'll use LiteLLM directly.
        # mode=instructor.Mode.MD_JSON is the most robust mode for mixed providers
        # (it forces the LLM to write Markdown JSON blocks which are then parsed).
        try:
            # Try to use MD_JSON mode (most robust)
            mode = instructor.Mode.MD_JSON
        except AttributeError:
            # Fallback to JSON mode if MD_JSON is not available
            try:
                mode = instructor.Mode.JSON
            except AttributeError:
                # If Mode enum doesn't exist, use string (older instructor versions)
                mode = "md_json"
        
        # Instructor client for structured extraction (extract method)
        self.instructor_client = instructor.from_litellm(acompletion, mode=mode)
        
        # Store LiteLLM completion function for raw chat (chat method)
        self.litellm_completion = acompletion
        
        # Shared Retry Configuration
        # This defines 'Resilience': simple network blips shouldn't crash the user's request.
        self.retry_policy = dict(
            stop=stop_after_attempt(self.settings.max_retries),
            # Wait 1s, then 2s, then 4s...
            wait=wait_exponential(multiplier=1, min=1, max=10),
            # Only retry strictly transient errors. Never retry a 400 Bad Request.
            retry=retry_if_exception_type((
                litellm_exceptions.RateLimitError,      # HTTP 429
                litellm_exceptions.ServiceUnavailableError, # HTTP 503
                litellm_exceptions.APIConnectionError,
                litellm_exceptions.Timeout
            )),
            # Log a warning every time we have to retry
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True
        )

    def _prepare_messages(self, prompt_or_msgs: Union[str, List[Dict[str, str]]]) -> List[Dict[str, str]]:
        """Helper to normalize string prompts into OpenAI message format."""
        if isinstance(prompt_or_msgs, str):
            return [{"role": "user", "content": prompt_or_msgs}]
        return prompt_or_msgs

    # -------------------------------------------------------------------------
    # METHOD 1: STRUCTURED EXTRACTION (The Workhorse)
    # -------------------------------------------------------------------------
    
    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((litellm_exceptions.RateLimitError, litellm_exceptions.ServiceUnavailableError)),
        reraise=True
    )
    async def extract(
        self, 
        prompt: Union[str, List[Dict[str, str]]], 
        schema: Type[T], 
        model: Optional[str] = None,
        **kwargs
    ) -> T:
        """
        Generates a validated Pydantic object from the LLM.
        
        This is your primary tool for data extraction, classification, and decision making.
        It uses `Instructor` to ensure the output matches the `schema` exactly.

        Args:
            prompt: The user input string or full message history list.
            schema: The Pydantic class defining the structure you want back.
            model: Override default model (e.g., 'claude-3-haiku-20240307').
            **kwargs: Extra parameters (temperature, max_tokens, etc).

        Returns:
            An instance of the class passed in `schema`.

        Raises:
            LLMServiceError: If the provider fails after all retries or validation fails.

        Example:
            ```python
            class UserInfo(BaseModel):
                name: str
                age: int
                
            # Returns a UserInfo object, NOT a dict
            user = await service.extract(
                prompt="John Doe is 30 years old",
                schema=UserInfo
            )
            print(user.name) # "John Doe"
            ```
        """
        model_name = model or self.settings.default_chat_model
        messages = self._prepare_messages(prompt)
        start_time = time.time()
        
        try:
            # The Magic: Instructor handles the parsing and validation loop
            # Extract temperature from kwargs to avoid duplicate argument
            temperature = kwargs.pop("temperature", self.settings.default_temperature)
            # Extract max_retries if provided, otherwise use default
            max_retries = kwargs.pop("max_retries", 3)
            
            response = await self.instructor_client.chat.completions.create(
                model=model_name,
                response_model=schema,
                messages=messages,
                temperature=temperature,
                # If validation fails, Instructor will re-prompt the LLM up to 3 times
                max_retries=max_retries, 
                **kwargs
            )
            
            # Observability Log
            duration = time.time() - start_time
            logger.info(
                "EXTRACT_SUCCESS", 
                extra={
                    "model": model_name, 
                    "schema": schema.__name__, 
                    "latency_sec": round(duration, 3)
                }
            )
            return response

        except Exception as e:
            # We catch everything here to wrap it in our domain exception
            # This ensures your API layer doesn't need to know about 'litellm' or 'instructor' errors
            logger.error(f"EXTRACT_FAILED: {str(e)}", extra={"model": model_name})
            raise LLMServiceError(f"Extraction failed: {str(e)}") from e

    # -------------------------------------------------------------------------
    # METHOD 2: RAW CHAT (Simple Text)
    # -------------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((litellm_exceptions.RateLimitError, litellm_exceptions.ServiceUnavailableError)),
        reraise=True
    )
    async def chat(
        self, 
        prompt: Union[str, List[Dict[str, str]]], 
        model: Optional[str] = None,
        **kwargs
    ) -> str:
        """
        Generates a raw string response. Use this for simple chatbots or summaries.

        Args:
            prompt: User string or message history.
            model: Override default model.

        Returns:
            str: The raw content of the AI response.

        Example:
            ```python
            # Simple call
            reply = await service.chat("Tell me a joke")
            
            # Model swap
            reply = await service.chat("Tell me a joke", model="claude-3-opus-20240229")
            ```
        """
        model_name = model or self.settings.default_chat_model
        messages = self._prepare_messages(prompt)
        start_time = time.time()

        try:
            # Use LiteLLM directly for raw chat (no structured extraction needed)
            # Extract temperature from kwargs to avoid duplicate argument
            temperature = kwargs.pop("temperature", self.settings.default_temperature)
            
            # Call LiteLLM directly (not through Instructor) for raw text generation
            response = await self.litellm_completion(
                model=model_name,
                messages=messages,
                temperature=temperature,
                **kwargs
            )
            
            # Extract content from LiteLLM response
            # LiteLLM returns a standard completion object
            if hasattr(response, 'choices') and len(response.choices) > 0:
                content = response.choices[0].message.content
            elif isinstance(response, dict):
                # Handle dict response format
                content = response.get('choices', [{}])[0].get('message', {}).get('content', '')
            else:
                # Fallback: try to get content directly
                content = str(response)
            
            duration = time.time() - start_time
            logger.info("CHAT_SUCCESS", extra={"model": model_name, "latency_sec": round(duration, 3)})
            return content

        except Exception as e:
            logger.error(f"CHAT_FAILED: {str(e)}", extra={"model": model_name})
            raise LLMServiceError(f"Chat completion failed: {str(e)}") from e

    # -------------------------------------------------------------------------
    # METHOD 3: EMBEDDINGS (VoyageAI / Vectors)
    # -------------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((litellm_exceptions.RateLimitError, litellm_exceptions.ServiceUnavailableError)),
        reraise=True
    )
    async def embed(
        self, 
        text: Union[str, List[str]], 
        model: Optional[str] = None
    ) -> List[List[float]]:
        """
        Generates vector embeddings for a string or list of strings.
        
        Optimized for RAG (Retrieval Augmented Generation).
        Works seamlessly with VoyageAI (SOTA) or OpenAI.

        Args:
            text: A single string document or a list of documents.
            model: The embedding model (defaults to settings.default_embedding_model).
                   e.g., 'voyage/voyage-2', 'text-embedding-3-small'.

        Returns:
            List[List[float]]: A list of vectors. 
                               If input was a single string, returns a list containing one vector.

        Example:
            ```python
            # Batch embedding (Faster)
            docs = ["Apple", "Banana", "Cherry"]
            vectors = await service.embed(docs, model="voyage/voyage-2")
            
            # vectors is [[0.1, ...], [0.2, ...], [0.3, ...]]
            ```
        """
        model_name = model or self.settings.default_embedding_model
        start_time = time.time()
        
        try:
            # LiteLLM handles the routing based on the model prefix
            response = await aembedding(
                model=model_name,
                input=text
            )
            
            # Normalize output: always return list of vectors
            # response.data is a list of objects with an 'embedding' attribute
            vectors = [item['embedding'] for item in response.data]
            
            duration = time.time() - start_time
            item_count = 1 if isinstance(text, str) else len(text)
            
            logger.info(
                "EMBED_SUCCESS", 
                extra={
                    "model": model_name, 
                    "count": item_count, 
                    "latency_sec": round(duration, 3)
                }
            )
            return vectors

        except Exception as e:
            logger.error(f"EMBED_FAILED: {str(e)}", extra={"model": model_name})
            raise LLMServiceError(f"Embedding failed: {str(e)}") from e


# --- 4. DEPENDENCY INJECTION HELPERS ---

@lru_cache()
def get_settings() -> LLMSettings:
    """Cached settings to prevent re-reading env vars on every request."""
    if not LLM_DEPENDENCIES_AVAILABLE:
        raise LLMServiceError(
            "LLM dependencies not available. Install with: "
            "pip install litellm instructor pydantic pydantic-settings tenacity"
        )
    return LLMSettings()

@lru_cache()
def get_llm_service(config: Optional[Dict[str, Any]] = None) -> LLMService:
    """
    FastAPI Dependency.
    Use this in your route signatures:
    
    `def my_route(service: LLMService = Depends(get_llm_service)): ...`
    
    Args:
        config: Optional LLM configuration dict (from manifest.json llm_config)
    """
    if not LLM_DEPENDENCIES_AVAILABLE:
        raise LLMServiceError(
            "LLM dependencies not available. Install with: "
            "pip install litellm instructor pydantic pydantic-settings tenacity"
        )
    settings = get_settings()
    return LLMService(settings, config=config)

