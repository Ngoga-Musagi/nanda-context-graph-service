#!/usr/bin/env python3
"""
Real Multi-Agent Demo — Car Rental Broker with NCG Decision Traces

Three Claude-powered agents collaborate to handle a car rental request.
Every decision is traced to nanda-context-graph with full reasoning steps.

Agents:
  1. @rental:broker       - Receives user request, evaluates needs, delegates pricing
  2. @rental:pricing      - Checks rates, applies discounts, delegates approval
  3. @rental:approval     - Reviews deal, approves or rejects

Flow:
  User -> Broker (why this car?) -> Pricing (why this price?) -> Approval (why approved?)

Each agent:
  - Calls Claude for real reasoning
  - Emits a DecisionTrace with detailed steps
  - Links to parent trace via parent_trace_id (causal chain)

Usage:
  docker-compose up -d
  python examples/real_agents_demo.py

Then open the dashboard to see the traces:
  cd dashboard && npm run dev
  Open http://localhost:5173 and search for any agent ID
"""

import json
import os
import subprocess
import sys
import time
import uuid

import requests

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from anthropic import Anthropic
except ImportError:
    print("Install anthropic: pip install anthropic")
    sys.exit(1)

# ── Config ──────────────────────────────────────────────────────────
NCG_INGEST = os.getenv("NCG_INGEST_URL", "http://localhost:7200")
NCG_QUERY  = os.getenv("NCG_GRAPH_API_URL", "http://localhost:7201")
INDEX_URL  = os.getenv("INDEX_URL", "http://localhost:6900")

API_KEY = os.getenv("ANTHROPIC_API_KEY")
if not API_KEY:
    print("  ERROR: Set ANTHROPIC_API_KEY environment variable")
    print("  export ANTHROPIC_API_KEY=sk-ant-...")
    sys.exit(1)

claude = Anthropic(api_key=API_KEY)

BROKER_ID   = "rental-broker"
PRICING_ID  = "rental-pricing"
APPROVAL_ID = "rental-approval"

index_proc = None

# ── Helpers ─────────────────────────────────────────────────────────

def banner(msg):
    print(f"\n{'='*70}")
    print(f"  {msg}")
    print(f"{'='*70}")


def emit_trace(trace: dict) -> bool:
    """Send a DecisionTrace to NCG ingest. Returns True on success."""
    try:
        r = requests.post(f"{NCG_INGEST}/ingest/trace", json=trace, timeout=5)
        return r.status_code == 202
    except Exception as e:
        print(f"  [WARN] Failed to emit trace: {e}")
        return False


def ask_claude(system_prompt: str, user_message: str) -> str:
    """Call Claude and return the response text."""
    resp = claude.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=512,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    return resp.content[0].text


def register_agent(agent_id, handle):
    """Register agent with nanda-index including trace metadata."""
    requests.post(f"{INDEX_URL}/register", json={
        "agent_id": agent_id,
        "agent_url": f"http://localhost:6000",
        "api_url": f"http://localhost:6001",
        "trace": {
            "endpointURL": NCG_QUERY,
            "privacyMode": "public",
            "schemaVersion": "nanda-context-graph:1.0",
        },
    })


def cleanup():
    if index_proc:
        index_proc.terminate()
        try:
            index_proc.wait(timeout=5)
        except:
            index_proc.kill()


# ── Agent Implementations ──────────────────────────────────────────

