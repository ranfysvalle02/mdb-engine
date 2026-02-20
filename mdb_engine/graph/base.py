"""
Base classes for Graph Service.

Defines the abstract interface that all graph service implementations must follow.
"""

from abc import ABC, abstractmethod
from typing import Any

from ..exceptions import MongoDBEngineError


class GraphServiceError(MongoDBEngineError):
    """Base exception for Graph Service failures."""

    pass


class BaseGraphService(ABC):
    """
    Abstract base class for Graph Service implementations.

    Defines the interface for knowledge graph operations including:
    - Node CRUD operations
    - Edge management with weights and temporal flags
    - Multi-hop graph traversal using $graphLookup
    - Hybrid search (vector + graph)
    - LLM-powered entity extraction

    All implementations must be app-scoped for multi-tenant isolation.
    """

    # ========================================================================
    # Node Operations
    # ========================================================================

    @abstractmethod
    async def upsert_node(
        self,
        node_id: str,
        node_type: str,
        name: str,
        properties: dict[str, Any] | None = None,
        user_id: str | None = None,
        embedding: list[float] | None = None,
        source_memory_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Create or update a node in the graph.

        Args:
            node_id: Unique node ID (format: type:identifier, e.g., "person:alex")
            node_type: Node type (person, interest, event, location, organization, etc.)
            name: Display name for the node
            properties: Optional type-specific properties
            user_id: Optional user ID for ownership
            embedding: Optional embedding vector for hybrid search
            source_memory_ids: Optional list of memory IDs that produced this node.
                Appended (not replaced) on upsert so multiple memories can
                reference the same entity.

        Returns:
            The upserted node document

        Raises:
            GraphServiceError: If upsert fails
        """
        pass

    async def remove_memory_references(self, memory_id: str) -> int:
        """
        Remove a memory reference from all graph nodes.

        Pulls *memory_id* from every node's ``source_memory_ids`` array.
        Nodes that become orphaned (empty ``source_memory_ids``) are deleted
        along with any edges pointing to them.

        Args:
            memory_id: The memory ID to remove.

        Returns:
            Number of nodes deleted (orphaned).
        """
        raise NotImplementedError

    @abstractmethod
    def get_node(self, node_id: str) -> dict[str, Any] | None:
        """
        Get a node by ID.

        Args:
            node_id: Node ID

        Returns:
            Node document or None if not found
        """
        pass

    @abstractmethod
    def delete_node(self, node_id: str) -> bool:
        """
        Delete a node and all its edges.

        Args:
            node_id: Node ID to delete

        Returns:
            True if deleted, False otherwise
        """
        pass

    @abstractmethod
    async def list_nodes(
        self,
        node_type: str | None = None,
        user_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        List nodes with optional filtering.

        Args:
            node_type: Filter by node type
            user_id: Filter by user ID
            limit: Maximum nodes to return

        Returns:
            List of node documents
        """
        pass

    # ========================================================================
    # Edge Operations
    # ========================================================================

    @abstractmethod
    async def add_edge(
        self,
        source_id: str,
        relation: str,
        target_id: str,
        properties: dict[str, Any] | None = None,
        weight: float = 1.0,
        active: bool = True,
    ) -> bool:
        """
        Add an edge (relationship) between two nodes.

        Args:
            source_id: Source node ID
            relation: Relationship type (e.g., "likes", "knows", "works_at")
            target_id: Target node ID
            properties: Optional edge properties
            weight: Edge weight (0.0 to 1.0, default 1.0)
            active: Whether the edge is active (default True)

        Returns:
            True if edge was added/updated
        """
        pass

    @abstractmethod
    def remove_edge(
        self,
        source_id: str,
        relation: str,
        target_id: str,
    ) -> bool:
        """
        Remove an edge between two nodes.

        Args:
            source_id: Source node ID
            relation: Relationship type
            target_id: Target node ID

        Returns:
            True if edge was removed
        """
        pass

    @abstractmethod
    def update_edge(
        self,
        source_id: str,
        relation: str,
        target_id: str,
        updates: dict[str, Any],
    ) -> bool:
        """
        Update an existing edge's properties.

        Args:
            source_id: Source node ID
            relation: Relationship type
            target_id: Target node ID
            updates: Dictionary of fields to update (weight, active, properties)

        Returns:
            True if edge was updated
        """
        pass

    @abstractmethod
    def deactivate_edge(
        self,
        source_id: str,
        relation: str,
        target_id: str,
    ) -> bool:
        """
        Mark an edge as inactive (soft delete).

        Useful for temporal relationships that have ended.

        Args:
            source_id: Source node ID
            relation: Relationship type
            target_id: Target node ID

        Returns:
            True if edge was deactivated
        """
        pass

    # ========================================================================
    # Graph Traversal
    # ========================================================================

    @abstractmethod
    async def traverse(
        self,
        start_id: str,
        max_depth: int | None = None,
        relation_filter: list[str] | None = None,
        include_inactive: bool = False,
        include_start: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Perform graph traversal using MongoDB's $graphLookup.

        Returns all nodes reachable from start_id within max_depth hops.

        Args:
            start_id: Starting node ID
            max_depth: Maximum traversal depth
            relation_filter: Optional list of relation types to follow
            include_inactive: Include inactive edges (default: False)
            include_start: Include the starting node in results (default: True)

        Returns:
            List of dicts with 'node' and 'hop_distance' keys, sorted by distance
        """
        pass

    @abstractmethod
    def get_neighbors(
        self,
        node_id: str,
        relation: str | None = None,
        include_inactive: bool = False,
    ) -> list[dict[str, Any]]:
        """
        Get immediate neighbors of a node (1-hop traversal).

        Args:
            node_id: Node ID
            relation: Optional relation type to filter
            include_inactive: Include inactive edges

        Returns:
            List of neighbor nodes with edge info
        """
        pass

    @abstractmethod
    async def find_path(
        self,
        start_id: str,
        end_id: str,
        max_depth: int = 5,
    ) -> list[str] | None:
        """
        Find a path between two nodes using BFS.

        Args:
            start_id: Starting node ID
            end_id: Ending node ID
            max_depth: Maximum search depth

        Returns:
            List of node IDs forming the path, or None if no path found
        """
        pass

    # ========================================================================
    # Hybrid Search (Vector + Graph)
    # ========================================================================

    @abstractmethod
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
        """
        Hybrid GraphRAG: Vector search finds entry points, graph traversal expands context.

        This is the core GraphRAG pattern:
        1. Vector search finds semantically similar nodes (entry points)
        2. Graph traversal explores the neighborhood of each entry point
        3. Results are deduplicated and ranked

        Args:
            query: Search query text
            user_id: Optional user ID to scope search
            max_depth: Traversal depth
            vector_limit: Max entry nodes from vector search
            include_inactive: Include inactive edges in traversal
            min_hop_distance: Minimum hop distance for graph_context nodes (filters shallow nodes)
            min_edge_count: Minimum edges required for graph_context nodes (filters isolated nodes)
            include_inactive: Include inactive edges in traversal

        Returns:
            Dict with:
                - entry_nodes: Vector search results (entry points)
                - graph_context: Traversed related nodes
                - total_nodes: Total unique nodes found
        """
        pass

    @abstractmethod
    def format_graph_context(
        self,
        hybrid_results: dict[str, Any],
        max_nodes: int = 10,
        include_edges: bool = True,
    ) -> str:
        """
        Format hybrid search results as a context string for LLM prompts.

        Args:
            hybrid_results: Results from hybrid_search()
            max_nodes: Maximum nodes to include
            include_edges: Include edge information

        Returns:
            Formatted context string
        """
        pass

    # ========================================================================
    # LLM-Based Graph Extraction
    # ========================================================================

    @abstractmethod
    async def extract_graph_from_text(
        self,
        text: str,
        user_id: str,
        auto_create_nodes: bool = True,
    ) -> dict[str, Any]:
        """
        Extract entities and relationships from text using LLM.

        This method:
        1. Sends text to LLM for entity/relationship extraction
        2. Creates nodes for extracted entities
        3. Creates edges for extracted relationships

        Args:
            text: Text to extract graph from
            user_id: User ID for node ownership
            auto_create_nodes: Automatically create extracted nodes

        Returns:
            Dict with:
                - nodes_created: Number of nodes created
                - edges_created: Number of edges created
                - extracted: Raw extraction result
        """
        pass

    # ========================================================================
    # Statistics
    # ========================================================================

    @abstractmethod
    async def get_stats(self) -> dict[str, Any]:
        """
        Get graph service statistics.

        Returns:
            Dict with node counts, edge counts, etc.
        """
        pass

    # ========================================================================
    # GraphRAG Search Methods
    # ========================================================================

    @abstractmethod
    async def local_search(
        self,
        query: str,
        user_id: str | None = None,
        max_depth: int = 2,
    ) -> dict[str, Any]:
        """
        Local Search: Entity-focused queries using graph traversal and community summaries.

        Args:
            query: User query string
            user_id: Optional user ID for scoping
            max_depth: Maximum traversal depth

        Returns:
            Dict with entry_nodes, graph_context, community_summaries, etc.
        """
        pass

    @abstractmethod
    async def global_search(
        self,
        query: str,
        user_id: str | None = None,
        max_communities: int = 10,
    ) -> dict[str, Any]:
        """
        Global Search: Thematic queries using map-reduce over community summaries.

        Args:
            query: User query string
            user_id: Optional user ID for scoping
            max_communities: Maximum number of communities to process

        Returns:
            Dict with communities, partial_responses, synthesized_answer, etc.
        """
        pass

    @abstractmethod
    async def drift_search(
        self,
        query: str,
        user_id: str | None = None,
        max_depth: int = 2,
    ) -> dict[str, Any]:
        """
        DRIFT Search: Entity-focused queries with community context.

        Args:
            query: User query string
            user_id: Optional user ID for scoping
            max_depth: Maximum traversal depth

        Returns:
            Dict with entry_nodes, graph_context, community_context, etc.
        """
        pass

    @abstractmethod
    async def classify_query(self, query: str) -> str:
        """
        Classify a query to determine the appropriate search method.

        Args:
            query: User query string

        Returns:
            One of: "local", "global", "drift", or "basic"
        """
        pass

    # ========================================================================
    # Properties
    # ========================================================================

    @property
    @abstractmethod
    def enabled(self) -> bool:
        """Whether the graph service is enabled."""
        pass

    @property
    @abstractmethod
    def app_slug(self) -> str:
        """The application slug this service is scoped to."""
        pass
