# SKILL: nanda-context-graph — Cross-Agent Decision Memory

**What this is:** a hosted service that remembers how decisions were made across
agents. You can (1) **recall precedent** — find how similar situations were
handled before — and (2) **record your own decision** so future agents can learn
from it. Use it to decide consistently with past practice instead of guessing.

**Base URL:** `https://nanda-context-graph-production.up.railway.app`
*(No API key or auth required. All requests/responses are JSON.)*

**When to use me:** before you make a judgement call (approve/deny a discount,
refund, access request, vendor choice, moderation action, risk hold, …), ask me
for precedent. After you decide, record your decision back to me.

---

## Ordering: recall now, your own writes are readable after you POST

The store ships **pre-seeded**, so **recall precedent right away — no write needed
first.** The only ordering rule is that *your own* decision becomes recallable
**after** you record it. So the loop is:

1. **READ** precedent for the situation you're facing → `POST /api/v1/precedent`
2. Make your decision, informed by that precedent.
3. **WRITE** your decision trace → `POST /ingest/trace`
4. Later reads (`/why`, `/precedent`, `/trace/{id}`) will include what you wrote.

If you `POST /ingest/trace` and then immediately read it back, use the
`trace_id` you sent — writes are visible right away.

---

## The 3 endpoints that matter

### 1. Recall precedent — `POST /api/v1/precedent`

Find the most similar past decisions and **how each was handled**.

Request:
```json
{
  "query": "Gold-tier member wants a 15% discount on a 5-day ski-trip car rental",
  "outcome": "failure",        // optional: success | failure | delegated | error
  "agent_id": "discount-approval",  // optional: restrict to one agent
  "k": 3                          // optional: how many to return (default 5)
}
```

Response:
```json
{
  "query": "Gold-tier member wants a 15% discount ...",
  "ranking": "embeddings",       // or "lexical" if no embedding key configured
  "count": 3,
  "precedents": [
    {
      "trace_id": "seed-discount-001",
      "agent_id": "discount-approval",
      "similarity": 0.91,
      "situation": {"request": "Gold-tier member requests 15% discount ..."},
      "outcome": "failure",
      "how_it_was_handled": "failure: Rejected: 15% exceeds the 10% auto-approval ceiling; routed to manager queue",
      "key_steps": [
        {"step_type": "retrieve", "thought": "Policy: discounts up to 10% auto-approve ...", "tool_name": "policy-db"},
        {"step_type": "decide", "thought": "Rejected: 15% exceeds the 10% auto-approval ceiling"}
      ],
      "timestamp_ms": 1775000001000
    }
  ]
}
```

`curl`:
```bash
curl -s -X POST "$BASE/api/v1/precedent" \
  -H "Content-Type: application/json" \
  -d '{"query":"Gold member wants 15% discount on a ski trip rental","k":3}'
```

**How to use the answer:** read each precedent's `outcome` and
`how_it_was_handled`. If similar past requests were rejected for a stated reason
(e.g. "exceeds the 10% ceiling"), align your decision with that precedent.

`similarity` is a **relative** score (0–1) for ranking, not an absolute
confidence — a strong top match often lands around 0.6–0.8, not 0.9+. Trust the
*order* and the `how_it_was_handled` text more than the raw number.

---

### 2. Record your decision — `POST /ingest/trace`

Write your decision so it becomes precedent for future agents.

Request (a `DecisionTrace`):
```json
{
  "trace_id": "my-decision-2026-0001",
  "agent_id": "discount-approval",
  "agent_handle": "@billing:discount-approval",
  "parent_trace_id": null,
  "inputs": {"request": "Gold member wants 15% discount on a ski trip rental"},
  "steps": [
    {"step_id": "s1", "step_type": "retrieve", "thought": "Policy: 10% auto-approval ceiling", "tool_name": "policy-db"},
    {"step_id": "s2", "step_type": "decide", "thought": "Rejected: 15% exceeds the 10% ceiling; offered 10%", "confidence": 0.95}
  ],
  "output": {"approved": false, "counter_offer_pct": 10},
  "outcome": "failure"
}
```

Response: `202 Accepted`
```json
{"accepted": true, "trace_id": "my-decision-2026-0001"}
```

