#!/usr/bin/env python3
"""
SSO App 3 - AI Chat with True Perfect Recall & the Full Power of mdb-engine Memory

This is the definitive example of what mdb-engine's memory system can do.
It showcases every layer of the cognitive architecture:

Memory Features Demonstrated:
- True Perfect Recall: Every memory is always searchable, forever. No decay, no forgetting.
- Emotion-Weighted Recall: Emotionally charged memories rank higher (amygdala effect)
- Temporal Recency Bias: Recent memories get a configurable boost
- Spreading Activation: Graph-connected memories discovered via associative recall
- Salience-Gated Encoding: Low-value messages skip expensive LLM extraction
- Prospective Memory: "Remember to do X when Y happens" -- intention-based triggers
- GraphRAG: Knowledge graph with multi-hop reasoning and community detection
- Shared Memory: Privacy-safe group memory with anonymized promotion
- Memory Vetoes: User-controlled "never share" flags
- Reflective Memory: Meta-cognitive insights about behavior patterns
- Predictive Memory: Counterfactuals and validated predictions
- Memory Versioning: Track how beliefs evolve over time
- Timeline Branching: Multiverse support for counterfactual reasoning
- Memory Consolidation: Episodic-to-semantic knowledge distillation
- Context Engineering: Dynamic persona adaptation from memory layers

Infrastructure:
- CognitiveEngine with Gemini: Full RAG pipeline via LLMProvider abstraction
- Document Processing: Advanced file processing with atomic fact extraction
- SSO Authentication: Shared authentication across multi-app deployments
- CSFLE Encryption: Client-Side Field Level Encryption for memory content
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from uuid import uuid4

if TYPE_CHECKING:
    from mdb_engine.memory.base import BaseMemoryService

from bson.objectid import ObjectId
from dotenv import load_dotenv
from pymongo.errors import PyMongoError
from fastapi import Depends, File, Form, HTTPException, Request, UploadFile
from starlette.requests import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from mdb_engine.llm import LLMService, get_llm_service
from mdb_engine.dependencies import get_scoped_db, get_memory_service, get_profile_service
from mdb_engine.graph.service import get_graph_service as get_graph_service_factory
from pydantic import BaseModel, Field

# Optional imports
try:
    import pandas as pd
except ImportError:
    pd = None
try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None
try:
    import docx
except ImportError:
    docx = None

from mdb_engine import MongoDBEngine
from mdb_engine.core import build_csfle_config_from_manifest
from mdb_engine.embeddings.service import EmbeddingServiceError, get_embedding_service
from mdb_engine.routing.websockets import broadcast_to_app
from mdb_engine.memory import (
    CognitiveEngine,
    SharedMemory,
    ReflectiveMemory,
    PredictiveMemory,
    QueryAwareRecall,
    MemoryVeto,
    MemoryVersioning,
    CognitiveMemory,
    TimelineService,
    ProspectiveMemory,
)
from mdb_engine.memory.consolidator import MemoryConsolidator
from mdb_engine.memory.hygiene import run_daily_hygiene
from mdb_engine.memory.reflection import ReflectionService

# Import shared security utilities


load_dotenv()
logger = logging.getLogger(__name__)

# Suppress RuntimeWarning from LiteLLM's async logging (coroutine not awaited)
# This is a known issue in LiteLLM where async_success_handler is not properly awaited
import warnings
warnings.filterwarnings("ignore", message="coroutine.*was never awaited", category=RuntimeWarning)

APP_SLUG = "ai-chat"
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Constants for Concurrency
MAX_CONCURRENT_CHUNKS = 5  # Increased slightly for better throughput
CHUNK_SIZE = 15000  # Large context window usage
CHUNK_OVERLAP = 1000

# --- IMPROVED STRUCTURED DATA MODELS ---


class DocumentMetadata(BaseModel):
    """Rich metadata extracted from the document header/summary."""

    title: str = Field(description="The official title of the document.")
    author: str | None = Field(
        description=(
            "The specific person, department, or entity who wrote the document. "
            "Look for 'Prepared by', 'Author', or bylines."
        )
    )
    organization: str | None = Field(
        description="The company or organization the document belongs to."
    )
    version: str | None = Field(
        description="Version number (e.g., '1.0', 'Draft', 'Final') if available."
    )
    creation_date: str | None = Field(
        description="The specific date the document was created or last modified."
    )
    summary: str = Field(
        description="A comprehensive 3-sentence summary of the document's purpose."
    )
    main_entities: list[str] = Field(
        description=(
            "List of the primary projects, products, or people discussed "
            "(e.g., 'Project Apollo', 'iPhone 15')."
        )
    )


class AtomicFact(BaseModel):
    """A single, self-contained fact optimized for vector retrieval."""

    statement: str = Field(
        description=(
            "The fact statement. MUST be rewritten to be standalone. "
            "Resolve all pronouns to specific names provided in the context."
        )
    )
    category: str = Field(
        description=(
            "Category of the fact: 'Financial', 'Technical', 'Legal', "
            "'Schedule', 'Personnel', or 'General'."
        )
    )
    importance: int = Field(
        description="1-10 score. 10 = Critical decision/deadline/cost. 1 = Minor detail."
    )
    entities: list[str] = Field(
        description="List of specific named entities mentioned in this fact."
    )


class ChunkInsights(BaseModel):
    """Insights extracted from a specific segment of text."""

    facts: list[AtomicFact] = Field(description="List of atomic facts extracted from this segment.")


# --- HELPER FUNCTIONS ---


def semantic_chunking(
    text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP
) -> list[str]:
    """Splits text into large chunks for efficient LLM processing."""
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    text_len = len(text)

    while start < text_len:
        end = start + chunk_size
        if end < text_len:
            # Try to split at paragraph or newline
            last_newline = text.rfind("\n", start, end)
            if last_newline != -1 and last_newline > start + (chunk_size // 2):
                end = last_newline + 1
            else:
                last_space = text.rfind(" ", start, end)
                if last_space != -1:
                    end = last_space + 1

        chunks.append(text[start:end])
        start = end - overlap

    return chunks


# Raw Content Service
class RawContentService:
    """Service for storing and retrieving raw content in a separate MongoDB collection."""

    def __init__(self, engine: MongoDBEngine, app_slug: str, config: dict):
        self.engine = engine
        self.app_slug = app_slug
        self.collection_name = config.get("collection_name", f"{app_slug}_raw_content")
        self.enabled = config.get("enabled", True)

        if not self.enabled:
            logger.info(f"Raw content service disabled for {app_slug}")
            self.embedding_service = None
            return

        embedding_config = {
            "embedding_model": config.get("embedding_model", "text-embedding-3-small"),
            "embedding_model_dims": config.get("embedding_model_dims", 1536),
        }
        try:
            self.embedding_service = get_embedding_service(config=embedding_config)
            logger.info(f"✅ Raw Content Service initialized: {self.collection_name}")
        except (
            EmbeddingServiceError,
            ValueError,
            RuntimeError,
            ImportError,
            AttributeError,
        ) as e:
            logger.warning(f"⚠️ Failed to initialize Raw Content Service: {e}")
            self.embedding_service = None

    async def store_raw_content(
        self, raw_content: str, user_id: str, bucket_id: str, metadata: dict | None = None
    ) -> str | None:
        if not self.enabled or not self.embedding_service:
            return None
        try:
            db = await self.engine.get_scoped_db(self.app_slug)
            collection = getattr(db, self.collection_name)
            embeddings = await self.embedding_service.embed(raw_content)
            if not embeddings:
                return None

            doc = {
                "id": str(uuid4()),
                "user_id": str(user_id),
                "bucket_id": bucket_id,
                "associated_bucket_id": bucket_id,
                "content": raw_content,
                "embedding": embeddings[0],
                "metadata": metadata or {},
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }
            await collection.insert_one(doc)
            return doc["id"]
        except (PyMongoError, ValueError, KeyError) as e:
            logger.error(f"Failed to store raw content: {e}", exc_info=True)
            return None

    async def get_raw_content(self, bucket_id: str, user_id: str) -> str | None:
        if not self.enabled:
            return None
        try:
            db = await self.engine.get_scoped_db(self.app_slug)
            doc = await getattr(db, self.collection_name).find_one(
                {"bucket_id": bucket_id, "user_id": str(user_id)}, sort=[("created_at", -1)]
            )
            return doc.get("content") if doc else None
        except (PyMongoError, ValueError):  # noqa: BLE001
            return None


# Initialize global vars
raw_content_service: RawContentService | None = None
cognitive_engine: CognitiveEngine | None = None
llm_service: LLMService | None = None

# Perfect Brain components
shared_memory: Any | None = None
reflective_memory: Any | None = None
predictive_memory: Any | None = None
query_aware_recall: Any | None = None
memory_veto: Any | None = None
prospective_memory: Any | None = None
memory_versioning: Any | None = None
cognitive_memory: Any | None = None
timeline_service: TimelineService | None = None
memory_consolidator: Any | None = None
reflection_service: Any | None = None


# Load manifest and build CSFLE config for encrypted memory
_manifest_path = Path(__file__).parent / "manifest.json"
_manifest_data = json.load(open(_manifest_path)) if _manifest_path.exists() else {}
_csfle_config = build_csfle_config_from_manifest(_manifest_data)

if _csfle_config:
    logger.info(
        f"🔐 CSFLE enabled for memory encryption: "
        f"collections={list(_csfle_config.encrypted_collections.keys())}"
    )

# `app` and `engine` are injected by create_multi_app before this module runs.


async def on_startup(app_instance, engine, manifest):
    """Called automatically by create_multi_app after engine init and memory service setup."""
    global raw_content_service, cognitive_engine, llm_service
    global shared_memory, reflective_memory, predictive_memory
    global query_aware_recall, memory_veto, prospective_memory
    global memory_versioning, cognitive_memory, timeline_service
    global memory_consolidator, reflection_service

    raw_content_config = manifest.get("raw_content_config", {})
    if raw_content_config.get("enabled", False):
        raw_content_service = RawContentService(engine, APP_SLUG, raw_content_config)
    
    # Initialize LLM service (used for chat and document processing)
    llm_config = manifest.get("llm_config", {})
    llm_service = get_llm_service(config=llm_config)
    
    # Initialize CognitiveEngine for complete RAG pipeline
    memory_service = engine.get_memory_service(APP_SLUG)
    if memory_service:
        # Inject LLM service into memory service if not already injected
        # This is required for background memory extraction operations (e.g., _detect_memory_type)
        if hasattr(memory_service, '_injected_llm_service'):
            if memory_service._injected_llm_service is None:
                memory_service._injected_llm_service = llm_service
                memory_service.llm_available = True
                logger.info("✅ Injected LLM service into memory service for background extraction")
        else:
            # If the attribute doesn't exist, set it
            memory_service._injected_llm_service = llm_service
            memory_service.llm_available = True
            logger.info("✅ Injected LLM service into memory service (attribute created)")
        
        try:
            # Get async scoped collection for chat history
            # MUST be an async Motor collection (ScopedCollectionWrapper), NOT a
            # synchronous pymongo.Collection. Using motor_client.delegate would
            # return a sync collection whose create_index() returns a string,
            # causing "TypeError: object str can't be used in 'await' expression".
            scoped_db = await engine.get_scoped_db(APP_SLUG)
            chat_history_collection = scoped_db["chat_history"]
            
            # Sync PyMongo refs needed for Perfect Brain component initialization
            # (SharedMemory, MemoryConsolidator, etc. accept sync pymongo collections).
            # Route handlers use Motor async collections instead -- see below.
            motor_client = engine._connection_manager.mongo_client
            pymongo_client = motor_client.delegate
            pymongo_db = pymongo_client[engine.db_name]
            
            cognitive_engine = CognitiveEngine(
                app_slug=APP_SLUG,
                memory_service=memory_service,
                chat_history_collection=chat_history_collection,
                stm_context_limit=10,
                ltm_search_limit=12,  # Match current limit
                auto_summarize_threshold=20,
                llm_service=llm_service,
                # Context Engineering configuration
                enable_context_engineering=True,
                stm_raw_window=5,
                enable_entity_extraction=True,
                enable_dynamic_persona=True,
                # GraphRAG configuration - thresholds for when graph context is included
                # These control when CognitiveEngine includes graph context in responses
                graph_min_nodes=3,  # Minimum nodes required to include graph context (more meaningful threshold)
                graph_min_hop_distance=0,  # Minimum hop distance for graph_context nodes (0 = include entry nodes)
                graph_min_edges=0,  # Minimum edges required for graph_context nodes
            )
            logger.info("✅ Cognitive Engine Online: Complete RAG Pipeline with Context Engineering Ready")
            
            # Initialize Perfect Brain components
            try:
                # Get collections for Perfect Brain features
                entity_collection = pymongo_db["entity_memory"]
                shared_collection = pymongo_db.get_collection("entity_memory")  # Use same collection with scope="shared"
                reflective_collection = pymongo_db["reflective_memory"]
                predictive_collection = pymongo_db["predictive_memory"]
                veto_collection = pymongo_db["memory_vetoes"]
                timelines_collection = pymongo_db["timelines"]
                
                # Initialize TimelineService (Cognitive OS feature)
                timelines_wrapper = (await engine.get_scoped_db(APP_SLUG)).timelines
                timeline_service = TimelineService(timelines_wrapper)
                
                # Initialize SharedMemory
                shared_memory = SharedMemory(
                    semantic_collection=entity_collection,
                    shared_collection=shared_collection,
                )
                
                # Initialize ReflectiveMemory
                reflective_memory = ReflectiveMemory(collection=reflective_collection)
                
                # Initialize PredictiveMemory
                predictive_memory = PredictiveMemory(collection=predictive_collection)
                
                # Initialize QueryAwareRecall
                query_aware_recall = QueryAwareRecall()
                
                # Initialize MemoryVeto
                memory_veto = MemoryVeto(collection=veto_collection)
                
                # Initialize ProspectiveMemory (intention-based triggers)
                prospective_collection = pymongo_db["prospective_triggers"]
                prospective_memory = ProspectiveMemory(
                    collection=prospective_collection,
                    embedding_model=manifest.get("memory_config", {}).get("embedding_model", "text-embedding-3-small"),
                )
                
                # Initialize MemoryVersioning
                memory_versioning = MemoryVersioning(collection=entity_collection)
                
                # Get scoped collections for CognitiveMemory
                scoped_db = await engine.get_scoped_db(APP_SLUG)
                episodic_collection = pymongo_db["episodic"]
                procedural_collection = pymongo_db["procedural"]
                
                # Initialize CognitiveMemory (multi-tier memory system)
                # Requires embedding_service from engine
                app_embedding_service = engine.get_embedding_service(APP_SLUG)
                cognitive_memory = CognitiveMemory(
                    collection=scoped_db.entity_memory,  # Use scoped collection wrapper
                    model=manifest.get("llm_config", {}).get("providers", {}).get("chat", "openai/gpt-4o"),
                    embed_model=manifest.get("memory_config", {}).get("embedding_model", "text-embedding-3-small"),
                    embedding_service=app_embedding_service,
                )
                
                # Initialize MemoryConsolidator (episodic → semantic consolidation)
                memory_consolidator = MemoryConsolidator(
                    db_client=pymongo_client,
                    db_name=engine.db_name,
                    model=manifest.get("llm_config", {}).get("providers", {}).get("chat", "openai/gpt-4o"),
                    episodic_collection=episodic_collection,
                    entity_collection=entity_collection,
                    procedural_collection=procedural_collection,
                    memory_veto=memory_veto,  # Use existing veto instance
                )
                
                # Initialize ReflectionService (periodic memory consolidation)
                reflection_config = manifest.get("memory_config", {}).get("reflection", {})
                if reflection_config.get("enabled", True):
                    reflection_service = ReflectionService(
                        app_slug=APP_SLUG,
                        memories_collection=memory_service.collection,
                        config=reflection_config,
                        llm_service=llm_service,
                    )
                    logger.info("✅ ReflectionService initialized")
                
                logger.info("✅ Perfect Brain Features Initialized: SharedMemory, ReflectiveMemory, PredictiveMemory, QueryAwareRecall, MemoryVeto, ProspectiveMemory, MemoryVersioning, TimelineService, MemoryConsolidator, ReflectionService")
            except (ImportError, RuntimeError, OSError) as e:
                logger.error(f"⚠️ Failed to initialize Perfect Brain components: {e}", exc_info=True)
        except (ImportError, RuntimeError, OSError) as e:
            logger.error(f"❌ Failed to initialize CognitiveEngine: {e}", exc_info=True)
            cognitive_engine = None
    else:
        logger.warning("⚠️ Memory service not found - Cognitive Engine disabled")
    
    logger.info("AI Chat services initialized (lazy init on first request)")

# --- CORE LOGIC (AI PROCESSING) ---


# REMOVED: _fallback_rag_chat function - NO FALLBACKS policy
# Failures must be explicit so they can be addressed properly
# If CognitiveEngine is unavailable, the endpoint will raise HTTPException with clear error details


async def convert_file_to_markdown(file: UploadFile) -> dict:
    filename = file.filename.lower()
    content_bytes = await file.read()
    file_obj = io.BytesIO(content_bytes)
    result = {"filename": file.filename, "content": "", "raw_text": "", "type": "unknown"}

    try:
        if filename.endswith(".docx") and docx:
            result["type"] = "document"
            doc = docx.Document(file_obj)
            text = "\n".join([p.text for p in doc.paragraphs])
            result["raw_text"] = text
            result["content"] = f"### Document: {file.filename}\n\n{text}"
        elif filename.endswith(".pdf") and PdfReader:
            result["type"] = "pdf"
            reader = PdfReader(file_obj)
            text = "\n".join([p.extract_text() or "" for p in reader.pages])
            result["raw_text"] = text
            result["content"] = f"### PDF: {file.filename}\n{text}"
        elif filename.endswith((".xlsx", ".csv")) and pd:
            result["type"] = "spreadsheet"
            df = pd.read_csv(file_obj) if filename.endswith(".csv") else pd.read_excel(file_obj)
            result["raw_text"] = df.to_csv(index=False)
            result["content"] = (
                f"### Data: {file.filename}\n\n{df.head(50).to_markdown(index=False)}"
            )
        else:
            result["type"] = "code"
            text = content_bytes.decode("utf-8", errors="ignore")
            result["raw_text"] = text
            result["content"] = f"### File: {file.filename}\n```\n{text}\n```"
    except (ValueError, OSError, UnicodeDecodeError) as e:
        logger.exception(f"Error reading file {file.filename}")
        result["content"] = f"[Error reading {file.filename}: {e}]"
    return result


async def extract_global_metadata(
    text: str, filename: str
) -> DocumentMetadata:
    """Extracts high-level metadata from the beginning of the file."""
    intro_text = text[:20000]  # Increased window to catch metadata at end of intro sections

    prompt = f"""You are an expert document archivist. Analyze this text to extract METADATA.

    CRITICAL: Look for specific details.
    - Author: Look for "Prepared by", "Written by", bylines, or email signatures.
    - Version: Look for "v1.0", "Draft", "Final", "Confidential".
    - Organization: Look for company names in headers or footers.
    - Date: Look for the specific document creation date.

    Filename: {filename}
    Content Snippet:
    {intro_text}
    """

    try:
        if not llm_service:
            raise ValueError("LLM service not initialized")
        
        # Use LiteLLM for metadata extraction with Pydantic structured output
        # Pass DocumentMetadata directly - LiteLLM will handle schema conversion
        messages = [{"role": "user", "content": prompt}]
        response_text = await llm_service.chat_completion(
            messages=messages,
            provider_name="chat",
            temperature=1.0,
            response_format=DocumentMetadata,  # Pass Pydantic model directly
        )
        
        # Parse and validate using Pydantic
        # LiteLLM returns JSON string that matches the Pydantic schema
        return DocumentMetadata.model_validate_json(response_text)
    except (PyMongoError, ValueError):
        logger.exception("Metadata extraction failed")
        # Return safe defaults
        return DocumentMetadata(
            title=filename,
            author="Unknown",
            organization="Unknown",
            version=None,
            creation_date=None,
            summary="Processing failed.",
            main_entities=[],
        )


async def extract_facts_from_chunk(
    chunk: str,
    chunk_index: int,
    doc_metadata: DocumentMetadata,
    semaphore: asyncio.Semaphore,
) -> list[AtomicFact]:
    """Extracts detailed facts from a specific chunk, constrained by semaphore."""

    async with semaphore:
        # Long prompt string - break to avoid line length issues
        prompt = (
            f"""You are an expert analyst. Extract INDEPENDENT, ATOMIC facts """
            f"""from the text segment below.

        GLOBAL CONTEXT (Use this to resolve pronouns):
        - Document: "{doc_metadata.title}"
        - Author: {doc_metadata.author or 'Unknown'}
        - Organization: {doc_metadata.organization or 'Unknown'}
        - Key Entities: {', '.join(doc_metadata.main_entities)}

        INSTRUCTIONS:
        1. **Resolve Pronouns**: NEVER use "He", "She", "It", "They", """
            + f""""The author", "The company".
           - BAD: "He expects revenue to grow."
           - GOOD: "{doc_metadata.author or 'The author'} expects revenue to grow."
           - BAD: "It will cost $1M."
           - GOOD: (
               f"Project "
               f"{doc_metadata.main_entities[0] if doc_metadata.main_entities else 'X'} "
               "will cost $1M."
           )

        2. **Be Specific**: Include numbers, dates, and exact names.

        3. **Filter**: Only extract facts with importance score > 4. Ignore fluff.

        SEGMENT:
        {chunk}
        """
        )

        try:
            if not llm_service:
                logger.warning(f"LLM service not initialized for chunk {chunk_index}")
                return []
            
            # Use LiteLLM for fact extraction with Pydantic structured output
            # Pass ChunkInsights directly - LiteLLM will handle schema conversion
            messages = [{"role": "user", "content": prompt}]
            response_text = await llm_service.chat_completion(
                messages=messages,
                provider_name="chat",
                temperature=1.0,
                response_format=ChunkInsights,  # Pass Pydantic model directly
            )
            
            # Parse and validate using Pydantic
            # LiteLLM returns JSON string that matches the Pydantic schema
            insights = ChunkInsights.model_validate_json(response_text)
            
            # Return the facts from the validated model
            return insights.facts if insights.facts else []
        except (RuntimeError, ValueError, OSError) as e:  # noqa: BLE001
            logger.warning(f"Chunk {chunk_index} failed: {e}")
            return []


async def process_and_store_file_memory(
    svc, user_id: str, file_data: dict, category: str, associated_bucket_id: str = None
) -> int:
    """
    Orchestrates the parallel processing of a file with enhanced metadata injection.
    """
    filename = file_data["filename"]
    raw_text = file_data["raw_text"]

    # 1. Global Metadata Extraction (First Pass)
    await broadcast_to_app(
        APP_SLUG,
        {
            "type": "memory_progress",
            "stage": "analyzing_metadata",
            "message": f"Identifying author and context for {filename}...",
            "filename": filename,
            "user_id": user_id,
        },
        user_id=None,
    )

    doc_metadata = await extract_global_metadata(raw_text, filename)
    logger.info(
        f"📋 Metadata for {filename}: Author={doc_metadata.author}, Org={doc_metadata.organization}"
    )

    # Bucket IDs
    file_bucket_id = f"file:{filename}:{user_id}"
    cat_bucket_id = associated_bucket_id or (
        f"bucket:{category}:{user_id}" if category != "general" else f"bucket:general:{user_id}"
    )

    # 2. Store Raw Content (Vector DB) with Rich Metadata
    if raw_content_service:
        await raw_content_service.store_raw_content(
            raw_content=raw_text,
            user_id=user_id,
            bucket_id=file_bucket_id,
            metadata={
                "filename": filename,
                "associated_bucket_id": cat_bucket_id,
                "category": category,
                "title": doc_metadata.title,
                "author": doc_metadata.author,
                "organization": doc_metadata.organization,
                "version": doc_metadata.version,
                "summary": doc_metadata.summary,
                "topics": doc_metadata.main_entities,
                "doc_date": doc_metadata.creation_date,
            },
        )

    # 3. Parallel Fact Extraction (Second Pass)
    chunks = semantic_chunking(raw_text)
    total_chunks = len(chunks)

    await broadcast_to_app(
        APP_SLUG,
        {
            "type": "memory_progress",
            "stage": "chunking",
            "message": f"📄 Split document into {total_chunks} segments...",
            "filename": filename,
            "user_id": user_id,
            "progress": 25,
        },
        user_id=None,
    )
    await asyncio.sleep(0.1)

    await broadcast_to_app(
        APP_SLUG,
        {
            "type": "memory_progress",
            "stage": "extracting_facts",
            "message": f"🔍 Extracting facts from {total_chunks} segments...",
            "filename": filename,
            "user_id": user_id,
            "progress": 30,
        },
        user_id=None,
    )

    # Create tasks for parallel execution with progress tracking
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_CHUNKS)
    completed_chunks = 0
    
    async def extract_with_progress(chunk, chunk_index, metadata, sem):
        """Extract facts from chunk and send progress update."""
        async with sem:
            result = await extract_facts_from_chunk(chunk, chunk_index, metadata, sem)
            nonlocal completed_chunks
            completed_chunks += 1
            
            # Send progress update every few chunks or at milestones
            if completed_chunks % max(1, total_chunks // 10) == 0 or completed_chunks == total_chunks:
                progress = 30 + int((completed_chunks / total_chunks) * 40)  # 30% to 70%
                await broadcast_to_app(
                    APP_SLUG,
                    {
                        "type": "memory_progress",
                        "stage": "extracting_facts",
                        "message": f"🔍 Processing segment {completed_chunks}/{total_chunks}...",
                        "filename": filename,
                        "user_id": user_id,
                        "progress": progress,
                        "fact_number": completed_chunks,
                        "total_facts": total_chunks,
                    },
                    user_id=None,
                )
            
            return result
    
    tasks = [
        extract_with_progress(chunk, i, doc_metadata, semaphore)
        for i, chunk in enumerate(chunks)
    ]

    # Run all chunks
    results = await asyncio.gather(*tasks)

    # Flatten results
    all_facts: list[AtomicFact] = [fact for sublist in results for fact in sublist]

    # Filter high value facts and deduplicate
    unique_facts = []
    seen_statements = set()

    for f in all_facts:
        # Strict deduplication
        if f.statement not in seen_statements and f.importance >= 5:
            seen_statements.add(f.statement)
            unique_facts.append(f)

    # CRITICAL: Explicitly add author information as a fact if available
    # This ensures author queries can be found via semantic search
    if doc_metadata.author and doc_metadata.author != "Unknown":
        author_fact = f"The authors of '{doc_metadata.title}' are {doc_metadata.author}."
        if author_fact not in seen_statements:
            unique_facts.append(
                AtomicFact(
                    statement=author_fact,
                    category="Personnel",
                    importance=8,  # High importance for authorship
                    entities=[doc_metadata.author, doc_metadata.title],
                )
            )
            seen_statements.add(author_fact)
            logger.info(f"📝 Added explicit author fact: {author_fact}")

    # 4. Filtering and deduplication
    await broadcast_to_app(
        APP_SLUG,
        {
            "type": "memory_progress",
            "stage": "filtering_facts",
            "message": f"🔎 Filtering {len(all_facts)} facts for quality...",
            "filename": filename,
            "user_id": user_id,
            "progress": 70,
        },
        user_id=None,
    )
    await asyncio.sleep(0.1)

    # 5. Batch Store in Mem0
    stored_count = 0
    if unique_facts:
        total_facts_to_store = len(unique_facts)
        await broadcast_to_app(
            APP_SLUG,
            {
                "type": "memory_progress",
                "stage": "storing_memories",
                "message": f"💾 Saving {total_facts_to_store} facts to memory...",
                "filename": filename,
                "user_id": user_id,
                "progress": 75,
                "fact_number": 0,
                "total_facts": total_facts_to_store,
            },
            user_id=None,
        )

        # Common metadata for all facts in this file
        # We attach the AUTHOR and ORGANIZATION to every single memory!
        common_metadata = {
            "filename": filename,
            "associated_bucket_id": cat_bucket_id,
            "category": category,
            "doc_title": doc_metadata.title,
            "doc_author": doc_metadata.author,
            "doc_org": doc_metadata.organization,
            "doc_version": doc_metadata.version,
            "doc_date": doc_metadata.creation_date,
            "extracted_fact": True,
        }

        # Batch insert simulation (as Mem0 add is singular usually)
        for fact_idx, fact in enumerate(unique_facts):
            try:
                # We append fact-specific tags to the common metadata
                fact_metadata = common_metadata.copy()
                fact_metadata.update(
                    {
                        "fact_category": fact.category,
                        "fact_importance": fact.importance,
                        "entities": fact.entities,
                    }
                )

                await svc.add(
                    messages=[{"role": "user", "content": fact.statement}],
                    user_id=user_id,
                    bucket_id=file_bucket_id,
                    bucket_type="file",
                    metadata=fact_metadata,
                    infer=False,  # CRITICAL: We already extracted the atomic fact.
                    # Don't summarize it.
                )
                stored_count += 1
                
                # Send progress update every few facts or at milestones
                if (fact_idx + 1) % max(1, total_facts_to_store // 10) == 0 or (fact_idx + 1) == total_facts_to_store:
                    progress = 75 + int(((fact_idx + 1) / total_facts_to_store) * 20)  # 75% to 95%
                    await broadcast_to_app(
                        APP_SLUG,
                        {
                            "type": "memory_progress",
                            "stage": "storing_memories",
                            "message": f"💾 Stored {fact_idx + 1}/{total_facts_to_store} facts...",
                            "filename": filename,
                            "user_id": user_id,
                            "progress": progress,
                            "fact_number": fact_idx + 1,
                            "total_facts": total_facts_to_store,
                        },
                        user_id=None,
                    )
            except (PyMongoError, ValueError):
                logger.exception("Fact storage error")

    # 6. Final Broadcast
    await broadcast_to_app(
        APP_SLUG,
        {
            "type": "memory_progress",
            "stage": "complete",
            "message": f"✅ Completed! Stored {stored_count} facts from {filename}",
            "filename": filename,
            "user_id": user_id,
            "progress": 100,
            "fact_number": stored_count,
            "total_facts": stored_count,
        },
        user_id=None,
    )

    await asyncio.sleep(0.2)  # Brief pause before final event
    
    await broadcast_to_app(
        APP_SLUG,
        {
            "type": "memory_stored",
            "memory_count": stored_count,
            "task_completed": False,
            "filename": filename,
            "message": (
                f"Analyzed {filename}: Found {stored_count} facts "
                f"(Author: {doc_metadata.author or 'Unknown'})"
            ),
            "user_id": user_id,
        },
        user_id=None,
    )

    return stored_count


# --- ROUTES ---


def get_current_user(request: Request):
    return getattr(request.state, "user", None)


def _configure_ticket_endpoint(app):
    """Sets up the auth ticket endpoint for WebSockets with SSO/shared auth support."""
    from fastapi import status
    from fastapi.responses import JSONResponse
    
    # Remove existing /auth/ticket route if it exists (from default registration)
    routes_to_keep = []
    removed_count = 0
    
    for route in app.router.routes:
        route_path = getattr(route, 'path', None)
        if route_path == "/auth/ticket":
            route_methods = getattr(route, 'methods', set())
            if isinstance(route_methods, set) and 'POST' in route_methods:
                logger.info(f"Removing default ticket endpoint: {type(route).__name__} at {route_path}")
                removed_count += 1
                continue
            elif hasattr(route, 'methods') and route.methods:
                if isinstance(route.methods, (set, list)) and 'POST' in route.methods:
                    logger.info(f"Removing default ticket endpoint: {type(route).__name__} at {route_path}")
                    removed_count += 1
                    continue
        
        routes_to_keep.append(route)
    
    app.router.routes = routes_to_keep
    
    if removed_count > 0:
        logger.info(f"✅ Removed {removed_count} default ticket endpoint(s)")
    
    async def sso_ticket_endpoint(request: Request):
        """Custom ticket endpoint that works with SSO/shared auth."""
        logger.info(f"[Ticket Endpoint] 🎫 SSO TICKET ENDPOINT CALLED - Path: {request.url.path}")
        
        # Get current user from request state (set by SSO middleware)
        user = get_current_user(request)
        
        if not user:
            logger.warning(f"[Ticket Endpoint] No user found - user not authenticated")
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Authentication required"},
            )
        
        logger.info(f"[Ticket Endpoint] User found: {user.get('_id')}")
        
        user_id = str(user.get("_id") or user.get("user_id"))
        user_email = user.get("email")
        
        if not user_id:
            logger.error(f"[Ticket Endpoint] Invalid user data: {user}")
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "Invalid user data"},
            )
        
        ticket = engine.websocket_ticket_store.create_ticket(
            user_id=user_id,
            user_email=user_email,
            app_slug=APP_SLUG,
        )
        
        logger.info(f"✅ Generated WebSocket ticket for user '{user_id}' (SSO/shared auth)")
        
        return JSONResponse({
            "ticket": ticket,
            "expires_in": engine.websocket_ticket_store.ticket_ttl,
        })
    
    app.add_api_route("/auth/ticket", sso_ticket_endpoint, methods=["POST"])
    logger.info("✅ Custom SSO ticket endpoint registered at /auth/ticket")


def get_auth_hub_url(request=None) -> str:
    if request:
        url = getattr(request.state, "auth_hub_url", None)
        if url:
            return url
    return os.getenv("AUTH_HUB_URL", "/auth-hub")


# /logout is auto-registered by the engine for shared-auth apps


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    if get_current_user(request):
        return RedirectResponse("/conversations")
    return RedirectResponse(f"{get_auth_hub_url(request)}/login")


# /auth/callback is auto-registered by the engine for shared-auth apps


@app.get("/conversations", response_class=HTMLResponse)
async def conversations_list(
    request: Request,
    db=Depends(get_scoped_db),
):
    """
    List all conversations for the current user.
    
    Best Practice: Uses dependency injection for database access.
    """

    user = get_current_user(request)
    if not user:
        return RedirectResponse("/")
    convos = (
        await db.conversations.find({"user_id": str(user["_id"])})
        .sort("updated_at", -1)
        .to_list(100)
    )
    return templates.TemplateResponse(
        request, "conversations.html", {"user": user, "conversations": convos}
    )


@app.get("/persona", response_class=HTMLResponse)
async def get_persona_page(request: Request):
    """Persona visualization page"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url=f"{get_auth_hub_url(request)}/login")
    
    return templates.TemplateResponse("persona.html", {"request": request, "user": user})


