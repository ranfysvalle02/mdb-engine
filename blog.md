# Why We Built mdb-engine: MongoDB as Your AI's Long-Term Memory

Every AI application eventually hits the same wall: **memory**.

Your chatbot forgets the user's name between sessions. Your agent can't recall what it learned yesterday. Your RAG pipeline treats every query like the first one. You duct-tape Redis for short-term state, Pinecone for vectors, Neo4j for relationships, and Postgres for everything else — then spend more time managing infrastructure than building features.

We built **mdb-engine** because we believe one database can do all of it — and that database is MongoDB.

---

## The Memory Problem in AI

Most AI frameworks treat memory as an afterthought. You get a chat history array, maybe a vector store adapter, and you're on your own. But real memory — the kind that makes an AI agent actually *useful* over time — is a layered, evolving system. It isn't a single table or a vector index. It's an architecture.

Neuroscience gives us the blueprint. Human memory isn't one thing — it's a hierarchy of systems that work together, each optimized for different kinds of knowledge and different time horizons. We took that seriously.

---

## The OSI Model for AI Memory

If you've built networked systems, you know the OSI model: seven layers, each with a clear responsibility, each depending on the layer below and serving the layer above. That separation of concerns is what makes the internet work.

mdb-engine applies the same philosophy to AI memory. Just as the OSI model separates physical transport from application logic, mdb-engine separates raw storage from cognitive reasoning. Each layer has one job, and the layers compose into something greater than the sum of their parts.

| Layer | OSI Analogy | mdb-engine Layer | Responsibility |
|-------|-------------|------------------|----------------|
| **7** | Application | **Orchestration** | CognitiveEngine — full chat pipeline, context assembly, LLM generation |
| **6** | Presentation | **Context Engineering** | Prompt formatting, persona injection, STM optimization, graph deduplication |
| **5** | Session | **Strategy** | Pluggable scoring, decay, importance, persona blending, reflection triggers |
| **4** | Transport | **Memory Services** | CognitiveMemoryService, GraphService, ChatHistoryService — the APIs your code talks to |
| **3** | Network | **Cognitive Tiers** | The six-tier Perfect Brain: Working, Episodic, Semantic, Procedural, Reflective, Predictive |
| **2** | Data Link | **Scoping & Isolation** | ScopedMongoWrapper — automatic `app_id` filtering, cross-tenant protection, query validation |
| **1** | Physical | **Storage** | MongoDB Atlas — documents, vector indexes, `$graphLookup`, TTL indexes, change streams |

Why does this matter? Because **each layer is independently replaceable and testable**. Swap a scoring strategy at Layer 5 without touching storage. Change your LLM provider at Layer 7 without rewiring memory retrieval. Add a new cognitive tier at Layer 3 without modifying the orchestrator. The layering isn't aesthetic — it's the reason the system stays maintainable as it grows.

The OSI model taught us that good abstractions at each layer let you evolve the system without rewriting it. mdb-engine's memory stack follows the same principle: **clean contracts between layers, so complexity stays local.**

---

## Going Deep on Memory: The Perfect Brain Architecture

Most "memory" solutions give you a vector store and call it a day. That's like building a human brain with only the hippocampus. mdb-engine implements a **six-tier cognitive memory system** modeled on how biological memory actually works — each tier with distinct storage semantics, retrieval patterns, and lifecycle rules.

### Tier 1: Working Memory — The Scratchpad

Working memory is the agent's immediate cognitive workspace. It holds the active context for the current task — variables, intermediate results, user intent signals — with a 24-hour TTL.

- **No embeddings.** This is key-value storage optimized for fast read/write.
- **TTL-indexed.** MongoDB's native TTL indexes handle automatic expiration. No cron jobs, no garbage collection.
- **Session-scoped.** Each conversation gets its own working context that evaporates when the session ends.

Working memory is cheap and disposable by design. It's the whiteboard you erase at the end of the meeting.

### Tier 2: Episodic Memory — What Happened

Episodic memory records *events* — raw interactions with full temporal context. Every conversation turn, every user action, every agent decision gets logged as an episode with timestamps and vector embeddings.

```python
# An episode captures the full interaction context
{
  "content": "User asked about breakfast preferences and mentioned they're lactose intolerant",
  "embedding": [0.023, -0.041, ...],
  "timestamp": "2025-03-15T10:30:00Z",
  "consolidated": false,
  "metadata": {
    "session_id": "sess_abc123",
    "interaction_type": "conversation",
    "emotion": "neutral"
  }
}
```

