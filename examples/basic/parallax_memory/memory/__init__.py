"""
Memory Module - Cross-Call Intelligence for Parallax

Demonstrates mdb-engine's Memory component for tracking customer preferences,
pain points, and journey across multiple calls.

Longitudinal AI Components:
- IntelligenceOrchestrator: The "magic layer" that synthesizes cross-call intelligence
- Sentinel: Pattern recognition (sentiment velocity, topic clustering)
- ContinuityManager: Context injection for seamless interactions
- ContradictionDetector: Identifies inconsistencies and opportunities
- CustomerProfileManager: Dynamic customer profiles with relationship health scoring
"""

from .memory_extractor import MemoryExtractor
from .memory_retriever import MemoryRetriever
from .intelligence_orchestrator import IntelligenceOrchestrator
from .sentinel import Sentinel
from .continuity_manager import ContinuityManager
from .contradiction_detector import ContradictionDetector
from .customer_profile import CustomerProfileManager

__all__ = [
    "MemoryExtractor",
    "MemoryRetriever",
    "IntelligenceOrchestrator",
    "Sentinel",
    "ContinuityManager",
    "ContradictionDetector",
    "CustomerProfileManager",
]
