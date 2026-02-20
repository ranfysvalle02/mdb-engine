#!/usr/bin/env python3
"""
Sentinel - Pattern Recognition System

The Sentinel runs background processes to detect subtle shifts across calls
that a human agent might miss. It identifies:
- Sentiment velocity (declining satisfaction)
- Topic clustering (recurring themes)
- Behavioral patterns (engagement trends)
"""
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger("Parallax.Sentinel")


class Sentinel:
    """
    The Sentinel - Pattern Recognition across calls.
    
    Detects:
    - Sentiment velocity (trends in customer satisfaction)
    - Topic clustering (recurring themes that indicate dealbreakers)
    - Engagement patterns (how customer behavior changes over time)
    """
    
    def __init__(
        self,
        db,
        openai_client,
        deployment_name: str = "gpt-4o",
        temperature: float = 0.3,
    ):
        """
        Initialize the Sentinel.
        
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
    
    async def detect_patterns(
        self,
        customer_company: str,
        recent_calls: List[Dict[str, Any]],
        new_call_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Detect patterns across recent calls.
        
        Args:
            customer_company: Customer company name
            recent_calls: List of recent call data (including new call)
            new_call_data: The new call that triggered this analysis
        
        Returns:
            Dict with detected patterns:
            - sentiment_velocity: Trend in sentiment scores
            - topic_clusters: Recurring topics/concerns
            - engagement_trends: Changes in engagement level
            - risk_indicators: Early warning signs
        """
        if len(recent_calls) < 2:
            return {}
        
        logger.info(f"🔍 Sentinel analyzing {len(recent_calls)} calls for {customer_company}")
        
        try:
            # Extract sentiment scores from calls
            sentiment_scores = self._extract_sentiment_scores(recent_calls)
            
            # Calculate sentiment velocity
            sentiment_velocity = self._calculate_sentiment_velocity(sentiment_scores)
            
            # Detect topic clusters using LLM
            topic_clusters = await self._detect_topic_clusters(recent_calls)
            
            # Analyze engagement trends
            engagement_trends = self._analyze_engagement_trends(recent_calls)
            
            # Identify risk indicators
            risk_indicators = await self._identify_risk_indicators(
                customer_company, recent_calls, sentiment_velocity, topic_clusters
            )
            
            patterns = {
                "sentiment_velocity": sentiment_velocity,
                "topic_clusters": topic_clusters,
                "engagement_trends": engagement_trends,
                "risk_indicators": risk_indicators,
                "analyzed_calls": len(recent_calls),
                "analysis_timestamp": datetime.utcnow().isoformat(),
            }
            
            # Store patterns
            await self.db.sentinel_patterns.insert_one({
                "customer_company": customer_company,
                "patterns": patterns,
                "timestamp": datetime.utcnow(),
            })
            
            return patterns
            
        except Exception as e:
            logger.error(f"Sentinel pattern detection failed: {e}", exc_info=True)
            return {}
    
    def _extract_sentiment_scores(self, calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Extract sentiment scores from call analyses."""
        scores = []
        for call in calls:
            report = call.get("report", {})
            marketing = report.get("marketing", {})
            
            # Try to extract sentiment from marketing lens
            sentiment = marketing.get("sentiment", {})
            if isinstance(sentiment, dict):
                score = sentiment.get("score") or sentiment.get("overall_sentiment_score")
                if score:
                    scores.append({
                        "call_id": call.get("call_id"),
                        "timestamp": call.get("timestamp"),
                        "score": float(score) if isinstance(score, (int, float, str)) else 0.5,
                    })
            elif isinstance(sentiment, (int, float)):
                scores.append({
                    "call_id": call.get("call_id"),
                    "timestamp": call.get("timestamp"),
                    "score": float(sentiment),
                })
        
        return scores
    
    def _calculate_sentiment_velocity(
        self,
        sentiment_scores: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Calculate sentiment velocity (rate of change)."""
        if len(sentiment_scores) < 2:
            return {
                "trend": "insufficient_data",
                "velocity": 0.0,
                "direction": "stable",
            }
        
        # Sort by timestamp
        sorted_scores = sorted(sentiment_scores, key=lambda x: x.get("timestamp", ""))
        
        # Calculate average score for first half vs second half
        mid_point = len(sorted_scores) // 2
        first_half = sorted_scores[:mid_point]
        second_half = sorted_scores[mid_point:]
        
        avg_first = sum(s["score"] for s in first_half) / len(first_half) if first_half else 0.5
        avg_second = sum(s["score"] for s in second_half) / len(second_half) if second_half else 0.5
        
        velocity = avg_second - avg_first
        direction = "improving" if velocity > 0.05 else "declining" if velocity < -0.05 else "stable"
        
        return {
            "trend": direction,
            "velocity": round(velocity, 3),
            "first_half_avg": round(avg_first, 3),
            "second_half_avg": round(avg_second, 3),
            "calls_analyzed": len(sentiment_scores),
            "risk_level": "high" if velocity < -0.1 else "medium" if velocity < -0.05 else "low",
        }
    
    async def _detect_topic_clusters(
        self,
        calls: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Detect recurring topics using LLM analysis."""
        if not self.openai_client:
            return []
        
        try:
            # Build context from recent calls
            call_summaries = []
            for call in calls[-5:]:  # Last 5 calls
                report = call.get("report", {})
                product = report.get("product", {})
                sales = report.get("sales", {})
                
                summary = f"Call {call.get('call_id', 'unknown')}: "
                if product:
                    summary += f"Product concerns: {product.get('key_insight', 'N/A')}. "
                if sales:
                    summary += f"Sales context: {sales.get('key_insight', 'N/A')}. "
                
                call_summaries.append(summary)
            
            prompt = f"""Analyze these recent customer calls and identify recurring topics or themes that appear across multiple calls.

Call summaries:
{chr(10).join(call_summaries)}

Identify:
1. Topics mentioned in 3+ calls (these are dealbreakers, not glitches)
2. Concerns that are escalating
3. Positive themes that indicate strong fit

Return JSON format:
{{
  "recurring_topics": [
    {{
      "topic": "API latency",
      "mention_count": 3,
      "severity": "high",
      "description": "Customer mentioned API latency issues in 3 distinct calls over 6 months"
    }}
  ],
  "escalating_concerns": [],
  "positive_themes": []
}}"""
            
            response = self.openai_client.chat.completions.create(
                model=self.deployment_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.temperature,
                response_format={"type": "json_object"},
            )
            
            import json
            result = json.loads(response.choices[0].message.content)
            return result.get("recurring_topics", [])
            
        except Exception as e:
            logger.warning(f"Topic cluster detection failed: {e}")
            return []
    
    def _analyze_engagement_trends(
        self,
        calls: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Analyze engagement trends across calls."""
        if len(calls) < 2:
            return {}
        
        # Simple heuristic: longer transcripts = more engagement
        transcript_lengths = []
        for call in calls:
            transcript = call.get("transcript", "")
            transcript_lengths.append(len(transcript))
        
        if len(transcript_lengths) < 2:
            return {}
        
        # Calculate trend
        mid_point = len(transcript_lengths) // 2
        first_half_avg = sum(transcript_lengths[:mid_point]) / len(transcript_lengths[:mid_point])
        second_half_avg = sum(transcript_lengths[mid_point:]) / len(transcript_lengths[mid_point:])
        
        trend = "increasing" if second_half_avg > first_half_avg * 1.1 else \
                "decreasing" if second_half_avg < first_half_avg * 0.9 else "stable"
        
        return {
            "trend": trend,
            "first_half_avg_length": int(first_half_avg),
            "second_half_avg_length": int(second_half_avg),
            "change_percent": round(((second_half_avg - first_half_avg) / first_half_avg) * 100, 1),
        }
    
    async def _identify_risk_indicators(
        self,
        customer_company: str,
        calls: List[Dict[str, Any]],
        sentiment_velocity: Dict[str, Any],
        topic_clusters: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Identify risk indicators based on patterns."""
        risks = []
        
        # Check sentiment velocity
        if sentiment_velocity.get("risk_level") == "high":
            risks.append({
                "type": "sentiment_decline",
                "severity": "high",
                "description": f"Customer sentiment has declined {abs(sentiment_velocity.get('velocity', 0)):.1%} over recent calls",
                "recommendation": "Immediate intervention recommended - silent churn risk",
            })
        
        # Check recurring negative topics
        for topic in topic_clusters:
            if topic.get("severity") == "high" and topic.get("mention_count", 0) >= 3:
                risks.append({
                    "type": "recurring_concern",
                    "severity": "high",
                    "description": f"Topic '{topic.get('topic')}' mentioned {topic.get('mention_count')} times - not a glitch, likely a dealbreaker",
                    "recommendation": "Address this concern directly or risk churn",
                })
        
        return risks
    
    async def get_recent_patterns(
        self,
        customer_company: str,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """Get recent pattern analyses for a customer."""
        patterns = await self.db.sentinel_patterns.find(
            {"customer_company": customer_company}
        ).sort("timestamp", -1).limit(limit).to_list(length=limit)
        
        return [p.get("patterns", {}) for p in patterns]