The `consolidated: false` flag is critical. It marks episodes that haven't yet been processed by the consolidation pipeline. This is analogous to how the brain replays and consolidates experiences during sleep — raw experiences need processing before they become durable knowledge.

**Why it matters:** Episodic memory lets the agent answer "what happened" questions — *"What did the user say last Tuesday?"* — with temporal precision. It's the audit trail of cognition.

### Tier 3: Semantic Memory — What the Agent Knows

This is where mdb-engine diverges most dramatically from typical RAG systems. Semantic memory doesn't store raw text chunks. It stores **extracted, structured, versioned facts** — the distilled knowledge derived from episodic experiences.

```python
{
  "entity": "user_123",
  "attribute": "dietary_restriction",
  "value": "lactose intolerant",
  "category": "preferences",
  "importance": 0.85,
  "confidence": 0.92,
  "version": 3,
  "history": [
    {"value": "no restrictions", "recorded_at": "2025-01-10T..."},
    {"value": "dairy sensitive", "recorded_at": "2025-02-20T..."},
    {"value": "lactose intolerant", "recorded_at": "2025-03-15T..."}
  ],
  "embedding": [0.018, -0.033, ...],
  "access_count": 14,
  "last_accessed": "2025-03-20T08:00:00Z"
}
```

Key properties:

- **Versioned truth.** Every fact carries its revision history. The agent can reason about what it *used to believe* versus what it knows now. Old values aren't deleted — they're preserved in the `history` array.
- **Importance scoring.** Each fact is scored 0.0–1.0, either by an LLM (default) or by rule-based heuristics (no API calls). High-importance facts surface first during retrieval.
- **Categories.** Facts are typed — biographical, preferences, temporal, relational — enabling category-aware retrieval and reasoning.
- **Access tracking.** `access_count` and `last_accessed` timestamps feed into relevance scoring. Frequently accessed facts are likely more important.

**The consolidation pipeline** is what turns episodic memories into semantic facts. It's an LLM-powered background process:

1. Fetch unconsolidated episodes
2. Extract entities and relationships via LLM
3. Store structured facts with version history
4. Extract procedures for procedural memory
5. Generate reflective insights via pattern detection
6. Mark episodes as consolidated

This is the AI equivalent of "sleeping on it" — raw experience gets compressed into durable, queryable knowledge.

### Tier 4: Procedural Memory — What the Agent Can Do

Procedural memory stores **learned skills, workflows, and executable patterns**. When an agent successfully completes a complex task, the steps can be captured as a procedure:

- **Success rate tracking.** Each procedure records how often it leads to successful outcomes.
- **Vector-searchable by capability.** "Find me a procedure for data migration" returns semantically relevant workflows.
- **Skill compilation (myelination).** High-confidence, repeatedly successful procedures get "compiled" — promoted to preferred status, analogous to how repeated practice strengthens neural pathways.

This tier is what makes agents genuinely improve at tasks over time, not by retraining the model, but by building a searchable library of proven approaches.

### Tier 5: Reflective Memory — What the Agent Thinks About What It Knows

Reflective memory is meta-cognition. It stores insights *about* the agent's own knowledge and behavior:

- **Pattern detection.** "I notice the user always asks about performance on Mondays."
- **Error analysis.** "My recommendation was wrong because I weighted recency too heavily."
- **Performance review.** "My accuracy on coding tasks has improved 15% since adopting the structured approach."

Reflections are triggered by configurable strategies — time-based (every 24 hours), count-based (every 50 new memories), or custom triggers. Each reflection carries a confidence score with tracking history, so the agent's self-assessment itself evolves over time.

**Why it matters:** Reflective memory is what separates a tool from an assistant. An agent without reflection makes the same mistakes forever. An agent with reflection identifies its own failure modes and adapts.

### Tier 6: Predictive Memory — What Might Happen

The most experimental tier. Predictive memory enables **counterfactual reasoning and hypothesis tracking**:

- **Scenarios.** "If the user switches to a plant-based diet, their recipe preferences will shift."
- **Counterfactuals.** "What would have happened if I'd recommended the alternative approach?"
- **Hypothesis validation.** Predictions are stored with confidence scores and later validated against reality. The agent learns not just from what happened, but from what it *expected* to happen.
- **Accuracy tracking per origin type** — simulation, counterfactual, hypothesis, pattern — so the agent knows which kinds of predictions it's good at.

