#!/usr/bin/env python3
"""
Intelligence Orchestrator - The "Magic Layer"

This is the core orchestrator that synthesizes cross-call intelligence.
It transforms episodic call data into longitudinal customer relationship intelligence.

Architecture:
1. Ingestion: Receives new call data
2. Extraction: Extracts entities and insights
3. Synthesis: Runs background jobs to detect patterns, contradictions, and trends
4. Storage: Updates customer profiles and relationship health scores
"""
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger("Parallax.Intelligence")


class IntelligenceOrchestrator:
    """
    The Orchestrator - The "Magic Layer" that powers Longitudinal AI.
    
    This orchestrator:
    - Runs synthesis jobs after each call
    - Detects patterns across calls (Sentinel)
    - Creates continuity summaries (Continuity Manager)
    - Detects contradictions (Contradiction Detector)
    - Updates relationship health scores
    """
    
    def __init__(
        self,
        db,
        openai_client,
        memory_service=None,
        master_memory_manager=None,
        deployment_name: str = "gpt-4o",
        temperature: float = 0.3,
    ):
        """
        Initialize the Intelligence Orchestrator.
        
        Args:
            db: MongoDB database instance
            openai_client: OpenAI/AzureOpenAI client for LLM calls
            memory_service: Optional memory service for semantic search
            master_memory_manager: Optional MasterMemoryManager for cross-call intelligence
            deployment_name: Model deployment name
            temperature: LLM temperature for synthesis
        """
        self.db = db
        self.openai_client = openai_client
        self.memory_service = memory_service
        self.master_memory_manager = master_memory_manager
        self.deployment_name = deployment_name
        self.temperature = temperature
        
        # Import specialized components (use relative imports)
        try:
            from .sentinel import Sentinel
            from .continuity_manager import ContinuityManager
            from .contradiction_detector import ContradictionDetector
            from .customer_profile import CustomerProfileManager
        except ImportError:
            # Fallback to absolute imports
            from memory.sentinel import Sentinel
            from memory.continuity_manager import ContinuityManager
            from memory.contradiction_detector import ContradictionDetector
            from memory.customer_profile import CustomerProfileManager
        
        self.sentinel = Sentinel(db, openai_client, deployment_name, temperature)
        self.continuity_manager = ContinuityManager(db, openai_client, deployment_name, temperature)
        self.contradiction_detector = ContradictionDetector(db, openai_client, deployment_name, temperature)
        self.profile_manager = CustomerProfileManager(db, openai_client, deployment_name, temperature)
    
    async def synthesize_after_call(
        self,
        call_id: str,
        customer_company: str,
        call_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Run synthesis job after a new call is analyzed.
        
        This is the main entry point for cross-call intelligence.
        It orchestrates all the "magical" features:
        - Pattern detection (Sentinel)
        - Continuity summary generation
        - Contradiction detection
        - Profile updates
        
        Args:
            call_id: The new call identifier
            customer_company: Customer company name
            call_data: Complete call data including transcript, analysis, metadata
        
        Returns:
            Dict with synthesis results
        """
        logger.info(f"🔮 Starting synthesis for call {call_id} (customer: {customer_company})")
        
        try:
            # Query master-memory for relevant cross-call insights
            master_context = []
            if self.master_memory_manager:
                try:
                    # Get relevant master-memory entries for this customer and cross-customer patterns
                    customer_memories = await self.master_memory_manager.search_by_customer(
                        customer_company, limit=10
                    )
                    cross_patterns = await self.master_memory_manager.get_cross_customer_patterns(limit=5)
                    master_context = customer_memories + cross_patterns
                    logger.info(f"Retrieved {len(master_context)} master-memory entries for synthesis")
                except Exception as e:
                    logger.warning(f"Failed to query master-memory: {e}")
            
            # Get recent calls for this customer (last 5-10 calls)
            recent_calls = await self._get_recent_calls(customer_company, limit=10)
            
            if len(recent_calls) < 2:
                logger.info(f"Insufficient call history for synthesis (only {len(recent_calls)} calls)")
                return {
                    "status": "insufficient_history",
                    "message": "Need at least 2 calls for cross-call intelligence",
                }
            
            # Run all synthesis components in parallel
            synthesis_tasks = [
                self.sentinel.detect_patterns(customer_company, recent_calls, call_data),
                self.contradiction_detector.detect_contradictions(customer_company, recent_calls, call_data),
                self.continuity_manager.generate_summary(customer_company, recent_calls),
            ]
            
            patterns, contradictions, continuity_summary = await asyncio.gather(
                *synthesis_tasks, return_exceptions=True
            )
            
            # Handle exceptions gracefully
            if isinstance(patterns, Exception):
                logger.warning(f"Pattern detection failed: {patterns}")
                patterns = {}
            if isinstance(contradictions, Exception):
                logger.warning(f"Contradiction detection failed: {contradictions}")
                contradictions = []
            if isinstance(continuity_summary, Exception):
                logger.warning(f"Continuity summary generation failed: {continuity_summary}")
                continuity_summary = {}
            
            # Update customer profile with all insights
            profile_update = await self.profile_manager.update_profile(
                customer_company,
                call_data,
                patterns=patterns,
                contradictions=contradictions,
                continuity_summary=continuity_summary,
            )
            
            # Store synthesis results
            synthesis_result = {
                "call_id": call_id,
                "customer_company": customer_company,
                "timestamp": datetime.utcnow().isoformat(),
                "patterns": patterns,
                "contradictions": contradictions,
                "continuity_summary": continuity_summary,
                "profile_update": profile_update,
                "master_memory_context_count": len(master_context),
            }
            
            await self.db.intelligence_synthesis.insert_one(synthesis_result)
            
            # Store synthesis insights in master-memory
            if self.master_memory_manager:
                try:
                    synthesis_summary = f"Synthesis for {customer_company}: Patterns: {len(patterns) if patterns else 0}, "
                    synthesis_summary += f"Contradictions: {len(contradictions) if contradictions else 0}, "
                    synthesis_summary += f"Continuity: {bool(continuity_summary)}"
                    
                    await self.master_memory_manager.add_call_insights(
                        call_id=call_id,
                        customer_company=customer_company,
                        lens_insights={},  # Synthesis insights, not lens insights
                        transcript_summary=synthesis_summary,
                        metadata={
                            "type": "synthesis",
                            "patterns": patterns,
                            "contradictions": contradictions,
                        },
                    )
                    logger.info(f"✅ Synthesis insights stored in master-memory for call {call_id}")
                except Exception as e:
                    logger.warning(f"Failed to store synthesis in master-memory: {e}")
            
            logger.info(f"✅ Synthesis complete for call {call_id}")
            
            return {
                "status": "success",
                "synthesis": synthesis_result,
            }
            
        except Exception as e:
            logger.error(f"❌ Synthesis failed for call {call_id}: {e}", exc_info=True)
            return {
                "status": "error",
                "error": str(e),
            }
    
    async def _get_recent_calls(
        self,
        customer_company: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Get recent calls for a customer.
        
        Args:
            customer_company: Customer company name
            limit: Maximum number of calls to retrieve
        
        Returns:
            List of call documents sorted by timestamp (newest first)
        """
        # Get reports for this customer
        reports = await self.db.parallax_reports.find(
            {"customer_company": customer_company}
        ).sort("timestamp", -1).limit(limit).to_list(length=limit)
        
        # Enrich with full transcript data
        enriched_calls = []
        for report in reports:
            call_id = report.get("call_id")
            if not call_id:
                continue
            
            # Get transcript
            transcript_doc = await self.db.call_transcripts.find_one({
                "call_id": call_id,
                "chunk_index": {"$exists": False}
            })
            
            if transcript_doc:
                enriched_calls.append({
                    "call_id": call_id,
                    "report": report,
                    "transcript": transcript_doc.get("transcript", ""),
                    "participants": transcript_doc.get("participants", {}),
                    "metadata": transcript_doc.get("metadata", {}),
                    "timestamp": report.get("timestamp") or transcript_doc.get("timestamp"),
                })
        
        return enriched_calls
    
    async def get_customer_intelligence(
        self,
        customer_company: str,
    ) -> Dict[str, Any]:
        """
        Get complete intelligence summary for a customer.
        
        This is the "State of the Union" that can be injected before interactions.
        
        Args:
            customer_company: Customer company name
        
        Returns:
            Complete intelligence summary including:
            - Current profile
            - Recent patterns
            - Active contradictions
            - Continuity summary
            - Relationship health score
        """
        try:
            # Get latest profile
            profile = await self.profile_manager.get_profile(customer_company)
            
            # Get latest synthesis
            latest_synthesis = await self.db.intelligence_synthesis.find_one(
                {"customer_company": customer_company},
                sort=[("timestamp", -1)]
            )
            
            # Get recent patterns
            recent_patterns = await self.sentinel.get_recent_patterns(customer_company, limit=5)
            
            # Get active contradictions
            active_contradictions = await self.contradiction_detector.get_active_contradictions(
                customer_company
            )
            
            # Get master-memory context if available
            master_memory_context = []
            if self.master_memory_manager:
                try:
                    master_memory_context = await self.master_memory_manager.search_by_customer(
                        customer_company, limit=10
                    )
                except Exception as e:
                    logger.warning(f"Failed to get master-memory context: {e}")
            
            return {
                "customer_company": customer_company,
                "profile": profile,
                "latest_synthesis": latest_synthesis,
                "recent_patterns": recent_patterns,
                "active_contradictions": active_contradictions,
                "master_memory_context": master_memory_context,
                "generated_at": datetime.utcnow().isoformat(),
            }
            
        except Exception as e:
            logger.error(f"Failed to get customer intelligence: {e}", exc_info=True)
            return {
                "customer_company": customer_company,
                "error": str(e),
            }
