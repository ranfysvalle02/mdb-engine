# Longitudinal AI - Cross-Call Intelligence System

## Overview

The Parallax demo now includes a **Longitudinal AI** system that transforms episodic call data into strategic customer relationship intelligence. This is the "magical layer" that powers business logic beyond simple chat memory.

## Architecture

### The Shift: From Episodic to Longitudinal

**Standard "Chat Memory":**
- Scope: Single Session
- Data Type: Raw Text / Vectors
- Goal: Continuity in conversation
- Trigger: User Prompt

**Longitudinal AI (This System):**
- Scope: Multi-Session / Lifecycle
- Data Type: Synthesized Insights / Trends
- Goal: Strategic decision making
- Trigger: Asynchronous Workflow

## Core Components

### 1. Intelligence Orchestrator (`intelligence_orchestrator.py`)

The "Magic Layer" that orchestrates all cross-call intelligence:

- **Ingestion**: Receives new call data after analysis
- **Synthesis**: Runs background jobs to detect patterns, contradictions, and trends
- **Storage**: Updates customer profiles and relationship health scores

**Key Method:**
```python
async def synthesize_after_call(call_id, customer_company, call_data)
```

This runs automatically after each call is analyzed, orchestrating:
- Pattern detection (Sentinel)
- Continuity summary generation
- Contradiction detection
- Profile updates

### 2. Sentinel (`sentinel.py`)

Pattern Recognition System that detects subtle shifts across calls:

**Capabilities:**
- **Sentiment Velocity**: Tracks sentiment trends (e.g., "Customer sentiment has declined 5% in every call for the last month - silent churn risk")
- **Topic Clustering**: Identifies recurring themes (e.g., "API latency mentioned in 3 distinct calls - not a glitch, it's a dealbreaker")
- **Engagement Trends**: Analyzes changes in engagement level
- **Risk Indicators**: Early warning signs for churn

**Example Output:**
```json
{
  "sentiment_velocity": {
    "trend": "declining",
    "velocity": -0.05,
    "risk_level": "high"
  },
  "topic_clusters": [
    {
      "topic": "API latency",
      "mention_count": 3,
      "severity": "high"
    }
  ],
  "risk_indicators": [
    {
      "type": "sentiment_decline",
      "severity": "high",
      "description": "Customer sentiment has declined...",
      "recommendation": "Immediate intervention recommended"
    }
  ]
}
```

### 3. Continuity Manager (`continuity_manager.py`)

Generates "State of the Union" summaries for seamless interactions:

**Instead of:** "How can I help you?"

**The Agent knows:** "I see we fixed that billing issue from last Tuesday, are you calling about the integration step we discussed?"

**Capabilities:**
- Recent call history summary
- Open items from previous calls
- Customer journey stage
- Recommended talking points

**Example Output:**
```json
{
  "recent_history": "Customer has been working through integration challenges...",
  "open_items": [
    {
      "item": "Follow up on API integration",
      "priority": "high",
      "source_call": "call_123"
    }
  ],
  "journey_stage": {
    "stage": "onboarding",
    "confidence": "medium"
  },
  "talking_points": [
    "Follow up on: API integration",
    "Reference recent discussion: Integration progress"
  ]
}
```

### 4. Contradiction Detector (`contradiction_detector.py`)

Compares new claims against historical facts:

**Detects:**
- Numerical contradictions (e.g., "50 seats" → "200 users" = upsell opportunity)
- Factual contradictions (changing requirements)
- Timeline inconsistencies
- Growth indicators (positive contradictions showing expansion)

**Example Output:**
```json
{
  "contradictions": [
    {
      "type": "seat_count_increase",
      "field": "seat_count",
      "old_value": "50",
      "new_value": "200",
      "significance": "high",
      "description": "Customer mentioned 50 seats in Call 1, now mentions 200 users",
      "opportunity": "upsell"
    }
  ]
}
```

### 5. Customer Profile Manager (`customer_profile.py`)

Maintains dynamic customer profiles that evolve over time:

**Tracks:**
- **Relationship Health Score** (0-100): Calculated from sentiment velocity, risk indicators, contradictions
- Customer journey stage
- Key insights and trends
- Risk indicators
- Engagement metrics

