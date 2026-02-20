"""
Vector Hacking Service
======================

Business logic for vector inversion attacks - demonstrating how an LLM
can reverse-engineer hidden text by measuring vector distances.

Architecture Notes:
- Uses constructor injection for testable dependencies
- AttackState manages thread-safe state with asyncio locks
- The main loop runs parallel guesses for faster convergence
"""

import asyncio
import logging
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Optional, Set

import numpy as np

from config import AppConfig

logger = logging.getLogger(__name__)


# ============================================================================
# ATTACK STATE
# ============================================================================


@dataclass
class AttackState:
    """
    Thread-safe state for tracking attack progress.
    
    Uses an asyncio lock to safely update state from concurrent tasks.
    """
    
    current_best_text: str = "Be"
    current_best_error: float = float("inf")
    guesses_made: int = 0
    total_cost: float = 0.0
    match_found: bool = False
    previous_guesses: Set[str] = field(default_factory=set)
    last_error: Optional[str] = None
    stagnation_count: int = 0
    target_text: Optional[str] = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def update(
        self,
        text: str,
        error: float,
        cost: float,
        match_threshold: float,
        similarity_threshold: float,
    ) -> None:
        """Update state with a new guess."""
        async with self._lock:
            self.guesses_made += 1
            self.total_cost += cost
            self.previous_guesses.add(text.lower().strip())

            # Track best guess
            if error < self.current_best_error:
                self.current_best_text = text
                self.current_best_error = error
                self.stagnation_count = 0
            else:
                self.stagnation_count += 1

            # Check for match (only set to True, never back to False)
            if not self.match_found:
                self.match_found = self._check_match(
                    text, error, match_threshold, similarity_threshold
                )

    def _check_match(
        self,
        text: str,
        error: float,
        match_threshold: float,
        similarity_threshold: float,
    ) -> bool:
        """Check if the guess matches the target."""
        # Vector distance match
        if error <= match_threshold:
            logger.info(f"✅ Match found (vector distance: {error:.4f})")
            return True
        
        # Text similarity match (fallback)
        if self.target_text:
            guess = text.lower().strip()
            target = self.target_text.lower().strip()
            
            if guess == target:
                logger.info("✅ Match found (exact text)")
                return True
            
            similarity = SequenceMatcher(None, guess, target).ratio()
            if similarity >= similarity_threshold:
                logger.info(f"✅ Match found (similarity: {similarity:.0%})")
                return True
        
        return False

    async def to_dict(self) -> dict:
        """Get state as a dictionary."""
        async with self._lock:
            return {
                "current_best_text": self.current_best_text,
                "current_best_error": self.current_best_error,
                "guesses_made": self.guesses_made,
                "total_cost": self.total_cost,
                "match_found": self.match_found,
                "last_error": self.last_error,
                "stagnation_count": self.stagnation_count,
            }


# ============================================================================
# VECTOR HACKING SERVICE
# ============================================================================


