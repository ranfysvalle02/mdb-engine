# Customize the Cognitive Engine

> **TL;DR:** The Cognitive Engine ships with sensible defaults (Perfect Recall scoring, no decay, LLM-based importance). You can swap any of the 6 core "mechanics" -- scoring, decay, extraction, importance, persona blending, reflection -- via code injection, custom classes, or manifest config. No inheritance required. No core code changes. Just implement the right methods and plug it in.

## Table of Contents

- [Why Customize?](#why-customize)
- [The 6 Strategy Slots](#the-6-strategy-slots)
- [Three Ways to Customize](#three-ways-to-customize)
- [Strategy 1: Scoring -- How Memories Are Ranked](#strategy-1-scoring----how-memories-are-ranked)
- [Strategy 2: Decay -- Memory Lifecycle](#strategy-2-decay----memory-lifecycle)
- [Strategy 3: Importance -- How Significance Is Assessed](#strategy-3-importance----how-significance-is-assessed)
- [Strategy 4: Extraction -- How Facts Are Pulled from Text](#strategy-4-extraction----how-facts-are-pulled-from-text)
- [Strategy 5: Persona -- How Identity Shapes Retrieval](#strategy-5-persona----how-identity-shapes-retrieval)
- [Strategy 6: Reflection -- When Consolidation Fires](#strategy-6-reflection----when-consolidation-fires)
- [Composing Strategies Together](#composing-strategies-together)
- [Real-World Recipes](#real-world-recipes)
- [Context Objects Reference](#context-objects-reference)
- [FAQ](#faq)

---

## Why Customize?

The default Cognitive Engine is built for **conversational AI assistants** -- it remembers everything forever (Perfect Recall), ranks memories by importance and access frequency, and uses LLM calls to assess what matters.

But your use case might be different:

- **Customer support bot**: Recent tickets should rank higher than year-old conversations. You want **recency decay**.
- **Healthcare AI**: Allergies and medications should always score 1.0, no LLM needed. You want **rule-based importance**.
- **Cost-sensitive deployment**: You can't afford an LLM call for every memory. You want **heuristic extraction**.
- **Therapeutic AI**: Personality should heavily influence what's recalled. You want **strong persona weighting**.
- **Research assistant**: Old references should fade unless re-cited. You want **exponential decay**.
- **Game NPC**: Memories should decay rapidly to simulate forgetfulness. You want **aggressive linear decay**.

The Strategy API lets you change any of these behaviors without forking the engine.

---

## The 6 Strategy Slots

Every `CognitiveMemoryService` instance has 6 pluggable strategy slots:

| Slot | Controls | Default | When It Runs |
|------|----------|---------|--------------|
| **ScoringStrategy** | How memories are ranked during search | `PerfectRecallScoring` | Every `search()` call, per result |
| **DecayStrategy** | Whether/how memories lose salience over time | `NoDecay` | Available for periodic maintenance |
| **ImportanceStrategy** | How new memories get their importance score | `LLMImportance` | Every `add()` call, per extracted fact |
| **ExtractionStrategy** | How raw text becomes atomic facts | *(built-in LLM extraction)* | Every `add()` call |
| **PersonaStrategy** | How persona influences search queries | `WeightedPersonaBlend` (80/20) | Every `search()` when persona is enabled |
| **ReflectionStrategy** | When memory consolidation triggers | `TimeCountReflection` | When reflection service checks triggers |

If you don't set a strategy, you get exactly the existing behavior. Zero breaking changes.

---

## Three Ways to Customize

### 1. Code Injection (Full Control)

Pass strategy instances directly to the factory. Best for custom logic that can't be expressed as config.

```python
from mdb_engine.memory import get_memory_service
from mdb_engine.memory.strategies import RecencyDecayScoring, ExponentialDecay

memory = get_memory_service(
    app_slug="my_app",
    collection=collection,
    config=config,
    scoring_strategy=RecencyDecayScoring(half_life_hours=72),
    decay_strategy=ExponentialDecay(half_life_hours=168),
)
```

### 2. Custom Classes (Your Own Logic)

Write a class with the right methods. No base class, no inheritance, no registration -- just implement the protocol.

```python
class AggressiveDecay:
    """Memories fully decay after 48 hours."""

    async def apply_decay(self, memory, now):
        if memory.created_at is None:
            return memory.importance
        age_hours = (now - memory.created_at).total_seconds() / 3600
        return max(0, memory.importance * (1 - age_hours / 48))

    def should_archive(self, memory):
        return memory.importance < 0.01

memory = get_memory_service(
    app_slug="my_app",
    collection=collection,
    decay_strategy=AggressiveDecay(),
)
```

### 3. Manifest Config (No Code)

Select built-in strategies from your `manifest.json`. Best for deployment-time tuning without code changes.

```json
{
  "slug": "my_app",
  "memory_config": {
    "enabled": true,
    "scoring": {
      "strategy": "recency_decay",
      "half_life_hours": 72
    },
    "decay": {
      "strategy": "exponential",
      "half_life_hours": 168
    },
    "importance": {
      "strategy": "rule_based"
    }
  }
}
```

Available manifest strategy keys:

| Config Key | Available Strategies | Default |
|------------|---------------------|---------|
| `scoring.strategy` | `"perfect_recall"`, `"recency_decay"` | `"perfect_recall"` |
| `decay.strategy` | `"none"`, `"exponential"`, `"linear"` | `"none"` |
| `importance.strategy` | `"llm"`, `"rule_based"` | `"llm"` |
| `persona_blending.strategy` | `"weighted"`, `"custom_weight"` | `"weighted"` |

Extra keys in the config block are passed as constructor kwargs to the strategy class. For example, `"half_life_hours": 72` becomes `RecencyDecayScoring(half_life_hours=72)`.

---

## Strategy 1: Scoring -- How Memories Are Ranked

The scoring strategy determines the **final relevance score** for each memory returned by `search()`. This is the single most impactful customization point -- it directly controls what your AI "remembers" during conversations.

### The Default: PerfectRecallScoring

```
score = similarity * importance * (1 + ln(access_count + 1)) * emotion_factor
```

- `similarity`: How close the memory is to the search query (0-1, from vector search)
- `importance`: AI-assessed significance (0.1-1.0)
- `access_count`: How many times this memory has been retrieved (rewards rehearsal)
- `emotion_factor`: Boost from emotional intensity and type (novelty, stakes, resonance)

This formula means old memories that are important and frequently accessed still surface. Nothing ever "fades" -- it's perfect digital recall.

### Built-in Alternative: RecencyDecayScoring

Same formula, but multiplied by an exponential decay factor based on memory age:

```
score = similarity * importance * (1 + ln(access_count + 1)) * emotion_factor * decay
where decay = 0.5 ^ (age_hours / half_life_hours)
```

```python
from mdb_engine.memory.strategies import RecencyDecayScoring

# Memories lose half their ranking weight every 3 days
scoring = RecencyDecayScoring(half_life_hours=72)
```

Or via manifest:

```json
{ "scoring": { "strategy": "recency_decay", "half_life_hours": 72 } }
```

### Build Your Own: ScoringStrategy Protocol

Your class needs one async method:

```python
async def score(self, memory: MemoryDocument, query_context: QueryContext) -> float
```

**Example: Boost-by-Category Scoring**

Biographical facts should always rank higher than temporal ones:

```python
from mdb_engine.memory.strategies import MemoryDocument, QueryContext
import math

CATEGORY_MULTIPLIERS = {
    "biographical": 1.5,
    "relational": 1.3,
    "preferences": 1.2,
    "temporal": 0.8,
    "procedural": 1.0,
}

class CategoryBoostScoring:
    """Score memories with category-based multipliers."""

    async def score(self, memory: MemoryDocument, query_context: QueryContext) -> float:
        base = memory.similarity * memory.importance * (1 + math.log(memory.access_count + 1))
        category_mult = CATEGORY_MULTIPLIERS.get(memory.category or "", 1.0)
        return base * category_mult
```

**Example: Time-of-Day Scoring**

A scheduling assistant that boosts temporal memories during work hours:

```python
class WorkHoursScoring:
    """Boost temporal memories during business hours (9-17)."""

    async def score(self, memory: MemoryDocument, query_context: QueryContext) -> float:
        import math
        base = memory.similarity * memory.importance * (1 + math.log(memory.access_count + 1))

        hour = query_context.now.hour
        is_work_hours = 9 <= hour <= 17

        if memory.category == "temporal" and is_work_hours:
            return base * 1.5  # 50% boost for schedule-related memories during work
        return base
```

---

## Strategy 2: Decay -- Memory Lifecycle

The decay strategy controls whether memories lose salience over time and whether they should be archived to cold storage. This runs separately from scoring -- you can have decay without changing your scoring formula, or combine them.

### The Default: NoDecay

Perfect Recall. Nothing ever decays. Nothing is ever archived. Every memory lives forever at its original importance.

### Built-in Alternatives

**ExponentialDecay** -- Memories lose half their importance every N hours:

```python
from mdb_engine.memory.strategies import ExponentialDecay

# Half-life of 1 week. Archive when importance drops below 0.05.
decay = ExponentialDecay(half_life_hours=168, archive_threshold=0.05)
```

**LinearDecay** -- Importance drops linearly to zero over N hours:

```python
from mdb_engine.memory.strategies import LinearDecay

# Memories fully fade after 30 days
decay = LinearDecay(lifetime_hours=720, archive_threshold=0.05)
```

### Build Your Own: DecayStrategy Protocol

Your class needs two methods:

```python
async def apply_decay(self, memory: MemoryDocument, now: datetime) -> float
def should_archive(self, memory: MemoryDocument) -> bool
```

**Example: Access-Based Decay**

Memories that haven't been accessed in a while decay faster:

```python
from datetime import datetime, timezone

class AccessBasedDecay:
    """Memories decay based on time since last access, not creation."""

    def __init__(self, idle_half_life_hours: float = 48):
        self.idle_half_life_hours = idle_half_life_hours

    async def apply_decay(self, memory: MemoryDocument, now: datetime) -> float:
        # Use last access time from metadata, fall back to created_at
        last_access = memory.metadata.get("last_accessed") or memory.created_at
        if last_access is None:
            return memory.importance

        idle_hours = (now - last_access).total_seconds() / 3600
        if idle_hours <= 0:
            return memory.importance

        decay_factor = 0.5 ** (idle_hours / self.idle_half_life_hours)
        return memory.importance * decay_factor

    def should_archive(self, memory: MemoryDocument) -> bool:
        return False  # Never archive, just decay ranking
```

**Example: Tiered Decay**

Different memory categories decay at different rates:

```python
class TieredDecay:
    """Biographical facts are permanent. Temporal facts decay in a week."""

    HALF_LIVES = {
        "biographical": float("inf"),  # Never decays
        "relational": float("inf"),     # Never decays
        "preferences": 720,             # 30 days
        "temporal": 168,                # 1 week
        "procedural": 360,              # 15 days
    }

    async def apply_decay(self, memory: MemoryDocument, now: datetime) -> float:
        half_life = self.HALF_LIVES.get(memory.category or "", 720)
        if half_life == float("inf") or memory.created_at is None:
            return memory.importance

        age_hours = (now - memory.created_at).total_seconds() / 3600
        return memory.importance * (0.5 ** (age_hours / half_life))

    def should_archive(self, memory: MemoryDocument) -> bool:
        return False
```

---

## Strategy 3: Importance -- How Significance Is Assessed

The importance strategy assigns an importance score (0.1-1.0) to each newly extracted fact. This score is stored permanently and used by the scoring strategy at retrieval time.

### The Default: LLMImportance

Sends each fact to the LLM with a prompt asking "rate 1-10 how important this is to remember long-term." Normalizes to 0.1-1.0. Costs one LLM call per extracted fact.

### Built-in Alternative: RuleBasedImportance

Keyword heuristics. Zero LLM calls. Fast and cheap.

```python
from mdb_engine.memory.strategies import RuleBasedImportance

# High-importance keywords: name, allergic, emergency, etc. -> 0.9
# Medium-importance keywords: likes, works, lives, etc. -> 0.7
# Everything else -> default (0.5)
importance = RuleBasedImportance(default_importance=0.5)
```

### Build Your Own: ImportanceStrategy Protocol

Your class needs one async method:

```python
async def assess(self, text: str, context: ImportanceContext) -> float
```

**Example: Domain-Specific Importance**

A medical AI that gives maximum importance to health-related facts:

```python
from mdb_engine.memory.strategies import ImportanceContext

CRITICAL_TERMS = {"allergic", "allergy", "medication", "surgery", "diagnosis",
                  "blood type", "condition", "diabetes", "asthma", "pregnant"}
HEALTH_TERMS = {"doctor", "hospital", "prescription", "symptom", "pain",
                "treatment", "therapy", "vaccine", "insurance"}

class MedicalImportance:
    """Health facts are critical. Everything else gets standard importance."""

    async def assess(self, text: str, context: ImportanceContext) -> float:
        words = set(text.lower().split())

        if words & CRITICAL_TERMS:
            return 1.0  # Maximum -- never lose this
        if words & HEALTH_TERMS:
            return 0.85
        return 0.5  # Standard importance for non-medical facts
```

**Example: Hybrid LLM + Rules**

Use cheap rules first, only call LLM for ambiguous cases:

```python
class HybridImportance:
    """Rules for obvious cases, LLM for everything else."""

    def __init__(self, llm_fn=None, model=None):
        self._llm_fn = llm_fn
        self._model = model

    async def assess(self, text: str, context: ImportanceContext) -> float:
        text_lower = text.lower()

        # Fast path: obvious high importance
        if any(kw in text_lower for kw in ["allergic", "name is", "birthday", "died"]):
            return 0.95

        # Fast path: obvious low importance
        if any(kw in text_lower for kw in ["weather", "hello", "thanks"]):
            return 0.2

        # Ambiguous: ask the LLM (only ~30% of facts reach here)
        if self._llm_fn:
            try:
                resp = await self._llm_fn(
                    messages=[{"role": "user", "content": f"Rate 1-10 importance: {text}"}],
                    model=self._model,
                )
                rating = float("".join(c for c in resp.choices[0].message.content if c.isdigit() or c == "."))
                return min(max(rating / 10.0, 0.1), 1.0)
            except (ValueError, AttributeError, RuntimeError):
                pass
        return 0.5
```

---

## Strategy 4: Extraction -- How Facts Are Pulled from Text

The extraction strategy controls how raw user input becomes atomic facts stored in memory. This is the most complex strategy to customize because the default implementation is very thorough (structured LLM output with categories, emotion tagging, emotion types).

### The Default: Built-in LLM Extraction

The built-in extraction uses a detailed system prompt to produce structured facts with:
- `text`: The atomic fact ("User's sister Emily is a doctor")
- `category`: biographical, preferences, temporal, relational, procedural
- `emotion`: 0.0-1.0 intensity score
- `emotion_type`: novelty, stakes, resonance, neutral

When no custom `ExtractionStrategy` is injected, the full built-in pipeline runs. When one **is** injected, it takes over and the built-in is skipped (unless the custom strategy raises an error, in which case it falls through to the built-in as a safety net).

### Build Your Own: ExtractionStrategy Protocol

Your class needs one async method:

```python
async def extract(self, text: str, context: ExtractionContext) -> list[ExtractedFact]
```

Each `ExtractedFact` has: `text`, `category`, `emotion`, `emotion_type`, `memory_type`.

**Example: Regex-Based Extraction (No LLM)**

For cost-sensitive deployments where you know the input format:

```python
import re
from mdb_engine.memory.strategies import ExtractionContext, ExtractedFact

class RegexExtractor:
    """Extract facts using pattern matching. Zero LLM cost."""

    PATTERNS = [
        (r"(?:my name is|i'm|i am)\s+(\w+)", "biographical", 0.8),
        (r"(?:i (?:love|like|enjoy))\s+(.+?)(?:\.|$)", "preferences", 0.5),
        (r"(?:i (?:hate|dislike|can't stand))\s+(.+?)(?:\.|$)", "preferences", 0.6),
        (r"(?:i work (?:at|for))\s+(.+?)(?:\.|$)", "biographical", 0.7),
        (r"(?:i live in)\s+(.+?)(?:\.|$)", "biographical", 0.6),
        (r"(?:my (?:wife|husband|partner|sister|brother|mother|father))\s+(.+?)(?:\.|$)", "relational", 0.7),
    ]

    async def extract(self, text: str, context: ExtractionContext) -> list[ExtractedFact]:
        facts = []
        text_lower = text.lower()
        for pattern, category, emotion in self.PATTERNS:
            for match in re.finditer(pattern, text_lower):
                facts.append(ExtractedFact(
                    text=f"User {match.group(0).strip()}",
                    category=category,
                    emotion=emotion,
                    emotion_type="neutral",
                ))
        return facts
```

**Example: Multi-Provider Extraction**

Use a cheaper model for extraction, save the expensive model for chat:

```python
class CheapModelExtractor:
    """Use a fast, cheap model (e.g. GPT-4o-mini) for extraction."""

    def __init__(self, llm_service, model: str = "openai/gpt-4o-mini"):
        self.llm = llm_service
        self.model = model

    async def extract(self, text: str, context: ExtractionContext) -> list[ExtractedFact]:
        prompt = (
            "Extract key facts about the user from this text. "
            "Return a JSON array of objects with 'text' and 'category' keys. "
            "Categories: biographical, preferences, temporal, relational.\n\n"
            f"Text: {text}"
        )
        import json
        response = await self.llm.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            provider_name="chat",
            model=self.model,
            response_format={"type": "json_object"},
        )
        data = json.loads(response)
        facts_raw = data.get("facts", data) if isinstance(data, dict) else data
        return [
            ExtractedFact(
                text=f.get("text", ""),
                category=f.get("category", "biographical"),
            )
            for f in (facts_raw if isinstance(facts_raw, list) else [])
            if f.get("text", "").strip()
        ]
```

---

## Strategy 5: Persona -- How Identity Shapes Retrieval

The persona strategy controls how the AI's persona/identity influences which memories are retrieved. When a persona is active, the query embedding is blended with a persona embedding before vector search -- this biases results toward memories that align with the persona.

### The Default: WeightedPersonaBlend

80% query vector, 20% persona vector, re-normalized. This gives a subtle persona influence without overwhelming the actual search query.

### Built-in Alternative: CustomWeightPersonaBlend

Same algorithm but reads weights from config at call time:

```python
from mdb_engine.memory.strategies import CustomWeightPersonaBlend

# 70% query, 30% persona -- stronger persona influence
persona = CustomWeightPersonaBlend(default_query_weight=0.7, default_persona_weight=0.3)
```

### Build Your Own: PersonaStrategy Protocol

Your class needs one async method:

```python
async def blend(self, query_vector: list[float], persona_vector: list[float], config: dict) -> list[float]
```

**Example: Persona-Free Search**

Completely disable persona influence for factual retrieval:

```python
class NoPersonaBlend:
    """Ignore persona entirely. Pure query-based retrieval."""

    async def blend(self, query_vector, persona_vector, config):
        return query_vector
```

**Example: Context-Dependent Persona**

Switch persona strength based on the type of query:

```python
import math

class AdaptivePersonaBlend:
    """Strong persona for emotional queries, weak for factual ones."""

    async def blend(self, query_vector, persona_vector, config):
        # Config might include a "mode" key set by the application
        mode = config.get("mode", "balanced")

        if mode == "empathetic":
            qw, pw = 0.5, 0.5  # Heavy persona influence
        elif mode == "factual":
            qw, pw = 0.95, 0.05  # Almost no persona
        else:
            qw, pw = 0.8, 0.2  # Default balance

        blended = [qw * q + pw * p for q, p in zip(query_vector, persona_vector)]
        mag = math.sqrt(sum(x * x for x in blended))
        if mag > 0:
            blended = [x / mag for x in blended]
        return blended
```

---

## Strategy 6: Reflection -- When Consolidation Fires

The reflection strategy controls when the memory system pauses to consolidate atomic memories into narrative summaries ("reflections"). Reflections improve retrieval quality by creating higher-level summaries that capture themes across many individual facts.

### The Default: TimeCountReflection

Two triggers:
- **Time-based**: Fires when N hours have passed since the last reflection (default: 24h)
- **Count-based**: Fires when memory count exceeds a threshold (default: 50)

### Build Your Own: ReflectionStrategy Protocol

Your class needs two async methods:

```python
async def should_trigger(self, user_id: str, stats: ReflectionStats) -> tuple[bool, str]
async def consolidate(self, memories: list[MemoryDocument], context: ReflectionContext) -> ConsolidationResult
```

**Example: Importance-Weighted Trigger**

Only reflect when there's enough high-importance material worth consolidating:

```python
from mdb_engine.memory.strategies import ReflectionStats, ReflectionContext, ConsolidationResult

class ImportanceThresholdReflection:
    """Only trigger reflection when cumulative importance is high enough."""

    def __init__(self, importance_threshold: float = 15.0, min_memories: int = 10):
        self.importance_threshold = importance_threshold
        self.min_memories = min_memories

    async def should_trigger(self, user_id: str, stats: ReflectionStats) -> tuple[bool, str]:
        if stats.recent_memory_count < self.min_memories:
            return False, f"Only {stats.recent_memory_count} recent memories (need {self.min_memories})"
        # In a real implementation you'd query the actual importance values
        # This is simplified for illustration
        return True, f"Enough memories ({stats.recent_memory_count}) for reflection"

    async def consolidate(self, memories, context):
        return ConsolidationResult(
            summary=None,  # Let the default reflection LLM handle summary generation
            memories_processed=len(memories),
        )
```

---

## Composing Strategies Together

Strategies are **independent** -- you can mix and match any combination. But they can also **compose** with each other. A scoring strategy can internally use a decay strategy:

```python
from mdb_engine.memory.strategies import ExponentialDecay, MemoryDocument, QueryContext
import math

class DecayAwareScoringWithFloor:
    """PerfectRecall scoring * exponential decay, with a minimum floor."""

    def __init__(self, half_life_hours: float = 168, floor: float = 0.1):
        self._decay = ExponentialDecay(half_life_hours=half_life_hours)
        self._floor = floor

    async def score(self, memory: MemoryDocument, query_context: QueryContext) -> float:
        # Base scoring (PerfectRecall formula)
        base = memory.similarity * memory.importance * (1 + math.log(memory.access_count + 1))

        # Apply decay
        decayed_importance = await self._decay.apply_decay(memory, query_context.now)
        decay_ratio = decayed_importance / max(memory.importance, 1e-9)

        # Floor prevents old important memories from vanishing completely
        effective_decay = max(decay_ratio, self._floor)

        return base * effective_decay
```

---

## Real-World Recipes

### Recipe 1: Customer Support Bot

Recent tickets matter most. Health data is critical. Save money on importance scoring.

```python
from mdb_engine.memory import get_memory_service
from mdb_engine.memory.strategies import (
    RecencyDecayScoring,
    ExponentialDecay,
    RuleBasedImportance,
)

memory = get_memory_service(
    app_slug="support_bot",
    collection=collection,
    scoring_strategy=RecencyDecayScoring(half_life_hours=48),
    decay_strategy=ExponentialDecay(half_life_hours=336, archive_threshold=0.01),
    importance_strategy=RuleBasedImportance(default_importance=0.6),
)
```

### Recipe 2: Personal Journal AI

Everything is important. Nothing decays. Strong persona identity.

```python
from mdb_engine.memory import get_memory_service
from mdb_engine.memory.strategies import (
    PerfectRecallScoring,
    NoDecay,
    CustomWeightPersonaBlend,
)

memory = get_memory_service(
    app_slug="journal_ai",
    collection=collection,
    scoring_strategy=PerfectRecallScoring(),  # Default, explicit for clarity
    decay_strategy=NoDecay(),
    persona_strategy=CustomWeightPersonaBlend(
        default_query_weight=0.6,
        default_persona_weight=0.4,  # Strong persona
    ),
)
```

### Recipe 3: Cost-Optimized Chatbot

Minimal LLM usage for the memory pipeline. Save tokens for chat responses.

```python
memory = get_memory_service(
    app_slug="cheap_bot",
    collection=collection,
    importance_strategy=RuleBasedImportance(default_importance=0.5),
    extraction_strategy=RegexExtractor(),  # Your custom regex extractor
)
```

### Recipe 4: Manifest-Only Config

No custom code. Just tune parameters from `manifest.json`:

```json
{
  "slug": "tuned_app",
  "memory_config": {
    "enabled": true,
    "scoring": {
      "strategy": "recency_decay",
      "half_life_hours": 96
    },
    "decay": {
      "strategy": "linear",
      "lifetime_hours": 2160,
      "archive_threshold": 0.02
    },
    "importance": {
      "strategy": "rule_based",
      "default_importance": 0.6
    },
    "persona_blending": {
      "strategy": "custom_weight",
      "query_weight": 0.7,
      "persona_weight": 0.3
    }
  }
}
```

---

## Context Objects Reference

Every strategy receives typed context objects so you have the data you need without coupling to service internals.

### MemoryDocument

Passed to scoring and decay strategies. Read-only view of a memory.

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Memory document ID |
| `text` | `str` | The memory content |
| `similarity` | `float` | Vector search similarity (0-1) |
| `importance` | `float` | Stored importance (0.1-1.0) |
| `access_count` | `int` | Number of times this memory has been retrieved |
| `confidence` | `float` | Confidence score (default 0.8) |
| `emotion` | `float` | Emotional intensity (0.0-1.0) |
| `emotion_type` | `str` | `"novelty"`, `"stakes"`, `"resonance"`, or `"neutral"` |
| `category` | `str \| None` | Memory category |
| `created_at` | `datetime \| None` | When the memory was created |
| `metadata` | `dict` | Full metadata dict |

### QueryContext

Passed to scoring strategies.

| Field | Type | Description |
|-------|------|-------------|
| `now` | `datetime` | Current UTC timestamp |
| `user_id` | `str \| None` | The user being queried |
| `scoring_weights` | `dict[str, float]` | Per-user neuroplasticity weight overrides |

### ImportanceContext

Passed to importance strategies.

| Field | Type | Description |
|-------|------|-------------|
| `app_slug` | `str` | Application slug |
| `user_id` | `str \| None` | The user whose memory is being assessed |
| `category` | `str \| None` | Pre-detected category (if available) |

### ExtractionContext

Passed to extraction strategies.

| Field | Type | Description |
|-------|------|-------------|
| `app_slug` | `str` | Application slug |
| `user_id` | `str \| None` | The user whose input is being processed |
| `categories_enabled` | `bool` | Whether category extraction is enabled |
| `custom_categories` | `list[str]` | Any custom categories defined in config |
| `memory_types_enabled` | `bool` | Whether memory type classification is enabled |
| `auto_detect_memory_type` | `bool` | Whether auto memory type detection is on |

### ExtractedFact

Returned by extraction strategies.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `text` | `str` | *(required)* | The extracted fact |
| `category` | `str` | `"biographical"` | Fact category |
| `emotion` | `float` | `0.3` | Emotional intensity |
| `emotion_type` | `str` | `"neutral"` | Emotion classification |
| `memory_type` | `str \| None` | `None` | Optional memory type override |

### ReflectionStats

Passed to reflection trigger checks.

| Field | Type | Description |
|-------|------|-------------|
| `last_reflection_at` | `datetime \| None` | When the last reflection ran |
| `recent_memory_count` | `int` | Memories created since last reflection |
| `total_memory_count` | `int` | Total memories for this user |

---

## FAQ

### Do I have to implement all 6 strategies?

No. Each slot is independent. Set only the ones you want to change. Everything else keeps its default behavior.

### What happens if my custom strategy raises an error?

The engine catches it, logs a warning, and falls back to safe defaults (inline formula for scoring, default 0.5 for importance, built-in LLM for extraction). Your app stays up.

### Can I change strategies at runtime?

The strategies are set at service construction time. To change them, create a new service instance. In practice, most apps set strategies once at startup.

### Do strategies affect `inject()`?

`inject()` bypasses extraction (it's for direct insertion). Importance is still assessed unless you set `importance` in metadata manually. Scoring and decay affect retrieval of injected memories the same as any other memory.

### Can I use strategies with the CognitiveEngine (chat orchestrator)?

Yes. The `CognitiveEngine` uses the `CognitiveMemoryService` (with your strategies) as its LTM backend. Your strategies apply to all LTM operations automatically.

### Where do I import everything from?

```python
# All strategies available from the memory package
from mdb_engine.memory import (
    # Protocols (for type hints)
    ScoringStrategy, DecayStrategy, ExtractionStrategy,
    ImportanceStrategy, PersonaStrategy, ReflectionStrategy,
    # Defaults
    PerfectRecallScoring, NoDecay, LLMImportance,
    WeightedPersonaBlend, TimeCountReflection,
    # Alternatives
    RecencyDecayScoring, ExponentialDecay, LinearDecay,
    RuleBasedImportance, CustomWeightPersonaBlend,
    # Context objects
    MemoryDocument, QueryContext, ImportanceContext,
    ExtractionContext, ExtractedFact, ReflectionStats,
    ReflectionContext, ConsolidationResult,
)

# Or from the strategies module directly
from mdb_engine.memory.strategies import RecencyDecayScoring

# Or from protocols (for typing only)
from mdb_engine.core.protocols import ScoringStrategy, DecayStrategy
```

### Priority order when I set both code and manifest strategies?

1. **Code injection** (constructor arg) -- highest priority
2. **Manifest config** (`memory_config.scoring.strategy`) -- used if no code injection
3. **Built-in default** -- used if neither code nor manifest specifies anything
