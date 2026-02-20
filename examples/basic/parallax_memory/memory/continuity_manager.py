#!/usr/bin/env python3
"""
Continuity Manager - Context Injection System

The Continuity Manager generates "State of the Union" summaries that can be
injected before interactions. Instead of "How can I help you?", the agent
knows: "I see we fixed that billing issue from last Tuesday, are you calling
about the integration step we discussed?"
"""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("Parallax.Continuity")


class ContinuityManager:
    """
    The Continuity Manager - Generates context summaries for seamless interactions.
    
    Creates "State of the Union" summaries that provide:
    - Recent call history summary
    - Open items from previous calls
    - Customer journey context
    - Recommended talking points
    """
    
    def __init__(
        self,
        db,
        openai_client,
        deployment_name: str = "gpt-4o",
        temperature: float = 0.3,
    ):
        """
        Initialize the Continuity Manager.
        
        Args:
            db: MongoDB database instance
            openai_client: OpenAI/AzureOpenAI client
            deployment_name: Model deployment name
            temperature: LLM temperature
        """
        self.db = db
        self.openai_client = openai_client
        self.deployment_name = deployment_name
        self.temperature = temperature
    
    async def generate_summary(
        self,
        customer_company: str,
        recent_calls: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Generate continuity summary for a customer.
        
        Args:
            customer_company: Customer company name
            recent_calls: List of recent call data
        
        Returns:
            Dict with continuity summary:
            - recent_history: Summary of recent calls
            - open_items: Items mentioned but not resolved
            - journey_stage: Current stage in customer journey
            - talking_points: Recommended topics for next interaction
        """
        if len(recent_calls) < 1:
            return {}
        
        logger.info(f"📋 Generating continuity summary for {customer_company} ({len(recent_calls)} calls)")
        
        try:
            # Use LLM to generate comprehensive summary
            summary = await self._generate_llm_summary(customer_company, recent_calls)
            
            # Extract open items
            open_items = await self._extract_open_items(recent_calls)
            
            # Determine journey stage
            journey_stage = self._determine_journey_stage(recent_calls)
            
            # Generate talking points
            talking_points = await self._generate_talking_points(recent_calls, open_items)
            
            continuity_summary = {
                "customer_company": customer_company,
                "recent_history": summary,
                "open_items": open_items,
                "journey_stage": journey_stage,
                "talking_points": talking_points,
                "last_updated": datetime.utcnow().isoformat(),
            }
            
            # Store summary
            await self.db.continuity_summaries.update_one(
                {"customer_company": customer_company},
                {"$set": continuity_summary},
                upsert=True,
            )
            
            return continuity_summary
            
        except Exception as e:
            logger.error(f"Continuity summary generation failed: {e}", exc_info=True)
            return {}
    
    async def _generate_llm_summary(
        self,
        customer_company: str,
        calls: List[Dict[str, Any]],
    ) -> str:
        """Generate LLM-powered summary of recent calls."""
        if not self.openai_client:
            return "Summary generation requires LLM service."
        
        try:
            # Build context from calls
            call_contexts = []
            for call in calls[-5:]:  # Last 5 calls
                report = call.get("report", {})
                sales = report.get("sales", {})
                product = report.get("product", {})
                marketing = report.get("marketing", {})
                
                context = f"Call on {call.get('timestamp', 'unknown date')}:\n"
                if sales:
                    context += f"- Sales: {sales.get('key_insight', 'N/A')}\n"
                if product:
                    context += f"- Product: {product.get('key_insight', 'N/A')}\n"
                if marketing:
                    context += f"- Sentiment: {marketing.get('sentiment', {}).get('overall_sentiment', 'N/A')}\n"
                
                call_contexts.append(context)
            
            prompt = f"""You are a customer relationship manager. Generate a concise "State of the Union" summary for {customer_company} based on their recent calls.

Recent call history:
{chr(10).join(call_contexts)}

Generate a 2-3 paragraph summary that:
1. Highlights key developments from recent calls
2. Notes any resolved issues or progress made
3. Identifies the current relationship status
4. Provides context for the next interaction

Write in a natural, conversational tone that an agent could use to start a call."""
            
            response = self.openai_client.chat.completions.create(
                model=self.deployment_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.temperature,
            )
            
            return response.choices[0].message.content.strip()
            
        except Exception as e:
            logger.warning(f"LLM summary generation failed: {e}")
            return f"Summary of {len(calls)} recent calls for {customer_company}."
    
    async def _extract_open_items(
        self,
        calls: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Extract open items (mentioned but not resolved) from calls."""
        open_items = []
        
        try:
            # Look for action items, follow-ups, or unresolved issues
            for call in calls[-3:]:  # Last 3 calls
                report = call.get("report", {})
                sales = report.get("sales", {})
                product = report.get("product", {})
                
                # Extract from sales lens
                if sales:
                    next_steps = sales.get("next_steps") or sales.get("action_items", [])
                    if isinstance(next_steps, list):
                        for item in next_steps:
                            if isinstance(item, dict):
                                open_items.append({
                                    "item": item.get("description") or item.get("action", ""),
                                    "source_call": call.get("call_id"),
                                    "source_date": call.get("timestamp"),
                                    "priority": item.get("priority", "medium"),
                                })
                            elif isinstance(item, str):
                                open_items.append({
                                    "item": item,
                                    "source_call": call.get("call_id"),
                                    "source_date": call.get("timestamp"),
                                    "priority": "medium",
                                })
                
                # Extract from product lens
                if product:
                    pain_points = product.get("pain_points", [])
                    if isinstance(pain_points, list):
                        for pain in pain_points:
                            if isinstance(pain, dict):
                                if not pain.get("resolved", False):
                                    open_items.append({
                                        "item": pain.get("description") or pain.get("pain_point", ""),
                                        "source_call": call.get("call_id"),
                                        "source_date": call.get("timestamp"),
                                        "priority": "high",
                                        "type": "pain_point",
                                    })
        
        except Exception as e:
            logger.warning(f"Open items extraction failed: {e}")
        
        return open_items
    
    def _determine_journey_stage(
        self,
        calls: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Determine current stage in customer journey."""
        if not calls:
            return {"stage": "unknown", "confidence": "low"}
        
        # Simple heuristic based on call types and content
        call_types = [c.get("metadata", {}).get("call_type", "unknown") for c in calls]
        
        # Count occurrences
        discovery_count = sum(1 for ct in call_types if "discovery" in ct.lower() or "sales" in ct.lower())
        support_count = sum(1 for ct in call_types if "support" in ct.lower() or "help" in ct.lower())
        onboarding_count = sum(1 for ct in call_types if "onboard" in ct.lower() or "setup" in ct.lower())
        
        if discovery_count > support_count and discovery_count > onboarding_count:
            stage = "discovery"
        elif onboarding_count > support_count:
            stage = "onboarding"
        elif support_count > 0:
            stage = "active_support"
        else:
            stage = "ongoing_relationship"
        
        return {
            "stage": stage,
            "confidence": "medium",
            "call_type_distribution": {
                "discovery": discovery_count,
                "support": support_count,
                "onboarding": onboarding_count,
            },
        }
    
    async def _generate_talking_points(
        self,
        calls: List[Dict[str, Any]],
        open_items: List[Dict[str, Any]],
    ) -> List[str]:
        """Generate recommended talking points for next interaction."""
        talking_points = []
        
        # Add high-priority open items
        high_priority_items = [item for item in open_items if item.get("priority") == "high"]
        for item in high_priority_items[:3]:
            talking_points.append(f"Follow up on: {item.get('item', '')}")
        
        # Add recent positive developments
        latest_call = calls[-1] if calls else None
        if latest_call:
            report = latest_call.get("report", {})
            sales = report.get("sales", {})
            if sales and sales.get("key_insight"):
                talking_points.append(f"Reference recent discussion: {sales.get('key_insight', '')[:100]}")
        
        return talking_points[:5]  # Limit to 5 talking points
    
    async def get_summary(
        self,
        customer_company: str,
    ) -> Optional[Dict[str, Any]]:
        """Get stored continuity summary for a customer."""
        summary = await self.db.continuity_summaries.find_one(
            {"customer_company": customer_company}
        )
        
        if summary:
            summary.pop("_id", None)
        
        return summary
