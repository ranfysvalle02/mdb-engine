"""Schema grounding: validate and correct LLM-extracted logic against the knowledge graph.

An LLM can hallucinate column/field names. This module grounds extracted logic
against the actual knowledge graph schema and OSI model definitions, correcting
mismatches and assigning confidence scores.

Grounding strategies (tried in order):
1. Exact match against known node types/properties (confidence: 1.0)
2. OSI synonym match (confidence: 0.95)
3. Fuzzy substring match (confidence: 0.7-0.9)
4. Unresolved: marked as UNRESOLVED (confidence: 0.0)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..core.protocols import GraphServiceProtocol
    from .registry import OsiModelRegistry

logger = logging.getLogger(__name__)


@dataclass
class FieldCorrection:
    """A single field correction made during grounding."""

    original: str
    grounded: str
    strategy: str  # "exact", "osi_synonym", "fuzzy", "unresolved"
    confidence: float
    note: str = ""


@dataclass
class GroundingResult:
    """Result of grounding extracted logic against the graph schema."""

    grounded_logic: dict[str, Any] = field(default_factory=dict)
    corrections: list[FieldCorrection] = field(default_factory=list)
    confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def confidence_label(self) -> str:
        """Human-readable confidence label."""
        if self.confidence >= 0.9:
            return "High"
        if self.confidence >= 0.7:
            return "Medium"
        if self.confidence >= 0.4:
            return "Low"
        return "Very Low"


class SchemaGrounder:
    """Validate and correct LLM-extracted logic against the knowledge graph schema."""

    def __init__(
        self,
        graph_service: GraphServiceProtocol | None = None,
        osi_registry: OsiModelRegistry | None = None,
    ):
        self._graph_service = graph_service
        self._osi_registry = osi_registry

    async def ground(
        self,
        extracted_logic: dict[str, Any],
    ) -> GroundingResult:
        """Ground extracted logic against the graph schema.

        Takes the ``extracted_logic`` dict from a concept node and validates
        each field reference against known node types, properties, and OSI
        dataset fields.

        Args:
            extracted_logic: The ``extracted_logic`` dict from a concept node,
                containing ``entities``, ``temporal_constraints``, and ``conditions``.

        Returns:
            GroundingResult with corrected field references and confidence score.
        """
        result = GroundingResult()
        result.grounded_logic = dict(extracted_logic)  # Start with a copy

        # Gather known schema from graph
        known_types, known_properties = await self._gather_schema()

        # Ground entity references
        entities = extracted_logic.get("entities", [])
        grounded_entities = []
        for entity in entities:
            grounded, correction = self._ground_entity(entity, known_types)
            grounded_entities.append(grounded)
            if correction:
                result.corrections.append(correction)
        result.grounded_logic["entities"] = grounded_entities

        # Ground field references in temporal constraints
        temporal = extracted_logic.get("temporal_constraints", [])
        grounded_temporal = []
        for constraint in temporal:
            grounded_constraint = dict(constraint)
            field_name = constraint.get("field", "")
            grounded_field, correction = self._ground_field(field_name, known_types, known_properties)
            grounded_constraint["field"] = grounded_field
            grounded_temporal.append(grounded_constraint)
            if correction:
                result.corrections.append(correction)
        result.grounded_logic["temporal_constraints"] = grounded_temporal

        # Ground field references in conditions
        conditions = extracted_logic.get("conditions", [])
        grounded_conditions = []
        for condition in conditions:
            grounded_condition = dict(condition)
            field_name = condition.get("field", "")
            grounded_field, correction = self._ground_field(field_name, known_types, known_properties)
            grounded_condition["field"] = grounded_field
            grounded_conditions.append(grounded_condition)
            if correction:
                result.corrections.append(correction)
        result.grounded_logic["conditions"] = grounded_conditions

        # Calculate overall confidence
        if result.corrections:
            confidences = [c.confidence for c in result.corrections]
            result.confidence = sum(confidences) / len(confidences)
        else:
            # No corrections needed = high confidence (fields may be novel)
            result.confidence = 0.8

        # Add warnings for unresolved fields
        for correction in result.corrections:
            if correction.strategy == "unresolved":
                result.warnings.append(f"Could not resolve field '{correction.original}': {correction.note}")

        return result

    async def _gather_schema(self) -> tuple[set[str], dict[str, set[str]]]:
        """Gather known types and properties from the graph.

        Returns:
            Tuple of (known_types, known_properties) where known_properties
            maps type -> set of property names.
        """
        known_types: set[str] = set()
        known_properties: dict[str, set[str]] = {}

        # From graph service
        if self._graph_service:
            try:
                node_types = getattr(self._graph_service, "node_types", [])
                known_types.update(t.lower() for t in node_types)
            except (AttributeError, TypeError) as e:
                logger.debug("Could not read node_types from graph service: %s", e)

            # Try to get property names from existing nodes via stats
            try:
                stats = await self._graph_service.get_stats()
                for node_type in stats.get("nodes_by_type", {}):
                    known_types.add(node_type.lower())
            except (AttributeError, TypeError, RuntimeError) as e:
                logger.debug("Could not read graph stats for grounding: %s", e)

        # From OSI registry
        if self._osi_registry:
            for dataset in self._osi_registry.list_datasets():
                ds_name = dataset.get("name", "").lower()
                known_types.add(ds_name)
                fields = {f.get("name", "").lower() for f in dataset.get("fields", []) if f.get("name")}
                known_properties[ds_name] = fields

        return known_types, known_properties

    def _ground_entity(
        self,
        entity: str,
        known_types: set[str],
    ) -> tuple[str, FieldCorrection | None]:
        """Ground an entity reference against known types."""
        entity_lower = entity.lower()

        # 1. Exact match
        if entity_lower in known_types:
            return entity, None

        # 2. OSI synonym match
        if self._osi_registry:
            dataset = self._osi_registry.get_dataset(entity)
            if dataset:
                ds_name = dataset.get("name", entity)
                return ds_name, FieldCorrection(
                    original=entity,
                    grounded=ds_name,
                    strategy="osi_synonym",
                    confidence=0.95,
                    note=f"Matched via OSI synonym to dataset '{ds_name}'",
                )

        # 3. Fuzzy match (substring)
        for known in known_types:
            if entity_lower in known or known in entity_lower:
                return known, FieldCorrection(
                    original=entity,
                    grounded=known,
                    strategy="fuzzy",
                    confidence=0.75,
                    note=f"Fuzzy matched '{entity}' to known type '{known}'",
                )

        # 4. Unresolved
        return entity, FieldCorrection(
            original=entity,
            grounded=entity,
            strategy="unresolved",
            confidence=0.0,
            note=f"No matching type found for '{entity}'",
        )

    def _ground_field(
        self,
        field_ref: str,
        known_types: set[str],
        known_properties: dict[str, set[str]],
    ) -> tuple[str, FieldCorrection | None]:
        """Ground a field reference (e.g., 'users.last_login') against known schema."""
        if not field_ref:
            return field_ref, None

        # Split "type.field" references
        parts = field_ref.split(".", 1)
        if len(parts) == 2:
            ref_type, ref_field = parts[0].lower(), parts[1].lower()
        else:
            ref_type, ref_field = "", field_ref.lower()

        # 1. Exact type+field match
        if ref_type in known_properties:
            if ref_field in known_properties[ref_type]:
                return field_ref, None  # Perfect match

        # 2. Check if type matches but field doesn't
        if ref_type in known_types:
            # Type matches, try to find the field
            for type_name, props in known_properties.items():
                if type_name == ref_type:
                    # Fuzzy match the field name
                    for prop in props:
                        if ref_field in prop or prop in ref_field:
                            grounded = f"{type_name}.{prop}"
                            return grounded, FieldCorrection(
                                original=field_ref,
                                grounded=grounded,
                                strategy="fuzzy",
                                confidence=0.8,
                                note=f"Field '{ref_field}' fuzzy-matched to '{prop}' in '{type_name}'",
                            )

        # 3. Search across all types for the field
        for type_name, props in known_properties.items():
            if ref_field in props:
                grounded = f"{type_name}.{ref_field}"
                return grounded, FieldCorrection(
                    original=field_ref,
                    grounded=grounded,
                    strategy="fuzzy",
                    confidence=0.7,
                    note=f"Found field '{ref_field}' in type '{type_name}' (original type: '{ref_type}')",
                )

        # 4. Fuzzy search across all properties
        for type_name, props in known_properties.items():
            for prop in props:
                if ref_field in prop or prop in ref_field:
                    grounded = f"{type_name}.{prop}"
                    return grounded, FieldCorrection(
                        original=field_ref,
                        grounded=grounded,
                        strategy="fuzzy",
                        confidence=0.6,
                        note=f"Fuzzy matched '{ref_field}' to '{prop}' in '{type_name}'",
                    )

        # 5. Unresolved
        return field_ref, FieldCorrection(
            original=field_ref,
            grounded=field_ref,
            strategy="unresolved",
            confidence=0.0,
            note=f"No matching field found for '{field_ref}'",
        )
