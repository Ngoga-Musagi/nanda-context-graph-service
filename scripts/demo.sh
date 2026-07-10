#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Live demo for nanda-context-graph — cross-agent decision memory.
#
#   ACT 1  Decision memory: an agent recalls precedent, decides, records, and
#          its decision immediately becomes precedent for the next agent.
#   ACT 2  Cross-agent causal chain: three agents (rental-broker -> rental-pricing
#          -> rental-approval) each record a decision linked by parent_trace_id,
#          then we follow the causal chain back to the root.
#
# Needs: curl + python3. Press ENTER to run each step (so you can narrate).
# Override the target with:  BASE=http://localhost:7200 ./scripts/demo.sh
#
# Trace IDs written (delete after recording): demo-live-001, demo-broker-001,
# demo-pricing-001, demo-approval-001.
# ---------------------------------------------------------------------------
set -uo pipefail
BASE="${BASE:-https://nanda-context-graph-production.up.railway.app}"

pp()   { python -c 'import sys,json; print(json.dumps(json.load(sys.stdin), indent=2))'; }
line() { printf '\n\033[2m%s\033[0m\n' "──────────────────────────────────────────────────────────"; }
step() { line; printf '\033[1;36m▶ %s\033[0m\n' "$*"; printf '\033[2m(enter)\033[0m '; read -r _; }
post() { curl -s -X POST "$BASE$1" -H "Content-Type: application/json" -d "$2"; }

clear
printf '\033[1mnanda-context-graph — live demo\033[0m\n%s\n' "$BASE"

step "It's live — no setup, no auth"
curl -s "$BASE/" | pp

# ─────────────────────────────  ACT 1  ─────────────────────────────
step "ACT 1 · An agent must decide a discount. First it RECALLS how similar cases went"
post /api/v1/precedent '{"query":"A loyal customer wants a bigger discount than policy usually allows on a holiday car rental","k":3}' \
 | python -c 'import sys,json; d=json.load(sys.stdin); print("ranking:", d["ranking"]); [print(" -", p["trace_id"], round(p["similarity"],3), "|", p["outcome"], "|", p["how_it_was_handled"][:72]) for p in d["precedents"]]'

step "Consistent with that precedent, it RECORDS its decision (reject 15%, counter 10%)"
post /ingest/trace '{
 "trace_id":"demo-live-001","agent_id":"discount-approval",
 "inputs":{"request":"Loyal customer wants 15% off a holiday rental","tier":"gold"},
 "steps":[{"step_id":"s1","step_type":"retrieve","thought":"Precedent: 15% rejected, 10% ceiling","tool_name":"nanda-context-graph"},
          {"step_id":"s2","step_type":"decide","thought":"Reject 15%; counter 10%; escalate"}],
 "output":{"approved":false,"counter_offer_pct":10,"escalated_to":"manager-queue"},"outcome":"failure"}' | pp

step "The loop closes — the decision it just made is now precedent for the next agent"
post /api/v1/precedent '{"query":"customer wants 15% off a holiday rental","agent_id":"discount-approval","k":3}' \
 | python -c 'import sys,json; print("now recallable:", [p["trace_id"] for p in json.load(sys.stdin)["precedents"]])'

# ─────────────────────────────  ACT 2  ─────────────────────────────
step "ACT 2 · Three agents collaborate. Agent 1 — rental-broker: recommends an SUV, DELEGATES pricing"
post /ingest/trace '{
 "trace_id":"demo-broker-001","agent_id":"rental-broker",
 "inputs":{"request":"Gold member: car for a 5-day ski trip, wants 15% off"},
 "steps":[{"step_id":"s1","step_type":"decide","thought":"RAV4 fits a 4-person ski trip; delegate discount to pricing"}],
 "output":{"vehicle":"RAV4","delegated_to":"rental-pricing"},"outcome":"delegated"}' >/dev/null && echo "  ✓ recorded demo-broker-001 (root)"

step "Agent 2 — rental-pricing: 15% exceeds the 10% ceiling, DELEGATES approval (parent = broker)"
post /ingest/trace '{
 "trace_id":"demo-pricing-001","agent_id":"rental-pricing","parent_trace_id":"demo-broker-001",
 "inputs":{"request":"15% discount on the RAV4 booking"},
 "steps":[{"step_id":"s1","step_type":"evaluate","thought":"15% > 10% auto-approval ceiling"},
          {"step_id":"s2","step_type":"delegate","thought":"Escalate to approval"}],
 "output":{"delegated_to":"rental-approval"},"outcome":"delegated"}' >/dev/null && echo "  ✓ recorded demo-pricing-001 (parent = demo-broker-001)"

step "Agent 3 — rental-approval: rejects 15%, counters 10%, escalates (parent = pricing)"
post /ingest/trace '{
 "trace_id":"demo-approval-001","agent_id":"rental-approval","parent_trace_id":"demo-pricing-001",
 "inputs":{"request":"Approve 15% discount?"},
 "steps":[{"step_id":"s1","step_type":"decide","thought":"Reject 15%; counter 10%; route to manager queue"}],
 "output":{"approved":false,"counter_offer_pct":10},"outcome":"failure"}' >/dev/null && echo "  ✓ recorded demo-approval-001 (parent = demo-pricing-001)"

step "WHY did this happen? Follow the causal chain from the final decision back to the root"
curl -s "$BASE/api/v1/chain/demo-approval-001/causal" | pp
line
printf '\033[1;32m→ three agents, one traceable decision.\033[0m Open the dashboard (or Neo4j) to see it as a graph.\n'