@app.get("/conversations/{cid}", response_class=HTMLResponse)
async def conversation_view(
    request: Request,
    cid: str,
    fork: Optional[str] = None,  # Original conversation ID
    fork_at: Optional[str] = None,  # Message ID to fork at
    db=Depends(get_scoped_db),
):
    """
    Get a specific conversation by ID.
    
    Best Practice: Uses dependency injection for database access.
    
    URL Parameters:
    - fork: Original conversation ID (for fork indicator)
    - fork_at: Message ID to fork at (for fork indicator)
    """

    user = get_current_user(request)
    if not user:
        return RedirectResponse("/")
    convo = await db.conversations.find_one({"_id": ObjectId(cid), "user_id": str(user["_id"])})
    if not convo:
        return RedirectResponse("/conversations")
    msgs = await db.messages.find({"conversation_id": cid}).sort("created_at", 1).to_list(1000)
    
    # Get last active context from conversation document (persisted from last message)
    last_active_context = convo.get("last_active_context", [])
    
    # Handle fork parameters
    fork_data = None
    if fork:
        fork_data = {
            "original_conversation_id": fork,
            "fork_at_message_id": fork_at,
        }
    
    return templates.TemplateResponse(
        request, "conversation.html", {
            "user": user,
            "conversation": convo,
            "messages": msgs,
            "last_active_context": last_active_context,
            "fork_data": fork_data,
        }
    )


@app.post("/api/conversations", response_class=JSONResponse)
async def create_convo(
    request: Request,
    db=Depends(get_scoped_db),
):
    """
    Create a new conversation.
    
    Best Practice: Uses dependency injection for database access.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    res = await db.conversations.insert_one(
        {
            "user_id": str(user["_id"]),
            "title": "New Chat",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
    )
    return JSONResponse({"success": True, "conversation": {"_id": str(res.inserted_id)}})


@app.post("/api/conversations/{cid}/messages", response_class=JSONResponse)
async def send_message(
    request: Request,
    cid: str,
    message: str = Form(""),
    category: str = Form("general"),
    files: list[UploadFile] = File(default=[]),
    db=Depends(get_scoped_db),
    svc=Depends(get_memory_service),
):
    """
    Send a message in a conversation using CognitiveEngine for complete RAG pipeline.
    
    Best Practice: Uses dependency injection for database and memory service.
    """

    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    user_id = str(user["_id"])

    # 1. Process Files for Chat Context
    file_context = ""
    processed_files = []
    file_list = files or []
    if not file_list:
        try:
            form = await request.form()
            file_list = [v for v in form.getlist("files") if isinstance(v, UploadFile)]
        except (ValueError, TypeError, KeyError) as exc:  # noqa: BLE001
            logger.debug("Could not parse form for file list: %s", exc)

    # Process files in parallel for better performance
    async def process_single_file(file: UploadFile) -> dict | None:
        """Process a single file and return its data."""
        if file.filename:
            try:
                data = await convert_file_to_markdown(file)
                if data["raw_text"]:
                    return data
            except (ValueError, OSError, UnicodeDecodeError) as e:
                logger.error(f"❌ Error processing file {file.filename}: {e}", exc_info=True)
        return None
    
    # Process all files in parallel
    if file_list:
        file_results = await asyncio.gather(*[
            process_single_file(f) for f in file_list
        ], return_exceptions=True)
        
        for result in file_results:
            if isinstance(result, Exception):
                logger.error(f"❌ File processing error: {result}", exc_info=True)
            elif result:
                processed_files.append(result)
                file_context += f"\n{result['content']}"

    # 2. Use CognitiveEngine for complete RAG pipeline (if available)
    full_input = message + file_context
    ai_text = ""
    retrieved_memories = []
    rag_context = []
    
    # Context Engineering metadata (will be populated if Context Engineering is used)
    persona_used = None
    entity_facts = {}
    dynamic_instructions = ""
    stm_summary = None
    prompt_template = None
    graph_context_result = None  # Store graph context for response
    
    if cognitive_engine and svc and message.strip():
        try:
            # Build bucket_id for bucket-aware memory isolation
            # This ensures memories in 'work' bucket won't appear when using 'personal' bucket
            memory_bucket_id = f"category:{category}:{user_id}"
            
            # Use CognitiveEngine for complete RAG pipeline with Context Engineering
            # PERFORMANCE OPTIMIZATION: Set extract_facts=False for immediate response
            # Memory extraction is moved to a background task below
            # Context Engineering automatically builds the system prompt from:
            # - PersonaEngine (role, description, traits)
            # - Entity facts (Name, OS, Language, Expertise)
            # - Dynamic persona instructions (based on user context)
            # - LTM + Graph context
            # - STM summary (if needed)
            # Document context instructions are included in persona description
            result = await cognitive_engine.chat(
                user_id=user_id,
                session_id=cid,
                user_query=full_input,
                system_prompt=None,  # Let Context Engineering build it automatically
                extract_facts=False,  # FAST: Skip extraction here, do it in background
                bucket_id=memory_bucket_id,  # Bucket-aware memory isolation
                bucket_type="category",  # Category-based bucket type
            )
            
            ai_text = result["response"]
            ltm_memories = result.get("ltm_memories", [])
            
            # Extract Context Engineering metadata
            persona_used = result.get("persona_used")
            entity_facts = result.get("entity_facts", {})
            dynamic_instructions = result.get("dynamic_instructions", "")
            stm_summary = result.get("stm_summary")
            
            # Build prompt template with placeholders showing the structure
            prompt_template_parts = []
            if persona_used:
                persona_section = "[PERSONA LAYER]\n{persona_role}\n{persona_description}\n\nTraits: {persona_traits}"
                prompt_template_parts.append(persona_section)
            if dynamic_instructions:
                prompt_template_parts.append("[META-INSTRUCTIONS]\n{dynamic_instructions}")
            if entity_facts:
                prompt_template_parts.append("[USER CONTEXT]\nKnown Facts: {entity_facts}")
            prompt_template_parts.append("[RELEVANT MEMORY]\n{ltm_context}")
            prompt_template_parts.append("[GRAPH CONTEXT]\n{graph_context}")
            if stm_summary:
                prompt_template_parts.append("[PREVIOUS CONTEXT]\n{stm_summary}")
            prompt_template_parts.append("[CHAT HISTORY]\n{chat_history}")
            prompt_template_parts.append("\nUse the Chat History to maintain conversation flow. Use the context above to provide accurate and relevant responses.")
            prompt_template = "\n\n".join(prompt_template_parts)
            
            # Log Context Engineering info
            if persona_used:
                logger.info(
                    f"🎭 [Context Engineering] Persona used: {persona_used.get('role', 'Unknown')} - "
                    f"{persona_used.get('description', '')[:80]}..."
                )
            if entity_facts:
                logger.info(
                    f"📋 [Context Engineering] Entity facts extracted: {list(entity_facts.keys())} - "
                    f"{entity_facts}"
                )
            if dynamic_instructions:
                logger.info(
                    f"⚙️ [Context Engineering] Dynamic instructions: {dynamic_instructions[:150]}..."
                )
            if stm_summary:
                logger.info(
                    f"📝 [Context Engineering] STM summary created: {stm_summary[:100]}..."
                )
            
            # Enrich memories with document metadata for display
            for m in ltm_memories:
                if isinstance(m, dict) and m.get("memory"):
                    memory_text = m.get("memory", "")
                    meta = m.get("metadata", {})
                    
                    # Add document metadata if available
                    doc_info_parts = []
                    if meta.get("doc_author") and meta.get("doc_author") != "Unknown":
                        doc_info_parts.append(f"Author: {meta['doc_author']}")
                    if meta.get("doc_title"):
                        doc_info_parts.append(f"Document: {meta['doc_title']}")
                    if meta.get("doc_org") and meta.get("doc_org") != "Unknown":
                        doc_info_parts.append(f"Organization: {meta['doc_org']}")
                    
                    enriched_memory = memory_text
                    if doc_info_parts:
                        enriched_memory = (
                            f"[Document Context: {', '.join(doc_info_parts)}]\n{memory_text}"
                        )
                    rag_context.append(enriched_memory)
            
            retrieved_memories = ltm_memories
            
            # Store GraphRAG context for response (CognitiveEngine returns standardized format)
            graph_context_result = result.get("graph_context")
            if graph_context_result:
                entry_nodes = graph_context_result.get("entry_nodes", [])
                context_nodes = graph_context_result.get("context_nodes", [])
                community_summaries = graph_context_result.get("community_summaries", [])
                query_type = graph_context_result.get("query_type", "unknown")
                total_graph_nodes = len(entry_nodes) + len(context_nodes)
                logger.info(
                    f"🕸️ [GraphRAG] Graph context retrieved (query_type: {query_type}): "
                    f"{len(entry_nodes)} entry nodes, {len(context_nodes)} context nodes, "
                    f"{len(community_summaries)} community summaries"
                )
            else:
                logger.debug("🕸️ [GraphRAG] No graph context found for this query")
            
            logger.info(
                f"✅ CognitiveEngine processed message (fast mode): {len(ltm_memories)} memories retrieved"
            )
            
            # Sync messages to messages collection for UI compatibility
            user_msg_doc = {
                "conversation_id": cid,
                "user_id": user_id,
                "role": "user",
                "content": full_input,
                "created_at": datetime.utcnow(),
            }
            ai_msg_doc = {
                "conversation_id": cid,
                "user_id": user_id,
                "role": "assistant",
                "content": ai_text,
                "created_at": datetime.utcnow(),
            }
            await db.messages.insert_many([user_msg_doc, ai_msg_doc])
            
            # Record episodic memory and set working context (multi-tier memory system)
            if cognitive_memory:
                try:
                    # Record user episode
                    await cognitive_memory.record_episode(
                        session_id=cid,
                        role="user",
                        content=full_input,
                        scope="user",
                        user_id=user_id,
                        bucket_id=memory_bucket_id,
                    )
                    # Record assistant episode
                    await cognitive_memory.record_episode(
                        session_id=cid,
                        role="assistant",
                        content=ai_text,
                        scope="user",
                        user_id=user_id,
                        bucket_id=memory_bucket_id,
                    )
                    # Set working context for this session
                    await cognitive_memory.set_working_context(
                        session_id=cid,
                        data={
                            "current_topic": message[:100] if message else "",
                            "category": category,
                            "last_message": full_input[:200] if full_input else "",
                        },
                    )
                except (PyMongoError, ValueError, KeyError) as e:
                    logger.warning(f"⚠️ Failed to record episodic memory or set working context: {e}")
            
            # IMMEDIATE MEMORY EXTRACTION: Extract memories immediately from user prompt
            # This allows memories to be displayed while AI response is being generated
            # After response completes, memories will be refined with AI context
            immediate_memories_task = None
            if svc and message.strip():
                logger.info(
                    f"⚡ [Immediate Extraction] Starting immediate memory extraction: "
                    f"user_id={user_id}, bucket_id={memory_bucket_id}"
                )
                immediate_memories_task = asyncio.create_task(
                    _extract_memories_immediate(
                        user_id=user_id,
                        conversation_id=cid,
                        message=full_input,
                        memory_service=svc,
                        bucket_id=memory_bucket_id,
                        bucket_type="category",
                        category=category,
                    )
                )
                # Add error callback to log task failures without disrupting main flow
                immediate_memories_task.add_done_callback(
                    lambda t: logger.error(
                        f"❌ Immediate extraction task failed: {t.exception()}"
                    ) if t.exception() else None
                )
            
            # REFINE MEMORIES: After AI response completes, refine memories with context
            if immediate_memories_task and ai_text and ai_text.strip():
                try:
                    # Wait for immediate extraction to complete and get memory IDs
                    initial_memory_ids = await immediate_memories_task
                    
                    if initial_memory_ids:
                        logger.info(
                            f"🔧 [Refinement] Starting memory refinement: "
                            f"user_id={user_id}, memory_ids={len(initial_memory_ids)}"
                        )
                        refinement_task = asyncio.create_task(
                            _refine_memories_with_context(
                                user_id=user_id,
                                conversation_id=cid,
                                initial_memory_ids=initial_memory_ids,
                                ai_response=ai_text,
                                memory_service=svc,
                                bucket_id=memory_bucket_id,
                            )
                        )
                        refinement_task.add_done_callback(
                            lambda t: logger.error(
                                f"❌ Refinement task failed: {t.exception()}"
                            ) if t.exception() else None
                        )
                    else:
                        logger.info(
                            f"ℹ️ [Refinement] No memories to refine (user_id={user_id})"
                        )
                except (PyMongoError, ValueError, KeyError) as e:
                    logger.error(
                        f"❌ [Refinement] Failed to start refinement: {e}",
                        exc_info=True
                    )
                    # Don't fail the request if refinement fails
            
        except (PyMongoError, ValueError, KeyError) as e:
            logger.error(
                f"❌ CognitiveEngine chat FAILED: {e}. "
                f"NO FALLBACK - This failure needs to be addressed. "
                f"user_id={user_id}, conversation_id={cid}",
                exc_info=True
            )
            # NO FALLBACK: Failures are explicit - raise error so caller can handle it properly
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "CognitiveEngine unavailable or failed",
                    "message": "The AI service encountered an error. Please check logs and fix the root cause.",
                    "error_type": "cognitive_engine_failure",
                    "user_id": user_id,
                    "conversation_id": cid,
                }
            ) from e
    
    if not cognitive_engine:
        logger.error(
            f"❌ CognitiveEngine not available. NO FALLBACK - This needs to be fixed. "
            f"user_id={user_id}, conversation_id={cid}"
        )
        raise HTTPException(
            status_code=503,
            detail={
                "error": "CognitiveEngine not configured",
                "message": "The AI service is not available. Please configure CognitiveEngine properly.",
                "error_type": "service_unavailable",
                "user_id": user_id,
                "conversation_id": cid,
            }
        )

    # 3. Background Memory Task (for file processing - CognitiveEngine handles chat memory automatically)
    if svc and processed_files:

        async def store_task():
            total_memories = 0
            errors = []
            try:
                # Note: Chat memory is handled automatically by CognitiveEngine when extract_facts=True
                # Only process file memories here
                
                # File Memory - process in parallel for better performance
                async def process_single_file_memory(file_data: dict) -> tuple[int, str | None]:
                    """Process a single file memory and return (count, error)."""
                    try:
                        count = await process_and_store_file_memory(
                            svc=svc, user_id=user_id, file_data=file_data, category=category
                        )
                        return count, None
                    except (PyMongoError, ValueError, KeyError) as e:
                        error_msg = f"Error processing {file_data.get('filename')}: {e}"
                        logger.error(f"❌ {error_msg}", exc_info=True)
                        return 0, error_msg
                
                # Process all files in parallel
                if processed_files:
                    memory_results = await asyncio.gather(*[
                        process_single_file_memory(pf) for pf in processed_files
                    ], return_exceptions=True)
                    
                    for result in memory_results:
                        if isinstance(result, Exception):
                            errors.append(f"Unexpected error: {result}")
                        elif isinstance(result, tuple):
                            count, error = result
                            total_memories += count
                            if error:
                                errors.append(error)

                await broadcast_to_app(
                    APP_SLUG,
                    {
                        "type": "memory_stored",
                        "memory_count": total_memories,
                        "task_completed": True,
                        "filename": None,
                        "message": f"Finished processing. {total_memories} insights stored.",
                        "user_id": user_id,
                        "errors": errors if errors else None,
                    },
                    user_id=None,
                )

            except (PyMongoError, ValueError, KeyError) as e:
                logger.error(f"❌ Background task failed: {e}", exc_info=True)

        task = asyncio.create_task(store_task())
        task.add_done_callback(
            lambda t: t.exception() and logger.error(f"Task Error: {t.exception()}")
        )

    # Format memories for UI response (like chit_chat)
    context_memories = [
        {
            "id": m.get("id"),
            "memory": m.get("memory"),
            "score": m.get("score", m.get("similarity", 0.0)),
            "metadata": m.get("metadata", {})
        }
        for m in retrieved_memories
        if isinstance(m, dict) and m.get("memory")
    ]
    
    # Persist active context to database (survives page refresh across devices)
    update_fields = {"updated_at": datetime.utcnow()}
    if context_memories:
        update_fields["last_active_context"] = context_memories
    await db.conversations.update_one(
        {"_id": ObjectId(cid)}, {"$set": update_fields}
    )
    
    # Determine if extraction is happening in background
    extraction_pending = (cognitive_engine is not None and svc is not None and message.strip())
    
    # Build response with Context Engineering metadata
    response_data = {
        "success": True,
        "message": {
            "role": "assistant",
            "content": ai_text,
            "created_at": datetime.utcnow().isoformat() + "Z",
        },
        # Enterprise Grade: We explicitly tell the UI what we found (like chit_chat)
        "memory_context": {
            "query": message,
            "used_memories": len(context_memories),
            "memories": context_memories[:3], 
            "search_details": context_memories,
        },
        "memory_operations": {
            "search_performed": True,
            "memories_found": len(context_memories),
            "extraction_pending": extraction_pending,  # NEW: Indicates background extraction is running
            "extraction_performed": False,  # Will be true when WebSocket event arrives
            "vector_search_used": svc is not None,
            "cognitive_engine_used": cognitive_engine is not None
        },
    }
    
    # Add Context Engineering metadata if available
    if cognitive_engine and cognitive_engine.enable_context_engineering:
        context_engineering_metadata = {}
        
        if persona_used:
            context_engineering_metadata["persona"] = {
                "role": persona_used.get("role"),
                "description": persona_used.get("description", "")[:200],  # Truncate for response
                "traits": persona_used.get("traits", {})
            }
        if entity_facts:
            context_engineering_metadata["entity_facts"] = entity_facts
        if dynamic_instructions:
            context_engineering_metadata["dynamic_instructions"] = dynamic_instructions[:200]  # Truncate
        if stm_summary:
            context_engineering_metadata["stm_summary"] = stm_summary[:200]  # Truncate
        if prompt_template:
            context_engineering_metadata["prompt_template"] = prompt_template
        
        if context_engineering_metadata:
            response_data["context_engineering"] = context_engineering_metadata
    
    # Add GraphRAG context if available
    if cognitive_engine and cognitive_engine.has_graph_service and graph_context_result:
            entry_nodes = graph_context_result.get("entry_nodes", [])
            context_nodes = graph_context_result.get("context_nodes", [])
            community_summaries = graph_context_result.get("community_summaries", [])
            query_type = graph_context_result.get("query_type", "unknown")
            total_graph_nodes = len(entry_nodes) + len(context_nodes)
            
            if total_graph_nodes > 0:
                # Prepare graph data for frontend display
                graph_context_nodes_raw = graph_context_result.get("graph_context", [])
                if not graph_context_nodes_raw and context_nodes:
                    graph_context_nodes_raw = context_nodes
                
                # Normalize graph_context_nodes to a consistent format
                graph_context_nodes = []
                for item in graph_context_nodes_raw:
                    if isinstance(item, dict):
                        if "node" in item:
                            graph_context_nodes.append(item)
                        else:
                            graph_context_nodes.append({"node": item})
                    else:
                        continue
                
                # Collect relationship types
                all_relations = set()
                for node in entry_nodes:
                    for edge in node.get("edges", []):
                        if edge.get("active", True):
                            all_relations.add(edge.get("relation", ""))
                for item in graph_context_nodes:
                    node_data = item.get("node", {})
                    if isinstance(node_data, dict):
                        for edge in node_data.get("edges", []):
                            if edge.get("active", True):
                                all_relations.add(edge.get("relation", ""))
                
                response_data["graph_context"] = {
                    "has_graph": True,
                    "query_type": query_type,
                    "total_nodes": total_graph_nodes,
                    "entry_nodes_count": len(entry_nodes),
                    "context_nodes_count": len(graph_context_nodes),
                    "community_summaries_count": len(community_summaries),
                    "relationship_types": sorted(list(all_relations)),
                    "entry_nodes": [
                        {
                            "id": str(node.get("_id", "")),
                            "name": node.get("name", ""),
                            "type": node.get("type", ""),
                            "edges": [
                                {
                                    "relation": edge.get("relation", ""),
                                    "target": edge.get("target", ""),
                                    "active": edge.get("active", True),
                                }
                                for edge in node.get("edges", [])[:10]
                            ],
                        }
                        for node in entry_nodes
                    ],
                    "related_nodes": [
                        {
                            "id": str(item.get("node", {}).get("_id", "")),
                            "name": item.get("node", {}).get("name", ""),
                            "type": item.get("node", {}).get("type", ""),
                            "hop_distance": item.get("hop_distance", 0),
                            "edges": [
                                {
                                    "relation": edge.get("relation", ""),
                                    "target": edge.get("target", ""),
                                    "active": edge.get("active", True),
                                }
                                for edge in item.get("node", {}).get("edges", [])[:5]
                            ],
                        }
                        for item in graph_context_nodes
                    ],
                    "community_summaries": [
                        {
                            "community_id": str(s.get("community_id", "")),
                            "summary": s.get("summary", ""),
                            "level": s.get("level", 0),
                            "size": s.get("size", 0),
                        }
                        for s in community_summaries[:5]
                    ],
                }
    
    return JSONResponse(response_data)


# --- PREVIEW ENDPOINT FOR CONTEXT ENGINEERING MODAL ---
@app.post("/api/conversations/{cid}/preview-prompt", response_class=JSONResponse)
async def preview_prompt(
    request: Request,
    cid: str,
    query: str = Form(""),
    category: str = Form("general"),
    db=Depends(get_scoped_db),
    svc=Depends(get_memory_service),
):
    """
    Generate a preview of the context-engineered prompt that would be used for a query.
    Uses the actual CognitiveEngine to ensure accuracy.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    user_id = str(user["_id"])
    
    if not query.strip():
        raise HTTPException(status_code=400, detail="Query is required")
    
    if not cognitive_engine:
        raise HTTPException(status_code=500, detail="CognitiveEngine not available")
    
    # Build bucket_id for bucket-aware memory isolation
    memory_bucket_id = f"category:{category}:{user_id}"
    
    try:
        # Get persona
        persona_used = None
        persona_engine = getattr(svc, "persona_engine", None)
        if persona_engine:
            try:
                persona_used = await persona_engine.get_persona()
            except (PyMongoError, ValueError, KeyError) as e:
                logger.warning(f"⚠️ Failed to get persona for preview: {e}")
                persona_used = None
        
        # Search LTM with bucket filtering
        ltm_filters = {"metadata": {"associated_bucket_id": memory_bucket_id}}
        relevant_memories = []
        try:
            relevant_memories = await svc.search(
                query=query,
                user_id=user_id,
                limit=cognitive_engine.ltm_search_limit,
                filters=ltm_filters,
            )
        except (PyMongoError, ValueError, KeyError) as e:
            logger.warning(f"⚠️ LTM search failed for preview: {e}")
            relevant_memories = []
        
        # Extract entity facts using the actual method
        entity_facts = {}
        if cognitive_engine.enable_entity_extraction:
            entity_facts = cognitive_engine._extract_entity_facts(user_id, relevant_memories)
        
        # Build dynamic instructions using the actual method
        dynamic_instructions = ""
        if cognitive_engine.enable_dynamic_persona:
            dynamic_instructions = cognitive_engine._build_dynamic_persona(
                persona_used, entity_facts, relevant_memories
            )
        
        # Format LTM context (same format as actual system)
        ltm_context = ""
        if relevant_memories:
            ltm_context = "RELEVANT FACTS FROM LONG-TERM MEMORY:\n"
            for mem in relevant_memories:
                memory_text = mem.get("memory", "") or mem.get("text", "")
                if memory_text:
                    ltm_context += f"- {memory_text}\n"
            ltm_context += "\n"
        
        # Get graph context using CognitiveEngine's automatic GraphRAG
        # CognitiveEngine handles query classification and routing automatically
        graph_context = ""
        graph_results = None
        graph_meets_threshold = False
        graph_search_error = None
        
        # Use CognitiveEngine's internal graph fetching (same as chat() method)
        if cognitive_engine.has_graph_service:
            try:
                # CognitiveEngine automatically classifies queries and routes to appropriate search method
                query_type = cognitive_engine.graph_service.classify_query(query)
                logger.info(f"🔍 [GraphRAG Preview] Query classified as: {query_type}")
                
                # Route to appropriate GraphRAG search method
                if query_type == "local":
                    graph_results = await cognitive_engine.graph_service.local_search(
                        query=query,
                        user_id=user_id,
                        max_depth=2,
                    )
                elif query_type == "global":
                    graph_results = await cognitive_engine.graph_service.global_search(
                        query=query,
                        user_id=user_id,
                        max_communities=10,
                    )
                elif query_type == "drift":
                    graph_results = await cognitive_engine.graph_service.drift_search(
                        query=query,
                        user_id=user_id,
                        max_depth=2,
                    )
                else:
                    # Fallback to hybrid search
                    graph_results = await cognitive_engine.graph_service.hybrid_search(
                        query=query,
                        user_id=user_id,
                        max_depth=2,
                    )
                
                # Deduplicate graph against memories (same as CognitiveEngine does)
                if graph_results:
                    graph_results = await cognitive_engine._deduplicate_graph_against_memories(
                        graph_results,
                        relevant_memories,
                        similarity_threshold=cognitive_engine.graph_deduplication_threshold,
                    )
                    
                    # Format graph context if meets threshold
                    if graph_results:
                        entry_nodes = graph_results.get("entry_nodes", [])
                        # Handle both old format (graph_context) and new format (context_nodes)
                        graph_context_nodes_raw = graph_results.get("graph_context", [])
                        if not graph_context_nodes_raw and graph_results.get("context_nodes"):
                            graph_context_nodes_raw = graph_results.get("context_nodes", [])
                        
                        # Normalize graph_context_nodes to a consistent format (same as display code)
                        graph_context_nodes = []
                        for item in graph_context_nodes_raw:
                            if isinstance(item, dict):
                                # Check if it's already in the format {"node": {...}, "hop_distance": ..., etc}
                                if "node" in item:
                                    graph_context_nodes.append(item)
                                else:
                                    # It's a direct node dict, wrap it
                                    graph_context_nodes.append({"node": item})
                            else:
                                # Skip non-dict items
                                continue
                        
                        community_summaries = graph_results.get("community_summaries", [])
                        
                        # Count consistently with display code
                        total_graph_nodes = len(entry_nodes) + len(graph_context_nodes)
                        
                        if total_graph_nodes >= cognitive_engine.graph_min_nodes:
                            graph_meets_threshold = True
                            graph_context = cognitive_engine.graph_service.format_graph_context(
                                graph_results,
                                max_nodes=15,
                                include_edges=True,
                            )
                            if graph_context:
                                graph_context += "\n\n"
                                logger.info(
                                    f"✅ [GraphRAG Preview] Graph context included: "
                                    f"{len(entry_nodes)} entry nodes, {len(graph_context_nodes)} context nodes, "
                                    f"query_type: {query_type}"
                                )
            except (PyMongoError, ValueError, KeyError) as e:
                logger.warning(f"⚠️ Graph search failed for preview: {e}", exc_info=True)
                graph_results = None
                graph_search_error = str(e)
        
        # Get STM context (for chat history)
        stm_context = []
        try:
            stm_context = await asyncio.to_thread(
                cognitive_engine.stm.get_context,
                session_id=cid,
                limit=cognitive_engine.stm_context_limit,
                user_id=user_id,
            )
        except (PyMongoError, ValueError, KeyError) as e:
            logger.warning(f"⚠️ STM context fetch failed for preview: {e}")
            stm_context = []
        
        # Format chat history
        chat_history = ""
        if stm_context:
            chat_history = "\n".join([
                f"{msg.get('role', 'user').capitalize()}: {msg.get('content', '')}"
                for msg in stm_context[-10:]
            ])
        else:
            chat_history = "[No chat history yet - this will be populated after you send messages]"
        
        # Build the actual prompt using the same method (without STM summary for preview)
        system_prompt = cognitive_engine._construct_context_engineered_prompt(
            persona=persona_used,
            entity_facts=entity_facts,
            ltm_context=ltm_context,
            graph_context=graph_context,
            dynamic_instructions=dynamic_instructions,
            stm_summary=None,  # Don't summarize for preview
        )
        
        # Add chat history to the end (as it's added separately in the actual system)
        if chat_history and chat_history != "[No chat history yet - this will be populated after you send messages]":
            system_prompt += f"\n\n[CHAT HISTORY]\n{chat_history}"
        
        # Add user query at the end
        system_prompt += f"\n\n[USER QUERY]\n{query}"
        
        # Prepare graph data for frontend display (always show if graph service exists)
        graph_data = None
        if cognitive_engine.has_graph_service:
            if graph_results:
                entry_nodes = graph_results.get("entry_nodes", [])
                # Handle both old format (graph_context) and new format (context_nodes)
                graph_context_nodes_raw = graph_results.get("graph_context", [])
                if not graph_context_nodes_raw and graph_results.get("context_nodes"):
                    graph_context_nodes_raw = graph_results.get("context_nodes", [])
                
                # Normalize graph_context_nodes to a consistent format
                # Each item should be a dict with node data and metadata
                graph_context_nodes = []
                for item in graph_context_nodes_raw:
                    if isinstance(item, dict):
                        # Check if it's already in the format {"node": {...}, "hop_distance": ..., etc}
                        if "node" in item:
                            graph_context_nodes.append(item)
                        else:
                            # It's a direct node dict, wrap it
                            graph_context_nodes.append({"node": item})
                    else:
                        # Skip non-dict items
                        continue
                
                total_nodes = len(entry_nodes) + len(graph_context_nodes)
                strategy = graph_results.get("strategy", "neighborhood")  # Get strategy from advanced search
                
                # Collect all relationship types found for summary
                all_relations = set()
                for node in entry_nodes:
                    for edge in node.get("edges", []):
                        if edge.get("active", True):
                            all_relations.add(edge.get("relation", ""))
                for item in graph_context_nodes:
                    # Extract node data safely
                    node_data = item.get("node", {})
                    if isinstance(node_data, dict):
                        for edge in node_data.get("edges", []):
                            if edge.get("active", True):
                                all_relations.add(edge.get("relation", ""))
                
                # Ensure meets_threshold uses same node count as total_nodes display
                calculated_total = len(entry_nodes) + len(graph_context_nodes)
                meets_threshold_calculated = calculated_total >= cognitive_engine.graph_min_nodes
                
                graph_data = {
                    "has_graph": True,
                    "meets_threshold": meets_threshold_calculated,
                    "strategy": strategy,  # Include strategy for frontend display
                    "entry_nodes": [
                        {
                            "id": node.get("_id", ""),
                            "name": node.get("name", ""),
                            "type": node.get("type", ""),
                            "edges": [
                                {
                                    "relation": edge.get("relation", ""),
                                    "target": edge.get("target", ""),
                                    "active": edge.get("active", True),
                                }
                                for edge in node.get("edges", [])[:15]  # Increased limit
                            ],
                        }
                        for node in entry_nodes
                    ],
                    "related_nodes": [
                        {
                            "id": item.get("node", {}).get("_id", ""),
                            "name": item.get("node", {}).get("name", ""),
                            "type": item.get("node", {}).get("type", ""),
                            "hop_distance": item.get("hop_distance", 0),
                            "relation": item.get("relation", ""),  # Direct relation from entry node
                            "priority": item.get("priority", 0.5),  # Priority score
                            "edges": [
                                {
                                    "relation": edge.get("relation", ""),
                                    "target": edge.get("target", ""),
                                    "active": edge.get("active", True),
                                }
                                for edge in item.get("node", {}).get("edges", [])[:8]  # Increased limit
                            ],
                        }
                        for item in graph_context_nodes
                    ],
                    "total_nodes": total_nodes,
                    "relationship_types": sorted(list(all_relations)),  # All unique relationship types
                    "min_nodes_required": cognitive_engine.graph_min_nodes,
                    "formatted_context": graph_context,
                    "search_found_nodes": True,
                }
            else:
                # Graph service exists but search returned no results
                graph_data = {
                    "has_graph": True,
                    "meets_threshold": False,
                    "entry_nodes": [],
                    "related_nodes": [],
                    "total_nodes": 0,
                    "min_nodes_required": cognitive_engine.graph_min_nodes,
                    "formatted_context": "",
                    "search_found_nodes": False,
                    "message": "Graph relationships exist but vector search found no matches. This usually means nodes don't have embeddings yet. The system will try name-based fallback automatically. To fix permanently, use POST /api/graph/backfill-embeddings to generate embeddings for all nodes.",
                    "error": graph_search_error,
                }
        else:
            graph_data = {"has_graph": False}
        
        return {
            "success": True,
            "preview": system_prompt,
            "persona": persona_used,
            "entity_facts": entity_facts,
            "dynamic_instructions": dynamic_instructions,
            "ltm_memories": relevant_memories,
            "graph_context": graph_context,
            "graph_data": graph_data,
            "chat_history": chat_history,
        }
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Error generating preview: {e}", exc_info=True)
        error_detail = str(e)
        # Provide more helpful error messages
        if "CognitiveEngine" in error_detail or "not available" in error_detail:
            error_detail = "CognitiveEngine is not initialized. Please check server configuration."
        raise HTTPException(status_code=500, detail=error_detail)


