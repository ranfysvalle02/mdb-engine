#!/usr/bin/env python3
"""
Customer Profile Manager

The Customer Profile Manager maintains dynamic customer profiles that evolve
over time. It tracks:
- Relationship health score
- Customer journey stage
- Key insights and trends
- Risk indicators
"""
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger("Parallax.Profile")


class CustomerProfileManager:
    """
    Customer Profile Manager - Maintains dynamic customer profiles.
    
    Creates and updates customer profiles with:
    - Relationship health score (0-100)
    - Customer journey stage
    - Key insights and trends
    - Risk indicators
    - Engagement metrics
    """
    
    def __init__(
        self,
        db,
        openai_client,
        deployment_name: str = "gpt-4o",
        temperature: float = 0.3,
    ):
        """
        Initialize the Customer Profile Manager.
        
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
    
    async def update_profile(
        self,
        customer_company: str,
        new_call_data: Dict[str, Any],
        patterns: Optional[Dict[str, Any]] = None,
        contradictions: Optional[List[Dict[str, Any]]] = None,
        continuity_summary: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Update customer profile with new call data and insights.
        
        Args:
            customer_company: Customer company name
            new_call_data: New call data
            patterns: Detected patterns from Sentinel
            contradictions: Detected contradictions
            continuity_summary: Continuity summary
        
        Returns:
            Updated profile
        """
        logger.info(f"👤 Updating profile for {customer_company}")
        
        try:
            # Get existing profile or create new
            existing_profile = await self.get_profile(customer_company)
            
            # Calculate relationship health score
            health_score = await self._calculate_health_score(
                existing_profile,
                new_call_data,
                patterns,
                contradictions,
            )
            
            # Update profile fields
            updated_profile = {
                "customer_company": customer_company,
                "relationship_health_score": health_score,
                "last_call_id": new_call_data.get("call_id"),
                "last_call_timestamp": new_call_data.get("timestamp") or datetime.utcnow().isoformat(),
                "updated_at": datetime.utcnow().isoformat(),
            }
            
            # Add patterns if available
            if patterns:
                updated_profile["recent_patterns"] = {
                    "sentiment_velocity": patterns.get("sentiment_velocity", {}),
                    "risk_indicators": patterns.get("risk_indicators", []),
                }
            
            # Add contradictions if available
            if contradictions:
                updated_profile["active_contradictions"] = contradictions
                # Check for upsell opportunities
                upsell_opportunities = [
                    c for c in contradictions
                    if c.get("opportunity") == "upsell" and c.get("significance") == "high"
                ]
                if upsell_opportunities:
                    updated_profile["upsell_opportunities"] = upsell_opportunities
            
            # Add journey stage from continuity summary
            if continuity_summary:
                updated_profile["journey_stage"] = continuity_summary.get("journey_stage", {})
                updated_profile["open_items_count"] = len(continuity_summary.get("open_items", []))
            
            # Merge with existing profile
            if existing_profile:
                existing_profile.update(updated_profile)
                updated_profile = existing_profile
            
            # Add metadata
            updated_profile["total_calls"] = await self._count_calls(customer_company)
            updated_profile["profile_version"] = existing_profile.get("profile_version", 0) + 1
            
            # Store profile
            await self.db.customer_profiles.update_one(
                {"customer_company": customer_company},
                {"$set": updated_profile},
                upsert=True,
            )
            
            logger.info(f"✅ Profile updated for {customer_company} (health score: {health_score})")
            
            return updated_profile
            
        except Exception as e:
            logger.error(f"Profile update failed: {e}", exc_info=True)
            return {}
    
    async def _calculate_health_score(
        self,
        existing_profile: Optional[Dict[str, Any]],
        new_call_data: Dict[str, Any],
        patterns: Optional[Dict[str, Any]],
        contradictions: Optional[List[Dict[str, Any]]],
    ) -> int:
        """Calculate relationship health score (0-100)."""
        # Start with base score
        base_score = existing_profile.get("relationship_health_score", 70) if existing_profile else 70
        
        # Adjust based on sentiment velocity
        if patterns and patterns.get("sentiment_velocity"):
            velocity = patterns["sentiment_velocity"]
            if velocity.get("trend") == "declining":
                decline = abs(velocity.get("velocity", 0))
                base_score -= int(decline * 200)  # Penalize decline
            elif velocity.get("trend") == "improving":
                improvement = velocity.get("velocity", 0)
                base_score += int(improvement * 100)  # Reward improvement
        
        # Adjust based on risk indicators
        if patterns and patterns.get("risk_indicators"):
            risk_count = len(patterns["risk_indicators"])
            high_risks = sum(1 for r in patterns["risk_indicators"] if r.get("severity") == "high")
            base_score -= (risk_count * 5) + (high_risks * 10)
        
        # Adjust based on contradictions (negative contradictions reduce score)
        if contradictions:
            negative_contradictions = [
                c for c in contradictions
                if c.get("significance") == "high" and c.get("opportunity") != "upsell"
            ]
            base_score -= len(negative_contradictions) * 5
        
        # Check call sentiment
        report = new_call_data.get("report", {})
        marketing = report.get("marketing", {})
        sentiment = marketing.get("sentiment", {})
        if isinstance(sentiment, dict):
            sentiment_score = sentiment.get("score") or sentiment.get("overall_sentiment_score")
            if sentiment_score:
                try:
                    sentiment_val = float(sentiment_score)
                    # Sentiment typically 0-1, convert to 0-100 scale
                    if sentiment_val <= 1.0:
                        sentiment_val *= 100
                    # Weight recent sentiment
                    base_score = int((base_score * 0.7) + (sentiment_val * 0.3))
                except (ValueError, TypeError):
                    pass
        
        # Ensure score is in valid range
        health_score = max(0, min(100, base_score))
        
        return health_score
    
    async def _count_calls(self, customer_company: str) -> int:
        """Count total calls for a customer."""
        count = await self.db.parallax_reports.count_documents(
            {"customer_company": customer_company}
        )
        return count
    
    async def get_profile(
        self,
        customer_company: str,
    ) -> Optional[Dict[str, Any]]:
        """Get customer profile."""
        profile = await self.db.customer_profiles.find_one(
            {"customer_company": customer_company}
        )
        
        if profile:
            profile.pop("_id", None)
        
        return profile
    
    async def get_all_profiles(
        self,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Get all customer profiles."""
        profiles = await self.db.customer_profiles.find({}).limit(limit).to_list(length=limit)
        
        # Remove _id for JSON serialization
        for profile in profiles:
            profile.pop("_id", None)
        
        return profiles
