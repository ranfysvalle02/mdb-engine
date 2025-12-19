"""
Vector Hacking Service

This module demonstrates vector inversion attacks using the LLM service abstraction.
All LLM interactions (chat completions and embeddings) go through the unified
LLM service interface configured via manifest.json.

Configuration (manifest.json):
    {
      "llm_config": {
        "enabled": true,
        "default_chat_model": "gpt-3.5-turbo",
        "default_embedding_model": "voyage/voyage-2",
        "default_temperature": 0.8,
        "max_retries": 4
      }
    }

Usage:
    The LLM service is automatically initialized by RuntimeEngine from manifest.json
    and passed to VectorHackingService. All LLM calls use the abstraction:
    - llm_service.chat() for text generation
    - llm_service.embed() for vector embeddings
"""
import logging
import time
import asyncio
import numpy as np
import pathlib
import os
from typing import Dict, Any, List, Optional

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
# All LLM models and settings are configured via manifest.json llm_config section.
# The LLM service abstraction handles provider routing (OpenAI, Anthropic, VoyageAI, etc.)
# via LiteLLM, so you can switch models by changing the manifest.json config. 

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
        self.LAST_ERROR = None # Store last error for debugging
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
                logger.info(f"✅ Match found via vector distance: {error:.4f} <= {MATCH_ERROR}")
            
            # Method 2: Text similarity (case-insensitive, handles variations)
            # Use stored target_text from SharedState
            if self.target_text and not is_match:
                guess_normalized = text.lower().strip()
                target_normalized = self.target_text.lower().strip()
                
                # Exact match (case-insensitive) - handles "Hello World" vs "HELLO WORLD"
                if guess_normalized == target_normalized:
                    is_match = True
                    logger.info(f"✅ Match found via exact text match: '{text}' == '{self.target_text}'")
                else:
                    # Calculate similarity for near-matches
                    from difflib import SequenceMatcher
                    similarity = SequenceMatcher(None, guess_normalized, target_normalized).ratio()
                    
                    if similarity >= TEXT_SIMILARITY_THRESHOLD:
                        is_match = True
                        logger.info(f"✅ Match found via text similarity: {similarity:.2%} >= {TEXT_SIMILARITY_THRESHOLD:.0%} ('{text}' vs '{self.target_text}')")
            
            if is_match:
                self.MATCH_FOUND = True

    async def report_error(self, error_msg):
        async with self._lock:
            self.LAST_ERROR = error_msg

    async def get_state(self):
        async with self._lock:
            return {
                'CURRENT_BEST_TEXT': self.CURRENT_BEST_TEXT,
                'CURRENT_BEST_ERROR': self.CURRENT_BEST_ERROR,
                'GUESSES_MADE': self.GUESSES_MADE,
                'TOTAL_COST': self.TOTAL_COST,
                'PREVIOUS_GUESSES': self.PREVIOUS_GUESSES.copy(),  # Return a copy
                'MATCH_FOUND': self.MATCH_FOUND,
                'LAST_ERROR': self.LAST_ERROR,
                'STAGNATION_COUNT': self.STAGNATION_COUNT
            }

