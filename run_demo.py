#!/usr/bin/env python3
"""
NANDA Context Graph -- one-command demo runner.

Runs the full story in the order that makes the strongest case:

  1. REAL adapter integration   (examples/adapter_integration_demo.py)  -- no LLM
  2. REAL NEST integration       (examples/nest_integration_demo.py)     -- no LLM
  3. Multi-agent Claude scenario (examples/real_agents_demo.py)          -- needs key
  -> then point at the live dashboard + Neo4j for exploration.

It handles the two things that previously caused friction:
  - loads ANTHROPIC_API_KEY (and NCG_* vars) from .env automatically
  - ensures the docker stack is up and healthy before running anything

Usage:
  python run_demo.py                # run everything end-to-end
  python run_demo.py --pause        # wait for <Enter> between phases (live demo)
  python run_demo.py --skip-stack   # assume the stack is already running
  python run_demo.py --only 4,5     # run only specific phases (e.g. the no-LLM proofs)
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
EXAMPLES = ROOT / "examples"
INGEST = "http://localhost:7200"
QUERY = "http://localhost:7201"
DASHBOARD = "http://localhost:8080"
NEO4J_BROWSER = "http://localhost:7474"

# phase id -> (title, script, needs_key)
# Scripts are resolved relative to examples/; "../x.py" points at the repo root.
PHASES = {
    "4": ("REAL Adapter Integration Proof  (no API key)", "adapter_integration_demo.py", False),
    "5": ("REAL NEST Integration Proof  (no API key)", "nest_integration_demo.py", False),
    "3": ("Multi-Agent Claude Scenario  (needs API key)", "real_agents_demo.py", True),
    "6": ("REAL Distributed Demo — 3 processes, real A2A, 3-hop chain", "../run_distributed_demo.py", False),
}
DEFAULT_ORDER = ["4", "5", "3", "6"]


def banner(title, ch="="):
    line = ch * 70
    print(f"\n{line}\n  {title}\n{line}")


def load_dotenv(path: Path) -> dict:
    """Minimal .env loader. Returns the keys it set."""
    loaded = {}
    if not path.exists():
        return loaded
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'").strip()
        if k and v:
            os.environ.setdefault(k, v)  # don't clobber an explicit shell value
            loaded[k] = v
    return loaded


def healthy() -> bool:
    try:
        return (
            requests.get(f"{INGEST}/health", timeout=2).status_code == 200
            and requests.get(f"{QUERY}/health", timeout=2).status_code == 200
        )
    except Exception:
        return False


def ensure_stack(skip: bool) -> bool:
    banner("Pre-flight: NCG stack", "-")
    if healthy():
        print("  Stack already healthy (ingest 7200 + query 7201).")
        return True
    if skip:
        print("  Stack not healthy and --skip-stack set. Aborting.")
        return False
    print("  Stack not up -- running `docker compose up -d` ...")
    try:
        subprocess.run(
            ["docker", "compose", "up", "-d"], cwd=ROOT, check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
        )
    except Exception as e:
        print(f"  Failed to start stack: {e}")
        print("  Is Docker Desktop running? Try: docker compose up -d")
        return False
    print("  Waiting for health", end="", flush=True)
    for _ in range(40):
        if healthy():
            print(" -- healthy.")
            return True
        print(".", end="", flush=True)
        time.sleep(2)
    print("\n  Timed out waiting for the stack to become healthy.")
    return False


def run_phase(pid: str, pause: bool) -> bool:
    title, script, needs_key = PHASES[pid]
    banner(f"Phase {pid}: {title}")
    if needs_key and not os.environ.get("ANTHROPIC_API_KEY"):
        print("  SKIPPED -- no ANTHROPIC_API_KEY found (set it in .env).")
        return True  # not a failure; just not runnable
    script_path = EXAMPLES / script
    if not script_path.exists():
        print(f"  MISSING script: {script_path}")
        return False
    proc = subprocess.run([sys.executable, str(script_path)], cwd=ROOT, env=os.environ)
    ok = proc.returncode == 0
    print(f"\n  -> Phase {pid} {'PASSED' if ok else 'FAILED'} (exit {proc.returncode})")
    if pause:
        try:
            input("\n  [Enter] to continue to the next phase... ")
        except (EOFError, KeyboardInterrupt):
            pass
    return ok


def main():
    ap = argparse.ArgumentParser(description="NANDA Context Graph demo runner")
    ap.add_argument("--pause", action="store_true", help="wait for Enter between phases")
    ap.add_argument("--skip-stack", action="store_true", help="don't try to start docker")
    ap.add_argument("--only", default="", help="comma-separated phase ids, e.g. 4,5")
    args = ap.parse_args()

    banner("NANDA Context Graph -- Demo Runner")
    loaded = load_dotenv(ROOT / ".env")
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    print(f"  Loaded {len(loaded)} vars from .env  |  ANTHROPIC_API_KEY: {'present' if has_key else 'MISSING'}")

    if not ensure_stack(args.skip_stack):
        sys.exit(1)

    order = [p.strip() for p in args.only.split(",") if p.strip()] or DEFAULT_ORDER
    bad = [p for p in order if p not in PHASES]
    if bad:
        print(f"  Unknown phase id(s): {bad}. Valid: {list(PHASES)}")
        sys.exit(2)

    results = {pid: run_phase(pid, args.pause) for pid in order}

    banner("Demo Complete")
    for pid in order:
        title = PHASES[pid][0]
        print(f"  Phase {pid}: {'PASS' if results[pid] else 'FAIL'}   {title}")
    print(f"""
  Explore the traces you just generated:
    Dashboard:      {DASHBOARD}        (search: rental-broker / rental-pricing / rental-approval)
    Neo4j Browser:  {NEO4J_BROWSER}        (neo4j / password)
        MATCH (a:Agent)<-[:MADE_BY]-(d:Decision)-[:DECIDED_BECAUSE]->(s:Step) RETURN a,d,s
        MATCH p=(d:Decision)-[:PRECEDED_BY*]->(root) RETURN p
    Query API:      {QUERY}/api/v1/why?agent_id=rental-approval
""")
    sys.exit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
