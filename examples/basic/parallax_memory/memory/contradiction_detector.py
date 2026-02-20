#!/usr/bin/env python3
"""
Contradiction Detector

The Contradiction Detector compares new claims against historical facts.
It identifies inconsistencies that might indicate:
- Upsell opportunities (e.g., "50 seats" → "200 users")
- Data quality issues
- Changing requirements
"""
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("Parallax.Contradiction")


class ContradictionDetector:
    """
    The Contradiction Detector - Identifies inconsistencies across calls.
    
    Detects:
    - Numerical contradictions (seat counts, usage numbers)
    - Factual contradictions (stated requirements vs. actual needs)
    - Timeline inconsistencies
    - Opportunity signals (growth indicators)
    """
    
    def __init__(
        self,
        db,
        openai_client,
        deployment_name: str = "gpt-4o",
        temperature: float = 0.3,
    ):
        """
        Initialize the Contradiction Detector.
        
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
    
    async def detect_contradictions(
        self,
        customer_company: str,
        recent_calls: List[Dict[str, Any]],
        new_call_data: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        Detect contradictions between new call and historical calls.
        
        Args:
            customer_company: Customer company name
            recent_calls: List of recent call data (including new call)
            new_call_data: The new call that triggered this analysis
        
        Returns:
            List of detected contradictions with:
            - type: Type of contradiction
            - field: What field contradicts
            - old_value: Previous value
            - new_value: New value
            - significance: Impact/opportunity level
        """
        if len(recent_calls) < 2:
            return []
        
        logger.info(f"🔍 Detecting contradictions for {customer_company} ({len(recent_calls)} calls)")
        
        try:
            # Extract facts from all calls
            all_facts = await self._extract_facts(recent_calls)
            
            # Detect contradictions using LLM
            contradictions = await self._detect_with_llm(all_facts, new_call_data)
            
            # Store contradictions
            if contradictions:
                await self.db.contradictions.insert_one({
                    "customer_company": customer_company,
                    "contradictions": contradictions,
                    "timestamp": datetime.utcnow(),
                })
            
            return contradictions
            
        except Exception as e:
            logger.error(f"Contradiction detection failed: {e}", exc_info=True)
            return []
    
    async def _extract_facts(
        self,
        calls: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Extract factual claims from calls."""
        facts = []
        
        for call in calls:
            report = call.get("report", {})
            sales = report.get("sales", {})
            product = report.get("product", {})
            
            call_facts = {
                "call_id": call.get("call_id"),
                "timestamp": call.get("timestamp"),
                "facts": [],
            }
            
            # Extract from sales lens
            if sales:
                deal_value = sales.get("deal_value") or sales.get("estimated_value")
                seat_count = sales.get("seat_count") or sales.get("user_count")
                company_size = sales.get("company_size")
                
                if deal_value:
                    call_facts["facts"].append({
                        "type": "deal_value",
                        "value": deal_value,
                        "source": "sales",
                    })
                if seat_count:
                    call_facts["facts"].append({
                        "type": "seat_count",
                        "value": seat_count,
                        "source": "sales",
                    })
                if company_size:
                    call_facts["facts"].append({
                        "type": "company_size",
                        "value": company_size,
                        "source": "sales",
                    })
            
            # Extract from product lens
            if product:
                use_case = product.get("use_case") or product.get("primary_use_case")
                requirements = product.get("requirements", [])
                
                if use_case:
                    call_facts["facts"].append({
                        "type": "use_case",
                        "value": use_case,
                        "source": "product",
                    })
                if requirements:
                    call_facts["facts"].append({
                        "type": "requirements",
                        "value": requirements,
                        "source": "product",
                    })
            
            if call_facts["facts"]:
                facts.append(call_facts)
        
        return facts
    
    async def _detect_with_llm(
        self,
        all_facts: List[Dict[str, Any]],
        new_call_data: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Use LLM to detect contradictions."""
        if not self.openai_client:
            return []
        
        try:
            # Build context
            facts_summary = []
            for fact_set in all_facts[:-1]:  # All except the new call
                facts_summary.append(
                    f"Call {fact_set['call_id']} ({fact_set['timestamp']}): "
                    f"{', '.join([f.get('type') + '=' + str(f.get('value')) for f in fact_set['facts']])}"
                )
            
            new_call_facts = all_facts[-1] if all_facts else {}
            new_facts_str = ", ".join([
                f.get('type') + '=' + str(f.get('value'))
                for f in new_call_facts.get('facts', [])
            ])
            
            prompt = f"""Analyze these customer call facts and identify contradictions or significant changes between the new call and historical calls.

Historical calls:
{chr(10).join(facts_summary)}

New call:
{new_facts_str}

Identify:
1. Numerical contradictions (e.g., seat count changed from 50 to 200 - potential upsell)
2. Factual contradictions (e.g., stated requirements changed)
3. Timeline inconsistencies
4. Growth indicators (positive contradictions that show expansion)

Return JSON format:
{{
  "contradictions": [
    {{
      "type": "seat_count_increase",
      "field": "seat_count",
      "old_value": "50",
      "new_value": "200",
      "significance": "high",
      "description": "Customer mentioned 50 seats in Call 1, now mentions 200 users - potential upsell opportunity",
      "opportunity": "upsell"
    }}
  ]
}}"""
            
            response = self.openai_client.chat.completions.create(
                model=self.deployment_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.temperature,
                response_format={"type": "json_object"},
            )
            
            import json
            result = json.loads(response.choices[0].message.content)
            contradictions = result.get("contradictions", [])
            
            # Add metadata
            for contradiction in contradictions:
                contradiction["detected_at"] = datetime.utcnow().isoformat()
                contradiction["new_call_id"] = new_call_data.get("call_id")
            
            return contradictions
            
        except Exception as e:
            logger.warning(f"LLM contradiction detection failed: {e}")
            return []
    
    async def get_active_contradictions(
        self,
        customer_company: str,
    ) -> List[Dict[str, Any]]:
        """Get active contradictions for a customer."""
        contradiction_doc = await self.db.contradictions.find_one(
            {"customer_company": customer_company},
            sort=[("timestamp", -1)]
        )
        
        if contradiction_doc:
            return contradiction_doc.get("contradictions", [])
        
        return []
