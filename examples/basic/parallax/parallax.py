#!/usr/bin/env python3
"""
Parallax Engine - Call Transcript Analysis Tool

The Parallax Engine orchestrates concurrent analysis of call transcripts from three business perspectives:
1. SALES: Sales opportunities, objections, closing signals, deal value
2. MARKETING: Messaging, positioning, campaign insights, customer sentiment
3. PRODUCT: Feature requests, pain points, product-market fit, use cases
"""
import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Setup logger first
logger = logging.getLogger("Parallax")

# OpenAI imports
try:
    from openai import AzureOpenAI, OpenAI

    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    logger.warning("OpenAI SDK not available")

from schema_generator import get_default_lens_configs
from schemas import ParallaxReport

# Import modules - use relative imports since we're in the same package
from analysis.lens_analyzer import LensAnalyzer
from memory.memory_extractor import MemoryExtractor
from memory.memory_retriever import MemoryRetriever
from memory.intelligence_orchestrator import IntelligenceOrchestrator
from rag.snippet_extractor import SnippetExtractor
from rag.transcript_indexer import TranscriptIndexer

# Default watchlist for call filtering (can be customized)
WATCHLIST = ["product", "feature", "integration", "platform"]


class ParallaxEngine:
    """
    The Parallax Engine analyzes call transcripts from SALES, MARKETING, and PRODUCT perspectives.

    Architecture:
    1. Load call transcripts from CALLS.json
    2. Index transcripts in MongoDB with embeddings for vector search
    3. Fan-out to three concurrent agents (SALES, MARKETING, PRODUCT)
    4. Aggregate results into ParallaxReport
    5. Store in MongoDB for dashboard visualization
    """

    def __init__(
        self,
        openai_client,
        db,
        embedding_service=None,
        memory_service=None,
        watchlist: Optional[List[str]] = None,
        deployment_name: str = "gpt-4o",
        calls_file: Optional[str] = None,
        app_slug: str = "parallax",
    ):
        """
        Initialize the Parallax Engine.

        Args:
            openai_client: AzureOpenAI or OpenAI client instance
            db: Scoped MongoDB database instance
            embedding_service: Optional EmbeddingService for vector search
            memory_service: Optional Mem0MemoryService for cross-call intelligence
            watchlist: Optional list of keywords to filter calls (defaults to WATCHLIST)
            deployment_name: Model deployment name (for Azure) or model name (for OpenAI)
            calls_file: Path to CALLS.json file (defaults to CALLS.json in same directory)
            app_slug: App slug (for vector index naming)
        """
        self.openai_client = openai_client
        self.db = db
        self.embedding_service = embedding_service
        self.memory_service = memory_service
        self.deployment_name = deployment_name
        self.watchlist = watchlist or WATCHLIST
        self.scan_limit = 3  # Default: 3 calls for simple, powerful demo
        self.temperature = 1.0  # Strict adherence to facts for Parallax
        self.app_slug = app_slug

        # Initialize specialized modules
        self.transcript_indexer = TranscriptIndexer(
            db=db,
            embedding_service=embedding_service,
            calls_file=calls_file,
            app_slug=app_slug,
        )
        self.lens_analyzer = LensAnalyzer(
            openai_client=openai_client,
            db=db,
            deployment_name=deployment_name,
            temperature=self.temperature,
        )
        self.snippet_extractor = SnippetExtractor(
            openai_client=openai_client,
            embedding_service=embedding_service,
            db=db,
            app_slug=app_slug,
            deployment_name=deployment_name,
        )
        self.memory_extractor = MemoryExtractor(
            memory_service=memory_service,
            app_slug=app_slug,
        )
        self.memory_retriever = MemoryRetriever(memory_service=memory_service)
        
        # Initialize Intelligence Orchestrator for cross-call intelligence
        self.intelligence_orchestrator = None
        if openai_client:
            try:
                self.intelligence_orchestrator = IntelligenceOrchestrator(
                    db=db,
                    openai_client=openai_client,
                    memory_service=memory_service,
                    deployment_name=deployment_name,
                    temperature=self.temperature,
                )
                logger.info("Intelligence Orchestrator initialized for longitudinal AI")
            except Exception as e:
                logger.warning(f"Failed to initialize Intelligence Orchestrator: {e}")

        if not OPENAI_AVAILABLE:
            raise RuntimeError("OpenAI SDK required for Parallax Engine")

        logger.info(f"Parallax Engine initialized with deployment: {deployment_name}")

    def load_transcripts(self) -> List[Dict[str, Any]]:
        """
        Load call transcripts from CALLS.json file.

        Delegates to TranscriptIndexer module.

        Returns:
            List of call transcript dictionaries
        """
        return self.transcript_indexer.load_transcripts()

    async def index_transcripts(self, progress_callback=None) -> int:
        """
        Index call transcripts in MongoDB with embeddings for vector search.
        
        Delegates to TranscriptIndexer module.

        Args:
            progress_callback: Optional async callback for progress updates

        Returns:
            Number of transcripts indexed
        """
        return await self.transcript_indexer.index_transcripts(progress_callback=progress_callback)

    async def _load_watchlist_config(self):
        """Load watchlist configuration from database"""
        try:
            config = await self.db.watchlist_config.find_one({"config_type": "watchlist"})
            if config:
                if config.get("keywords"):
                    self.watchlist = config["keywords"]
                    logger.info(f"Loaded watchlist from config: {self.watchlist}")
                if config.get("scan_limit"):
                    self.scan_limit = config["scan_limit"]
                    logger.info(f"Loaded scan limit from config: {self.scan_limit}")
        except (AttributeError, RuntimeError, ConnectionError, ValueError, TypeError, KeyError):
            logger.debug("Could not load watchlist config, using default", exc_info=True)

    async def update_watchlist(
        self,
        keywords: List[str],
        scan_limit: Optional[int] = None,
    ) -> bool:
        """Update the watchlist configuration"""
        try:
            update_data = {"keywords": keywords, "updated_at": datetime.utcnow()}
            if scan_limit is not None:
                update_data["scan_limit"] = scan_limit
                self.scan_limit = scan_limit

            await self.db.watchlist_config.update_one(
                {"config_type": "watchlist"}, {"$set": update_data}, upsert=True
            )
            self.watchlist = keywords
            logger.info(f"Updated watchlist: {keywords}, scan_limit: {self.scan_limit}")
            return True
        except (AttributeError, RuntimeError, ConnectionError, ValueError, TypeError):
            logger.error("Failed to update watchlist", exc_info=True)
            return False

    async def get_scan_limit(self) -> int:
        """Get current scan limit"""
        await self._load_watchlist_config()
        return self.scan_limit

    async def get_watchlist(self) -> List[str]:
        """Get current watchlist"""
        await self._load_watchlist_config()
        return self.watchlist

    async def analyze_transcripts(self, progress_callback=None) -> List[ParallaxReport]:
        """
        Analyze call transcripts through SALES, MARKETING, and PRODUCT lenses.

        Args:
            progress_callback: Optional async callback for progress updates

        Returns:
            List of ParallaxReport instances
        """
        await self._load_watchlist_config()

        # Get indexed transcripts from database
        transcripts = await self.db.call_transcripts.find({}).to_list(length=self.scan_limit)
        logger.info(f"Found {len(transcripts)} call transcripts to analyze")

        reports = []
        new_calls = 0
        cached_calls = 0

        for transcript_doc in transcripts:
            call_id = transcript_doc.get("call_id")
            if not call_id:
                continue

            # Check if already analyzed
            cached = await self.db.parallax_reports.find_one({"call_id": call_id})
            if cached:
                cached_calls += 1
                logger.debug(f"Skipping cached call: {call_id}")
                continue

            new_calls += 1

            call_type = transcript_doc.get("call_type", "unknown")
            participants = transcript_doc.get("participants", {})
            transcript_text = transcript_doc.get("transcript", "")
            metadata = transcript_doc.get("metadata", {})
            timestamp = transcript_doc.get("timestamp")

            # Extract participant info
            customer_name = participants.get("customer", "Unknown")
            customer_company = participants.get("company", "Unknown")
            customer_role = participants.get("role", "Unknown")

            logger.info(
                f"Analyzing call: {call_id} ({call_type}) - {customer_name} at {customer_company}"
            )

            # Retrieve memory context for this customer (optional enhancement)
            memory_context = {}
            if self.memory_retriever:
                try:
                    products_mentioned = metadata.get("product_mentioned", [])
                    memory_context = await self.memory_retriever.get_context_for_call(
                        customer_company, call_type, products_mentioned
                    )
                except Exception as e:
                    logger.debug(f"Failed to retrieve memory context: {e}")
            
            # Fan-out to three lenses simultaneously
            task_sales = self.lens_analyzer.generate_viewpoint(
                call_id, call_type, participants, transcript_text, metadata, "SALES"
            )
            task_marketing = self.lens_analyzer.generate_viewpoint(
                call_id, call_type, participants, transcript_text, metadata, "MARKETING"
            )
            task_product = self.lens_analyzer.generate_viewpoint(
                call_id, call_type, participants, transcript_text, metadata, "PRODUCT"
            )

            # Wait for all three views
            sales_view, marketing_view, product_view = await asyncio.gather(
                task_sales, task_marketing, task_product
            )

            # Convert Pydantic models to dicts
            sales_dict = {}
            if sales_view:
                if hasattr(sales_view, "model_dump"):
                    sales_dict = sales_view.model_dump()
                elif hasattr(sales_view, "dict"):
                    sales_dict = sales_view.dict()
                else:
                    sales_dict = sales_view if isinstance(sales_view, dict) else {}

            marketing_dict = {}
            if marketing_view:
                if hasattr(marketing_view, "model_dump"):
                    marketing_dict = marketing_view.model_dump()
                elif hasattr(marketing_view, "dict"):
                    marketing_dict = marketing_view.dict()
            else:
                    marketing_dict = marketing_view if isinstance(marketing_view, dict) else {}

            product_dict = {}
            if product_view:
                if hasattr(product_view, "model_dump"):
                    product_dict = product_view.model_dump()
                elif hasattr(product_view, "dict"):
                    product_dict = product_view.dict()
                else:
                    product_dict = product_view if isinstance(product_view, dict) else {}

            # Only create report if we have at least one valid viewpoint
            if sales_dict or marketing_dict or product_dict:
                # Extract matched keywords from metadata
                matched_keywords = []
                products_mentioned = metadata.get("product_mentioned", [])
                for keyword in self.watchlist:
                    if any(
                        keyword.lower() in str(p).lower() or keyword.lower() in transcript_text.lower()
                        for p in products_mentioned
                    ):
                        matched_keywords.append(keyword)

                # Progress: Step 5 - Extracting relevant snippets
                if progress_callback:
                    await progress_callback({
                        "step": 5,
                        "stage": "extracting_snippets",
                        "message": "Extracting relevant snippets using RAG",
                        "call_id": call_id
                    })
                
                # Extract relevant snippets for each lens using RAG (in parallel)
                snippet_tasks = []
                if sales_dict:
                    snippet_tasks.append(("SALES", self.snippet_extractor.extract_snippets(transcript_text, call_id, "SALES", sales_dict)))
                if marketing_dict:
                    snippet_tasks.append(("MARKETING", self.snippet_extractor.extract_snippets(transcript_text, call_id, "MARKETING", marketing_dict)))
                if product_dict:
                    snippet_tasks.append(("PRODUCT", self.snippet_extractor.extract_snippets(transcript_text, call_id, "PRODUCT", product_dict)))
                
                # Wait for all snippet extractions
                relevant_snippets = {}
                if snippet_tasks:
                    results = await asyncio.gather(*[task[1] for task in snippet_tasks], return_exceptions=True)
                    for (lens_name, _), snippets in zip(snippet_tasks, results):
                        if isinstance(snippets, Exception):
                            logger.error(f"❌ Failed to extract snippets for {lens_name}: {snippets}", exc_info=snippets)
                        elif snippets:
                            relevant_snippets[lens_name] = snippets
                            logger.info(f"✅ Extracted {len(snippets)} snippets for {lens_name}")
                logger.info(f"✅ RAG snippet extraction complete: {len(relevant_snippets)} lenses have snippets")
                
                # Progress: Step 6 - Snippets extracted
                if progress_callback:
                    await progress_callback({
                        "step": 6,
                        "stage": "snippets_extracted",
                        "message": f"Extracted snippets for {len(relevant_snippets)} lenses",
                        "call_id": call_id
                    })
                
                # Extract and store memories from this call (PER-CALL memories)
                lens_insights = {
                    "sales": sales_dict,
                    "marketing": marketing_dict,
                    "product": product_dict,
                }
                # Extract and store memories (PER-CALL memories)
                if self.memory_extractor:
                    try:
                        # Progress: Step 7 - Extracting memories
                        if progress_callback:
                            await progress_callback({
                                "step": 7,
                                "stage": "extracting_memories",
                                "message": "Parsing transcript and extracting memories",
                                "call_id": call_id
                            })
                        
                        logger.info(f"🔵 CALLING memory_extractor.extract_and_store for call_id: {call_id}")
                        try:
                            memories_result = await self.memory_extractor.extract_and_store(
                                call_id, transcript_text, participants, lens_insights, metadata
                            )
                            memory_count = len(memories_result) if memories_result and isinstance(memories_result, list) else 0
                            logger.info(f"✅ Memory extraction complete for call {call_id}: {memory_count} memories extracted")
                        except Exception as e:
                            # Import debug utilities if available
                            try:
                                from debug import ErrorTracker
                                ErrorTracker.log_error(e, context={"operation": "memory_extraction"}, call_id=call_id, stage="extracting_memories")
                            except ImportError:
                                pass
                            logger.error(f"❌ Memory extraction failed for call {call_id}: {e}", exc_info=True)
                            raise
                        
                        # Progress: Step 8 - Memories stored
                        if progress_callback:
                            await progress_callback({
                                "step": 8,
                                "stage": "memories_stored",
                                "message": f"Stored {memory_count} memories for this call",
                                "call_id": call_id,
                                "memory_count": memory_count
                            })
                    except Exception as e:
                        logger.error(f"❌ Failed to extract memories for call {call_id}: {e}", exc_info=True)
                else:
                    logger.debug("Memory service not available, skipping memory extraction")
                    # Still send progress update even if skipping
                    if progress_callback:
                        await progress_callback({
                            "step": 7,
                            "stage": "extracting_memories",
                            "message": "Memory service not available, skipping",
                            "call_id": call_id
                        })
                        await progress_callback({
                            "step": 8,
                            "stage": "memories_stored",
                            "message": "Skipped (memory service not enabled)",
                            "call_id": call_id,
                            "memory_count": 0
                        })

                # Create report (using call_id instead of repo_id)
                report_dict_data = {
                    "call_id": call_id,  # Use call_id instead of repo_id
                    "repo_id": call_id,  # Keep for backward compatibility
                    "repo_name": f"{customer_company} - {call_type}",
                    "repo_owner": customer_name,
                    "customer_company": customer_company,  # Store company name directly for memory retrieval
                    "stars": 0,  # Not applicable for calls
                    "file_found": "transcript",
                    "original_title": f"Call with {customer_name} ({customer_company})",
                    "url": "",  # Not applicable for calls
                    "sales": sales_dict if sales_dict else None,
                    "marketing": marketing_dict if marketing_dict else None,
                    "product": product_dict if product_dict else None,
                    "relevance": None,  # Not used for calls
                    "timestamp": timestamp or datetime.utcnow().isoformat(),
                    "matched_keywords": matched_keywords,
                    "relevant_snippets": relevant_snippets if relevant_snippets else None,
                }
                
                # Create Pydantic model (will ignore extra fields like customer_company)
                report = ParallaxReport(**{k: v for k, v in report_dict_data.items() if k != "customer_company"})

                # Store in MongoDB
                report_dict = (
                    report.model_dump() if hasattr(report, "model_dump") else report.dict()
                )
                # Add customer_company to stored dict for easy retrieval
                report_dict["customer_company"] = customer_company
                await self.db.parallax_reports.insert_one(report_dict)
                reports.append(report)

                logger.info(
                    f"Parallax report stored for: {call_id} (sales: {bool(sales_dict)}, marketing: {bool(marketing_dict)}, product: {bool(product_dict)})"
                )
                
                # Progress: Step 9 - Report stored
                if progress_callback:
                    await progress_callback({
                        "step": 9,
                        "stage": "complete",
                        "message": "Analysis complete! Report stored.",
                        "call_id": call_id
                    })
                
                # Run Intelligence Orchestrator synthesis (Longitudinal AI)
                if self.intelligence_orchestrator:
                    try:
                        call_data = {
                            "call_id": call_id,
                            "report": report_dict,
                            "transcript": transcript_text,
                            "participants": participants,
                            "metadata": metadata,
                            "timestamp": timestamp or datetime.utcnow().isoformat(),
                        }
                        # Run synthesis asynchronously (don't block report generation)
                        asyncio.create_task(
                            self.intelligence_orchestrator.synthesize_after_call(
                                call_id=call_id,
                                customer_company=customer_company,
                                call_data=call_data,
                            )
                        )
                        logger.info(f"🔮 Intelligence synthesis triggered for call {call_id}")
                    except Exception as e:
                        logger.warning(f"Failed to trigger intelligence synthesis: {e}")

                # Send progress update
                if progress_callback:
                    try:
                        await progress_callback(
                            {
                                "type": "report_complete",
                                "call_id": call_id,
                                "repo_id": call_id,  # For backward compatibility
                                "repo_name": f"{customer_company} - {call_type}",
                                "total_processed": new_calls,
                                "total_found": len(transcripts),
                            }
                        )
                    except Exception:
                        pass

        logger.info(
            f"Analysis complete: {new_calls} new calls analyzed, {cached_calls} cached, {len(reports)} reports generated"
        )

        if progress_callback:
            try:
                await progress_callback(
                    {
                        "type": "analysis_complete",
                        "total_reports": len(reports),
                        "new_calls": new_calls,
                        "cached_calls": cached_calls,
                    }
                )
            except Exception:
                pass

        return reports

    async def analyze_single_transcript(
        self,
        call_id: str,
        progress_callback=None,
    ) -> Optional[ParallaxReport]:
        """
        Analyze a single call transcript.
        
        Args:
            call_id: The call identifier to analyze
            progress_callback: Optional async callback for progress updates
        
        Returns:
            ParallaxReport instance if successful, None otherwise
        """
        # Get transcript from database (non-chunk document)
        transcript_doc = await self.db.call_transcripts.find_one({
            "call_id": call_id,
            "chunk_index": {"$exists": False}  # Get the main transcript document, not chunks
        })
        
        if not transcript_doc:
            logger.warning(f"Transcript not found for call_id: {call_id}")
            return None
        
        # Check if already analyzed
        cached = await self.db.parallax_reports.find_one({"call_id": call_id})
        if cached:
            logger.debug(f"Call {call_id} already analyzed, returning cached report")
            # Return cached report as ParallaxReport
            cached.pop("_id", None)
            try:
                return ParallaxReport(**cached)
            except Exception as e:
                logger.warning(f"Failed to parse cached report: {e}")
                return None
        
        call_type = transcript_doc.get("call_type", "unknown")
        participants = transcript_doc.get("participants", {})
        transcript_text = transcript_doc.get("transcript", "")
        metadata = transcript_doc.get("metadata", {})
        timestamp = transcript_doc.get("timestamp")
        
        # Extract participant info
        customer_name = participants.get("customer", "Unknown")
        customer_company = participants.get("company", "Unknown")
        customer_role = participants.get("role", "Unknown")
        
        logger.info(
            f"Analyzing single call: {call_id} ({call_type}) - {customer_name} at {customer_company}"
        )
        
        # Progress: Step 1 - Transcript loaded
        if progress_callback:
            await progress_callback({
                "step": 1,
                "stage": "transcript_loaded",
                "message": "Transcript loaded and parsed",
                "call_id": call_id
            })
        
        # Retrieve memory context for this customer (optional enhancement)
        memory_context = {}
        if progress_callback:
            await progress_callback({
                "step": 2,
                "stage": "retrieving_memory_context",
                "message": "Retrieving relevant memories from past calls",
                "call_id": call_id
            })
        
        if self.memory_retriever:
            try:
                products_mentioned = metadata.get("product_mentioned", [])
                memory_context = await self.memory_retriever.get_context_for_call(
                    customer_company, call_type, products_mentioned
                )
            except Exception as e:
                logger.debug(f"Failed to retrieve memory context: {e}")
        else:
            logger.debug("Memory retriever not available, skipping memory context retrieval")
        
        # Progress: Step 3 - Starting lens analysis
        if progress_callback:
            await progress_callback({
                "step": 3,
                "stage": "lens_analysis_started",
                "message": "Analyzing through SALES, MARKETING, and PRODUCT lenses",
                "call_id": call_id
            })
        
        # Fan-out to three lenses simultaneously
        task_sales = self.lens_analyzer.generate_viewpoint(
            call_id, call_type, participants, transcript_text, metadata, "SALES"
        )
        task_marketing = self.lens_analyzer.generate_viewpoint(
            call_id, call_type, participants, transcript_text, metadata, "MARKETING"
        )
        task_product = self.lens_analyzer.generate_viewpoint(
            call_id, call_type, participants, transcript_text, metadata, "PRODUCT"
        )
        
        # Wait for all three views
        sales_view, marketing_view, product_view = await asyncio.gather(
            task_sales, task_marketing, task_product
        )
        
        # Progress: Step 4 - Lens analysis complete
        if progress_callback:
            await progress_callback({
                "step": 4,
                "stage": "lens_analysis_complete",
                "message": "Multi-lens analysis complete",
                "call_id": call_id
            })
        
        # Convert Pydantic models to dicts
        sales_dict = {}
        if sales_view:
            if hasattr(sales_view, "model_dump"):
                sales_dict = sales_view.model_dump()
            elif hasattr(sales_view, "dict"):
                sales_dict = sales_view.dict()
            else:
                sales_dict = sales_view if isinstance(sales_view, dict) else {}
        
        marketing_dict = {}
        if marketing_view:
            if hasattr(marketing_view, "model_dump"):
                marketing_dict = marketing_view.model_dump()
            elif hasattr(marketing_view, "dict"):
                marketing_dict = marketing_view.dict()
            else:
                marketing_dict = marketing_view if isinstance(marketing_view, dict) else {}
        
        product_dict = {}
        if product_view:
            if hasattr(product_view, "model_dump"):
                product_dict = product_view.model_dump()
            elif hasattr(product_view, "dict"):
                product_dict = product_view.dict()
            else:
                product_dict = product_view if isinstance(product_view, dict) else {}
        
        # Only create report if we have at least one valid viewpoint
        if not (sales_dict or marketing_dict or product_dict):
            logger.warning(f"No valid viewpoints generated for call {call_id}")
            return None
        
        # Extract matched keywords from metadata
        matched_keywords = []
        products_mentioned = metadata.get("product_mentioned", [])
        for keyword in self.watchlist:
            if any(
                keyword.lower() in str(p).lower() or keyword.lower() in transcript_text.lower()
                for p in products_mentioned
            ):
                matched_keywords.append(keyword)
        
        # Progress: Step 5 - Extracting relevant snippets
        if progress_callback:
            await progress_callback({
                "step": 5,
                "stage": "extracting_snippets",
                "message": "Extracting relevant snippets using RAG",
                "call_id": call_id
            })
        
        # Extract relevant snippets for each lens using RAG (in parallel)
        snippet_tasks = []
        if sales_dict:
            snippet_tasks.append(("SALES", self.snippet_extractor.extract_snippets(transcript_text, call_id, "SALES", sales_dict)))
        if marketing_dict:
            snippet_tasks.append(("MARKETING", self.snippet_extractor.extract_snippets(transcript_text, call_id, "MARKETING", marketing_dict)))
        if product_dict:
            snippet_tasks.append(("PRODUCT", self.snippet_extractor.extract_snippets(transcript_text, call_id, "PRODUCT", product_dict)))
        
        # Wait for all snippet extractions
        relevant_snippets = {}
        if snippet_tasks:
            results = await asyncio.gather(*[task[1] for task in snippet_tasks], return_exceptions=True)
            for (lens_name, _), snippets in zip(snippet_tasks, results):
                if isinstance(snippets, Exception):
                    # Track error if debug available
                    try:
                        from debug import ErrorTracker
                        ErrorTracker.log_error(snippets, context={"lens": lens_name, "operation": "snippet_extraction"}, call_id=call_id, stage="extracting_snippets")
                    except ImportError:
                        pass
                    logger.error(f"❌ Failed to extract snippets for {lens_name}: {snippets}", exc_info=snippets)
                elif snippets:
                    relevant_snippets[lens_name] = snippets
                    logger.info(f"✅ Extracted {len(snippets)} snippets for {lens_name}")
        logger.info(f"✅ RAG snippet extraction complete: {len(relevant_snippets)} lenses have snippets")
        
        # Progress: Step 6 - Snippets extracted
        if progress_callback:
            await progress_callback({
                "step": 6,
                "stage": "snippets_extracted",
                "message": f"Extracted snippets for {len(relevant_snippets)} lenses",
                "call_id": call_id
            })
        
        # Extract and store memories from this call (PER-CALL memories)
        lens_insights = {
            "sales": sales_dict,
            "marketing": marketing_dict,
            "product": product_dict,
        }
        # Extract and store memories (PER-CALL memories)
        if self.memory_extractor:
            try:
                # Progress: Step 7 - Extracting memories
                if progress_callback:
                    await progress_callback({
                        "step": 7,
                        "stage": "extracting_memories",
                        "message": "Parsing transcript and extracting memories",
                        "call_id": call_id
                    })
                
                logger.info(f"🔵 CALLING memory_extractor.extract_and_store for call_id: {call_id}")
                memories_result = await self.memory_extractor.extract_and_store(
                    call_id, transcript_text, participants, lens_insights, metadata
                )
                memory_count = len(memories_result) if memories_result and isinstance(memories_result, list) else 0
                logger.info(f"✅ Memory extraction complete for call {call_id}: {memory_count} memories extracted")
                
                # Progress: Step 8 - Memories stored
                if progress_callback:
                    await progress_callback({
                        "step": 8,
                        "stage": "memories_stored",
                        "message": f"Stored {memory_count} memories for this call",
                        "call_id": call_id,
                        "memory_count": memory_count
                    })
            except Exception as e:
                logger.error(f"❌ Failed to extract memories for call {call_id}: {e}", exc_info=True)
                # Still send progress update even on error
                if progress_callback:
                    await progress_callback({
                        "step": 8,
                        "stage": "memories_stored",
                        "message": f"Memory extraction failed: {str(e)[:50]}",
                        "call_id": call_id,
                        "memory_count": 0
                    })
        else:
            logger.debug("Memory service not available, skipping memory extraction")
            # Still send progress updates even if skipping
            if progress_callback:
                await progress_callback({
                    "step": 7,
                    "stage": "extracting_memories",
                    "message": "Memory service not available, skipping",
                    "call_id": call_id
                })
                await progress_callback({
                    "step": 8,
                    "stage": "memories_stored",
                    "message": "Skipped (memory service not enabled)",
                    "call_id": call_id,
                    "memory_count": 0
                })
        
        # Create report
        report_dict_data = {
            "call_id": call_id,
            "repo_id": call_id,  # Keep for backward compatibility
            "repo_name": f"{customer_company} - {call_type}",
            "repo_owner": customer_name,
            "customer_company": customer_company,
            "stars": 0,
            "file_found": "transcript",
            "original_title": f"Call with {customer_name} ({customer_company})",
            "url": "",
            "sales": sales_dict if sales_dict else None,
            "marketing": marketing_dict if marketing_dict else None,
            "product": product_dict if product_dict else None,
            "relevance": None,
            "timestamp": timestamp or datetime.utcnow().isoformat(),
            "matched_keywords": matched_keywords,
            "relevant_snippets": relevant_snippets if relevant_snippets else None,
        }
        
        # Create Pydantic model
        report = ParallaxReport(**{k: v for k, v in report_dict_data.items() if k != "customer_company"})
        
        # Store in MongoDB
        report_dict = (
            report.model_dump() if hasattr(report, "model_dump") else report.dict()
        )
        report_dict["customer_company"] = customer_company
        await self.db.parallax_reports.insert_one(report_dict)
        
        logger.info(
            f"Parallax report stored for single call: {call_id} (sales: {bool(sales_dict)}, marketing: {bool(marketing_dict)}, product: {bool(product_dict)})"
        )
        
        # Progress: Step 9 - Report stored
        if progress_callback:
            await progress_callback({
                "step": 9,
                "stage": "complete",
                "message": "Analysis complete! Report stored.",
                "call_id": call_id
            })
        
        # Run Intelligence Orchestrator synthesis (Longitudinal AI)
        if self.intelligence_orchestrator:
            try:
                call_data = {
                    "call_id": call_id,
                    "report": report_dict,
                    "transcript": transcript_text,
                    "participants": participants,
                    "metadata": metadata,
                    "timestamp": timestamp or datetime.utcnow().isoformat(),
                }
                # Run synthesis asynchronously
                await self.intelligence_orchestrator.synthesize_after_call(
                    call_id=call_id,
                    customer_company=customer_company,
                    call_data=call_data,
                )
                logger.info(f"🔮 Intelligence synthesis complete for call {call_id}")
            except Exception as e:
                logger.warning(f"Failed to run intelligence synthesis: {e}")
        
        # Send progress update
        if progress_callback:
            try:
                await progress_callback(
                    {
                        "type": "report_complete",
                        "call_id": call_id,
                        "repo_id": call_id,
                        "repo_name": f"{customer_company} - {call_type}",
                    }
                )
            except Exception:
                pass
        
        return report

