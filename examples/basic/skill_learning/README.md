# Nexus -- AI SRE Skill Learning Demo

A pure-Python CLI example demonstrating **Memory + Procedural Skill Learning** with MDB-Engine. No GUI, no web server -- just a script that tells a story.

## What It Does

Nexus is a simulated AI Site Reliability Engineer that handles infrastructure incidents. Each time it resolves an issue, it stores the procedure as a **skill**. When a similar incident surfaces later, Nexus recalls the learned skill and resolves it instantly.

**The narrative:**

| Act | Incident | Skills Known | Resolution Time |
|-----|----------|-------------|-----------------|
| 1 | DB Replication Lag | 0 | 12 minutes |
| 2 | DB Replication Lag (again) | 1 | 45 seconds |
| 3 | Payment Service CrashLoop | 1 | 15 minutes |
| 4 | DB Replication Lag (infra changed) | 2 | 8 minutes (adapted) |

**Key capabilities demonstrated:**

- `memory.inject()` -- storing infrastructure knowledge as long-term memories
- `memory.search()` -- semantic recall of relevant context during incidents
- `ProceduralMemory.store_procedure()` -- learning skills from successful resolutions
- `ProceduralMemory.search_procedures()` -- recalling skills for similar problems
- `ProceduralMemory.mark_procedure_used()` -- tracking success/failure feedback
- `ProceduralMemory.deactivate_below_threshold()` -- pruning underperforming skills
- `ProceduralMemory.format_for_prompt()` -- formatting skills for LLM context injection

## Prerequisites

- Python 3.11+
- A running MongoDB instance (local or Atlas)
- An OpenAI API key (for embeddings only)

## Setup

```bash
# From the repo root
cd examples/basic/skill_learning

# Install mdb-engine with AI extras
pip install -e "../../..[ai]"

# Install example deps
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your MONGO_URI and OPENAI_API_KEY
```

## Run

```bash
python run.py
```

To keep data in MongoDB after the demo (for inspection):

```bash
python run.py --no-cleanup
```

## Expected Output

```
======================================================================
  NEXUS -- AI Site Reliability Engineer
  Memory + Skill Learning Demo  |  mdb-engine
======================================================================

--- PROLOGUE: Bootstrapping Nexus ---
  [engine] MongoDBEngine initialized
  ...
  [memory] Injected 8 infrastructure memories.

--- ACT 1: First Incident -- Database Replication Lag ---
  [ALERT] pg-primary replication lag > 30s  @ 2025-02-14T03:22:00Z
  [search] No matching skills found. Reasoning from scratch.
  [resolve] Resolution time: 12 minutes (manual reasoning)
  [learn] NEW SKILL stored: "Resolve PostgreSQL Replication Lag"

--- ACT 2: Deja Vu -- Similar DB Issue ---
  [search] MATCH: "Resolve PostgreSQL Replication Lag" (success: 1.00, used: 0x)
  [apply] Resolution time: 45 seconds (skill-assisted)

--- ACT 3: New Territory -- Payment Service CrashLoop ---
  [search] No matching skills found. This is a new problem domain.
  [learn] NEW SKILL stored: "Resolve K8s CrashLoopBackOff (Stale Secret)"

--- ACT 4: Adaptation -- Learned Skill Fails, Agent Adapts ---
  [FAIL] Skill execution FAILED. Replication lag persists.
  [learn] EVOLVED SKILL stored: "Resolve Logical Replication Lag (v2)"

--- EPILOGUE: Nexus Knowledge Dashboard ---
  Total memories: 12
  Skills learned: 2  |  Skills pruned: 1
  Fastest resolution: 45s  |  Slowest: 15min

======================================================================
  DEMO COMPLETE
  Nexus learned 3 skills, adapted 1, and resolved 4 incidents.
======================================================================
```