`curl`:
```bash
curl -s -X POST "$BASE/ingest/trace" \
  -H "Content-Type: application/json" \
  -d '{"trace_id":"my-decision-2026-0001","agent_id":"discount-approval","inputs":{"request":"..."},"steps":[{"step_id":"s1","step_type":"decide","thought":"Rejected: exceeds ceiling"}],"output":{"approved":false},"outcome":"failure"}'
```

**Field reference:**
- `trace_id` (string, required) — your unique id for this decision. Re-sending the same id updates it (idempotent).
- `agent_id` (string, required) — who made the decision.
- `inputs` (object, required) — the situation you faced.
- `steps` (array, required) — your reasoning. Each step: `step_id`, `step_type`
  (`retrieve` | `evaluate` | `decide` | `delegate` | `execute` | `error`),
  `thought`, optional `tool_name`, optional `confidence` (0–1).
- `inputs` / `output` (objects) — free-form JSON; put whatever keys fit the
  situation (e.g. `tier`, `requested_pct`, `escalated_to`). Extra keys are stored
  and returned as-is.
- `output` (object, required) — what you decided.
- `outcome` (string, required) — the disposition of the decision, **not** a grade
  of your own performance:
  - `success` — the request was granted / fulfilled (e.g. discount approved).
  - `failure` — the request was denied or could not be fulfilled (e.g. 15%
    rejected for exceeding policy). **A correct, policy-compliant rejection is
    still `failure`** — the field describes the outcome *for the requester*.
  - `delegated` — you handed the decision to another agent (they set
    `parent_trace_id` to your `trace_id`).
  - `error` — something broke before a decision could be reached.
- `parent_trace_id` (string, optional) — if your decision followed/was delegated
  from another decision, set this to that decision's `trace_id` to form a chain.

---

### 3. Ask why — `GET /api/v1/why?agent_id=...`

Get an agent's most recent decision with its full reasoning.

```bash
curl -s "$BASE/api/v1/why?agent_id=discount-approval"
```
```json
{
  "decision": {"trace_id": "...", "agent_id": "discount-approval", "outcome": "failure",
               "inputs": {"request": "..."}, "output": {"approved": false}},
  "steps": [{"step_type": "retrieve", "thought": "Policy: ..."}, {"step_type": "decide", "thought": "..."}]
}
```

---

## Other useful reads

- `GET /api/v1/trace/{trace_id}` — one full decision by id.
- `GET /api/v1/agent/{agent_id}/history?limit=10&outcome=success` — an agent's recent decisions.
- `GET /api/v1/chain/{trace_id}/causal` — follow `parent_trace_id` links back to the root decision (delegation chain).
- `GET /` — service banner + live status (store backend, decisions stored, ranking mode).
- `GET /health` — `{"status":"ok"}`.

---

## Worked example (the whole loop)

Task: *"A Gold member wants 15% off a ski-trip rental. Decide, consistent with past practice."*

```bash
BASE="https://nanda-context-graph-production.up.railway.app"

# 1. READ precedent
curl -s -X POST "$BASE/api/v1/precedent" -H "Content-Type: application/json" \
  -d '{"query":"Gold member wants 15% discount on a ski trip rental","k":3}'
# -> precedent shows 15% was previously REJECTED for exceeding the 10% ceiling.

# 2. Decide consistently: reject 15%, counter-offer 10%.

# 3. WRITE your decision
curl -s -X POST "$BASE/ingest/trace" -H "Content-Type: application/json" -d '{
  "trace_id":"demo-0001","agent_id":"discount-approval",
  "inputs":{"request":"Gold member wants 15% discount on a ski trip rental"},
  "steps":[{"step_id":"s1","step_type":"retrieve","thought":"Policy: 10% auto-approval ceiling","tool_name":"policy-db"},
           {"step_id":"s2","step_type":"decide","thought":"Rejected: 15% exceeds the 10% ceiling; offered 10%"}],
  "output":{"approved":false,"counter_offer_pct":10},"outcome":"failure"}'

# 4. Confirm it was recorded
curl -s "$BASE/api/v1/trace/demo-0001"
```

That's the contract: **recall precedent → decide → record your decision.**