Predictive memory is backed by a **Timeline Service** that supports multiverse branching. Branch a timeline, simulate a scenario, compare outcomes. This isn't science fiction — it's `$graphLookup` and document versioning applied to temporal reasoning.

### How the Tiers Work Together: Consolidation

The tiers aren't silos. They're a pipeline:

```
Working Memory (active session)
       ↓ session ends
Episodic Memory (raw events)
       ↓ consolidation pipeline
Semantic Memory (extracted facts) ←── Knowledge Graph (entities + relationships)
       ↓ pattern detection
Reflective Memory (meta-insights)
       ↓ hypothesis generation
Predictive Memory (scenarios + validation)

Procedural Memory (learned skills) ←── compiled from repeated successful episodes
```

The **MemoryConsolidator** orchestrates this flow. It runs the full cycle: episode extraction → entity storage → procedure extraction → reflection generation → skill compilation → neuroplasticity adaptation. Each step enriches the next.

### Neuroplasticity: Memory That Adapts to the User

The consolidation pipeline includes a **neuroplasticity engine** that adjusts scoring weights per user based on interaction patterns. If a user consistently accesses emotion-tagged memories, the system increases the emotion weight in that user's scoring function. If recency matters more for a particular user's workflow, the recency weight adapts upward.

This isn't global tuning. It's **per-user cognitive adaptation** — the memory system literally reshapes itself around each user's behavior.

---

## The Semantic Knowledge Graph: Structure From Chaos

Vector search finds similar things. But similarity isn't understanding. Knowing that "oat milk" and "almond milk" are similar doesn't tell you that the user *switched from* one *to* the other, or that the switch was *because of* a lactose intolerance diagnosis.

mdb-engine builds a **semantic knowledge graph** alongside its memory tiers, turning unstructured conversations into structured, traversable knowledge.

### How It Works

1. **Automatic Entity Extraction.** The `GraphService` uses LLM-powered extraction (`extract_graph_from_text()`) to identify entities and relationships from every interaction. No manual annotation. No schema upfront.

2. **Nodes and Edges in MongoDB.** The knowledge graph lives in a dedicated `__kg` collection. Nodes are entities (people, concepts, preferences). Edges are relationships with weights, temporal flags, and metadata.

3. **Multi-Hop Traversal via `$graphLookup`.** MongoDB's native graph traversal operator lets you walk the knowledge graph without a separate graph database:

```python
{
  "$graphLookup": {
    "from": "__kg",
    "startWith": "$edges.target",
    "connectFromField": "edges.target",
    "connectToField": "_id",
    "as": "network",
    "maxDepth": 2,
    "depthField": "hop_distance",
    "restrictSearchWithMatch": {"app_slug": "myapp"}
  }
}
```

4. **GraphRAG Query Classification.** Not all queries need the same retrieval strategy. mdb-engine classifies queries and routes them:
   - **Local** — Entity-focused. Walk the graph from a known entity. *"What does Sarah prefer for lunch?"*
   - **Global** — Community summaries + map-reduce across the full graph. *"What are the team's dietary requirements?"*
   - **Drift** — Exploratory search across communities. *"What interesting patterns exist in user behavior?"*
   - **Hybrid** — Vector search + graph traversal combined. The best of both worlds for most queries.

### The Value of Structure

A pure vector store gives you relevance. A knowledge graph gives you **reasoning**:

- **Causal chains.** "User switched to oat milk → because of lactose intolerance → diagnosed in February."
- **Contradiction detection.** "Memory A says user prefers dairy. Memory B says user is lactose intolerant." The graph surfaces the conflict; the version history resolves it.
- **Relationship-aware retrieval.** When the user asks about "my team's preferences," the graph knows who's on the team and traverses to each member's preference nodes — no manual joins.
- **Temporal reasoning.** Edges carry temporal flags, so the graph distinguishes "was friends with" from "is friends with."

The knowledge graph isn't a replacement for vector search — it's a **complement**. Vector search finds relevant memories. The knowledge graph explains *why* they're related and *how* they connect. Together, they give the agent something approaching genuine understanding.

---

## Duplicate Detection and Memory Merging: Clean Knowledge

