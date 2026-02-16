"""Graph extraction and context formatting mixin for GraphService."""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from typing import Any

from ..core.validation import validate_user_id
from .prompts import GRAPH_EXTRACTION_USER_PROMPT, build_extraction_system_prompt

logger = logging.getLogger(__name__)


def _normalize_node_id(raw_id: str) -> str:
    """Normalize a node ID for deterministic consistency across LLM calls.

    The LLM produces inconsistent IDs: "User's Son" vs "Users Son" vs
    "user_s_son". This function enforces one canonical form so that
    ``upsert_node`` merges them by ``_id`` instead of creating duplicates.

    Steps:
        1. Split ``type:name``
        2. Unicode normalize (strip accents)
        3. Lowercase
        4. Replace hyphens and whitespace with underscores
        5. Strip all remaining punctuation (apostrophes, periods, etc.)
        6. Collapse consecutive underscores
        7. Reassemble ``type:name``

    Examples::

        "person:User's Son"   -> "person:users_son"
        "person:Users Son"    -> "person:users_son"
        "concept:peanut-allergy" -> "concept:peanut_allergy"
        "person:Dr. Smith"    -> "person:dr_smith"
        "interest:PB&J"       -> "interest:pbj"
    """
    raw_id = raw_id.strip()
    if not raw_id:
        return raw_id

    # Split type:name
    if ":" in raw_id:
        node_type, name_part = raw_id.split(":", 1)
    else:
        node_type = "concept"
        name_part = raw_id

    # Unicode normalize (strip accents like é -> e)
    name_part = unicodedata.normalize("NFKD", name_part)
    name_part = "".join(c for c in name_part if not unicodedata.combining(c))

    # Lowercase
    node_type = node_type.lower().strip()
    name_part = name_part.lower().strip()

    # Replace hyphens and spaces with underscores FIRST (preserves word boundaries)
    name_part = re.sub(r"[\s\-]+", "_", name_part)

    # Strip all remaining non-alphanumeric/non-underscore chars (apostrophes, &, etc.)
    name_part = re.sub(r"[^a-z0-9_]", "", name_part)

    # Collapse consecutive underscores
    name_part = re.sub(r"_+", "_", name_part).strip("_")

    # Clean type the same way
    node_type = re.sub(r"[^a-z0-9_]", "", node_type)

    if not name_part:
        return raw_id
    return f"{node_type}:{name_part}"