def broker_agent(user_request: str) -> tuple[dict, str]:
    """
    Car Rental Broker: receives user request, reasons about needs,
    recommends a car, and delegates to pricing agent.
    Returns (trace_dict, recommendation).
    """
    trace_id = str(uuid.uuid4())
    start_ms = int(time.time() * 1000)
    steps = []

    # Step 1: Understand user needs
    print("  [Broker] Analyzing user request with Claude...")
    analysis = ask_claude(
        system_prompt=(
            "You are a car rental broker agent. Analyze the customer request "
            "and determine: trip type, duration, number of passengers, budget level, "
            "and any special needs. Be concise (2-3 sentences)."
        ),
        user_message=user_request,
    )
    steps.append({
        "step_id": f"s-{uuid.uuid4().hex[:6]}",
        "step_type": "evaluate",
        "thought": f"User needs analysis: {analysis}",
        "tool_name": None,
        "confidence": 0.95,
        "duration_ms": int(time.time() * 1000) - start_ms,
    })
    print(f"  [Broker] Analysis: {analysis[:120]}...")

    # Step 2: Check available inventory (simulated MCP tool call)
    print("  [Broker] Checking car inventory...")
    inventory = {
        "economy": {"model": "Toyota Corolla", "daily_rate": 45, "available": 3},
        "midsize": {"model": "Honda Accord", "daily_rate": 65, "available": 5},
        "suv":     {"model": "Toyota RAV4", "daily_rate": 85, "available": 2},
        "luxury":  {"model": "BMW 5 Series", "daily_rate": 150, "available": 1},
    }
    steps.append({
        "step_id": f"s-{uuid.uuid4().hex[:6]}",
        "step_type": "retrieve",
        "thought": "Queried car inventory database for available vehicles",
        "tool_name": "inventory-api",
        "tool_input": {"location": "Boston", "date": "2026-04-10"},
        "tool_output": inventory,
        "confidence": 1.0,
    })

    # Step 3: Claude recommends a car
    print("  [Broker] Getting recommendation from Claude...")
    recommendation = ask_claude(
        system_prompt=(
            "You are a car rental broker. Based on the customer analysis and available inventory, "
            "recommend ONE specific car. Explain WHY this car matches their needs. "
            "Format: 'I recommend [car] because [2-3 specific reasons].' Be concise."
        ),
        user_message=(
            f"Customer analysis: {analysis}\n\n"
            f"Available inventory: {json.dumps(inventory, indent=2)}\n\n"
            f"Original request: {user_request}"
        ),
    )
    steps.append({
        "step_id": f"s-{uuid.uuid4().hex[:6]}",
        "step_type": "decide",
        "thought": f"Recommendation: {recommendation}",
        "tool_name": None,
        "confidence": 0.88,
        "duration_ms": int(time.time() * 1000) - start_ms,
    })
    print(f"  [Broker] Recommendation: {recommendation[:120]}...")

    # Step 4: Delegate to pricing
    steps.append({
        "step_id": f"s-{uuid.uuid4().hex[:6]}",
        "step_type": "delegate",
        "thought": "Delegating to @rental:pricing for rate calculation and discount eligibility",
        "tool_name": "a2a-delegate",
        "tool_input": {"target": PRICING_ID, "action": "calculate_price"},
        "confidence": 1.0,
    })

    duration_ms = int(time.time() * 1000) - start_ms
    trace = {
        "trace_id": trace_id,
        "agent_id": BROKER_ID,
        "agent_handle": "@rental:broker",
        "inputs": {"user_request": user_request},
        "steps": steps,
        "output": {"recommendation": recommendation, "delegated_to": PRICING_ID},
        "outcome": "delegated",
        "timestamp_ms": start_ms,
        "duration_ms": duration_ms,
    }
    return trace, recommendation


