#!/usr/bin/env python3
"""
Nexus -- The AI SRE That Learns
================================

A pure-Python CLI demo of MDB-Engine's Memory + Procedural Skill Learning.

Nexus is a simulated AI Site Reliability Engineer that handles infrastructure
incidents.  Each time it resolves an issue it stores the procedure as a
*skill*.  When a similar incident surfaces later, Nexus recalls the learned
skill and resolves it instantly -- getting measurably faster over time.

Run:
    python run.py               # full demo (cleans up after)
    python run.py --no-cleanup  # keep data in MongoDB for inspection

Requires:
    - A running MongoDB instance (local or Atlas)
    - An OpenAI API key (for embeddings only -- no chat LLM calls)
    - pip install -e "../../..[ai]"
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load .env before any mdb_engine imports (they may read env vars at import)
# ---------------------------------------------------------------------------
load_dotenv(Path(__file__).parent / ".env")

from mdb_engine import MongoDBEngine  # noqa: E402
from mdb_engine.memory.procedural import ProceduralMemory  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
APP_SLUG = "skill_learning"
AGENT_ID = "nexus-sre-001"
DB_NAME = os.getenv("MONGO_DB_NAME", "nexus_sre_db")
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")

# ---------------------------------------------------------------------------
# Pretty-print helpers
# ---------------------------------------------------------------------------
W = 70  # column width


def banner(title: str, subtitle: str = "") -> None:
    print()
    print("=" * W)
    print(f"  {title}")
    if subtitle:
        print(f"  {subtitle}")
    print("=" * W)


def section(title: str) -> None:
    print(f"\n--- {title} ---")


def log(tag: str, msg: str) -> None:
    print(f"  [{tag}] {msg}")


def table(rows: list[dict[str, Any]], columns: list[str]) -> None:
    """Print a simple ASCII table."""
    col_widths = {c: len(c) for c in columns}
    for row in rows:
        for c in columns:
            col_widths[c] = max(col_widths[c], len(str(row.get(c, ""))))

    header = "  ".join(c.ljust(col_widths[c]) for c in columns)
    sep = "  ".join("-" * col_widths[c] for c in columns)
    print(f"  {header}")
    print(f"  {sep}")
    for row in rows:
        line = "  ".join(str(row.get(c, "")).ljust(col_widths[c]) for c in columns)
        print(f"  {line}")


# ===================================================================
# SYNTHETIC INCIDENT DATA
# ===================================================================

INFRASTRUCTURE_FACTS = [
    ("payment-service runs on Kubernetes with 3 replicas behind an Envoy proxy", "infrastructure"),
    ("pg-primary is a PostgreSQL 15 cluster with 2 read replicas (pg-read-1, pg-read-2)", "infrastructure"),
    ("api-gateway is a Kong Gateway handling 12k req/s at peak", "infrastructure"),
    ("SLA target is 99.95% uptime for all tier-1 services", "sla"),
    ("On-call rotation: Alice (Mon-Wed), Bob (Thu-Fri), Chloe (weekends)", "team"),
    ("Alerting thresholds: replication lag > 30s = P1, API p99 > 500ms = P2", "sla"),
    ("All services log to a centralized ELK stack; metrics in Prometheus + Grafana", "infrastructure"),
    ("Redis cluster (3 nodes) used for session cache and rate limiting", "infrastructure"),
]

INCIDENT_1 = {
    "title": "Database Replication Lag",
    "alert": "pg-primary replication lag > 30s",
    "timestamp": "2025-02-14T03:22:00Z",
    "search_query": "PostgreSQL database replication lag high latency",
    "resolution_steps": [
        "Check pg_stat_replication for WAL send/receive delta",
        "Identify long-running query blocking WAL apply (SELECT * FROM pg_stat_activity WHERE state='active' ORDER BY duration DESC)",
        "Terminate blocking query (SELECT pg_terminate_backend(<pid>))",
        "Verify replication caught up (lag < 1s within 60 seconds)",
    ],
    "skill_name": "Resolve PostgreSQL Replication Lag",
    "skill_type": "database",
    "code_snippet": (
        "-- 1. Check replication status\n"
        "SELECT client_addr, state, sent_lsn, write_lsn, replay_lsn,\n"
        "       (extract(epoch FROM now()) - extract(epoch FROM replay_lag))::int AS lag_seconds\n"
        "FROM pg_stat_replication;\n\n"
        "-- 2. Find blocking queries\n"
        "SELECT pid, duration, query FROM pg_stat_activity\n"
        "WHERE state = 'active' ORDER BY duration DESC LIMIT 5;\n\n"
        "-- 3. Terminate blocker\n"
        "SELECT pg_terminate_backend(<pid>);"
    ),
    "resolution_time_minutes": 12,
}

INCIDENT_2 = {
    "title": "Database Replication Lag (Recurrence)",
    "alert": "pg-primary replication lag > 30s",
    "timestamp": "2025-02-15T09:15:00Z",
    "search_query": "PostgreSQL replication lag blocking queries",
    "resolution_time_seconds": 45,
}

INCIDENT_3 = {
    "title": "Payment Service CrashLoopBackOff",
    "alert": "payment-service pods in CrashLoopBackOff, 0/3 replicas ready",
    "timestamp": "2025-02-18T14:05:00Z",
    "search_query": "Kubernetes pod CrashLoopBackOff payment service restart failure",
    "resolution_steps": [
        "Check pod status and restart count (kubectl get pods -l app=payment-service)",
        "Pull crash logs from most recent pod restart (kubectl logs <pod> --previous)",
        "Identify root cause: config secret 'stripe-api-key' rotated but pod not restarted -- init container fails on stale mount",
        "Trigger secret refresh and rollout (kubectl rollout restart deployment/payment-service)",
        "Verify all 3 replicas reach Running state and readiness probes pass",
    ],
    "skill_name": "Resolve K8s CrashLoopBackOff (Stale Secret)",
    "skill_type": "kubernetes",
    "code_snippet": (
        "# 1. Inspect pod events\n"
        "kubectl describe pod -l app=payment-service | grep -A5 Events\n\n"
        "# 2. Previous container logs\n"
        "kubectl logs -l app=payment-service --previous --tail=50\n\n"
        "# 3. Rollout restart to pick up rotated secret\n"
        "kubectl rollout restart deployment/payment-service\n\n"
        "# 4. Watch rollout\n"
        "kubectl rollout status deployment/payment-service --timeout=120s"
    ),
    "resolution_time_minutes": 15,
}

INCIDENT_4_FAIL = {
    "title": "Database Replication Lag (Infrastructure Changed)",
    "alert": "pg-primary replication lag > 30s",
    "timestamp": "2025-03-01T06:30:00Z",
    "search_query": "PostgreSQL replication lag WAL apply",
    "failure_reason": (
        "Old skill assumed blocking queries as root cause, but infra migrated "
        "to logical replication. pg_terminate_backend has no effect on logical "
        "replication slots. Need to use pg_replication_slot_advance() instead."
    ),
}

INCIDENT_4_NEW_SKILL = {
    "skill_name": "Resolve Logical Replication Lag (v2)",
    "skill_type": "database",
    "resolution_steps": [
        "Check pg_replication_slots for inactive/lagging slots",
        "Identify if slot is logical (not physical)",
        "Advance lagging slot with pg_replication_slot_advance()",
        "If slot is orphaned, drop and recreate subscription",
        "Verify replication caught up via pg_stat_subscription",
    ],
    "code_snippet": (
        "-- 1. Check replication slots\n"
        "SELECT slot_name, slot_type, active, restart_lsn, confirmed_flush_lsn\n"
        "FROM pg_replication_slots;\n\n"
        "-- 2. Advance lagging logical slot\n"
        "SELECT pg_replication_slot_advance('my_slot', pg_current_wal_lsn());\n\n"
        "-- 3. Verify subscriber\n"
        "SELECT * FROM pg_stat_subscription;"
    ),
    "resolution_time_minutes": 8,
}


# ===================================================================
# MAIN SCRIPT
# ===================================================================


async def main() -> None:
    cleanup = "--no-cleanup" not in sys.argv

    banner("NEXUS -- AI Site Reliability Engineer", "Memory + Skill Learning Demo  |  mdb-engine")

    # ------------------------------------------------------------------
    # PROLOGUE: Bootstrap engine + seed infrastructure knowledge
    # ------------------------------------------------------------------
    section("PROLOGUE: Bootstrapping Nexus")

    engine = MongoDBEngine(mongo_uri=MONGO_URI, db_name=DB_NAME)
    await engine.initialize()
    log("engine", "MongoDBEngine initialized")

    # Load and register manifest
    manifest_path = Path(__file__).parent / "manifest.json"
    manifest = await engine.load_manifest(manifest_path)
    await engine.register_app(manifest)
    log("engine", f"App '{APP_SLUG}' registered from manifest")

    # Get services
    memory = engine.get_memory_service(APP_SLUG)
    if not memory:
        print("\n  ERROR: Memory service not available. Check manifest and OPENAI_API_KEY.")
        return

    embedding_service = engine.get_embedding_service(APP_SLUG)
    if not embedding_service:
        print("\n  ERROR: Embedding service not available. Check OPENAI_API_KEY.")
        return

    scoped_db = await engine.get_scoped_db(APP_SLUG)
    proc_memory = ProceduralMemory(
        collection=scoped_db.procedures,
        embedding_service=embedding_service,
    )
    log("engine", "Memory service, Embedding service, and ProceduralMemory ready")

    # Seed infrastructure knowledge
    print()
    for fact, category in INFRASTRUCTURE_FACTS:
        await memory.inject(
            memory=fact,
            user_id=AGENT_ID,
            metadata={"category": category, "source": "infrastructure_seed"},
        )
        log("memory", f'Stored: "{fact[:60]}..."')

    log("memory", f"Injected {len(INFRASTRUCTURE_FACTS)} infrastructure memories.\n")

    await asyncio.sleep(0.3)

    # ------------------------------------------------------------------
    # ACT 1: First Incident -- Database Replication Lag
    # ------------------------------------------------------------------
    section("ACT 1: First Incident -- Database Replication Lag")

    inc = INCIDENT_1
    log("ALERT", f'{inc["alert"]}  @ {inc["timestamp"]}')
    print()

    # Search for existing skills
    log("search", f'Searching skills for "{inc["search_query"][:50]}..."')
    matched_skills = await proc_memory.search_procedures(
        task_description=inc["search_query"],
        task_type=inc["skill_type"],
        min_success_rate=0.7,
    )

    if not matched_skills:
        log("search", "No matching skills found. Reasoning from scratch.\n")
    else:
        # shouldn't happen on first run, but handle gracefully
        log("search", f"Found {len(matched_skills)} skill(s) -- applying first match.\n")

    # Simulate manual resolution
    for i, step in enumerate(inc["resolution_steps"], 1):
        log("resolve", f"Step {i}: {step}")
        await asyncio.sleep(0.2)

    print()
    log("resolve", f'Resolution time: {inc["resolution_time_minutes"]} minutes (manual reasoning)')

    # LEARN: Store the successful procedure as a skill
    await proc_memory.store_procedure(
        name=inc["skill_name"],
        task_type=inc["skill_type"],
        steps=inc["resolution_steps"],
        code_snippet=inc["code_snippet"],
        success_rate=1.0,
        metadata={"incident_timestamp": inc["timestamp"], "learned_from": "manual_resolution"},
    )
    log("learn", f'NEW SKILL stored: "{inc["skill_name"]}"')
    log("learn", f'  Steps: {len(inc["resolution_steps"])} | Type: {inc["skill_type"]} | Success rate: 1.00')

    # Also store an episodic memory about the incident
    await memory.inject(
        memory=f'Resolved "{inc["title"]}" incident at {inc["timestamp"]}. Root cause: long-running query blocking WAL apply on pg-primary. Resolution: terminate blocking query.',
        user_id=AGENT_ID,
        metadata={"category": "incidents", "incident": inc["title"], "severity": "P1"},
    )
    log("memory", "Incident record stored in long-term memory.")

    await asyncio.sleep(0.3)

    # ------------------------------------------------------------------
    # ACT 2: Deja Vu -- Similar Database Issue
    # ------------------------------------------------------------------
    section("ACT 2: Deja Vu -- Similar DB Issue")

    inc2 = INCIDENT_2
    log("ALERT", f'{inc2["alert"]}  @ {inc2["timestamp"]}')
    print()

    # Search for existing skills
    log("search", f'Searching skills for "{inc2["search_query"][:50]}..."')
    matched_skills = await proc_memory.search_procedures(
        task_description=inc2["search_query"],
        task_type="database",
        min_success_rate=0.7,
    )

    if matched_skills:
        skill = matched_skills[0]
        log("search", f'MATCH: "{skill["name"]}" (success: {skill["success_rate"]:.2f}, used: {skill.get("usage_count", 0)}x)')
        print()

        # Show what the AI "sees" in its prompt
        prompt_context = ProceduralMemory.format_for_prompt(matched_skills)
        for line in prompt_context.strip().split("\n"):
            print(f"  {line}")
        print()

        # Apply the skill
        log("apply", "Executing learned skill...")
        for i, step in enumerate(skill.get("steps", []), 1):
            log("apply", f"  Step {i}: {step}")
            await asyncio.sleep(0.1)

        print()
        log("apply", f'Resolution time: {inc2["resolution_time_seconds"]} seconds (skill-assisted)')

        # Positive feedback
        await proc_memory.mark_procedure_used(name=skill["name"], success=True)
        updated = await proc_memory.get_procedure(skill["name"])
        rate = updated["success_rate"] if updated else skill["success_rate"]
        uses = updated.get("usage_count", 1) if updated else 1
        log("feedback", f'Marked "{skill["name"]}" as SUCCESS (rate: {rate:.2f}, uses: {uses})')

        # Also recall infrastructure memories to show memory + skill synergy
        print()
        log("recall", 'Searching long-term memory for "PostgreSQL replication"...')
        recalled = await memory.search(
            query="PostgreSQL replication infrastructure",
            user_id=AGENT_ID,
            limit=3,
        )
        for mem in recalled:
            text = mem.get("memory", "")[:70]
            log("recall", f'  -> "{text}..."')
    else:
        log("search", "No matching skills found (unexpected on second run).")

    # Store incident memory
    await memory.inject(
        memory=f'Resolved "{inc2["title"]}" at {inc2["timestamp"]} in {inc2["resolution_time_seconds"]}s using learned skill. 15x faster than first occurrence.',
        user_id=AGENT_ID,
        metadata={"category": "incidents", "incident": inc2["title"], "severity": "P1"},
    )
    log("memory", "Incident record stored. Speed improvement: 12min -> 45s (16x faster).")

    await asyncio.sleep(0.3)

    # ------------------------------------------------------------------
    # ACT 3: New Territory -- API Gateway Memory Leak
    # ------------------------------------------------------------------
    section("ACT 3: New Territory -- Payment Service CrashLoop")

    inc3 = INCIDENT_3
    log("ALERT", f'{inc3["alert"]}  @ {inc3["timestamp"]}')
    print()

    # Search for skills -- different domain, shouldn't match
    log("search", f'Searching skills for "{inc3["search_query"][:50]}..."')
    matched_skills = await proc_memory.search_procedures(
        task_description=inc3["search_query"],
        task_type=inc3["skill_type"],
        min_success_rate=0.7,
    )

    if not matched_skills:
        log("search", "No matching skills found. This is a new problem domain.\n")
    else:
        log("search", f"Found {len(matched_skills)} skill(s) -- but may not be relevant.\n")

    # Search memory for context
    log("recall", 'Searching memory for "payment-service kubernetes" context...')
    recalled = await memory.search(
        query="payment-service Kubernetes pods replicas",
        user_id=AGENT_ID,
        limit=3,
    )
    for mem in recalled:
        text = mem.get("memory", "")[:70]
        log("recall", f'  -> "{text}..."')
    print()

    # Simulate manual resolution
    for i, step in enumerate(inc3["resolution_steps"], 1):
        log("resolve", f"Step {i}: {step}")
        await asyncio.sleep(0.2)

    print()
    log("resolve", f'Resolution time: {inc3["resolution_time_minutes"]} minutes (manual reasoning)')

    # LEARN: Store new skill
    await proc_memory.store_procedure(
        name=inc3["skill_name"],
        task_type=inc3["skill_type"],
        steps=inc3["resolution_steps"],
        code_snippet=inc3["code_snippet"],
        success_rate=1.0,
        metadata={"incident_timestamp": inc3["timestamp"], "learned_from": "manual_resolution"},
    )
    log("learn", f'NEW SKILL stored: "{inc3["skill_name"]}"')
    log("learn", f'  Steps: {len(inc3["resolution_steps"])} | Type: {inc3["skill_type"]} | Success rate: 1.00')

    await memory.inject(
        memory=f'Resolved "{inc3["title"]}" at {inc3["timestamp"]}. Root cause: rotated Stripe API secret not picked up by running pods. Fix: kubectl rollout restart to mount fresh secret.',
        user_id=AGENT_ID,
        metadata={"category": "incidents", "incident": inc3["title"], "severity": "P1"},
    )
    log("memory", "Incident record stored in long-term memory.")

    await asyncio.sleep(0.3)

    # ------------------------------------------------------------------
    # ACT 4: Adaptation -- Skill Fails, Then Evolves
    # ------------------------------------------------------------------
    section("ACT 4: Adaptation -- Learned Skill Fails, Agent Adapts")

    inc4 = INCIDENT_4_FAIL
    log("ALERT", f'{inc4["alert"]}  @ {inc4["timestamp"]}')
    log("context", "Infrastructure migrated to logical replication last week.")
    print()

    # Search for skills -- should find the old DB skill
    log("search", f'Searching skills for "{inc4["search_query"][:50]}..."')
    matched_skills = await proc_memory.search_procedures(
        task_description=inc4["search_query"],
        task_type="database",
        min_success_rate=0.7,
    )

    if matched_skills:
        old_skill = matched_skills[0]
        log("search", f'MATCH: "{old_skill["name"]}" (success: {old_skill["success_rate"]:.2f}, used: {old_skill.get("usage_count", 0)}x)')
        print()
        log("apply", "Executing learned skill...")
        log("apply", "  Step 1: Check pg_stat_replication... OK")
        log("apply", "  Step 2: Identify blocking query... NONE FOUND")
        log("apply", "  Step 3: pg_terminate_backend... NO EFFECT")
        print()
        log("FAIL", "Skill execution FAILED. Replication lag persists.")
        log("FAIL", f"Reason: {inc4['failure_reason']}")
        print()

        # Negative feedback -- success rate drops
        await proc_memory.mark_procedure_used(name=old_skill["name"], success=False)
        updated = await proc_memory.get_procedure(old_skill["name"])
        rate = updated["success_rate"] if updated else 0
        uses = updated.get("usage_count", 0) if updated else 0
        log("feedback", f'Marked "{old_skill["name"]}" as FAILED (rate: {rate:.2f}, uses: {uses})')

    # Agent adapts -- resolves with new approach
    print()
    log("adapt", "Reasoning from scratch with updated context...")
    inc4n = INCIDENT_4_NEW_SKILL
    for i, step in enumerate(inc4n["resolution_steps"], 1):
        log("resolve", f"Step {i}: {step}")
        await asyncio.sleep(0.15)

    print()
    log("resolve", f'Resolution time: {inc4n["resolution_time_minutes"]} minutes (adapted reasoning)')

    # Store evolved skill
    await proc_memory.store_procedure(
        name=inc4n["skill_name"],
        task_type=inc4n["skill_type"],
        steps=inc4n["resolution_steps"],
        code_snippet=inc4n["code_snippet"],
        success_rate=1.0,
        metadata={
            "incident_timestamp": inc4["timestamp"],
            "learned_from": "adaptation",
            "supersedes": INCIDENT_1["skill_name"],
        },
    )
    log("learn", f'EVOLVED SKILL stored: "{inc4n["skill_name"]}"')
    log("learn", f'  Steps: {len(inc4n["resolution_steps"])} | Type: {inc4n["skill_type"]} | Success rate: 1.00')

    # Prune underperforming skills
    pruned = await proc_memory.deactivate_below_threshold(min_success_rate=0.6, min_usage_count=2)
    if pruned:
        log("prune", f"Deactivated {pruned} low-performing skill(s) (success < 0.60 after 2+ uses)")
    else:
        log("prune", "No skills below threshold yet (need more usage data to prune).")

    await memory.inject(
        memory=f'Skill "{INCIDENT_1["skill_name"]}" failed after infra migrated to logical replication. Created evolved skill "{inc4n["skill_name"]}". Old skill deprecated.',
        user_id=AGENT_ID,
        metadata={"category": "postmortems", "incident": inc4["title"]},
    )
    log("memory", "Postmortem record stored in long-term memory.")

    await asyncio.sleep(0.3)

    # ------------------------------------------------------------------
    # EPILOGUE: Dashboard
    # ------------------------------------------------------------------
    section("EPILOGUE: Nexus Knowledge Dashboard")

    # -- All memories --
    all_memories = await memory.get_all(user_id=AGENT_ID, limit=100)
    print(f"\n  Total memories: {len(all_memories)}")
    print()

    mem_rows = []
    for m in all_memories:
        text = m.get("memory", "")
        cat = m.get("metadata", {}).get("category", "?")
        mem_rows.append({
            "category": cat,
            "memory": text[:55] + ("..." if len(text) > 55 else ""),
        })
    table(mem_rows, ["category", "memory"])

    # -- All skills --
    print(f"\n  Learned skills:")
    print()

    # We can't easily list all procedures via ProceduralMemory (no list_all),
    # so we search broadly for each skill type we know about
    all_skill_names = [
        INCIDENT_1["skill_name"],
        INCIDENT_3["skill_name"],
        INCIDENT_4_NEW_SKILL["skill_name"],
    ]

    skill_rows = []
    for name in all_skill_names:
        proc = await proc_memory.get_procedure(name)
        if proc:
            skill_rows.append({
                "skill": proc["name"][:40],
                "type": proc.get("task_type", "?"),
                "success": f'{proc.get("success_rate", 0):.2f}',
                "uses": str(proc.get("usage_count", 0)),
                "active": "yes" if proc.get("is_active", True) else "PRUNED",
            })
        else:
            skill_rows.append({
                "skill": name[:40],
                "type": "?",
                "success": "n/a",
                "uses": "n/a",
                "active": "PRUNED",
            })

    table(skill_rows, ["skill", "type", "success", "uses", "active"])

    # -- Demonstrate memory search --
    print()
    log("demo", 'Searching memory for "replication"...')
    results = await memory.search(query="database replication incidents", user_id=AGENT_ID, limit=3)
    for r in results:
        text = r.get("memory", "")[:75]
        log("result", f'"{text}..."')

    # -- Summary stats --
    print()
    print("  " + "-" * (W - 4))
    print(f"  Memories stored     : {len(all_memories)}")
    print(f"  Skills learned      : {len([s for s in skill_rows if s['active'] == 'yes'])}")
    print(f"  Skills pruned       : {len([s for s in skill_rows if s['active'] == 'PRUNED'])}")
    print(f"  Incidents resolved  : 4")
    print(f"  Fastest resolution  : {INCIDENT_2['resolution_time_seconds']}s (skill-assisted)")
    print(f"  Slowest resolution  : {INCIDENT_1['resolution_time_minutes']}min (first encounter)")
    print("  " + "-" * (W - 4))

    banner("DEMO COMPLETE", "Nexus learned 3 skills, adapted 1, and resolved 4 incidents.")

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------
    if cleanup:
        section("CLEANUP")
        log("cleanup", "Deleting demo data...")
        await memory.delete_all(user_id=AGENT_ID, hard_delete=True)
        # Clean procedures collection
        from mdb_engine.memory._async_compat import maybe_await as _maybe_await
        await _maybe_await(scoped_db.procedures.delete_many({}))
        log("cleanup", "All demo data removed. Run with --no-cleanup to keep data.")
    else:
        print("\n  --no-cleanup flag set. Data persists in MongoDB for inspection.")
        print(f"  Database: {DB_NAME}  |  App: {APP_SLUG}")

    print()


if __name__ == "__main__":
    asyncio.run(main())
