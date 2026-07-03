#!/usr/bin/env python3
"""
Distributed demo -- generic REAL NEST agent (its own OS process).

Role-based, so one file serves every NEST node in the chain:

    ROLE=pricing   -> quotes a price, then (if NEXT_AGENT_ID is set) makes a REAL
                      A2A sub-call to the next agent for sign-off, propagating
                      x-parent-trace = its own trace_id so the chain links.
    ROLE=approval  -> approves/rejects against policy. Terminal node.

Boots the actual projnanda/NEST bridge:

    SimpleAgentBridge(agent_id, RoleAgent(), registry_url=<local index>)
        -> run_server(bridge, port=PORT)     (real python_a2a HTTP A2A server)

The bridge's trace_collector reads x-parent-trace from the incoming message and
emits a DecisionTrace whose parent_trace_id is the caller's trace -- a real
cross-process causal chain, with zero changes to this agent's business logic.

Env (set by the orchestrator):
    AGENT_ID, PORT, ROLE, REGISTRY_URL, NCG_INGEST_URL, ANTHROPIC_API_KEY?,
    NEXT_AGENT_ID? (only meaningful for ROLE=pricing)
"""

import os
import sys

AGENT_ID = os.environ.setdefault("AGENT_ID", "dist-nest")
PORT = int(os.environ.setdefault("PORT", "6100"))
ROLE = os.environ.setdefault("ROLE", "pricing")
REGISTRY_URL = os.environ.setdefault("REGISTRY_URL", "http://localhost:6900")
NEXT_AGENT_ID = os.getenv("NEXT_AGENT_ID")  # optional onward hop
os.environ.setdefault("NCG_INGEST_URL", "http://localhost:7200")

NEST_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "NEST")
)
sys.path.insert(0, NEST_DIR)

import requests                                              # noqa: E402
from python_a2a import (                                     # noqa: E402
    run_server, A2AClient, Message, TextContent, MessageRole, Metadata,
)
from nanda_core.core.agent_bridge import SimpleAgentBridge   # noqa: E402
from nanda_core.interface import AgentInterface              # noqa: E402
from nanda_core.telemetry.trace_collector import trace_collector  # noqa: E402

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

ROLE_PROMPTS = {
    "pricing": (
        "You are a car-rental pricing specialist. Given a broker's brief, quote a "
        "final price. Base rate $85/day. Gold tier = 15% off. Show the math in one "
        "or two sentences and end with 'Final price: $<amount>'."
    ),
    "approval": (
        "You are a car-rental deal approver. Policy: auto-approve only if the "
        "discount is 10% or less; anything above must be REJECTED. Read the quote, "
        "then reply with exactly 'APPROVED' or 'REJECTED' followed by a one-line "
        "reason."
    ),
}


def _unwrap(text: str) -> str:
    """Strip the adapter's __EXTERNAL_MESSAGE__ envelope if present."""
    if "__MESSAGE_START__" in text and "__MESSAGE_END__" in text:
        inner = text.split("__MESSAGE_START__", 1)[1].split("__MESSAGE_END__", 1)[0]
        return inner.strip()
    return text.strip()


def _reason(brief: str) -> str:
    """Role-specific reasoning. Claude when keyed, deterministic otherwise."""
    if os.getenv("ANTHROPIC_API_KEY"):
        try:
            from anthropic import Anthropic
            client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
            resp = client.messages.create(
                model=MODEL, max_tokens=220,
                system=ROLE_PROMPTS.get(ROLE, ROLE_PROMPTS["pricing"]),
                messages=[{"role": "user", "content": brief}],
            )
            return resp.content[0].text.strip()
        except Exception as e:
            print(f"[{AGENT_ID}] Claude unavailable ({e}); deterministic")
    if ROLE == "approval":
        return "REJECTED: 15% discount exceeds the 10% auto-approve policy."
    return f"5 days x $85 = $425, Gold 15% off. Final price: $361.25 (brief: {brief[:40]})"


def _delegate(next_id: str, text: str, conversation_id: str) -> str:
    """Make a REAL A2A sub-call to next_id, propagating our trace as its parent."""
    try:
        r = requests.get(f"{REGISTRY_URL}/lookup/{next_id}", timeout=5)
        url = r.json().get("agent_url") if r.status_code == 200 else None
    except Exception as e:
        return f"(could not locate {next_id}: {e})"
    if not url:
        return f"({next_id} not found in registry)"
    if not url.endswith("/a2a"):
        url += "/a2a"

    # Our active trace_id (opened by before_call for this conversation) becomes
    # the downstream agent's parent -> the causal chain extends one more hop.
    parent = None
    try:
        parent = trace_collector.current_trace_id(conversation_id)
    except Exception:
        pass

    fields = {"from_agent_id": AGENT_ID, "to_agent_id": next_id}
    if parent:
        fields["x-parent-trace"] = parent
        fields["x-reason"] = "a2a-delegation"

    print(f"[{AGENT_ID}] -> {next_id} (parent={parent}): {text[:50]}...")
    client = A2AClient(url, timeout=60)
    resp = client.send_message(Message(
        role=MessageRole.USER,
        content=TextContent(text=text),
        conversation_id=conversation_id,
        metadata=Metadata(custom_fields=fields),
    ))
    if resp and hasattr(resp, "content"):
        return resp.content.text
    return str(resp)


class RoleAgent(AgentInterface):
    def process_message(self, message: str, context: dict) -> str:
        brief = _unwrap(message)
        conversation_id = context.get("conversation_id", "")
        result = _reason(brief)

        # Pricing specialist consults the approver as part of its own work.
        if ROLE == "pricing" and NEXT_AGENT_ID:
            verdict = _delegate(NEXT_AGENT_ID, f"Approve this quote:\n{result}",
                                conversation_id)
            return f"{result}\n\nApproval [{NEXT_AGENT_ID}]: {verdict}"
        return result


def register():
    payload = {
        "agent_id": AGENT_ID,
        "agent_url": f"http://localhost:{PORT}",
        "api_url": f"http://localhost:{PORT}",
        "trace": {"endpointURL": os.getenv("NCG_INGEST_URL"),
                  "privacyMode": "public",
                  "schemaVersion": "nanda-context-graph:1.0"},
    }
    try:
        r = requests.post(f"{REGISTRY_URL}/register", json=payload, timeout=5)
        print(f"[{AGENT_ID}] registered in index: {r.status_code}")
    except Exception as e:
        print(f"[{AGENT_ID}] registration failed: {e}")


def main():
    nxt = f" -> {NEXT_AGENT_ID}" if (ROLE == "pricing" and NEXT_AGENT_ID) else ""
    print(f"[{AGENT_ID}] starting REAL NEST bridge  role={ROLE} port={PORT}{nxt}")
    print(f"[{AGENT_ID}] registry={REGISTRY_URL}  ncg={os.getenv('NCG_INGEST_URL')}")
    print(f"[{AGENT_ID}] anthropic_key={'present' if os.getenv('ANTHROPIC_API_KEY') else 'absent (deterministic)'}")
    register()
    bridge = SimpleAgentBridge(agent_id=AGENT_ID, agent=RoleAgent(),
                               registry_url=REGISTRY_URL)
    run_server(bridge, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    main()