# --- PREVIEW RESPONSE ENDPOINT ---
@app.post("/api/conversations/{cid}/preview-response", response_class=JSONResponse)
async def preview_response(
    request: Request,
    cid: str,
    message: str = Form(""),
    category: str = Form("general"),
    db=Depends(get_scoped_db),
    svc=Depends(get_memory_service),
):
    """
    Generate a preview response without saving to STM/LTM.
    Uses CognitiveEngine but skips persistence steps.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    user_id = str(user["_id"])
    
    if not message.strip():
        raise HTTPException(status_code=400, detail="Message is required")
    
    if not cognitive_engine:
        raise HTTPException(status_code=500, detail="CognitiveEngine not available")
    
    memory_bucket_id = f"category:{category}:{user_id}"
    
    try:
        # Get STM context WITHOUT adding the new message
        stm_context = await asyncio.to_thread(
            cognitive_engine.stm.get_context,
            session_id=cid,
            limit=cognitive_engine.stm_context_limit,
            user_id=user_id,
        )
        
        # Add user query to context temporarily (for preview only)
        preview_stm_context = stm_context + [{"role": "user", "content": message}]
        
        # Get LTM memories
        ltm_filters = {"metadata": {"associated_bucket_id": memory_bucket_id}}
        relevant_memories = []
        try:
            relevant_memories = await svc.search(
                query=message,
                user_id=user_id,
                limit=cognitive_engine.ltm_search_limit,
                filters=ltm_filters,
            )
        except (PyMongoError, ValueError, KeyError) as e:
            logger.warning(f"⚠️ LTM search failed for preview response: {e}")
            relevant_memories = []
        
        # Get persona and build context (same as regular flow)
        persona_used = None
        persona_engine = getattr(svc, "persona_engine", None)
        if persona_engine:
            try:
                persona_used = await persona_engine.get_persona()
            except (PyMongoError, ValueError, KeyError) as e:
                logger.warning(f"⚠️ Failed to get persona for preview response: {e}")
                persona_used = None
        
        entity_facts = {}
        if cognitive_engine.enable_entity_extraction:
            entity_facts = cognitive_engine._extract_entity_facts(user_id, relevant_memories)
        
        dynamic_instructions = ""
        if cognitive_engine.enable_dynamic_persona:
            dynamic_instructions = cognitive_engine._build_dynamic_persona(
                persona_used, entity_facts, relevant_memories
            )
        
        # Format LTM context
        ltm_context = ""
        if relevant_memories:
            ltm_context = "RELEVANT FACTS FROM LONG-TERM MEMORY:\n"
            for mem in relevant_memories:
                memory_text = mem.get("memory", "") or mem.get("text", "")
                if memory_text:
                    ltm_context += f"- {memory_text}\n"
            ltm_context += "\n"
        
        # Get graph context using CognitiveEngine's automatic GraphRAG
        graph_context = ""
        if cognitive_engine.has_graph_service:
            try:
                # CognitiveEngine automatically classifies queries and routes to appropriate search method
                query_type = cognitive_engine.graph_service.classify_query(message)
                
                # Route to appropriate GraphRAG search method
                if query_type == "local":
                    graph_results = await cognitive_engine.graph_service.local_search(
                        query=message,
                        user_id=user_id,
                        max_depth=2,
                    )
                elif query_type == "global":
                    graph_results = await cognitive_engine.graph_service.global_search(
                        query=message,
                        user_id=user_id,
                        max_communities=10,
                    )
                elif query_type == "drift":
                    graph_results = await cognitive_engine.graph_service.drift_search(
                        query=message,
                        user_id=user_id,
                        max_depth=2,
                    )
                else:
                    # Fallback to hybrid search
                    graph_results = await cognitive_engine.graph_service.hybrid_search(
                        query=message,
                        user_id=user_id,
                        max_depth=2,
                    )
                
                # Deduplicate graph against memories (same as CognitiveEngine does)
                if graph_results:
                    graph_results = await cognitive_engine._deduplicate_graph_against_memories(
                        graph_results, relevant_memories,
                        similarity_threshold=cognitive_engine.graph_deduplication_threshold,
                    )
                    if graph_results:
                        entry_nodes = graph_results.get("entry_nodes", [])
                        # Handle both old format (graph_context) and new format (context_nodes)
                        graph_context_nodes_raw = graph_results.get("graph_context", [])
                        if not graph_context_nodes_raw and graph_results.get("context_nodes"):
                            graph_context_nodes_raw = graph_results.get("context_nodes", [])
                        
                        # Normalize graph_context_nodes to a consistent format
                        graph_context_nodes = []
                        for item in graph_context_nodes_raw:
                            if isinstance(item, dict):
                                if "node" in item:
                                    graph_context_nodes.append(item)
                                else:
                                    graph_context_nodes.append({"node": item})
                            else:
                                continue
                        
                        # Count consistently
                        total_graph_nodes = len(entry_nodes) + len(graph_context_nodes)
                        if total_graph_nodes >= cognitive_engine.graph_min_nodes:
                            graph_context = cognitive_engine.graph_service.format_graph_context(
                                graph_results, max_nodes=10, include_edges=True,  # Increased from 8 for consistency
                            )
                            if graph_context:
                                graph_context += "\n\n"
            except (PyMongoError, ValueError, KeyError) as e:
                logger.warning(f"⚠️ Graph search failed for preview response: {e}")
        
        # Build system prompt
        system_prompt = cognitive_engine._construct_context_engineered_prompt(
            persona=persona_used,
            entity_facts=entity_facts,
            ltm_context=ltm_context,
            graph_context=graph_context,
            dynamic_instructions=dynamic_instructions,
            stm_summary=None,
        )
        
        # Prepare messages for LLM (without saving)
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(preview_stm_context)
        
        # Generate response (without saving)
        chat_model = None  # Use default
        ai_response = await cognitive_engine.llm_service.chat_completion(
            messages=messages, model=chat_model
        )
        
        return {
            "success": True,
            "response": ai_response,
            "preview_id": str(uuid4()),  # Unique ID for this preview
            "persona": persona_used,
            "entity_facts": entity_facts,
            "ltm_memories": relevant_memories,
        }
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Error generating preview response: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# --- FORK CONVERSATION ENDPOINT ---
@app.post("/api/conversations/{cid}/fork", response_class=JSONResponse)
async def fork_conversation(
    request: Request,
    cid: str,
    preview_id: str = Form(""),
    preview_response: str = Form(""),
    fork_at_message_id: str = Form(""),  # Optional: fork at specific message
    db=Depends(get_scoped_db),
    svc=Depends(get_memory_service),
):
    """
    Fork a conversation: create new conversation with messages up to a point,
    optionally including a previewed response.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    user_id = str(user["_id"])
    
    # Verify original conversation belongs to user
    original_convo = await db.conversations.find_one({
        "_id": ObjectId(cid),
        "user_id": user_id
    })
    if not original_convo:
        raise HTTPException(status_code=404, detail="Conversation not found")
    
    # Get messages up to fork point
    query = {"conversation_id": cid}
    if fork_at_message_id and fork_at_message_id.strip():
        # Get messages up to and including the fork point
        try:
            fork_msg = await db.messages.find_one({"_id": ObjectId(fork_at_message_id)})
            if fork_msg:
                query["created_at"] = {"$lte": fork_msg["created_at"]}
        except (PyMongoError, ValueError, KeyError) as e:
            logger.warning(f"⚠️ Failed to find fork message: {e}")
    
    messages = await db.messages.find(query).sort("created_at", 1).to_list(1000)
    
    # Create new conversation
    new_convo = await db.conversations.insert_one({
        "user_id": user_id,
        "title": f"{original_convo.get('title', 'New Chat')} (Fork)",
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    })
    new_cid = str(new_convo.inserted_id)
    
    # Copy messages to new conversation
    if messages:
        new_messages = []
        for msg in messages:
            new_messages.append({
                "conversation_id": new_cid,
                "user_id": user_id,
                "role": msg["role"],
                "content": msg["content"],
                "created_at": msg["created_at"],
            })
        await db.messages.insert_many(new_messages)
    
    # Add previewed response if provided
    if preview_response:
        await db.messages.insert_one({
            "conversation_id": new_cid,
            "user_id": user_id,
            "role": "assistant",
            "content": preview_response,
            "created_at": datetime.utcnow(),
        })
    
    # Copy STM context to new conversation
    if cognitive_engine:
        try:
            stm_context = await asyncio.to_thread(
                cognitive_engine.stm.get_context,
                session_id=cid,
                limit=100,
                user_id=user_id,
            )
            for msg in stm_context:
                cognitive_engine.stm.add_message(
                    session_id=new_cid,
                    role=msg["role"],
                    content=msg["content"],
                    user_id=user_id,
                )
            if preview_response:
                cognitive_engine.stm.add_message(
                    session_id=new_cid,
                    role="assistant",
                    content=preview_response,
                    user_id=user_id,
                )
        except (PyMongoError, ValueError, KeyError) as e:
            logger.warning(f"⚠️ Failed to copy STM context: {e}")
    
    return {
        "success": True,
        "conversation": {"_id": new_cid},
        "forked_from": cid,
    }


# --- STREAMING ENDPOINT FOR REAL-TIME AI RESPONSES ---
# This endpoint provides a superior UX by streaming AI responses token-by-token


@app.post("/api/conversations/{cid}/messages/stream")
async def send_message_stream(
    request: Request,
    cid: str,
    message: str = Form(""),
    category: str = Form("general"),
    reasoning_effort: str = Form("medium"),  # Gemini 3 reasoning: none, low, medium, high
    db=Depends(get_scoped_db),
    svc=Depends(get_memory_service),
):
    """
    Send a message with STREAMING AI response using Server-Sent Events (SSE).
    
    This endpoint provides real-time token-by-token streaming for a superior UX.
    Works especially well with Gemini 3 models that support reasoning_effort.
    
    Features:
    - Real-time streaming response (token by token)
    - Reasoning effort control for Gemini 3 models
    - Memory retrieval and background extraction
    - SSE format for easy frontend integration
    
    Args:
        cid: Conversation ID
        message: User message
        category: Memory category (general, work, personal, etc.)
        reasoning_effort: Reasoning level for Gemini 3 models:
            - "none" or "low": Fastest, minimal reasoning
            - "medium": Balanced (default)
            - "high": Maximum reasoning depth
    
    Returns:
        StreamingResponse with SSE format:
        - data: {"type": "chunk", "content": "..."} for response tokens
        - data: {"type": "context", "memories": [...]} for retrieved memories
        - data: {"type": "done", "full_response": "..."} on completion
    """

    from fastapi.responses import StreamingResponse
    import json as json_module
    
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    user_id = str(user["_id"])
    
    # Store user message
    await db.messages.insert_one({
        "conversation_id": cid,
        "user_id": user_id,
        "role": "user",
        "content": message,
        "created_at": datetime.utcnow(),
    })
    
    # Record episodic memory and set working context (multi-tier memory system)
    memory_bucket_id = f"category:{category}:{user_id}"
    if cognitive_memory:
        try:
            # Record user episode
            await cognitive_memory.record_episode(
                session_id=cid,
                role="user",
                content=message,
                scope="user",
                user_id=user_id,
                bucket_id=memory_bucket_id,
            )
            # Set working context for this session
            await cognitive_memory.set_working_context(
                session_id=cid,
                data={
                    "current_topic": message[:100] if message else "",
                    "category": category,
                    "last_message": message[:200] if message else "",
                },
            )
        except (PyMongoError, ValueError, KeyError) as e:
            logger.warning(f"⚠️ Failed to record episodic memory or set working context: {e}")
    
    async def generate_stream():
        """Async generator that yields SSE events."""
        full_response = ""
        retrieved_memories = []
        memory_bucket_id = f"category:{category}:{user_id}"
        persona_used_stream = None
        entity_facts_stream = {}
        dynamic_instructions_stream = ""
        prompt_template_stream = None
        rag_context = []  # Initialize before try block to avoid UnboundLocalError
        graph_context_result_stream = None  # Store graph context for streaming response
        immediate_memories_task = None  # Track immediate extraction task
        
        try:
            # 0. Extract memories IMMEDIATELY from user prompt (before AI response)
            if svc and message.strip():
                logger.info(
                    f"⚡ [Immediate Extraction] Starting immediate memory extraction: "
                    f"user_id={user_id}, bucket_id={memory_bucket_id}"
                )
                immediate_memories_task = asyncio.create_task(
                    _extract_memories_immediate(
                        user_id=user_id,
                        conversation_id=cid,
                        message=message,
                        memory_service=svc,
                        bucket_id=memory_bucket_id,
                        bucket_type="category",
                        category=category,
                    )
                )
                # Add error callback to log task failures without disrupting main flow
                immediate_memories_task.add_done_callback(
                    lambda t: logger.error(
                        f"❌ Immediate extraction task failed: {t.exception()}"
                    ) if t.exception() else None
                )
            # 1. Use CognitiveEngine with Context Engineering to build context
            # For streaming, we get context from CognitiveEngine then stream the LLM response separately
            if cognitive_engine and svc and message.strip():
                try:
                    # Get context from CognitiveEngine (this builds the context-engineered system prompt)
                    # Note: This will call the LLM once to get context, then we'll stream separately
                    # Future optimization: Extract context building without LLM call
                    result = await cognitive_engine.chat(
                        user_id=user_id,
                        session_id=cid,
                        user_query=message,
                        system_prompt=None,  # Let Context Engineering build it
                        extract_facts=False,  # Skip extraction for streaming
                        bucket_id=memory_bucket_id,
                        bucket_type="category",
                    )
                    
                    # Extract Context Engineering metadata
                    persona_used_stream = result.get("persona_used")
                    entity_facts_stream = result.get("entity_facts", {})
                    dynamic_instructions_stream = result.get("dynamic_instructions", "")
                    retrieved_memories = result.get("ltm_memories", [])
                    stm_context = result.get("stm_context", [])
                    graph_context_result = result.get("graph_context")
                    graph_context_result_stream = graph_context_result  # Store for done event
                    stm_summary_stream = result.get("stm_summary")
                    
                    # Build prompt template with placeholders
                    prompt_template_parts_stream = []
                    if persona_used_stream:
                        prompt_template_parts_stream.append("[PERSONA LAYER]\n{persona_role}\n{persona_description}\n\nTraits: {persona_traits}")
                    if dynamic_instructions_stream:
                        prompt_template_parts_stream.append("[META-INSTRUCTIONS]\n{dynamic_instructions}")
                    if entity_facts_stream:
                        prompt_template_parts_stream.append("[USER CONTEXT]\nKnown Facts: {entity_facts}")
                    prompt_template_parts_stream.append("[RELEVANT MEMORY]\n{ltm_context}")
                    prompt_template_parts_stream.append("[GRAPH CONTEXT]\n{graph_context}")
                    if stm_summary_stream:
                        prompt_template_parts_stream.append("[PREVIOUS CONTEXT]\n{stm_summary}")
                    prompt_template_parts_stream.append("[CHAT HISTORY]\n{chat_history}")
                    prompt_template_parts_stream.append("\nUse the Chat History to maintain conversation flow. Use the context above to provide accurate and relevant responses.")
                    prompt_template_stream = "\n\n".join(prompt_template_parts_stream)
                    
                    # Enrich memories with document metadata for display
                    rag_context = []
                    for m in retrieved_memories:
                        memory_text = m.get("memory", "")
                        if memory_text:
                            meta = m.get("metadata", {})
                            doc_info_parts = []
                            if meta.get("doc_author") and meta.get("doc_author") != "Unknown":
                                doc_info_parts.append(f"Author: {meta['doc_author']}")
                            if meta.get("doc_title"):
                                doc_info_parts.append(f"Document: {meta['doc_title']}")
                            if meta.get("doc_org") and meta.get("doc_org") != "Unknown":
                                doc_info_parts.append(f"Organization: {meta['doc_org']}")
                            
                            enriched_memory = memory_text
                            if doc_info_parts:
                                enriched_memory = (
                                    f"[Document Context: {', '.join(doc_info_parts)}]\n{memory_text}"
                                )
                            rag_context.append(enriched_memory)
                    
                    # Build context-engineered system prompt using Context Engineering patterns
                    system_prompt_parts = []
                    
                    # Persona layer
                    if persona_used_stream:
                        role = persona_used_stream.get("role", "")
                        description = persona_used_stream.get("description", "")
                        traits = persona_used_stream.get("traits", {})
                        if role:
                            persona_text = f"[PERSONA LAYER]\n{role}\n{description}"
                            if traits:
                                trait_list = [f"{k}: {v:.1f}" if isinstance(v, (int, float)) else f"{k}: {v}" 
                                            for k, v in traits.items()]
                                persona_text += f"\n\nTraits: {', '.join(trait_list)}"
                            system_prompt_parts.append(persona_text)
                    
                    # Dynamic instructions
                    if dynamic_instructions_stream:
                        system_prompt_parts.append(f"[META-INSTRUCTIONS]\n{dynamic_instructions_stream}")
                    
                    # Entity facts
                    if entity_facts_stream:
                        facts_list = [f"{k}: {v}" for k, v in entity_facts_stream.items()]
                        system_prompt_parts.append(f"[USER CONTEXT]\nKnown Facts: {', '.join(facts_list)}")
                    
                    # Memory context (LTM)
                    if rag_context:
                        memory_context_str = "\n".join([f"- {mem}" for mem in rag_context])
                        system_prompt_parts.append(f"[RELEVANT MEMORY]\n{memory_context_str}")
                    
                    # Graph context (if available)
                    if graph_context_result and isinstance(graph_context_result, dict):
                        graph_context_str = graph_context_result.get("graph_context", "")
                        if graph_context_str:
                            system_prompt_parts.append(f"[GRAPH CONTEXT]\n{graph_context_str}")
                    
                    # STM summary (if available)
                    if stm_summary_stream:
                        system_prompt_parts.append(f"[PREVIOUS CONTEXT]\n{stm_summary_stream}")
                    
                    # Build final system prompt
                    system_prompt = "\n\n".join(system_prompt_parts) if system_prompt_parts else "You are Orby, an AI assistant."
                    system_prompt += "\n\nUse the Chat History to maintain conversation flow. Use the context above to provide accurate and relevant responses."
                    
                    # Build messages with context-engineered system prompt
                    messages = [{"role": "system", "content": system_prompt}]
                    messages.extend(stm_context)  # Add STM context from CognitiveEngine
                    
                    logger.info(
                        f"🎯 [Streaming] Context Engineering enabled: "
                        f"persona={'yes' if persona_used_stream else 'no'}, "
                        f"entities={len(entity_facts_stream)}, "
                        f"memories={len(retrieved_memories)}"
                    )
                    
                except (RuntimeError, ValueError, OSError) as e:
                    logger.error(
                        f"❌ CognitiveEngine context building FAILED: {e}. "
                        f"NO FALLBACK - This failure needs to be addressed.",
                        exc_info=True
                    )
                    # NO FALLBACK: Failures are explicit - yield error event and stop
                    error_data = {
                        'type': 'error',
                        'error': 'CognitiveEngine failed',
                        'message': 'The AI service encountered an error. Please check logs and fix the root cause.',
                        'error_type': 'cognitive_engine_failure'
                    }
                    yield f"data: {json_module.dumps(error_data)}\n\n"
                    return
            
            if not cognitive_engine:
                logger.error(
                    f"❌ CognitiveEngine not available for streaming. "
                    f"NO FALLBACK - This needs to be fixed."
                )
                # NO FALLBACK: Failures are explicit - yield error event and stop
                error_data = {
                    'type': 'error',
                    'error': 'CognitiveEngine not configured',
                    'message': 'The AI service is not available. Please configure CognitiveEngine properly.',
                    'error_type': 'service_unavailable'
                }
                yield f"data: {json_module.dumps(error_data)}\n\n"
                return
            
            # Send context event with Context Engineering metadata
            context_event = {
                "type": "context",
                "memories": [
                    {
                        "id": m.get("id"),
                        "memory": m.get("memory"),
                        "score": m.get("score", m.get("similarity", 0.0)),
                    }
                    for m in retrieved_memories[:5]
                ],
                "total_found": len(retrieved_memories),
            }
            
            # Add Context Engineering metadata to context event
            if persona_used_stream or entity_facts_stream or dynamic_instructions_stream:
                context_event["context_engineering"] = {}
                if persona_used_stream:
                    context_event["context_engineering"]["persona"] = {
                        "role": persona_used_stream.get("role"),
                        "description": persona_used_stream.get("description", "")[:150],
                        "traits": persona_used_stream.get("traits", {})
                    }
                if entity_facts_stream:
                    context_event["context_engineering"]["entity_facts"] = entity_facts_stream
                if dynamic_instructions_stream:
                    context_event["context_engineering"]["dynamic_instructions"] = dynamic_instructions_stream[:150]
                if stm_summary_stream:
                    context_event["context_engineering"]["stm_summary"] = stm_summary_stream[:150]
                if prompt_template_stream:
                    context_event["context_engineering"]["prompt_template"] = prompt_template_stream
            
            yield f"data: {json_module.dumps(context_event)}\n\n"
            
            # 3. Stream response from LLM
            if llm_service:
                try:
                    # Use reasoning_effort for Gemini 3 models
                    # Valid values: "none", "low", "medium", "high"
                    effective_reasoning = reasoning_effort if reasoning_effort in ["none", "low", "medium", "high"] else "medium"
                    full_reasoning = ""  # Capture reasoning separately for audit
                    
                    async for chunk in llm_service.chat_completion_stream(
                        messages=messages,
                        provider_name="chat",
                        reasoning_effort=effective_reasoning,
                        stream_reasoning=True,  # Enable thinking bubbles
                    ):
                        # Handle reasoning markers - send as separate events for UI
                        if chunk == "__REASONING_START__":
                            # Signal frontend to start showing thinking bubble
                            yield f"data: {json_module.dumps({'type': 'reasoning_start'})}\n\n"
                        elif chunk == "__REASONING_END__":
                            # Signal frontend to close thinking bubble
                            yield f"data: {json_module.dumps({'type': 'reasoning_end'})}\n\n"
                        elif chunk.startswith("__REASONING__:"):
                            # Stream reasoning content for thinking bubble
                            reasoning_content = chunk[14:]  # Strip "__REASONING__:" prefix
                            full_reasoning += reasoning_content
                            yield f"data: {json_module.dumps({'type': 'reasoning', 'content': reasoning_content})}\n\n"
                        else:
                            # Regular content - add to response and stream
                            full_response += chunk
                            chunk_event = {
                                "type": "chunk",
                                "content": chunk,
                            }
                            yield f"data: {json_module.dumps(chunk_event)}\n\n"
                    
                    # Log reasoning for audit trail
                    if full_reasoning:
                        logger.info(f"AI reasoning captured ({len(full_reasoning)} chars)")
                        
                except (RuntimeError, ValueError, OSError) as e:
                    logger.error(f"Streaming LLM failed: {e}", exc_info=True)
                    
                    # Provide user-friendly error messages for common issues
                    error_message = str(e)
                    error_type = "error"
                    
                    # Check for model overloaded (503) errors
                    if "503" in error_message or "overloaded" in error_message.lower() or "ServiceUnavailableError" in str(type(e).__name__):
                        error_message = "The AI model is currently overloaded. Please try again in a few moments."
                        error_type = "retry"
                    # Check for rate limiting
                    elif "429" in error_message or "rate limit" in error_message.lower():
                        error_message = "Rate limit exceeded. Please wait a moment before trying again."
                        error_type = "retry"
                    # Check for authentication errors
                    elif "401" in error_message or "403" in error_message or "unauthorized" in error_message.lower():
                        error_message = "Authentication error. Please refresh the page and try again."
                        error_type = "auth_error"
                    # Check for timeout errors
                    elif "timeout" in error_message.lower() or "timed out" in error_message.lower():
                        error_message = "Request timed out. Please try again."
                        error_type = "retry"
                    # Check for mid-stream fallback errors (model switching)
                    elif "MidStreamFallbackError" in str(type(e).__name__):
                        error_message = "The AI model encountered an issue and switched to a fallback. Please try again."
                        error_type = "retry"
                    else:
                        # For other errors, provide a generic but helpful message
                        error_message = "An error occurred while generating the response. Please try again."
                    
                    error_event = {
                        "type": error_type,
                        "message": error_message,
                        "technical_details": str(e) if error_type != "error" else None  # Include technical details for debugging
                    }
                    yield f"data: {json_module.dumps(error_event)}\n\n"
                    full_response = error_message
            else:
                full_response = "LLM service not initialized"
                error_event = {"type": "error", "message": full_response}
                yield f"data: {json_module.dumps(error_event)}\n\n"
            
            # 4. Store assistant message
            await db.messages.insert_one({
                "conversation_id": cid,
                "user_id": user_id,
                "role": "assistant",
                "content": full_response,
                "created_at": datetime.utcnow(),
            })
            
            # Record assistant episode (multi-tier memory system)
            if cognitive_memory:
                try:
                    await cognitive_memory.record_episode(
                        session_id=cid,
                        role="assistant",
                        content=full_response,
                        scope="user",
                        user_id=user_id,
                        bucket_id=memory_bucket_id,
                    )
                except (PyMongoError, ValueError, KeyError) as e:
                    logger.warning(f"⚠️ Failed to record assistant episode: {e}")
            
            # 5. Prepare graph context for done event (if available)
            graph_context_for_response = None
            if graph_context_result_stream and cognitive_engine and cognitive_engine.has_graph_service:
                entry_nodes = graph_context_result_stream.get("entry_nodes", [])
                context_nodes = graph_context_result_stream.get("context_nodes", [])
                community_summaries = graph_context_result_stream.get("community_summaries", [])
                query_type = graph_context_result_stream.get("query_type", "unknown")
                total_graph_nodes = len(entry_nodes) + len(context_nodes)
                
                if total_graph_nodes > 0:
                    # Normalize graph_context_nodes
                    graph_context_nodes_raw = graph_context_result_stream.get("graph_context", [])
                    if not graph_context_nodes_raw and context_nodes:
                        graph_context_nodes_raw = context_nodes
                    
                    graph_context_nodes = []
                    for item in graph_context_nodes_raw:
                        if isinstance(item, dict):
                            if "node" in item:
                                graph_context_nodes.append(item)
                            else:
                                graph_context_nodes.append({"node": item})
                    
                    # Collect relationship types
                    all_relations = set()
                    for node in entry_nodes:
                        for edge in node.get("edges", []):
                            if edge.get("active", True):
                                all_relations.add(edge.get("relation", ""))
                    for item in graph_context_nodes:
                        node_data = item.get("node", {})
                        if isinstance(node_data, dict):
                            for edge in node_data.get("edges", []):
                                if edge.get("active", True):
                                    all_relations.add(edge.get("relation", ""))
                    
                    graph_context_for_response = {
                        "has_graph": True,
                        "query_type": query_type,
                        "total_nodes": total_graph_nodes,
                        "entry_nodes_count": len(entry_nodes),
                        "context_nodes_count": len(graph_context_nodes),
                        "community_summaries_count": len(community_summaries),
                        "relationship_types": sorted(list(all_relations)),
                        "entry_nodes": [
                            {
                                "id": str(node.get("_id", "")),
                                "name": node.get("name", ""),
                                "type": node.get("type", ""),
                                "edges": [
                                    {
                                        "relation": edge.get("relation", ""),
                                        "target": edge.get("target", ""),
                                        "active": edge.get("active", True),
                                    }
                                    for edge in node.get("edges", [])[:10]
                                ],
                            }
                            for node in entry_nodes
                        ],
                        "related_nodes": [
                            {
                                "id": str(item.get("node", {}).get("_id", "")),
                                "name": item.get("node", {}).get("name", ""),
                                "type": item.get("node", {}).get("type", ""),
                                "hop_distance": item.get("hop_distance", 0),
                                "edges": [
                                    {
                                        "relation": edge.get("relation", ""),
                                        "target": edge.get("target", ""),
                                        "active": edge.get("active", True),
                                    }
                                    for edge in item.get("node", {}).get("edges", [])[:5]
                                ],
                            }
                            for item in graph_context_nodes
                        ],
                        "community_summaries": [
                            {
                                "community_id": str(s.get("community_id", "")),
                                "summary": s.get("summary", ""),
                                "level": s.get("level", 0),
                                "size": s.get("size", 0),
                            }
                            for s in community_summaries[:5]
                        ],
                    }
            
            # 5. Send done event with full response
            done_event = {
                "type": "done",
                "full_response": full_response,
                "memories_used": len(retrieved_memories),
            }
            if graph_context_for_response:
                done_event["graph_context"] = graph_context_for_response
            yield f"data: {json_module.dumps(done_event)}\n\n"
            
            # 6. Refine memories with AI response context (if immediate extraction happened)
            if immediate_memories_task and full_response and full_response.strip():
                try:
                    # Wait for immediate extraction to complete and get memory IDs
                    initial_memory_ids = await immediate_memories_task
                    
                    if initial_memory_ids:
                        logger.info(
                            f"🔧 [Refinement] Starting memory refinement: "
                            f"user_id={user_id}, memory_ids={len(initial_memory_ids)}"
                        )
                        refinement_task = asyncio.create_task(
                            _refine_memories_with_context(
                                user_id=user_id,
                                conversation_id=cid,
                                initial_memory_ids=initial_memory_ids,
                                ai_response=full_response,
                                memory_service=svc,
                                bucket_id=memory_bucket_id,
                            )
                        )
                        refinement_task.add_done_callback(
                            lambda t: logger.error(f"Refinement failed: {t.exception()}")
                            if t.exception() else None
                        )
                    else:
                        logger.info(
                            f"ℹ️ [Refinement] No memories to refine (user_id={user_id})"
                        )
                except (PyMongoError, ValueError, KeyError) as e:
                    logger.error(
                        f"❌ [Refinement] Failed to start refinement: {e}",
                        exc_info=True
                    )
                    # Don't fail the request if refinement fails
                
        except (RuntimeError, ValueError, OSError) as e:
            logger.error(f"Streaming error: {e}", exc_info=True)
            error_event = {"type": "error", "message": str(e)}
            yield f"data: {json_module.dumps(error_event)}\n\n"
    
    return StreamingResponse(
        generate_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        }
    )