Real conversations produce redundant information. A user mentions their coffee preference five times across ten conversations. Naive systems store five separate memories. mdb-engine handles this with a **similarity-band merging system**:

| Similarity | Action | What Happens |
|-----------|--------|-------------|
| **≥ 0.90** | **Skip** | Near-identical memory already exists. No storage needed. |
| **0.85–0.90** | **Reinforce** | Boost the existing memory's importance by a configurable factor (default 1.1x). Repeated mentions signal importance. |
| **0.70–0.85** | **Merge** | LLM combines old and new into a unified, richer fact. Embeddings averaged. Higher importance preserved. Graph references cleaned up. |
| **< 0.70** | **Create** | Sufficiently novel information. Store as new memory. |

This means the memory store stays **lean and authoritative**. No duplicates cluttering search results. No contradictory versions of the same fact. And the merging process preserves the third-person, factual perspective that makes retrieved context useful in prompts.

---

## Pluggable Strategies — Your Memory, Your Rules

Every aspect of memory behavior is customizable through strategy injection. Each strategy is a **protocol** (structural typing) — no inheritance required. Implement the interface and swap it in.

### Scoring: How Memories Are Ranked

- **PerfectRecallScoring** (default): `similarity × effective_importance × emotion_factor`. Nothing decays. Every memory is forever retrievable, ranked by relevance.
- **RecencyDecayScoring**: Adds exponential recency decay. Recent memories rank higher. Good for fast-moving domains where old context loses value.

### Decay: Whether Memories Fade

- **NoDecay** (default): Perfect Recall — the system never forgets.
- **ExponentialDecay**: Ebbinghaus forgetting curve: `S(t) = R × exp(-t / H)`. Memories fade unless reinforced by access.
- **LinearDecay**: Steady, predictable fade over time.

### Importance: How Memories Are Valued

- **LLMImportance** (default): An LLM rates each memory 1–10, normalized to 0.1–1.0. Captures semantic significance.
- **RuleBasedImportance**: Keyword-based heuristics. No API calls, no latency, no cost. Good for high-throughput systems where LLM scoring per memory is impractical.

### Persona: How User Context Shapes Retrieval

- **WeightedPersonaBlend** (default): 80% query vector / 20% user persona vector. Retrieval is biased toward what *this specific user* cares about.
- **CustomWeightPersonaBlend**: Configurable weights for domain-specific tuning.

### Reflection: When the Agent Self-Examines

- **TimeCountReflection** (default): Triggers reflection after 24 hours or 50 new memories, whichever comes first.
- Custom triggers for domain-specific reflection cadences.

### QueryAwareRecall: Retrieval That Adapts to the Task

Not all queries deserve the same retrieval depth. mdb-engine's `QueryAwareRecall` classifies queries by task type and adjusts retrieval accordingly:

| Task Type | Risk Tolerance | Latency Budget | Behavior |
|-----------|---------------|----------------|----------|
| **fast_answer** | High | Fast | Minimal retrieval, top-k only |
| **general** | Medium | Normal | Standard vector search + graph context |
| **exploration** | Medium | Normal | Broader search, more candidates |
| **critical_decision** | Low | Deep | Cross-checking, multi-source validation |

A casual question gets a fast answer. A medical recommendation triggers deep retrieval with cross-validation. The memory system allocates cognitive resources proportionally to the stakes.

---

## The CognitiveEngine: Full Orchestration

The **CognitiveEngine** is the top of the stack — Layer 7 in our OSI analogy. It orchestrates a nine-phase pipeline for every conversation turn:

1. **Validate & Prep** — Input validation, skill feedback processing
2. **Store in STM** — User message saved to short-term chat history
3. **Parallel Retrieval** — Concurrent fetch from:
   - Long-term memory (vector search)
   - Short-term memory (recent messages)
   - Knowledge graph (GraphRAG)
   - Procedural memory (relevant skills)
   - Predictive memory (prospective triggers)
4. **Context Formatting** — Raw retrievals formatted into prompt-ready strings
5. **System Prompt Assembly** — Context engineering: persona injection, dynamic instructions, STM optimization (sliding window + summarization)
6. **LLM Generation** — Response generation with full context
7. **Store Response** — Assistant message saved to STM
8. **Background Extraction** — Facts extracted and stored in LTM (async, non-blocking)
9. **Result Assembly** — Final response with metadata

