#!/usr/bin/env python3
"""
Dynamic Schema Generator for Parallax Lenses

Generates Pydantic models on-the-fly from database-stored schema definitions.
"""
import logging
from typing import Any, Dict, List, Type

from pydantic import BaseModel, Field, create_model

logger = logging.getLogger("Parallax.SchemaGenerator")


# Type mapping from string to Python types
TYPE_MAPPING = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "List[str]": List[str],
    "List[int]": List[int],
    "Literal": str,  # For Literal types, we'll handle them specially
}


def create_dynamic_model(model_name: str, fields: List[Dict[str, Any]]) -> Type[BaseModel]:
    """
    Dynamically create a Pydantic model from field definitions.

    Args:
        model_name: Name of the model class (e.g., "MarketingView")
        fields: List of field definitions, each with:
            - name: str (field name)
            - type: str (Python type as string)
            - description: str (field description)
            - default: Any (optional default value)
            - constraints: Dict (optional constraints like ge, le, min_length, max_length)
            - literal_values: List[str] (for Literal types)

    Returns:
        A Pydantic BaseModel class
    """
    model_fields: Dict[str, Any] = {}

    for field_def in fields:
        field_name = field_def.get("name")
        if not field_name:
            logger.warning(f"Skipping field without name: {field_def}")
            continue

        field_type_str = field_def.get("type", "str")
        description = field_def.get("description", "")
        default_value = field_def.get("default", ...)  # Use ... for required fields
        constraints = field_def.get("constraints", {})
        literal_values = field_def.get("literal_values", [])

        # Determine Python type
        if literal_values:
            # Create Literal type
            from typing import Literal

            python_type = Literal[tuple(literal_values)]
        elif field_type_str.startswith("List["):
            # Handle List types
            inner_type = field_type_str.replace("List[", "").replace("]", "").strip()
            if inner_type == "str":
                python_type = List[str]
            elif inner_type == "int":
                python_type = List[int]
            else:
                python_type = List[str]  # Default to List[str]
        else:
            python_type = TYPE_MAPPING.get(field_type_str, str)

        # Build Field with constraints
        field_kwargs = {"description": description}
        field_kwargs.update(constraints)

        # Set default if provided
        if default_value is not ...:
            field_kwargs["default"] = default_value

        model_fields[field_name] = (python_type, Field(**field_kwargs))

    # Type 4: Let errors bubble up (already raises, just need to catch specific exceptions for logging)
    # Create the model dynamically
    try:
        DynamicModel = create_model(model_name, **model_fields)
        logger.info(f"Created dynamic model: {model_name} with {len(fields)} fields")
        return DynamicModel
    except (AttributeError, RuntimeError, ValueError, TypeError, KeyError) as e:
        # Type 4: Re-raise with context
        logger.error(f"Failed to create dynamic model {model_name}: {e}", exc_info=True)
        raise