# Constants for memory broadcasting
VECTOR_INDEX_UPDATE_DELAY_SECONDS = 1.5  # Delay to allow MongoDB Atlas vector index to update
MAX_MEMORIES_TO_FETCH = 50  # Maximum number of memories to fetch for broadcast
MAX_NEW_MEMORIES_TO_DISPLAY = 5  # Maximum number of new memories to include in event


async def _send_extraction_status(
    user_id: str,
    conversation_id: str,
    stage: str,
    message: str,
    progress: int = 0,
    details: Optional[Dict[str, Any]] = None,
    filename: Optional[str] = None,
    fact_number: Optional[int] = None,
    total_facts: Optional[int] = None,
) -> None:
    """Send a status update via WebSocket during memory extraction.
    
    Sends both memory_extraction_status and memory_progress events for full transparency.
    """
    try:
        # Send memory_extraction_status event (for chat status indicator)
        payload_status = {
            "type": "memory_extraction_status",
            "user_id": user_id,
            "conversation_id": conversation_id,
            "stage": stage,
            "message": message,
            "progress": progress,  # 0-100
            "details": details or {},
        }
        await broadcast_to_app(APP_SLUG, payload_status, user_id=user_id)
        
        # Also send memory_progress event (for progress bars and modals)
        payload_progress = {
            "type": "memory_progress",
            "user_id": user_id,
            "conversation_id": conversation_id,
            "stage": stage,
            "message": message,
            "progress": progress,
            "details": details or {},
            "filename": filename,
            "fact_number": fact_number,
            "total_facts": total_facts,
        }
        await broadcast_to_app(APP_SLUG, payload_progress, user_id=user_id)
        
        logger.debug(f"📡 Sent extraction status: stage={stage}, progress={progress}%")
    except (PyMongoError, ValueError, KeyError) as e:
        logger.warning(f"Failed to send extraction status: {e}")


async def _extract_memories_background(
    user_id: str,
    conversation_id: str,
    message: str,
    memory_service: "BaseMemoryService",
    bucket_id: Optional[str] = None,
    bucket_type: str = "conversation",
    category: str = "general",
    ai_response: Optional[str] = None,
) -> None:
    """
    Extract memories from user message in background after response is returned.
    
    This function provides fast response times by decoupling memory extraction from
    the main request/response cycle. The UI is notified via WebSocket with step-by-step
    status updates showing exactly what's happening.
    
    Args:
        user_id: User ID for scoping memory operations
        conversation_id: Conversation/session ID for metadata
        message: The user message to extract facts from
        memory_service: Memory service instance for storage
        bucket_id: Optional bucket ID for memory isolation
        bucket_type: Type of bucket (category, conversation, etc.)
        category: Memory category for metadata
        ai_response: The AI response text (optional). If provided, it allows
                    extracting memories from the full interaction context.
    
    Note:
        This function is designed to run as a background task via asyncio.create_task().
        Failures are logged but do not affect the main request/response cycle.
    """
    if not user_id or not message.strip():
        logger.warning("⚠️ _extract_memories_background called with empty user_id or message, skipping")
        return
    
    if not memory_service:
        logger.warning("⚠️ _extract_memories_background called with None memory_service, skipping")
        return
    
    try:
        # Stage 1: Starting
        await _send_extraction_status(
            user_id, conversation_id,
            stage="starting",
            message="🔄 Starting memory extraction...",
            progress=10,
        )
        
        logger.info(
            f"🧠 [Background Extraction] Starting memory extraction: "
            f"user_id={user_id}, message='{message[:50]}...'"
        )
        
        # Stage 2: Analyzing
        await _send_extraction_status(
            user_id, conversation_id,
            stage="analyzing",
            message="🔍 Analyzing message for memorable facts...",
            progress=30,
            details={"message_length": len(message)},
        )
        
        # Build storage metadata
        storage_bucket_id = bucket_id or f"session:{conversation_id}"
        storage_metadata = {
            "source": "chat_session",
            "session_id": conversation_id,
            "category": category,
            "associated_bucket_id": storage_bucket_id,
            "raw_input": message,
            "raw_output": ai_response,
        }
        
        # Extract memories from user message only
        # AI responses are conversational and shouldn't be stored as memories
        # We include AI response as context to help understand user's question, but only extract from user message
        extraction_text = message
        if ai_response:
            # Include AI response as context, but extraction should focus on user message
            # The cognitive extraction prompt already instructs to ignore AI responses
            extraction_text = f"User: {message}\nAI: {ai_response}\n\nNote: Only extract facts from the USER message above, not from the AI response."
        
        # Stage 3: Preparing extraction
        await _send_extraction_status(
            user_id, conversation_id,
            stage="preparing_extraction",
            message="🔧 Preparing AI extraction pipeline...",
            progress=40,
        )
        await asyncio.sleep(0.1)  # Brief pause for UI update
        
        # Stage 4: Extracting (the LLM call happens here)
        await _send_extraction_status(
            user_id, conversation_id,
            stage="extracting",
            message="🧠 Analyzing message with AI...",
            progress=45,
        )
        
        # Send periodic updates during extraction (heartbeat)
        extraction_start = asyncio.get_event_loop().time()
        heartbeat_task = None
        
        async def send_extraction_heartbeat():
            """Send periodic progress updates during extraction."""
            heartbeat_count = 0
            while True:
                await asyncio.sleep(0.8)  # Update every 800ms
                elapsed = asyncio.get_event_loop().time() - extraction_start
                heartbeat_count += 1
                
                # Gradually increase progress from 45% to 65% during extraction
                # This prevents the UI from appearing stuck
                progress_increment = min(20, heartbeat_count * 2)  # Max 20% increase
                current_progress = 45 + progress_increment
                
                await _send_extraction_status(
                    user_id, conversation_id,
                    stage="extracting",
                    message=f"🧠 AI is analyzing... ({int(elapsed)}s)",
                    progress=current_progress,
                    details={"elapsed_seconds": int(elapsed)},
                )
        
        # Start heartbeat
        heartbeat_task = asyncio.create_task(send_extraction_heartbeat())
        
        try:
            # CRITICAL: Only extract facts from USER messages, not AI responses
            # AI responses are conversational advice and should NOT be stored as memories
            # We include AI response in metadata for context, but extraction only uses user message
            if ai_response:
                # Only pass user message for extraction - AI response goes in metadata only
                extraction_messages = message  # String format - user message only
                # Store AI response in metadata for reference, but don't extract from it
                storage_metadata["ai_response_context"] = ai_response[:200]  # Truncated for metadata
            else:
                extraction_messages = message  # Use string format
            
            # Extract and store memories (this uses LLM for fact extraction)
            # Progress callback for real-time updates during extraction
            async def progress_callback(stage: str, message: str, progress: int):
                """Send progress updates via WebSocket during extraction."""
                await _send_extraction_status(
                    user_id, conversation_id,
                    stage=stage,
                    message=message,
                    progress=progress,
                )
            
            stored = await memory_service.add(
                messages=extraction_messages,  # Only user message - AI responses not extracted
                user_id=user_id,
                metadata=storage_metadata,
                bucket_id=storage_bucket_id,
                bucket_type=bucket_type,
                progress_callback=progress_callback,
            )
        finally:
            # Stop heartbeat
            if heartbeat_task:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
        
        # Stage 5: Processing results
        await _send_extraction_status(
            user_id, conversation_id,
            stage="processing_results",
            message="📊 Processing extracted facts...",
            progress=70,
        )
        await asyncio.sleep(0.1)  # Brief pause for UI update
        
        memories_count = len(stored) if isinstance(stored, list) else 0
        
        # Stage 6: Storing
        if memories_count > 0:
            await _send_extraction_status(
                user_id, conversation_id,
                stage="storing",
                message=f"💾 Storing {memories_count} new memories...",
                progress=75,
                details={"count": memories_count},
            )
            await asyncio.sleep(0.1)
            
            # Send progress updates for each memory being stored
            for idx, memory in enumerate(stored[:memories_count]):
                if idx > 0:  # Skip first update (already sent above)
                    progress = 75 + int((idx / memories_count) * 15)  # 75% to 90%
                    await _send_extraction_status(
                        user_id, conversation_id,
                        stage="storing_memories",
                        message=f"💾 Storing memory {idx + 1}/{memories_count}...",
                        progress=progress,
                        details={"count": memories_count, "current": idx + 1},
                        fact_number=idx + 1,
                        total_facts=memories_count,
                    )
                    await asyncio.sleep(0.05)  # Small delay between updates
            
            logger.info(
                f"✅ [Background Extraction] Stored {memories_count} memories "
                f"for user_id={user_id}"
            )
            
            # Brief delay to show the storing message
            await asyncio.sleep(0.2)
            
            # Stage 5: Complete with memories
            memory_previews = [
                m.get("memory", "")[:60] + "..." if len(m.get("memory", "")) > 60 else m.get("memory", "")
                for m in (stored[:3] if isinstance(stored, list) else [])
            ]
            
            await _send_extraction_status(
                user_id, conversation_id,
                stage="complete",
                message=f"✅ Extracted {memories_count} new memories!",
                progress=100,
                details={
                    "count": memories_count,
                    "previews": memory_previews,
                },
            )
            
            # Broadcast memory_stored event to update Memory Explorer
            await _broadcast_memory_stored(
                user_id=user_id,
                conversation_id=conversation_id,
                memory_service=memory_service,
                new_memories=stored,
            )
        else:
            # Stage 5: Complete with no memories
            await _send_extraction_status(
                user_id, conversation_id,
                stage="complete",
                message="ℹ️ No new facts to remember from this message",
                progress=100,
                details={"count": 0},
            )
            
            logger.info(
                f"ℹ️ [Background Extraction] No new facts extracted from message "
                f"(user_id={user_id})"
            )
            
            # Still broadcast to signal extraction completed
            await _broadcast_memory_stored(
                user_id=user_id,
                conversation_id=conversation_id,
                memory_service=memory_service,
                new_memories=[],
            )
            
    except asyncio.CancelledError:
        logger.debug(f"🔄 Background extraction task cancelled for user_id={user_id}")
        raise
    except (PyMongoError, RuntimeError, ValueError, OSError) as e:
        # Stage: Error
        await _send_extraction_status(
            user_id, conversation_id,
            stage="error",
            message=f"❌ Memory extraction failed: {str(e)[:50]}",
            progress=0,
            details={"error": str(e)},
        )
        
        logger.error(
            f"❌ [Background Extraction] Failed: user_id={user_id}, error={e}",
            exc_info=True
        )
        # Don't re-raise - background task failures shouldn't affect main flow


async def _extract_memories_immediate(
    user_id: str,
    conversation_id: str,
    message: str,
    memory_service: "BaseMemoryService",
    bucket_id: Optional[str] = None,
    bucket_type: str = "conversation",
    category: str = "general",
) -> List[str]:
    """
    Extract memories IMMEDIATELY from user prompt (before AI response).
    
    This function extracts memories from the user message right away, allowing
    them to be displayed while the AI response is streaming. The memories are
    marked with refinement_pending=True and will be refined later with the
    AI response context.
    
    Args:
        user_id: User ID for scoping memory operations
        conversation_id: Conversation/session ID for metadata
        message: The user message to extract facts from
        memory_service: Memory service instance for storage
        bucket_id: Optional bucket ID for memory isolation
        bucket_type: Type of bucket (category, conversation, etc.)
        category: Memory category for metadata
    
    Returns:
        List of memory IDs that were extracted (for later refinement)
    
    Note:
        This function is designed to run as a background task via asyncio.create_task().
        Failures are logged but do not affect the main request/response cycle.
    """
    if not user_id or not message.strip():
        logger.warning("⚠️ _extract_memories_immediate called with empty user_id or message, skipping")
        return []
    
    if not memory_service:
        logger.warning("⚠️ _extract_memories_immediate called with None memory_service, skipping")
        return []
    
    try:
        # Stage 1: Starting immediate extraction
        await _send_extraction_status(
            user_id, conversation_id,
            stage="starting",
            message="🔄 Extracting memories immediately...",
            progress=10,
        )
        
        logger.info(
            f"⚡ [Immediate Extraction] Starting immediate memory extraction: "
            f"user_id={user_id}, message='{message[:50]}...'"
        )
        
        # Build storage metadata with refinement_pending flag
        storage_bucket_id = bucket_id or f"session:{conversation_id}"
        storage_metadata = {
            "source": "chat_session",
            "session_id": conversation_id,
            "category": category,
            "associated_bucket_id": storage_bucket_id,
            "raw_input": message,
            "refinement_pending": True,
            "initial_extraction_time": datetime.utcnow().isoformat(),
        }
        
        # Stage 2: Extracting (the LLM call happens here)
        await _send_extraction_status(
            user_id, conversation_id,
            stage="extracting",
            message="🧠 Analyzing message with AI...",
            progress=45,
        )
        
        # Progress callback for real-time updates during extraction
        async def progress_callback(stage: str, message: str, progress: int):
            """Send progress updates via WebSocket during extraction."""
            await _send_extraction_status(
                user_id, conversation_id,
                stage=stage,
                message=message,
                progress=progress,
            )
        
        # Extract and store memories (this uses LLM for fact extraction)
        stored = await memory_service.add(
            messages=message,  # User message only - no AI response yet
            user_id=user_id,
            metadata=storage_metadata,
            bucket_id=storage_bucket_id,
            bucket_type=bucket_type,
            progress_callback=progress_callback,
        )
        
        memories_count = len(stored) if isinstance(stored, list) else 0
        memory_ids = []
        
        if memories_count > 0:
            # Extract memory IDs from stored memories
            for memory in stored:
                memory_id = memory.get("id") or memory.get("_id")
                if memory_id:
                    memory_ids.append(str(memory_id))
            
            # Stage 3: Complete with memories
            await _send_extraction_status(
                user_id, conversation_id,
                stage="complete",
                message=f"✅ Extracted {memories_count} memories (refining after response)...",
                progress=100,
                details={
                    "count": memories_count,
                    "memory_ids": memory_ids,
                    "refinement_pending": True,
                },
            )
            
            # Send immediate extraction event with new memories
            await broadcast_to_app(
                APP_SLUG,
                {
                    "type": "memory_extracted_immediate",
                    "user_id": user_id,
                    "conversation_id": conversation_id,
                    "new_memories": stored[:MAX_NEW_MEMORIES_TO_DISPLAY],
                    "memory_ids": memory_ids,
                    "total_count": memories_count,
                },
                user_id=user_id,
            )
            
            # Broadcast memory_stored event to update Memory Explorer
            await _broadcast_memory_stored(
                user_id=user_id,
                conversation_id=conversation_id,
                memory_service=memory_service,
                new_memories=stored,
            )
            
            logger.info(
                f"✅ [Immediate Extraction] Extracted {memories_count} memories "
                f"(refinement_pending=True) for user_id={user_id}"
            )
        else:
            # No memories extracted
            await _send_extraction_status(
                user_id, conversation_id,
                stage="complete",
                message="ℹ️ No new facts to remember from this message",
                progress=100,
                details={"count": 0},
            )
            
            logger.info(
                f"ℹ️ [Immediate Extraction] No new facts extracted from message "
                f"(user_id={user_id})"
            )
        
        return memory_ids
        
    except asyncio.CancelledError:
        logger.debug(f"🔄 Immediate extraction task cancelled for user_id={user_id}")
        raise
    except (PyMongoError, RuntimeError, ValueError, OSError) as e:
        # Stage: Error
        await _send_extraction_status(
            user_id, conversation_id,
            stage="error",
            message=f"❌ Immediate memory extraction failed: {str(e)[:50]}",
            progress=0,
            details={"error": str(e)},
        )
        
        logger.error(
            f"❌ [Immediate Extraction] Failed: user_id={user_id}, error={e}",
            exc_info=True
        )
        # Return empty list on error - don't re-raise
        return []


async def _refine_memories_with_context(
    user_id: str,
    conversation_id: str,
    initial_memory_ids: List[str],
    ai_response: str,
    memory_service: "BaseMemoryService",
    bucket_id: Optional[str] = None,
) -> None:
    """
    Refine memories with AI response context.
    
    This function takes initial memories extracted from user prompt and refines them
    using the AI response context. It makes a lightweight LLM call to refine/extend
    memories and updates them in-place.
    
    Args:
        user_id: User ID for scoping memory operations
        conversation_id: Conversation/session ID for metadata
        initial_memory_ids: List of memory IDs to refine
        ai_response: The AI response text used for refinement context
        memory_service: Memory service instance for refinement
        bucket_id: Optional bucket ID for memory isolation
    
    Note:
        This function is designed to run as a background task via asyncio.create_task().
        Failures are logged but do not affect the main request/response cycle.
    """
    if not user_id or not initial_memory_ids:
        logger.warning("⚠️ _refine_memories_with_context called with empty user_id or memory_ids, skipping")
        return
    
    if not memory_service:
        logger.warning("⚠️ _refine_memories_with_context called with None memory_service, skipping")
        return
    
    if not ai_response or not ai_response.strip():
        logger.warning("⚠️ _refine_memories_with_context called with empty ai_response, skipping")
        return
    
    try:
        # Stage 1: Starting refinement
        await _send_extraction_status(
            user_id, conversation_id,
            stage="refinement_started",
            message="🔧 Refining memories with AI context...",
            progress=10,
        )
        
        # Send refinement started event
        await broadcast_to_app(
            APP_SLUG,
            {
                "type": "memory_refinement_started",
                "user_id": user_id,
                "conversation_id": conversation_id,
                "memory_ids": initial_memory_ids,
            },
            user_id=user_id,
        )
        
        logger.info(
            f"🔧 [Refinement] Starting memory refinement: "
            f"user_id={user_id}, memory_ids={len(initial_memory_ids)}"
        )
        
        # Check if memory_service has refine_memories_with_context method
        if hasattr(memory_service, 'refine_memories_with_context'):
            # Progress callback for real-time updates during refinement
            async def progress_callback(stage: str, message: str, progress: int):
                """Send progress updates via WebSocket during refinement."""
                await _send_extraction_status(
                    user_id, conversation_id,
                    stage=stage,
                    message=message,
                    progress=progress,
                )
            
            # Call refinement method
            refined_memories = await memory_service.refine_memories_with_context(
                memory_ids=initial_memory_ids,
                ai_response=ai_response,
                user_id=user_id,
                progress_callback=progress_callback,
            )
            
            if refined_memories:
                # Stage 2: Refinement complete
                await _send_extraction_status(
                    user_id, conversation_id,
                    stage="refinement_complete",
                    message=f"✅ Refined {len(refined_memories)} memories",
                    progress=100,
                    details={"count": len(refined_memories)},
                )
                
                # Send refinement complete event with refined memories
                await broadcast_to_app(
                    APP_SLUG,
                    {
                        "type": "memory_refined",
                        "user_id": user_id,
                        "conversation_id": conversation_id,
                        "refined_memories": refined_memories[:MAX_NEW_MEMORIES_TO_DISPLAY],
                        "memory_ids": initial_memory_ids,
                        "total_count": len(refined_memories),
                    },
                    user_id=user_id,
                )
                
                # Broadcast memory_stored event to update Memory Explorer
                await _broadcast_memory_stored(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    memory_service=memory_service,
                    new_memories=refined_memories,
                )
                
                logger.info(
                    f"✅ [Refinement] Refined {len(refined_memories)} memories "
                    f"for user_id={user_id}"
                )
            else:
                # No memories were refined (maybe they were already accurate)
                await _send_extraction_status(
                    user_id, conversation_id,
                    stage="refinement_complete",
                    message="ℹ️ Memories were already accurate",
                    progress=100,
                    details={"count": 0},
                )
                
                logger.info(
                    f"ℹ️ [Refinement] No memories needed refinement "
                    f"(user_id={user_id})"
                )
        else:
            logger.warning(
                "⚠️ [Refinement] Memory service does not support refine_memories_with_context, "
                "skipping refinement"
            )
            
    except asyncio.CancelledError:
        logger.debug(f"🔄 Refinement task cancelled for user_id={user_id}")
        raise
    except (PyMongoError, RuntimeError, ValueError, OSError) as e:
        # Stage: Error
        await _send_extraction_status(
            user_id, conversation_id,
            stage="error",
            message=f"❌ Memory refinement failed: {str(e)[:50]}",
            progress=0,
            details={"error": str(e)},
        )
        
        logger.error(
            f"❌ [Refinement] Failed: user_id={user_id}, error={e}",
            exc_info=True
        )
        # Don't re-raise - background task failures shouldn't affect main flow


