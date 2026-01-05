"""
Vector Hacking Service

This module demonstrates vector inversion attacks using the LLM service abstraction.
All LLM interactions (chat completions and embeddings) go through the unified
LLM service interface configured via manifest.json.

Configuration:
    LLM and embedding models are configured via environment variables:
    - OPENAI_API_KEY or AZURE_OPENAI_API_KEY for chat completions and embeddings
    - Embedding model can be configured in manifest.json embedding_config section

Usage:
    The code uses OpenAI SDK directly for chat completions and EmbeddingService
    (via mem0) for embeddings. All LLM calls use direct API clients.
"""

import asyncio
import logging
import os
import pathlib
from typing import List, Optional

import numpy as np

# Configure logging
logger = logging.getLogger(__name__)

# User-configurable difficulty settings
# Change TARGET to make it easier/harder (shorter = easier, longer = harder)
# Change MATCH_ERROR to adjust precision needed (lower = stricter, higher = more lenient)
TARGET = "Be mindful of your thoughts"  # User can change this
MATCH_ERROR = 0.6  # Vector distance threshold (increased to catch close matches)
TEXT_SIMILARITY_THRESHOLD = 0.85  # Text similarity threshold (0-1, higher = stricter)
COST_LIMIT = 100.0  # Maximum cost before stopping

# LLM Configuration:
# All LLM models and settings are configured via environment variables.
# The code uses OpenAI SDK directly (Azure OpenAI or standard OpenAI) for chat completions.
# Embeddings are handled via EmbeddingService which uses mem0.

# Experiment-local paths
experiment_dir = pathlib.Path(__file__).parent
templates_dir = experiment_dir / "templates"


class SharedState:
    """
    Shared state for vector hacking experiment.
    Thread-safe operations using asyncio locks.
    """

    def __init__(self):
        self.CURRENT_BEST_TEXT = "Be"  # Start with just "Be"
        self.CURRENT_BEST_ERROR = np.inf
        self.GUESSES_MADE = 0
        self.TOTAL_COST = 0.0
        self.MATCH_FOUND = False
        self.PREVIOUS_GUESSES = set()
        self.LAST_ERROR = None  # Store last error for debugging
        self.STAGNATION_COUNT = 0  # Track how many guesses without improvement
        self.target_text = None  # Store target text for text similarity matching
        self._lock = asyncio.Lock()  # Lock for thread-safe operations

    async def increment_attempts(self, count=1):
        async with self._lock:
            self.GUESSES_MADE += count

    async def increment_cost(self, cost: float):
        """Increment total cost (for failed attempts or partial costs)."""
        async with self._lock:
            self.TOTAL_COST += cost

    async def update_best_guess(self, text, error, cost=0.0):
        """
        Update best guess and check for match.

        Uses both vector distance and text similarity for matching.
        """
        async with self._lock:
            self.TOTAL_COST += cost
            self.PREVIOUS_GUESSES.add(text.lower().strip())

            if error < self.CURRENT_BEST_ERROR:
                self.CURRENT_BEST_TEXT = text
                self.CURRENT_BEST_ERROR = error
                self.STAGNATION_COUNT = 0  # Reset stagnation counter
            else:
                self.STAGNATION_COUNT += 1  # Increment if no improvement

            # Check for match using both vector distance and text similarity
            is_match = False

            # Method 1: Vector distance threshold
            if error <= MATCH_ERROR:
                is_match = True
                logger.info(f"Match found via vector distance: {error:.4f} <= {MATCH_ERROR}")

            # Method 2: Text similarity (case-insensitive, handles variations)
            # Use stored target_text from SharedState
            if self.target_text and not is_match:
                guess_normalized = text.lower().strip()
                target_normalized = self.target_text.lower().strip()

                # Exact match (case-insensitive) - handles "Hello World" vs "HELLO WORLD"
                if guess_normalized == target_normalized:
                    is_match = True
                    logger.info(
                        f"Match found via exact text match: '{text}' == '{self.target_text}'"
                    )
                else:
                    # Calculate similarity for near-matches
                    from difflib import SequenceMatcher

                    similarity = SequenceMatcher(None, guess_normalized, target_normalized).ratio()

                    if similarity >= TEXT_SIMILARITY_THRESHOLD:
                        is_match = True
                        logger.info(
                            f"Match found via text similarity: {similarity:.2%} >= {TEXT_SIMILARITY_THRESHOLD:.0%} ('{text}' vs '{self.target_text}')"
                        )

            if is_match:
                self.MATCH_FOUND = True

    async def report_error(self, error_msg):
        async with self._lock:
            self.LAST_ERROR = error_msg

    async def get_state(self):
        async with self._lock:
            return {
                "CURRENT_BEST_TEXT": self.CURRENT_BEST_TEXT,
                "CURRENT_BEST_ERROR": self.CURRENT_BEST_ERROR,
                "GUESSES_MADE": self.GUESSES_MADE,
                "TOTAL_COST": self.TOTAL_COST,
                "PREVIOUS_GUESSES": self.PREVIOUS_GUESSES.copy(),  # Return a copy
                "MATCH_FOUND": self.MATCH_FOUND,
                "LAST_ERROR": self.LAST_ERROR,
                "STAGNATION_COUNT": self.STAGNATION_COUNT,
            }