def get_default_lens_configs() -> Dict[str, Dict[str, Any]]:
    """
    Get default lens configurations for call transcript analysis.
    Focused on SALES, MARKETING, and PRODUCT perspectives.
    """
    return {
        "SALES": {
            "lens_name": "SALES",
            "prompt_template": (
                "You are the Sales Specialist. Analyze this call transcript for sales opportunities, objections, closing signals, and deal value.\n\n"
                "Your analysis must focus on:\n"
                "1. **SALES OPPORTUNITIES**:\n"
                "   - What products or services were discussed?\n"
                "   - What is the potential deal value or budget mentioned?\n"
                "   - What is the customer's decision stage (evaluating, ready to buy, etc.)?\n"
                "   - Are there any buying signals or urgency indicators?\n\n"
                "2. **OBJECTIONS & CONCERNS**:\n"
                "   - What objections or concerns did the customer raise?\n"
                "   - What are the main pain points or challenges?\n"
                "   - Are there any competitive mentions or comparisons?\n\n"
                "3. **CLOSING SIGNALS**:\n"
                "   - Are there clear next steps or commitments?\n"
                "   - Did the customer express strong interest or intent to purchase?\n"
                "   - What is the likelihood of closing this deal?\n\n"
                "Be specific and factual. Base your analysis on what was actually said in the transcript. "
                "Do not hallucinate. Keep responses concise (1-2 sentences per field). Output valid JSON.\n\n"
                "{format_instructions}"
            ),
            "schema_fields": [
                {
                    "name": "deal_value",
                    "type": "str",
                    "description": "Estimated deal value or budget range mentioned (e.g., '$2,000-$5,000/month', 'Not specified')",
                    "default": None,
                },
                {
                    "name": "decision_stage",
                    "type": "Literal",
                    "description": "Customer's decision stage",
                    "default": None,
                    "literal_values": ["Discovery", "Evaluating", "Ready to Buy", "Closed Won", "Closed Lost", "Unknown"],
                    "display_type": "badge",
                },
                {
                    "name": "products_discussed",
                    "type": "List[str]",
                    "description": "List of products or services discussed in the call",
                    "default": None,
                },
                {
                    "name": "objections_raised",
                    "type": "List[str]",
                    "description": "Key objections or concerns raised by the customer",
                    "default": None,
                },
                {
                    "name": "pain_points",
                    "type": "List[str]",
                    "description": "Main pain points or challenges mentioned",
                    "default": None,
                },
                {
                    "name": "closing_probability",
                    "type": "Literal",
                    "description": "Likelihood of closing this deal",
                    "default": None,
                    "literal_values": ["Low", "Medium", "High"],
                    "display_type": "badge",
                },
                {
                    "name": "next_steps",
                    "type": "str",
                    "description": "Next steps or commitments made (1-2 sentences)",
                    "default": None,
                },
                {
                    "name": "key_insight",
                    "type": "str",
                    "description": "Most important sales insight from this call (1 sentence)",
                    "default": None,
                },
            ],
        },
        "MARKETING": {
            "lens_name": "MARKETING",
            "prompt_template": (
                "You are the Marketing Specialist. Analyze this call transcript to understand customer sentiment, messaging effectiveness, and brand perception.\n\n"
                "Extract insights about:\n"
                "1. **CUSTOMER SENTIMENT**: How does the customer feel? Are they excited, frustrated, curious, skeptical? What is their overall emotional tone?\n"
                "2. **MESSAGING EFFECTIVENESS**: What did the agent say that got positive reactions? What value propositions resonated? What language worked?\n"
                "3. **BRAND PERCEPTION**: How does the customer view our company/product? What do they think we do well? What concerns do they have?\n"
                "4. **COMPETITIVE LANDSCAPE**: Did they mention competitors? What alternatives are they considering? How do we compare?\n"
                "5. **CUSTOMER LANGUAGE**: What words and phrases does the customer use to describe their needs? This helps with messaging alignment.\n"
                "6. **ENGAGEMENT LEVEL**: How engaged was the customer? Were they asking questions, showing interest, or just listening?\n\n"
                "IMPORTANT: Always provide analysis. If something wasn't mentioned, use 'Not mentioned' or 'Not discussed'. "
                "Base everything on what was actually said in the transcript. Be specific and factual.\n\n"
                "{format_instructions}"
            ),
            "schema_fields": [
                {
                    "name": "customer_sentiment",
                    "type": "Literal",
                    "description": "Overall customer sentiment based on tone and language used",
                    "default": "Neutral",
                    "literal_values": ["Very Positive", "Positive", "Neutral", "Negative", "Very Negative"],
                    "display_type": "badge",
                },
                {
                    "name": "engagement_level",
                    "type": "Literal",
                    "description": "How engaged and interested the customer appeared",
                    "default": "Moderate",
                    "literal_values": ["Very High", "High", "Moderate", "Low", "Very Low"],
                    "display_type": "badge",
                },
                {
                    "name": "messaging_resonance",
                    "type": "str",
                    "description": "What messaging or value propositions resonated most with the customer (what got positive reactions or interest). If none, say 'Not clearly evident'",
                    "default": "Not clearly evident",
                },
                {
                    "name": "brand_perception",
                    "type": "str",
                    "description": "How the customer perceives our brand/product based on what they said (1-2 sentences). If not discussed, say 'Not discussed'",
                    "default": "Not discussed",
                },
                {
                    "name": "competitive_mentions",
                    "type": "List[str]",
                    "description": "Competitors or alternative solutions mentioned by the customer. If none, return empty list []",
                    "default": [],
                },
                {
                    "name": "customer_language",
                    "type": "str",
                    "description": "Key words, phrases, or terminology the customer uses to describe their needs/problems (1 sentence). Extract actual phrases they used",
                    "default": "Not evident",
                },
                {
                    "name": "pain_points_mentioned",
                    "type": "List[str]",
                    "description": "Pain points or challenges the customer explicitly mentioned (use their exact words when possible)",
                    "default": [],
                },
                {
                    "name": "key_insight",
                    "type": "str",
                    "description": "Most important marketing insight from this call - what should marketing know? (1 sentence)",
                    "default": None,
                },
            ],
        },
        "PRODUCT": {
            "lens_name": "PRODUCT",
            "prompt_template": (
                "You are the Product Specialist. Analyze this call transcript for feature requests, pain points, product-market fit, and use cases.\n\n"
                "Your analysis must focus on:\n"
                "1. **FEATURE REQUESTS**:\n"
                "   - What features or capabilities did the customer ask about?\n"
                "   - What functionality is missing or desired?\n"
                "   - What integrations or capabilities were discussed?\n\n"
                "2. **PAIN POINTS**:\n"
                "   - What problems is the customer trying to solve?\n"
                "   - What challenges are they facing with current solutions?\n"
                "   - What workflows or processes are causing friction?\n\n"
                "3. **PRODUCT-MARKET FIT**:\n"
                "   - How well does our product match their needs?\n"
                "   - What use cases were discussed?\n"
                "   - What is the customer's current solution and why are they looking to change?\n"
                "   - What would make our product a better fit?\n\n"
                "Be specific and factual. Base your analysis on what was actually said in the transcript. "
                "Do not hallucinate. Keep responses concise (1-2 sentences per field). Output valid JSON.\n\n"
                "{format_instructions}"
            ),
            "schema_fields": [
                {
                    "name": "feature_requests",
                    "type": "List[str]",
                    "description": "Features or capabilities requested or discussed",
                    "default": None,
                },
                {
                    "name": "pain_points",
                    "type": "List[str]",
                    "description": "Key pain points or problems the customer is trying to solve",
                    "default": None,
                },
                {
                    "name": "use_cases",
                    "type": "List[str]",
                    "description": "Primary use cases discussed",
                    "default": None,
                },
                {
                    "name": "current_solution",
                    "type": "str",
                    "description": "Customer's current solution or approach (1-2 sentences)",
                    "default": None,
                },
                {
                    "name": "product_fit_score",
                    "type": "Literal",
                    "description": "How well our product fits their needs",
                    "default": None,
                    "literal_values": ["Poor", "Fair", "Good", "Excellent"],
                    "display_type": "badge",
                },
                {
                    "name": "integration_needs",
                    "type": "List[str]",
                    "description": "Integrations or integrations mentioned",
                    "default": None,
                },
                {
                    "name": "workflow_insights",
                    "type": "str",
                    "description": "Key insights about customer workflows or processes (1-2 sentences)",
                    "default": None,
                },
                {
                    "name": "key_insight",
                    "type": "str",
                    "description": "Most important product insight from this call (1 sentence)",
                    "default": None,
                },
            ],
        },
    }
