#!/usr/bin/env python3
"""
Dynamic Schema Generator for Parallax Lenses

Generates Pydantic models on-the-fly from database-stored schema definitions.
"""
import logging
from typing import Any, Dict, List, Optional, Type

from pydantic import BaseModel, Field, create_model
from pydantic.fields import FieldInfo

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
    Get default lens configurations.
    Focused on Relevance (personalized value) and Technical (practical engineering insights).
    """
    return {
        "Relevance": {
            "lens_name": "Relevance",
            "prompt_template": (
                "You are the Relevance Specialist. Analyze this GitHub repository's code implementation (from AGENTS.md/LLMs.md) for PARTNERSHIP OPPORTUNITIES and KEYWORD USAGE VERIFICATION.\n\n"
                "Watchlist keywords to check: {watchlist}\n\n"
                "Your analysis must focus on:\n"
                "1. **PARTNERSHIP OPPORTUNITIES**:\n"
                "   - Is this repository a potential partnership opportunity?\n"
                "   - What kind of partnership would make sense (integration, collaboration, co-marketing, etc.)?\n"
                "   - What value could this partnership bring?\n"
                "   - Is the repository mature/established enough for partnership consideration?\n\n"
                "2. **KEYWORD USAGE VERIFICATION**:\n"
                "   - Do the watchlist keywords ({watchlist}) actually appear in the code/implementation?\n"
                "   - How are these keywords used? (e.g., MongoDB as database, vector for embeddings, etc.)\n"
                "   - Are the keywords central to the implementation or just mentioned?\n"
                "   - What specific technologies/tools from the watchlist are being used?\n\n"
                "3. **RELEVANCE ASSESSMENT**:\n"
                "   - Why does this repository matter given the watchlist keywords?\n"
                "   - What makes it relevant for partnership or collaboration?\n"
                "   - What is the urgency/priority for engaging with this repository?\n\n"
                "Be specific and factual. Base your analysis on actual code implementation details from the AGENTS.md/LLMs.md file. "
                "Do not hallucinate. If keywords are not found, state that clearly. Keep responses concise (1-2 sentences per field). Output valid JSON.\n\n"
                "{format_instructions}"
            ),
            "schema_fields": [
                {
                    "name": "relevance_score",
                    "type": "int",
                    "description": "Relevance score for partnership opportunities (1-100)",
                    "default": None,
                    "constraints": {"ge": 1, "le": 100},
                },
                {
                    "name": "partnership_opportunity",
                    "type": "str",
                    "description": "Type of partnership opportunity identified (e.g., 'Integration', 'Collaboration', 'Co-marketing', 'Technology partnership', 'None')",
                    "default": None,
                },
                {
                    "name": "keywords_used",
                    "type": "List[str]",
                    "description": "List of watchlist keywords that are actually used in the code/implementation",
                    "default": None,
                },
                {
                    "name": "keywords_usage_details",
                    "type": "str",
                    "description": "How the watchlist keywords are used in the implementation (1-2 sentences)",
                    "default": None,
                },
                {
                    "name": "why_it_matters",
                    "type": "str",
                    "description": "Why this repository matters for partnership opportunities (1 sentence)",
                    "default": None,
                },
                {
                    "name": "key_insight",
                    "type": "str",
                    "description": "Most important takeaway about partnership potential (1 sentence)",
                    "default": None,
                },
                {
                    "name": "urgency",
                    "type": "Literal",
                    "description": "Urgency level for partnership engagement",
                    "default": None,
                    "literal_values": ["Low", "Medium", "High"],
                    "display_type": "badge",
                },
            ],
        },
        "Technical": {
            "lens_name": "Technical",
            "prompt_template": (
                "You are the Technical Specialist. Analyze the GitHub repository's code implementation (from AGENTS.md/LLMs.md) and provide a concise CODE-FOCUSED technical assessment.\n\n"
                "Focus on CODE ANALYSIS:\n"
                "- Architecture: Code structure, design patterns, modularity, separation of concerns\n"
                "- Implementation Quality: Code organization, best practices, maintainability indicators\n"
                "- Technology Stack: Dependencies, frameworks, libraries, and their usage patterns\n"
                "- Complexity: Code complexity metrics, cognitive load, implementation difficulty\n"
                "- Readiness: Production-readiness indicators from code (error handling, testing, documentation in code)\n"
                "- Code Patterns: Specific patterns used (e.g., async/await, dependency injection, event-driven)\n\n"
                "Analyze actual code implementation details, not just descriptions. Be concise (1-2 sentences per field). Focus on what developers can learn from the code. Do not hallucinate. Output valid JSON.\n\n"
                "{format_instructions}"
            ),
            "schema_fields": [
                {
                    "name": "architecture",
                    "type": "str",
                    "description": "Code architecture and design patterns observed (1 sentence)",
                    "default": None,
                },
                {
                    "name": "tech_stack",
                    "type": "str",
                    "description": "Technology stack and key dependencies (1 sentence)",
                    "default": None,
                },
                {
                    "name": "complexity",
                    "type": "Literal",
                    "description": "Code implementation complexity",
                    "default": None,
                    "literal_values": ["Low", "Medium", "High"],
                    "display_type": "badge",
                },
                {
                    "name": "readiness",
                    "type": "Literal",
                    "description": "Production readiness based on code quality",
                    "default": None,
                    "literal_values": ["Experimental", "Beta", "Production"],
                    "display_type": "badge",
                },
                {
                    "name": "code_patterns",
                    "type": "str",
                    "description": "Key code patterns and implementation approaches (1 sentence)",
                    "default": None,
                },
            ],
        },
    }
