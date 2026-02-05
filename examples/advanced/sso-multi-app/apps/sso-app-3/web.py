#!/usr/bin/env python3
"""
SSO App 3 - AI Chat Application with SSO Authentication & Smart Document Memory

This example demonstrates:
- CognitiveEngine Integration: Complete RAG pipeline orchestration via CognitiveEngine
- Gemini LLM Support: Uses Google Gemini via LLMProvider abstraction
- Document Processing: Advanced file processing with metadata extraction
- SSO Authentication: Shared authentication across multi-app deployments

Key Features:
- CognitiveEngine with Gemini: Uses GeminiProvider (extends LLMProvider) for Google Gemini support
- Automatic Memory Extraction: Facts are automatically extracted and stored to LTM
- Document Memory: Advanced document processing with atomic fact extraction
- Best Practices: Demonstrates CognitiveEngine usage with non-OpenAI LLM providers via LLMProvider abstraction
"""

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
from fastapi import Depends, File, Form, HTTPException, Request, UploadFile
from starlette.requests import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from mdb_engine.llm import LLMService, get_llm_service
from mdb_engine.dependencies import get_scoped_db, get_memory_service
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
from mdb_engine.memory import CognitiveEngine

# Import shared security utilities
try:
    from shared_security import get_cookie_settings, validate_jwt_token_format
except ImportError:
    # Fallback if shared_security not available (shouldn't happen in normal usage)
    def get_cookie_settings():
        return {"httponly": True, "samesite": "lax", "secure": False}
    def validate_jwt_token_format(token: str) -> bool:
        return bool(token and len(token) > 10)

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
            db = self.engine.get_scoped_db(self.app_slug)
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
        except Exception as e:
            logger.error(f"Failed to store raw content: {e}", exc_info=True)
            return None

    async def get_raw_content(self, bucket_id: str, user_id: str) -> str | None:
        if not self.enabled:
            return None
        try:
            db = self.engine.get_scoped_db(self.app_slug)
            doc = await getattr(db, self.collection_name).find_one(
                {"bucket_id": bucket_id, "user_id": str(user_id)}, sort=[("created_at", -1)]
            )
            return doc.get("content") if doc else None
        except Exception:  # noqa: BLE001
            return None


# Initialize global vars
raw_content_service: RawContentService | None = None
cognitive_engine: CognitiveEngine | None = None
llm_service: LLMService | None = None


# Load manifest and build CSFLE config for encrypted memory
_manifest_path = Path(__file__).parent / "manifest.json"
_manifest_data = json.load(open(_manifest_path)) if _manifest_path.exists() else {}
_csfle_config = build_csfle_config_from_manifest(_manifest_data)

if _csfle_config:
    logger.info(
        f"🔐 CSFLE enabled for memory encryption: "
        f"collections={list(_csfle_config.encrypted_collections.keys())}"
    )

engine = MongoDBEngine(
    mongo_uri=os.getenv("MONGO_URI", "mongodb://mongodb:27017/"),
    db_name=os.getenv("MONGO_DB_NAME", "oblivio_apps"),
    csfle_config=_csfle_config,
)


