#!/usr/bin/env python3
"""
Memory Retriever Module

Retrieves relevant memories for context-aware analysis.
Demonstrates semantic memory search and customer journey understanding.
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("Parallax.Memory")


class MemoryRetriever:
    """
    Retrieve relevant memories to enhance call analysis.
    
    This module demonstrates:
    - Semantic memory search
    - Context-aware analysis using past call insights
    - Customer journey understanding
    """
    
    def __init__(self, memory_service):
        """
        Initialize the Memory Retriever.
        
        Args:
            memory_service: Mem0MemoryService instance (from mdb-engine)
        """
        self.memory_service = memory_service
    
    async def get_customer_memories(
        self,
        customer_company: str,
        query: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Retrieve memories for a customer.
        
        If query provided, performs semantic search.
        Otherwise returns all memories for the customer.
        
        Args:
            customer_company: Customer company name (used as user_id)
            query: Optional semantic search query
            limit: Maximum number of memories to return
        
        Returns:
            List of memory dictionaries
        """
        if not self.memory_service:
            logger.debug("Memory service not available for retrieval")
            return []
        
        try:
            logger.info(f"Retrieving memories for company: '{customer_company}' (query={query}, limit={limit})")
            
            # Use asyncio.to_thread to run synchronous memory service calls
            import asyncio
            
            # Ensure user_id is a string (mem0 requires this)
            user_id_str = str(customer_company) if customer_company else "unknown"
            logger.info(f"Retrieving memories with user_id: '{user_id_str}' (type: {type(user_id_str).__name__})")
            
            if query:
                # Semantic search for relevant memories
                results = await asyncio.to_thread(
                    self.memory_service.search,
                    query=query,
                    user_id=user_id_str,
                    limit=limit,
                )
                logger.info(f"Search returned {len(results) if results else 0} memories for '{user_id_str}'")
                
                # Normalize search results (like chit_chat pattern)
                if results:
                    normalized_results = []
                    for res in results:
                        if isinstance(res, dict):
                            memory_text = (
                                res.get("memory")
                                or res.get("data", {}).get("memory")
                                or res.get("text")
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
                # Get all memories for customer
                all_memories = await asyncio.to_thread(
                    self.memory_service.get_all,
                    user_id=user_id_str,
                    limit=limit,
                )
                logger.info(f"get_all returned {len(all_memories) if all_memories else 0} memories for '{user_id_str}'")
                
                # Handle different return types
                if all_memories is None:
                    logger.warning(f"get_all returned None for '{user_id_str}'")
                    return []
                
                if isinstance(all_memories, list):
                    # Normalize memory format (like chit_chat pattern)
                    normalized_memories = []
                    for mem in all_memories:
                        if isinstance(mem, dict):
                            # Extract memory text from various possible fields (like chit_chat)
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
                    
                    logger.info(f"Normalized {len(normalized_memories)} memories from {len(all_memories)} raw results")
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
                
                logger.warning(f"Unexpected memory format: {type(all_memories)}")
                return []
                
        except Exception as e:
            logger.error(f"Failed to retrieve memories for '{customer_company}': {e}", exc_info=True)
            return []
    
    async def get_context_for_call(
        self,
        customer_company: str,
        call_type: str,
        products_mentioned: List[str],
    ) -> Dict[str, Any]:
        """
        Get relevant memories to provide context for call analysis.
        
        Returns:
            Dict with memory context organized by category
        """
        if not self.memory_service:
            return {}
        
        try:
            context = {
                "customer_preferences": [],
                "previous_pain_points": [],
                "deal_history": [],
                "product_interests": [],
            }
            
            # Search for customer preferences
            preferences = self.memory_service.search(
                query=f"Customer {customer_company} preferences, likes, and interests",
                user_id=customer_company,
                limit=5,
            )
            context["customer_preferences"] = preferences or []
            
            # Search for pain points
            pain_points = self.memory_service.search(
                query=f"Pain points and challenges for {customer_company}",
                user_id=customer_company,
                limit=5,
            )
            context["previous_pain_points"] = pain_points or []
            
            # Search for deal history
            deals = self.memory_service.search(
                query=f"Deal value, pricing discussions, and purchase decisions for {customer_company}",
                user_id=customer_company,
                limit=5,
            )
            context["deal_history"] = deals or []
            
            # Search for product interests
            if products_mentioned:
                products_query = f"Interest in {', '.join(products_mentioned[:3])} for {customer_company}"
                products = self.memory_service.search(
                    query=products_query,
                    user_id=customer_company,
                    limit=5,
                )
                context["product_interests"] = products or []
            
            return context
            
        except Exception as e:
            logger.warning(f"Failed to retrieve memory context: {e}", exc_info=True)
            return {}