async def _broadcast_memory_stored(
    user_id: str,
    conversation_id: str,
    memory_service: "BaseMemoryService",
    new_memories: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """
    Broadcast memory_stored event via WebSocket after a delay to allow vector index to update.
    
    This function ensures the Memory Explorer UI automatically refreshes when new memories
    are stored. It includes proper error handling, input validation, and structured logging.
    
    The delay is necessary because MongoDB Atlas vector indexes update asynchronously, and
    we want to ensure the frontend receives consistent data when it queries for memories.
    
    Args:
        user_id: User ID for scoping memory operations
        conversation_id: Conversation/session ID for filtering new memories
        memory_service: Memory service instance (must implement BaseMemoryService interface)
        new_memories: Optional list of newly stored memories from CognitiveEngine.
                     If provided, these are used for immediate display. Otherwise, memories
                     are filtered by conversation_id from fresh database query.
    
    Raises:
        Logs errors but does not raise exceptions to prevent disrupting the main request flow.
        Background tasks should fail gracefully.
    
    Note:
        This function is designed to run as a background task via asyncio.create_task().
        Failures are logged but do not affect the main request/response cycle.
    """
    if not user_id:
        logger.warning("⚠️ _broadcast_memory_stored called with empty user_id, skipping broadcast")
        return
    
    if not conversation_id:
        logger.warning("⚠️ _broadcast_memory_stored called with empty conversation_id, skipping broadcast")
        return
    
    if not memory_service:
        logger.warning("⚠️ _broadcast_memory_stored called with None memory_service, skipping broadcast")
        return
    
    try:
        # Wait for vector index to update (MongoDB Atlas indexes update asynchronously)
        # This delay ensures the vector index is consistent for search operations
        await asyncio.sleep(VECTOR_INDEX_UPDATE_DELAY_SECONDS)
        
        # Fetch fresh memories to broadcast (ensures we have the latest state)
        try:
            fresh_memories = await memory_service.get_all(
                user_id=str(user_id),
                limit=MAX_MEMORIES_TO_FETCH
            )
        except (PyMongoError, ValueError, KeyError) as fetch_error:
            logger.error(
                f"❌ Failed to fetch fresh memories for broadcast (user_id={user_id}): {fetch_error}",
                exc_info=True
            )
            # Continue with provided new_memories if available, otherwise abort
            if not new_memories:
                logger.warning("⚠️ No fresh memories and no new_memories provided, aborting broadcast")
                return
            fresh_memories = []
        
        # Format memories for broadcast with validation
        formatted_memories: List[Dict[str, Any]] = []
        if isinstance(fresh_memories, list):
            for m in fresh_memories:
                if isinstance(m, dict) and m.get("memory"):
                    formatted_memories.append({
                        "id": str(m.get("id", "")),  # Ensure ID is string
                        "memory": str(m.get("memory", "")),  # Ensure memory is string
                        "metadata": m.get("metadata", {}),  # Preserve metadata dict
                    })
        
        # Determine new memories to highlight
        formatted_new: List[Dict[str, Any]] = []
        new_memories_count = 0
        
        if new_memories and isinstance(new_memories, list):
            # Use provided new_memories from CognitiveEngine result (preferred)
            for m in new_memories:
                if isinstance(m, dict) and m.get("memory"):
                    formatted_new.append({
                        "id": str(m.get("id", "")),
                        "memory": str(m.get("memory", "")),
                        "metadata": m.get("metadata", {}),
                    })
            new_memories_count = len(formatted_new)
        else:
            # Fallback: filter by conversation_id from fresh_memories
            # This is less reliable but provides a backup if new_memories not provided
            for m in formatted_memories:
                metadata = m.get("metadata", {})
                if isinstance(metadata, dict):
                    if (metadata.get("session_id") == conversation_id or
                        metadata.get("conversation_id") == conversation_id):
                        formatted_new.append(m)
            new_memories_count = len(formatted_new)
        
        # Prepare broadcast payload with validation
        broadcast_payload: Dict[str, Any] = {
            "type": "memory_stored",
            "user_id": str(user_id),
            "conversation_id": str(conversation_id),
            "memory_count": len(formatted_memories),
            "new_memories": formatted_new[:MAX_NEW_MEMORIES_TO_DISPLAY],
            "all_memories": formatted_memories,  # All memories for refresh
            "message": f"🧠 Extracted {new_memories_count} new memories"
        }
        
        # Broadcast to frontend with error handling
        try:
            await broadcast_to_app(
                APP_SLUG,
                broadcast_payload,
                user_id=str(user_id)
            )
            
            logger.info(
                f"📡 Successfully broadcasted memory_stored event: "
                f"user_id={user_id}, conversation_id={conversation_id}, "
                f"new_memories={new_memories_count}, total_memories={len(formatted_memories)}"
            )
        except (PyMongoError, ValueError, KeyError) as broadcast_error:
            logger.error(
                f"❌ Failed to broadcast WebSocket event (user_id={user_id}): {broadcast_error}",
                exc_info=True
            )
            # Don't re-raise - background task failures shouldn't affect main flow
            
    except asyncio.CancelledError:
        # Task was cancelled - this is expected behavior, don't log as error
        logger.debug(f"🔄 Memory broadcast task cancelled for user_id={user_id}")
        raise  # Re-raise to properly handle cancellation
    except (PyMongoError, RuntimeError, ValueError, OSError) as e:
        # Catch-all for any unexpected errors
        logger.error(
            f"❌ Unexpected error in _broadcast_memory_stored (user_id={user_id}, "
            f"conversation_id={conversation_id}): {e}",
            exc_info=True
        )
        # Don't re-raise - background task failures shouldn't affect main flow


@app.get("/api/buckets/{bucket_id}/files", response_class=JSONResponse)
async def get_bucket_files(
    request: Request,
    bucket_id: str,
    svc=Depends(get_memory_service),
):
    """
    Get files associated with a bucket.
    
    Best Practice: Uses dependency injection for memory service.
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not svc:
        return JSONResponse({"files": []})

    user_id = str(user["_id"])
    files_list = []

    if bucket_id.startswith("file:"):
        parts = bucket_id.split(":")
        if len(parts) >= 2:
            mems = await svc.get_all(user_id=user_id, filters={"bucket_id": bucket_id}, limit=1)
            if mems:
                files_list.append(
                    {
                        "filename": parts[1],
                        "bucket_id": bucket_id,
                        "memory_count": len(mems),
                        "upload_date": mems[0].get("created_at") or "Unknown",
                    }
                )
    else:
        # Get by association
        mems = await svc.get_all(user_id=user_id, filters={"associated_bucket_id": bucket_id}, limit=500)
        seen = set()
        for m in mems:
            meta = m.get("metadata", {})
            f_bucket = meta.get("bucket_id")
            if (
                meta.get("filename")
                and f_bucket
                and f_bucket.startswith("file:")
                and f_bucket not in seen
            ):
                files_list.append(
                    {
                        "filename": meta.get("filename"),
                        "bucket_id": f_bucket,
                        "upload_date": meta.get("created_at") or "Unknown",
                        "author": meta.get("doc_author", "Unknown"),  # Return author to frontend
                    }
                )
                seen.add(f_bucket)

    return JSONResponse({"success": True, "files": files_list})


@app.post("/api/buckets/{bucket_id}/files", response_class=JSONResponse)
async def add_file_to_bucket(
    request: Request,
    bucket_id: str,
    files: list[UploadFile] = File(default=[]),
    svc=Depends(get_memory_service),
):
    """
    Add files to a bucket and process them for memory storage.
    
    Best Practice: Uses dependency injection for memory service.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    user_id = str(user["_id"])

    file_list = files or []
    if not file_list:
        try:
            form = await request.form()
            file_list = [v for v in form.getlist("files") if isinstance(v, UploadFile)]
        except (ValueError, TypeError, KeyError) as exc:  # noqa: BLE001
            logger.debug("Could not parse form for file list: %s", exc)
    if not file_list:
        return JSONResponse({"success": False, "error": "No files provided"})

    parts = bucket_id.split(":")
    category = parts[1] if len(parts) > 1 and parts[0] == "bucket" else "general"
    processed_count = 0

    for f in file_list:
        try:
            data = await convert_file_to_markdown(f)
            if data["raw_text"]:
                await process_and_store_file_memory(
                    svc=svc,
                    user_id=user_id,
                    file_data=data,
                    category=category,
                    associated_bucket_id=bucket_id,
                )
                processed_count += 1
        except (ValueError, TypeError, KeyError, AttributeError) as e:
            logger.exception(f"Failed to add file to bucket: {e}")

    return JSONResponse({"success": True, "processed": processed_count})


@app.get("/api/memories", response_class=JSONResponse)
async def get_all_memories(
    request: Request,
    limit: int = 500,
    svc=Depends(get_memory_service),
):
    """
    Get all memories for the current user.
    
    Best Practice: Uses dependency injection for memory service.
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not svc:
        return JSONResponse({"success": True, "memories": [], "count": 0})

    memories = await svc.get_all(user_id=str(user["_id"]), limit=limit)
    normalized = normalize_memories(memories)
    return JSONResponse({"success": True, "memories": normalized, "count": len(normalized)})


@app.get("/api/memories/stats", response_class=JSONResponse)
async def get_memory_stats(
    request: Request,
    svc=Depends(get_memory_service),
):
    """
    Get memory statistics for the current user.
    
    Best Practice: Uses dependency injection for memory service.
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"success": False}, status_code=401)
    if not svc:
        return JSONResponse({"success": False, "error": "No service"})

    all_mems = await svc.get_all(user_id=str(user["_id"]), limit=2000)
    stats = {"file_contexts": {}, "general_buckets": {}, "bucket_files": {}}
    buckets = {}

    for m in all_mems:
        meta = m.get("metadata", {})
        bid = meta.get("bucket_id") or meta.get("context_id")
        if bid:
            if bid not in buckets:
                buckets[bid] = {
                    "bucket_id": bid,
                    "bucket_type": meta.get("bucket_type", "general"),
                    "memory_count": 0,
                    "metadata": meta,
                }
            buckets[bid]["memory_count"] += 1

    for b in buckets.values():
        bid = b["bucket_id"]
        meta = b.get("metadata", {})
        if b["bucket_type"] == "file":
            fname = meta.get("filename") or "Unknown"
            stats["file_contexts"][fname] = {
                "context_id": bid,
                "count": b["memory_count"],
                "bucket_type": "file",
            }
            assoc = meta.get("associated_bucket_id")
            if assoc:
                if assoc not in stats["bucket_files"]:
                    stats["bucket_files"][assoc] = {}
                stats["bucket_files"][assoc][bid] = {
                    "filename": fname,
                    "bucket_id": bid,
                    "memory_count": b["memory_count"],
                    "author": meta.get("doc_author", "Unknown"),
                }
        else:
            cat = meta.get("category", "General")
            stats["general_buckets"][bid] = {"name": cat, "count": b["memory_count"]}

    stats["bucket_files"] = {k: list(v.values()) for k, v in stats["bucket_files"].items()}
    
    # Add Perfect Brain feature counts
    perfect_brain_stats = {}
    user_id = str(user["_id"])
    
    try:
        # Shared memory stats
        if shared_memory:
            # Count shared memories for groups this user belongs to
            # Note: This is a simplified version - in production, track user-group relationships
            motor_client = engine._connection_manager.mongo_client
            motor_db = motor_client[engine.db_name]
            shared_collection = motor_db.get_collection("entity_memory")
            shared_count = await shared_collection.count_documents({"scope": "shared"})
            perfect_brain_stats["shared_memories"] = shared_count
        
        # Reflective memory stats
        if reflective_memory:
            motor_client = engine._connection_manager.mongo_client
            motor_db = motor_client[engine.db_name]
            reflective_collection = motor_db.get_collection("reflective_memory")
            reflective_count = await reflective_collection.count_documents({"user_id": user_id})
            perfect_brain_stats["reflections"] = reflective_count
        
        # Predictive memory stats
        if predictive_memory:
            motor_client = engine._connection_manager.mongo_client
            motor_db = motor_client[engine.db_name]
            predictive_collection = motor_db.get_collection("predictive_memory")
            predictive_total = await predictive_collection.count_documents({"user_id": user_id})
            predictive_validated = await predictive_collection.count_documents({"user_id": user_id, "validated": True})
            predictive_unvalidated = await predictive_collection.count_documents({"user_id": user_id, "validated": False})
            perfect_brain_stats["predictions"] = {
                "total": predictive_total,
                "validated": predictive_validated,
                "unvalidated": predictive_unvalidated,
            }
        
        # Memory veto stats
        if memory_veto:
            vetoes = await asyncio.to_thread(memory_veto.get_user_vetoes, user_id=user_id)
            perfect_brain_stats["vetoes"] = len(vetoes)
        
        # Version history stats (count entities with history)
        if memory_versioning:
            # This would require querying for entities with history - simplified for now
            perfect_brain_stats["versioned_entities"] = 0  # Placeholder
        
        # Multi-tier memory stats
        motor_client = engine._connection_manager.mongo_client
        motor_db = motor_client[engine.db_name]
        
        # Episodic memory stats
        episodic_collection = motor_db.get_collection("episodic")
        episodic_total = await episodic_collection.count_documents({"user_id": user_id})
        episodic_consolidated = await episodic_collection.count_documents({"user_id": user_id, "consolidated": True})
        episodic_unconsolidated = await episodic_collection.count_documents({"user_id": user_id, "consolidated": {"$ne": True}})
        perfect_brain_stats["episodic"] = {
            "total": episodic_total,
            "consolidated": episodic_consolidated,
            "unconsolidated": episodic_unconsolidated,
        }
        
        # Procedural memory stats
        procedural_collection = motor_db.get_collection("procedural")
        procedural_total = await procedural_collection.count_documents({"user_id": user_id})
        perfect_brain_stats["procedural"] = {
            "total": procedural_total,
        }
        
        # Working memory stats (active sessions)
        working_collection = motor_db.get_collection("working_memory")
        working_active = await working_collection.count_documents({"user_id": user_id})
        perfect_brain_stats["working"] = {
            "active_sessions": working_active,
        }
        
        # Semantic entity stats
        entity_collection = motor_db.get_collection("entity_memory")
        semantic_total = await entity_collection.count_documents({"user_id": user_id, "scope": "user"})
        perfect_brain_stats["semantic"] = {
            "total_entities": semantic_total,
        }
        
        # Consolidation stats (if consolidator available)
        if memory_consolidator:
            # Get last consolidation run info (would need to track this)
            perfect_brain_stats["consolidation"] = {
                "available": True,
                "last_run": None,  # Would need to track this
            }
        
        # GraphRAG stats
        graph_service = get_graph_service_from_request(request, svc)
        if graph_service:
            try:
                graph_stats = await graph_service.get_stats()
                perfect_brain_stats["graphrag"] = {
                    "enabled": True,
                    "total_nodes": graph_stats.get("total_nodes", 0),
                    "total_edges": graph_stats.get("total_edges", 0),
                    "node_types": graph_stats.get("node_types", {}),
                }
            except (PyMongoError, ValueError, KeyError) as graph_error:
                logger.warning(f"Failed to get GraphRAG stats: {graph_error}")
                perfect_brain_stats["graphrag"] = {"enabled": True, "error": str(graph_error)}
        else:
            perfect_brain_stats["graphrag"] = {"enabled": False}
        
    except (PyMongoError, ValueError, KeyError) as e:
        logger.warning(f"Failed to get Perfect Brain stats: {e}")
        perfect_brain_stats["error"] = str(e)
    
    stats["perfect_brain"] = perfect_brain_stats
    return JSONResponse({"success": True, "stats": stats})


@app.get("/api/memories/by-context", response_class=JSONResponse)
async def get_memories_by_context(
    request: Request,
    bucket_id: str,
    limit: int = 100,
    svc=Depends(get_memory_service),
):
    """
    Get memories filtered by bucket/context ID.
    
    Uses associated_bucket_id for bucket-aware filtering, which finds:
    - Conversation memories in the bucket
    - File memories associated with the bucket
    
    Best Practice: Uses dependency injection for memory service.
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not svc:
        return JSONResponse({"success": True, "memories": [], "memoryCount": 0})
    # Use associated_bucket_id for bucket-aware filtering (finds both conversation and file memories)
    mems = await svc.get_all(user_id=str(user["_id"]), filters={"metadata": {"associated_bucket_id": bucket_id}}, limit=limit)
    normalized = normalize_memories(mems)
    return JSONResponse({"success": True, "memories": normalized, "memoryCount": len(normalized)})


@app.get("/api/memories/search", response_class=JSONResponse)
async def search_memories(
    request: Request,
    query: str,
    bucket_id: str | None = None,
    category: str | None = None,  # Convenience: constructs bucket_id server-side
    search_all: bool = False,
    limit: int = 50,
    task_type: str = "general",  # QueryAwareRecall: "fast_answer", "critical_decision", "general", "exploration"
    risk_tolerance: str = "medium",  # QueryAwareRecall: "low", "medium", "high"
    latency_budget: str = "normal",  # QueryAwareRecall: "fast", "normal", "deep"
    timeline_id: str = "root",  # Cognitive OS: Timeline ID to search in (default: "root")
    svc=Depends(get_memory_service),
):
    """
    Search memories using True Perfect Recall semantic search.
    
    **True Perfect Recall**: Every memory is always searchable, forever.
    Ranking (similarity, importance, emotion, recency, access count) handles relevance.
    No confidence filtering -- all memories are accessible.
    
    **Security**: Requires explicit bucket scoping to prevent accidental cross-bucket data leakage.
    
    Parameters:
    - query: Search query string (required)
    - bucket_id: Full bucket ID to search within (e.g., "category:work:user123") - required UNLESS search_all=true
    - category: Category name (e.g., "work", "coding") - convenience parameter that constructs bucket_id server-side
    - search_all: If true, search across all buckets for the user (explicit opt-in)
    - limit: Maximum number of results (default: 50)
    - timeline_id: Timeline ID to search in (default: "root") - Cognitive OS feature
    
    Best Practice: Uses dependency injection for memory service.
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not svc:
        return JSONResponse({"success": True, "results": [], "count": 0})
    
    user_id = str(user["_id"])
    
    # If category provided, construct bucket_id server-side (convenience)
    if category:
        if bucket_id:
            return JSONResponse(
                {
                    "error": "bucket_id and category are mutually exclusive",
                    "detail": "Provide either bucket_id (full ID) OR category (name), not both."
                },
                status_code=400
            )
        bucket_id = f"category:{category}:{user_id}"
    
    # Validation: Require either bucket_id OR search_all=true (mutually exclusive)
    if not bucket_id and not search_all:
        return JSONResponse(
            {
                "error": "Either bucket_id, category, or search_all=true must be provided",
                "detail": "For security, memory search requires explicit bucket scoping. Provide bucket_id (or category) to search within a specific bucket, or search_all=true to search across all buckets."
            },
            status_code=400
        )
    
    if bucket_id and search_all:
        return JSONResponse(
            {
                "error": "bucket_id and search_all are mutually exclusive",
                "detail": "Provide either bucket_id (for scoped search) OR search_all=true (for cross-bucket search), not both."
            },
            status_code=400
        )
    
    # Use QueryAwareRecall if available, otherwise fall back to standard search
    if query_aware_recall and svc:
        try:
            # Get the semantic collection for QueryAwareRecall
            # QueryAwareRecall works with collections directly
            motor_client = engine._connection_manager.mongo_client
            motor_db = motor_client[engine.db_name]
            semantic_collection = motor_db.get_collection(svc.collection_name if hasattr(svc, 'collection_name') else "user_memories")
            
            # Determine scope based on bucket_id
            scope = "user"
            group_id = None
            if bucket_id and ":" in bucket_id:
                # Extract group_id from bucket_id if it's a group bucket
                parts = bucket_id.split(":")
                if len(parts) >= 3 and parts[0] in ["category", "bucket"]:
                    # Check if it's a group bucket (doesn't end with user_id)
                    if not bucket_id.endswith(user_id):
                        scope = "shared"
                        # Extract group_id (e.g., "category:CODE:team-001" -> "team-001")
                        group_id = parts[-1] if len(parts) > 2 else None
            
            # Use QueryAwareRecall
            recall_result = await asyncio.to_thread(
                query_aware_recall.recall,
                query=query,
                user_id=user_id,
                collection=semantic_collection,
                task_type=task_type,
                risk_tolerance=risk_tolerance,
                latency_budget=latency_budget,
                scope=scope,
                group_id=group_id,
                bucket_id=bucket_id if not search_all else None,
                memory_veto=memory_veto,
            )
            
            results = recall_result.get("memories", [])
            policy = recall_result.get("policy", {})
            
            logger.info(f"🔍 [QueryAwareRecall] task_type={task_type}, risk_tolerance={risk_tolerance}, latency_budget={latency_budget}, found {len(results)} results")
        except (PyMongoError, ValueError, KeyError) as e:
            logger.warning(f"QueryAwareRecall failed, falling back to standard search: {e}")
            # Fall back to standard search
            filters = None
            if search_all:
                filters = None
                logger.info(f"🔍 [Memory Search] Cross-bucket search requested for user_id={user_id}")
            else:
                filters = {"metadata": {"associated_bucket_id": bucket_id}}
                logger.info(f"🔍 [Memory Search] Scoped search: bucket_id={bucket_id}, user_id={user_id}")
            
            results = await svc.search(
                query=query, 
                user_id=user_id, 
                limit=limit, 
                filters=filters,
                timeline_id=timeline_id,
            )
            policy = None
    else:
        # Standard search (True Perfect Recall: no confidence filtering)
        filters = None
        if search_all:
            filters = None
            logger.info(f"🔍 [Memory Search] Cross-bucket search requested for user_id={user_id}")
        else:
            filters = {"metadata": {"associated_bucket_id": bucket_id}}
            logger.info(f"🔍 [Memory Search] Scoped search: bucket_id={bucket_id}, user_id={user_id}")
        
        results = await svc.search(
            query=query, 
            user_id=user_id, 
            limit=limit, 
            filters=filters,
            timeline_id=timeline_id,
        )
        policy = None

    normalized_results = []
    if isinstance(results, list):
        for res in results:
            if isinstance(res, dict):
                normalized_results.append(
                    {
                        "memory": res.get("memory")
                        or res.get("data", {}).get("memory")
                        or res.get("text")
                        or str(res),
                        "id": res.get("id") or res.get("_id"),
                        "metadata": res.get("metadata", {}),
                        "score": res.get("score"),
                    }
                )
            elif isinstance(res, str):
                normalized_results.append({"memory": res})

    response_data = {
        "success": True,
        "results": normalized_results,
        "count": len(normalized_results),
        "query": query,
    }
    
    # Add QueryAwareRecall policy info if available
    if policy:
        response_data["policy"] = policy
        response_data["recall_method"] = "query_aware"
    else:
        response_data["recall_method"] = "standard"
    
    return JSONResponse(response_data)


@app.post("/api/memories/timelines/fork", response_class=JSONResponse)
async def fork_timeline_endpoint(
    request: Request,
    svc=Depends(get_memory_service),
):
    """
    Fork a new timeline for counterfactual reasoning.
    
    Creates a new timeline branching from the current timeline, enabling
    parallel memory timelines for "what if" scenarios.
    
    Body:
    - current_timeline: Timeline ID to fork from (default: "root")
    - new_name: Display name for the new timeline (required)
    
    Returns:
    - timeline_id: New timeline ID
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not svc:
        return JSONResponse({"success": False, "error": "Memory service not available"}, status_code=503)
    
    user_id = str(user["_id"])
    
    try:
        body = await request.json()
        current_timeline = body.get("current_timeline", "root")
        new_name = body.get("new_name")
        
        if not new_name:
            return JSONResponse({"error": "new_name is required"}, status_code=400)
        
        new_timeline_id = await svc.fork_timeline(current_timeline, new_name, user_id)
        return JSONResponse({"success": True, "timeline_id": new_timeline_id})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to fork timeline: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/memories/timelines", response_class=JSONResponse)
async def list_timelines_endpoint(request: Request):
    """
    List all timelines for the current user.
    
    Returns:
    - timelines: List of timeline documents with _id, name, parent, created_at
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not timeline_service:
        return JSONResponse({"success": False, "error": "TimelineService not available"}, status_code=503)
    
    user_id = str(user["_id"])
    
    try:
        timelines = await timeline_service.list_timelines_async(user_id=user_id)
        return JSONResponse({"success": True, "timelines": timelines})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to list timelines: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/memories/timelines/{timeline_id}/ancestry", response_class=JSONResponse)
async def get_timeline_ancestry_endpoint(
    request: Request,
    timeline_id: str,
):
    """
    Get timeline ancestry chain (for inheritance).
    
    Returns the hierarchical chain from the specified timeline to root,
    enabling understanding of timeline inheritance.
    
    Example: If timeline C is child of B, which is child of root:
    Returns: ["branch_c", "branch_b", "root"]
    
    This shows which timelines are searched when querying in this timeline.
    
    Returns:
    - ancestry: List of timeline IDs from current to root (inclusive)
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not timeline_service:
        return JSONResponse({"success": False, "error": "TimelineService not available"}, status_code=503)
    
    try:
        ancestry = await timeline_service.get_timeline_ancestry_async(timeline_id)
        return JSONResponse({"success": True, "ancestry": ancestry})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to get timeline ancestry: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/memories/timelines/current", response_class=JSONResponse)
async def get_current_timeline_endpoint(
    request: Request,
    svc=Depends(get_memory_service),
):
    """
    Get user's current active timeline.
    
    Returns the timeline ID that is currently active for the user.
    This is the timeline used by default for memory operations.
    
    Returns:
    - timeline_id: Current active timeline ID (default: "root")
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not svc:
        return JSONResponse({"success": False, "error": "Memory service not available"}, status_code=503)
    
    user_id = str(user["_id"])
    
    try:
        current_timeline = await svc.get_current_timeline(user_id)
        return JSONResponse({"success": True, "timeline_id": current_timeline})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to get current timeline: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/memories/timelines/switch", response_class=JSONResponse)
async def switch_timeline_endpoint(
    request: Request,
    svc=Depends(get_memory_service),
):
    """
    Switch user's active timeline context.
    
    Changes the default timeline used for memory operations.
    This enables users to work in different "realities" or contexts.
    
    Body:
    - timeline_id: Timeline ID to switch to (required)
    
    Returns:
    - success: True if timeline was switched successfully
    - timeline_id: New active timeline ID
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not svc:
        return JSONResponse({"success": False, "error": "Memory service not available"}, status_code=503)
    
    user_id = str(user["_id"])
    
    try:
        body = await request.json()
        timeline_id = body.get("timeline_id")
        
        if not timeline_id:
            return JSONResponse({"error": "timeline_id is required"}, status_code=400)
        
        await svc.switch_timeline(user_id, timeline_id)
        return JSONResponse({"success": True, "timeline_id": timeline_id})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to switch timeline: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/memories/{memory_id}/raw", response_class=JSONResponse)
async def get_memory_raw(
    request: Request,
    memory_id: str,
    svc=Depends(get_memory_service),
):
    """
    Get raw content associated with a memory.
    
    Best Practice: Uses dependency injection for memory service.
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not svc:
        return JSONResponse({"success": False, "error": "Memory service not available"})
    user_id = str(user["_id"])

    memory = await svc.get(memory_id=memory_id, user_id=user_id)
    bucket_id = memory_id
    if memory:
        bucket_id = memory.get("metadata", {}).get("bucket_id") or memory.get("metadata", {}).get(
            "associated_bucket_id"
        )

    raw_content = None
    filename = None
    if raw_content_service and bucket_id:
        raw_content = await raw_content_service.get_raw_content(
            bucket_id=bucket_id, user_id=user_id
        )

    if not raw_content and memory:
        raw_content = memory.get("metadata", {}).get("raw_content")
        filename = memory.get("metadata", {}).get("filename")

    if not raw_content:
        return JSONResponse({"success": False, "error": "Raw content not available"})
    return JSONResponse({"success": True, "raw_content": raw_content, "filename": filename})


@app.post("/api/memories/inject", response_class=JSONResponse)
async def inject_memory(
    request: Request,
    svc=Depends(get_memory_service),
):
    """
    Manually inject a memory without LLM inference.
    
    Gold Standard Memory Injection API:
    - category: Memory category (biographical, preferences, temporal, relational, etc.)
    - importance: Manual importance score (0.1-1.0)
    - bucket_id: Auto-constructed from category if not provided
    - metadata: Auto-built with category, source, and conversation context
    
    Example request:
    {
        "memory": "User prefers dark mode interfaces",
        "category": "preferences",
        "importance": 0.8,
        "conversation_id": "abc123"
    }
    
    Best Practice: Uses dependency injection for memory service.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    if not svc:
        return JSONResponse(
            {
                "success": False,
                "error": "Memory service not available",
                "memory": None,
            },
            status_code=503,
        )
    
    user_id = str(user["_id"])
    
    try:
        body = await request.json()
        memory_content = body.get("memory")
        
        # Enhanced memory customization
        category = body.get("category", "general")
        importance = body.get("importance")  # Optional manual importance (0.1-1.0)
        conversation_id = body.get("conversation_id")  # Optional conversation context
        
        # Allow explicit overrides for backward compatibility
        explicit_bucket_id = body.get("bucket_id")
        explicit_metadata = body.get("metadata")
        explicit_bucket_type = body.get("bucket_type")
        
        if not memory_content:
            raise HTTPException(status_code=400, detail="Missing 'memory' field in request body")
        
        # Validate category
        valid_categories = [
            "biographical", "preferences", "temporal", 
            "relational", "general", "work", "health", "finance", "travel"
        ]
        category_normalized = category.lower() if category else "general"
        if category_normalized not in valid_categories:
            category_normalized = "general"
        
        # Validate importance
        if importance is not None:
            try:
                importance = float(importance)
                importance = max(0.1, min(1.0, importance))
            except (ValueError, TypeError):
                importance = None
        
        # Automatically determine bucket_id from category if not explicitly provided
        if explicit_bucket_id:
            bucket_id = explicit_bucket_id
        else:
            bucket_id = f"category:{category_normalized}:{user_id}"
        
        # Build enhanced metadata
        if explicit_metadata:
            metadata = dict(explicit_metadata)
        else:
            metadata = {}
        
        metadata["source"] = metadata.get("source", "manual_injection")
        metadata["category"] = category_normalized
        if importance is not None:
            metadata["manual_importance"] = importance
        if conversation_id:
            metadata["conversation_id"] = conversation_id
            metadata["session_id"] = conversation_id
        
        # Determine bucket_type if not explicitly provided
        bucket_type = explicit_bucket_type or "category"
        
        logger.info(
            f"💉 Injecting memory for user {user_id}",
            extra={
                "user_id": user_id,
                "category": category_normalized,
                "importance": importance,
                "conversation_id": conversation_id,
                "bucket_id": bucket_id,
                "bucket_type": bucket_type,
            },
        )
        
        # Inject memory without inference
        injected_memory = await svc.inject(
            memory=memory_content,
            user_id=user_id,
            metadata=metadata,
            bucket_id=bucket_id,
            bucket_type=bucket_type,
        )
        
        if not injected_memory:
            return JSONResponse(
                {
                    "success": False,
                    "error": "Failed to inject memory",
                    "memory": None,
                },
                status_code=500,
            )
        
        # Normalize memory format with enhanced fields
        if isinstance(injected_memory, dict):
            normalized_memory = {
                "memory": injected_memory.get("memory", ""),
                "id": injected_memory.get("id"),
                "category": category_normalized,
                "importance": importance or injected_memory.get("importance", 0.5),
                "metadata": injected_memory.get("metadata", {}),
                "user_id": injected_memory.get("user_id", user_id),
            }
        else:
            normalized_memory = {
                "memory": str(injected_memory),
                "id": None,
                "category": category_normalized,
            }
        
        logger.info(
            f"✅ Successfully injected memory with id={normalized_memory.get('id')} "
            f"category={category_normalized} for user {user_id}"
        )
        
        # Broadcast memory storage event
        if svc:
            broadcast_task = asyncio.create_task(
                _broadcast_memory_stored(
                    user_id=user_id,
                    conversation_id=conversation_id or "manual_inject",
                    memory_service=svc,
                    new_memories=[normalized_memory] if normalized_memory.get("id") else None,
                )
            )
            broadcast_task.add_done_callback(
                lambda t: logger.error(
                    f"❌ Memory broadcast task failed: {t.exception()}"
                ) if t.exception() else None
            )
        
        return JSONResponse({"success": True, "memory": normalized_memory})
    except HTTPException:
        raise
    except (PyMongoError, ValueError, KeyError) as e:
        logger.exception(f"Error injecting memory: {e}")
        return JSONResponse(
            {
                "success": False,
                "error": f"Failed to inject memory: {str(e)}",
                "memory": None,
            },
            status_code=500,
        )


@app.get("/api/memories/categories", response_class=JSONResponse)
async def get_memory_categories(request: Request):
    """
    Get available memory categories for the UI.
    
    Returns standard categories and custom categories from configuration.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    # Standard categories with descriptions and icons
    categories = [
        {"id": "biographical", "name": "Biographical", "icon": "👤", "description": "Personal info: name, age, occupation, family, location"},
        {"id": "preferences", "name": "Preferences", "icon": "❤️", "description": "Likes, dislikes, preferences, favorites"},
        {"id": "temporal", "name": "Temporal", "icon": "📅", "description": "Current projects, deadlines, short-term goals"},
        {"id": "relational", "name": "Relational", "icon": "👥", "description": "Relationships, feelings about others"},
        {"id": "work", "name": "Work", "icon": "💼", "description": "Job-related information and projects"},
        {"id": "health", "name": "Health", "icon": "🏥", "description": "Health conditions, medications, fitness"},
        {"id": "finance", "name": "Finance", "icon": "💰", "description": "Financial preferences and goals"},
        {"id": "travel", "name": "Travel", "icon": "✈️", "description": "Travel preferences and plans"},
        {"id": "general", "name": "General", "icon": "📝", "description": "Other facts and information"},
    ]

    return JSONResponse({
        "success": True,
        "categories": categories,
    })


