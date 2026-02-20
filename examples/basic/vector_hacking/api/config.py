"""
Configuration Module
====================

Centralizes all configuration for the Vector Hacking API.

Best Practice: Keep configuration in one place for easy management
and clear visibility into what can be customized.
"""

import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class AppConfig:
    """
    Application configuration.
    
    Using a dataclass makes configuration explicit and type-safe.
    The frozen=True ensures immutability after creation.
    """
    
    # App identity (used by mdb_engine for data isolation)
    app_slug: str = "vector_hacking_api"
    
    # MongoDB connection
    mongo_uri: str = "mongodb://admin:password@mongodb:27017/?authSource=admin&directConnection=true"
    db_name: str = "vector_hacking_db"
    
    # LLM configuration
    deployment_name: str = "gpt-4o"
    embedding_model: str = "text-embedding-3-small"
    temperature: float = 0.8
    
    # Attack parameters
    default_target: str = "Be mindful of your thoughts"
    match_threshold: float = 0.6
    text_similarity_threshold: float = 0.85
    cost_limit: float = 100.0
    
    @classmethod
    def from_env(cls) -> "AppConfig":
        """
        Load configuration from environment variables.
        
        Best Practice: Allow configuration via environment variables
        for flexibility across different deployment environments.
        """
        from mdb_engine.env import get_mongo_uri, get_db_name

        return cls(
            app_slug=os.getenv("APP_SLUG", cls.app_slug),
            mongo_uri=get_mongo_uri(fallback=cls.mongo_uri),
            db_name=get_db_name(fallback=cls.db_name),
            deployment_name=os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", cls.deployment_name),
            embedding_model=os.getenv("EMBEDDING_MODEL", cls.embedding_model),
            temperature=float(os.getenv("LLM_TEMPERATURE", cls.temperature)),
            default_target=os.getenv("DEFAULT_TARGET", cls.default_target),
            match_threshold=float(os.getenv("MATCH_THRESHOLD", cls.match_threshold)),
            cost_limit=float(os.getenv("COST_LIMIT", cls.cost_limit)),
        )


def get_llm_client():
    """
    Create the appropriate LLM client based on environment.
    
    Supports both Azure OpenAI and standard OpenAI APIs.
    """
    azure_key = os.getenv("AZURE_OPENAI_API_KEY")
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    
    if azure_key and azure_endpoint:
        from openai import AzureOpenAI
        return AzureOpenAI(
            api_key=azure_key,
            azure_endpoint=azure_endpoint,
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01"),
        )
    else:
        from openai import OpenAI
        return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


# Global config instance - load once at startup
config = AppConfig.from_env()