**Example Output:**
```json
{
  "customer_company": "Acme Corp",
  "relationship_health_score": 75,
  "total_calls": 5,
  "recent_patterns": {
    "sentiment_velocity": {...},
    "risk_indicators": [...]
  },
  "active_contradictions": [...],
  "upsell_opportunities": [...],
  "journey_stage": {...}
}
```

## API Endpoints

### Get Complete Intelligence
```
GET /api/customers/{company}/intelligence
```
Returns complete longitudinal intelligence including profile, patterns, contradictions, and continuity summary.

### Get Customer Profile
```
GET /api/customers/{company}/profile
```
Returns customer profile with relationship health score.

### Get Patterns
```
GET /api/customers/{company}/patterns?limit=5
```
Returns detected patterns (Sentinel analysis).

### Get Contradictions
```
GET /api/customers/{company}/contradictions
```
Returns detected contradictions.

### Get Continuity Summary
```
GET /api/customers/{company}/continuity
```
Returns "State of the Union" summary.

### Get All Customers
```
GET /api/customers?limit=50
```
Returns all customer profiles.

## Dashboard Integration

The dashboard now includes a **"Longitudinal AI"** button on each report card that opens a modal displaying:

1. **Relationship Health Score** (0-100) with visual indicator
2. **Sentiment Velocity** - Trend analysis showing improving/declining/stable
3. **Risk Indicators** - Early warning signs with recommendations
4. **Detected Contradictions** - Including upsell opportunities
5. **State of the Union** - Recent history summary
6. **Recommended Talking Points** - For next interaction

## How It Works

1. **After Each Call Analysis:**
   - Parallax Engine analyzes the call through SALES, MARKETING, PRODUCT lenses
   - Intelligence Orchestrator is triggered automatically
   - Synthesis job runs in background (non-blocking)

2. **Synthesis Process:**
   - Retrieves recent calls for the customer (last 5-10)
   - Runs Sentinel pattern detection
   - Detects contradictions
   - Generates continuity summary
   - Updates customer profile with health score

3. **Intelligence Available:**
   - Before next interaction, agent can retrieve complete intelligence
   - Dashboard displays insights visually
   - API endpoints provide programmatic access

## Requirements

- **Minimum 2 calls** per customer for cross-call intelligence
- OpenAI/Azure OpenAI client for LLM-powered analysis
- MongoDB for storing synthesis results and profiles

## Database Collections

New collections created:
- `customer_profiles` - Customer profiles with health scores
- `sentinel_patterns` - Pattern detection results
- `contradictions` - Detected contradictions
- `continuity_summaries` - Continuity summaries
- `intelligence_synthesis` - Complete synthesis results

## Benefits

1. **Proactive Risk Detection**: Identify churn risks before they become problems
2. **Upsell Opportunities**: Detect growth indicators automatically
3. **Context-Aware Interactions**: Agents have full context before interactions
4. **Strategic Insights**: Move from reactive to proactive customer management
5. **Relationship Health Tracking**: Quantify customer relationship strength

## Example Use Cases

### Use Case 1: Silent Churn Detection
**Scenario:** Customer is polite but sentiment declining
**Detection:** Sentinel identifies 5% sentiment decline over last month
**Action:** System flags as "high risk" and recommends immediate intervention

### Use Case 2: Upsell Opportunity
**Scenario:** Customer mentions 50 seats in Call 1, 200 users in Call 3
**Detection:** Contradiction Detector identifies seat count increase
**Action:** System flags as "upsell opportunity" with high significance

### Use Case 3: Recurring Concern
**Scenario:** Customer mentions "API latency" in 3 separate calls over 6 months
**Detection:** Sentinel topic clustering identifies recurring theme
**Action:** System flags as "dealbreaker" (not a glitch) requiring direct address

## Future Enhancements

Potential additions:
- Graph database for entity relationships
- Predictive churn modeling
- Automated action recommendations
- Integration with CRM systems
- Real-time alerts for critical patterns

## Technical Notes

- Synthesis runs asynchronously to avoid blocking call analysis
- LLM calls are optimized with structured prompts
- Health score calculation uses weighted factors
- All components handle edge cases gracefully (insufficient data, errors, etc.)
