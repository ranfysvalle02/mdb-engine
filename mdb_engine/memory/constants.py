"""
Shared constants for the memory subsystem.

Centralised here to avoid circular imports between modules like
``scoring`` and ``extraction`` that both need these values.
"""

# Category priority: higher number = more specific / higher priority.
# Used for deduplication and category resolution across the memory system.
CATEGORY_PRIORITY: dict[str, int] = {
    "biographical": 4,
    "preferences": 3,
    "temporal": 2,
    "relational": 1,
}