def pricing_agent(recommendation: str, parent_trace_id: str) -> tuple[dict, dict]:
    """
    Pricing Agent: calculates final price with discounts.
    Returns (trace_dict, pricing_result).
    """
    trace_id = str(uuid.uuid4())
    start_ms = int(time.time() * 1000)
    steps = []

    # Step 1: Look up base rates
    print("  [Pricing] Looking up base rates...")
    base_rate = 85  # RAV4 or similar based on recommendation
    days = 5
    steps.append({
        "step_id": f"s-{uuid.uuid4().hex[:6]}",
        "step_type": "retrieve",
        "thought": f"Base rate lookup: ${base_rate}/day for 5-day rental",
        "tool_name": "rate-engine",
        "tool_input": {"car": "recommended", "days": days},
        "tool_output": {"base_rate": base_rate, "days": days, "subtotal": base_rate * days},
        "confidence": 1.0,
    })

    # Step 2: Check customer loyalty (simulated CRM lookup)
    print("  [Pricing] Checking customer loyalty status...")
    loyalty = {"tier": "Gold", "rentals_ytd": 8, "eligible_discount": 15}
    steps.append({
        "step_id": f"s-{uuid.uuid4().hex[:6]}",
        "step_type": "retrieve",
        "thought": f"Customer is Gold tier with {loyalty['rentals_ytd']} rentals this year. Eligible for {loyalty['eligible_discount']}% discount.",
        "tool_name": "crm-loyalty",
        "tool_input": {"customer_id": "cust-2847"},
        "tool_output": loyalty,
        "confidence": 1.0,
    })

    # Step 3: Claude evaluates discount
    print("  [Pricing] Evaluating discount with Claude...")
    pricing_analysis = ask_claude(
        system_prompt=(
            "You are a pricing agent for a car rental company. "
            "Calculate the final price and explain the discount rationale. "
            "Be specific about numbers. Format: Final price: $X. Reason: [explanation]. One paragraph."
        ),
        user_message=(
            f"Base rate: ${base_rate}/day for {days} days = ${base_rate * days}\n"
            f"Customer loyalty: {json.dumps(loyalty)}\n"
            f"Recommendation context: {recommendation[:200]}"
        ),
    )
    subtotal = base_rate * days
    discount_pct = loyalty["eligible_discount"]
    discount_amt = subtotal * discount_pct / 100
    final_price = subtotal - discount_amt

    steps.append({
        "step_id": f"s-{uuid.uuid4().hex[:6]}",
        "step_type": "evaluate",
        "thought": f"Price evaluation: {pricing_analysis}",
        "tool_name": None,
        "confidence": 0.92,
        "duration_ms": int(time.time() * 1000) - start_ms,
    })
    print(f"  [Pricing] Analysis: {pricing_analysis[:120]}...")

    # Step 4: Delegate to approval
    steps.append({
        "step_id": f"s-{uuid.uuid4().hex[:6]}",
        "step_type": "delegate",
        "thought": f"Discount of {discount_pct}% (${discount_amt:.0f}) exceeds 10% threshold. Delegating to @rental:approval.",
        "tool_name": "a2a-delegate",
        "tool_input": {"target": APPROVAL_ID, "discount_pct": discount_pct},
        "confidence": 1.0,
    })

    pricing_result = {
        "base_rate": base_rate,
        "days": days,
        "subtotal": subtotal,
        "discount_pct": discount_pct,
        "discount_amt": discount_amt,
        "final_price": final_price,
        "loyalty_tier": loyalty["tier"],
    }

    duration_ms = int(time.time() * 1000) - start_ms
    trace = {
        "trace_id": trace_id,
        "agent_id": PRICING_ID,
        "agent_handle": "@rental:pricing",
        "parent_trace_id": parent_trace_id,
        "inputs": {"recommendation": recommendation[:200], "from_agent": BROKER_ID},
        "steps": steps,
        "output": pricing_result,
        "outcome": "delegated",
        "timestamp_ms": start_ms,
        "duration_ms": duration_ms,
    }
    return trace, pricing_result


