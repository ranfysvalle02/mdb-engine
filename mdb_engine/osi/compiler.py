"""Compile grounded concept logic into OSI-compliant YAML using Jinja2 templates.

The compiler takes validated, grounded logic from the SchemaGrounder and renders
it into OSI-compatible YAML with provenance comments and confidence annotations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .grounding import GroundingResult

logger = logging.getLogger(__name__)

# Default templates directory (alongside this file)
DEFAULT_TEMPLATES_DIR = Path(__file__).parent / "templates"


@dataclass
class CompiledYAML:
    """Result of compiling a concept into OSI YAML."""

    yaml_content: str
    metric_name: str
    confidence: float
    confidence_label: str
    corrections: list[str]
    status: str = "DRAFT"


class OsiCompiler:
    """Compile grounded concept logic into OSI-compliant YAML.

    Uses Jinja2 templates for clean, maintainable YAML generation.
    Falls back to string formatting if Jinja2 is not available.
    """

    def __init__(self, templates_dir: Path | None = None):
        self._templates_dir = templates_dir or DEFAULT_TEMPLATES_DIR
        self._env = None

        try:
            import jinja2

            self._env = jinja2.Environment(
                loader=jinja2.FileSystemLoader(str(self._templates_dir)),
                trim_blocks=True,
                lstrip_blocks=True,
                keep_trailing_newline=True,
            )
            logger.debug(f"Jinja2 template environment loaded from {self._templates_dir}")
        except ImportError:
            logger.info("Jinja2 not available; using built-in YAML formatting")

    def compile_metric(
        self,
        concept_node: dict[str, Any],
        grounding_result: GroundingResult,
    ) -> CompiledYAML:
        """Compile a concept node into an OSI metric YAML snippet.

        Args:
            concept_node: The concept node dict from the knowledge graph.
            grounding_result: The result from SchemaGrounder.ground().

        Returns:
            CompiledYAML with the rendered YAML content.
        """
        props = concept_node.get("properties", {})
        name = concept_node.get("name", "unknown_metric")
        metric_name = name.lower().replace(" ", "_").replace("-", "_")
        definition = props.get("definition", "")
        created_by = props.get("created_by", "unknown")
        created_at = datetime.now(timezone.utc).isoformat()

        # Build SQL expression from grounded conditions
        grounded = grounding_result.grounded_logic
        conditions = grounded.get("conditions", [])
        temporal = grounded.get("temporal_constraints", [])

        sql_parts = []
        for cond in conditions:
            field = cond.get("field", "unknown")
            op = cond.get("operator", "=")
            value = cond.get("value", 0)
            window = cond.get("window", "")
            part = f"{field} {op} {value}"
            if window:
                part += f"  -- window: {window}"
            sql_parts.append(part)

        sql_expression = "\nAND\n".join(sql_parts) if sql_parts else "-- no conditions extracted"

        # Build temporal window comment
        window_parts = []
        for tc in temporal:
            window_parts.append(f"{tc.get('field', '?')} {tc.get('operator', '?')} {tc.get('value', '?')}")
        window_str = ", ".join(window_parts) if window_parts else None

        # Synonyms from the concept name
        synonyms = [name.lower()]
        if "_" in metric_name:
            synonyms.append(metric_name.replace("_", " "))

        # Build corrections list as strings
        correction_strs = [
            f"{c.original} -> {c.grounded} ({c.note})"
            for c in grounding_result.corrections
            if c.strategy != "unresolved" and c.original != c.grounded
        ]

        context = {
            "metric_name": metric_name,
            "definition": definition,
            "created_by": created_by,
            "created_at": created_at,
            "confidence_score": round(grounding_result.confidence, 2),
            "confidence_label": grounding_result.confidence_label,
            "status": "DRAFT",
            "sql_expression": sql_expression,
            "synonyms": synonyms,
            "window": window_str,
            "corrections": correction_strs,
        }

        # Try Jinja2 template first, fall back to built-in
        if self._env:
            import jinja2

            try:
                template = self._env.get_template("metric.yaml.j2")
                yaml_content = template.render(**context)
            except (OSError, jinja2.TemplateError) as e:
                logger.warning(f"Jinja2 template rendering failed, using fallback: {e}")
                yaml_content = self._fallback_metric_yaml(context)
        else:
            yaml_content = self._fallback_metric_yaml(context)

        return CompiledYAML(
            yaml_content=yaml_content,
            metric_name=metric_name,
            confidence=grounding_result.confidence,
            confidence_label=grounding_result.confidence_label,
            corrections=correction_strs,
            status="DRAFT",
        )

    def _fallback_metric_yaml(self, context: dict[str, Any]) -> str:
        """Generate metric YAML without Jinja2."""
        lines = [
            "# AUTO-GENERATED BY MDB-ENGINE",
            f"# Source: Conversation with {context['created_by']} ({context['created_at']})",
            f"# Confidence: {context['confidence_label']} ({context['confidence_score']})",
            f"# Status: {context['status']}",
        ]

        if context.get("corrections"):
            lines.append("# Corrections applied:")
            for c in context["corrections"]:
                lines.append(f"#   - {c}")

        lines.append("")
        lines.append("metric:")
        lines.append(f"  name: {context['metric_name']}")
        lines.append(f'  description: "{context["definition"]}"')
        lines.append("  expression:")
        lines.append("    dialects:")
        lines.append("      - dialect: ANSI_SQL")
        lines.append(f'        expression: "{context["sql_expression"]}"')

        if context.get("synonyms"):
            import json

            lines.append("  ai_context:")
            lines.append(f"    synonyms: {json.dumps(context['synonyms'])}")

        if context.get("window"):
            lines.append(f"  # Temporal window: {context['window']}")

        lines.append("")
        return "\n".join(lines)
