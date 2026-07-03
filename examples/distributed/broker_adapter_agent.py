#!/usr/bin/env python3
"""
Distributed demo -- REAL adapter "broker" agent (its own OS process).

This is NOT a simulation. It boots the actual projnanda/adapter bridge:

    NANDA(broker_improver)
        -> register_custom_improver()      (wraps improver in traced_call)
        -> run_server(bridge, port=6000)   (real python_a2a HTTP A2A server)

When a terminal-style message "@<specialist> <request>" arrives on /a2a, the
real bridge:
    1. runs broker_improver inside traced_call  -> emits DecisionTrace (this agent)
    2. forwards over real HTTP A2A to the specialist, injecting x-parent-trace
       = this agent's trace_id  (so the specialist's trace links back to ours)

Env (set by the orchestrator):
    AGENT_ID, PORT, API_PORT, REGISTRY_URL, NCG_INGEST_URL, ANTHROPIC_API_KEY?
"""

import os
import sys

# ── env MUST be set before importing the adapter (NCG_INGEST_URL + AGENT_ID
#    are read at import / construction time) ───────────────────────────────
AGENT_ID = os.environ.setdefault("AGENT_ID", "dist-broker")
PORT = int(os.environ.setdefault("PORT", "6000"))
API_PORT = int(os.environ.setdefault("API_PORT", "6001"))
REGISTRY_URL = os.environ.setdefault("REGISTRY_URL", "http://localhost:6900")
os.environ.setdefault("NCG_INGEST_URL", "http://localhost:7200")
os.environ["IMPROVE_MESSAGES"] = "true"   # the @ path only traces when improving
os.environ["UI_MODE"] = "false"           # no chat UI client in this demo
os.environ.setdefault("PUBLIC_URL", f"http://localhost:{PORT}")
os.environ.setdefault("API_URL", f"http://localhost:{API_PORT}")

ADAPTER_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "adapter")
)
sys.path.insert(0, ADAPTER_DIR)

# Point the adapter at our LOCAL index. get_registry_url() reads ./registry_url.txt
# from the cwd first, else defaults to the production registry. Writing this file
# is the documented, module-agnostic override seam (both register + lookup use it).
with open(os.path.join(os.path.dirname(__file__), "registry_url.txt"), "w") as _f:
    _f.write(REGISTRY_URL)

import nanda_adapter.core.nanda as nanda_mod          # noqa: E402

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")


def broker_improver(message_text: str) -> str:
    """Broker logic: turn a raw user request into a crisp delegation brief.

    Uses Claude when ANTHROPIC_API_KEY is present; otherwise a deterministic
    transform. Either way this runs inside the adapter's traced_call, so a
    DecisionTrace is emitted for THIS agent with zero changes to the bridge.
    """
    if os.getenv("ANTHROPIC_API_KEY"):
        try:
            from anthropic import Anthropic
            client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
            resp = client.messages.create(
                model=MODEL,
                max_tokens=200,
                system=(
                    "You are a rental broker. Rewrite the user's request into a "
                    "single concise delegation brief for a pricing specialist. "
                    "State the car class, days, and any loyalty tier. One sentence."
                ),
                messages=[{"role": "user", "content": message_text}],
            )
            return resp.content[0].text.strip()
        except Exception as e:
            print(f"[broker] Claude unavailable ({e}); using deterministic brief")
    return f"[BROKER BRIEF] {message_text.strip()}"


def main():
    print(f"[broker] starting REAL adapter bridge  id={AGENT_ID} port={PORT}")
    print(f"[broker] registry={REGISTRY_URL}  ncg={os.getenv('NCG_INGEST_URL')}")
    print(f"[broker] anthropic_key={'present' if os.getenv('ANTHROPIC_API_KEY') else 'absent (deterministic)'}")

    nanda = nanda_mod.NANDA(broker_improver)
    # start_server() registers with the (now-local) registry, then run_server()
    # blocks serving real A2A on PORT.
    nanda.start_server()


if __name__ == "__main__":
    main()
