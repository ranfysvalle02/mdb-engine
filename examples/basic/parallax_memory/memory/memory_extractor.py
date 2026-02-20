#!/usr/bin/env python3
"""
Memory Extractor Module

Extracts and stores memories from call transcripts.
Demonstrates mdb-engine's Memory component for cross-call intelligence tracking.
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("Parallax.Memory")


class MemoryExtractor:
    """
    Extract and store memories from call transcripts.
    
    This module demonstrates:
    1. Memory service for intelligent memory extraction
    2. Customer journey tracking across calls
    3. Semantic memory storage with rich metadata
    """
    
    def __init__(self, memory_service, app_slug: str = "parallax_memory"):
        """
        Initialize the Memory Extractor.
        
        Args:
            memory_service: Mem0MemoryService instance (from mdb-engine)
            app_slug: App slug for logging
        """
        self.memory_service = memory_service
        self.app_slug = app_slug
    
    async def extract_and_store(
        self,
        call_id: str,
        transcript: str,
        participants: Dict[str, Any],
        lens_insights: Dict[str, Dict[str, Any]],
        metadata: Dict[str, Any],
    ) -> Optional[List[Dict[str, Any]]]:
        """
        Extract memories from call transcript and store them.
        
        Uses customer company as "user_id" to track memories per customer.
        This enables cross-call intelligence and customer journey tracking.
        
        Args:
            call_id: Call identifier
            transcript: Full call transcript text
            participants: Call participants information
            lens_insights: Analysis results from SALES, MARKETING, PRODUCT lenses
            metadata: Call metadata (call_type, timestamp, etc.)
        
        Returns:
            List of memory events if successful, None otherwise
        """
        if not self.memory_service:
            logger.warning(f"⚠️ Memory service not available for call '{call_id}', skipping memory extraction. Check manifest.json memory_config.enabled")
            return None
        
        try:
            customer_id = participants.get("company", "unknown")
            customer_name = participants.get("customer", "Unknown")
            
            # PER-CALL MEMORIES: Use call_id as user_id so each call has its own memories
            # This ensures memories are always created per-call, not aggregated by company
            user_id_str = f"call_{call_id}"  # Use call_id as user_id for per-call memories
            
            # Extract lens insights for metadata (always needed)
            sales_insight = lens_insights.get("sales", {}).get("key_insight", "")
            marketing_insight = lens_insights.get("marketing", {}).get("key_insight", "")
            product_insight = lens_insights.get("product", {}).get("key_insight", "")
            
            # Format transcript as conversation turns (like chit_chat pattern)
            # CRITICAL: mem0 needs actual conversation turns (user/assistant pairs) to extract memories
            # Parse transcript into proper conversation format where Customer = user, Agent = assistant
            messages = self._parse_transcript_to_conversation(transcript)
            
            logger.info(f"Parsed transcript into {len(messages)} conversation turns for mem0")
            if messages:
                logger.debug(f"Sample turns: {messages[:2]}")
            
            # If parsing failed or produced no messages, fall back to simple format
            if not messages or len(messages) == 0:
                logger.warning(f"Failed to parse transcript into conversation turns, using fallback format")
                # Fallback: Use transcript as user message with a helpful assistant response
                assistant_summary = f"I understand. Let me help you with that. "
                if sales_insight:
                    assistant_summary += f"Sales: {sales_insight[:200]}. "
                if marketing_insight:
                    assistant_summary += f"Marketing: {marketing_insight[:200]}. "
                if product_insight:
                    assistant_summary += f"Product: {product_insight[:200]}."
                
                messages = [
                    {"role": "user", "content": transcript},
                    {"role": "assistant", "content": assistant_summary}
                ]
            
            # Build rich metadata for memory context
            memory_metadata = {
                "call_id": call_id,
                "call_type": metadata.get("call_type", "unknown"),
                "customer": customer_name,
                "customer_company": customer_id,  # Store company in metadata for filtering
                "timestamp": metadata.get("timestamp"),
                "lens_insights": {
                    "sales": sales_insight,
                    "marketing": marketing_insight,
                    "product": product_insight,
                },
                "source": "call_transcript",
            }
            
            # Extract and store memories (like chit_chat pattern)
            import asyncio
            logger.info(
                f"🔵 STORING PER-CALL MEMORY - user_id={user_id_str}, call_id={call_id}, messages={len(messages)}",
                extra={
                    "user_id": user_id_str,
                    "call_id": call_id,
                    "messages_count": len(messages),
                    "transcript_length": len(transcript),
                },
            )
            
            # Check if infer is enabled (required for memory extraction)
            infer_enabled = getattr(self.memory_service, 'infer', True)
            logger.info(f"Infer enabled: {infer_enabled} (required for memory extraction)")
            
            if not infer_enabled:
                logger.warning("⚠️ Memory inference is DISABLED. mem0 will not extract memories. Enable 'infer: true' in manifest.json")
            
            result = await asyncio.to_thread(
                self.memory_service.add,
                messages=messages,
                user_id=user_id_str,
                metadata=memory_metadata,
            )
            
            logger.info(f"mem0.add() returned: type={type(result)}, is_list={isinstance(result, list)}, length={len(result) if isinstance(result, list) else 'N/A'}")
            
            # Normalize result format (like chit_chat pattern)
            memory_count = len(result) if result and isinstance(result, list) else 0
            logger.info(f"✅ Mem0 extracted {memory_count} memories from call '{call_id}' for user_id='{user_id_str}'")
            
            if memory_count > 0:
                # Extract memory texts for logging (like chit_chat)
                memory_texts = []
                for m in result:
                    if isinstance(m, dict):
                        memory_text = (
                            m.get("memory")
                            or m.get("data", {}).get("memory", "")
                            or m.get("text", "")
                        )
                        if memory_text:
                            memory_texts.append({
                                "id": m.get("id") or m.get("_id"),
                                "memory": memory_text[:100] + "..." if len(memory_text) > 100 else memory_text,
                            })
                logger.info(f"Sample memories extracted: {memory_texts[:3]}")
                
                # Wait a bit for async processing (like chit_chat does - 0.3s)
                await asyncio.sleep(0.3)
                
                # Verify memories were stored by retrieving them (like chit_chat)
                fresh_memories = await asyncio.to_thread(
                    self.memory_service.get_all, user_id=user_id_str, limit=10
                )
                stored_count = len(fresh_memories) if isinstance(fresh_memories, list) else 0
                logger.info(f"✅ Verified: {stored_count} memories now stored for user_id='{user_id_str}' (expected {memory_count})")
            else:
                # If 0 memories, log helpful info (like chit_chat does)
                transcript_preview = transcript[:300] + "..." if len(transcript) > 300 else transcript
                logger.warning(
                    f"⚠️ Mem0 returned 0 memories for call '{call_id}'. "
                    "This may be normal if the transcript doesn't contain extractable facts. "
                    "mem0 extracts personal preferences, facts, and details - not generic greetings or small talk. "
                    f"Infer enabled: {infer_enabled}, Messages: {len(messages)}, "
                    f"Transcript length: {len(transcript)} chars. "
                    f"Preview: {transcript_preview}"
                )
            
            return result
            
        except Exception as e:
            logger.warning(f"Failed to extract memories: {e}", exc_info=True)
            return None
    
    def _parse_transcript_to_conversation(self, transcript: str) -> List[Dict[str, str]]:
        """
        Parse a call transcript into conversation turns for mem0.
        
        Transcripts typically have format:
        Agent: ...
        Customer: ...
        Agent: ...
        
        We convert this to:
        {"role": "assistant", "content": "..."}  (Agent)
        {"role": "user", "content": "..."}       (Customer)
        
        Args:
            transcript: Raw transcript text
            
        Returns:
            List of message dicts with "role" and "content"
        """
        messages = []
        lines = transcript.split('\n')
        current_role = None
        current_content = []
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Check if line starts with a speaker label
            if ':' in line:
                parts = line.split(':', 1)
                speaker = parts[0].strip().lower()
                content = parts[1].strip() if len(parts) > 1 else ""
                
                # Map speakers to roles
                if 'customer' in speaker or 'client' in speaker or 'user' in speaker:
                    # Save previous turn if exists
                    if current_role and current_content:
                        messages.append({
                            "role": current_role,
                            "content": "\n".join(current_content).strip()
                        })
                    current_role = "user"
                    current_content = [content] if content else []
                elif 'agent' in speaker or 'assistant' in speaker or 'rep' in speaker:
                    # Save previous turn if exists
                    if current_role and current_content:
                        messages.append({
                            "role": current_role,
                            "content": "\n".join(current_content).strip()
                        })
                    current_role = "assistant"
                    current_content = [content] if content else []
                else:
                    # Unknown speaker or continuation of previous content
                    if current_content is not None:
                        current_content.append(line)
            else:
                # Continuation line - append to current content
                if current_content is not None:
                    current_content.append(line)
        
        # Save final turn
        if current_role and current_content:
            messages.append({
                "role": current_role,
                "content": "\n".join(current_content).strip()
            })
        
        # Filter out empty messages
        messages = [msg for msg in messages if msg.get("content", "").strip()]
        
        logger.debug(f"Parsed transcript into {len(messages)} conversation turns")
        return messages