async def generate_and_evaluate_guess(
    v_target,
    shared_state,
    openai_client,
    embedding_service,
    deployment_name: str,
    embedding_model: str,
    temperature: float = 0.8,
    target_hint=None,
):
    """
    Generate and evaluate a guess using direct Azure OpenAI client and EmbeddingService.

    This function uses:
    - Azure OpenAI client for chat completions
    - EmbeddingService (using mem0) for embeddings

    Args:
        v_target: Target vector (numpy array)
        shared_state: SharedState instance for shared state
        openai_client: AzureOpenAI client instance
        embedding_service: EmbeddingService instance (using mem0)
        deployment_name: Chat model deployment name
        embedding_model: Embedding model name
        temperature: Temperature for chat completions
        target_hint: Optional hint about the target phrase
    """

    # Build prompt with target hint if available
    target_info = ""
    if target_hint:
        words = target_hint.split()
        word_count = len(words)
        first_word = words[0] if words else ""
        target_info = f"""
The target phrase has approximately {word_count} words.
The first word is "{first_word}".
You must guess the COMPLETE phrase that starts with "{first_word}".
"""
    else:
        target_info = """
The target is a SHORT PHRASE (typically 3-7 words).
The phrase likely starts with "Be" but could be different.
You must guess the COMPLETE phrase.
"""

    prompt_template = f"""You are an AI agent trying to reverse-engineer a hidden text by measuring vector distances.

Your task: Given the last guess and its ERROR (distance from target), generate a BETTER guess that will have a LOWER error.

{target_info}

Rules:
- Do NOT repeat previous guesses (listed in [context])
- Your response must be a complete, meaningful phrase
- Do NOT include explanations, just the phrase
- Think semantically - what meaningful phrases exist?
- Use the error feedback to guide your next guess
- The phrase should be natural English, not random words
- Match the word count and style of previous good guesses

[IMPORTANT]
- Output ONLY the phrase, nothing else
- Be creative but logical
- Lower error = closer to target
- Consider common phrases, idioms, or advice
- Pay attention to the first word hint
[/IMPORTANT]
"""

    try:
        # Get state
        state = await shared_state.get_state()

        # Stop if match found already - check early to avoid unnecessary work
        if state["MATCH_FOUND"]:
            return

        # Register attempt immediately
        await shared_state.increment_attempts()

        # Format error safely (handle np.inf)
        error_val = state["CURRENT_BEST_ERROR"]
        if error_val == np.inf or error_val is None:
            error_str = "∞"
        else:
            error_str = f"{error_val:.4f}"

        assist = f"""\nBEST_GUESS: "{state['CURRENT_BEST_TEXT']}" (ERROR {error_str})"""
        previous_guesses = state["PREVIOUS_GUESSES"]

        # Safeguard for prompt size - show more recent guesses
        prev_guesses_list = list(previous_guesses)
        if len(prev_guesses_list) > 30:
            prev_guesses_list = prev_guesses_list[-30:]  # Show last 30

        if prev_guesses_list:
            previous_guesses_str = ", ".join(f'"{guess}"' for guess in prev_guesses_list)
            assist += f"\nPrevious guesses (avoid these): {previous_guesses_str}"
        else:
            assist += "\nNo previous guesses yet."

        m = f"ERROR {error_str}, \"{state['CURRENT_BEST_TEXT']}\""

        # Track cost for this attempt (chat + embedding)
        chat_cost = 0.0
        embed_cost = 0.0

        # Use Azure OpenAI client for chat completion
        try:
            # Create messages for the LLM
            messages = [
                {"role": "system", "content": prompt_template},
                {
                    "role": "user",
                    "content": f"[context]{assist}[/context] \n\n [user input]{m}[/user input]",
                },
            ]

            # Call Azure OpenAI directly
            response = await asyncio.to_thread(
                openai_client.chat.completions.create,
                model=deployment_name,
                messages=messages,
                temperature=temperature,
                max_tokens=15,  # Allow for longer phrases
            )

            TEXT = response.choices[0].message.content.strip()
            # Estimate chat cost (slightly variable for realism)
            chat_cost = 0.0001 + (np.random.random() * 0.00005)

        except (
            AttributeError,
            RuntimeError,
            ConnectionError,
            ValueError,
            TypeError,
            KeyError,
        ) as e:
            # Type 2: Recoverable - LLM call failed, report error and continue
            error_msg = f"LLM chat call failed: {e}"
            logger.warning(f"⚠️ {error_msg}")
            await shared_state.report_error(error_msg)
            # Still track cost for failed attempt
            chat_cost = 0.0001
            await shared_state.increment_cost(chat_cost)
            return

        TEXT = TEXT.replace('"', "").replace("'", "")

        if TEXT.lower() in previous_guesses:
            # Duplicate guess - still track chat cost
            await shared_state.increment_cost(chat_cost)
            return

        # Embed using EmbeddingService (mem0)
        try:
            # Call EmbeddingService for embeddings
            vectors = await embedding_service.embed_chunks([TEXT], model=embedding_model)

            # Extract the vector (embed_chunks returns List[List[float]], we want the first one)
            if vectors and len(vectors) > 0:
                v_text = np.array(vectors[0])
            else:
                await shared_state.report_error("Empty embedding result")
                # Track costs so far
                embed_cost = 0.0001
                await shared_state.increment_cost(chat_cost + embed_cost)
                return

            # Estimate embedding cost
            embed_cost = 0.0001 + (np.random.random() * 0.00005)

        except (
            AttributeError,
            RuntimeError,
            ConnectionError,
            ValueError,
            TypeError,
            KeyError,
        ) as e:
            # Type 2: Recoverable - embedding failed, report error and continue
            error_msg = f"Embedding failed: {e}"
            logger.warning(f"⚠️ {error_msg}")
            await shared_state.report_error(error_msg)
            # Track costs so far
            embed_cost = 0.0001
            await shared_state.increment_cost(chat_cost + embed_cost)
            return

        # Dimension check
        if v_text.shape != v_target.shape:
            await shared_state.report_error(
                f"Dim mismatch: Target {v_target.shape}, Guess {v_text.shape}"
            )
            # Track costs so far
            await shared_state.increment_cost(chat_cost + embed_cost)
            return

        # Calculate Error
        dv = v_target - v_text
        VECTOR_ERROR = np.sqrt((dv * dv).sum())

        # Total cost for this attempt
        total_cost = chat_cost + embed_cost

        # Log interesting progress - only for really good guesses
        if VECTOR_ERROR < 0.45:
            logger.info(f"🔥 Hot guess! '{TEXT}' with error {VECTOR_ERROR:.4f}")
        elif VECTOR_ERROR < 1.0:
            logger.debug(f"📊 Guess: '{TEXT}' with error {VECTOR_ERROR:.4f}")

        # Update best guess (target_text is stored in shared_state for text matching)
        # Cost is tracked here for successful attempts
        await shared_state.update_best_guess(TEXT, VECTOR_ERROR, total_cost)

    except (AttributeError, RuntimeError, ConnectionError, ValueError, TypeError, KeyError) as e:
        # Type 2: Recoverable - worker error, report and continue
        error_msg = f"Worker Error: {e}"
        logger.error(f"❌ {error_msg}", exc_info=True)
        await shared_state.report_error(error_msg)