async def on_startup(app, engine, manifest):
    global raw_content_service, cognitive_engine, llm_service
    
    raw_content_config = manifest.get("raw_content_config", {})
    if raw_content_config.get("enabled", False):
        raw_content_service = RawContentService(engine, APP_SLUG, raw_content_config)
    
    # Initialize LLM service (used for chat and document processing)
    llm_config = manifest.get("llm_config", {})
    llm_service = get_llm_service(config=llm_config)
    
    # Initialize CognitiveEngine for complete RAG pipeline
    memory_service = engine.get_memory_service(APP_SLUG)
    if memory_service:
        try:
            # Get collections from MDB-Engine connection manager
            motor_client = engine._connection_manager.mongo_client
            pymongo_client = motor_client.delegate
            pymongo_db = pymongo_client[engine.db_name]
            
            chat_history_collection = pymongo_db["chat_history"]
            
            # Get the LLMProvider from the service for CognitiveEngine
            # CognitiveEngine expects an LLMProvider instance (sync interface)
            from mdb_engine.memory.orchestrator import LLMProvider as OrchestratorLLMProvider
            
            # Create a wrapper that adapts our async LLMService to CognitiveEngine's sync LLMProvider interface
            class LLMServiceProvider(OrchestratorLLMProvider):
                """Adapter to use async LLMService with sync CognitiveEngine LLMProvider interface."""
                def __init__(self, llm_service):
                    self.llm_service = llm_service
                    self._loop = None
                
                def generate_chat_completion(self, messages, model=None, **kwargs):
                    """Generate chat completion using LLMService (sync wrapper for async)."""
                    import asyncio
                    try:
                        # Try to get the current event loop
                        loop = asyncio.get_running_loop()
                        # If we're in an async context, we need to run in a thread
                        import concurrent.futures
                        with concurrent.futures.ThreadPoolExecutor() as executor:
                            future = executor.submit(
                                lambda: asyncio.run(
                                    self.llm_service.chat_completion(messages, model=model, **kwargs)
                                )
                            )
                            return future.result(timeout=300)  # 5 minute timeout
                    except RuntimeError:
                        # No running loop, we can use asyncio.run
                        return asyncio.run(
                            self.llm_service.chat_completion(messages, model=model, **kwargs)
                        )
            
            llm_provider = LLMServiceProvider(llm_service)
            
            cognitive_engine = CognitiveEngine(
                app_slug=APP_SLUG,
                memory_service=memory_service,
                chat_history_collection=chat_history_collection,
                stm_context_limit=10,
                ltm_search_limit=12,  # Match current limit
                auto_summarize_threshold=20,
                llm_provider=llm_provider,  # Use LLMService via adapter
                # Context Engineering configuration
                enable_context_engineering=True,
                stm_raw_window=5,
                enable_entity_extraction=True,
                enable_dynamic_persona=True,
            )
            logger.info("✅ Cognitive Engine Online: Complete RAG Pipeline with Context Engineering Ready")
        except Exception as e:
            logger.error(f"❌ Failed to initialize CognitiveEngine: {e}", exc_info=True)
            cognitive_engine = None
    else:
        logger.warning("⚠️ Memory service not found - Cognitive Engine disabled")
    
    try:
        engine.register_websocket_routes(app, APP_SLUG)
        logger.info("✅ WebSocket routes registered")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Failed to register WebSocket routes: {e}")
    
    # Configure App-Level Auth Ticket Endpoint (for WebSocket authentication)
    if engine.websocket_ticket_store:
        _configure_ticket_endpoint(app)
    
    logger.info("AI Chat application ready!")


app = engine.create_app(
    slug=APP_SLUG,
    manifest=Path(__file__).parent / "manifest.json",
    title="AI Chat",
    on_startup=on_startup,
)

# --- CORE LOGIC (AI PROCESSING) ---


