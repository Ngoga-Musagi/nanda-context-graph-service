#!/usr/bin/env python3
"""
NANDA Context Graph -- REAL DISTRIBUTED demo.

Unlike run_demo.py (which exercises the hooks in-process), this stands up the
whole thing as separate OS processes talking over real HTTP A2A:

    nanda-index (TEST_MODE) :6900  ── discovery / @id -> URL ──┐
                                                               │
    broker (REAL adapter :6000) ──A2A──► specialist (REAL NEST :6100) ──A2A──► approver (REAL NEST :6200)
      traced_call -> trace#1               process_message -> trace#2            process_message -> trace#3
      x-parent-trace = #1                  x-parent-trace = #2                   parent_trace_id = #2
            └──────────── all three traces land in NCG :7200 -> Neo4j ◄──────────────┘
                          chain(approver) = [#3, #2, #1]

What it proves end-to-end, over the network:
  1. Three real agents (one adapter, two NEST) run as independent processes.
  2. They discover each other through a real nanda-index.
  3. They delegate down the line over real python_a2a HTTP (the pricing
     specialist consults the approver as part of its own reasoning).
  4. All three auto-emit DecisionTraces (zero changes to their business logic).
  5. The traces form ONE causal chain ACROSS processes AND frameworks
     (adapter -> NEST -> NEST), verified via NCG's causal-chain query.

Requires the NCG stack already up (docker compose up -d / run_demo.py once).

Usage:
  python run_distributed_demo.py
  python run_distributed_demo.py --keep-up   # leave agents running to explore
"""

import argparse
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "examples" / "distributed"
LOGDIR = DIST / ".logs"
PY = sys.executable

INGEST = "http://localhost:7200"
QUERY = "http://localhost:7201"
REGISTRY = "http://localhost:6900"
BROKER_PORT = 6000
BROKER_API_PORT = 6001
SPECIALIST_PORT = 6100
APPROVER_PORT = 6200

TOKEN = uuid.uuid4().hex[:6]
BROKER_ID = f"dist-broker-{TOKEN}"
SPECIALIST_ID = f"dist-specialist-{TOKEN}"
APPROVER_ID = f"dist-approver-{TOKEN}"

procs: list[tuple[str, subprocess.Popen]] = []


def banner(title, ch="="):
    print(f"\n{ch * 72}\n  {title}\n{ch * 72}")


def load_dotenv(path: Path):
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'").strip()
        if k.strip() and v:
            os.environ.setdefault(k.strip(), v)


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def http_up(url: str) -> bool:
    try:
        requests.get(url, timeout=2)
        return True  # any HTTP response (even 404) means the server is listening
    except Exception:
        return False


INDEX_SCRIPT = (ROOT / ".." / "nanda-index" / "registry.py").resolve()


def spawn(name: str, script_path: Path, extra_env: dict) -> subprocess.Popen:
    LOGDIR.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"  # flush child stdout to the log immediately
    env.update(extra_env)
    log = open(LOGDIR / f"{name}.log", "w", encoding="utf-8")
    p = subprocess.Popen(
        [PY, str(script_path)], env=env, stdout=log, stderr=subprocess.STDOUT,
        cwd=str(script_path.parent),
    )
    procs.append((name, p))
    return p


def tail(name: str, n: int = 25):
    f = LOGDIR / f"{name}.log"
    if not f.exists():
        return
    lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
    print(f"\n  --- last {n} lines of {name}.log ---")
    for ln in lines[-n:]:
        print(f"  | {ln}")


def wait_for(label: str, fn, timeout=40) -> bool:
    print(f"  waiting for {label}", end="", flush=True)
    t0 = time.time()
    while time.time() - t0 < timeout:
        if fn():
            print(" -- ready")
            return True
        print(".", end="", flush=True)
        time.sleep(1)
    print(" -- TIMEOUT")
    return False


def registered(agent_id: str) -> bool:
    try:
        r = requests.get(f"{REGISTRY}/lookup/{agent_id}", timeout=2)
        return r.status_code == 200 and r.json().get("agent_url")
    except Exception:
        return False


def history(agent_id: str):
    try:
        r = requests.get(f"{QUERY}/api/v1/agent/{agent_id}/history", timeout=4)
        if r.status_code == 200:
            return r.json().get("traces", [])
    except Exception:
        pass
    return []


