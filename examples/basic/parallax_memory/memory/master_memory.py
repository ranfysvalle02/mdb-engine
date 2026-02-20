#!/usr/bin/env python3
"""
Master Memory Manager Module

Manages global master-memory for cross-call intelligence across all customers.
Aggregates insights from individual calls into a single master-memory store.
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("Parallax.MasterMemory")


class MasterMemoryManager:
    """
    Manages global master-memory for cross-call intelligence.
    
    This module:
    1. Stores aggregated insights from each call analysis
    2. Retrieves master-memory for cross-call intelligence
    3. Provides semantic search across all customer insights
    4. Enables pattern detection across customers
    """
    
    def __init__(self, memory_service, app_slug: str = "parallax_memory"):
        """
        Initialize the Master Memory Manager.
        
        Args:
            memory_service: Mem0MemoryService instance (from mdb-engine)
            app_slug: App slug for logging
        """
        self.memory_service = memory_service
        self.app_slug = app_slug
        self.master_user_id = "master"  # Single user_id for all master-memory entries
    
    async def add_call_insights(
        self,
        call_id: str,
        customer_company: str,
        lens_insights: Dict[str, Dict[str, Any]],
        transcript_summary: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Add insights from a call analysis to master-memory.
        
        Args:
            call_id: Call identifier
            customer_company: Customer company name
            lens_insights: Analysis results from SALES, MARKETING, PRODUCT lenses
            transcript_summary: Optional summary of the transcript
            metadata: Optional additional metadata
            
        Returns:
            List of memory events if successful, None otherwise
        """
        if not self.memory_service:
            logger.warning("⚠️ Memory service not available for master-memory, skipping")
            return None
        
        try:
            # Build a summary message from lens insights
            insights_parts = []
            
            # Sales insights
            if lens_insights.get("sales"):
                sales = lens_insights["sales"]
                sales_summary = f"Sales: {sales.get('key_insight', '')}"
                if sales.get("deal_value"):
                    sales_summary += f" Deal value: {sales['deal_value']}"
                if sales.get("pain_points"):
                    sales_summary += f" Pain points: {', '.join(sales['pain_points'][:3])}"
                insights_parts.append(sales_summary)
            
            # Marketing insights
            if lens_insights.get("marketing"):
                marketing = lens_insights["marketing"]
                marketing_summary = f"Marketing: {marketing.get('key_insight', '')}"
                if marketing.get("customer_sentiment"):
                    marketing_summary += f" Sentiment: {marketing['customer_sentiment']}"
                insights_parts.append(marketing_summary)
            
            # Product insights
            if lens_insights.get("product"):
                product = lens_insights["product"]
                product_summary = f"Product: {product.get('key_insight', '')}"
                if product.get("feature_requests"):
                    product_summary += f" Feature requests: {', '.join(product['feature_requests'][:3])}"
                insights_parts.append(product_summary)
            
            # Combine insights into a message
            insights_text = "\n".join(insights_parts)
            if transcript_summary:
                insights_text = f"{transcript_summary}\n\n{insights_text}"
            
            # Format as natural conversation for mem0 to extract memories
            # mem0 works best with actual conversational content that contains facts and insights
            # Use transcript content as the primary source for memory extraction
            if transcript_summary and len(transcript_summary) > 200:
                # Use transcript as the main content - mem0 will extract memories from it
                # Format as a natural conversation about the call
                transcript_preview = transcript_summary[:3000]  # Use substantial context
                
                # Create conversation that mem0 can extract memories from
                # User describes the call content
                user_content = f"In a call with {customer_company}, here's what was discussed:\n\n{transcript_preview}"
                
                # Assistant summarizes and extracts insights - this helps mem0 understand what to remember
                if insights_text:
                    assistant_content = f"Based on this call with {customer_company}, here are the key insights:\n\n{insights_text}\n\nThis call reveals important information about {customer_company}'s needs, preferences, challenges, and business context that should be remembered for future interactions."
                else:
                    assistant_content = f"This call with {customer_company} provides valuable context about their business, needs, and preferences that should be remembered."
            else:
                # Fallback: create conversation from insights if no transcript
                user_content = f"I analyzed a call with {customer_company}. Here's what I found:\n{insights_text}"
                assistant_content = f"From this call with {customer_company}, I learned:\n{insights_text}\n\nThis information about {customer_company}'s needs, preferences, and business context is important to remember."
            
            messages = [
                {"role": "user", "content": user_content},
                {"role": "assistant", "content": assistant_content}
            ]
            
            # Build metadata
            memory_metadata = {
                "call_id": call_id,
                "customer_company": customer_company,
                "source": "call_analysis",
                "lens_types": list(lens_insights.keys()),
                **(metadata or {}),
            }
            
            logger.info(
                f"🔵 STORING MASTER-MEMORY - user_id={self.master_user_id}, "
                f"call_id={call_id}, company={customer_company}",
                extra={
                    "user_id": self.master_user_id,
                    "call_id": call_id,
                    "customer_company": customer_company,
                },
            )
            
            # Store in master-memory
            # Use infer=True to ensure mem0 extracts memories from the conversation
            result = await asyncio.to_thread(
                self.memory_service.add,
                messages=messages,
                user_id=self.master_user_id,
                metadata=memory_metadata,
                infer=True,  # Explicitly enable inference for memory extraction
            )
            
            memory_count = len(result) if result and isinstance(result, list) else 0
            logger.info(
                f"✅ Master-memory updated: {memory_count} memories added for call {call_id}"
            )
            
            return result
            
        except Exception as e:
            logger.warning(f"Failed to add call insights to master-memory: {e}", exc_info=True)
            return None
    
    async def get_master_context(
        self,
        query: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve relevant master-memory entries.
        
        Args:
            query: Optional semantic search query
            limit: Maximum number of memories to return
            
        Returns:
            List of memory dictionaries
        """
        if not self.memory_service:
            logger.debug("Memory service not available for master-memory retrieval")
            return []
        
        try:
            import asyncio
            
            if query:
                # Semantic search for relevant memories
                results = await asyncio.to_thread(
                    self.memory_service.search,
                    query=query,
                    user_id=self.master_user_id,
                    limit=limit,
                )
                logger.info(f"Master-memory search returned {len(results) if results else 0} memories")
                
                # Normalize search results
                if results:
                    normalized_results = []
                    for res in results:
                        if isinstance(res, dict):
                            memory_text = (
                                res.get("memory")
                                or res.get("data", {}).get("memory", "")
                                or res.get("text", "")
                                or str(res)
                            )
                            normalized_results.append({
                                "memory": memory_text,
                                "id": res.get("id") or res.get("_id"),
                                "metadata": res.get("metadata", {}),
                                "score": res.get("score"),
                            })
                        elif isinstance(res, str):
                            normalized_results.append({"memory": res})
                    return normalized_results
                return []
            else:
                # Get all master-memories
                all_memories = await asyncio.to_thread(
                    self.memory_service.get_all,
                    user_id=self.master_user_id,
                    limit=limit,
                )
                logger.info(f"Retrieved {len(all_memories) if all_memories else 0} master-memories")
                
                # Normalize memory format
                if all_memories is None:
                    return []
                
                if isinstance(all_memories, list):
                    normalized_memories = []
                    for mem in all_memories:
                        if isinstance(mem, dict):
                            memory_text = (
                                mem.get("memory")
                                or mem.get("text")
                                or (
                                    mem.get("data", {}).get("memory")
                                    if isinstance(mem.get("data"), dict)
                                    else None
                                )
                                or str(mem)
                            )
                            if memory_text:
                                normalized_memories.append({
                                    "memory": memory_text,
                                    "id": mem.get("id") or mem.get("_id"),
                                    "metadata": mem.get("metadata", {}),
                                })
                        elif isinstance(mem, str):
                            normalized_memories.append({"memory": mem, "id": None, "metadata": {}})
                    
                    return normalized_memories
                
                # If it's a single dict, wrap it in a list
                if isinstance(all_memories, dict):
                    memory_text = (
                        all_memories.get("memory")
                        or all_memories.get("text")
                        or (
                            all_memories.get("data", {}).get("memory")
                            if isinstance(all_memories.get("data"), dict)
                            else None
                        )
                        or str(all_memories)
                    )
                    if memory_text:
                        return [{
                            "memory": memory_text,
                            "id": all_memories.get("id") or all_memories.get("_id"),
                            "metadata": all_memories.get("metadata", {}),
                        }]
                    return [all_memories]
                
                logger.warning(f"Unexpected master-memory format: {type(all_memories)}")
                return []
                
        except Exception as e:
            logger.error(f"Failed to retrieve master-memory: {e}", exc_info=True)
            return []
    
    async def get_cross_customer_patterns(
        self,
        pattern_query: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Get patterns detected across customers from master-memory.
        
        Args:
            pattern_query: Optional query to search for specific patterns
            limit: Maximum number of patterns to return
            
        Returns:
            List of pattern dictionaries with metadata
        """
        if not pattern_query:
            pattern_query = "patterns, trends, common themes across customers"
        
        return await self.get_master_context(query=pattern_query, limit=limit)
    
    async def search_by_customer(
        self,
        customer_company: str,
        query: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Search master-memory for insights related to a specific customer.
        
        Args:
            customer_company: Customer company name
            query: Optional additional search query
            limit: Maximum number of memories to return
            
        Returns:
            List of memory dictionaries for the customer
        """
        search_query = f"{customer_company}"
        if query:
            search_query = f"{customer_company} {query}"
        
        memories = await self.get_master_context(query=search_query, limit=limit)
        
        # Filter by customer_company in metadata for exact match
        filtered = []
        for mem in memories:
            metadata = mem.get("metadata", {})
            if metadata.get("customer_company") == customer_company:
                filtered.append(mem)
        
        return filtered if filtered else memories[:limit]