async def _fallback_rag_chat(db, svc, user_id, cid, full_input, message, category):
    """Fallback RAG chat when CognitiveEngine is not available."""
    # Store user message
    await db.messages.insert_one(
        {
            "conversation_id": cid,
            "user_id": user_id,
            "role": "user",
            "content": full_input,
            "created_at": datetime.utcnow(),
        }
    )
    
    # Manual RAG search
    rag_context = []
    if svc and message.strip():
        try:
            query_lower = message.lower()
            is_author_query = any(
                keyword in query_lower
                for keyword in ["author", "who wrote", "who created", "who is the author"]
            )
            
            mems = await asyncio.to_thread(
                svc.search, query=message[:500], user_id=user_id, limit=12
            )
            
            if is_author_query and len(mems) < 5:
                all_mems = await asyncio.to_thread(svc.get_all, user_id=user_id, limit=200)
                author_mems = [
                    m
                    for m in all_mems
                    if m.get("metadata", {}).get("doc_author")
                    and m.get("metadata", {}).get("doc_author") != "Unknown"
                ]
                existing_ids = {
                    m.get("id") or m.get("_id") for m in mems if m.get("id") or m.get("_id")
                }
                for am in author_mems:
                    if (am.get("id") or am.get("_id")) not in existing_ids:
                        mems.append(am)
                mems = mems[:15]
            
            if category != "general" and mems:
                filtered = [
                    m
                    for m in mems
                    if m.get("metadata", {}).get("category") == category
                    or m.get("metadata", {}).get("category") == "general"
                ]
                if filtered:
                    mems = filtered[:10]
            
            for m in mems:
                memory_text = m.get("memory")
                if not memory_text:
                    continue
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
        except Exception as e:
            logger.error(f"RAG search failed: {e}", exc_info=True)
    
    # Generate response using LLM service (LiteLLM)
    memory_context_str = (
        "\n".join([f"- {mem}" for mem in rag_context])
        if rag_context
        else "No relevant memories found."
    )
    
    messages = [
        {
            "role": "system",
            "content": "You are Orby, an AI assistant with access to stored memories."
        },
        {
            "role": "user",
            "content": f"""MEMORY CONTEXT (use this information to answer questions):
{memory_context_str}

IMPORTANT: When answering questions about document authors, titles, or metadata,
pay special attention to the [Document Context] information in the memories above.

User: {full_input}"""
        }
    ]
    
    try:
        if llm_service:
            ai_text = await llm_service.chat_completion(messages=messages)
        else:
            ai_text = "LLM service not initialized"
    except Exception as e:
        ai_text = f"AI Error: {e}"
    
    # Store assistant message
    await db.messages.insert_one(
        {
            "conversation_id": cid,
            "user_id": user_id,
            "role": "assistant",
            "content": ai_text,
            "created_at": datetime.utcnow(),
        }
    )
    
    return ai_text


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
    except Exception as e:
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
            temperature=1.0,
            response_format=DocumentMetadata,  # Pass Pydantic model directly
        )
        
        # Parse and validate using Pydantic
        # LiteLLM returns JSON string that matches the Pydantic schema
        return DocumentMetadata.model_validate_json(response_text)
    except Exception:
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
                temperature=1.0,
                response_format=ChunkInsights,  # Pass Pydantic model directly
            )
            
            # Parse and validate using Pydantic
            # LiteLLM returns JSON string that matches the Pydantic schema
            insights = ChunkInsights.model_validate_json(response_text)
            
            # Return the facts from the validated model
            return insights.facts if insights.facts else []
        except Exception as e:  # noqa: BLE001
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

                await asyncio.to_thread(
                    svc.add,
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
            except Exception:
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


def get_auth_hub_url() -> str:
    return os.getenv("AUTH_HUB_URL", "http://localhost:8000")


@app.post("/logout")
async def logout(request: Request):
    """Logout and revoke token."""
    from mdb_engine.auth.shared_users import SharedUserPool

    pool: SharedUserPool = getattr(app.state, "user_pool", None)

    # Get token from cookie
    token = request.cookies.get("mdb_auth_token")

    # Revoke token if we have pool and token
    if pool and token:
        try:
            await pool.revoke_token(token, reason="logout")
        except (AttributeError, TypeError, ValueError, KeyError) as e:
            logger.warning(f"Failed to revoke token: {e}")

    # Create response redirecting to auth hub
    response = RedirectResponse(url=f"{get_auth_hub_url()}/login", status_code=302)

    # Delete cookie
    cookie_settings = get_cookie_settings()
    response.delete_cookie(
        "mdb_auth_token",
        path="/",
        domain=None,  # Let browser handle domain
        secure=cookie_settings["secure"],
        samesite=cookie_settings["samesite"],
    )

    return response


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    if get_current_user(request):
        return RedirectResponse("/conversations")
    callback = f"{request.url.scheme}://{request.url.hostname}:{request.url.port}/auth/callback"
    return RedirectResponse(f"{get_auth_hub_url()}/login?redirect_to={callback}")


@app.get("/auth/callback")
async def auth_callback(request: Request, token: str = None):
    """Token exchange endpoint - sets cookie for this app after auth hub login."""
    from urllib.parse import unquote_plus
    
    if not token:
        token = request.query_params.get("token")
    if token:
        token = unquote_plus(token)
    
    # Validate token format before processing
    if not token or not validate_jwt_token_format(token):
        return RedirectResponse(
            url=f"{get_auth_hub_url()}/login?error=invalid_token", status_code=302
        )
    
    # Validate token with user pool
    from mdb_engine.auth.shared_users import SharedUserPool
    pool: SharedUserPool = getattr(app.state, "user_pool", None)
    if pool:
        user = await pool.validate_token(token)
        if not user:
            return RedirectResponse(
                url=f"{get_auth_hub_url()}/login?error=invalid_token", status_code=302
            )
    
    response = RedirectResponse("/", status_code=302)
    cookie_settings = get_cookie_settings()
    response.set_cookie(
        "mdb_auth_token",
        token,
        httponly=cookie_settings["httponly"],
        samesite=cookie_settings["samesite"],
        secure=cookie_settings["secure"],
        max_age=86400,  # 24 hours
        path="/",
    )
    return response


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
        return RedirectResponse(url="/login")
    
    return templates.TemplateResponse("persona.html", {"request": request, "user": user})


@app.get("/perceptions", response_class=HTMLResponse)
async def get_perceptions_page(request: Request):
    """Perceptions visualization page"""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login")
    
    return templates.TemplateResponse("perceptions.html", {"request": request, "user": user})


@app.get("/conversations/{cid}", response_class=HTMLResponse)
async def conversation_view(
    request: Request,
    cid: str,
    db=Depends(get_scoped_db),
):
    """
    Get a specific conversation by ID.
    
    Best Practice: Uses dependency injection for database access.
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
    
    return templates.TemplateResponse(
        request, "conversation.html", {
            "user": user,
            "conversation": convo,
            "messages": msgs,
            "last_active_context": last_active_context,
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
        except Exception:  # noqa: BLE001
            pass

    for f in file_list:
        if f.filename:
            data = await convert_file_to_markdown(f)
            if data["raw_text"]:
                processed_files.append(data)
                file_context += f"\n{data['content']}"

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
            
            # BACKGROUND MEMORY EXTRACTION: Extract facts after returning response
            # This provides fast response times while memory extraction happens asynchronously
            # The UI will be notified via WebSocket when extraction completes
            # 
            # NOTE: extract_facts=False above is INTENTIONAL - it's a performance optimization.
            # Memory extraction is expensive (LLM call), so we do it in the background after
            # returning the response. This gives users instant responses while memories are
            # extracted and stored asynchronously. The log message "Skipping memory storage"
            # is expected and normal - extraction happens via _extract_memories_background() below.
            if svc and message.strip():
                logger.info(
                    f"📡 [Background Extraction] Scheduling memory extraction: "
                    f"user_id={user_id}, bucket_id={memory_bucket_id}"
                )
                extraction_task = asyncio.create_task(
                    _extract_memories_background(
                        user_id=user_id,
                        conversation_id=cid,
                        message=full_input,
                        memory_service=svc,
                        bucket_id=memory_bucket_id,
                        bucket_type="category",
                        category=category,
                        ai_response=ai_text,
                    )
                )
                # Add error callback to log task failures without disrupting main flow
                extraction_task.add_done_callback(
                    lambda t: logger.error(
                        f"❌ Background extraction task failed: {t.exception()}"
                    ) if t.exception() else None
                )
            
        except Exception as e:
            logger.error(f"❌ CognitiveEngine chat failed: {e}", exc_info=True)
            # Fallback to manual RAG
            ai_text = await _fallback_rag_chat(
                db, svc, user_id, cid, full_input, message, category
            )
            retrieved_memories = []
            
            # IMPORTANT: Store memories even when CognitiveEngine fails (like chit_chat)
            # This ensures memories are always stored for the conversation
            if svc and message.strip():
                try:
                    # Use category-based bucket for bucket awareness
                    fallback_bucket_id = f"category:{category}:{user_id}"
                    logger.info(f"💾 [Fallback] Storing memory for failed CognitiveEngine chat: user_id={user_id}, bucket_id={fallback_bucket_id}, message='{message[:50]}...'")
                    
                    # Combine user message and AI response for extraction context
                    extraction_text = full_input
                    if ai_text:
                        extraction_text = f"User: {full_input}\nAI: {ai_text}"

                    # Use add_async for optimal performance with parallel processing
                    stored = await svc.add_async(
                        messages=extraction_text,
                        user_id=user_id,
                        metadata={
                            "source": "chat_session",
                            "session_id": cid,
                            "category": category,
                            "associated_bucket_id": fallback_bucket_id,  # For unified bucket search
                            "raw_input": full_input,
                            "raw_output": ai_text,
                        },
                        bucket_id=fallback_bucket_id,
                        bucket_type="category",
                    )
                    if stored and isinstance(stored, list) and len(stored) > 0:
                        logger.info(f"✅ [Fallback] Stored {len(stored)} memories after CognitiveEngine failure")
                        # Broadcast memory storage event
                        broadcast_task = asyncio.create_task(
                            _broadcast_memory_stored(
                                user_id=user_id,
                                conversation_id=cid,
                                memory_service=svc,
                                new_memories=stored,
                            )
                        )
                        broadcast_task.add_done_callback(
                            lambda t: logger.error(
                                f"❌ Memory broadcast task failed: {t.exception()}"
                            ) if t.exception() else None
                        )
                    else:
                        logger.warning(f"⚠️ [Fallback] Memory storage returned empty: {stored}")
                except Exception as mem_error:
                    logger.error(f"❌ [Fallback] Failed to store memory: {mem_error}", exc_info=True)
    else:
        # Fallback if CognitiveEngine not available
        logger.warning("⚠️ CognitiveEngine not available, using fallback RAG")
        ai_text = await _fallback_rag_chat(
            db, svc, user_id, cid, full_input, message, category
        )
        retrieved_memories = []
        
        # IMPORTANT: Store memories even when CognitiveEngine not available (like chit_chat)
        # This ensures memories are always stored for the conversation
        if svc and message.strip():
            try:
                # Use category-based bucket for bucket awareness
                fallback_bucket_id = f"category:{category}:{user_id}"
                logger.info(f"💾 [Fallback] Storing memory when CognitiveEngine unavailable: user_id={user_id}, bucket_id={fallback_bucket_id}, message='{message[:50]}...'")
                
                # Combine user message and AI response for extraction context
                extraction_text = full_input
                if ai_text:
                    extraction_text = f"User: {full_input}\nAI: {ai_text}"

                # Use add_async for optimal performance with parallel processing
                stored = await svc.add_async(
                    messages=extraction_text,
                    user_id=user_id,
                    metadata={
                        "source": "chat_session",
                        "session_id": cid,
                        "category": category,
                        "associated_bucket_id": fallback_bucket_id,  # For unified bucket search
                        "raw_input": full_input,
                        "raw_output": ai_text,
                    },
                    bucket_id=fallback_bucket_id,
                    bucket_type="category",
                )
                if stored and isinstance(stored, list) and len(stored) > 0:
                    logger.info(f"✅ [Fallback] Stored {len(stored)} memories when CognitiveEngine unavailable")
                    # Broadcast memory storage event
                    broadcast_task = asyncio.create_task(
                        _broadcast_memory_stored(
                            user_id=user_id,
                            conversation_id=cid,
                            memory_service=svc,
                            new_memories=stored,
                        )
                    )
                    broadcast_task.add_done_callback(
                        lambda t: logger.error(
                            f"❌ Memory broadcast task failed: {t.exception()}"
                        ) if t.exception() else None
                    )
                else:
                    logger.warning(f"⚠️ [Fallback] Memory storage returned empty: {stored}")
            except Exception as mem_error:
                logger.error(f"❌ [Fallback] Failed to store memory: {mem_error}", exc_info=True)

    # 3. Background Memory Task (for file processing - CognitiveEngine handles chat memory automatically)
    if svc and processed_files:

        async def store_task():
            total_memories = 0
            errors = []
            try:
                # Note: Chat memory is handled automatically by CognitiveEngine when extract_facts=True
                # Only process file memories here
                
                # File Memory
                for pf in processed_files:
                    try:
                        count = await process_and_store_file_memory(
                            svc=svc, user_id=user_id, file_data=pf, category=category
                        )
                        total_memories += count
                    except Exception as e:
                        error_msg = f"Error processing {pf.get('filename')}: {e}"
                        logger.error(f"❌ {error_msg}", exc_info=True)
                        errors.append(error_msg)

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

            except Exception as e:
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
    
    return JSONResponse(response_data)


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
    
    async def generate_stream():
        """Async generator that yields SSE events."""
        full_response = ""
        retrieved_memories = []
        memory_bucket_id = f"category:{category}:{user_id}"
        persona_used_stream = None
        entity_facts_stream = {}
        dynamic_instructions_stream = ""
        prompt_template_stream = None
        
        try:
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
                    
                except Exception as e:
                    logger.error(f"❌ CognitiveEngine context building failed: {e}", exc_info=True)
                    # Fallback to manual RAG
                    if svc and message.strip():
                        try:
                            mems = await asyncio.to_thread(
                                svc.search, query=message[:500], user_id=user_id, limit=12
                            )
                            for m in mems:
                                memory_text = m.get("memory")
                                if memory_text:
                                    rag_context.append(memory_text)
                                    retrieved_memories.append({
                                        "id": m.get("id"),
                                        "memory": m.get("memory"),
                                        "score": m.get("score", m.get("similarity", 0.0)),
                                    })
                        except Exception as e2:
                            logger.error(f"Fallback RAG search failed: {e2}", exc_info=True)
                    
                    # Build fallback messages
                    memory_context_str = (
                        "\n".join([f"- {mem}" for mem in rag_context])
                        if rag_context
                        else "No relevant memories found."
                    )
                    
                    messages = [
                        {
                            "role": "system",
                            "content": "You are Orby, an AI assistant with access to stored memories."
                        },
                        {
                            "role": "user",
                            "content": f"MEMORY CONTEXT:\n{memory_context_str}\n\nUser: {message}"
                        }
                    ]
            else:
                # Fallback if CognitiveEngine not available
                rag_context = []
                if svc and message.strip():
                    try:
                        mems = await asyncio.to_thread(
                            svc.search, query=message[:500], user_id=user_id, limit=12
                        )
                        for m in mems:
                            memory_text = m.get("memory")
                            if memory_text:
                                rag_context.append(memory_text)
                                retrieved_memories.append({
                                    "id": m.get("id"),
                                    "memory": m.get("memory"),
                                    "score": m.get("score", m.get("similarity", 0.0)),
                                })
                    except Exception as e:
                        logger.error(f"RAG search failed: {e}", exc_info=True)
                
                memory_context_str = (
                    "\n".join([f"- {mem}" for mem in rag_context])
                    if rag_context
                    else "No relevant memories found."
                )
                
                messages = [
                    {
                        "role": "system",
                        "content": "You are Orby, an AI assistant with access to stored memories."
                    },
                    {
                        "role": "user",
                        "content": f"MEMORY CONTEXT:\n{memory_context_str}\n\nUser: {message}"
                    }
                ]
            
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
                        
                except Exception as e:
                    logger.error(f"Streaming LLM failed: {e}", exc_info=True)
                    error_event = {"type": "error", "message": str(e)}
                    yield f"data: {json_module.dumps(error_event)}\n\n"
                    full_response = f"Error generating response: {e}"
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
            
            # 5. Send done event with full response
            done_event = {
                "type": "done",
                "full_response": full_response,
                "memories_used": len(retrieved_memories),
            }
            yield f"data: {json_module.dumps(done_event)}\n\n"
            
            # 6. Trigger background memory extraction
            if svc and message.strip():
                extraction_task = asyncio.create_task(
                    _extract_memories_background(
                        user_id=user_id,
                        conversation_id=cid,
                        message=message,
                        memory_service=svc,
                        bucket_id=memory_bucket_id,
                        bucket_type="category",
                        category=category,
                        ai_response=full_response,
                    )
                )
                extraction_task.add_done_callback(
                    lambda t: logger.error(f"Background extraction failed: {t.exception()}")
                    if t.exception() else None
                )
                
        except Exception as e:
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
            "filename": filename,
            "fact_number": fact_number,
            "total_facts": total_facts,
        }
        await broadcast_to_app(APP_SLUG, payload_progress, user_id=user_id)
        
        logger.debug(f"📡 Sent extraction status: stage={stage}, progress={progress}%")
    except Exception as e:
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
        
        # Combine user message and AI response for extraction context
        # This allows the memory system to understand the full interaction
        extraction_text = message
        if ai_response:
            extraction_text = f"User: {message}\nAI: {ai_response}"
        
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
            # Format messages for perception analysis (if AI response available)
            # Perception analysis requires both user and assistant messages
            if ai_response:
                extraction_messages = [
                    {"role": "user", "content": message},
                    {"role": "assistant", "content": ai_response}
                ]
            else:
                extraction_messages = extraction_text  # Use string format if no AI response
            
            # Extract and store memories (this uses LLM for fact extraction)
            # PERFORMANCE OPTIMIZATION: Use add_async for ~5x faster parallel processing
            # Batch embeddings, parallel vector searches, and concurrent importance assessments
            stored = await memory_service.add_async(
                messages=extraction_messages,  # Pass messages list if AI response available
                user_id=user_id,
                metadata=storage_metadata,
                bucket_id=storage_bucket_id,
                bucket_type=bucket_type,
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
    except Exception as e:
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
        # Use asyncio.to_thread to run synchronous memory service methods safely
        try:
            fresh_memories = await asyncio.to_thread(
                memory_service.get_all,
                user_id=str(user_id),
                limit=MAX_MEMORIES_TO_FETCH
            )
        except Exception as fetch_error:
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
        except Exception as broadcast_error:
            logger.error(
                f"❌ Failed to broadcast WebSocket event (user_id={user_id}): {broadcast_error}",
                exc_info=True
            )
            # Don't re-raise - background task failures shouldn't affect main flow
            
    except asyncio.CancelledError:
        # Task was cancelled - this is expected behavior, don't log as error
        logger.debug(f"🔄 Memory broadcast task cancelled for user_id={user_id}")
        raise  # Re-raise to properly handle cancellation
    except Exception as e:
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
            mems = await asyncio.to_thread(
                svc.get_all, user_id=user_id, filters={"bucket_id": bucket_id}, limit=1
            )
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
        mems = await asyncio.to_thread(
            svc.get_all, user_id=user_id, filters={"associated_bucket_id": bucket_id}, limit=500
        )
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
        except Exception:  # noqa: BLE001
            pass
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

    memories = await asyncio.to_thread(svc.get_all, user_id=str(user["_id"]), limit=limit)
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

    all_mems = await asyncio.to_thread(svc.get_all, user_id=str(user["_id"]), limit=2000)
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
    mems = await asyncio.to_thread(
        svc.get_all, user_id=str(user["_id"]), filters={"metadata": {"associated_bucket_id": bucket_id}}, limit=limit
    )
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
    svc=Depends(get_memory_service),
):
    """
    Search memories using semantic search with explicit bucket scoping.
    
    **Security**: Requires explicit bucket scoping to prevent accidental cross-bucket data leakage.
    
    Parameters:
    - query: Search query string (required)
    - bucket_id: Full bucket ID to search within (e.g., "category:work:user123") - required UNLESS search_all=true
    - category: Category name (e.g., "work", "coding") - convenience parameter that constructs bucket_id server-side
                 Cannot be used with bucket_id (mutually exclusive)
    - search_all: If true, search across all buckets for the user (explicit opt-in)
    - limit: Maximum number of results (default: 50)
    
    **Validation**:
    - Either bucket_id OR category OR search_all=true must be provided
    - bucket_id and category are mutually exclusive (cannot provide both)
    - bucket_id and search_all are mutually exclusive
    - category and search_all are mutually exclusive
    - If neither provided: Returns 400 error
    - Always scoped by user_id (enforced server-side)
    
    Uses associated_bucket_id for bucket-aware filtering, which finds:
    - Conversation memories in the bucket
    - File memories associated with the bucket
    
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
    
    # Build filters based on search mode
    filters = None
    if search_all:
        # Cross-bucket search: Only filter by user_id (already enforced in svc.search)
        filters = None
        logger.info(f"🔍 [Memory Search] Cross-bucket search requested for user_id={user_id}")
    else:
        # Scoped search: Filter by associated_bucket_id
        filters = {"metadata": {"associated_bucket_id": bucket_id}}
        logger.info(f"🔍 [Memory Search] Scoped search: bucket_id={bucket_id}, user_id={user_id}")
    
    results = await asyncio.to_thread(
        svc.search, query=query, user_id=user_id, limit=limit, filters=filters
    )

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

    return JSONResponse(
        {
            "success": True,
            "results": normalized_results,
            "count": len(normalized_results),
            "query": query,
        }
    )


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

    memory = await asyncio.to_thread(svc.get, memory_id=memory_id, user_id=user_id)
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
        injected_memory = await asyncio.to_thread(
            svc.inject,
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
    except Exception as e:
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
        updated_memory = await asyncio.to_thread(
            svc.update, memory_id=memory_id, memory=data, user_id=user_id
        )
        
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
    except Exception as e:
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
    success = await asyncio.to_thread(svc.delete, memory_id=memory_id, user_id=user_id)
    
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
        
        analytics = await asyncio.to_thread(
            svc.get_memory_analytics,
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
    except Exception as e:
        logger.error(f"Failed to get memory analytics: {e}", exc_info=True)
        return JSONResponse({
            "success": False,
            "error": str(e),
        }, status_code=500)


@app.get("/api/memories/cold-storage", response_class=JSONResponse)
async def get_cold_storage(
    request: Request,
    limit: int = 50,
    svc=Depends(get_memory_service),
):
    """
    Get memories from cold storage (pruned/inactive memories).
    
    Cold storage provides paper trail for analytics and recovery.
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
        if not hasattr(svc, 'get_cold_storage'):
            return JSONResponse({
                "success": False,
                "error": "Cold storage not available",
            }, status_code=501)
        
        cold_memories = await asyncio.to_thread(
            svc.get_cold_storage,
            user_id=user_id,
            limit=limit,
            include_reason=True,
        )
        
        return JSONResponse({
            "success": True,
            "memories": cold_memories,
            "count": len(cold_memories),
        })
    except NotImplementedError:
        return JSONResponse({
            "success": False,
            "error": "Cold storage not supported",
        }, status_code=501)
    except Exception as e:
        logger.error(f"Failed to get cold storage: {e}", exc_info=True)
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
        
        restored_memory = await asyncio.to_thread(
            svc.restore_from_cold_storage,
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
    except Exception as e:
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
        
        if hasattr(svc, 'detect_knowledge_conflict_sync'):
            conflict = await asyncio.to_thread(
                svc.detect_knowledge_conflict_sync,
                user_id=user_id,
                new_fact=new_fact,
            )
        elif hasattr(svc, 'detect_knowledge_conflict'):
            conflict = await svc.detect_knowledge_conflict(
                user_id=user_id,
                new_fact=new_fact,
            )
        else:
            return JSONResponse({
                "success": False,
                "error": "Conflict detection not available",
            }, status_code=501)
        
        return JSONResponse({
            "success": True,
            "has_conflict": conflict is not None,
            "conflict_description": conflict,
        })
    except HTTPException:
        raise
    except Exception as e:
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
        except Exception:
            pass
        
        max_capacity = body.get("max_capacity")
        reason = body.get("reason", "manual_trigger")
        
        if not hasattr(svc, 'prune_memories'):
            return JSONResponse({
                "success": False,
                "error": "Pruning not available",
            }, status_code=501)
        
        pruned_count = await asyncio.to_thread(
            svc.prune_memories,
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
    except Exception as e:
        logger.error(f"Failed to prune memories: {e}", exc_info=True)
        return JSONResponse({
            "success": False,
            "error": str(e),
        }, status_code=500)


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
    except Exception as e:
        logger.error(f"Failed to get reflections: {e}", exc_info=True)
        return JSONResponse({
            "success": False,
            "error": str(e),
        }, status_code=500)


# ============================================================================
# GRAPH ENDPOINTS (Knowledge Graph / GraphRAG)
# ============================================================================


@app.get("/api/graph/stats", response_class=JSONResponse)
async def get_graph_stats(
    request: Request,
    svc=Depends(get_memory_service),
):
    """Get graph store statistics"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    if not svc or not getattr(svc, "graph_store", None):
        return JSONResponse(
            {"success": True, "enabled": False, "total_nodes": 0, "total_edges": 0}
        )

    stats = await asyncio.to_thread(svc.graph_store.get_stats)
    return JSONResponse({"success": True, "enabled": True, **stats})


@app.get("/api/graph/search", response_class=JSONResponse)
async def graph_hybrid_search(
    request: Request,
    query: str,
    max_depth: int = 2,
    limit: int = 10,
    svc=Depends(get_memory_service),
):
    """Hybrid search combining vector similarity and graph traversal"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")

    graph_store = getattr(svc, "graph_store", None) if svc else None
    
    if not graph_store:
        return JSONResponse(
            {"success": True, "entry_nodes": [], "graph_context": [], "total_nodes": 0}
        )

    user_id = str(user["_id"])
    
    results = await asyncio.to_thread(
        graph_store.hybrid_search,
        query=query,
        user_id=user_id,
        max_depth=max_depth,
        vector_limit=limit
    )
    
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

    graph_store = getattr(svc, "graph_store", None) if svc else None
    
    if not graph_store:
        return JSONResponse({"success": True, "nodes": []})

    results = await asyncio.to_thread(
        graph_store.traverse,
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

    graph_store = getattr(svc, "graph_store", None) if svc else None
    
    if not graph_store:
        return JSONResponse({"success": True, "nodes": []})

    user_id = str(user["_id"])
    
    nodes = await asyncio.to_thread(
        graph_store.list_nodes,
        node_type=node_type,
        user_id=user_id,
        limit=limit
    )
    
    # Serialize datetime objects for JSON response
    serialized_nodes = serialize_for_json(nodes)
    return JSONResponse({"success": True, "nodes": serialized_nodes})


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
    
    persona = svc.get_persona()
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
        
        updated = svc.update_persona(
            role=role,
            description=description,
            traits=traits,
        )
        
        return JSONResponse({
            "success": True,
            "persona": serialize_for_json(updated)
        })
    except Exception as e:
        logger.error(f"Failed to update persona: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Perceptions API Endpoints
# ============================================================================

@app.get("/api/perceptions/user", response_class=JSONResponse)
async def get_user_perceptions(
    request: Request,
    svc=Depends(get_memory_service),
):
    """Get user perceptions for the current user"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    if not svc or not hasattr(svc, "perception_engine") or not svc.perception_engine:
        return JSONResponse({
            "success": False,
            "error": "Perception feature not enabled"
        })
    
    user_id = str(user["_id"])
    perception = svc.perception_engine.get_user_perception(user_id)
    
    return JSONResponse({
        "success": True,
        "perception": serialize_for_json(perception) if perception else None
    })


@app.get("/api/perceptions/self", response_class=JSONResponse)
async def get_self_perceptions(
    request: Request,
    svc=Depends(get_memory_service),
):
    """Get self-perceptions (robot's view of itself)"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    if not svc or not hasattr(svc, "perception_engine") or not svc.perception_engine:
        return JSONResponse({
            "success": False,
            "error": "Perception feature not enabled"
        })
    
    perception = svc.perception_engine.get_self_perception()
    
    return JSONResponse({
        "success": True,
        "perception": serialize_for_json(perception) if perception else None
    })


@app.get("/api/perceptions/history", response_class=JSONResponse)
async def get_perception_history(
    request: Request,
    user_id: Optional[str] = None,
    perception_type: Optional[str] = None,
    limit: int = 100,
    svc=Depends(get_memory_service),
):
    """Get perception history for temporal analysis"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    if not svc or not hasattr(svc, "perception_engine") or not svc.perception_engine:
        return JSONResponse({
            "success": False,
            "error": "Perception feature not enabled"
        })
    
    # Use current user's ID if not specified
    if not user_id:
        user_id = str(user["_id"])
    
    history = svc.perception_engine.get_perception_history(
        user_id=user_id,
        perception_type=perception_type,
        limit=limit,
    )
    
    return JSONResponse({
        "success": True,
        "history": serialize_for_json(history)
    })


@app.get("/api/perceptions/stats", response_class=JSONResponse)
async def get_perception_stats(
    request: Request,
    svc=Depends(get_memory_service),
):
    """Get perception statistics"""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    if not svc or not hasattr(svc, "perception_engine") or not svc.perception_engine:
        return JSONResponse({
            "success": False,
            "error": "Perception feature not enabled"
        })
    
    user_id = str(user["_id"])
    user_perception = svc.perception_engine.get_user_perception(user_id)
    self_perception = svc.perception_engine.get_self_perception()
    
    stats = {
        "user_perception_exists": user_perception is not None,
        "self_perception_exists": self_perception is not None,
    }
    
    if user_perception:
        stats["user_interaction_count"] = user_perception.get("interaction_count", 0)
        stats["user_last_updated"] = user_perception.get("last_updated")
        stats["user_attributes"] = user_perception.get("attributes", {})
    
    if self_perception:
        stats["self_interaction_count"] = self_perception.get("interaction_count", 0)
        stats["self_last_updated"] = self_perception.get("last_updated")
        stats["self_attributes"] = self_perception.get("attributes", {})
    
    return JSONResponse({
        "success": True,
        "stats": serialize_for_json(stats)
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
