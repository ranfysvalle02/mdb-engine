"""Semantic Discovery Service -- orchestrates the 4-step auto-generation pipeline.

Pipeline:
1. Listen & Extract (LLM Layer) -- handled by graph extraction with DEFINITION_DETECTION
2. Verify Against Reality (Graph Layer) -- SchemaGrounder
3. Compile to YAML (Template Layer) -- OsiCompiler
4. Store & Route -- updates concept node, notifies for review

This service also handles the dual-path input: user-provided YAML
is validated and loaded into the registry alongside auto-generated models.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..core.protocols import GraphServiceProtocol
    from ..llm.service import LLMService
    from .compiler import OsiCompiler
    from .grounding import SchemaGrounder
    from .registry import OsiModelRegistry


@dataclass
class DiscoveryResult:
    """Result of the semantic discovery pipeline for a concept."""

    concept_id: str
    metric_name: str = ""
    yaml_content: str = ""
    confidence: float = 0.0
    confidence_label: str = ""
    corrections: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    status: str = "provisional"
    error: str | None = None


@dataclass
class ImportResult:
    """Result of importing user-provided YAML."""

    success: bool
    models_loaded: int = 0
    error: str | None = None


class SemanticDiscoveryService:
    """Orchestrates the Listen -> Verify -> Compile -> Store pipeline.

    This service is the heart of the OSI differentiator. It coordinates
    the grounding and compilation steps after the extraction LLM has
    identified a business concept definition.
    """

    def __init__(
        self,
        graph_service: GraphServiceProtocol | None = None,
        osi_registry: OsiModelRegistry | None = None,
        llm_service: LLMService | None = None,
        compiler: OsiCompiler | None = None,
        grounder: SchemaGrounder | None = None,
    ):
        self._graph_service = graph_service
        self._osi_registry = osi_registry
        self._llm_service = llm_service

        # Lazy-initialize compiler and grounder
        self._compiler = compiler
        self._grounder = grounder

    def _ensure_compiler(self) -> OsiCompiler:
        """Lazy-initialize the YAML compiler."""
        if self._compiler is None:
            from .compiler import OsiCompiler

            self._compiler = OsiCompiler()
        return self._compiler

    def _ensure_grounder(self) -> SchemaGrounder:
        """Lazy-initialize the schema grounder."""
        if self._grounder is None:
            from .grounding import SchemaGrounder

            self._grounder = SchemaGrounder(
                graph_service=self._graph_service,
                osi_registry=self._osi_registry,
            )
        return self._grounder

    async def on_concept_discovered(
        self,
        concept_node: dict[str, Any],
        user_id: str,
    ) -> DiscoveryResult:
        """Called when extraction detects a definition concept node.

        Runs Steps 2-3 of the pipeline (Step 1 already happened in extraction):
        1. Ground extracted_logic against graph schema (Step 2)
        2. Compile to OSI YAML via Jinja2 template (Step 3)
        3. Update concept node with grounded_logic, osi_yaml, confidence

        Args:
            concept_node: The concept node dict with ``is_definition: true``
                and ``extracted_logic`` in properties.
            user_id: The user who defined the concept.

        Returns:
            DiscoveryResult with generated YAML and metadata.
        """
        concept_id = concept_node.get("_id", concept_node.get("id", "unknown"))
        props = concept_node.get("properties", {})
        extracted_logic = props.get("extracted_logic")

        if not extracted_logic:
            return DiscoveryResult(
                concept_id=concept_id,
                error="No extracted_logic found in concept node properties",
            )

        try:
            # Step 2: Ground against reality
            grounder = self._ensure_grounder()
            grounding_result = await grounder.ground(extracted_logic)

            # Step 3: Compile to YAML
            compiler = self._ensure_compiler()
            compiled = compiler.compile_metric(concept_node, grounding_result)

            # Update the concept node properties (if graph service available)
            if self._graph_service:
                try:
                    updated_props = dict(props)
                    updated_props["grounded_logic"] = grounding_result.grounded_logic
                    updated_props["osi_yaml"] = compiled.yaml_content
                    updated_props["confidence"] = compiled.confidence
                    updated_props["confidence_label"] = compiled.confidence_label
                    updated_props["osi_draft_generated"] = True

                    await self._graph_service.upsert_node(
                        node_id=concept_id,
                        node_type="concept",
                        name=concept_node.get("name", ""),
                        properties=updated_props,
                        user_id=user_id,
                    )
                    logger.info(
                        f"Concept '{concept_id}' updated with OSI YAML " f"(confidence: {compiled.confidence_label})"
                    )
                except (AttributeError, TypeError, RuntimeError) as e:
                    logger.warning(f"Could not update concept node '{concept_id}': {e}")

            return DiscoveryResult(
                concept_id=concept_id,
                metric_name=compiled.metric_name,
                yaml_content=compiled.yaml_content,
                confidence=compiled.confidence,
                confidence_label=compiled.confidence_label,
                corrections=compiled.corrections,
                warnings=grounding_result.warnings,
                status="provisional",
            )

        except (ValueError, TypeError, RuntimeError) as e:
            logger.warning(f"Discovery pipeline failed for '{concept_id}': {e}")
            return DiscoveryResult(
                concept_id=concept_id,
                error=str(e),
            )

    async def import_user_yaml(
        self,
        yaml_content: str,
        source: str = "user_upload",
    ) -> ImportResult:
        """Import user-provided YAML (Path B of dual-path input).

        Validates the YAML against OSI structure and loads into the registry.

        Args:
            yaml_content: Raw YAML string to import.
            source: Description of the source (for logging).

        Returns:
            ImportResult indicating success/failure and models loaded.
        """
        try:
            import yaml
        except ImportError:
            return ImportResult(
                success=False,
                error="PyYAML not installed. Install with: pip install pyyaml",
            )

        try:
            data = yaml.safe_load(yaml_content)
            if not data:
                return ImportResult(success=False, error="Empty YAML content")

            # Extract models from the parsed data
            models = []
            if "semantic_model" in data:
                sm = data["semantic_model"]
                if isinstance(sm, list):
                    models.extend(sm)
                elif isinstance(sm, dict):
                    models.append(sm)
            elif "metric" in data or "datasets" in data:
                # Single model or fragment
                models.append(data)
            else:
                models.append(data)

            if not models:
                return ImportResult(success=False, error="No semantic models found in YAML")

            # Load into registry if available
            if self._osi_registry:
                # Merge into existing models
                for model in models:
                    model["_source"] = source
                    model["_status"] = "approved"  # User-provided = approved
                self._osi_registry._models.extend(models)  # noqa: SLF001
                self._osi_registry._index_models()  # noqa: SLF001
                self._osi_registry._build_prompt_context()  # noqa: SLF001

            logger.info(f"Imported {len(models)} OSI model(s) from {source}")
            return ImportResult(
                success=True,
                models_loaded=len(models),
            )

        except yaml.YAMLError as e:
            return ImportResult(success=False, error=f"Invalid YAML: {e}")
        except (ValueError, TypeError) as e:
            return ImportResult(success=False, error=f"Import failed: {e}")