def approval_agent(pricing_result: dict, parent_trace_id: str) -> tuple[dict, str]:
    """
    Approval Agent: reviews the deal and makes final decision.
    Returns (trace_dict, decision).
    """
    trace_id = str(uuid.uuid4())
    start_ms = int(time.time() * 1000)
    steps = []

    # Step 1: Check discount policy
    print("  [Approval] Checking discount policy...")
    policy = {
        "max_auto_approve_pct": 10,
        "max_manager_approve_pct": 25,
        "requires_justification_above": 10,
        "policy_version": "discount-policy-v3.2",
    }
    steps.append({
        "step_id": f"s-{uuid.uuid4().hex[:6]}",
        "step_type": "retrieve",
        "thought": f"Loaded {policy['policy_version']}: auto-approve up to {policy['max_auto_approve_pct']}%, manager up to {policy['max_manager_approve_pct']}%",
        "tool_name": "policy-engine",
        "tool_input": {"policy": "discount-policy"},
        "tool_output": policy,
        "confidence": 1.0,
    })

    # Step 2: Check precedent (query NCG for similar past decisions)
    print("  [Approval] Checking precedent in Context Graph...")
    precedent_note = "No prior Gold-tier discount rejections found in trace history"
    steps.append({
        "step_id": f"s-{uuid.uuid4().hex[:6]}",
        "step_type": "retrieve",
        "thought": f"Precedent check: {precedent_note}",
        "tool_name": "ncg-precedent-query",
        "tool_input": {"query": "Gold tier discount > 10%", "endpoint": NCG_QUERY},
        "tool_output": {"prior_approvals": 12, "prior_rejections": 0},
        "confidence": 0.90,
    })

    # Step 3: Claude makes final decision
    print("  [Approval] Claude reviewing deal for final approval...")
    decision_text = ask_claude(
        system_prompt=(
            "You are an approval agent for a car rental company. "
            "Review the pricing deal and policy, then APPROVE or REJECT with reasoning. "
            "Format: 'APPROVED: [reason]' or 'REJECTED: [reason]'. Be specific. 2-3 sentences."
        ),
        user_message=(
            f"Pricing: {json.dumps(pricing_result)}\n"
            f"Policy: {json.dumps(policy)}\n"
            f"Precedent: 12 similar Gold-tier discounts approved, 0 rejected\n"
            f"Discount requested: {pricing_result['discount_pct']}%"
        ),
    )
    # Claude is prompted to answer "APPROVED: ..." or "REJECTED: ...".
    # Match the prefix — a substring check trips on words like "auto-approval".
    approved = decision_text.strip().upper().startswith("APPROVE")

    steps.append({
        "step_id": f"s-{uuid.uuid4().hex[:6]}",
        "step_type": "decide",
        "thought": f"Final decision: {decision_text}",
        "tool_name": None,
        "confidence": 0.96 if approved else 0.85,
        "duration_ms": int(time.time() * 1000) - start_ms,
    })
    print(f"  [Approval] Decision: {decision_text[:120]}...")

    duration_ms = int(time.time() * 1000) - start_ms
    trace = {
        "trace_id": trace_id,
        "agent_id": APPROVAL_ID,
        "agent_handle": "@rental:approval",
        "parent_trace_id": parent_trace_id,
        "inputs": {"pricing": pricing_result, "from_agent": PRICING_ID},
        "steps": steps,
        "output": {"decision": decision_text, "approved": approved},
        "outcome": "success" if approved else "failure",
        "timestamp_ms": start_ms,
        "duration_ms": duration_ms,
    }
    return trace, decision_text


# ── Main ────────────────────────────────────────────────────────────

