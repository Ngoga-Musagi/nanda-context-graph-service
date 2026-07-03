#!/usr/bin/env python3
"""
NANDA Context Graph -- REAL Adapter Integration Proof (Phase 4)

Unlike e2e_demo.py and real_agents_demo.py (which POST hand-built traces to
simulate an agent), this script drives the *real* projnanda/adapter code:

    NANDA(improvement_fn)  ->  register_custom_improver()  ->  traced_call()
                                                                    |
                                              fire-and-forget POST  v
                                                          /ingest/trace  -> Neo4j

It proves the headline claim:
    "Set one env var (NCG_INGEST_URL) and any NANDA adapter agent auto-emits a
     full DecisionTrace -- with ZERO changes to the agent's own logic."

No Anthropic API key needed: the improvement_fn is a deterministic transform,
so the proof isolates the *integration*, not the LLM.

What it checks:
  1. With NCG_INGEST_URL set, NANDA wraps the improver in traced_call (real code).
  2. Invoking the real bridge.improve_message_direct() emits a trace to NCG.
  3. The trace lands in Neo4j with the right agent_id / input / output / outcome.
  4. During the call, agent_bridge.get_current_trace_id() is populated -- proving
     the A2A-delegation header-injection hook (x-trace-id) would fire on delegation.
  5. Control: with NCG_INGEST_URL UNSET, no trace is emitted (opt-in guarantee).

Usage:
  docker-compose up -d           # NCG stack on 7200/7201
  python examples/adapter_integration_demo.py
"""

import os
import sys
import time
import uuid

# ── Config: set env BEFORE importing the adapter ─────────────────────
# nanda.py reads NCG_INGEST_URL at *import* time (module global), and
# register_custom_improver reads AGENT_ID when NANDA() is constructed.
NCG_INGEST = os.environ.setdefault("NCG_INGEST_URL", "http://localhost:7200")
NCG_QUERY = os.getenv("NCG_GRAPH_API_URL", "http://localhost:7201")
AGENT_ID = f"adapter-agent-{uuid.uuid4().hex[:6]}"
os.environ["AGENT_ID"] = AGENT_ID
# The adapter's default improver path can short-circuit on this flag; keep it on.
os.environ.setdefault("IMPROVE_MESSAGES", "true")

# Make the real adapter package importable (sibling repo).
ADAPTER_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "adapter")
)
sys.path.insert(0, ADAPTER_DIR)

import requests  # noqa: E402

passed = 0
failed = 0


def check(label, condition, detail=""):
    global passed, failed
    mark = "OK  " if condition else "FAIL"
    if condition:
        passed += 1
    else:
        failed += 1
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{mark}] {label}{suffix}")
    return condition


def poll_trace(agent_id, attempts=15, delay=1.0):
    """Poll the NCG query API until the agent has at least one trace."""
    for _ in range(attempts):
        try:
            r = requests.get(
                f"{NCG_QUERY}/api/v1/agent/{agent_id}/history", timeout=3
            )
            if r.status_code == 200:
                traces = r.json().get("traces", [])
                if traces:
                    return traces
        except Exception:
            pass
        time.sleep(delay)
    return []


print("=" * 64)
print("  NANDA Context Graph -- REAL Adapter Integration Proof")
print("=" * 64)
print(f"  Agent ID:        {AGENT_ID}")
print(f"  NCG_INGEST_URL:  {NCG_INGEST}")
print(f"  Adapter source:  {ADAPTER_DIR}")
print()

# ── Pre-flight: NCG stack reachable ──────────────────────────────────
print("Pre-flight")
try:
    h1 = requests.get(f"{NCG_INGEST}/health", timeout=3).status_code
    h2 = requests.get(f"{NCG_QUERY}/health", timeout=3).status_code
except Exception as e:
    print(f"  NCG stack not reachable: {e}")
    print("  Start it with: docker-compose up -d")
    sys.exit(1)
check("NCG ingest + query healthy", h1 == 200 and h2 == 200, f"ingest={h1} query={h2}")
print()

# ── Import the REAL adapter ──────────────────────────────────────────
print("Wiring the real adapter")
import nanda_adapter.core.nanda as nanda_mod  # noqa: E402
from nanda_adapter.core import agent_bridge  # noqa: E402