@app.put("/api/memories/{memory_id}", response_class=JSONResponse)
async def update_memory(
    request: Request,
    memory_id: str,
    svc=Depends(get_memory_service),
):
    """
    Update a memory by ID.
    
    Best Practice: Uses dependency injection for memory service.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    if not svc:
        return JSONResponse(
            {"success": False, "error": "Memory service not available", "memory": None},
            status_code=503,
        )
    
    user_id = str(user["_id"])
    
    try:
        body = await request.json()
        data = body.get("data")
        
        if not data:
            raise HTTPException(status_code=400, detail="Missing 'data' field in request body")
        
        logger.info(
            f"🔄 Updating memory {memory_id} for user {user_id}",
            extra={
                "memory_id": memory_id,
                "user_id": user_id,
            },
        )
        
        # Update memory content - automatically regenerates embeddings if text changes
        updated_memory = await svc.update(memory_id=memory_id, memory=data, user_id=user_id)
        
        if updated_memory is None:
            return JSONResponse(
                {
                    "success": False,
                    "error": f"Memory {memory_id} not found or could not be updated",
                    "memory": None,
                },
                status_code=404,
            )
        
        # Normalize memory format
        normalized_memory = {
            "memory": updated_memory.get("memory", ""),
            "id": updated_memory.get("id") or memory_id,
            "metadata": updated_memory.get("metadata", {}),
            "user_id": updated_memory.get("user_id", user_id),
        }
        
        # Broadcast memory update event
        if svc:
            broadcast_task = asyncio.create_task(
                _broadcast_memory_stored(
                    user_id=user_id,
                    conversation_id="manual_update",  # Use placeholder for manual update
                    memory_service=svc,
                    new_memories=None,  # Will fetch fresh memories
                )
            )
            broadcast_task.add_done_callback(
                lambda t: logger.error(
                    f"❌ Memory broadcast task failed: {t.exception()}"
                ) if t.exception() else None
            )
        
        return JSONResponse({"success": True, "memory": normalized_memory})
    except HTTPException:
        raise
    except (PyMongoError, ValueError, KeyError) as e:
        logger.exception(f"Error updating memory {memory_id}: {e}")
        return JSONResponse(
            {
                "success": False,
                "error": f"Failed to update memory: {str(e)}",
                "memory": None,
            },
            status_code=500,
        )


@app.delete("/api/memories/{memory_id}", response_class=JSONResponse)
async def delete_memory(
    request: Request,
    memory_id: str,
    svc=Depends(get_memory_service),
):
    """
    Delete a single memory by ID.
    
    Best Practice: Uses dependency injection for memory service.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    if not svc:
        return JSONResponse(
            {
                "success": False,
                "error": "Memory service not available",
                "message": "Memory service not available",
            },
            status_code=503,
        )
    
    user_id = str(user["_id"])
    success = await svc.delete(memory_id=memory_id, user_id=user_id)
    
    # Broadcast memory deletion event (refresh UI)
    if svc and success:
        broadcast_task = asyncio.create_task(
            _broadcast_memory_stored(
                user_id=user_id,
                conversation_id="manual_delete",  # Use placeholder for manual delete
                memory_service=svc,
                new_memories=None,  # Will fetch fresh memories
            )
        )
        broadcast_task.add_done_callback(
            lambda t: logger.error(
                f"❌ Memory broadcast task failed: {t.exception()}"
            ) if t.exception() else None
        )
    
    return JSONResponse(
        {
            "success": success,
            "message": (
                f"Memory {memory_id} deleted successfully"
                if success
                else f"Memory {memory_id} not found or could not be deleted"
            ),
        }
    )


# ============================================================================
# Cognitive Memory API Routes (Advanced Features)
# ============================================================================


@app.get("/api/memories/analytics", response_class=JSONResponse)
async def get_memory_analytics(
    request: Request,
    svc=Depends(get_memory_service),
):
    """
    Get cognitive memory analytics for the current user.
    
    Returns metrics for memory health including:
    - active_memories, cold_storage_memories
    - average_strength, average_stability, average_emotion
    - weak/strong memory counts
    - Category breakdown
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    if not svc:
        return JSONResponse(
            {"success": False, "error": "Memory service not available"},
            status_code=503,
        )

    user_id = str(user["_id"])
    
    try:
        if not hasattr(svc, 'get_memory_analytics'):
            return JSONResponse({
                "success": False,
                "error": "Analytics not available for this memory provider",
            }, status_code=501)
        
        analytics = await svc.get_memory_analytics(
            user_id=user_id,
        )
        
        return JSONResponse({
            "success": True,
            "analytics": analytics,
        })
    except NotImplementedError:
        return JSONResponse({
            "success": False,
            "error": "Analytics not supported by this memory provider",
        }, status_code=501)
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to get memory analytics: {e}", exc_info=True)
        return JSONResponse({
            "success": False,
            "error": str(e),
        }, status_code=500)


@app.get("/api/memories/health", response_class=JSONResponse)
async def get_memory_health(
    request: Request,
    svc=Depends(get_memory_service),
):
    """
    Get memory health insights and recommendations.
    
    Processes analytics data into actionable health insights including:
    - Health score breakdown
    - Access pattern insights
    - Quality recommendations
    - Graph integration status
    - Timeline organization
    - Actionable recommendations
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    if not svc:
        return JSONResponse(
            {"success": False, "error": "Memory service not available"},
            status_code=503,
        )

    user_id = str(user["_id"])
    
    try:
        if not hasattr(svc, 'get_memory_analytics'):
            return JSONResponse({
                "success": False,
                "error": "Analytics not available for this memory provider",
            }, status_code=501)
        
        # Get comprehensive analytics
        analytics = await svc.get_memory_analytics(user_id=user_id)
        
        if "error" in analytics:
            return JSONResponse({
                "success": False,
                "error": analytics["error"],
            }, status_code=500)
        
        # Process analytics into health insights
        quality_metrics = analytics.get("quality_metrics", {})
        access_patterns = analytics.get("access_patterns", {})
        graph_connectivity = analytics.get("graph_connectivity", {})
        timeline_org = analytics.get("timeline_organization", {})
        
        health_score = quality_metrics.get("health_score", 0)
        
        # Calculate health breakdown
        importance_score = (quality_metrics.get("avg_importance", 0) * 40)
        confidence_score = (quality_metrics.get("avg_confidence", 0) * 30)
        avg_access = access_patterns.get("avg_access_count", 0)
        access_score = min((avg_access / 10.0) * 20, 20)
        total_memories = analytics.get("total_memories", 0)
        memories_with_links = graph_connectivity.get("memories_with_links", 0)
        graph_score = min((memories_with_links / max(total_memories, 1)) * 10, 10)
        
        # Generate recommendations
        recommendations = []
        
        if quality_metrics.get("confidence_distribution", {}).get("low", 0) > 0:
            low_conf_count = quality_metrics["confidence_distribution"]["low"]
            recommendations.append({
                "message": f"Consider reinforcing {low_conf_count} low-confidence memories to improve reliability",
                "priority": "warning",
            })
        
        if access_patterns.get("access_distribution", {}).get("low", 0) > total_memories * 0.5:
            low_access_count = access_patterns["access_distribution"]["low"]
            recommendations.append({
                "message": f"{low_access_count} memories haven't been accessed recently. Consider reviewing them",
                "priority": "info",
            })
        
        if memories_with_links < total_memories * 0.3 and total_memories > 5:
            unlinked = total_memories - memories_with_links
            recommendations.append({
                "message": f"{unlinked} memories aren't connected to the knowledge graph. Enable GraphRAG for better context",
                "priority": "info",
            })
        
        if health_score < 50:
            recommendations.append({
                "message": "Memory health score is below optimal. Focus on adding high-importance memories",
                "priority": "warning",
            })
        
        if quality_metrics.get("importance_distribution", {}).get("high", 0) < 3 and total_memories > 10:
            recommendations.append({
                "message": "Consider adding more high-importance memories to strengthen your knowledge base",
                "priority": "info",
            })
        
        # Access insights
        access_dist = access_patterns.get("access_distribution", {})
        underutilized_count = 0  # Would need to query for high importance + low access
        
        # Generate heatmap data (last 7 days) - simplified version
        # In a real implementation, you'd query access logs by date
        heatmap_data = [0] * 7  # Placeholder - would need date-based access tracking
        
        # Quality insights
        quality_insights = {
            "low_confidence_count": quality_metrics.get("confidence_distribution", {}).get("low", 0),
            "high_importance_count": quality_metrics.get("importance_distribution", {}).get("high", 0),
        }
        
        # Find memory gaps (categories with few memories)
        categories = analytics.get("categories", {})
        memory_gaps = []
        if categories:
            avg_per_category = sum(categories.values()) / len(categories)
            for cat, count in categories.items():
                if count < avg_per_category * 0.3 and count < 3:
                    memory_gaps.append(cat)
        
        quality_insights["memory_gaps"] = memory_gaps[:5]  # Top 5 gaps
        
        # Graph status
        graph_status = {
            "connected_count": memories_with_links,
            "total_memories": total_memories,
            "unlinked_count": total_memories - memories_with_links,
            "connectivity_percentage": (memories_with_links / max(total_memories, 1)) * 100,
        }
        
        # Timeline organization
        timeline_dist = timeline_org.get("timeline_distribution", {})
        timeline_organization = {
            "timeline_count": len(timeline_dist),
            "avg_memories_per_timeline": timeline_org.get("memories_per_timeline", 0),
            "timeline_distribution": dict(list(timeline_dist.items())[:10]),  # Top 10 timelines
        }
        
        health_data = {
            "health_score": health_score,
            "health_breakdown": {
                "importance_score": round(importance_score, 1),
                "confidence_score": round(confidence_score, 1),
                "access_score": round(access_score, 1),
                "graph_score": round(graph_score, 1),
            },
            "recommendations": recommendations,
            "access_insights": {
                "underutilized_count": underutilized_count,
                "heatmap_data": heatmap_data,
            },
            "quality_insights": quality_insights,
            "graph_status": graph_status,
            "timeline_organization": timeline_organization,
        }
        
        return JSONResponse({
            "success": True,
            "health": health_data,
        })
    except NotImplementedError:
        return JSONResponse({
            "success": False,
            "error": "Health insights not supported by this memory provider",
        }, status_code=501)
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to get memory health: {e}", exc_info=True)
        return JSONResponse({
            "success": False,
            "error": str(e),
        }, status_code=500)


@app.post("/api/memories/{memory_id}/restore", response_class=JSONResponse)
async def restore_from_cold_storage(
    request: Request,
    memory_id: str,
    svc=Depends(get_memory_service),
):
    """
    Restore a memory from cold storage to active status.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    if not svc:
        return JSONResponse(
            {"success": False, "error": "Memory service not available"},
            status_code=503,
        )

    user_id = str(user["_id"])
    
    try:
        if not hasattr(svc, 'restore_from_cold_storage'):
            return JSONResponse({
                "success": False,
                "error": "Restore not available",
            }, status_code=501)
        
        restored_memory = await svc.restore_from_cold_storage(
            memory_id=memory_id,
            user_id=user_id,
        )
        
        if restored_memory:
            return JSONResponse({
                "success": True,
                "memory": restored_memory,
                "message": f"Memory {memory_id} restored successfully",
            })
        else:
            return JSONResponse({
                "success": False,
                "error": "Memory not found in cold storage",
            }, status_code=404)
    except NotImplementedError:
        return JSONResponse({
            "success": False,
            "error": "Restore not supported",
        }, status_code=501)
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to restore memory: {e}", exc_info=True)
        return JSONResponse({
            "success": False,
            "error": str(e),
        }, status_code=500)


@app.post("/api/memories/check-conflict", response_class=JSONResponse)
async def check_knowledge_conflict(
    request: Request,
    svc=Depends(get_memory_service),
):
    """
    Check if new information conflicts with existing knowledge.
    
    Request body: {"fact": "User is allergic to penicillin"}
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    if not svc:
        return JSONResponse(
            {"success": False, "error": "Memory service not available"},
            status_code=503,
        )

    user_id = str(user["_id"])
    
    try:
        body = await request.json()
        new_fact = body.get("fact")
        
        if not new_fact:
            raise HTTPException(status_code=400, detail="Missing 'fact' field")
        
        conflict = await svc.detect_knowledge_conflict(
            user_id=user_id,
            new_fact=new_fact,
        )
        
        return JSONResponse({
            "success": True,
            "has_conflict": conflict is not None,
            "conflict_description": conflict,
        })
    except HTTPException:
        raise
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to check conflict: {e}", exc_info=True)
        return JSONResponse({
            "success": False,
            "error": str(e),
        }, status_code=500)


@app.post("/api/memories/prune", response_class=JSONResponse)
async def trigger_pruning(
    request: Request,
    svc=Depends(get_memory_service),
):
    """
    Manually trigger memory pruning (soft-delete weakest memories).
    
    Request body (optional): {"max_capacity": 100, "reason": "manual_cleanup"}
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    if not svc:
        return JSONResponse(
            {"success": False, "error": "Memory service not available"},
            status_code=503,
        )

    user_id = str(user["_id"])
    
    try:
        body = {}
        try:
            body = await request.json()
        except (ValueError, TypeError) as exc:
            logger.debug("Could not parse JSON body: %s", exc)
        
        max_capacity = body.get("max_capacity")
        reason = body.get("reason", "manual_trigger")
        
        if not hasattr(svc, 'prune_memories'):
            return JSONResponse({
                "success": False,
                "error": "Pruning not available",
            }, status_code=501)
        
        pruned_count = await svc.prune_memories(
            user_id=user_id,
            max_capacity=max_capacity,
            reason=reason,
        )
        
        return JSONResponse({
            "success": True,
            "pruned_count": pruned_count,
            "message": f"Pruned {pruned_count} memories to cold storage",
        })
    except NotImplementedError:
        return JSONResponse({
            "success": False,
            "error": "Pruning not supported",
        }, status_code=501)
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to prune memories: {e}", exc_info=True)
        return JSONResponse({
            "success": False,
            "error": str(e),
        }, status_code=500)


# ============================================================================
# PERFECT BRAIN FEATURES ENDPOINTS
# ============================================================================

@app.post("/api/memories/shared/promote", response_class=JSONResponse)
async def promote_to_shared(
    request: Request,
    svc=Depends(get_memory_service),
):
    """
    Promote a user memory to shared/group memory.
    
    Request body:
    {
        "fact": "We prefer async/await patterns",
        "source_user_ids": ["user1", "user2"],
        "group_id": "team-001",
        "bucket_id": "category:CODE:team-001",
        "bucket_type": "category",
        "confidence": 0.85
    }
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not shared_memory:
        return JSONResponse({"success": False, "error": "SharedMemory not available"}, status_code=503)
    
    user_id = str(user["_id"])
    
    try:
        body = await request.json()
        fact = body.get("fact")
        source_user_ids = body.get("source_user_ids", [user_id])
        group_id = body.get("group_id")
        bucket_id = body.get("bucket_id")
        bucket_type = body.get("bucket_type", "category")
        confidence = body.get("confidence", 0.8)
        sensitivity = body.get("sensitivity", "low")
        
        if not fact:
            return JSONResponse({"error": "fact is required"}, status_code=400)
        if not group_id:
            return JSONResponse({"error": "group_id is required"}, status_code=400)
        
        # Check promotion rules
        if not shared_memory.check_promotion_rules(fact, source_user_ids, sensitivity):
            return JSONResponse({
                "success": False,
                "error": "Fact does not meet promotion rules",
                "reason": "Must have multiple users, low sensitivity, and no sensitive keywords"
            }, status_code=400)
        
        # Check for vetoes
        if memory_veto:
            for source_id in source_user_ids:
                if memory_veto.check_veto("", source_id, target_scope="shared"):
                    return JSONResponse({
                        "success": False,
                        "error": "Memory veto prevents promotion",
                        "user_id": source_id
                    }, status_code=403)
        
        # Promote to shared
        result = await asyncio.to_thread(
            shared_memory.promote_to_shared,
            fact=fact,
            source_user_ids=source_user_ids,
            confidence=confidence,
            group_id=group_id,
            bucket_id=bucket_id,
            bucket_type=bucket_type,
        )
        
        return JSONResponse({
            "success": True,
            "shared_memory": result,
        })
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to promote to shared: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/memories/shared", response_class=JSONResponse)
async def get_shared_memories(
    request: Request,
    group_id: str,
    query: str | None = None,
    bucket_id: str | None = None,
    category: str | None = None,
    min_confidence: float = 0.7,
    limit: int = 10,
):
    """
    Get shared/group memories with optional bucket filtering.
    
    Parameters:
    - group_id: Generic group identifier (team, family, org, etc.)
    - query: Optional search query for vector search
    - bucket_id: Optional bucket ID to filter by
    - category: Optional category name (constructs bucket_id)
    - min_confidence: Minimum confidence threshold (default: 0.7)
    - limit: Maximum number of results (default: 10)
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not shared_memory:
        return JSONResponse({"success": False, "error": "SharedMemory not available"}, status_code=503)
    
    user_id = str(user["_id"])
    
    try:
        # Construct bucket_id from category if provided
        if category and not bucket_id:
            bucket_id = f"category:{category}:{group_id}"
        
        results = await asyncio.to_thread(
            shared_memory.get_shared_memory,
            group_id=group_id,
            query=query,
            min_confidence=min_confidence,
            limit=limit,
            bucket_id=bucket_id,
        )
        
        return JSONResponse({
            "success": True,
            "memories": results,
            "count": len(results),
            "group_id": group_id,
            "bucket_id": bucket_id,
        })
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to get shared memories: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/memories/shared/stats", response_class=JSONResponse)
async def get_shared_memory_stats(
    request: Request,
    group_id: str,
    bucket_id: str | None = None,
):
    """
    Get statistics about shared memory for a group.
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not shared_memory:
        return JSONResponse({"success": False, "error": "SharedMemory not available"}, status_code=503)
    
    try:
        stats = await asyncio.to_thread(
            shared_memory.get_shared_stats,
            group_id=group_id,
            bucket_id=bucket_id,
        )
        
        return JSONResponse({
            "success": True,
            "stats": stats,
            "group_id": group_id,
            "bucket_id": bucket_id,
        })
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to get shared memory stats: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/memories/reflections", response_class=JSONResponse)
async def store_reflection(
    request: Request,
    svc=Depends(get_memory_service),
):
    """
    Store a meta-cognitive reflection.
    
    Request body:
    {
        "reflection": "I tend to over-weight recent conversations",
        "trigger": "performance_review",
        "confidence": 0.8,
        "scope": "user",
        "bucket_id": "category:CODE:user123"
    }
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not cognitive_memory:
        return JSONResponse({"success": False, "error": "CognitiveMemory not available"}, status_code=503)
    
    user_id = str(user["_id"])
    
    try:
        body = await request.json()
        reflection = body.get("reflection")
        trigger = body.get("trigger", "manual")
        confidence = body.get("confidence", 0.7)
        scope = body.get("scope", "user")
        bucket_id = body.get("bucket_id")
        bucket_type = body.get("bucket_type")
        group_id = body.get("group_id")
        
        if not reflection:
            return JSONResponse({"error": "reflection is required"}, status_code=400)
        
        result = await cognitive_memory.store_reflection(
            reflection=reflection,
            trigger=trigger,
            confidence=confidence,
            scope=scope,
            user_id=user_id if scope == "user" else None,
            group_id=group_id,
            bucket_id=bucket_id,
            bucket_type=bucket_type,
        )
        
        return JSONResponse({
            "success": True,
            "reflection": result,
        })
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to store reflection: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/memories/reflections", response_class=JSONResponse)
async def get_reflections_perfect_brain(
    request: Request,
    scope: str = "user",
    bucket_id: str | None = None,
    category: str | None = None,
    min_confidence: float = 0.5,
    limit: int = 10,
):
    """
    Get meta-cognitive reflections with bucket filtering.
    
    Note: This is different from /api/reflections which returns consolidated summaries.
    This endpoint returns ReflectiveMemory (meta-cognitive insights).
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not cognitive_memory:
        return JSONResponse({"success": False, "error": "CognitiveMemory not available"}, status_code=503)
    
    user_id = str(user["_id"])
    
    try:
        # Construct bucket_id from category if provided
        if category and not bucket_id:
            bucket_id = f"category:{category}:{user_id}"
        
        results = await cognitive_memory.get_reflections(
            scope=scope,
            user_id=user_id if scope == "user" else None,
            min_confidence=min_confidence,
            limit=limit,
        )
        
        return JSONResponse({
            "success": True,
            "reflections": results,
            "count": len(results),
        })
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to get reflections: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/memories/predictions", response_class=JSONResponse)
async def store_prediction(
    request: Request,
    svc=Depends(get_memory_service),
):
    """
    Store a prediction or counterfactual scenario.
    
    Request body:
    {
        "scenario": "If we switch to TypeScript, we'll reduce bugs by 30%",
        "origin": "pattern_analysis",
        "confidence": 0.7,
        "scope": "shared",
        "group_id": "team-001",
        "bucket_id": "category:CODE:team-001"
    }
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not cognitive_memory:
        return JSONResponse({"success": False, "error": "CognitiveMemory not available"}, status_code=503)
    
    user_id = str(user["_id"])
    
    try:
        body = await request.json()
        scenario = body.get("scenario")
        origin = body.get("origin", "manual")
        confidence = body.get("confidence", 0.7)
        validated = body.get("validated", False)
        scope = body.get("scope", "user")
        bucket_id = body.get("bucket_id")
        bucket_type = body.get("bucket_type")
        group_id = body.get("group_id")
        
        if not scenario:
            return JSONResponse({"error": "scenario is required"}, status_code=400)
        
        result = await cognitive_memory.store_prediction(
            scenario=scenario,
            origin=origin,
            confidence=confidence,
            validated=validated,
            scope=scope,
            user_id=user_id if scope == "user" else None,
            group_id=group_id,
            bucket_id=bucket_id,
            bucket_type=bucket_type,
        )
        
        return JSONResponse({
            "success": True,
            "prediction": result,
        })
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to store prediction: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/memories/predictions/{prediction_id}/validate", response_class=JSONResponse)
async def validate_prediction(
    request: Request,
    prediction_id: str,
):
    """
    Validate a prediction when outcome is known.
    
    Request body:
    {
        "actual_outcome": "Bug rate reduced by 25%",
        "was_correct": true
    }
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not predictive_memory:
        return JSONResponse({"success": False, "error": "PredictiveMemory not available"}, status_code=503)
    
    try:
        body = await request.json()
        actual_outcome = body.get("actual_outcome")
        was_correct = body.get("was_correct", False)
        
        if actual_outcome is None:
            return JSONResponse({"error": "actual_outcome is required"}, status_code=400)
        
        result = await asyncio.to_thread(
            predictive_memory.validate_prediction,
            prediction_id=prediction_id,
            actual_outcome=actual_outcome,
            was_correct=was_correct,
        )
        
        return JSONResponse({
            "success": True,
            "prediction": result,
        })
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to validate prediction: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/memories/predictions", response_class=JSONResponse)
async def get_predictions(
    request: Request,
    scope: str = "user",
    bucket_id: str | None = None,
    category: str | None = None,
    validated: bool | None = None,
    min_confidence: float = 0.5,
    limit: int = 10,
):
    """
    Get predictions with bucket filtering.
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not cognitive_memory:
        return JSONResponse({"success": False, "error": "CognitiveMemory not available"}, status_code=503)
    
    user_id = str(user["_id"])
    
    try:
        # Construct bucket_id from category if provided
        if category and not bucket_id:
            bucket_id = f"category:{category}:{user_id}"
        
        results = await cognitive_memory.get_predictions(
            scope=scope,
            user_id=user_id if scope == "user" else None,
            validated=validated,
            min_confidence=min_confidence,
            limit=limit,
        )
        
        return JSONResponse({
            "success": True,
            "predictions": results,
            "count": len(results),
        })
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to get predictions: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/memories/vetoes", response_class=JSONResponse)
async def add_memory_veto(
    request: Request,
):
    """
    Add a memory veto to prevent sharing.
    
    Request body:
    {
        "memory_id": "mem123",
        "scope": "shared"
    }
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not memory_veto:
        return JSONResponse({"success": False, "error": "MemoryVeto not available"}, status_code=503)
    
    user_id = str(user["_id"])
    
    try:
        body = await request.json()
        memory_id = body.get("memory_id")
        scope = body.get("scope", "shared")
        
        if not memory_id:
            return JSONResponse({"error": "memory_id is required"}, status_code=400)
        
        result = await asyncio.to_thread(
            memory_veto.add_veto,
            memory_id=memory_id,
            user_id=user_id,
            scope=scope,
        )
        
        return JSONResponse({
            "success": True,
            "veto": result,
        })
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to add veto: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.delete("/api/memories/vetoes/{memory_id}", response_class=JSONResponse)
async def remove_memory_veto(
    request: Request,
    memory_id: str,
):
    """
    Remove a memory veto.
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not memory_veto:
        return JSONResponse({"success": False, "error": "MemoryVeto not available"}, status_code=503)
    
    user_id = str(user["_id"])
    
    try:
        body = await request.json() if await request.body() else {}
        scope = body.get("scope", "shared")
        
        await asyncio.to_thread(
            memory_veto.remove_veto,
            memory_id=memory_id,
            user_id=user_id,
            scope=scope,
        )
        
        return JSONResponse({
            "success": True,
            "message": "Veto removed",
        })
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to remove veto: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/memories/vetoes", response_class=JSONResponse)
async def get_memory_vetoes(
    request: Request,
):
    """
    Get user's memory vetoes.
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not memory_veto:
        return JSONResponse({"success": False, "error": "MemoryVeto not available"}, status_code=503)
    
    user_id = str(user["_id"])
    
    try:
        vetoes = await asyncio.to_thread(
            memory_veto.get_user_vetoes,
            user_id=user_id,
        )
        
        return JSONResponse({
            "success": True,
            "vetoes": vetoes,
            "count": len(vetoes),
        })
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to get vetoes: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# =============================================================================
# Prospective Memory API - "Remember to do X when Y happens"
# =============================================================================


@app.post("/api/prospective/triggers", response_class=JSONResponse)
async def set_prospective_trigger(request: Request):
    """
    Set a prospective memory trigger ("remember to do X when Y happens").
    
    This is an AI superpower -- intention-based memory that fires when context matches.
    The system stores a condition embedding and checks every incoming query against it.
    When the condition matches, the action is surfaced as a reminder in the AI's response.
    
    Body:
    - condition: When to trigger (e.g., "user mentions project deadline")
    - action: What to do (e.g., "Remind about the pending risk assessment")
    - one_shot: If true, trigger fires once and deactivates (default: true)
    - metadata: Optional metadata dict
    
    Example:
    ```bash
    POST /api/prospective/triggers
    {
      "condition": "user asks about pricing or costs",
      "action": "Suggest the enterprise plan with volume discounts",
      "one_shot": false
    }
    ```
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not prospective_memory:
        return JSONResponse({"success": False, "error": "ProspectiveMemory not available"}, status_code=503)
    
    try:
        body = await request.json()
        condition = body.get("condition", "")
        action = body.get("action", "")
        one_shot = body.get("one_shot", True)
        metadata = body.get("metadata")
        
        if not condition or not action:
            return JSONResponse(
                {"error": "Both 'condition' and 'action' are required"}, status_code=400
            )
        
        user_id = str(user["_id"])
        trigger_id = await prospective_memory.set_trigger(
            condition=condition,
            action=action,
            user_id=user_id,
            one_shot=one_shot,
            metadata=metadata,
        )
        
        return JSONResponse({
            "success": True,
            "trigger_id": trigger_id,
            "condition": condition,
            "action": action,
            "one_shot": one_shot,
        })
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to set prospective trigger: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/prospective/triggers", response_class=JSONResponse)
async def list_prospective_triggers(request: Request):
    """
    List all active (unfired) prospective memory triggers for the current user.
    
    Returns triggers that are waiting to fire. Each trigger has a condition
    (when to fire) and an action (what to remind about).
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not prospective_memory:
        return JSONResponse({"success": True, "triggers": []})
    
    try:
        user_id = str(user["_id"])
        triggers = await prospective_memory.get_active_triggers(user_id=user_id)
        return JSONResponse({"success": True, "triggers": triggers, "count": len(triggers)})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to list prospective triggers: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.delete("/api/prospective/triggers/{trigger_id}", response_class=JSONResponse)
async def deactivate_prospective_trigger(request: Request, trigger_id: str):
    """
    Manually deactivate a prospective memory trigger.
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not prospective_memory:
        return JSONResponse({"success": False, "error": "ProspectiveMemory not available"}, status_code=503)
    
    try:
        result = await prospective_memory.deactivate_trigger(trigger_id)
        return JSONResponse({"success": result, "trigger_id": trigger_id})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to deactivate trigger: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/memories/consolidate", response_class=JSONResponse)
async def consolidate_memories(
    request: Request,
):
    """
    Trigger manual memory consolidation (episodic → semantic).
    Consolidates unprocessed episodic memories into semantic facts and procedural lessons.
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not memory_consolidator:
        return JSONResponse({"success": False, "error": "MemoryConsolidator not available"}, status_code=503)
    
    try:
        body = await request.json() if await request.body() else {}
        user_id = str(user["_id"])
        limit = body.get("limit", 10)
        force = body.get("force", False)
        
        result = await asyncio.to_thread(
            memory_consolidator.consolidate_episodes,
            agent_id=user_id,
            limit=limit,
            force=force,
        )
        
        return JSONResponse({
            "success": True,
            "result": result,
            "entities_extracted": result.get("entities_extracted", 0),
            "procedures_created": result.get("procedures_created", 0),
            "episodes_processed": result.get("episodes_processed", 0),
        })
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to consolidate memories: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/memories/episodic", response_class=JSONResponse)
async def record_episode(
    request: Request,
):
    """
    Record an episodic memory (raw chronological interaction).
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not cognitive_memory:
        return JSONResponse({"success": False, "error": "CognitiveMemory not available"}, status_code=503)
    
    try:
        body = await request.json()
        session_id = body.get("session_id")
        role = body.get("role", "user")
        content = body.get("content")
        bucket_id = body.get("bucket_id")
        
        if not session_id or not content:
            return JSONResponse({"success": False, "error": "session_id and content required"}, status_code=400)
        
        user_id = str(user["_id"])
        result = await cognitive_memory.record_episode(
            session_id=session_id,
            role=role,
            content=content,
            scope="user",
            user_id=user_id,
            bucket_id=bucket_id,
        )
        
        return JSONResponse({"success": True, "episode": result})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to record episode: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/memories/episodic", response_class=JSONResponse)
async def get_episodic_memories(
    request: Request,
    session_id: Optional[str] = None,
    consolidated: Optional[bool] = None,
    bucket_id: Optional[str] = None,
    limit: int = 50,
):
    """
    Query episodic memories with filters.
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not cognitive_memory:
        return JSONResponse({"success": False, "error": "CognitiveMemory not available"}, status_code=503)
    
    try:
        user_id = str(user["_id"])
        motor_client = engine._connection_manager.mongo_client
        motor_db = motor_client[engine.db_name]
        episodic_collection = motor_db["episodic"]
        
        query = {"user_id": user_id}
        if session_id:
            query["session_id"] = session_id
        if consolidated is not None:
            query["consolidated"] = consolidated
        if bucket_id:
            query["bucket_id"] = bucket_id
        
        episodes = await episodic_collection.find(query).sort("timestamp", -1).to_list(length=limit)
        
        # Serialize ObjectId and datetime
        for ep in episodes:
            if "_id" in ep:
                ep["_id"] = str(ep["_id"])
            if "timestamp" in ep:
                ep["timestamp"] = ep["timestamp"].isoformat() if hasattr(ep["timestamp"], "isoformat") else str(ep["timestamp"])
        
        return JSONResponse({"success": True, "episodes": episodes, "count": len(episodes)})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to get episodic memories: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/memories/episodic/{episode_id}", response_class=JSONResponse)