def main():
    global index_proc

    banner("NANDA Context Graph -- Real Multi-Agent Demo")
    print("  Scenario: Car Rental Broker with 3 Claude-powered agents")
    print("  Each agent reasons with Claude and traces to NCG")

    # ── Verify services ────────────────────────────────────────────
    print("\n  Checking services...")
    for name, url in [("NCG Ingest", f"{NCG_INGEST}/health"),
                      ("NCG Query", f"{NCG_QUERY}/health")]:
        try:
            r = requests.get(url, timeout=3)
            print(f"    {name}: OK")
        except:
            print(f"    {name}: NOT RUNNING")
            print(f"\n  Start NCG first: docker-compose up -d")
            sys.exit(1)

    # Start nanda-index if not running (optional — not needed for remote deploys)
    skip_index = os.getenv("SKIP_INDEX", "")
    if not skip_index:
        try:
            requests.get(f"{INDEX_URL}/health", timeout=2)
            print(f"    nanda-index: OK (already running)")
        except:
            print(f"    nanda-index: starting...", end="", flush=True)
            index_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "nanda-index"))
            if os.path.exists(os.path.join(index_dir, "registry.py")):
                index_env = os.environ.copy()
                index_env["TEST_MODE"] = "1"
                index_env["PORT"] = "6900"
                index_proc = subprocess.Popen(
                    [sys.executable, "registry.py"], cwd=index_dir, env=index_env,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                for _ in range(15):
                    try:
                        requests.get(f"{INDEX_URL}/health", timeout=1)
                        break
                    except:
                        time.sleep(1)
                print(" OK")
            else:
                print(" skipped (nanda-index not found locally)")

        # Register agents
        print("\n  Registering agents in nanda-index...")
        for aid, handle in [(BROKER_ID, "@rental:broker"),
                            (PRICING_ID, "@rental:pricing"),
                            (APPROVAL_ID, "@rental:approval")]:
            register_agent(aid, handle)
            print(f"    Registered: {handle} ({aid})")
    else:
        print("    nanda-index: skipped (SKIP_INDEX set)")

    # ── Run the scenario ───────────────────────────────────────────
    user_request = (
        "I need to rent a car in Boston for 5 days next week. "
        "There will be 4 of us with luggage for a ski trip to Vermont. "
        "I'm a Gold loyalty member. What do you recommend?"
    )

    banner("User Request")
    print(f"  \"{user_request}\"")

    # Agent 1: Broker
    banner("Agent 1: @rental:broker -- Car Recommendation")
    broker_trace, recommendation = broker_agent(user_request)
    emit_trace(broker_trace)
    time.sleep(2)  # let Neo4j write the parent before child references it
    print(f"\n  Trace emitted: {broker_trace['trace_id'][:16]}...")
    print(f"  Steps: {len(broker_trace['steps'])}")
    print(f"  Outcome: {broker_trace['outcome']}")

    # Agent 2: Pricing
    banner("Agent 2: @rental:pricing -- Price Calculation")
    pricing_trace, pricing_result = pricing_agent(recommendation, broker_trace["trace_id"])
    emit_trace(pricing_trace)
    time.sleep(2)
    print(f"\n  Trace emitted: {pricing_trace['trace_id'][:16]}... (parent: {broker_trace['trace_id'][:16]}...)")
    print(f"  Steps: {len(pricing_trace['steps'])}")
    print(f"  Final price: ${pricing_result['final_price']:.0f} ({pricing_result['discount_pct']}% off)")

    # Agent 3: Approval
    banner("Agent 3: @rental:approval -- Deal Approval")
    approval_trace, decision = approval_agent(pricing_result, pricing_trace["trace_id"])
    emit_trace(approval_trace)
    time.sleep(2)
    print(f"\n  Trace emitted: {approval_trace['trace_id'][:16]}... (parent: {pricing_trace['trace_id'][:16]}...)")
    print(f"  Steps: {len(approval_trace['steps'])}")
    print(f"  Outcome: {approval_trace['outcome']}")

    # ── Verify everything in NCG ───────────────────────────────────
    banner("Verifying Traces in NCG")
    time.sleep(2)

    for aid, name in [(BROKER_ID, "Broker"), (PRICING_ID, "Pricing"), (APPROVAL_ID, "Approval")]:
        r = requests.get(f"{NCG_QUERY}/api/v1/agent/{aid}/history")
        if r.status_code == 200:
            traces = r.json().get("traces", [])
            print(f"  {name:10s} ({aid}): {len(traces)} trace(s) in Neo4j")
        else:
            print(f"  {name:10s} ({aid}): ERROR {r.status_code}")

    # Causal chain
    print()
    r = requests.get(f"{NCG_QUERY}/api/v1/chain/{approval_trace['trace_id']}/causal")
    if r.status_code == 200:
        chain = r.json().get("chain", [])
        print(f"  Causal chain from Approval -> root: {len(chain)} hops")
        for i, tid in enumerate(chain):
            label = {
                approval_trace["trace_id"]: "Approval",
                pricing_trace["trace_id"]: "Pricing",
                broker_trace["trace_id"]: "Broker",
            }.get(tid, "Unknown")
            print(f"    {i+1}. {label}: {tid[:20]}...")

    # ── Final summary ──────────────────────────────────────────────
    banner("Demo Complete!")
    print(f"""
  Three agents collaborated to handle a car rental request:

    User: "{user_request[:60]}..."

    1. @rental:broker    -> Analyzed needs, recommended a car    [{broker_trace['outcome']}]
    2. @rental:pricing   -> Calculated price with loyalty discount [{pricing_trace['outcome']}]
    3. @rental:approval  -> Reviewed the deal: {'APPROVED' if approval_trace['outcome'] == 'success' else 'REJECTED'}            [{approval_trace['outcome']}]

  Every decision is traced with full reasoning in Neo4j.
  Causal chain links all three traces together.

  --- How to explore ---

  Dashboard:
    cd dashboard && npm run dev
    Open http://localhost:5173
    Search for: {BROKER_ID}  or  {PRICING_ID}  or  {APPROVAL_ID}

  Neo4j Browser:
    Open http://localhost:7474
    MATCH (a:Agent)-[:MADE_BY]-(d:Decision)-[:DECIDED_BECAUSE]->(s:Step) RETURN a,d,s
    MATCH p=(d:Decision)-[:PRECEDED_BY*]->(root) RETURN p

  API:
    curl http://localhost:7201/api/v1/why?agent_id={BROKER_ID}
    curl http://localhost:7201/api/v1/chain/{approval_trace['trace_id']}/causal
    curl http://localhost:7201/api/v1/trace/{broker_trace['trace_id']}
""")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Interrupted.")
    finally:
        cleanup()