async def generate_and_evaluate_guess(v_target, shared_state, llm_service, llm_config, target_hint=None):
    """
    Generate and evaluate a guess using the LLM service abstraction.
    
    This function demonstrates the LLM service abstraction:
    - Uses llm_service.chat() for text generation (configured via manifest.json)
    - Uses llm_service.embed() for vector embeddings (configured via manifest.json)
    - All models and settings come from manifest.json llm_config section
    
    Args:
        v_target: Target vector (numpy array)
        shared_state: SharedState instance for shared state
        llm_service: LLMService instance (from RuntimeEngine, configured via manifest.json)
        llm_config: LLM configuration dict (from manifest.json llm_config section)
        target_hint: Optional hint about the target phrase
    """
    # LLM service is already initialized and passed in

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
        if state['MATCH_FOUND']:
            return

        # Register attempt immediately
        await shared_state.increment_attempts()

        # Format error safely (handle np.inf)
        error_val = state['CURRENT_BEST_ERROR']
        if error_val == np.inf or error_val is None:
            error_str = "∞"
        else:
            error_str = f"{error_val:.4f}"

        assist = f"""\nBEST_GUESS: "{state['CURRENT_BEST_TEXT']}" (ERROR {error_str})"""
        previous_guesses = state['PREVIOUS_GUESSES']
        
        # Safeguard for prompt size - show more recent guesses
        prev_guesses_list = list(previous_guesses)
        if len(prev_guesses_list) > 30:
             prev_guesses_list = prev_guesses_list[-30:]  # Show last 30

        if prev_guesses_list:
            previous_guesses_str = ', '.join(f'"{guess}"' for guess in prev_guesses_list)
            assist += f"\nPrevious guesses (avoid these): {previous_guesses_str}"
        else:
            assist += "\nNo previous guesses yet."

        m = f"ERROR {error_str}, \"{state['CURRENT_BEST_TEXT']}\""

        # Track cost for this attempt (chat + embedding)
        chat_cost = 0.0
        embed_cost = 0.0
        
        # Use LLM service abstraction for chat completion
        # Model and settings come from manifest.json llm_config section
        try:
            # Create messages for the LLM
            messages = [
                {"role": "system", "content": prompt_template},
                {"role": "user", "content": f"[context]{assist}[/context] \n\n [user input]{m}[/user input]"}
            ]
            
            # Call LLM service abstraction - model configured via manifest.json
            # llm_service.chat() handles provider routing (OpenAI, Anthropic, etc.) via LiteLLM
            TEXT = await llm_service.chat(
                messages,
                model=llm_config.get("default_chat_model", "gpt-3.5-turbo"),  # From manifest.json
                temperature=llm_config.get("default_temperature", 0.8),  # From manifest.json
                max_tokens=15  # Allow for longer phrases
            )
            
            TEXT = TEXT.strip()
            # Estimate chat cost (slightly variable for realism)
            chat_cost = 0.0001 + (np.random.random() * 0.00005)
            
        except Exception as e:
            await shared_state.report_error(f"LLM chat call failed: {e}")
            # Still track cost for failed attempt
            chat_cost = 0.0001
            await shared_state.increment_cost(chat_cost)
            return
        
        TEXT = TEXT.replace('"', '').replace("'", "")
        
        if TEXT.lower() in previous_guesses:
            # Duplicate guess - still track chat cost
            await shared_state.increment_cost(chat_cost)
            return

        # Embed using LLM service abstraction
        # Embedding model configured via manifest.json llm_config.default_embedding_model
        try:
            # Get embedding model from manifest.json config
            embedding_model = llm_config.get("default_embedding_model", "voyage/voyage-2")
            
            # Call LLM service abstraction for embeddings
            # llm_service.embed() handles provider routing (VoyageAI, OpenAI, Cohere, etc.) via LiteLLM
            vectors = await llm_service.embed(TEXT, model=embedding_model)
            
            # Extract the vector (embed returns List[List[float]], we want the first one)
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
                
        except Exception as e:
            await shared_state.report_error(f"Embedding failed: {e}")
            # Track costs so far
            embed_cost = 0.0001
            await shared_state.increment_cost(chat_cost + embed_cost)
            return
        
        # Dimension check
        if v_text.shape != v_target.shape:
             await shared_state.report_error(f"Dim mismatch: Target {v_target.shape}, Guess {v_text.shape}")
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
        
        # Update best guess (target_text is stored in shared_state for text matching)
        # Cost is tracked here for successful attempts
        await shared_state.update_best_guess(TEXT, VECTOR_ERROR, total_cost)

    except Exception as e:
        await shared_state.report_error(f"Worker Error: {e}")