async def get_episode(
    request: Request,
    episode_id: str,
):
    """
    Get a specific episode by ID.
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    
    try:
        motor_client = engine._connection_manager.mongo_client
        motor_db = motor_client[engine.db_name]
        episodic_collection = motor_db["episodic"]
        
        from bson import ObjectId
        episode = await episodic_collection.find_one({"_id": ObjectId(episode_id), "user_id": str(user["_id"])})
        
        if not episode:
            return JSONResponse({"success": False, "error": "Episode not found"}, status_code=404)
        
        if "_id" in episode:
            episode["_id"] = str(episode["_id"])
        if "timestamp" in episode:
            episode["timestamp"] = episode["timestamp"].isoformat() if hasattr(episode["timestamp"], "isoformat") else str(episode["timestamp"])
        
        return JSONResponse({"success": True, "episode": episode})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to get episode: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/memories/procedural", response_class=JSONResponse)
async def store_procedural_memory(
    request: Request,
):
    """
    Store procedural knowledge (skills, workflows, executable procedures).
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not cognitive_memory:
        return JSONResponse({"success": False, "error": "CognitiveMemory not available"}, status_code=503)
    
    try:
        body = await request.json()
        task_type = body.get("task_type")
        procedure = body.get("procedure")
        success_rate = body.get("success_rate", 0.5)
        bucket_id = body.get("bucket_id")
        
        if not task_type or not procedure:
            return JSONResponse({"success": False, "error": "task_type and procedure required"}, status_code=400)
        
        user_id = str(user["_id"])
        motor_client = engine._connection_manager.mongo_client
        motor_db = motor_client[engine.db_name]
        procedural_collection = motor_db["procedural"]
        
        doc = {
            "user_id": user_id,
            "task_type": task_type,
            "procedure": procedure,
            "success_rate": success_rate,
            "bucket_id": bucket_id,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        }
        
        result = await procedural_collection.insert_one(doc)
        doc["_id"] = str(result.inserted_id)
        
        return JSONResponse({"success": True, "procedure": doc})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to store procedural memory: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/memories/procedural", response_class=JSONResponse)
async def get_procedural_memories(
    request: Request,
    task_type: Optional[str] = None,
    bucket_id: Optional[str] = None,
    limit: int = 50,
):
    """
    Query procedural memories.
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    
    try:
        user_id = str(user["_id"])
        motor_client = engine._connection_manager.mongo_client
        motor_db = motor_client[engine.db_name]
        procedural_collection = motor_db["procedural"]
        
        query = {"user_id": user_id}
        if task_type:
            query["task_type"] = task_type
        if bucket_id:
            query["bucket_id"] = bucket_id
        
        procedures = await procedural_collection.find(query).sort("success_rate", -1).to_list(length=limit)
        
        # Serialize ObjectId and datetime
        for proc in procedures:
            if "_id" in proc:
                proc["_id"] = str(proc["_id"])
            if "created_at" in proc:
                proc["created_at"] = proc["created_at"].isoformat() if hasattr(proc["created_at"], "isoformat") else str(proc["created_at"])
            if "updated_at" in proc:
                proc["updated_at"] = proc["updated_at"].isoformat() if hasattr(proc["updated_at"], "isoformat") else str(proc["updated_at"])
        
        return JSONResponse({"success": True, "procedures": procedures, "count": len(procedures)})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to get procedural memories: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/memories/procedural/{procedure_id}", response_class=JSONResponse)
async def get_procedure(
    request: Request,
    procedure_id: str,
):
    """
    Get a specific procedure by ID.
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    
    try:
        motor_client = engine._connection_manager.mongo_client
        motor_db = motor_client[engine.db_name]
        procedural_collection = motor_db["procedural"]
        
        from bson import ObjectId
        procedure = await procedural_collection.find_one({"_id": ObjectId(procedure_id), "user_id": str(user["_id"])})
        
        if not procedure:
            return JSONResponse({"success": False, "error": "Procedure not found"}, status_code=404)
        
        if "_id" in procedure:
            procedure["_id"] = str(procedure["_id"])
        if "created_at" in procedure:
            procedure["created_at"] = procedure["created_at"].isoformat() if hasattr(procedure["created_at"], "isoformat") else str(procedure["created_at"])
        if "updated_at" in procedure:
            procedure["updated_at"] = procedure["updated_at"].isoformat() if hasattr(procedure["updated_at"], "isoformat") else str(procedure["updated_at"])
        
        return JSONResponse({"success": True, "procedure": procedure})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to get procedure: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.put("/api/memories/procedural/{procedure_id}", response_class=JSONResponse)
async def update_procedure(
    request: Request,
    procedure_id: str,
):
    """
    Update a procedure (e.g., success rate, procedure steps).
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    
    try:
        body = await request.json()
        motor_client = engine._connection_manager.mongo_client
        motor_db = motor_client[engine.db_name]
        procedural_collection = motor_db["procedural"]
        
        from bson import ObjectId
        update_data = {"updated_at": datetime.utcnow()}
        if "success_rate" in body:
            update_data["success_rate"] = body["success_rate"]
        if "procedure" in body:
            update_data["procedure"] = body["procedure"]
        if "task_type" in body:
            update_data["task_type"] = body["task_type"]
        
        result = await procedural_collection.update_one(
            {"_id": ObjectId(procedure_id), "user_id": str(user["_id"])},
            {"$set": update_data}
        )
        
        if result.matched_count == 0:
            return JSONResponse({"success": False, "error": "Procedure not found"}, status_code=404)
        
        return JSONResponse({"success": True, "updated": result.modified_count > 0})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to update procedure: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/memories/working/context", response_class=JSONResponse)
async def set_working_context(
    request: Request,
):
    """
    Set working context for a session (short-term active context).
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not cognitive_memory:
        return JSONResponse({"success": False, "error": "CognitiveMemory not available"}, status_code=503)
    
    try:
        body = await request.json()
        session_id = body.get("session_id")
        context = body.get("context", {})
        
        if not session_id:
            return JSONResponse({"success": False, "error": "session_id required"}, status_code=400)
        
        result = await cognitive_memory.set_working_context(
            session_id=session_id,
            data=context,
        )
        
        return JSONResponse({"success": True, "context": result})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to set working context: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/memories/working/context", response_class=JSONResponse)
async def get_working_context(
    request: Request,
    session_id: str,
):
    """
    Get working context for a session.
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not cognitive_memory:
        return JSONResponse({"success": False, "error": "CognitiveMemory not available"}, status_code=503)
    
    try:
        context = await cognitive_memory.get_working_context(
            session_id=session_id,
        )
        
        return JSONResponse({"success": True, "context": context})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to get working context: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.delete("/api/memories/working/context", response_class=JSONResponse)
async def clear_working_context(
    request: Request,
    session_id: str,
):
    """
    Clear working context for a session.
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not cognitive_memory:
        return JSONResponse({"success": False, "error": "CognitiveMemory not available"}, status_code=503)
    
    try:
        result = await cognitive_memory.set_working_context(
            session_id=session_id,
            data={},
        )
        
        return JSONResponse({"success": True, "cleared": True})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to clear working context: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/memories/semantic/entity", response_class=JSONResponse)
async def update_semantic_entity(
    request: Request,
):
    """
    Update or create a semantic entity (structured facts).
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not cognitive_memory:
        return JSONResponse({"success": False, "error": "CognitiveMemory not available"}, status_code=503)
    
    try:
        body = await request.json()
        entity_name = body.get("entity_name")
        attributes = body.get("attributes", {})
        confidence = body.get("confidence", 0.8)
        bucket_id = body.get("bucket_id")
        
        if not entity_name:
            return JSONResponse({"success": False, "error": "entity_name required"}, status_code=400)
        
        user_id = str(user["_id"])
        result = await cognitive_memory.update_entity(
            entity_name=entity_name,
            attributes=attributes,
            confidence=confidence,
            scope="user",
            user_id=user_id,
            bucket_id=bucket_id,
        )
        
        return JSONResponse({"success": True, "entity": result})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to update semantic entity: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/memories/semantic/entity", response_class=JSONResponse)
async def search_semantic_entities(
    request: Request,
    query: Optional[str] = None,
    bucket_id: Optional[str] = None,
    limit: int = 20,
):
    """
    Search semantic entities.
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not cognitive_memory:
        return JSONResponse({"success": False, "error": "CognitiveMemory not available"}, status_code=503)
    
    try:
        user_id = str(user["_id"])
        if query:
            entities = await cognitive_memory.search_entities(
                query=query,
                scope="user",
                user_id=user_id,
                bucket_id=bucket_id,
            )
        else:
            # Get all entities for user
            motor_client = engine._connection_manager.mongo_client
            motor_db = motor_client[engine.db_name]
            entity_collection = motor_db["entity_memory"]
            
            query_filter = {"user_id": user_id, "scope": "user"}
            if bucket_id:
                query_filter["bucket_id"] = bucket_id
            
            entities = await entity_collection.find(query_filter).to_list(length=limit)
            for ent in entities:
                if "_id" in ent:
                    ent["_id"] = str(ent["_id"])
        
        return JSONResponse({"success": True, "entities": entities, "count": len(entities) if isinstance(entities, list) else 1})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to search semantic entities: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/memories/semantic/entity/{entity_name}", response_class=JSONResponse)
async def get_semantic_entity(
    request: Request,
    entity_name: str,
    bucket_id: Optional[str] = None,
):
    """
    Get a specific semantic entity by name.
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    
    try:
        user_id = str(user["_id"])
        motor_client = engine._connection_manager.mongo_client
        motor_db = motor_client[engine.db_name]
        entity_collection = motor_db["entity_memory"]
        
        query = {"user_id": user_id, "entity": entity_name, "scope": "user"}
        if bucket_id:
            query["bucket_id"] = bucket_id
        
        entity = await entity_collection.find_one(query)
        
        if not entity:
            return JSONResponse({"success": False, "error": "Entity not found"}, status_code=404)
        
        if "_id" in entity:
            entity["_id"] = str(entity["_id"])
        
        return JSONResponse({"success": True, "entity": entity})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to get semantic entity: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/memories/reflection/run", response_class=JSONResponse)
async def run_reflection(
    request: Request,
):
    """
    Trigger reflection/consolidation using ReflectionService.
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not reflection_service:
        return JSONResponse({"success": False, "error": "ReflectionService not available"}, status_code=503)
    
    try:
        user_id = str(user["_id"])
        result = await reflection_service.reflect(user_id=user_id)
        
        return JSONResponse({
            "success": True,
            "result": result,
            "memories_consolidated": result.get("memories_consolidated", 0),
            "reflections_created": result.get("reflections_created", 0),
        })
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to run reflection: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/memories/{entity_name}/history", response_class=JSONResponse)
async def get_memory_history(
    request: Request,
    entity_name: str,
):
    """
    Get version history for an entity (belief evolution over time).
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not memory_versioning:
        return JSONResponse({"success": False, "error": "MemoryVersioning not available"}, status_code=503)
    
    user_id = str(user["_id"])
    
    try:
        history = await asyncio.to_thread(
            memory_versioning.get_version_history,
            entity_name=entity_name,
            user_id=user_id,
        )
        
        return JSONResponse({
            "success": True,
            "entity_name": entity_name,
            "history": history,
            "versions": len(history),
        })
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to get memory history: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ============================================================================
# GRAPH LINKS ENDPOINTS (Cognitive OS)
# ============================================================================


@app.post("/api/memories/{memory_id}/contradict", response_class=JSONResponse)
async def mark_contradiction_endpoint(
    request: Request,
    memory_id: str,
    svc=Depends(get_memory_service),
):
    """
    Mark a memory as contradicting another (Bayesian update).
    
    When new information contradicts existing memory:
    1. Old memory is marked as deprecated with low confidence (0.1)
    2. Bidirectional graph links are created between the memories
    3. Both memories are preserved for audit trail
    
    Body:
    - contradicted_memory_id: ID of the old memory being contradicted (required)
    
    Returns:
    - success: True if contradiction was marked successfully
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not svc:
        return JSONResponse({"success": False, "error": "Memory service not available"}, status_code=503)
    
    user_id = str(user["_id"])
    
    try:
        body = await request.json()
        contradicted_memory_id = body.get("contradicted_memory_id")
        
        if not contradicted_memory_id:
            return JSONResponse({"error": "contradicted_memory_id is required"}, status_code=400)
        
        await svc.mark_contradiction(memory_id, contradicted_memory_id, user_id)
        return JSONResponse({"success": True})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to mark contradiction: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/memories/with-links", response_class=JSONResponse)
async def add_memory_with_links_endpoint(
    request: Request,
    svc=Depends(get_memory_service),
):
    """
    Create a memory with explicit graph links.
    
    Allows creating memories with explicit relationships to other memories:
    - derived_from: Memory IDs this memory is derived from (semantic distillation)
    - contradicts: Memory IDs this memory contradicts
    - timeline_id: Timeline ID (default: "root")
    - confidence: Confidence score (default: 0.8)
    
    Body:
    - content: Memory content (required)
    - derived_from: List of memory IDs (optional)
    - contradicts: List of memory IDs (optional)
    - timeline_id: Timeline ID (optional, default: "root")
    - confidence: Confidence score 0.0-1.0 (optional, default: 0.8)
    - metadata: Additional metadata dictionary (optional)
    - bucket_id: Bucket ID for filtering (optional)
    - bucket_type: Bucket type (optional)
    
    Returns:
    - success: True if memory was created
    - memory: Created memory document
    """
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Auth required"}, status_code=401)
    if not svc:
        return JSONResponse({"success": False, "error": "Memory service not available"}, status_code=503)
    
    user_id = str(user["_id"])
    
    try:
        body = await request.json()
        content = body.get("content")
        
        if not content:
            return JSONResponse({"error": "content is required"}, status_code=400)
        
        memory = await svc.add_memory_with_links(
            content=content,
            user_id=user_id,
            derived_from=body.get("derived_from", []),
            contradicts=body.get("contradicts", []),
            timeline_id=body.get("timeline_id", "root"),
            confidence=body.get("confidence", 0.8),
            metadata=body.get("metadata"),
            bucket_id=body.get("bucket_id"),
            bucket_type=body.get("bucket_type"),
        )
        return JSONResponse({"success": True, "memory": memory})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to create memory with links: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ============================================================================
# REFLECTION ENDPOINTS
# ============================================================================


@app.get("/api/reflections", response_class=JSONResponse)
async def get_reflections(
    request: Request,
    limit: int = 20,
    svc=Depends(get_memory_service),
):
    """
    Get memory reflections (consolidated summaries) for the current user.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    if not svc:
        return JSONResponse(
            {"success": False, "error": "Memory service not available"},
            status_code=503,
        )

    user_id = str(user["_id"])
    
    try:
        reflection_service = getattr(svc, 'reflection_service', None)
        if not reflection_service:
            return JSONResponse({
                "success": True,
                "enabled": False,
                "reflections": [],
                "message": "Reflection service not enabled for this app",
            })
        
        reflections_col = getattr(reflection_service, 'reflections_collection', None)
        if not reflections_col:
            return JSONResponse({
                "success": True,
                "enabled": True,
                "reflections": [],
            })
        
        reflections = list(
            reflections_col.find(
                {"user_id": user_id},
                sort=[("created_at", -1)],
                limit=limit,
            )
        )
        
        for r in reflections:
            r["_id"] = str(r["_id"])
        
        return JSONResponse({
            "success": True,
            "enabled": True,
            "reflections": reflections,
            "count": len(reflections),
        })
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to get reflections: {e}", exc_info=True)
        return JSONResponse({
            "success": False,
            "error": str(e),
        }, status_code=500)


# ============================================================================
# GRAPH ENDPOINTS (Knowledge Graph / GraphRAG)
# ============================================================================

def get_graph_service_from_request(request: Request, svc=None):
    """
    Helper to get graph service from memory service or engine.
    Checks memory service's _graph_service attribute first, then falls back to engine, then manifest config.
    """
    app = request.app
    
    # Try to get graph service from memory service first (injected via dependency injection)
    if svc and hasattr(svc, "_graph_service") and svc._graph_service:
        return svc._graph_service
    
    # Check if service is already cached in app state
    if hasattr(app.state, "graph_service") and app.state.graph_service is not None:
        return app.state.graph_service
    
    # Try to get from engine (if using MDB-Engine)
    engine = getattr(app.state, "engine", None)
    if engine:
        # Try to get slug from app state first, then fall back to APP_SLUG
        slug = getattr(app.state, "app_slug", None) or getattr(app.state, "slug", None) or APP_SLUG
        service = engine.get_graph_service(slug)
        if service:
            app.state.graph_service = service
            return service
    
    # Fallback: create from manifest config if available
    if hasattr(app.state, "manifest") and app.state.manifest is not None:
        manifest = app.state.manifest
        graph_config = manifest.get("graph_config", {})
        
        if graph_config.get("enabled", True):
            # Get required dependencies
            llm_service = getattr(app.state, "llm_service", None)
            embedding_service = getattr(app.state, "embedding_service", None)
            
            # Get collection (requires engine)
            if engine:
                slug = manifest.get("slug", APP_SLUG)
                # Match dependency logic: default to "kg" if not specified
                base_collection_name = graph_config.get("collection_name", "kg")
                # Normalize legacy "__kg" to "kg" (private attributes are blocked by ScopedMongoWrapper)
                if base_collection_name == "__kg":
                    base_collection_name = "kg"
                # Prefix with slug if not already prefixed
                if base_collection_name.startswith(f"{slug}_"):
                    collection_name = base_collection_name
                else:
                    collection_name = f"{slug}_{base_collection_name}"
                
                try:
                    # Use Motor async collections directly - forward-facing DI
                    motor_client = engine._connection_manager.mongo_client  # noqa: SLF001
                    motor_db = motor_client[engine.db_name]
                    collection = motor_db[collection_name]  # Motor AsyncIOMotorCollection
                    
                    service = get_graph_service_factory(
                        app_slug=slug,
                        collection=collection,
                        config=graph_config,
                        llm_service=llm_service,
                        embedding_service=embedding_service,
                    )
                    app.state.graph_service = service
                    return service
                except (AttributeError, RuntimeError, KeyError) as e:
                    logger.warning(f"Failed to create GraphService from manifest: {e}")
    
    return None


@app.get("/api/graph/stats", response_class=JSONResponse)
async def get_graph_stats(
    request: Request,
    svc=Depends(get_memory_service),
):
    """Get graph store statistics"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    graph_service = get_graph_service_from_request(request, svc)
    if not graph_service:
        return JSONResponse(
            {"success": True, "enabled": False, "total_nodes": 0, "total_edges": 0}
        )

    stats = await graph_service.get_stats()
    return JSONResponse({"success": True, "enabled": True, **stats})


@app.get("/api/graph/search", response_class=JSONResponse)
async def graph_hybrid_search(
    request: Request,
    query: str,
    max_depth: int = 2,
    limit: int = 10,
    svc=Depends(get_memory_service),
):
    """
    GraphRAG search endpoint using automatic query classification.
    Routes queries to appropriate search method (local/global/drift/hybrid) based on query type.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    graph_service = get_graph_service_from_request(request, svc)
    
    if not graph_service:
        return JSONResponse(
            {"success": True, "query_type": "none", "entry_nodes": [], "graph_context": [], "total_nodes": 0}
        )

    user_id = str(user["_id"])
    
    # Use GraphRAG query classification to determine search method
    query_type = graph_service.classify_query(query)
    logger.info(f"🔍 [GraphRAG API] Query classified as: {query_type}")
    
    # Route to appropriate GraphRAG search method
    if query_type == "local":
        results = await graph_service.local_search(
            query=query,
            user_id=user_id,
            max_depth=max_depth,
        )
    elif query_type == "global":
        results = await graph_service.global_search(
            query=query,
            user_id=user_id,
            max_communities=limit,
        )
    elif query_type == "drift":
        results = await graph_service.drift_search(
            query=query,
            user_id=user_id,
            max_depth=max_depth,
        )
    else:
        # Fallback to hybrid search
        results = await graph_service.hybrid_search(
            query=query,
            user_id=user_id,
            max_depth=max_depth,
            vector_limit=limit
        )
    
    # Ensure query_type is included in results
    if results and "query_type" not in results:
        results["query_type"] = query_type
    
    # Serialize datetime objects for JSON response
    serialized_results = serialize_for_json(results)
    return JSONResponse({"success": True, **serialized_results})


@app.get("/api/graph/traverse", response_class=JSONResponse)
async def graph_traverse(
    request: Request,
    node_id: str,
    max_depth: int = 2,
    svc=Depends(get_memory_service),
):
    """Traverse the graph from a specific node"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    graph_service = get_graph_service_from_request(request, svc)
    
    if not graph_service:
        return JSONResponse({"success": True, "nodes": []})

    results = await graph_service.traverse(
        start_id=node_id,
        max_depth=max_depth
    )
    
    # Serialize datetime objects for JSON response
    serialized_results = serialize_for_json(results)
    return JSONResponse({"success": True, "nodes": serialized_results})


@app.get("/api/graph/nodes", response_class=JSONResponse)
async def list_graph_nodes(
    request: Request,
    node_type: Optional[str] = None,
    limit: int = 50,
    svc=Depends(get_memory_service),
):
    """List graph nodes for the user"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    graph_service = get_graph_service_from_request(request, svc)
    
    if not graph_service:
        return JSONResponse({"success": True, "nodes": []})

    user_id = str(user["_id"])
    
    nodes = await graph_service.list_nodes(
        node_type=node_type,
        user_id=user_id,
        limit=limit
    )
    
    # Serialize datetime objects for JSON response
    serialized_nodes = serialize_for_json(nodes)
    return JSONResponse({"success": True, "nodes": serialized_nodes})


@app.get("/api/graph/nodes/{node_id}", response_class=JSONResponse)
async def get_graph_node(
    request: Request,
    node_id: str,
    svc=Depends(get_memory_service),
):
    """Get a specific graph node by ID"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    graph_service = get_graph_service_from_request(request, svc)
    
    if not graph_service:
        return JSONResponse({"success": False, "error": "Graph service not available"}, status_code=503)

    node = await asyncio.to_thread(graph_service.get_node, node_id=node_id)
    
    if not node:
        return JSONResponse({"success": False, "error": "Node not found"}, status_code=404)
    
    serialized_node = serialize_for_json(node)
    return JSONResponse({"success": True, "node": serialized_node})


@app.post("/api/graph/nodes", response_class=JSONResponse)
async def create_graph_node(
    request: Request,
    svc=Depends(get_memory_service),
):
    """Create or update a graph node"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    graph_service = get_graph_service_from_request(request, svc)
    
    if not graph_service:
        return JSONResponse({"success": False, "error": "Graph service not available"}, status_code=503)

    try:
        body = await request.json()
        node_id = body.get("node_id")
        node_type = body.get("node_type")
        name = body.get("name")
        properties = body.get("properties", {})
        embedding = body.get("embedding")  # Optional
        
        if not node_id or not node_type or not name:
            return JSONResponse({"success": False, "error": "node_id, node_type, and name are required"}, status_code=400)
        
        user_id = str(user["_id"])
        
        node = await asyncio.to_thread(
            graph_service.upsert_node,
            node_id=node_id,
            node_type=node_type,
            name=name,
            properties=properties,
            user_id=user_id,
            embedding=embedding,
        )
        
        serialized_node = serialize_for_json(node)
        return JSONResponse({"success": True, "node": serialized_node})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to create graph node: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.delete("/api/graph/nodes/{node_id}", response_class=JSONResponse)
async def delete_graph_node(
    request: Request,
    node_id: str,
    svc=Depends(get_memory_service),
):
    """Delete a graph node and all its edges"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    graph_service = get_graph_service_from_request(request, svc)
    
    if not graph_service:
        return JSONResponse({"success": False, "error": "Graph service not available"}, status_code=503)

    try:
        deleted = await asyncio.to_thread(graph_service.delete_node, node_id=node_id)
        return JSONResponse({"success": True, "deleted": deleted})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to delete graph node: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/graph/edges", response_class=JSONResponse)
async def create_graph_edge(
    request: Request,
    svc=Depends(get_memory_service),
):
    """Create an edge between two nodes"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    graph_service = get_graph_service_from_request(request, svc)
    
    if not graph_service:
        return JSONResponse({"success": False, "error": "Graph service not available"}, status_code=503)

    try:
        body = await request.json()
        source_id = body.get("source_id")
        relation = body.get("relation")
        target_id = body.get("target_id")
        properties = body.get("properties", {})
        weight = body.get("weight", 1.0)
        
        if not source_id or not relation or not target_id:
            return JSONResponse({"success": False, "error": "source_id, relation, and target_id are required"}, status_code=400)
        
        edge = await asyncio.to_thread(
            graph_service.add_edge,
            source_id=source_id,
            relation=relation,
            target_id=target_id,
            properties=properties,
            weight=weight,
        )
        
        return JSONResponse({"success": True, "edge": edge})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to create graph edge: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.delete("/api/graph/edges", response_class=JSONResponse)
async def delete_graph_edge(
    request: Request,
    svc=Depends(get_memory_service),
):
    """Remove an edge between two nodes"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    graph_service = get_graph_service_from_request(request, svc)
    
    if not graph_service:
        return JSONResponse({"success": False, "error": "Graph service not available"}, status_code=503)

    try:
        body = await request.json()
        source_id = body.get("source_id")
        relation = body.get("relation")
        target_id = body.get("target_id")
        
        if not source_id or not relation or not target_id:
            return JSONResponse({"success": False, "error": "source_id, relation, and target_id are required"}, status_code=400)
        
        removed = await asyncio.to_thread(
            graph_service.remove_edge,
            source_id=source_id,
            relation=relation,
            target_id=target_id,
        )
        
        return JSONResponse({"success": True, "removed": removed})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to delete graph edge: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/graph/path", response_class=JSONResponse)
async def find_graph_path(
    request: Request,
    source_id: str,
    target_id: str,
    max_depth: int = 5,
    svc=Depends(get_memory_service),
):
    """Find a path between two nodes in the graph"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    graph_service = get_graph_service_from_request(request, svc)
    
    if not graph_service:
        return JSONResponse({"success": False, "error": "Graph service not available"}, status_code=503)

    try:
        path = await asyncio.to_thread(
            graph_service.find_path,
            source_id=source_id,
            target_id=target_id,
            max_depth=max_depth,
        )
        
        serialized_path = serialize_for_json(path)
        return JSONResponse({"success": True, "path": serialized_path})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to find graph path: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/graph/neighbors", response_class=JSONResponse)
async def get_graph_neighbors(
    request: Request,
    node_id: str,
    relation: Optional[str] = None,
    svc=Depends(get_memory_service),
):
    """Get neighbors of a node (directly connected nodes)"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    graph_service = get_graph_service_from_request(request, svc)
    
    if not graph_service:
        return JSONResponse({"success": False, "error": "Graph service not available"}, status_code=503)

    try:
        neighbors = await asyncio.to_thread(
            graph_service.get_neighbors,
            node_id=node_id,
            relation=relation,
        )
        
        serialized_neighbors = serialize_for_json(neighbors)
        return JSONResponse({"success": True, "neighbors": serialized_neighbors})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to get graph neighbors: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/graph/extract", response_class=JSONResponse)
async def extract_graph_from_text(
    request: Request,
    svc=Depends(get_memory_service),
):
    """Extract entities and relationships from text and add them to the graph (GraphRAG extraction)"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    graph_service = get_graph_service_from_request(request, svc)
    
    if not graph_service:
        return JSONResponse({"success": False, "error": "Graph service not available"}, status_code=503)

    try:
        body = await request.json()
        text = body.get("text")
        
        if not text:
            return JSONResponse({"success": False, "error": "text is required"}, status_code=400)
        
        user_id = str(user["_id"])
        
        # Use async extraction method
        result = await graph_service.extract_graph_from_text(
            text=text,
            user_id=user_id,
        )
        
        serialized_result = serialize_for_json(result)
        return JSONResponse({"success": True, "result": serialized_result})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to extract graph from text: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.post("/api/graph/backfill-embeddings", response_class=JSONResponse)
async def backfill_graph_embeddings(
    request: Request,
    batch_size: int = 50,
    limit: Optional[int] = None,
    svc=Depends(get_memory_service),
):
    """
    Backfill embeddings for existing graph nodes that don't have them.
    
    This fixes the issue where GraphRAG returns 0 results because nodes
    were created before embeddings were enabled or when embedding service
    wasn't available.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    graph_service = get_graph_service_from_request(request, svc)
    
    if not graph_service:
        return JSONResponse({"success": False, "error": "Graph service not available"}, status_code=503)

    try:
        user_id = str(user["_id"])
        
        result = await graph_service.backfill_embeddings(
            user_id=user_id,
            batch_size=batch_size,
            limit=limit,
        )
        
        return JSONResponse({"success": True, "result": result})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to backfill graph embeddings: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@app.get("/api/graph/visualize", response_class=JSONResponse)
async def visualize_graph(
    request: Request,
    node_id: Optional[str] = None,
    max_depth: int = 2,
    limit: int = 50,
    svc=Depends(get_memory_service),
):
    """Get graph data formatted for visualization (nodes and edges)"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    graph_service = get_graph_service_from_request(request, svc)
    
    if not graph_service:
        return JSONResponse({"success": False, "error": "Graph service not available"}, status_code=503)

    try:
        user_id = str(user["_id"])
        
        if node_id:
            # Traverse from specific node
            nodes = await graph_service.traverse(
                start_id=node_id,
                max_depth=max_depth,
            )
        else:
            # Get all nodes for user
            nodes = await graph_service.list_nodes(
                user_id=user_id,
                limit=limit,
            )
        
        # Format for visualization (nodes and edges)
        visualization_data = {
            "nodes": [],
            "edges": [],
        }
        
        node_ids_seen = set()
        
        for node_data in nodes:
            if isinstance(node_data, dict):
                node = node_data.get("node", node_data)
            else:
                node = node_data
            
            node_id = node.get("_id")
            if not node_id or node_id in node_ids_seen:
                continue
            
            node_ids_seen.add(node_id)
            
            visualization_data["nodes"].append({
                "id": node_id,
                "type": node.get("type"),
                "name": node.get("name"),
                "properties": node.get("properties", {}),
            })
            
            # Extract edges
            edges = node.get("edges", [])
            for edge in edges:
                if edge.get("active", True):  # Only active edges
                    visualization_data["edges"].append({
                        "source": node_id,
                        "target": edge.get("target"),
                        "relation": edge.get("relation"),
                        "weight": edge.get("weight", 1.0),
                        "properties": edge.get("properties", {}),
                    })
        
        serialized_data = serialize_for_json(visualization_data)
        return JSONResponse({"success": True, "graph": serialized_data})
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to visualize graph: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


# ============================================================================
# Persona API Endpoints
# ============================================================================

@app.get("/api/persona", response_class=JSONResponse)
async def get_persona(
    request: Request,
    svc=Depends(get_memory_service),
):
    """Get current persona for the app"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    if not svc or not hasattr(svc, "persona_engine") or not svc.persona_engine:
        return JSONResponse({
            "success": False,
            "error": "Persona feature not enabled"
        })
    
    persona = await svc.get_persona()
    return JSONResponse({
        "success": True,
        "persona": serialize_for_json(persona) if persona else None
    })