The key insight is **Phase 3: parallel retrieval**. The engine doesn't search memories sequentially — it fans out across all memory tiers simultaneously, then merges results with deduplication. Graph context is deduplicated against LTM results so the prompt doesn't contain redundant information.

```python
from mdb_engine.memory import ChatEngine

response = await chat_engine.chat(
    message="What was that restaurant I mentioned last week?",
    user_id="user123"
)
```

One call. Nine phases. STM + LTM + knowledge graph + procedural skills + predictive context + LLM generation. No manual context window management. No "retrieve then generate" boilerplate.

---

## Memory That Respects Privacy

Real-world AI needs privacy controls that go beyond access tokens:

### Memory Vetoes

Users can flag memories as "never share" with configurable scopes:
- `"all"` — Never surface this memory in any context
- `"family"` — Don't share with family/group members
- `"system"` — Don't use for system-level analytics

The veto system runs `check_veto()` before any memory promotion to shared pools. Vetoed memories are invisible to the sharing pipeline.

### Shared/Group Memory

Not all knowledge is private. Teams share context. mdb-engine supports **privacy-safe memory promotion** with strict guardrails:

- Multiple users (default: 2+) must independently establish a fact before it's promotable
- Confidence threshold (default: 0.7) — low-confidence facts stay private
- Sensitivity filtering — only "low" sensitivity facts are candidates
- Keyword screening — sensitive keywords block promotion automatically
- Completeness check — fragments (< 3 words) are never promoted
- **Anonymization** — user-specific references are stripped before promotion

### GDPR Compliance

Built into the data layer, not bolted on:

- **Soft delete**: `is_active: false` — memory hidden but recoverable
- **Hard delete**: `delete_all(hard_delete=True)` — permanent, irreversible removal
- **Data export**: Full memory export per user
- **Right to be forgotten**: Cascade deletion across all tiers, including knowledge graph edges

---

## Why MongoDB? (The Technical Case)

### 1. Native Vector Search

MongoDB Atlas Vector Search means your embeddings live *next to* your data — not in a separate system. One aggregation pipeline can combine semantic similarity with metadata filtering, importance scoring, and access tracking:

```python
pipeline = [
    {
        "$vectorSearch": {
            "index": "memory_vector_index",
            "path": "embedding",
            "queryVector": query_vector,
            "numCandidates": limit * 20,
            "limit": limit * 2,
            "filter": {
                "user_id": user_id,
                "metadata.timeline_id": {"$in": accessible_timelines},
                "metadata.confidence": {"$gte": 0.5},
                "is_active": True
            }
        }
    },
    {"$addFields": {"similarity": {"$meta": "vectorSearchScore"}}},
]
```

No network hops between your vector store and your document store. No sync jobs. No drift. Vector search, metadata filtering, timeline scoping, and confidence thresholds — **one query, one round trip.**

### 2. The Document Model Fits Memory Naturally

A memory isn't a row. It's a rich, nested document with embeddings, version history, emotion tags, access counts, and graph references. MongoDB's document model stores this without schema gymnastics:

```json
{
  "memory": "User prefers oat milk in their coffee",
  "embedding": [0.023, -0.041, "..."],
  "importance": 0.7,
  "confidence": 0.92,
  "access_count": 12,
  "emotion": "neutral",
  "category": "preferences",
  "user_id": "user123",
  "bucket_id": "preferences",
  "version": 3,
  "history": [
    {"value": "likes whole milk", "recorded_at": "2025-01-10T..."},
    {"value": "prefers oat milk", "recorded_at": "2025-03-15T..."}
  ],
  "created_at": "2025-03-15T10:30:00Z",
  "last_accessed": "2025-03-20T08:00:00Z"
}
```

### 3. `$graphLookup` — Graph Traversal Without a Graph Database

MongoDB's `$graphLookup` gives you multi-hop graph traversal natively. mdb-engine uses this for its full knowledge graph — entity extraction, relationship tracking, GraphRAG queries — all without deploying Neo4j, managing another cluster, or syncing data between systems.

The knowledge graph and the memories it describes live in the **same database, same query language, same transaction scope**. That co-location isn't just convenient — it eliminates an entire class of consistency bugs.

### 4. Multi-Tenancy is a First-Class Citizen

