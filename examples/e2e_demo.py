#!/usr/bin/env python3
"""
NANDA Context Graph -- End-to-End Demo

Demonstrates the full pipeline without requiring an Anthropic API key.
Only requires: docker-compose up -d (NCG stack on ports 7200/7201)

What it does:
  1. Verifies NCG stack is healthy
  2. Starts nanda-index in TEST_MODE on port 6900
  3. Registers an agent with trace metadata (simulates adapter)
  4. Emits a multi-step DecisionTrace (simulates traced_call)
  5. Emits a delegated trace with parent_trace_id (causal chain)
  6. Queries all API endpoints and verifies results
  7. Prints pass/fail summary

Usage:
  docker-compose up -d
  python examples/e2e_demo.py
"""

import json
import os
import subprocess
import sys
import time
import uuid

import requests

# ── Config ──────────────────────────────────────────────────────────
NCG_INGEST = "http://localhost:7200"
NCG_QUERY  = "http://localhost:7201"
INDEX_URL  = "http://localhost:6900"
AGENT_ID   = f"demo-agent-{uuid.uuid4().hex[:6]}"

passed = 0
failed = 0
index_proc = None


def step(num, label):
    print(f"\n  Step {num}: {label}", end="", flush=True)


def dots():
    print(" ", end="", flush=True)


def ok(detail=""):
    global passed
    passed += 1
    msg = " OK"
    if detail:
        msg += f"  ({detail})"
    print(msg)


def fail(detail=""):
    global failed
    failed += 1
    msg = " FAIL"
    if detail:
        msg += f"  ({detail})"
    print(msg)


def check(condition, detail=""):
    if condition:
        ok(detail)
    else:
        fail(detail)
    return condition


def cleanup():
    if index_proc:
        index_proc.terminate()
        try:
            index_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            index_proc.kill()


# ── Banner ──────────────────────────────────────────────────────────
print("=" * 60)
print("  NANDA Context Graph -- End-to-End Demo")
print("=" * 60)
print(f"  Agent ID:  {AGENT_ID}")
print(f"  NCG:       {NCG_INGEST} / {NCG_QUERY}")

# ── Step 1: Health check ───────────────────────────────────────────
step(1, "Verify NCG stack")
dots()
try:
    r1 = requests.get(f"{NCG_INGEST}/health", timeout=3)
    r2 = requests.get(f"{NCG_QUERY}/health", timeout=3)
    check(r1.status_code == 200 and r2.status_code == 200, "ingest + query healthy")
except Exception as e:
    fail(str(e))
    print("\n  NCG stack is not running. Start it with:")
    print("    docker-compose up -d")
    sys.exit(1)

# ── Step 2: Start nanda-index ──────────────────────────────────────
step(2, "Start nanda-index (TEST_MODE)")
dots()
index_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "nanda-index"))
if not os.path.exists(os.path.join(index_dir, "registry.py")):
    fail(f"nanda-index not found at {index_dir}")
    print("\n  Expected sibling directory layout:")
    print("    NANDA/adapter/")
    print("    NANDA/nanda-index/    <-- missing")
    print("    NANDA/nanda-context-graph/")
    sys.exit(1)

# Check if nanda-index is already running
try:
    r = requests.get(f"{INDEX_URL}/health", timeout=2)
    if r.status_code == 200:
        ok("already running")
        index_proc = None  # don't kill it on cleanup
    else:
        raise Exception("not healthy")