@app.put("/api/persona", response_class=JSONResponse)
async def update_persona(
    request: Request,
    svc=Depends(get_memory_service),
):
    """Update persona configuration"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    if not svc or not hasattr(svc, "persona_engine") or not svc.persona_engine:
        raise HTTPException(status_code=400, detail="Persona feature not enabled")
    
    try:
        data = await request.json()
        role = data.get("role")
        description = data.get("description")
        traits = data.get("traits")
        
        updated = await svc.update_persona(
            role=role,
            description=description,
            traits=traits,
        )
        
        return JSONResponse({
            "success": True,
            "persona": serialize_for_json(updated)
        })
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to update persona: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# PROFILE SERVICE ENDPOINTS
# =============================================================================


@app.get("/api/profile", response_class=JSONResponse)
async def get_user_profile(
    request: Request,
    profile_svc=Depends(get_profile_service),
):
    """
    Get the current user's materialized profile.

    Returns the LLM-synthesized profile with identity, preferences,
    relationships, active context, safety alerts, and narrative.
    This is a single MongoDB read -- no LLM calls.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_id = str(user["_id"])

    try:
        profile = await profile_svc.get_user_profile(user_id)
        if not profile:
            return JSONResponse({
                "success": True,
                "profile": None,
                "message": "No profile exists yet. Call POST /api/profile/build to create one.",
            })

        return JSONResponse({
            "success": True,
            "profile": serialize_for_json(profile),
        })
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to get user profile: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/profile/text", response_class=JSONResponse)
async def get_user_profile_text(
    request: Request,
    profile_svc=Depends(get_profile_service),
):
    """
    Get the user's profile formatted for prompt injection.

    Returns a concise text block suitable for the [USER PROFILE]
    section of a conversation system prompt. Always surfaces
    safety-critical facts (allergies, medical).
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_id = str(user["_id"])

    try:
        text = await profile_svc.get_user_profile_text(user_id)
        return JSONResponse({
            "success": True,
            "text": text,
        })
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to get profile text: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/profile/build", response_class=JSONResponse)
async def build_user_profile(
    request: Request,
    profile_svc=Depends(get_profile_service),
):
    """
    Trigger a full profile rebuild from all memories and graph nodes.

    This is expensive (1 LLM call + memory fetch + graph fetch) and
    should be called periodically, not on every request. Use
    POST /api/profile/incremental for lightweight updates.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_id = str(user["_id"])

    try:
        profile = await profile_svc.build_user_profile(user_id)
        return JSONResponse({
            "success": True,
            "profile": serialize_for_json(profile),
            "message": "Full profile rebuild complete.",
        })
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to build user profile: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/profile/incremental", response_class=JSONResponse)
async def incremental_profile_update(
    request: Request,
    profile_svc=Depends(get_profile_service),
    svc=Depends(get_memory_service),
):
    """
    Trigger an incremental profile update with recent memories.

    Fetches the most recent memories and merges them into the
    existing profile via a lightweight LLM call. If no profile
    exists yet, triggers a full build instead.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_id = str(user["_id"])

    try:
        # Get recent memories to merge
        body = await request.json() if await request.body() else {}
        limit = body.get("limit", 20)

        recent_memories = await svc.get_all(user_id=user_id, limit=limit)
        if not recent_memories:
            return JSONResponse({
                "success": True,
                "message": "No memories found to update profile with.",
            })

        profile = await profile_svc.incremental_update(user_id, recent_memories)
        return JSONResponse({
            "success": True,
            "profile": serialize_for_json(profile) if profile else None,
            "memories_merged": len(recent_memories),
        })
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed incremental profile update: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/profile", response_class=JSONResponse)
async def delete_user_profile(
    request: Request,
    profile_svc=Depends(get_profile_service),
):
    """
    Delete the current user's profile (GDPR compliance).
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_id = str(user["_id"])

    try:
        deleted = await profile_svc.delete_user_profile(user_id)
        return JSONResponse({
            "success": True,
            "deleted": deleted,
        })
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to delete user profile: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# COMMUNITY PROFILE ENDPOINTS
# =============================================================================


@app.get("/api/community/profile", response_class=JSONResponse)
async def get_community_profile(
    request: Request,
    profile_svc=Depends(get_profile_service),
):
    """
    Get the community profile for this app.

    Returns anonymous aggregate statistics across all users:
    population, common preferences, shared knowledge, and
    memory landscape. No LLM calls -- pure MongoDB aggregation.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        profile = await profile_svc.get_community_profile()
        if not profile:
            return JSONResponse({
                "success": True,
                "profile": None,
                "message": "No community profile exists yet. Call POST /api/community/profile/build to create one.",
            })

        return JSONResponse({
            "success": True,
            "profile": serialize_for_json(profile),
        })
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to get community profile: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/community/profile/build", response_class=JSONResponse)
async def build_community_profile(
    request: Request,
    profile_svc=Depends(get_profile_service),
):
    """
    Trigger a full rebuild of the community profile.

    Uses MongoDB aggregation (no LLM calls) to aggregate anonymous
    statistics across all users of this app. Includes population
    stats, common preferences, shared knowledge from the graph,
    and memory landscape distribution.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        profile = await profile_svc.build_community_profile()
        if not profile:
            return JSONResponse({
                "success": True,
                "profile": None,
                "message": "Community profiles are not enabled or not enough users.",
            })

        return JSONResponse({
            "success": True,
            "profile": serialize_for_json(profile),
            "message": "Community profile rebuild complete.",
        })
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to build community profile: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/community/profile/text", response_class=JSONResponse)
async def get_community_profile_text(
    request: Request,
    profile_svc=Depends(get_profile_service),
):
    """
    Get the community profile formatted for prompt injection.

    Returns a concise text summary suitable for injecting into
    an LLM system prompt to provide community context.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        text = await profile_svc.get_community_profile_text()
        return JSONResponse({
            "success": True,
            "text": text if text else "No community profile data available.",
        })
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to get community profile text: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# DAILY HYGIENE / SCHEDULED MAINTENANCE ENDPOINTS
# =============================================================================

# In-memory storage for last hygiene run results (per user)
_hygiene_results: dict[str, dict[str, Any]] = {}


@app.post("/api/maintenance/hygiene", response_class=JSONResponse)
async def trigger_daily_hygiene(
    request: Request,
    svc=Depends(get_memory_service),
    db=Depends(get_scoped_db),
):
    """
    Trigger daily brain hygiene for the current user.

    Runs the memory consolidation process that transforms episodic
    memories into semantic facts and procedural lessons. This is
    the "learning" maintenance for the Perfect Brain.

    True Perfect Recall: no decay, no forgetting. This only
    consolidates episodic memories into reusable knowledge.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_id = str(user["_id"])

    try:
        # NOTE: Prefer Depends(get_engine) from mdb_engine.dependencies in new code
        engine = getattr(request.app.state, "engine", None)
        if not engine:
            return JSONResponse(
                {"success": False, "error": "Engine not available"},
                status_code=503,
            )

        # Get LLM and embedding services if available
        llm_service = None
        embedding_service = None
        try:
            slug = getattr(request.app.state, "app_slug", None)
            if slug and engine._service_initializer:
                llm_service = engine._service_initializer._llm_services.get(slug) if hasattr(engine._service_initializer, '_llm_services') else None
        except (AttributeError, KeyError):
            pass

        result = await run_daily_hygiene(
            agent_id=user_id,
            db_client=db,
            db_name=engine.db_name,
            llm_service=llm_service,
            embedding_service=embedding_service,
        )

        # Store result for status endpoint
        _hygiene_results[user_id] = {
            "last_run": datetime.utcnow().isoformat() + "Z",
            "result": result,
        }

        return JSONResponse({
            "success": result.get("success", False),
            "result": serialize_for_json(result),
        })
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Daily hygiene failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/maintenance/status", response_class=JSONResponse)
async def get_maintenance_status(
    request: Request,
):
    """
    Get the status of the last daily hygiene run for the current user.

    Returns the timestamp and results of the most recent hygiene run,
    including how many episodes were consolidated, entities extracted,
    and procedures created.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_id = str(user["_id"])

    status = _hygiene_results.get(user_id)
    if not status:
        return JSONResponse({
            "success": True,
            "status": None,
            "message": "No hygiene run recorded yet. Call POST /api/maintenance/hygiene to run.",
        })

    return JSONResponse({
        "success": True,
        "status": serialize_for_json(status),
    })


# =============================================================================
# MEMORY VERIFICATION ENDPOINTS (JIT Citation Checking)
# =============================================================================


@app.post("/api/memories/verify", response_class=JSONResponse)
async def verify_memories_batch(
    request: Request,
    svc=Depends(get_memory_service),
):
    """
    Verify a batch of memories by checking their citations.

    For each memory with metadata.citations (file_path + content_hash),
    checks if the source file still exists and its hash matches.

    Each memory gets a verification_status:
    - 'verified': Citation checks out
    - 'stale': File changed or deleted
    - 'unverified': No citation or verification disabled
    - 'skipped': File too large or other skip condition
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_id = str(user["_id"])

    try:
        body = await request.json() if await request.body() else {}
        limit = body.get("limit", 50)

        # Fetch memories to verify
        memories = await svc.get_all(user_id=user_id, limit=limit)
        if not memories:
            return JSONResponse({
                "success": True,
                "verified": [],
                "message": "No memories found to verify.",
            })

        # Check if verification is available
        if not hasattr(svc, 'verify_memories'):
            return JSONResponse({
                "success": False,
                "error": "Memory verification not available. Enable verification in memory_config.",
            }, status_code=501)

        verified = await svc.verify_memories(memories)

        # Collect stats
        stats = {"verified": 0, "stale": 0, "unverified": 0, "skipped": 0}
        for m in verified:
            status = m.get("verification_status", "unverified")
            if status in stats:
                stats[status] += 1

        return JSONResponse({
            "success": True,
            "verified": serialize_for_json(verified),
            "stats": stats,
            "total": len(verified),
        })
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Memory verification failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/memories/{memory_id}/verify", response_class=JSONResponse)
async def verify_single_memory(
    request: Request,
    memory_id: str,
    svc=Depends(get_memory_service),
):
    """
    Verify a single memory's citations.

    Checks if the source files referenced by this memory still
    match their stored content hashes.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_id = str(user["_id"])

    try:
        # Get the specific memory
        memory = await svc.get(memory_id, user_id=user_id)
        if not memory:
            raise HTTPException(status_code=404, detail="Memory not found")

        if not hasattr(svc, 'verify_memories'):
            return JSONResponse({
                "success": False,
                "error": "Memory verification not available.",
            }, status_code=501)

        verified = await svc.verify_memories([memory])
        result = verified[0] if verified else memory

        return JSONResponse({
            "success": True,
            "memory": serialize_for_json(result),
            "verification_status": result.get("verification_status", "unverified"),
        })
    except HTTPException:
        raise
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Single memory verification failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/memories/citation", response_class=JSONResponse)
async def generate_memory_citation(
    request: Request,
    svc=Depends(get_memory_service),
):
    """
    Generate a citation object for a file path.

    Creates a content hash that can be stored with a memory to
    enable future JIT verification. Useful when creating memories
    from file content.

    Request body:
        file_path: str - Path to the file
        line_start: int (optional) - Start line for partial citation
        line_end: int (optional) - End line for partial citation
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    try:
        body = await request.json()
        file_path = body.get("file_path")
        if not file_path:
            raise HTTPException(status_code=400, detail="file_path is required")

        line_start = body.get("line_start")
        line_end = body.get("line_end")

        if not hasattr(svc, 'generate_citation'):
            return JSONResponse({
                "success": False,
                "error": "Citation generation not available.",
            }, status_code=501)

        citation = await svc.generate_citation(
            file_path=file_path,
            line_start=line_start,
            line_end=line_end,
        )

        if not citation:
            return JSONResponse({
                "success": False,
                "error": "Could not generate citation. File may not exist or is too large.",
            })

        return JSONResponse({
            "success": True,
            "citation": serialize_for_json(citation),
        })
    except HTTPException:
        raise
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Citation generation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# =============================================================================
# COLD STORAGE BROWSER ENDPOINTS
# =============================================================================


@app.get("/api/memories/cold-storage", response_class=JSONResponse)
async def list_cold_storage_memories(
    request: Request,
    svc=Depends(get_memory_service),
    db=Depends(get_scoped_db),
):
    """
    Browse memories in cold storage (pruned/archived).

    Cold storage is where memories go after pruning (soft delete).
    These memories are not returned in normal searches but can
    be browsed and restored via POST /api/memories/{id}/restore.

    Query params:
        limit: int - Max results (default: 50)
        skip: int - Offset for pagination (default: 0)
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_id = str(user["_id"])

    try:
        limit = int(request.query_params.get("limit", "50"))
        skip = int(request.query_params.get("skip", "0"))

        # Query for pruned/cold-storage memories
        collection_name = "user_memories"
        collection = getattr(db, collection_name, None)
        if collection is None:
            return JSONResponse({
                "success": True,
                "memories": [],
                "total": 0,
                "message": "Memory collection not found.",
            })

        # Cold storage memories have status "cold_storage" or pruned=True
        cold_filter = {
            "user_id": user_id,
            "$or": [
                {"status": "cold_storage"},
                {"pruned": True},
                {"metadata.status": "cold_storage"},
                {"metadata.pruned": True},
            ],
        }

        cursor = collection.find(cold_filter).sort("updated_at", -1).skip(skip).limit(limit)
        memories = await cursor.to_list(limit)
        total = await collection.count_documents(cold_filter)

        return JSONResponse({
            "success": True,
            "memories": serialize_for_json(memories),
            "total": total,
            "skip": skip,
            "limit": limit,
        })
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to list cold storage: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/memories/cold-storage/stats", response_class=JSONResponse)
async def get_cold_storage_stats(
    request: Request,
    db=Depends(get_scoped_db),
):
    """
    Get statistics about memories in cold storage.

    Returns count, date range, and category breakdown of
    archived/pruned memories.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    user_id = str(user["_id"])

    try:
        collection_name = "user_memories"
        collection = getattr(db, collection_name, None)
        if collection is None:
            return JSONResponse({
                "success": True,
                "stats": {"total": 0},
            })

        cold_filter = {
            "user_id": user_id,
            "$or": [
                {"status": "cold_storage"},
                {"pruned": True},
                {"metadata.status": "cold_storage"},
                {"metadata.pruned": True},
            ],
        }

        total = await collection.count_documents(cold_filter)

        # Get date range and category breakdown via aggregation
        stats: dict[str, Any] = {"total": total}

        if total > 0:
            # Get oldest and newest
            pipeline = [
                {"$match": cold_filter},
                {
                    "$group": {
                        "_id": None,
                        "oldest": {"$min": "$created_at"},
                        "newest": {"$max": "$created_at"},
                    }
                },
            ]
            date_results = []
            async for doc in collection.aggregate(pipeline):
                date_results.append(doc)

            if date_results:
                stats["oldest"] = serialize_for_json(date_results[0].get("oldest"))
                stats["newest"] = serialize_for_json(date_results[0].get("newest"))

            # Category breakdown
            cat_pipeline = [
                {"$match": cold_filter},
                {
                    "$group": {
                        "_id": {
                            "$ifNull": [
                                "$category",
                                {"$ifNull": ["$metadata.category", "unknown"]},
                            ]
                        },
                        "count": {"$sum": 1},
                    }
                },
                {"$sort": {"count": -1}},
            ]
            categories: dict[str, int] = {}
            async for doc in collection.aggregate(cat_pipeline):
                categories[doc["_id"] or "unknown"] = doc["count"]

            stats["categories"] = categories

        return JSONResponse({
            "success": True,
            "stats": stats,
        })
    except (PyMongoError, ValueError, KeyError) as e:
        logger.error(f"Failed to get cold storage stats: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Family Nexus -- Domain-Specific Endpoints (OSI + Knowledge Graph)
# ---------------------------------------------------------------------------
# These endpoints provide a structured, Family-Nexus-style REST API on top
# of the OSI-typed knowledge graph.  Each endpoint queries or mutates graph
# nodes whose *type* matches an OSI dataset name (family_member, grocery_item,
# chore, etc.).  This demonstrates the core OSI + MongoDB value proposition:
#   "Define your semantic model once; the graph and the API stay in sync."
# ---------------------------------------------------------------------------


@app.get("/api/family/members", response_class=JSONResponse)
async def get_family_members(
    request: Request,
    svc=Depends(get_memory_service),
):
    """Retrieve all family_member nodes from the knowledge graph."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    graph_service = get_graph_service_from_request(request, svc)
    if not graph_service:
        return JSONResponse({"success": True, "members": []})

    user_id = str(user["_id"])
    nodes = await graph_service.list_nodes(
        node_type="family_member",
        user_id=user_id,
        limit=100,
    )
    return JSONResponse({
        "success": True,
        "members": serialize_for_json(nodes),
        "total": len(nodes) if isinstance(nodes, list) else 0,
    })


@app.get("/api/family/members/{name}/location", response_class=JSONResponse)
async def get_member_location(
    request: Request,
    name: str,
    svc=Depends(get_memory_service),
):
    """Get the last known location / status of a family member.

    Looks up the member by name, then traverses to connected
    device_location nodes.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    graph_service = get_graph_service_from_request(request, svc)
    if not graph_service:
        return JSONResponse({"success": False, "error": "Graph service not available"}, status_code=503)

    user_id = str(user["_id"])
    name_lower = name.lower()

    members = await graph_service.list_nodes(
        node_type="family_member",
        user_id=user_id,
        limit=200,
    )
    member_node = None
    for m in (members or []):
        node_name = (m.get("name") or "").lower()
        if node_name == name_lower or name_lower in node_name:
            member_node = m
            break

    if not member_node:
        return JSONResponse({"success": False, "error": f"Member '{name}' not found"}, status_code=404)

    member_id = member_node.get("_id")
    try:
        neighbors = await asyncio.to_thread(
            graph_service.get_neighbors,
            node_id=member_id,
            relation=None,
        )
        locations = [
            n for n in (neighbors or [])
            if (n.get("type") or n.get("node_type") or "") == "device_location"
        ]
    except (PyMongoError, ValueError):
        locations = []

    return JSONResponse({
        "success": True,
        "member": serialize_for_json(member_node),
        "locations": serialize_for_json(locations),
    })


@app.get("/api/family/members/{name}/health", response_class=JSONResponse)
async def get_member_health(
    request: Request,
    name: str,
    svc=Depends(get_memory_service),
):
    """Aggregate health data for a family member.

    Returns allergies, medications, medical conditions, and vaccinations
    connected to the named member in the knowledge graph.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    graph_service = get_graph_service_from_request(request, svc)
    if not graph_service:
        return JSONResponse({"success": False, "error": "Graph service not available"}, status_code=503)

    user_id = str(user["_id"])
    name_lower = name.lower()

    members = await graph_service.list_nodes(
        node_type="family_member",
        user_id=user_id,
        limit=200,
    )
    member_node = None
    for m in (members or []):
        node_name = (m.get("name") or "").lower()
        if node_name == name_lower or name_lower in node_name:
            member_node = m
            break

    if not member_node:
        return JSONResponse({"success": False, "error": f"Member '{name}' not found"}, status_code=404)

    member_id = member_node.get("_id")
    health_types = {"allergy", "medication", "medical_condition", "vaccination"}

    try:
        neighbors = await asyncio.to_thread(
            graph_service.get_neighbors,
            node_id=member_id,
            relation=None,
        )
    except (PyMongoError, ValueError):
        neighbors = []

    health: dict[str, list] = {t: [] for t in health_types}
    for n in (neighbors or []):
        ntype = n.get("type") or n.get("node_type") or ""
        if ntype in health_types:
            health[ntype].append(n)

    return JSONResponse({
        "success": True,
        "member": serialize_for_json(member_node),
        "allergies": serialize_for_json(health["allergy"]),
        "medications": serialize_for_json(health["medication"]),
        "conditions": serialize_for_json(health["medical_condition"]),
        "vaccinations": serialize_for_json(health["vaccination"]),
    })


# --- Grocery / Shopping ---------------------------------------------------


@app.get("/api/lists/grocery", response_class=JSONResponse)
async def get_grocery_list(
    request: Request,
    status: Optional[str] = None,
    svc=Depends(get_memory_service),
):
    """Get the current grocery list (all grocery_item graph nodes).

    Optional *status* filter: 'pending', 'purchased', etc.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    graph_service = get_graph_service_from_request(request, svc)
    if not graph_service:
        return JSONResponse({"success": True, "items": []})

    user_id = str(user["_id"])
    nodes = await graph_service.list_nodes(
        node_type="grocery_item",
        user_id=user_id,
        limit=200,
    )

    if status:
        nodes = [
            n for n in (nodes or [])
            if (n.get("properties", {}).get("status") or "").lower() == status.lower()
        ]

    return JSONResponse({
        "success": True,
        "items": serialize_for_json(nodes),
        "total": len(nodes) if isinstance(nodes, list) else 0,
    })


@app.post("/api/lists/grocery", response_class=JSONResponse)
async def add_to_grocery_list(
    request: Request,
    svc=Depends(get_memory_service),
):
    """Add one or more items to the grocery list.

    Body:
        items (list[str]): Item names (e.g. ["Milk", "Eggs"]).
        requested_by (str, optional): Name of the family member.
        store (str, optional): Target store.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    graph_service = get_graph_service_from_request(request, svc)
    if not graph_service:
        return JSONResponse({"success": False, "error": "Graph service not available"}, status_code=503)

    user_id = str(user["_id"])
    body = await request.json()
    items = body.get("items", [])
    requested_by = body.get("requested_by", "")
    store = body.get("store", "")

    if not items:
        return JSONResponse({"success": False, "error": "'items' list is required"}, status_code=400)

    created = []
    for item_name in items:
        node_id = f"grocery_item:{item_name.lower().replace(' ', '_')}"
        node = await asyncio.to_thread(
            graph_service.upsert_node,
            node_id=node_id,
            node_type="grocery_item",
            name=item_name,
            properties={
                "status": "pending",
                "requested_by": requested_by,
                "store": store,
            },
            user_id=user_id,
        )
        created.append(node)

    return JSONResponse({
        "success": True,
        "items_added": len(created),
        "items": serialize_for_json(created),
    })


# --- Chores ----------------------------------------------------------------


@app.get("/api/chores", response_class=JSONResponse)
async def get_chores(
    request: Request,
    status: Optional[str] = None,
    assigned_to: Optional[str] = None,
    svc=Depends(get_memory_service),
):
    """List chore nodes with optional filters.

    Query params:
        status: 'pending', 'completed', 'overdue'
        assigned_to: Family member name
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    graph_service = get_graph_service_from_request(request, svc)
    if not graph_service:
        return JSONResponse({"success": True, "chores": []})

    user_id = str(user["_id"])
    nodes = await graph_service.list_nodes(
        node_type="chore",
        user_id=user_id,
        limit=200,
    )

    filtered = nodes or []
    if status:
        filtered = [
            n for n in filtered
            if (n.get("properties", {}).get("status") or "").lower() == status.lower()
        ]
    if assigned_to:
        at_lower = assigned_to.lower()
        filtered = [
            n for n in filtered
            if at_lower in (n.get("properties", {}).get("assigned_to") or "").lower()
        ]

    return JSONResponse({
        "success": True,
        "chores": serialize_for_json(filtered),
        "total": len(filtered),
    })


@app.post("/api/chores", response_class=JSONResponse)
async def assign_chore(
    request: Request,
    svc=Depends(get_memory_service),
):
    """Create a chore and optionally link it to a family member.

    Body:
        name (str): Chore name.
        assigned_to (str, optional): Family member name.
        due_date (str, optional): ISO date string.
        reward (str, optional): Reward description.
        difficulty (str, optional): easy / medium / hard.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    graph_service = get_graph_service_from_request(request, svc)
    if not graph_service:
        return JSONResponse({"success": False, "error": "Graph service not available"}, status_code=503)

    user_id = str(user["_id"])
    body = await request.json()
    chore_name = body.get("name", "").strip()
    if not chore_name:
        return JSONResponse({"success": False, "error": "'name' is required"}, status_code=400)

    node_id = f"chore:{chore_name.lower().replace(' ', '_')}"
    props = {
        "status": "pending",
        "assigned_to": body.get("assigned_to", ""),
        "due_date": body.get("due_date", ""),
        "reward": body.get("reward", ""),
        "difficulty": body.get("difficulty", ""),
        "recurrence": body.get("recurrence", "one-time"),
    }

    node = await asyncio.to_thread(
        graph_service.upsert_node,
        node_id=node_id,
        node_type="chore",
        name=chore_name,
        properties=props,
        user_id=user_id,
    )

    # Link to member if assigned_to is given
    assigned_to_name = body.get("assigned_to", "")
    if assigned_to_name:
        members = await graph_service.list_nodes(
            node_type="family_member",
            user_id=user_id,
            limit=200,
        )
        for m in (members or []):
            if assigned_to_name.lower() in (m.get("name") or "").lower():
                try:
                    await asyncio.to_thread(
                        graph_service.add_edge,
                        source_id=node_id,
                        relation="assigned_to",
                        target_id=m.get("_id"),
                        properties={},
                        weight=1.0,
                    )
                except (PyMongoError, ValueError, KeyError) as edge_err:
                    logger.warning(f"Could not link chore to member: {edge_err}")
                break

    return JSONResponse({
        "success": True,
        "chore": serialize_for_json(node),
    })


# --- Schedule --------------------------------------------------------------


@app.get("/api/schedule", response_class=JSONResponse)
async def get_schedule(
    request: Request,
    member: Optional[str] = None,
    svc=Depends(get_memory_service),
):
    """Get the family schedule: appointments + routines.

    Optional *member* filter narrows results to nodes connected to
    a specific family member.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    graph_service = get_graph_service_from_request(request, svc)
    if not graph_service:
        return JSONResponse({"success": True, "appointments": [], "routines": []})

    user_id = str(user["_id"])

    appointments = await graph_service.list_nodes(
        node_type="appointment",
        user_id=user_id,
        limit=200,
    )
    routines = await graph_service.list_nodes(
        node_type="routine",
        user_id=user_id,
        limit=200,
    )

    if member:
        member_lower = member.lower()

        members = await graph_service.list_nodes(
            node_type="family_member",
            user_id=user_id,
            limit=200,
        )
        member_ids = {
            m.get("_id") for m in (members or [])
            if member_lower in (m.get("name") or "").lower()
        }

        if member_ids:
            connected_ids: set[str] = set()
            for mid in member_ids:
                try:
                    neighbors = await asyncio.to_thread(
                        graph_service.get_neighbors,
                        node_id=mid,
                        relation=None,
                    )
                    for n in (neighbors or []):
                        connected_ids.add(n.get("_id", ""))
                except (PyMongoError, ValueError):
                    pass

            appointments = [a for a in (appointments or []) if a.get("_id") in connected_ids]
            routines = [r for r in (routines or []) if r.get("_id") in connected_ids]

    return JSONResponse({
        "success": True,
        "appointments": serialize_for_json(appointments or []),
        "routines": serialize_for_json(routines or []),
        "total": len(appointments or []) + len(routines or []),
    })


# --- Budget ----------------------------------------------------------------


@app.get("/api/budget/summary", response_class=JSONResponse)
async def get_budget_summary(
    request: Request,
    svc=Depends(get_memory_service),
):
    """Aggregate budget_entry nodes into a spending summary.

    Returns all budget entries and totals grouped by category.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    graph_service = get_graph_service_from_request(request, svc)
    if not graph_service:
        return JSONResponse({"success": True, "entries": [], "by_category": {}, "total": 0.0})

    user_id = str(user["_id"])
    nodes = await graph_service.list_nodes(
        node_type="budget_entry",
        user_id=user_id,
        limit=500,
    )

    by_category: dict[str, float] = {}
    grand_total = 0.0
    for n in (nodes or []):
        props = n.get("properties", {})
        try:
            amount = float(props.get("amount", 0))
        except (TypeError, ValueError):
            amount = 0.0
        category = props.get("category", "other")
        by_category[category] = by_category.get(category, 0.0) + amount
        grand_total += amount

    return JSONResponse({
        "success": True,
        "entries": serialize_for_json(nodes or []),
        "by_category": by_category,
        "total": grand_total,
        "count": len(nodes or []),
    })


# --- Homework --------------------------------------------------------------


@app.get("/api/homework", response_class=JSONResponse)
async def get_homework(
    request: Request,
    status: Optional[str] = None,
    member: Optional[str] = None,
    svc=Depends(get_memory_service),
):
    """List homework / school assignment nodes.

    Query params:
        status: 'pending', 'completed', 'late', 'submitted'
        member: Family member name to filter by.
    """
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    graph_service = get_graph_service_from_request(request, svc)
    if not graph_service:
        return JSONResponse({"success": True, "assignments": []})

    user_id = str(user["_id"])
    nodes = await graph_service.list_nodes(
        node_type="homework",
        user_id=user_id,
        limit=200,
    )

    filtered = nodes or []
    if status:
        filtered = [
            n for n in filtered
            if (n.get("properties", {}).get("status") or "").lower() == status.lower()
        ]
    if member:
        member_lower = member.lower()
        filtered = [
            n for n in filtered
            if member_lower in (n.get("properties", {}).get("assigned_to") or n.get("name") or "").lower()
        ]

    return JSONResponse({
        "success": True,
        "assignments": serialize_for_json(filtered),
        "total": len(filtered),
    })


def serialize_for_json(obj):
    """Recursively serialize objects for JSON, handling datetime and ObjectId."""
    if isinstance(obj, datetime):
        return obj.isoformat() + "Z" if obj.tzinfo is None else obj.isoformat()
    elif isinstance(obj, ObjectId):
        return str(obj)
    elif isinstance(obj, dict):
        return {k: serialize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [serialize_for_json(item) for item in obj]
    return obj


def normalize_memories(memories):
    norm = []
    if not isinstance(memories, list):
        return norm
    for m in memories:
        if not isinstance(m, dict):
            continue
        metadata = m.get("metadata", {})
        txt = (
            m.get("memory")
            or m.get("text")
            or m.get("content")
            or (
                m.get("messages", [{}])[0].get("content")
                if isinstance(m.get("messages"), list)
                else None
            )
            or m.get("data", {}).get("memory", "")
            or m.get("data", {}).get("text", "")
            or metadata.get("raw_content")
            or str(m)
        )
        if txt:
            norm.append({"memory": txt, "id": m.get("id") or m.get("_id"), "metadata": metadata})
    return norm


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