class VectorHackingService:
    def __init__(
        self,
        mongo_uri: str,
        db_name: str,
        write_scope: str,
        read_scopes: List[str],
        openai_client=None,
        embedding_service=None,
        deployment_name: str = "gpt-4o",
        embedding_model: str = "text-embedding-3-small",
        temperature: float = 0.8,
    ):
        """
        Vector Hacking Service - runs vector inversion attacks using Azure OpenAI and EmbeddingService.

        This service uses:
        1. Azure OpenAI client for chat completions
        2. EmbeddingService (using mem0) for embeddings
        3. Configuration from manifest.json and environment variables

        Args:
            mongo_uri: MongoDB connection URI
            db_name: Database name
            write_scope: App slug for write operations
            read_scopes: List of app slugs for read operations
            openai_client: AzureOpenAI client instance
            embedding_service: EmbeddingService instance (using mem0)
            deployment_name: Chat model deployment name
            embedding_model: Embedding model name
            temperature: Temperature for chat completions
        """
        self.mongo_uri = mongo_uri
        self.db_name = db_name
        self.write_scope = write_scope
        self.running = False
        self.task = None
        self.shared_state = None
        self.v_target = None
        self.current_target = TARGET  # Track current target phrase
        self.openai_client = openai_client
        self.embedding_service = embedding_service
        self.deployment_name = deployment_name
        self.embedding_model = embedding_model
        self.temperature = temperature
        self.last_best_error = np.inf  # Track last best error to detect improvements

        # Load templates
        try:
            from fastapi.templating import Jinja2Templates

            if templates_dir.is_dir():
                self.templates = Jinja2Templates(directory=str(templates_dir))
            else:
                self.templates = None
        except ImportError:
            self.templates = None

        # Initialize target embedding using EmbeddingService
        # Note: __init__ is sync, so we'll defer embedding to start_hacking
        # This is fine since we need to embed the target anyway when starting
        self.v_target = None
        logger.info(
            f"[{write_scope}] OpenAI client and EmbeddingService configured. Target embedding will be done on start."
        )

    async def render_index(self):
        if self.templates:
            # Create a minimal request object for template rendering
            fake_request = type("Request", (), {"url": type("URL", (), {"path": "/"})()})()
            return self.templates.TemplateResponse(fake_request, "index.html").body.decode("utf-8")
        return "<h1>Vector Hacking Demo</h1><p>Templates not loaded.</p>"

    async def generate_random_target(self) -> str:
        """
        Generate a random target phrase using Azure OpenAI client.

        Uses the Azure OpenAI client to generate a random meaningful phrase that will
        be used as the target for vector inversion attack.

        Returns:
            str: A random target phrase
        """
        if not self.openai_client:
            logger.error("OpenAI client not provided")
            return TARGET  # Fallback to default target only

        try:
            prompt = """Generate a short, meaningful phrase (3-7 words) that would be good for a vector inversion challenge.
Examples:
- "Be mindful of your thoughts"
- "Live in the present moment"
- "Kindness costs nothing"
- "Dream big, work hard"

Generate ONE random phrase. Output ONLY the phrase, nothing else."""

            # Use Azure OpenAI client to generate random target
            response = await asyncio.to_thread(
                self.openai_client.chat.completions.create,
                model=self.deployment_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=1.2,  # Higher temperature for more randomness
            )

            random_phrase = response.choices[0].message.content.strip().strip('"').strip("'")
            if random_phrase:
                logger.info(f"Generated random target: '{random_phrase}'")
                return random_phrase
            else:
                return TARGET  # Fallback
        except (
            AttributeError,
            RuntimeError,
            ConnectionError,
            ValueError,
            TypeError,
            KeyError,
        ) as e:
            # Type 2: Recoverable - target generation failed, return fallback
            logger.error(f"Failed to generate random target: {e}")
            return TARGET  # Fallback to default

    async def start_attack(
        self, custom_target: Optional[str] = None, generate_random: bool = False
    ):
        """
        Start vector hacking attack - game-like experience.

        Args:
            custom_target: Optional custom target phrase (if provided, used instead of default)
            generate_random: If True, generate a random target using LLM

        Returns:
            dict with status and target info
        """
        if self.running:
            return {
                "status": "already_running",
                "message": "Attack already in progress! Stop current attack first.",
                "current_target": self.current_target,
            }

        # If we have a match from previous attack, reset first
        if self.shared_state:
            state = await self.shared_state.get_state()
            if state.get("MATCH_FOUND"):
                logger.info("🔄 Previous attack succeeded. Clearing state for new attack...")
                # Just clear state, don't change target unless explicitly requested
                self.shared_state = None
                self.v_target = None
                self.last_best_error = np.inf

        # Determine target to use
        if generate_random:
            target_to_use = await self.generate_random_target()
            logger.info(f"🎲 Using AI-generated random target: '{target_to_use}'")
        elif custom_target and custom_target.strip():
            target_to_use = custom_target.strip()
            logger.info(f"🎯 Using custom target: '{target_to_use}'")
        else:
            # Use current target if set, otherwise default
            target_to_use = self.current_target if self.current_target else TARGET
            logger.info(f"🎯 Using target: '{target_to_use}'")

        self.current_target = target_to_use

        # Re-embed the target using LLM service abstraction
        # Model configured via EmbeddingService (from manifest.json embedding_config or environment)
        try:
            # Use EmbeddingService (from mem0)
            if not self.embedding_service:
                raise RuntimeError(
                    "EmbeddingService not provided - must be initialized from engine.get_embedding_service()"
                )

            # Embedding model is configured in constructor
            vectors = await self.embedding_service.embed_chunks(
                [target_to_use], model=self.embedding_model
            )

            if vectors and len(vectors) > 0:
                self.v_target = np.array(vectors[0])
                logger.info(
                    f"Target vector initialized: '{target_to_use}' (dim: {len(self.v_target)})"
                )
            else:
                return {"status": "error", "error": "Failed to embed target - empty result"}

        except (
            AttributeError,
            RuntimeError,
            ConnectionError,
            ValueError,
            TypeError,
            KeyError,
        ) as e:
            # Type 2: Recoverable - embedding failed, return error status
            logger.error(f"Failed to embed target: {e}")
            return {"status": "error", "error": f"Failed to embed target: {e}"}

        if self.v_target is None:
            return {
                "status": "error",
                "error": "Target vector not initialized. Check LLM service configuration and logs.",
            }

        # Always create a fresh SharedState for each new attack
        # Pass target text for text similarity matching
        self.shared_state = SharedState()
        self.shared_state.target_text = target_to_use  # Store target for text matching
        self.last_best_error = np.inf  # Reset tracking

        self.running = True
        self.task = asyncio.create_task(self._run_loop())
        logger.info(
            f"🎯 Attack started! Target: '{target_to_use}', vector dim: {len(self.v_target)}"
        )

        # Game-like response with excitement
        return {
            "status": "started",
            "target": target_to_use,
            "message": f"🚀 Attack initiated! Target locked: '{target_to_use}'",
            "ready": False,
        }

    async def stop_attack(self):
        """Stop the attack - game-like experience"""
        was_running = self.running
        self.running = False

        if self.task:
            # Type 4: Let errors bubble up
            self.task.cancel()
            # Wait for task to actually cancel
            try:
                await asyncio.wait_for(self.task, timeout=1.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass

        # Reset shared state to ensure fresh start on next attack
        self.shared_state = None
        self.last_best_error = np.inf

        return {
            "status": "stopped",
            "message": (
                "⏸️ Attack stopped. Ready for new challenge!" if was_running else "Already stopped"
            ),
            "was_running": was_running,
        }

    async def _run_loop(self):
        if not self.shared_state or self.v_target is None:
            logger.warning("⚠️ Attack loop cannot start: shared_state or v_target is None")
            return

        logger.info("🚀 Attack loop started!")
        try:
            iteration = 0
            while self.running:
                # Check for match/cost limit BEFORE starting new batch
                # This ensures we don't start new work if match already found
                state = await self.shared_state.get_state()
                if state["MATCH_FOUND"]:
                    logger.info("🎉 MATCH FOUND! Vector inversion successful!")
                    # Stop immediately when match found
                    self.running = False

                    # Game-like: Show victory, then auto-reset after brief celebration
                    await asyncio.sleep(3)  # Celebration pause
                    logger.info("🔄 Preparing for next challenge...")

                    # Clear state but keep target (user can start new attack)
                    self.shared_state = None
                    self.v_target = None
                    self.last_best_error = np.inf
                    logger.info("Ready for next attack!")
                    break

                if state["TOTAL_COST"] >= COST_LIMIT:
                    logger.info(f"💰 Cost limit reached (${state['TOTAL_COST']:.2f})")
                    self.running = False
                    break

                # Fixed parallelism - user controls difficulty via TARGET and MATCH_ERROR
                NUM_PARALLEL_GUESSES = 3

                # Use asyncio.gather for parallel execution
                # All guesses use Azure OpenAI client and EmbeddingService
                if not self.openai_client or not self.embedding_service:
                    raise RuntimeError("OpenAI client and EmbeddingService must be provided")

                tasks = [
                    generate_and_evaluate_guess(
                        self.v_target,
                        self.shared_state,
                        self.openai_client,
                        self.embedding_service,
                        self.deployment_name,
                        self.embedding_model,
                        self.temperature,
                        self.current_target,  # Pass target hint
                    )
                    for _ in range(NUM_PARALLEL_GUESSES)
                ]

                # Execute all guesses in parallel using LLM service abstraction
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Log any exceptions that occurred
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        logger.warning(f"⚠️ Guess task {i+1} failed: {result}")

                # Check again AFTER batch completes (in case match was found during batch)
                state = await self.shared_state.get_state()
                if state["MATCH_FOUND"]:
                    logger.info("🎉 MATCH FOUND! Vector inversion successful!")
                    self.running = False
                    await asyncio.sleep(3)  # Celebration pause
                    logger.info("🔄 Preparing for next challenge...")
                    self.shared_state = None
                    self.v_target = None
                    self.last_best_error = np.inf
                    logger.info("Ready for next attack!")
                    break

                iteration += 1

                # Track best error for logging (no pausing)
                current_error = state["CURRENT_BEST_ERROR"]
                if current_error != np.inf and current_error < self.last_best_error:
                    # New optimization found - log it but keep running
                    self.last_best_error = current_error
                    logger.info(
                        f"New best guess found (error: {current_error:.4f}) - continuing attack..."
                    )

                # Log progress every 20 iterations (or every 5 for first 20 iterations)
                log_interval = 5 if iteration < 20 else 20
                if iteration % log_interval == 0:
                    if state["CURRENT_BEST_ERROR"] != np.inf:
                        logger.info(
                            f"📈 Progress (iter {iteration}): {state['GUESSES_MADE']} guesses, best error: {state['CURRENT_BEST_ERROR']:.4f}, cost: ${state['TOTAL_COST']:.4f}, best: '{state['CURRENT_BEST_TEXT']}'"
                        )
                    else:
                        logger.info(
                            f"📈 Progress (iter {iteration}): {state['GUESSES_MADE']} guesses made, no valid guesses yet, cost: ${state['TOTAL_COST']:.4f}"
                        )
                    if state.get("LAST_ERROR"):
                        logger.warning(f"⚠️ Last error: {state['LAST_ERROR']}")

                # Small delay between batches
                await asyncio.sleep(0.3)

        except (
            AttributeError,
            RuntimeError,
            ConnectionError,
            ValueError,
            TypeError,
            KeyError,
            asyncio.CancelledError,
        ) as e:
            # Type 2: Recoverable - attack loop error, stop gracefully
            logger.error(f"❌ Error in attack loop: {e}", exc_info=True)
            self.running = False

    async def get_status(self):
        """
        Get current attack status - game-like experience.

        Returns comprehensive status with game-like messaging.
        """
        if not self.shared_state:
            return {
                "status": "ready",
                "running": False,
                "paused": False,
                "TARGET": self.current_target,
                "MODEL_USED": self.deployment_name,
                "READY_FOR_NEXT": True,
                "message": "Ready to start attack! 🎯",
            }

        state = await self.shared_state.get_state()

        # Check if match was found
        match_found = state["MATCH_FOUND"]
        if match_found and self.running:
            # Match found - will be reset in _run_loop, but update status
            self.running = False

        # Calculate progress percentage (inverse of error, normalized)
        current_error = state["CURRENT_BEST_ERROR"]
        if current_error != np.inf and current_error > 0:
            # Convert error to "proximity" percentage (lower error = higher proximity)
            # Error of 0.5 = ~50% proximity, error of 0.0 = 100% proximity
            max_error = 2.0  # Assume max reasonable error
            proximity = max(0, min(100, (1 - (current_error / max_error)) * 100))
        else:
            proximity = 0

        # Game-like status messages
        if match_found:
            status_msg = "🎉 VICTORY! Target compromised!"
            status = "victory"
        elif self.running:
            status_msg = "⚡ Attack in progress..."
            status = "running"
        else:
            status_msg = "⏸️ Attack stopped"
            status = "stopped"

        response = {
            "status": status,
            "running": self.running,
            "paused": False,  # No more pausing
            "CURRENT_BEST_TEXT": state["CURRENT_BEST_TEXT"],
            "CURRENT_BEST_ERROR": float(current_error) if current_error != np.inf else None,
            "PROXIMITY_PERCENT": round(proximity, 2),  # Game-like progress metric
            "GUESSES_MADE": state["GUESSES_MADE"],
            "TOTAL_COST": round(state["TOTAL_COST"], 4),
            "MATCH_FOUND": match_found,
            "LAST_ERROR": state["LAST_ERROR"],
            "MODEL_USED": self.deployment_name,
            "TARGET": self.current_target,
            "READY_FOR_NEXT": not self.running and (match_found or not self.shared_state),
            "message": status_msg,
        }
        return response

    async def reset_attack(self, new_target: Optional[str] = None, generate_random: bool = False):
        """
        Reset attack state for a new attack - clean slate for game experience.

        Args:
            new_target: Optional new target phrase (if provided, uses this)
            generate_random: If True, generate random target using LLM

        Returns:
            dict with reset status and new target info
        """
        # Stop current attack if running
        if self.running:
            await self.stop_attack()

        # Wait a moment for clean shutdown
        await asyncio.sleep(0.2)

        # Clear all state - clean slate
        self.shared_state = None
        self.v_target = None
        self.last_best_error = np.inf
        self.task = None
        self.running = False

        # Determine new target (only if explicitly requested)
        if generate_random:
            new_target = await self.generate_random_target()
            logger.info(f"🎲 Generated random target: '{new_target}'")
            self.current_target = new_target
        elif new_target and new_target.strip():
            new_target = new_target.strip()
            logger.info(f"🎯 Using provided target: '{new_target}'")
            self.current_target = new_target
        # Otherwise keep current target (for page reload scenario)

        return {
            "status": "reset",
            "message": "Attack state reset. Ready for new challenge!",
            "target": self.current_target,
            "ready": True,
        }
