"""Search mixin for GraphService — hybrid, advanced, local, global, drift search."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from pymongo.errors import OperationFailure, PyMongoError

from ..core.validation import validate_user_id

logger = logging.getLogger(__name__)


class SearchMixin:
    """Mixin providing hybrid vector+graph search and GraphRAG search methods.

    Expects the composing class to provide:
        self.collection, self._app_slug, self._enabled,
        self.default_max_depth, self.vector_index_name,
        self.embedding_service, self.llm_service, self.config,
        self._get_embedding(), self.traverse(), self.find_path(),
        self.get_node(), self.hybrid_search(), self.advanced_graph_search(),
        self.local_search()
    """

    _community_service: Any | None = None

    def _get_community_service(self) -> Any:
        """Lazily create and cache a CommunityService instance."""
        if self._community_service is None:
            from .community import get_community_service

            db = self.collection.database
            community_collection = db[f"{self._app_slug}__kg_communities"]

            self._community_service = get_community_service(
                app_slug=self._app_slug,
                node_collection=self.collection,
                community_collection=community_collection,
                config=self.config.get("graphrag_config", {}).get("community_detection", {}),
                llm_service=self.llm_service,
                embedding_service=self.embedding_service,
            )
        return self._community_service

    async def hybrid_search(
        self,
        query: str,
        user_id: str | None = None,
        max_depth: int | None = None,
        vector_limit: int = 5,
        include_inactive: bool = False,
        min_hop_distance: int | None = None,
        min_edge_count: int | None = None,
    ) -> dict[str, Any]:
        """Hybrid GraphRAG: Vector search finds entry points, graph traversal expands context."""
        if not self._enabled:
            return {"entry_nodes": [], "graph_context": [], "total_nodes": 0}

        await self._ensure_ready()
        validate_user_id(user_id, allow_none=True)

        if max_depth is None:
            max_depth = self.default_max_depth

        # Step 1: Get embedding for query
        embedding = await self._get_embedding(query)
        if not embedding:
            logger.warning(f"No embedding available for hybrid search query: {query}")
            return {"entry_nodes": [], "graph_context": [], "total_nodes": 0}

        # Step 2: Vector search to find entry nodes
        vector_filter: dict[str, Any] = {"app_slug": self._app_slug}
        if user_id:
            vector_filter["user_id"] = str(user_id)

        entry_pipeline: list[dict[str, Any]] = [
            {
                "$vectorSearch": {
                    "index": self.vector_index_name,
                    "path": "embedding",
                    "queryVector": embedding,
                    "numCandidates": vector_limit * 10,
                    "limit": vector_limit,
                    "filter": vector_filter,
                }
            },
            {"$addFields": {"similarity": {"$meta": "vectorSearchScore"}}},
            {
                "$project": {
                    "_id": 1,
                    "type": 1,
                    "name": 1,
                    "properties": 1,
                    "edges": 1,
                    "similarity": 1,
                }
            },
        ]

        try:
            cursor = self.collection.aggregate(entry_pipeline)
            entry_nodes = await cursor.to_list(length=vector_limit * 2)
            logger.info(f"Vector search found {len(entry_nodes)} entry nodes")
        except (PyMongoError, OperationFailure) as e:
            logger.warning(f"Vector search failed: {e}")
            entry_nodes = []

        # Step 2b: NO FALLBACK - Require embeddings for vector search
        # If vector search found no results, this is a failure that needs to be addressed
        if not entry_nodes:
            logger.error(
                f"Vector search returned 0 results. NO FALLBACK - This needs to be fixed. "
                f"query='{query[:100]}...', user_id={user_id}. "
                f"Nodes may not have embeddings. "
                f"Use POST /api/graph/backfill-embeddings to generate embeddings for all nodes."
            )
            # NO FALLBACK: Return empty results with clear error message
            # Caller should handle this appropriately (e.g., show error to user or trigger backfill)
            return {
                "entry_nodes": [],
                "context_nodes": [],
                "total_nodes": 0,
                "error": "No nodes found - embeddings may be missing. Run backfill-embeddings to fix.",
            }

        # Step 3: Graph traversal from each entry node
        graph_context: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        for entry_node in entry_nodes:
            node_id = entry_node["_id"]
            seen_ids.add(node_id)

            # Traverse from this entry point
            traversed = await self.traverse(
                start_id=node_id,
                max_depth=max_depth,
                include_inactive=include_inactive,
                include_start=False,  # Don't duplicate entry nodes
            )

            for item in traversed:
                item_id = item["node"]["_id"]
                if item_id not in seen_ids:
                    seen_ids.add(item_id)

                    # Apply filters
                    hop_distance = item.get("hop_distance", 0)
                    edge_count = len(item.get("node", {}).get("edges", []))

                    # Filter by min_hop_distance if specified
                    if min_hop_distance is not None and hop_distance < min_hop_distance:
                        continue

                    # Filter by min_edge_count if specified
                    if min_edge_count is not None and edge_count < min_edge_count:
                        continue

                    # Add entry node info for context
                    item["entry_node"] = node_id
                    item["entry_similarity"] = entry_node.get("similarity", 0)
                    graph_context.append(item)

        # Sort graph context by hop distance, then by entry similarity
        graph_context.sort(key=lambda x: (x["hop_distance"], -x.get("entry_similarity", 0)))

        logger.info(
            f"Hybrid search complete: {len(entry_nodes)} entry nodes, " f"{len(graph_context)} graph context nodes"
        )

        return {
            "entry_nodes": entry_nodes,
            "graph_context": graph_context,
            "total_nodes": len(seen_ids),
        }

    async def extract_query_entities(self, query: str) -> list[str]:
        """
        Extract potential entity names from the user query to use as direct
        graph entry points. Uses LLM to identify people, organizations, projects, etc.

        Args:
            query: User's query string

        Returns:
            List of entity names extracted from the query (e.g., ["Alex", "Project Hades"])
        """
        if not self.llm_service:
            logger.debug("No LLM service available for query entity extraction")
            return []

        try:
            from ..core.prompt_safety import sanitize_for_prompt

            _safe_q = sanitize_for_prompt(query, tag="query")
        except ImportError:
            _safe_q = query

        prompt = f"""Extract the main entities (people, projects, organizations, locations, concepts)