class ExtractionMixin:
    """Mixin providing LLM-based graph extraction and context formatting.

    Expects the composing class to provide:
        self._enabled, self.auto_extract, self.llm_service,
        self.llm_model, self.temperature,
        self.upsert_node(), self.get_node(), self.add_edge()
    """

    async def _persist_extracted_graph(
        self,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        user_id: str,
        source_memory_id: str | None,
    ) -> tuple[int, int]:
        """Persist extracted nodes and edges to the graph store.

        Args:
            nodes: List of node dictionaries.
            edges: List of edge dictionaries.
            user_id: User ID for ownership.
            source_memory_id: Optional memory ID.

        Returns:
            Tuple of (nodes_created, edges_created).
        """
        nodes_created = 0
        edges_created = 0

        for node in nodes:
            node_id = node.get("id", "")

            await self.upsert_node(
                node_id=node_id,
                node_type=node.get("type", "concept"),
                name=node.get("name", node_id),
                properties=node.get("properties", {}),
                user_id=user_id,
                source_memory_ids=[source_memory_id] if source_memory_id else None,
            )
            nodes_created += 1

        for edge in edges:
            source = edge.get("source", "")
            target = edge.get("target", "")

            # Ensure source node exists (auto-create if missing)
            if not await self.get_node(source):
                source_type = source.split(":")[0] if ":" in source else "concept"
                source_name = source.split(":")[-1].replace("_", " ").title()
                await self.upsert_node(
                    node_id=source,
                    node_type=source_type,
                    name=source_name,
                    user_id=user_id,
                    source_memory_ids=[source_memory_id] if source_memory_id else None,
                )

            # Ensure target node exists
            if not await self.get_node(target):
                target_type = target.split(":")[0] if ":" in target else "concept"
                target_name = target.split(":")[-1].replace("_", " ").title()
                await self.upsert_node(
                    node_id=target,
                    node_type=target_type,
                    name=target_name,
                    user_id=user_id,
                    source_memory_ids=[source_memory_id] if source_memory_id else None,
                )

            if await self.add_edge(
                source_id=source,
                relation=edge.get("relation", "related_to"),
                target_id=target,
                properties=edge.get("properties", {}),
            ):
                edges_created += 1

        return nodes_created, edges_created

    async def _get_llm_extraction(
        self, text: str, user_id: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
        """Get raw extracted nodes and edges from LLM."""
        if not self.llm_service:
            logger.warning("LLM service not available for graph extraction")
            return None

        # Use extraction provider for maximum speed
        extraction_provider = None
        try:
            extraction_provider = self.llm_service.get_provider("extraction")
        except (AttributeError, KeyError):
            extraction_provider = self.llm_service.get_provider("chat")

        if not extraction_provider:
            logger.warning("No LLM provider available for graph extraction")
            return None

        model_to_use = self.llm_model or extraction_provider.default_model
        provider_name = "extraction" if "extraction" in self.llm_service.providers else "chat"

        # Sanitize user-controlled text
        try:
            from ..core.prompt_safety import sanitize_for_prompt

            safe_text = sanitize_for_prompt(text, tag="user_text")
        except ImportError:
            safe_text = text

        system_prompt = build_extraction_system_prompt(
            osi_context=None,
            custom_node_types=getattr(self, "node_types", None),
            enable_definition_detection=getattr(self, "_osi_discovery_enabled", False),
        )

        response = await self.llm_service.chat_completion(
            provider_name=provider_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": GRAPH_EXTRACTION_USER_PROMPT.format(
                        text=safe_text,
                        user_id=user_id,
                    ),
                },
            ],
            model=model_to_use,
            response_format={"type": "json_object"},
            temperature=self.temperature,
        )

        # Strip markdown code fences
        response = response.strip()
        if response.startswith("```"):
            response = re.sub(r"^```\w*\n?", "", response)
            response = re.sub(r"\n?```$", "", response)
            response = response.strip()

        try:
            extracted = json.loads(response)
            return extracted.get("nodes", []), extracted.get("edges", [])
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse graph extraction JSON: {e}")
            return [], []

    def _normalize_extracted_entities(
        self, nodes: list[dict[str, Any]], edges: list[dict[str, Any]], user_id: str
    ) -> None:
        """Normalize IDs and types in extracted nodes and edges in-place."""
        # Normalize all IDs
        for node in nodes:
            node["id"] = _normalize_node_id(node.get("id", ""))
            if node.get("type"):
                node["type"] = re.sub(r"[^a-z0-9_]", "", node["type"].lower().strip())
        for edge in edges:
            edge["source"] = _normalize_node_id(edge.get("source", ""))
            edge["target"] = _normalize_node_id(edge.get("target", ""))

        # Replace person:user placeholder
        for node in nodes:
            if node.get("id") == "person:user":
                node["id"] = f"person:{user_id}"
        for edge in edges:
            if edge.get("source") == "person:user":
                edge["source"] = f"person:{user_id}"
            if edge.get("target") == "person:user":
                edge["target"] = f"person:{user_id}"

    def _resolve_osi_entities(self, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Resolve entities using OSI registry and update edges. Returns updated nodes."""
        osi_registry = getattr(self, "_osi_registry", None) or (
            self.config.get("_osi_registry") if hasattr(self, "config") else None
        )

        if not osi_registry or not nodes:
            return nodes

        try:
            from ..osi.resolver import resolve_entities

            # Capture pre-resolution IDs to remap edge references
            pre_osi_ids = [node.get("id", "") for node in nodes]
            resolved_nodes = resolve_entities(nodes, osi_registry)

            # Build old→new mapping for any IDs that changed
            id_remap = {}
            for old_id, node in zip(pre_osi_ids, resolved_nodes, strict=False):
                new_id = node.get("id", "")
                if old_id != new_id:
                    id_remap[old_id] = new_id

            # Apply remap to edge source/target so they stay in sync
            if id_remap:
                for edge in edges:
                    src = edge.get("source", "")
                    tgt = edge.get("target", "")
                    if src in id_remap:
                        edge["source"] = id_remap[src]
                    if tgt in id_remap:
                        edge["target"] = id_remap[tgt]

            return resolved_nodes
        except ImportError:
            return nodes

    async def extract_graph_from_text(
        self,
        text: str,
        user_id: str,
        auto_create_nodes: bool = True,
        source_memory_id: str | None = None,
    ) -> dict[str, Any]:
        """Extract entities and relationships from text using LLM.

        Args:
            text: The text to extract entities/relationships from.
            user_id: User ID for ownership.
            auto_create_nodes: Whether to auto-create extracted nodes.
            source_memory_id: Optional memory ID that produced this text.
                When provided, graph nodes are linked back to the memory
                via ``source_memory_ids`` for cleanup on memory deletion.
        """
        if not self._enabled:
            return {"nodes_created": 0, "edges_created": 0, "extracted": None}

        await self._ensure_ready()
        validate_user_id(user_id)

        if not self.auto_extract:
            logger.debug("Auto-extraction disabled")
            return {"nodes_created": 0, "edges_created": 0, "extracted": None}

        try:
            # 1. Get raw extraction from LLM
            result = await self._get_llm_extraction(text, user_id)
            if not result:
                return {"nodes_created": 0, "edges_created": 0, "extracted": None}
            nodes, edges = result

            # 2. Normalize entities (IDs, types, user placeholder)
            self._normalize_extracted_entities(nodes, edges, user_id)

            # 3. Resolve OSI entities (if enabled)
            nodes = self._resolve_osi_entities(nodes, edges)

            # 4. Persist to graph store
            nodes_created = 0
            edges_created = 0

            if auto_create_nodes:
                nodes_created, edges_created = await self._persist_extracted_graph(
                    nodes, edges, user_id, source_memory_id
                )

            logger.info(
                f"Graph extraction complete: {nodes_created} nodes, {edges_created} edges "
                f"from text ({len(text)} chars)"
            )

            return {
                "nodes_created": nodes_created,
                "edges_created": edges_created,
                "extracted": {"nodes": nodes, "edges": edges},
            }

        except (
            AttributeError,
            KeyError,
            TypeError,
            ValueError,
            RuntimeError,
            json.JSONDecodeError,
        ) as e:
            logger.warning(f"Graph extraction failed: {e}")
            return {"nodes_created": 0, "edges_created": 0, "extracted": None, "error": str(e)}

    def format_graph_context(
        self,
        hybrid_results: dict[str, Any],
        max_nodes: int = 10,
        include_edges: bool = True,
    ) -> str:
        """Format hybrid search results as a context string for LLM prompts."""
        graph_context_items = hybrid_results.get("graph_context", [])
        if not graph_context_items:
            graph_context_items = hybrid_results.get("context_nodes", [])

        entry_nodes = hybrid_results.get("entry_nodes", [])

        if not entry_nodes and not graph_context_items:
            return ""

        lines = ["KNOWLEDGE GRAPH CONTEXT:"]

        for node in entry_nodes[:max_nodes]:
            node_str = f"- {node.get('name', node['_id'])} ({node.get('type', 'unknown')})"
            props = node.get("properties", {})
            if props:
                prop_str = ", ".join(f"{k}: {v}" for k, v in list(props.items())[:3])
                node_str += f" [{prop_str}]"
            lines.append(node_str)

            if include_edges:
                for edge in node.get("edges", [])[:5]:
                    if edge.get("active", True):
                        lines.append(f"  -> {edge['relation']} -> {edge['target']}")

        normalized_items = []
        for item in graph_context_items:
            if isinstance(item, dict):
                if "node" in item:
                    normalized_items.append(item)
                else:
                    normalized_items.append({"node": item, "hop_distance": 0})

        remaining = max_nodes - len(entry_nodes)
        for item in normalized_items[:remaining]:
            node = item.get("node", item)
            distance = item.get("hop_distance", 0)
            node_name = node.get("name", node.get("_id", "unknown"))
            node_type = node.get("type", "unknown")
            node_str = f"- {node_name} ({node_type}) [hop={distance}]"
            lines.append(node_str)

            if include_edges:
                for edge in node.get("edges", [])[:3]:
                    if edge.get("active", True):
                        lines.append(f"  -> {edge['relation']} -> {edge['target']}")

        return "\n".join(lines)

    def format_graph_narrative(
        self,
        graph_result: dict[str, Any],
        max_nodes: int = 10,
    ) -> str:
        """Converts graph nodes/edges into a raw narrative for LLM consumption."""
        if not graph_result or not graph_result.get("context_nodes"):
            return ""

        nodes = graph_result.get("context_nodes", [])[:max_nodes]
        strategy = graph_result.get("strategy", "neighborhood")
        entry_entities = graph_result.get("entry_entities", [])

        graph_dump = f"Search Strategy: {strategy}\n"
        if entry_entities:
            graph_dump += f"Entry Entities: {', '.join(entry_entities)}\n"
        graph_dump += "\n"

        for node in nodes:
            name = node.get("name", node.get("_id", "Unknown"))
            type_ = node.get("type", "concept")
            edges = node.get("edges", [])
            hop_distance = node.get("hop_distance", "?")

            connections = []
            for edge in edges:
                if edge.get("active", True):
                    target = edge.get("target", "")
                    if ":" in target:
                        target = target.split(":")[-1]
                    connections.append(f"{edge.get('relation', 'related')} {target}")

            connections_str = ", ".join(connections[:5])
            if len(connections) > 5:
                connections_str += f" (and {len(connections) - 5} more)"

            graph_dump += f"Node: {name} ({type_})"
            if hop_distance != "?":
                graph_dump += f" [hop={hop_distance}]"
            if node.get("search_strategy") == "path_connection":
                graph_dump += " [PATH NODE]"
            graph_dump += f". Links: {connections_str}\n"

        return f"[RAW GRAPH DATA]\n{graph_dump}\n[/RAW GRAPH DATA]"
