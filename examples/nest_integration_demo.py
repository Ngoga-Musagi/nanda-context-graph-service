#!/usr/bin/env python3
"""
NANDA Context Graph -- REAL NEST Integration Proof (Phase 5)

Drives the *real* projnanda/NEST code path:

    SimpleAgentBridge.handle_message(a2a_msg)
        -> trace_collector.before_call(agent_id, conversation_id, ...)
        -> agent.process_message(...)                 # the agent's own logic
        -> trace_collector.after_call(conversation_id, response, outcome)
                -> fire-and-forget POST /ingest/trace -> Neo4j

It proves NEST's value-add over the adapter: the trace is correlated to NEST's
existing A2A `conversation_id` (stored as DecisionTrace.a2a_msg_id), and the
incoming `x-parent-trace` metadata is captured for causal chaining.

No Anthropic API key needed -- the agent is a deterministic AgentInterface.

What it checks:
  1. With NCG_INGEST_URL set, trace_collector is armed.
  2. A real handle_message() of a regular A2A message emits a trace to NCG.
  3. The trace lands in Neo4j with the right agent_id and outcome=success.
  4. The emitted payload correlates a2a_msg_id == the A2A conversation_id, and
     carries parent_trace_id from the incoming x-parent-trace metadata.
  5. Control: with tracing OFF, no trace is emitted (opt-in guarantee).

Usage:
  docker-compose up -d
  python examples/nest_integration_demo.py
"""

import os
import sys
import time
import uuid

# ── set env BEFORE importing NEST (trace_collector reads NCG_INGEST_URL at import) ──
NCG_INGEST = os.environ.setdefault("NCG_INGEST_URL", "http://localhost:7200")
NCG_QUERY = os.getenv("NCG_GRAPH_API_URL", "http://localhost:7201")

NEST_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "NEST"))
sys.path.insert(0, NEST_DIR)

import requests  # noqa: E402

passed = 0
failed = 0


def check(label, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        mark = "OK  "
    else:
        failed += 1
        mark = "FAIL"
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{mark}] {label}{suffix}")
    return condition


def poll_trace(agent_id, attempts=15, delay=1.0):
    for _ in range(attempts):
        try:
            r = requests.get(f"{NCG_QUERY}/api/v1/agent/{agent_id}/history", timeout=3)
            if r.status_code == 200 and r.json().get("traces"):
                return r.json()["traces"]
        except Exception:
            pass
        time.sleep(delay)
    return []


AGENT_ID = f"nest-agent-{uuid.uuid4().hex[:6]}"

print("=" * 64)
print("  NANDA Context Graph -- REAL NEST Integration Proof")
print("=" * 64)
print(f"  Agent ID:        {AGENT_ID}")
print(f"  NCG_INGEST_URL:  {NCG_INGEST}")
print(f"  NEST source:     {NEST_DIR}")
print()

print("Pre-flight")
try:
    h1 = requests.get(f"{NCG_INGEST}/health", timeout=3).status_code
    h2 = requests.get(f"{NCG_QUERY}/health", timeout=3).status_code
except Exception as e:
    print(f"  NCG stack not reachable: {e}\n  Start it with: docker-compose up -d")
    sys.exit(1)
check("NCG ingest + query healthy", h1 == 200 and h2 == 200, f"ingest={h1} query={h2}")
print()

# ── Import the REAL NEST bridge + trace collector ────────────────────
print("Wiring the real NEST bridge")
from python_a2a import Message, TextContent, MessageRole, Metadata  # noqa: E402
from nanda_core.core.agent_bridge import SimpleAgentBridge  # noqa: E402
from nanda_core.telemetry import trace_collector  # noqa: E402
from nanda_core.interface import AgentInterface  # noqa: E402

check("NEST trace_collector is armed", trace_collector._active is True,
      f"_active={trace_collector._active}")

