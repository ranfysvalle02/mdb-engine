#!/usr/bin/env python3
"""
Snippet Extractor Module

Extracts relevant snippets from call transcripts using direct LLM extraction.
Uses LLM to analyze the full transcript and extract key snippets for each lens.
"""
import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("Parallax.Snippet")


class SnippetExtractor:
    """
    Extract relevant snippets using direct LLM extraction.
    
    This module demonstrates:
    1. Direct LLM analysis of full transcript
    2. LLM formatting to explain why snippets are relevant
    3. Structured JSON output for consistent results
    """
    
    def __init__(
        self,
        openai_client,
        app_slug: str = "parallax_memory",
        deployment_name: str = "gpt-4o",
    ):
        """
        Initialize the Snippet Extractor.
        
        Args:
            openai_client: AzureOpenAI or OpenAI client instance
            app_slug: App slug (for logging)
            deployment_name: Model deployment name (for Azure) or model name (for OpenAI)
        """
        self.openai_client = openai_client
        self.app_slug = app_slug
        self.deployment_name = deployment_name
    
    async def extract_snippets(
        self,
        transcript: str,
        call_id: str,
        lens_name: str,
        lens_analysis: Dict[str, Any],
    ) -> List[Dict[str, str]]:
        """
        Extract relevant snippets using direct LLM extraction.
        
        Pipeline:
        1. Build context from lens analysis
        2. Use LLM to extract and format snippets from full transcript
        
        Args:
            transcript: The full call transcript
            call_id: Call identifier (for logging)
            lens_name: Lens name (SALES, MARKETING, PRODUCT)
            lens_analysis: The analysis results from the lens
            
        Returns:
            List of dicts with 'snippet' and 'reason' keys
        """
        if not self.openai_client:
            logger.warning(
                f"Missing OpenAI client for snippet extraction: "
                f"openai_client={bool(self.openai_client)}"
            )
            return []
        
        try:
            # Build lens focus description
            lens_focus = {
                "SALES": "sales opportunities, deal value, objections, closing signals, buying intent, pricing discussions",
                "MARKETING": "customer sentiment, messaging effectiveness, brand perception, engagement, competitive mentions",
                "PRODUCT": "feature requests, pain points, use cases, product fit, technical requirements",
            }.get(lens_name, "key insights")
            
            # Use LLM directly on full transcript
            return await self._format_snippets_with_llm(
                transcript, lens_name, lens_analysis, lens_focus
            )
        
        except Exception as e:
            logger.warning(f"Failed to extract snippets for {lens_name}: {e}", exc_info=True)
            return []
    
    async def _format_snippets_with_llm(
        self,
        transcript: str,
        lens_name: str,
        lens_analysis: Dict[str, Any],
        lens_focus: str,
    ) -> List[Dict[str, str]]:
        """
        Use LLM to extract and format snippets from full transcript.
        
        Args:
            transcript: Full call transcript
            lens_name: Lens name (SALES, MARKETING, PRODUCT)
            lens_analysis: Analysis results from the lens
            lens_focus: Description of what the lens focuses on
            
        Returns:
            List of dicts with 'snippet' and 'reason' keys
        """
        try:
            analysis_summary = json.dumps(lens_analysis, indent=2)[:800]
            
            system_prompt = f"""You are a {lens_name} analyst extracting the most relevant snippets from a call transcript.

Your task:
1. From the provided transcript, identify 3-5 key snippets MOST relevant to {lens_focus}
2. Each snippet should be 1-3 sentences, preferably exact quotes from the transcript
3. Explain WHY each snippet is relevant to {lens_name} analysis
4. Focus on the most impactful and actionable insights

Return JSON:
{{
  "snippets": [
    {{
      "snippet": "exact quote from transcript (1-3 sentences)",
      "reason": "why this snippet is relevant to {lens_name} analysis (1 sentence)"
    }}
  ]
}}

Be critical and selective - only include snippets that truly matter."""

            user_prompt = f"""CALL TRANSCRIPT:
{transcript[:8000]}

LENS ANALYSIS SUMMARY:
{analysis_summary}

Extract the most relevant snippets for {lens_name} analysis. Return JSON with snippets array."""

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            
            response = await asyncio.to_thread(
                self.openai_client.chat.completions.create,
                model=self.deployment_name,
                messages=messages,
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            
            content = response.choices[0].message.content
            result_data = json.loads(content)
            
            snippets = result_data.get("snippets", [])
            if not isinstance(snippets, list):
                snippets = []
            
            # Validate and limit snippets
            validated_snippets = []
            for snippet in snippets[:5]:
                if isinstance(snippet, dict) and "snippet" in snippet and "reason" in snippet:
                    validated_snippets.append({
                        "snippet": str(snippet["snippet"])[:500],
                        "reason": str(snippet["reason"])[:300],
                    })
            
            logger.debug(f"Extracted {len(validated_snippets)} snippets for {lens_name} using LLM")
            return validated_snippets
        
        except Exception as e:
            logger.warning(f"Failed to format snippets with LLM: {e}", exc_info=True)
            return []