class VectorHackingService:
    """
    Service for running vector inversion attacks.
    
    Uses constructor injection for clean, testable dependency management.
    Register with the DI container using a factory function.
    
    Example:
        container.register_factory(
            VectorHackingService,
            create_vector_hacking_service,
            Scope.SINGLETON,
        )
    """

    def __init__(
        self,
        embedding_service: Any,
        llm_client: Any,
        config: AppConfig,
    ):
        """
        Initialize the service with dependencies.
        
        Args:
            embedding_service: Service for generating text embeddings
            llm_client: OpenAI client for generating guesses
            config: Application configuration
        """
        self.embedding_service = embedding_service
        self.llm_client = llm_client
        self.config = config
        
        # Runtime state
        self.running = False
        self.task: Optional[asyncio.Task] = None
        self.state: Optional[AttackState] = None
        self.target_vector: Optional[np.ndarray] = None
        self.current_target = config.default_target
        
        logger.info(f"VectorHackingService initialized (model: {config.deployment_name})")

    # ------------------------------------------------------------------------
    # PUBLIC API
    # ------------------------------------------------------------------------

    async def start_attack(self, target: Optional[str] = None) -> dict:
        """
        Start a vector hacking attack against a target phrase.
        
        Args:
            target: The phrase to attack (or use current/default target)
            
        Returns:
            Status dict with 'status', 'target', and 'message' keys
        """
        if self.running:
            return {
                "status": "already_running",
                "message": "Attack already in progress",
                "target": self.current_target,
            }

        # Reset if previous attack found a match
        if self.state and self.state.match_found:
            self._reset_state()

        # Set target
        self.current_target = (target or self.current_target or self.config.default_target).strip()

        # Generate target embedding
        try:
            vectors = await self.embedding_service.embed(
                [self.current_target],
                model=self.config.embedding_model,
            )
            if not vectors:
                return {"status": "error", "error": "Failed to embed target"}
            
            self.target_vector = np.array(vectors[0])
            logger.info(f"Target embedded: '{self.current_target}' (dim: {len(self.target_vector)})")
        except Exception as e:
            logger.error(f"Embedding failed: {e}")
            return {"status": "error", "error": str(e)}

        # Initialize state and start attack loop
        self.state = AttackState(target_text=self.current_target)
        self.running = True
        self.task = asyncio.create_task(self._attack_loop())

        return {
            "status": "started",
            "target": self.current_target,
            "message": f"Attack started against: '{self.current_target}'",
        }

    async def stop_attack(self) -> dict:
        """Stop the current attack."""
        was_running = self.running
        self.running = False

        if self.task:
            self.task.cancel()
            try:
                await asyncio.wait_for(self.task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        self._reset_state()

        return {
            "status": "stopped",
            "message": "Attack stopped" if was_running else "Already stopped",
        }

    async def get_status(self) -> dict:
        """Get current attack status."""
        if not self.state:
            return {
                "status": "ready",
                "running": False,
                "TARGET": self.current_target,
                "MODEL_USED": self.config.deployment_name,
                "READY_FOR_NEXT": True,
                "message": "Ready to start attack",
            }

        state = await self.state.to_dict()
        
        # Calculate proximity percentage
        error = state["current_best_error"]
        proximity = 0
        if error != float("inf") and error > 0:
            proximity = max(0, min(100, (1 - error / 2.0) * 100))

        # Determine status
        if state["match_found"]:
            status, message = "victory", "🎉 Target found!"
            self.running = False
        elif self.running:
            status, message = "running", "Attack in progress..."
        else:
            status, message = "stopped", "Attack stopped"

        return {
            "status": status,
            "running": self.running,
            "CURRENT_BEST_TEXT": state["current_best_text"],
            "CURRENT_BEST_ERROR": error if error != float("inf") else None,
            "PROXIMITY_PERCENT": round(proximity, 2),
            "GUESSES_MADE": state["guesses_made"],
            "TOTAL_COST": round(state["total_cost"], 4),
            "MATCH_FOUND": state["match_found"],
            "LAST_ERROR": state["last_error"],
            "MODEL_USED": self.config.deployment_name,
            "TARGET": self.current_target,
            "READY_FOR_NEXT": not self.running,
            "message": message,
        }

    async def generate_random_target(self) -> str:
        """Generate a random target phrase using the LLM."""
        if not self.llm_client:
            return self.config.default_target

        try:
            response = await asyncio.to_thread(
                self.llm_client.chat.completions.create,
                model=self.config.deployment_name,
                messages=[{
                    "role": "user",
                    "content": (
                        "Generate a short phrase (3-7 words) for a vector guessing game. "
                        "Examples: 'Be mindful of your thoughts', 'Live in the present'. "
                        "Output ONLY the phrase, no quotes."
                    ),
                }],
                temperature=1.2,
            )
            phrase = response.choices[0].message.content.strip().strip('"\'')
            logger.info(f"Generated target: '{phrase}'")
            return phrase or self.config.default_target
        except Exception as e:
            logger.error(f"Failed to generate target: {e}")
            return self.config.default_target

    # ------------------------------------------------------------------------
    # PRIVATE METHODS
    # ------------------------------------------------------------------------

    def _reset_state(self) -> None:
        """Reset attack state."""
        self.state = None
        self.target_vector = None

    async def _attack_loop(self) -> None:
        """Main attack loop - runs parallel guesses until match or cost limit."""
        if not self.state or self.target_vector is None:
            return

        logger.info("🚀 Attack loop started")
        
        try:
            iteration = 0
            while self.running:
                state = await self.state.to_dict()
                
                # Check termination conditions
                if state["match_found"]:
                    logger.info("🎉 Match found - stopping")
                    self.running = False
                    await asyncio.sleep(3)  # Let UI show victory
                    break

                if state["total_cost"] >= self.config.cost_limit:
                    logger.info(f"💰 Cost limit reached: ${state['total_cost']:.2f}")
                    self.running = False
                    break

                # Run parallel guesses for faster convergence
                await asyncio.gather(
                    *[self._make_guess() for _ in range(3)],
                    return_exceptions=True,
                )

                # Log progress periodically
                iteration += 1
                if iteration % 10 == 0:
                    state = await self.state.to_dict()
                    if state["current_best_error"] != float("inf"):
                        logger.info(
                            f"Progress: {state['guesses_made']} guesses, "
                            f"error: {state['current_best_error']:.4f}"
                        )

                await asyncio.sleep(0.3)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Attack loop error: {e}")
            self.running = False

    async def _make_guess(self) -> None:
        """Generate and evaluate a single guess."""
        if not self.state or self.target_vector is None:
            return

        state = await self.state.to_dict()
        if state["match_found"]:
            return

        # Build prompt with current best guess info
        error_str = f"{state['current_best_error']:.4f}" if state["current_best_error"] != float("inf") else "∞"
        prompt = f"""You are guessing a hidden phrase by measuring vector distances.

BEST: "{state['current_best_text']}" (error: {error_str})
The target has ~{len(self.current_target.split())} words.
First word hint: "{self.current_target.split()[0]}"

Output ONLY your next guess (the complete phrase)."""

        try:
            # Generate guess via LLM
            response = await asyncio.to_thread(
                self.llm_client.chat.completions.create,
                model=self.config.deployment_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.config.temperature,
                max_tokens=15,
            )
            text = response.choices[0].message.content.strip().strip('"\'')
            
            # Skip duplicates
            if text.lower() in state.get("previous_guesses", set()):
                return

            # Get embedding and calculate distance
            vectors = await self.embedding_service.embed(
                [text],
                model=self.config.embedding_model,
            )
            if not vectors:
                return

            error = float(np.linalg.norm(self.target_vector - np.array(vectors[0])))
            
            if error < 0.45:
                logger.info(f"🔥 Hot guess: '{text}' (error: {error:.4f})")

            # Update state
            cost = 0.0002  # Approximate cost per guess
            await self.state.update(
                text=text,
                error=error,
                cost=cost,
                match_threshold=self.config.match_threshold,
                similarity_threshold=self.config.text_similarity_threshold,
            )

        except Exception as e:
            if self.state:
                async with self.state._lock:  # noqa: SLF001
                    self.state.last_error = str(e)