# Capture what NEST actually emits (the trace payload) so we can assert on
# a2a_msg_id / parent_trace_id, which the Neo4j store does not persist.
emitted = []
_orig_emit = trace_collector._emit
trace_collector._emit = lambda trace: (emitted.append(trace), _orig_emit(trace))


class EchoAgent(AgentInterface):
    """Minimal deterministic agent -- no LLM."""

    def process_message(self, message: str, context: dict) -> str:
        return f"ECHO[{context.get('conversation_id', '?')[:8]}]: {message}"


bridge = SimpleAgentBridge(agent_id=AGENT_ID, agent=EchoAgent())
check("SimpleAgentBridge constructed", bridge.agent_id == AGENT_ID, f"agent_id={bridge.agent_id}")
print()

# ── Drive a real A2A message through handle_message ──────────────────
print("Handling a real A2A message")
conversation_id = f"conv-{uuid.uuid4().hex[:10]}"
parent_trace = f"parent-{uuid.uuid4().hex[:8]}"
incoming = Message(
    role=MessageRole.USER,
    content=TextContent(text="evaluate the renewal discount for acme corp"),
    conversation_id=conversation_id,
    metadata=Metadata(custom_fields={"x-parent-trace": parent_trace}),
)
response = bridge.handle_message(incoming)
check(
    "agent produced a response",
    "ECHO[" in response.content.text,
    f"response={response.content.text!r}",
)
print()

# ── Verify the auto-emitted trace ────────────────────────────────────
print("Verifying the auto-emitted trace in NCG")
traces = poll_trace(AGENT_ID)
if check("trace reached Neo4j via /ingest/trace", len(traces) >= 1, f"count={len(traces)}"):
    tid = traces[0]["trace_id"]
    full = requests.get(f"{NCG_QUERY}/api/v1/trace/{tid}", timeout=3).json()
    check("  trace.agent_id matches", full.get("agent_id") == AGENT_ID, full.get("agent_id"))
    check("  trace.outcome == success", full.get("outcome") == "success", full.get("outcome"))

# Assert on the emitted payload for the NEST-specific correlation fields
if check("NEST emitted a trace payload", len(emitted) >= 1, f"emitted={len(emitted)}"):
    payload = emitted[0]
    check("  a2a_msg_id == conversation_id (NEST correlation)",
          payload.get("a2a_msg_id") == conversation_id,
          f"{payload.get('a2a_msg_id')} == {conversation_id}")
    check("  parent_trace_id captured from x-parent-trace",
          payload.get("parent_trace_id") == parent_trace,
          f"{payload.get('parent_trace_id')} == {parent_trace}")
    check("  trace_collector._pending drained after call",
          conversation_id not in trace_collector._pending,
          f"pending_keys={list(trace_collector._pending.keys())}")
print()

# ── Control: opt-in guarantee ────────────────────────────────────────
print("Control: opt-in guarantee (tracing off)")
trace_collector._active = False
emitted.clear()
ctl_conv = f"conv-{uuid.uuid4().hex[:10]}"
ctl_msg = Message(
    role=MessageRole.USER,
    content=TextContent(text="this should NOT be traced"),
    conversation_id=ctl_conv,
)
bridge.handle_message(ctl_msg)
time.sleep(1)
check("NO trace emitted when tracing is off", len(emitted) == 0, f"emitted={len(emitted)}")
trace_collector._active = True  # restore
print()

total = passed + failed
print("=" * 64)
print(f"  RESULT: {passed}/{total} checks passed")
print("=" * 64)
if failed == 0:
    print(f"""
  PROVEN: a real NEST SimpleAgentBridge auto-emits decision traces,
  correlated to its existing A2A conversation_id, with causal-chain
  parent linkage -- and the agent's logic (EchoAgent) was never modified.

    curl {NCG_QUERY}/api/v1/why?agent_id={AGENT_ID}
""")
    sys.exit(0)
else:
    print(f"\n  {failed} check(s) failed -- see above.")
    sys.exit(1)