from this query. Return ONLY a JSON object with a "entities" key containing an array of strings.
Query: {_safe_q}

Example response:
{{"entities": ["Alex", "Project Hades", "Google"]}}"""

        try:
            provider_name = "extraction" if "extraction" in self.llm_service.providers else "chat"
            response = await self.llm_service.chat_completion(
                provider_name=provider_name,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.0,
            )
            data = json.loads(response)
            # Handle various JSON structures (key could be 'entities', 'names', etc)
            if isinstance(data, dict):
                entities = data.get("entities", data.get("names", []))
                if isinstance(entities, list):
                    return [str(e) for e in entities if e]
            elif isinstance(data, list):
                return [str(e) for e in data if e]
            return []
        except (json.JSONDecodeError, ConnectionError, TimeoutError, ValueError) as e:
            logger.warning(f"Graph query entity extraction failed: {e}")
            return []

    async def advanced_graph_search(
        self,
        query: str,
        user_id: str | None = None,
        max_depth: int = 2,
    ) -> dict[str, Any]:
        """
        Powerful GraphRAG strategy implementing Microsoft Research principles:
        1. Query Decomposition: Extract entities from query for precise entry points
        2. Pathfinding: If multiple entities found, find connections between them
        3. Community Context: Retrieve dense clusters around entry nodes

        Args:
            query: User's query string
            user_id: User ID for scoping
            max_depth: Maximum traversal depth

        Returns:
            Dict with:
                - strategy: "pathfinding" or "neighborhood"
                - entry_entities: List of entity names used as entry points
                - context_nodes: List of nodes with search_strategy metadata
                - total_nodes: Total number of unique nodes found
        """
        if not self._enabled:
            return {
                "strategy": "neighborhood",
                "entry_entities": [],
                "context_nodes": [],
                "total_nodes": 0,
            }

        # Step 1: Parallel execution - Extract entities & Vector search
        explicit_entities_task = self.extract_query_entities(query)
        vector_search_task = self.hybrid_search(query, user_id, max_depth=1, vector_limit=5)

        gather_results = await asyncio.gather(explicit_entities_task, vector_search_task, return_exceptions=True)

        # Safely unpack — log and default on failure
        if isinstance(gather_results[0], BaseException):
            logger.error("Entity extraction failed: %s", gather_results[0], exc_info=gather_results[0])
            explicit_entities = []
        else:
            explicit_entities = gather_results[0]

        if isinstance(gather_results[1], BaseException):
            logger.error("Vector search failed: %s", gather_results[1], exc_info=gather_results[1])
            vector_results = {"nodes": [], "edges": [], "entry_nodes": []}
        else:
            vector_results = gather_results[1]

        entry_nodes = []

        # Step 2: Resolve explicit entities to Node IDs
        if explicit_entities:
            # Build case-insensitive regex queries for entity name matching
            filter_conditions = []
            for entity in explicit_entities:
                # Try exact match first, then case-insensitive regex
                filter_conditions.append({"name": {"$regex": f"^{re.escape(entity)}$", "$options": "i"}})

            if filter_conditions:
                query_filter: dict[str, Any] = {
                    "app_slug": self._app_slug,
                    "$or": filter_conditions,
                }
                if user_id:
                    query_filter["user_id"] = str(user_id)

                try:
                    cursor = self.collection.find(query_filter)
                    found_nodes = await cursor.to_list(length=50)
                    entry_nodes.extend(found_nodes)
                    logger.info(f"Found {len(found_nodes)} nodes matching explicit entities: {explicit_entities}")
                except (PyMongoError, OperationFailure) as e:
                    logger.warning(f"Entity lookup failed: {e}")

        # Step 3: Merge with vector results (deduplicate by _id)
        seen_ids = {str(n["_id"]) for n in entry_nodes}
        for item in vector_results.get("entry_nodes", []):
            if str(item["_id"]) not in seen_ids:
                entry_nodes.append(item)
                seen_ids.add(str(item["_id"]))

        # Step 4: Strategy Selection
        graph_context = []
        strategy_used = "neighborhood"

        # STRATEGY A: Pathfinding (Connecting the dots)
        # If we have 2+ distinct entry nodes, try to find connections between them
        if len(entry_nodes) >= 2:
            strategy_used = "pathfinding"
            # Take top 3 most relevant nodes
            targets = entry_nodes[:3]

            # Find paths between pairs
            path_nodes_set = set()
            for i in range(len(targets)):
                for j in range(i + 1, len(targets)):
                    start = targets[i]["_id"]
                    end = targets[j]["_id"]

                    path = await self.find_path(start, end, max_depth=3)
                    if path:
                        # Add all nodes in the path
                        path_nodes_set.update(path)
                        logger.info(f"Found path from {start} to {end}: {path}")

            if path_nodes_set:
                # Fetch full node details for path nodes
                try:
                    cursor = self.collection.find(
                        {
                            "_id": {"$in": list(path_nodes_set)},
                            "app_slug": self._app_slug,
                        }
                    )
                    full_nodes = await cursor.to_list(length=100)

                    # Add to context with path strategy marker
                    for n in full_nodes:
                        n["search_strategy"] = "path_connection"
                        graph_context.append(n)
                except (PyMongoError, OperationFailure) as e:
                    logger.warning(f"Failed to fetch path nodes: {e}")

        # STRATEGY B: Weighted Neighborhood Exploration
        # If pathfinding yielded nothing (or only 1 node found), do standard expansion
        if not graph_context or len(entry_nodes) == 1:
            strategy_used = "neighborhood"

            # High-value relationship types for prioritization
            high_value_relations = {
                "parent_of",
                "child_of",
                "sibling_of",
                "spouse_of",
                "works_at",
                "member_of",
                "part_of",
                "interested_in",
                "likes",
                "loves",
            }

            for node in entry_nodes[:3]:  # Top 3 entry points
                # 2-Hop Traversal
                network = await self.traverse(node["_id"], max_depth=max_depth, include_start=False)

                # Intelligent Filtering and Prioritization
                for item in network:
                    n = item["node"]
                    hop_distance = item.get("hop_distance", 999)

                    # Calculate relevance score based on hop distance and edge weights
                    relevance = 1.0 / (hop_distance + 1)

                    # Boost relevance for high-value relationships
                    edges = n.get("edges", [])
                    for edge in edges:
                        if edge.get("relation") in high_value_relations:
                            relevance *= 1.5
                            break

                    n["relevance"] = relevance
                    n["hop_distance"] = hop_distance
                    n["search_strategy"] = "neighbor"
                    graph_context.append(n)

        # Step 5: Deduplicate final context
        unique_context = {}
        for n in graph_context:
            node_id = str(n["_id"])
            if node_id not in unique_context:
                unique_context[node_id] = n
            else:
                # If duplicate, keep the one with higher relevance or path strategy
                existing = unique_context[node_id]
                if n.get("search_strategy") == "path_connection" or n.get("relevance", 0) > existing.get(
                    "relevance", 0
                ):
                    unique_context[node_id] = n

        # Sort by relevance (descending) and hop_distance (ascending)
        final_context = list(unique_context.values())
        final_context.sort(key=lambda x: (-x.get("relevance", 0), x.get("hop_distance", 999)))

        logger.info(
            f"Advanced graph search complete: strategy={strategy_used}, "
            f"{len(entry_nodes)} entry nodes, {len(final_context)} context nodes"
        )

        return {
            "strategy": strategy_used,
            "entry_nodes": entry_nodes,  # Include entry_nodes for local_search compatibility
            "entry_entities": [n.get("name", str(n["_id"])) for n in entry_nodes],
            "context_nodes": final_context,
            "total_nodes": len(entry_nodes) + len(final_context),
        }

    async def local_search(
        self,
        query: str,
        user_id: str | None = None,
        max_depth: int = 2,
    ) -> dict[str, Any]:
        """
        Local Search: Entity-focused queries using graph traversal and community summaries.

        This method:
        1. Resolves entities from query
        2. Performs graph traversal from entry nodes
        3. Retrieves community summaries for traversed nodes
        4. Combines graph context + community summaries

        Args:
            query: User query string
            user_id: Optional user ID for scoping
            max_depth: Maximum traversal depth

        Returns:
            Dict with:
                - entry_nodes: Vector search results (entry points)
                - graph_context: Traversed related nodes
                - community_summaries: Community summaries for context
                - total_nodes: Total unique nodes found
        """
        if not self._enabled:
            return {
                "entry_nodes": [],
                "graph_context": [],
                "community_summaries": [],
                "total_nodes": 0,
            }

        # Use existing advanced_graph_search as base
        results = await self.advanced_graph_search(
            query=query,
            user_id=user_id,
            max_depth=max_depth,
        )

        # Get community summaries for context nodes
        community_summaries = []
        if results.get("context_nodes"):
            try:
                community_service = self._get_community_service()

                # Get node IDs from context
                node_ids = [node.get("_id") for node in results.get("context_nodes", []) if node.get("_id")]

                # Get communities for these nodes
                communities = await community_service.get_communities_for_nodes(node_ids)

                # Extract summaries
                community_summaries = [
                    {
                        "community_id": c.get("_id"),
                        "level": c.get("level"),
                        "summary": c.get("summary", ""),
                        "size": c.get("properties", {}).get("size", 0),
                    }
                    for c in communities
                    if c.get("summary")
                ]
            except (PyMongoError, OperationFailure, ImportError) as e:
                logger.warning(f"Failed to retrieve community summaries: {e}")

        return {
            "query_type": "local",
            "entry_nodes": results.get("entry_nodes", []),
            "graph_context": results.get("context_nodes", []),
            "community_summaries": community_summaries,
            "total_nodes": results.get("total_nodes", 0),
        }

    async def global_search(
        self,
        query: str,
        user_id: str | None = None,
        max_communities: int = 10,
    ) -> dict[str, Any]:
        """
        Global Search: Thematic queries using map-reduce over community summaries.

        This method:
        1. Performs vector search on community summaries (not nodes)
        2. Generates partial response for each relevant community
        3. Synthesizes partial responses into final answer

        Args:
            query: User query string
            user_id: Optional user ID for scoping
            max_communities: Maximum number of communities to process

        Returns:
            Dict with:
                - communities: Relevant communities found
                - partial_responses: Partial responses for each community
                - synthesized_answer: Final synthesized answer (if LLM available)
                - total_communities: Number of communities processed
        """
        if not self._enabled:
            return {
                "communities": [],
                "partial_responses": [],
                "synthesized_answer": None,
                "total_communities": 0,
            }

        try:
            community_service = self._get_community_service()
            community_collection = community_service.community_collection

            # Step 1: Vector search on community summaries
            if not self.embedding_service:
                logger.warning("No embedding service for global search")
                return {
                    "communities": [],
                    "partial_responses": [],
                    "synthesized_answer": None,
                    "total_communities": 0,
                }

            # Get query embedding
            query_embedding = await self._get_embedding(query)
            if not query_embedding:
                logger.warning("Failed to generate query embedding for global search")
                return {
                    "communities": [],
                    "partial_responses": [],
                    "synthesized_answer": None,
                    "total_communities": 0,
                }

            # Vector search on community summaries
            vector_filter: dict[str, Any] = {"app_slug": self._app_slug}
            if user_id:
                vector_filter["user_id"] = str(user_id)

            # Check if vector index exists on community collection
            # For now, fallback to text-based search if no index
            try:
                search_pipeline = [
                    {
                        "$vectorSearch": {
                            "index": "community_vector_index",  # Would need to be created
                            "path": "embedding",
                            "queryVector": query_embedding,
                            "numCandidates": max_communities * 10,
                            "limit": max_communities,
                            "filter": vector_filter,
                        }
                    },
                    {"$addFields": {"similarity": {"$meta": "vectorSearchScore"}}},
                ]
                cursor = community_collection.aggregate(search_pipeline)
                communities = await cursor.to_list(length=max_communities)
            except (PyMongoError, OperationFailure):
                # Fallback: Get all communities and filter by text matching
                logger.debug("Vector search not available, using text-based fallback")
                all_communities = await community_service.list_communities(limit=100)
                # Simple text matching (would be better with embeddings)
                query_words = set(query.lower().split())
                scored_communities = []
                for comm in all_communities:
                    summary = comm.get("summary", "").lower()
                    score = sum(1 for word in query_words if word in summary)
                    if score > 0:
                        scored_communities.append((comm, score))
                scored_communities.sort(key=lambda x: x[1], reverse=True)
                communities = [c[0] for c in scored_communities[:max_communities]]

            if not communities:
                logger.info("No relevant communities found for global search")
                return {
                    "communities": [],
                    "partial_responses": [],
                    "synthesized_answer": None,
                    "total_communities": 0,
                }

            # Step 2: Map-Reduce - Generate partial responses in parallel
            partial_responses = []
            if self.llm_service:
                # Sanitize user query for safe prompt interpolation
                try:
                    from ..core.prompt_safety import sanitize_for_prompt
                except ImportError:
                    sanitize_for_prompt = None  # type: ignore[assignment]

                _safe_query = sanitize_for_prompt(query, tag="query") if sanitize_for_prompt else query

                async def _generate_partial(community: dict[str, Any]) -> dict[str, Any] | None:
                    summary = community.get("summary", "")
                    if not summary:
                        return None
                    try:
                        _safe_summary = (
                            sanitize_for_prompt(summary, tag="community_summary") if sanitize_for_prompt else summary
                        )
                        prompt = (
                            f"Based on this community summary, provide a partial answer to the query.\n\n"
                            f"Query: {_safe_query}\n\n"
                            f"Community Summary:\n{_safe_summary}\n\n"
                            f"Provide a concise partial answer (2-3 sentences) that addresses "
                            f"how this community relates to the query."
                        )
                        partial_response = await self.llm_service.chat_completion(
                            provider_name="chat",
                            messages=[
                                {
                                    "role": "system",
                                    "content": (
                                        "You are providing partial answers "
                                        "that will be synthesized into a complete response."
                                    ),
                                },
                                {"role": "user", "content": prompt},
                            ],
                            temperature=0.5,
                        )
                        if partial_response:
                            return {
                                "community_id": community.get("_id"),
                                "level": community.get("level"),
                                "partial_response": partial_response.strip(),
                            }
                    except (ConnectionError, TimeoutError, ValueError) as e:
                        logger.warning(f"Failed to generate partial response for community {community.get('_id')}: {e}")
                    return None

                results_raw = await asyncio.gather(
                    *(_generate_partial(c) for c in communities),
                    return_exceptions=True,
                )
                partial_responses = [r for r in results_raw if r is not None and not isinstance(r, BaseException)]

            # Step 3: Synthesize partial responses
            synthesized_answer = None
            if partial_responses and self.llm_service:
                try:

                    def _sanitize_partial(text: str) -> str:
                        return sanitize_for_prompt(text, tag="partial") if sanitize_for_prompt else text

                    _safe_partials = chr(10).join(
                        f"{i+1}. {_sanitize_partial(pr['partial_response'])}" for i, pr in enumerate(partial_responses)
                    )
                    synthesis_prompt = f"""Synthesize these partial answers into a comprehensive response to the query.