MongoDB's flexible querying makes it trivial to scope every operation by `app_id`. mdb-engine's `ScopedMongoWrapper` intercepts every query and injects tenant isolation automatically:

```python
# What you write:
await db.users.find({"status": "active"})

# What actually executes:
{"$and": [{"status": "active"}, {"app_id": {"$in": ["my_app"]}}]}
```

You literally cannot leak data across tenants. The scoping layer sits below your code and above the database — Layer 2 in our OSI model — and it's invisible to your business logic.

---

## Beyond Memory: What the Engine Handles

Memory is the headline feature, but mdb-engine is a full runtime:

- **Automatic data scoping** — Every query filtered by `app_id`. No accidental cross-tenant reads. The `ScopedMongoWrapper` proxy pattern ensures isolation is structural, not behavioral.
- **Manifest-driven configuration** — One `manifest.json` defines your app's identity, indexes, auth, AI services, and more. Schema-versioned (currently v2.0) with automatic migration from older formats.
- **Multi-app architecture** — Run multiple isolated apps on one engine with cross-app data sharing via read scopes. Single MongoDB connection pool. Single auth pool. Individual data isolation.
- **Auth built in** — JWT authentication with session binding (IP/fingerprint validation). RBAC via Casbin (MongoDB-backed policy storage) or OSO Cloud. SSO with `SharedUserPool` for multi-app deployments. Per-app role hierarchies auto-generated from manifests.
- **Declarative indexes** — Define indexes in your manifest. The engine creates and manages them. Regular indexes, vector indexes, TTL indexes — all declarative.
- **Dependency injection** — `RequestContext` for all-in-one access, individual dependencies like `get_scoped_db` and `get_memory_service` for simple routes, and a full DI `Container` with singleton/request/transient scopes for custom services.

The philosophy is simple: **you write clean, naive code. The engine handles the complexity.**

---

## The Value Proposition

Let's be concrete about what this architecture delivers:

### For Developers

- **One database.** MongoDB handles documents, vectors, graphs, TTL caches, and multi-tenant isolation. That's five fewer systems to deploy, monitor, sync, and debug.
- **Three lines to production.** `quickstart("my_app")` gives you a scoped, authenticated, memory-enabled API. No infrastructure week.
- **Strategies, not rewrites.** Need different memory behavior? Swap a strategy object. Don't rewrite the pipeline.

### For AI Agents

- **Genuine learning.** The consolidation pipeline means the agent actually gets smarter over time — not because you retrained the model, but because its knowledge base grows and refines itself.
- **Contextual recall.** The combination of vector search, knowledge graph traversal, and query-aware retrieval means the agent retrieves the *right* context, not just *similar* context.
- **Self-awareness.** Reflective memory gives the agent the ability to reason about its own knowledge gaps and failure modes.

### For Users

- **Continuity.** The agent remembers. Not just the last message, but the conversation from three weeks ago, the preference mentioned in passing, the correction made offhandedly.
- **Privacy.** Memory vetoes, GDPR compliance, and anonymized sharing mean users control what the agent retains and shares.
- **Trust.** An agent that remembers accurately, admits what it doesn't know (reflective memory), and improves over time (neuroplasticity) is an agent worth using.

---

## The Real Pitch

If you're building AI applications on MongoDB, you have two choices:

1. Wire up Motor, build a scoping layer, integrate a vector store, implement a knowledge graph, build a six-tier memory system with consolidation and neuroplasticity, add auth middleware, handle multi-tenancy, manage indexes, implement duplicate detection and merging, build privacy controls, and maintain all of it forever.

2. Use mdb-engine.

```python
from mdb_engine import quickstart
from mdb_engine.dependencies import get_memory_service
from fastapi import Depends

app = quickstart("my_app")

@app.post("/remember")
async def remember(text: str, memory=Depends(get_memory_service)):
    return await memory.add(messages=text, user_id="user1")

@app.get("/recall")
async def recall(query: str, memory=Depends(get_memory_service)):
    return await memory.search(query=query, user_id="user1", limit=5)
```

That's a production-ready AI memory API. Scoped. Authenticated. Searchable. Persistent. Backed by a six-tier cognitive architecture, a semantic knowledge graph, and a neuroplasticity engine — all running on a single MongoDB cluster.

**Stop building scaffolding. Start building features.**

---

*mdb-engine is open source under the MIT License. [GitHub](https://github.com/ranfysvalle02/mdb-engine)*