class VectorHackingService:
    def __init__(self, mongo_uri: str, db_name: str, write_scope: str, read_scopes: List[str], llm_service=None, llm_config: Optional[Dict[str, Any]] = None):
        """
        Vector Hacking Service - runs vector inversion attacks using LLM service abstraction.
        
        This service demonstrates the LLM service abstraction pattern:
        1. LLM configuration comes from manifest.json llm_config section
        2. RuntimeEngine initializes LLMService from manifest.json
        3. All LLM calls use the unified abstraction (llm_service.chat(), llm_service.embed())
        4. Provider-agnostic: switch models/providers by changing manifest.json
        
        Configuration Example (manifest.json):
            {
              "llm_config": {
                "enabled": true,
                "default_chat_model": "gpt-3.5-turbo",
                "default_embedding_model": "voyage/voyage-2",
                "default_temperature": 0.8,
                "max_retries": 4
              }
            }
        
        Args:
            mongo_uri: MongoDB connection URI
            db_name: Database name
            write_scope: App slug for write operations
            read_scopes: List of app slugs for read operations
            llm_service: LLMService instance (initialized by RuntimeEngine from manifest.json)
            llm_config: LLM configuration dict from manifest.json llm_config section
        """
        self.mongo_uri = mongo_uri
        self.db_name = db_name
        self.write_scope = write_scope
        self.running = False
        self.task = None
        self.shared_state = None
        self.v_target = None
        self.current_target = TARGET  # Track current target phrase
        self.llm_service = llm_service  # LLM service instance (if available)
        self.llm_config = llm_config or {}  # LLM config for Ray workers
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

        # Initialize target embedding using LLM service
        # Note: __init__ is sync, so we'll defer embedding to start_hacking
        # This is fine since we need to embed the target anyway when starting
        self.v_target = None
        logger.info(f"[{write_scope}] LLM service configured. Target embedding will be done on start.")

    async def render_index(self):
        if self.templates:
            return self.templates.TemplateResponse(
                "index.html",
                {
                    "request": type('Request', (), {'url': type('URL', (), {'path': '/'})()})()
                }
            ).body.decode('utf-8')
        return "<h1>Vector Hacking Demo</h1><p>Templates not loaded.</p>"

    async def generate_random_target(self) -> str:
        """
        Generate a random target phrase using LLM service abstraction.
        
        Uses the LLM service to generate a random meaningful phrase that will
        be used as the target for vector inversion attack.
        
        Returns:
            str: A random target phrase
        """
        if not self.llm_service:
            from mdb_runtime.llm import LLMService
            if not self.llm_config:
                return TARGET  # Fallback to default
            self.llm_service = LLMService(config=self.llm_config)
        
        try:
            prompt = """Generate a short, meaningful phrase (3-7 words) that would be good for a vector inversion challenge.
Examples:
- "Be mindful of your thoughts"
- "Live in the present moment"
- "Kindness costs nothing"
- "Dream big, work hard"

Generate ONE random phrase. Output ONLY the phrase, nothing else."""
            
            # Use LLM service abstraction to generate random target
            random_phrase = await self.llm_service.chat(
                prompt,
                model=self.llm_config.get("default_chat_model", "gpt-3.5-turbo"),
                temperature=1.2  # Higher temperature for more randomness
            )
            
            # Clean up the response
            random_phrase = random_phrase.strip().strip('"').strip("'")
            if random_phrase:
                logger.info(f"Generated random target: '{random_phrase}'")
                return random_phrase
            else:
                return TARGET  # Fallback
        except Exception as e:
            logger.error(f"Failed to generate random target: {e}")
            return TARGET  # Fallback to default
    
    async def start_attack(self, custom_target: Optional[str] = None, generate_random: bool = False):
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
                "current_target": self.current_target
            }
        
        # If we have a match from previous attack, reset first
        if self.shared_state:
            state = await self.shared_state.get_state()
            if state.get('MATCH_FOUND'):
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
        # Model configured via manifest.json llm_config.default_embedding_model
        try:
            from mdb_runtime.llm import LLMService
            
            # Use provided LLM service (from RuntimeEngine) or create one from manifest config
            service_to_use = self.llm_service
            if service_to_use is None:
                # Fallback: create service from manifest.json config
                service_to_use = LLMService(config=self.llm_config)
            
            # Get embedding model from manifest.json config
            embedding_model = self.llm_config.get("default_embedding_model", "voyage/voyage-2")
            
            # Use LLM service abstraction for embedding
            # llm_service.embed() handles provider routing via LiteLLM
            vectors = await service_to_use.embed([target_to_use], model=embedding_model)
            
            if vectors and len(vectors) > 0:
                self.v_target = np.array(vectors[0])
                logger.info(f"Target vector initialized: '{target_to_use}' (dim: {len(self.v_target)})")
            else:
                return {"status": "error", "error": "Failed to embed target - empty result"}
                
        except Exception as e:
            logger.error(f"Failed to embed target: {e}")
            return {"status": "error", "error": f"Failed to embed target: {e}"}
        
        if self.v_target is None:
            return {"status": "error", "error": "Target vector not initialized. Check LLM service configuration and logs."}

        # Always create a fresh SharedState for each new attack
        # Pass target text for text similarity matching
        self.shared_state = SharedState()
        self.shared_state.target_text = target_to_use  # Store target for text matching
        self.last_best_error = np.inf  # Reset tracking
        
        self.running = True
        self.task = asyncio.create_task(self._run_loop())
        
        # Game-like response with excitement
        return {
            "status": "started",
            "target": target_to_use,
            "message": f"🚀 Attack initiated! Target locked: '{target_to_use}'",
            "ready": False
        }

    async def stop_attack(self):
        """Stop the attack - game-like experience"""
        was_running = self.running
        self.running = False
        
        if self.task:
            try:
                self.task.cancel()
                # Wait for task to actually cancel
                try:
                    await asyncio.wait_for(self.task, timeout=1.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
            except Exception:
                pass
        
        # Reset shared state to ensure fresh start on next attack
        self.shared_state = None
        self.last_best_error = np.inf
        
        return {
            "status": "stopped",
            "message": "⏸️ Attack stopped. Ready for new challenge!" if was_running else "Already stopped",
            "was_running": was_running
        }

    async def _run_loop(self):
        if not self.shared_state or self.v_target is None:
            return

        try:
            iteration = 0
            while self.running:
                # Check for match/cost limit BEFORE starting new batch
                # This ensures we don't start new work if match already found
                state = await self.shared_state.get_state()
                if state['MATCH_FOUND']:
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
                    logger.info("✅ Ready for next attack!")
                    break
                
                if state['TOTAL_COST'] >= COST_LIMIT:
                    logger.info(f"💰 Cost limit reached (${state['TOTAL_COST']:.2f})")
                    self.running = False
                    break
                
                # Fixed parallelism - user controls difficulty via TARGET and MATCH_ERROR
                NUM_PARALLEL_GUESSES = 3
                
                # Use asyncio.gather for parallel execution
                # All guesses use the LLM service abstraction (configured via manifest.json)
                from mdb_runtime.llm import LLMService
                
                # Use LLM service from RuntimeEngine (configured via manifest.json)
                # or create fallback from config
                llm_service_to_use = self.llm_service or LLMService(config=self.llm_config)
                
                tasks = [
                    generate_and_evaluate_guess(
                        self.v_target, 
                        self.shared_state,
                        llm_service_to_use,  # LLM service abstraction
                        self.llm_config,     # Config from manifest.json
                        self.current_target   # Pass target hint
                    ) 
                    for _ in range(NUM_PARALLEL_GUESSES)
                ]
                
                # Execute all guesses in parallel using LLM service abstraction
                await asyncio.gather(*tasks, return_exceptions=True)
                
                # Check again AFTER batch completes (in case match was found during batch)
                state = await self.shared_state.get_state()
                if state['MATCH_FOUND']:
                    logger.info("🎉 MATCH FOUND! Vector inversion successful!")
                    self.running = False
                    await asyncio.sleep(3)  # Celebration pause
                    logger.info("🔄 Preparing for next challenge...")
                    self.shared_state = None
                    self.v_target = None
                    self.last_best_error = np.inf
                    logger.info("✅ Ready for next attack!")
                    break
                
                iteration += 1
                
                # Track best error for logging (no pausing)
                current_error = state['CURRENT_BEST_ERROR']
                if current_error != np.inf and current_error < self.last_best_error:
                    # New optimization found - log it but keep running
                    self.last_best_error = current_error
                    logger.info(f"✨ New best guess found (error: {current_error:.4f}) - continuing attack...")
                
                # Log progress every 20 iterations
                if iteration % 20 == 0 and state['CURRENT_BEST_ERROR'] != np.inf:
                    logger.info(f"📊 Progress: {state['GUESSES_MADE']} guesses, best error: {state['CURRENT_BEST_ERROR']:.4f}, cost: ${state['TOTAL_COST']:.4f}")
                
                # Small delay between batches
                await asyncio.sleep(0.3)
                
        except Exception as e:
            logger.error(f"Error in loop: {e}")
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
                "MODEL_USED": self.llm_config.get("default_chat_model", "gpt-3.5-turbo"),
                "READY_FOR_NEXT": True,
                "message": "Ready to start attack! 🎯"
            }
        
        state = await self.shared_state.get_state()
        
        # Check if match was found
        match_found = state['MATCH_FOUND']
        if match_found and self.running:
            # Match found - will be reset in _run_loop, but update status
            self.running = False
        
        # Calculate progress percentage (inverse of error, normalized)
        current_error = state['CURRENT_BEST_ERROR']
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
            "CURRENT_BEST_TEXT": state['CURRENT_BEST_TEXT'],
            "CURRENT_BEST_ERROR": float(current_error) if current_error != np.inf else None,
            "PROXIMITY_PERCENT": round(proximity, 2),  # Game-like progress metric
            "GUESSES_MADE": state['GUESSES_MADE'],
            "TOTAL_COST": round(state['TOTAL_COST'], 4),
            "MATCH_FOUND": match_found,
            "LAST_ERROR": state['LAST_ERROR'],
            "MODEL_USED": self.llm_config.get("default_chat_model", "gpt-3.5-turbo"),
            "TARGET": self.current_target,
            "READY_FOR_NEXT": not self.running and (match_found or not self.shared_state),
            "message": status_msg
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
            "ready": True
        }