Query: {_safe_query}

Partial Answers:
{_safe_partials}

Provide a comprehensive, synthesized answer that combines insights from all partial answers."""

                    provider_name = "chat"
                    synthesized_answer = await self.llm_service.chat_completion(
                        provider_name=provider_name,
                        messages=[
                            {
                                "role": "system",
                                "content": (
                                    "You are synthesizing multiple partial answers " "into a comprehensive response."
                                ),
                            },
                            {"role": "user", "content": synthesis_prompt},
                        ],
                        temperature=0.7,
                    )
                except (ConnectionError, TimeoutError, ValueError) as e:
                    logger.warning(f"Failed to synthesize answer: {e}")

            return {
                "query_type": "global",
                "communities": communities,
                "partial_responses": partial_responses,
                "synthesized_answer": synthesized_answer.strip() if synthesized_answer else None,
                "total_communities": len(communities),
            }

        except (
            PyMongoError,
            OperationFailure,
            ImportError,
            ConnectionError,
            TimeoutError,
            ValueError,
        ) as e:
            logger.exception(f"Global search failed: {e}")
            return {
                "communities": [],
                "partial_responses": [],
                "synthesized_answer": None,
                "total_communities": 0,
                "error": str(e),
            }

    async def drift_search(
        self,
        query: str,
        user_id: str | None = None,
        max_depth: int = 2,
    ) -> dict[str, Any]:
        """
        DRIFT Search: Entity-focused queries with community context.

        Similar to Local Search but includes community context for entry nodes.

        Args:
            query: User query string
            user_id: Optional user ID for scoping
            max_depth: Maximum traversal depth

        Returns:
            Dict with:
                - entry_nodes: Vector search results (entry points)
                - graph_context: Traversed related nodes
                - community_context: Community summaries for entry nodes
                - total_nodes: Total unique nodes found
        """
        if not self._enabled:
            return {
                "entry_nodes": [],
                "graph_context": [],
                "community_context": [],
                "total_nodes": 0,
            }

        # Start with local search
        local_results = await self.local_search(
            query=query,
            user_id=user_id,
            max_depth=max_depth,
        )

        # Get community context for entry nodes specifically
        community_context = []
        if local_results.get("entry_nodes"):
            try:
                community_service = self._get_community_service()

                # Get node IDs from entry nodes
                entry_node_ids = [node.get("_id") for node in local_results.get("entry_nodes", []) if node.get("_id")]

                # Get communities for entry nodes
                entry_communities = await community_service.get_communities_for_nodes(entry_node_ids)

                # Extract community context
                community_context = [
                    {
                        "community_id": c.get("_id"),
                        "level": c.get("level"),
                        "summary": c.get("summary", ""),
                        "size": c.get("properties", {}).get("size", 0),
                        "entry_nodes": [nid for nid in c.get("node_ids", []) if nid in entry_node_ids],
                    }
                    for c in entry_communities
                    if c.get("summary")
                ]
            except (PyMongoError, OperationFailure, ImportError) as e:
                logger.warning(f"Failed to retrieve community context: {e}")

        return {
            "query_type": "drift",
            "entry_nodes": local_results.get("entry_nodes", []),
            "graph_context": local_results.get("graph_context", []),
            "community_context": community_context,
            "total_nodes": local_results.get("total_nodes", 0),
        }

    async def classify_query(self, query: str) -> str:
        """
        Classify a query to determine the appropriate search method.

        Args:
            query: User query string

        Returns:
            One of: "local", "global", "drift", "osi_metric", or "basic"
        """
        from .query_classifier import get_query_classifier

        # Get OSI registry if available (for metric-aware classification)
        osi_registry = getattr(self, "_osi_registry", None) or (
            self.config.get("_osi_registry") if hasattr(self, "config") else None
        )

        classifier = get_query_classifier(
            llm_service=self.llm_service,
            use_llm=self.config.get("graphrag_config", {}).get("query_classification", {}).get("use_llm", True),
            cache_results=self.config.get("graphrag_config", {})
            .get("query_classification", {})
            .get("cache_results", True),
            osi_registry=osi_registry,
        )

        return await classifier.classify_query(query)

    async def metric_search(
        self,
        query: str,
        user_id: str | None = None,
        max_depth: int = 2,
    ) -> dict[str, Any]:
        """Search for governed OSI metrics alongside graph context.

        When a user asks about a governed metric (e.g., "What was our revenue?"),
        this method returns the metric definition AND graph context for narrative.

        Args:
            query: User query string
            user_id: Optional user ID for graph context scoping
            max_depth: Maximum traversal depth for graph context

        Returns:
            Dict with query_type, matched metrics, and graph context.
        """
        osi_registry = getattr(self, "_osi_registry", None) or (
            self.config.get("_osi_registry") if hasattr(self, "config") else None
        )

        if not self._enabled or not osi_registry:
            return {
                "query_type": "osi_metric",
                "metrics": [],
                "graph_context": [],
                "total_nodes": 0,
            }

        # Step 1: Find matching metrics
        matched_metrics = []
        query_lower = query.lower()

        try:
            metric_synonyms = osi_registry.get_metric_synonyms()
            for m_name, synonyms in metric_synonyms.items():
                # Check metric name
                if m_name.lower().replace("_", " ") in query_lower:
                    metric_info = osi_registry.get_metric(m_name)
                    if metric_info:
                        matched_metrics.append(self._format_metric_result(m_name, metric_info))
                    continue
                # Check synonyms
                for syn in synonyms:
                    if syn.lower() in query_lower:
                        metric_info = osi_registry.get_metric(m_name)
                        if metric_info:
                            matched_metrics.append(self._format_metric_result(m_name, metric_info))
                        break
        except (AttributeError, TypeError) as e:
            logger.debug("OSI metric lookup unavailable: %s", e)

        # Step 2: Get graph context for related entities
        graph_context = []
        if matched_metrics:
            try:
                hybrid_results = await self.hybrid_search(
                    query=query,
                    user_id=user_id,
                    max_depth=max_depth,
                    vector_limit=5,
                )
                graph_context = hybrid_results.get("graph_context", [])
            except (ValueError, RuntimeError, AttributeError) as e:
                logger.debug("Hybrid search for OSI metric context failed: %s", e)

        return {
            "query_type": "osi_metric",
            "metrics": matched_metrics,
            "graph_context": graph_context,
            "total_nodes": len(graph_context),
        }

    @staticmethod
    def _format_metric_result(name: str, metric_info: dict[str, Any]) -> dict[str, Any]:
        """Format a metric for search results."""
        expression = ""
        dialects = metric_info.get("expression", {})
        if isinstance(dialects, dict):
            dialect_list = dialects.get("dialects", [])
            if dialect_list and isinstance(dialect_list, list):
                expression = dialect_list[0].get("expression", "")

        ai_ctx = metric_info.get("ai_context", {})
        synonyms = ai_ctx.get("synonyms", []) if isinstance(ai_ctx, dict) else []

        return {
            "name": name,
            "description": metric_info.get("description", ""),
            "expression": expression,
            "synonyms": synonyms,
            "model": metric_info.get("_model_name", "unknown"),
        }