except Exception:
    index_env = os.environ.copy()
    index_env["TEST_MODE"] = "1"
    index_env["PORT"] = "6900"
    index_proc = subprocess.Popen(
        [sys.executable, "registry.py"],
        cwd=index_dir,
        env=index_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Wait for startup
    for _ in range(15):
        try:
            r = requests.get(f"{INDEX_URL}/health", timeout=1)
            if r.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(1)
    else:
        fail("timeout")
        cleanup()
        sys.exit(1)
    ok("started")

try:
    # ── Step 3: Register agent ─────────────────────────────────────
    step(3, "Register agent with trace metadata")
    dots()
    payload = {
        "agent_id": AGENT_ID,
        "agent_url": "http://localhost:6000",
        "api_url": "http://localhost:6001",
        "trace": {
            "endpointURL": NCG_QUERY,
            "privacyMode": "public",
            "schemaVersion": "nanda-context-graph:1.0",
        },
    }
    r = requests.post(f"{INDEX_URL}/register", json=payload)
    check(r.status_code == 200, f"registered as {AGENT_ID}")

    # ── Step 4: Emit multi-step trace ──────────────────────────────
    step(4, "Emit multi-step DecisionTrace")
    dots()
    trace_1 = str(uuid.uuid4())
    ts = int(time.time() * 1000)
    trace_payload = {
        "trace_id": trace_1,
        "agent_id": AGENT_ID,
        "agent_handle": f"@demo:{AGENT_ID}",
        "inputs": {"message": "Propose 20% renewal discount for Acme Corp"},
        "steps": [
            {
                "step_id": f"s-{uuid.uuid4().hex[:6]}",
                "step_type": "retrieve",
                "thought": "Pulling customer ARR from Salesforce",
                "tool_name": "salesforce-mcp",
                "tool_input": {"account": "acme-corp"},
                "tool_output": {"arr": 500000},
                "confidence": 1.0,
            },
            {
                "step_id": f"s-{uuid.uuid4().hex[:6]}",
                "step_type": "retrieve",
                "thought": "Checking PagerDuty for recent incidents",
                "tool_name": "pagerduty-mcp",
                "tool_input": {"account": "acme-corp", "days": 90},
                "tool_output": {"sev1_count": 3},
                "confidence": 1.0,
            },
            {
                "step_id": f"s-{uuid.uuid4().hex[:6]}",
                "step_type": "evaluate",
                "thought": "3 SEV-1s in 90 days qualifies for exception under discount-policy-v3.2",
                "confidence": 0.92,
            },
            {
                "step_id": f"s-{uuid.uuid4().hex[:6]}",
                "step_type": "decide",
                "thought": "Exception route triggered. Delegating to policy evaluator.",
                "confidence": 0.88,
            },
        ],
        "output": {"action": "delegate_to_policy_evaluator", "proposed_discount": "20%"},
        "outcome": "delegated",
        "timestamp_ms": ts,
        "duration_ms": 340,
    }
    r = requests.post(f"{NCG_INGEST}/ingest/trace", json=trace_payload)
    check(r.status_code == 202, f"trace_id={trace_1[:12]}...")

    # Wait for parent trace to commit before writing child (PRECEDED_BY needs parent)
    time.sleep(3)

    # ── Step 5: Emit delegated trace ───────────────────────────────
    step(5, "Emit delegated trace (causal chain)")
    dots()
    trace_2 = str(uuid.uuid4())
    trace_payload_2 = {
        "trace_id": trace_2,
        "agent_id": AGENT_ID,
        "agent_handle": f"@demo:{AGENT_ID}",
        "parent_trace_id": trace_1,
        "inputs": {"delegated_from": trace_1, "action": "evaluate_exception"},
        "steps": [
            {
                "step_id": f"s-{uuid.uuid4().hex[:6]}",
                "step_type": "evaluate",
                "thought": "Validating exception criteria against policy-v3.2",
                "confidence": 0.95,
            },
            {
                "step_id": f"s-{uuid.uuid4().hex[:6]}",
                "step_type": "decide",
                "thought": "Exception approved: 3 SEV-1s exceeds threshold of 2",
                "confidence": 0.97,
            },
        ],
        "output": {"decision": "EXCEPTION_APPROVED", "discount": "20%"},
        "outcome": "success",
        "timestamp_ms": ts + 500,
        "duration_ms": 120,
    }
    r = requests.post(f"{NCG_INGEST}/ingest/trace", json=trace_payload_2)
    check(r.status_code == 202, f"trace_id={trace_2[:12]}... parent={trace_1[:12]}...")

    # Wait for Neo4j background writes
    print("\n  Waiting for Neo4j writes (4s)...", end="", flush=True)
    time.sleep(4)
    print(" done")

    # ── Step 6: Query trace by ID ──────────────────────────────────
    step(6, "Query -- trace by ID")
    dots()
    r = requests.get(f"{NCG_QUERY}/api/v1/trace/{trace_1}")
    if r.status_code == 200:
        data = r.json()
        steps_count = len(data.get("steps", []))
        check(data.get("agent_id") == AGENT_ID and steps_count == 4,
              f"agent_id={data.get('agent_id')}, steps={steps_count}")
    else:
        fail(f"HTTP {r.status_code}")

    # ── Step 7: Query why() ────────────────────────────────────────
    step(7, "Query -- why()")
    dots()
    r = requests.get(f"{NCG_QUERY}/api/v1/why", params={"agent_id": AGENT_ID})
    if r.status_code == 200:
        data = r.json()
        decision = data.get("decision", {})
        check(decision.get("outcome") == "success",
              f"latest outcome={decision.get('outcome')}")
    else:
        fail(f"HTTP {r.status_code}")

    # ── Step 8: Query history ──────────────────────────────────────
    step(8, "Query -- agent history")
    dots()
    r = requests.get(f"{NCG_QUERY}/api/v1/agent/{AGENT_ID}/history")
    if r.status_code == 200:
        traces = r.json().get("traces", [])
        check(len(traces) == 2, f"count={len(traces)}")
    else:
        fail(f"HTTP {r.status_code}")

    # ── Step 9: Query causal chain ─────────────────────────────────
    step(9, "Query -- causal chain")
    dots()
    r = requests.get(f"{NCG_QUERY}/api/v1/chain/{trace_2}/causal")
    if r.status_code == 200:
        chain = r.json().get("chain", [])
        check(len(chain) == 2 and chain[-1] == trace_1,
              f"chain length={len(chain)}, root={chain[-1][:12]}..." if chain else "empty")
    else:
        fail(f"HTTP {r.status_code}")

    # ── Step 10: Federation endpoint ───────────────────────────────
    step(10, "Query -- federation endpoint")
    dots()
    r = requests.get(f"{NCG_QUERY}/federation/traces", params={"since_ms": 0})
    if r.status_code == 200:
        fed = r.json()
        our_traces = [t for t in fed if t.get("agent_id") == AGENT_ID]
        check(len(our_traces) >= 2, f"total={len(fed)}, ours={len(our_traces)}")
    else:
        fail(f"HTTP {r.status_code}")

    # ── Summary ────────────────────────────────────────────────────
    total = passed + failed
    print(f"\n{'='*60}")
    print(f"  RESULTS: {passed}/{total} passed")
    print(f"{'='*60}")

    if failed == 0:
        print(f"""
  All checks passed! The full pipeline works:

    Agent -> traced_call() -> POST /ingest/trace -> Neo4j
                                                      |
    Auditor <- GET /api/v1/why <-- Query API <--------+

  Explore the graph:
    Browser:  http://localhost:7474
    Cypher:   MATCH (a:Agent)-[:MADE_BY]-(d:Decision) RETURN a, d
    Chain:    MATCH p=(d)-[:PRECEDED_BY*]->(root) RETURN p

  Traces for this demo:
    Trace 1 (delegated): {trace_1}
    Trace 2 (approved):  {trace_2}
    Agent: {AGENT_ID}
""")
    else:
        print(f"\n  {failed} check(s) failed. See output above.")

finally:
    cleanup()
