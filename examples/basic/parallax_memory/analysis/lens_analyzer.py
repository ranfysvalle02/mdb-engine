#!/usr/bin/env python3
"""
Lens Analyzer Module

Handles analysis of call transcripts through different business lenses (SALES, MARKETING, PRODUCT).
Demonstrates dynamic schema generation and LLM-based analysis.
"""
import asyncio
import json
import logging
from typing import Any, Dict, Optional

from schema_generator import create_dynamic_model

logger = logging.getLogger("Parallax.Analysis")


class LensAnalyzer:
    """
    Analyze call transcripts through business lenses.
    
    This module demonstrates:
    1. Dynamic schema generation from database configs
    2. LLM-based analysis with structured output
    3. Multi-lens concurrent analysis (SALES, MARKETING, PRODUCT)
    """
    
    def __init__(
        self,
        openai_client,
        db,
        deployment_name: str = "gpt-4o",
        temperature: float = 0.0,
    ):
        """
        Initialize the Lens Analyzer.
        
        Args:
            openai_client: AzureOpenAI or OpenAI client instance
            db: Scoped MongoDB database instance
            deployment_name: Model deployment name (for Azure) or model name (for OpenAI)
            temperature: Temperature for LLM (default 0.0 for strict adherence to facts)
        """
        self.openai_client = openai_client
        self.db = db
        self.deployment_name = deployment_name
        self.temperature = temperature
        self.lens_configs = {}  # Cache for lens configurations
        self.lens_models = {}  # Cache for dynamically generated models
    
    async def _load_lens_config(self, lens_name: str) -> Optional[Dict[str, Any]]:
        """Load lens configuration from database"""
        try:
            config = await self.db.lens_configs.find_one({"lens_name": lens_name})
            if config:
                config.pop("_id", None)
                self.lens_configs[lens_name] = config
                return config
        except (AttributeError, RuntimeError, ConnectionError, ValueError, TypeError, KeyError):
            logger.debug(f"Could not load lens config for {lens_name}", exc_info=True)
        return None
    
    async def _get_lens_model(self, lens_name: str) -> Optional[Any]:
        """Get or create the dynamic Pydantic model for a lens"""
        if lens_name in self.lens_models:
            return self.lens_models[lens_name]
        
        config = await self._load_lens_config(lens_name)
        if not config:
            logger.error(f"No configuration found for lens: {lens_name}")
            return None
        
        try:
            model = create_dynamic_model(f"{lens_name}View", config.get("schema_fields", []))
            self.lens_models[lens_name] = model
            return model
        except (AttributeError, RuntimeError, ValueError, TypeError, KeyError):
            logger.error(f"Failed to create model for {lens_name}", exc_info=True)
            return None
    
    async def generate_viewpoint(
        self,
        call_id: str,
        call_type: str,
        participants: Dict[str, Any],
        transcript: str,
        metadata: Dict[str, Any],
        lens_name: str,
    ) -> Optional[Any]:
        """
        Generate a viewpoint analysis for a call transcript using a specific lens.
        
        Args:
            call_id: The call identifier
            call_type: Type of call (sales_discovery, customer_support, etc.)
            participants: Call participants information
            transcript: The call transcript text
            metadata: Call metadata (product_mentioned, pain_points, etc.)
            lens_name: Lens name (SALES, MARKETING, PRODUCT)
        
        Returns:
            Parsed schema instance or None on error
        """
        if not self.openai_client:
            logger.error("OpenAI client not initialized")
            return None
        
        config = await self._load_lens_config(lens_name)
        if not config:
            logger.error(f"No configuration found for lens: {lens_name}")
            return None
        
        schema = await self._get_lens_model(lens_name)
        if not schema:
            logger.error(f"Failed to get model for lens: {lens_name}")
            return None
        
        try:
            prompt_template = config.get(
                "prompt_template", "You are the {role} Specialist.\n\n{format_instructions}"
            )
            
            format_instructions = schema.model_json_schema()
            format_instructions_str = (
                f"Respond with valid JSON matching this schema: {format_instructions}"
            )
            
            system_prompt = prompt_template.format(
                role=lens_name, format_instructions=format_instructions_str
            )
            
            # Truncate transcript if too long (keep first 15000 chars)
            transcript_preview = transcript[:15000] + ("..." if len(transcript) > 15000 else "")
            
            # Build user prompt with call context
            customer_name = participants.get("customer", "Unknown")
            customer_company = participants.get("company", "Unknown")
            customer_role = participants.get("role", "Unknown")
            
            user_prompt = f"""CALL TRANSCRIPT ANALYSIS

Call ID: {call_id}
Call Type: {call_type}
Customer: {customer_name} ({customer_role}) at {customer_company}
Agent: {participants.get('agent', 'Unknown')}

Metadata:
- Products Mentioned: {metadata.get('product_mentioned', [])}
- Pain Points: {metadata.get('pain_points', [])}
- Budget Range: {metadata.get('budget_range', 'Not specified')}
- Decision Stage: {metadata.get('decision_stage', 'Unknown')}

TRANSCRIPT:
{transcript_preview}"""
            
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            
            response = await asyncio.to_thread(
                self.openai_client.chat.completions.create,
                model=self.deployment_name,
                messages=messages,
                temperature=self.temperature,
                response_format={"type": "json_object"},
            )
            
            content = response.choices[0].message.content
            result_data = json.loads(content)
            result = schema.model_validate(result_data)
            
            logger.debug(f"{lens_name} Viewpoint generated successfully for {call_id}")
            return result
        
        except json.JSONDecodeError as e:
            logger.error(f"{lens_name} Viewpoint Failed: Invalid JSON response - {e}", exc_info=True)
            if "content" in locals():
                logger.error(f"Response content: {content[:500]}")
            logger.error(f"Failed to parse {lens_name} analysis for call {call_id}")
            return None
        except (AttributeError, RuntimeError, ConnectionError, ValueError, TypeError, KeyError) as e:
            logger.error(f"{lens_name} Viewpoint Failed: {type(e).__name__}: {e}", exc_info=True)
            if "content" in locals():
                logger.error(f"Response content: {content[:500]}")
            logger.error(f"Failed to generate {lens_name} analysis for call {call_id}")
            return None
        except Exception as e:
            logger.error(f"{lens_name} Viewpoint Failed: Unexpected error - {type(e).__name__}: {e}", exc_info=True)
            logger.error(f"Failed to generate {lens_name} analysis for call {call_id}")
            return None