def cleanup():
    banner("Cleanup", "-")
    for name, p in procs:
        if p.poll() is None:
            p.terminate()
            print(f"  terminated {name} (pid {p.pid})")
    time.sleep(1)
    for _, p in procs:
        if p.poll() is None:
            p.kill()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-up", action="store_true",
                    help="leave agents running after the demo for manual exploration")
    args = ap.parse_args()

    banner("NANDA Context Graph -- REAL Distributed Demo")
    load_dotenv(ROOT / ".env")
    key = "present" if os.getenv("ANTHROPIC_API_KEY") else "absent (deterministic agents)"
    print(f"  run token: {TOKEN}")
    print(f"  broker (adapter):    {BROKER_ID}   :{BROKER_PORT}")
    print(f"  specialist (NEST):   {SPECIALIST_ID}   :{SPECIALIST_PORT}")
    print(f"  ANTHROPIC_API_KEY:   {key}")

    # 0. NCG stack must be up already
    banner("Pre-flight: NCG stack", "-")
    if not (http_up(f"{INGEST}/health") and http_up(f"{QUERY}/health")):
        print("  NCG stack not healthy on 7200/7201.")
        print("  Start it first:  python run_demo.py --only 4   (or docker compose up -d)")
        sys.exit(1)
    print("  NCG ingest + query healthy.")

    ok = False
    try:
        # 1. discovery: nanda-index in TEST_MODE
        banner("1. Start nanda-index (TEST_MODE, in-memory)", "-")
        spawn("index", INDEX_SCRIPT, {"TEST_MODE": "1", "PORT": "6900"})
        if not wait_for("index :6900", lambda: http_up(f"{REGISTRY}/lookup/__ping__")):
            tail("index"); raise SystemExit(1)

        # 2. real adapter broker process
        banner("2. Start REAL adapter broker process", "-")
        spawn("broker", DIST / "broker_adapter_agent.py", {
            "AGENT_ID": BROKER_ID, "PORT": str(BROKER_PORT),
            "API_PORT": str(BROKER_API_PORT), "REGISTRY_URL": REGISTRY,
            "NCG_INGEST_URL": INGEST,
            "PUBLIC_URL": f"http://localhost:{BROKER_PORT}",
            "API_URL": f"http://localhost:{BROKER_API_PORT}",
        })
        if not wait_for(f"broker registered ({BROKER_ID})", lambda: registered(BROKER_ID)):
            tail("broker"); raise SystemExit(1)
        if not wait_for(f"broker A2A :{BROKER_PORT}", lambda: port_open(BROKER_PORT)):
            tail("broker"); raise SystemExit(1)

        # 3a. real NEST approver process (terminal node, started first so the
        #     specialist can resolve it when it delegates)
        banner("3a. Start REAL NEST approver process", "-")
        spawn("approver", DIST / "nest_agent.py", {
            "AGENT_ID": APPROVER_ID, "PORT": str(APPROVER_PORT), "ROLE": "approval",
            "REGISTRY_URL": REGISTRY, "NCG_INGEST_URL": INGEST,
        })
        if not wait_for(f"approver registered ({APPROVER_ID})", lambda: registered(APPROVER_ID)):
            tail("approver"); raise SystemExit(1)
        if not wait_for(f"approver A2A :{APPROVER_PORT}", lambda: port_open(APPROVER_PORT)):
            tail("approver"); raise SystemExit(1)

        # 3b. real NEST pricing specialist process -> consults the approver
        banner("3b. Start REAL NEST pricing specialist process", "-")
        spawn("specialist", DIST / "nest_agent.py", {
            "AGENT_ID": SPECIALIST_ID, "PORT": str(SPECIALIST_PORT), "ROLE": "pricing",
            "NEXT_AGENT_ID": APPROVER_ID,
            "REGISTRY_URL": REGISTRY, "NCG_INGEST_URL": INGEST,
        })
        if not wait_for(f"specialist registered ({SPECIALIST_ID})", lambda: registered(SPECIALIST_ID)):
            tail("specialist"); raise SystemExit(1)
        if not wait_for(f"specialist A2A :{SPECIALIST_PORT}", lambda: port_open(SPECIALIST_PORT)):
            tail("specialist"); raise SystemExit(1)

        # 4. fire a REAL A2A message at the broker, telling it to delegate
        banner("4. Send a REAL A2A request to the broker", "-")
        sys.path.insert(0, os.path.abspath(
            os.path.join(str(ROOT), "..", "adapter")))
        from python_a2a import A2AClient, Message, TextContent, MessageRole

        user_request = ("I need a midsize AWD car in Boston for 5 days for a ski "
                        "trip, 4 people with luggage. I'm a Gold loyalty member.")
        delegation = f"@{SPECIALIST_ID} {user_request}"
        print(f"  user -> broker:  {delegation[:80]}...")

        client = A2AClient(f"http://localhost:{BROKER_PORT}/a2a", timeout=60)
        last_err = None
        for attempt in range(5):
            try:
                resp = client.send_message(Message(
                    role=MessageRole.USER,
                    content=TextContent(text=delegation),
                    conversation_id=f"dist-{TOKEN}",
                ))
                txt = resp.content.text if resp and hasattr(resp, "content") else str(resp)
                print(f"  broker ack:      {txt[:80]}")
                break
            except Exception as e:
                last_err = e
                time.sleep(2)
        else:
            print(f"  failed to reach broker A2A: {last_err}")
            tail("broker"); raise SystemExit(1)

        # 5. verify all three traces landed and form one cross-process chain
        banner("5. Verify the 3-hop cross-process causal chain in NCG", "-")

        def all_traced():
            return (history(BROKER_ID) and history(SPECIALIST_ID)
                    and history(APPROVER_ID))

        if not wait_for("all three traces in NCG", all_traced, timeout=60):
            tail("broker"); tail("specialist"); tail("approver")
            raise SystemExit(1)

        broker_tid = history(BROKER_ID)[0]["trace_id"]
        spec_tid = history(SPECIALIST_ID)[0]["trace_id"]
        appr_tid = history(APPROVER_ID)[0]["trace_id"]
        print(f"  broker trace      ({BROKER_ID}):     {broker_tid}")
        print(f"  specialist trace  ({SPECIALIST_ID}): {spec_tid}")
        print(f"  approver trace    ({APPROVER_ID}):   {appr_tid}")

        # walk the chain from the deepest node (approver) back to the root
        chain = requests.get(f"{QUERY}/api/v1/chain/{appr_tid}/causal", timeout=5)
        chain_json = chain.json() if chain.status_code == 200 else {}
        chain_ids = chain_json.get("chain", []) if isinstance(chain_json, dict) else []
        chain_text = str(chain_json)

        print(f"\n  causal chain from approver -> root:")
        print(f"    {chain_text[:320]}")

        results = {
            "broker (adapter) emitted a trace": bool(history(BROKER_ID)),
            "specialist (NEST) emitted a trace": bool(history(SPECIALIST_ID)),
            "approver (NEST) emitted a trace": bool(history(APPROVER_ID)),
            "approver links to specialist (hop 2)": spec_tid in chain_text,
            "chain reaches broker root (hop 1, 3 nodes total)":
                broker_tid in chain_text and len(chain_ids) >= 3,
        }
        print()
        for label, passed in results.items():
            print(f"  [{'OK  ' if passed else 'FAIL'}] {label}")
        ok = all(results.values())

        banner("Distributed Demo " + ("PASSED" if ok else "FAILED"))
        if ok:
            print(f"""  Three real agents, three processes, two frameworks, one causal chain.

    broker (adapter :{BROKER_PORT}) --A2A--> specialist (NEST :{SPECIALIST_PORT}) --A2A--> approver (NEST :{APPROVER_PORT})
        {broker_tid[:8]}  <--PRECEDED_BY--  {spec_tid[:8]}  <--PRECEDED_BY--  {appr_tid[:8]}

  Explore:
    Dashboard:   http://localhost:8080   (search: {BROKER_ID} / {SPECIALIST_ID} / {APPROVER_ID})
    Causal:      {QUERY}/api/v1/chain/{appr_tid}/causal
    Why:         {QUERY}/api/v1/why?agent_id={APPROVER_ID}
""")
        if args.keep_up and ok:
            print("  --keep-up: agents are still running. Press Ctrl+C to stop.")
            while True:
                time.sleep(1)
    finally:
        if not (args.keep_up and ok):
            cleanup()

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