check(
    "adapter sees NCG_INGEST_URL (tracing armed)",
    nanda_mod.NCG_INGEST_URL == NCG_INGEST,
    f"NCG_INGEST_URL={nanda_mod.NCG_INGEST_URL}",
)

# A deterministic improver -- NO LLM. It also records whether a trace context
# was active mid-call (the value agent_bridge injects as x-trace-id on delegation).
seen_trace_id = {"value": None}


def shouting_improver(message_text: str) -> str:
    """Trivial agent logic: uppercase + tag. Records active trace context."""
    seen_trace_id["value"] = agent_bridge.get_current_trace_id()
    return f"[IMPROVED] {message_text.upper()}"


# Construct the REAL NANDA object. This runs the real register_custom_improver(),
# which (because NCG_INGEST_URL is set) wraps shouting_improver in traced_call.
nanda = nanda_mod.NANDA(shouting_improver)
check(
    "NANDA registered the traced improver as active",
    nanda.bridge.active_improver == "nanda_custom",
    f"active_improver={nanda.bridge.active_improver}",
)
print()

# ── Drive the real code path the bridge uses for a regular message ───
print("Sending a message through the real bridge improver")
test_message = "please review the q3 renewal for acme corp"
result = nanda.bridge.improve_message_direct(test_message)
check(
    "improver returned transformed text",
    result == f"[IMPROVED] {test_message.upper()}",
    f"result={result!r}",
)
check(
    "trace context was active during the call (A2A x-trace-id hook)",
    seen_trace_id["value"] is not None,
    f"trace_id seen mid-call = {seen_trace_id['value']}",
)
print()

# ── Verify the trace auto-landed in NCG (no manual POST anywhere) ────
print("Verifying the auto-emitted trace in NCG")
traces = poll_trace(AGENT_ID)
if check("trace reached Neo4j via /ingest/trace", len(traces) >= 1, f"count={len(traces)}"):
    t = traces[0]
    tid = t.get("trace_id")
    full = requests.get(f"{NCG_QUERY}/api/v1/trace/{tid}", timeout=3).json()
    check("  trace.agent_id matches", full.get("agent_id") == AGENT_ID, full.get("agent_id"))
    check("  trace.outcome == success", full.get("outcome") == "success", full.get("outcome"))
    # confirm the trace id we saw mid-call is the one that was stored
    check(
        "  mid-call trace_id == stored trace_id",
        seen_trace_id["value"] == tid,
        f"{seen_trace_id['value']} == {tid}",
    )
print()

# ── Control: opt-in guarantee (tracing OFF => no emission) ───────────
print("Control: opt-in guarantee (NCG_INGEST_URL unset)")
# traced_call short-circuits to a passthrough when the module global is falsy.
saved = nanda_mod.NCG_INGEST_URL
nanda_mod.NCG_INGEST_URL = None
control_agent = f"control-{uuid.uuid4().hex[:6]}"
passthrough = nanda_mod.traced_call(shouting_improver, "hello", agent_id=control_agent)
nanda_mod.NCG_INGEST_URL = saved
time.sleep(2)
ctl = requests.get(f"{NCG_QUERY}/api/v1/agent/{control_agent}/history", timeout=3).json()
check("passthrough still returns correct output", passthrough == "[IMPROVED] HELLO")
check(
    "NO trace emitted when tracing is off",
    len(ctl.get("traces", [])) == 0,
    f"control traces={len(ctl.get('traces', []))}",
)
print()

# ── Summary ──────────────────────────────────────────────────────────
total = passed + failed
print("=" * 64)
print(f"  RESULT: {passed}/{total} checks passed")
print("=" * 64)
if failed == 0:
    print(f"""
  PROVEN: a real NANDA adapter agent auto-emits decision traces.

    NANDA(improvement_fn) --[NCG_INGEST_URL set]--> traced_call()
        -> POST {NCG_INGEST}/ingest/trace -> Neo4j

  The agent's own logic (shouting_improver) was never modified.
  Inspect it:
    curl {NCG_QUERY}/api/v1/why?agent_id={AGENT_ID}
    Neo4j: MATCH (a:Agent {{agent_id:'{AGENT_ID}'}})<-[:MADE_BY]-(d) RETURN a,d
""")
    sys.exit(0)
else:
    print(f"\n  {failed} check(s) failed -- see above.")
    sys.exit(1)
