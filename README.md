# nanda-context-graph

A decentralized decision-trace graph for the [NANDA Internet of Agents](https://www.media.mit.edu/projects/mit-nanda/overview/). Records **why** NANDA agents take actions — inputs, reasoning steps, tool calls, outputs, and causal links across multi-agent chains — and exposes them through a REST explainability API.

Submitted as an RFC (v0.4) to the NANDA Writing Group at MIT Media Lab and the Agentic AI Summit 2026 (Berkeley RDI).

---

## How it fits in the NANDA ecosystem

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   adapter    │     │  nanda-index │     │     NEST     │
│  (agent hub) │     │  (registry)  │     │  (testbed)   │
│  :6000/:6001 │     │    :6900     │     │   :6000+     │
└──────┬───────┘     └──────┬───────┘     └──────┬───────┘
       │ emit trace         │ /behavior proxy     │ emit trace
       │ (daemon thread)    │ (GET proxy)         │ (TraceCollector)
       ▼                    ▼                     ▼
┌─────────────────────────────────────────────────────────┐
│              nanda-context-graph                        │
│                                                         │
│  Ingest API :7200    Query API :7201    Neo4j :7687     │
│  POST /ingest/trace  GET /api/v1/why    Graph store     │
│  POST /ingest/step   GET /api/v1/trace                  │
│                      GET /federation/traces              │
└─────────────────────────────────────────────────────────┘
```

### Integration with each repo

| Repo | What nanda-context-graph adds | Activated by |
|---|---|---|
| **projnanda/adapter** | `traced_call()` wrapper in `nanda.py` emits a `DecisionTrace` after every agent action. `agent_bridge.py` injects `x-trace-id` / `x-parent-trace` headers on A2A calls. Registration includes `trace` metadata. | `NCG_INGEST_URL` env var |
| **projnanda/nanda-index** | `POST /register` stores optional `trace` sub-document. New `GET /agents/<id>/behavior` endpoint proxies to the agent's trace endpoint. | `NCG_GRAPH_API_URL` env var |
| **projnanda/NEST** | `TraceCollector` in `telemetry/trace_collector.py` hooks into `agent_bridge.py` `handle_message()` with `before_call`/`after_call` lifecycle. Maps `conversation_id` to `trace_id`. | `NCG_INGEST_URL` env var |

**All integration is opt-in.** If `NCG_INGEST_URL` is not set, all three repos behave exactly as before. Zero breaking changes.

---

## Workspace layout

This repo is designed to sit alongside the other NANDA repos:

```
NANDA/                              <-- your workspace root
├── adapter/                        <-- projnanda/adapter (git clone)
│   └── nanda_adapter/core/
│       ├── nanda.py                <-- modified: traced_call(), _emit_trace()
│       ├── agent_bridge.py         <-- modified: trace context, A2A headers
│       └── registry_url.txt        <-- default registry URL
│
├── nanda-index/                    <-- projnanda/nanda-index (git clone)
│   └── registry.py                 <-- modified: trace field, /behavior endpoint
│
├── NEST/                           <-- projnanda/NEST (git clone)
│   └── nanda_core/
│       ├── core/agent_bridge.py    <-- modified: TraceCollector hooks
│       └── telemetry/
│           └── trace_collector.py  <-- NEW: before_call/after_call lifecycle
│
└── nanda-context-graph/            <-- THIS REPO
    ├── schema/models.py            <-- Pydantic v2: DecisionTrace, ReasoningStep
    ├── store/neo4j_adapter.py      <-- Neo4j read/write with MERGE
    ├── ingest/main.py              <-- FastAPI ingest service :7200
    ├── api/query.py                <-- REST query API :7201
    ├── middleware/mcp_shim.py      <-- TracedMCP: wraps MCP tool calls
    ├── federation/sync.py          <-- Push/pull federation sync
    ├── cli/main.py                 <-- ncg CLI (emit, trace, why, history, health)
    ├── dashboard/                  <-- React + Vite explainability dashboard
    ├── examples/
    │   ├── e2e_demo.py             <-- Automated 10-step test (no API key needed)
    │   └── real_agents_demo.py     <-- 3 Claude-powered agents (needs API key)
    ├── tests/                      <-- 31 tests
    ├── docs/
    │   ├── paper_draft.md          <-- RFC v0.3
    │   └── nanda_context_graph_proposal.docx
    ├── docker-compose.yml          <-- 5-service local stack
    ├── Dockerfile
    └── pyproject.toml
```

---

## Quick start

### Prerequisites

- Python >= 3.10
- Docker Desktop running
- (Optional) An [Anthropic API key](https://console.anthropic.com/) for the real agents demo

### Step 1: Clone and start infrastructure

```bash
git clone https://github.com/Ngoga-Musagi/nanda-context-graph.git
cd nanda-context-graph
docker-compose up -d
```

Wait ~15 seconds for Neo4j to initialize, then verify:

```bash
curl http://localhost:7200/health
# {"status":"ok","service":"nanda-context-graph-ingest"}

curl http://localhost:7201/health
# {"status":"ok","service":"nanda-context-graph-query"}
```

This starts 5 services:

| Service | Port | Purpose |
|---|---|---|
| Neo4j | 7474 (browser), 7687 (bolt) | Graph database for decision traces |
| Redis | 6379 | Async queue |
| MongoDB | 27017 | For nanda-index if running locally |
| NCG Ingest API | 7200 | Receives traces from agents |
| NCG Query API | 7201 | Serves explainability queries |

### Step 2: Install Python package

```bash
pip install -e ".[dev]"
```

### Step 3: Run the automated demo (no API key needed)

```bash
python examples/e2e_demo.py
```

This starts nanda-index in TEST_MODE, registers an agent, emits multi-step traces with causal chaining, and queries every API endpoint. Expected: **10/10 passed**.

---

## Real multi-agent demo (with Claude)

This is the full showcase: **3 Claude-powered agents** collaborate on a car rental request. Each agent reasons with Claude and traces every decision to NCG.

### The agents

The agents are defined in `examples/real_agents_demo.py`:

| Agent | ID | What it does |
|---|---|---|
| **Car Rental Broker** | `rental-broker` | Receives user request, analyzes needs with Claude, checks car inventory, recommends a car, explains why, delegates to pricing |
| **Pricing Agent** | `rental-pricing` | Looks up base rates, checks customer loyalty tier, calculates discount with Claude, delegates to approval |
| **Approval Agent** | `rental-approval` | Loads discount policy, queries NCG for precedent (prior similar decisions), Claude reviews and approves/rejects |

### The flow

```
User: "I need a car in Boston for 5 days, 4 people, ski trip, Gold member"
  │
  ▼
┌─────────────────────────────────────────────────┐
│ @rental:broker                                  │
│  Step 1: [evaluate] Claude analyzes user needs  │
│  Step 2: [retrieve] Query inventory-api         │
│  Step 3: [decide]   Claude recommends RAV4      │
│  Step 4: [delegate] → @rental:pricing           │
│  Outcome: delegated                             │
└──────────────────┬──────────────────────────────┘
                   │ parent_trace_id
                   ▼
┌─────────────────────────────────────────────────┐
│ @rental:pricing                                 │
│  Step 1: [retrieve] Rate engine: $85/day        │
│  Step 2: [retrieve] CRM: Gold tier, 15% off     │
│  Step 3: [evaluate] Claude calculates $361.25   │
│  Step 4: [delegate] → @rental:approval          │
│  Outcome: delegated                             │
└──────────────────┬──────────────────────────────┘
                   │ parent_trace_id
                   ▼
┌─────────────────────────────────────────────────┐
│ @rental:approval                                │
│  Step 1: [retrieve] Policy: max 10% auto-approve│
│  Step 2: [retrieve] NCG precedent: 12 approved  │
│  Step 3: [decide]   Claude: APPROVED/REJECTED   │
│  Outcome: success                               │
└─────────────────────────────────────────────────┘
```

### Run it

```bash
# 1. Make sure docker-compose is up
docker-compose up -d

# 2. Set your Anthropic API key
export ANTHROPIC_API_KEY="sk-ant-..."

# 3. Run the 3-agent demo
python examples/real_agents_demo.py
```

The script will:
- Start nanda-index in TEST_MODE automatically
- Register all 3 agents with trace metadata
- Run each agent with real Claude calls
- Emit traces to NCG with full reasoning steps
- Verify all traces and the 3-hop causal chain in Neo4j

### View results in the dashboard

```bash
cd dashboard
npm install    # first time only
npm run dev
```

Open **http://localhost:5173** and:

1. Type `rental-broker` → click "Why did this agent act?" → see Claude's car recommendation reasoning
2. Type `rental-pricing` → see the loyalty discount calculation
3. Type `rental-approval` → see policy check + precedent query + final decision
4. Click **"View Causal Chain"** on any trace → see all 3 agents' reasoning linked together

### Causal chain visualization

The dashboard traces the full causal chain across all three agents — from the user's original request through broker recommendation, pricing calculation, and final approval — with every reasoning step, tool call:

![NANDA Context Graph — Causal Chain](nanda-context-graph-casual-chain.png)

### View results in Neo4j

Open **http://localhost:7474** (user: `neo4j`, password: `password`):

```cypher
-- See all agents, decisions, and reasoning steps
MATCH (a:Agent)-[:MADE_BY]-(d:Decision)-[:DECIDED_BECAUSE]->(s:Step) RETURN a,d,s

-- See the causal chain (delegation path)
MATCH p=(d:Decision)-[:PRECEDED_BY*]->(root) RETURN p

-- Ask: why did the broker act?
MATCH (a:Agent {agent_id: "rental-broker"})<-[:MADE_BY]-(d:Decision)-[:DECIDED_BECAUSE]->(s:Step)
RETURN d.outcome, s.step_type, s.thought, s.tool_name
```

### Query results via API

```bash
# Why did the broker recommend this car?
curl "http://localhost:7201/api/v1/why?agent_id=rental-broker" | python -m json.tool

# Full pricing trace with all steps
curl "http://localhost:7201/api/v1/agent/rental-pricing/history" | python -m json.tool

# Causal chain: follow from approval back to the original request
curl "http://localhost:7201/api/v1/chain/<approval-trace-id>/causal" | python -m json.tool

# Federation: all traces available for sync
curl "http://localhost:7201/federation/traces?since_ms=0" | python -m json.tool
```

---

## Running with the adapter (live A2A integration)

This runs a real NANDA adapter agent where messages go through the A2A protocol and traces are emitted automatically.

### Step 1: Start nanda-index

```bash
cd ../nanda-index
TEST_MODE=1 PORT=6900 python registry.py
```

### Step 2: Start the adapter agent with tracing

```bash
cd ../adapter/nanda_adapter/core

ANTHROPIC_API_KEY="sk-ant-..." \
AGENT_ID="my-agent" \
PORT=6000 \
PUBLIC_URL="http://localhost:6000" \
API_URL="http://localhost:6001" \
REGISTRY_URL="http://localhost:6900" \
NCG_INGEST_URL="http://localhost:7200" \
python -c "
import sys, os
sys.path.insert(0, '.')
from nanda import NANDA

def my_agent(message):
    return f'Processed: {message}'

agent = NANDA(my_agent)
agent.start_server_api(
    anthropic_key=os.environ['ANTHROPIC_API_KEY'],
    domain='localhost',
    agent_id='my-agent',
    port=6000, api_port=6001,
    registry='http://localhost:6900',
    public_url='http://localhost:6000',
    api_url='http://localhost:6001',
    ssl=False,
)
"
```

### Step 3: Send a message via A2A

```bash
python -c "
from python_a2a import A2AClient, Message, TextContent, MessageRole
client = A2AClient('http://localhost:6000/a2a')
msg = Message(content=TextContent(text='What is 2+2?'), role=MessageRole.USER)
resp = client.send_message(msg)
print(resp.content.text)
"
```

### Step 4: Verify the trace

```bash
curl http://localhost:7201/api/v1/agent/my-agent/history | python -m json.tool
curl "http://localhost:7201/api/v1/why?agent_id=my-agent" | python -m json.tool
```

---

## Deployment

### Google Cloud (one command)

Deploy the full stack (Neo4j, Redis, Ingest API, Query API, Dashboard) to a GCP Compute Engine VM:

```bash
./scripts/deploy-gcp.sh
```

This will:
1. Enable the Compute Engine API
2. Create a firewall rule for ports 8080, 7200, 7201, 7474
3. Launch an e2-medium VM with Ubuntu 22.04
4. Install Docker, clone the repo, and run `docker compose up`
5. Wait for all services to come online
6. Seed demo data using `real_agents_demo.py` (requires `ANTHROPIC_API_KEY` in your environment or `.env` file)

Once complete, the script prints the public dashboard URL.

**Options:**

```bash
./scripts/deploy-gcp.sh --project PROJECT   # GCP project ID
./scripts/deploy-gcp.sh --zone ZONE         # GCP zone (default: us-central1-a)
./scripts/deploy-gcp.sh --machine TYPE      # Machine type (default: e2-medium)
./scripts/deploy-gcp.sh --teardown          # Delete the VM and firewall rule
```

**Prerequisites:** `gcloud` CLI installed and authenticated (`gcloud auth login`), a GCP project set (`gcloud config set project YOUR_PROJECT`).

### Other deployment options

- **AWS EC2:** `./scripts/deploy-aws.sh` — similar one-command deployment to AWS
- **Netlify (dashboard only):** `./scripts/deploy-netlify.sh YOUR_BACKEND_IP` — deploys the React dashboard to Netlify with API proxying to your backend VM

---

## Performance

Benchmarks measured on a single-node deployment (Neo4j 5, FastAPI, Python 3.11+). Full details in [docs/benchmarks.md](docs/benchmarks.md).

| Concurrent Agents | P50 (ms) | P95 (ms) | P99 (ms) | Throughput (traces/s) |
|---|---|---|---|---|
| 100 | 4.2 | 12.8 | 18.5 | 312 |
| 1,000 | 6.1 | 28.3 | 45.7 | 476 |
| 5,000 | 11.4 | 68.2 | 142.0 | 562 |
| 10,000 | 18.7 | 125.4 | 198.3 | 606 |

Fire-and-forget overhead on the agent: **< 1 ms**. If NCG is down, the agent is unaffected (daemon thread fails silently).

To regenerate: `python tests/benchmark_ingest.py` (requires `docker-compose up`).

---

## Privacy Modes

nanda-context-graph supports three privacy modes, configurable per-agent via `NCG_PRIVACY_MODE`:

| Mode | Storage | Who can query | Use case |
|---|---|---|---|
| **public** | Plain JSON in Neo4j | Any agent with a valid NANDA DID | Open-source agents, public services |
| **private** | Encrypted at rest (AES-256-GCM) | Agent's own DID, authorized auditors in AgentFacts, jurisdiction-scoped regulators | Enterprise, healthcare, financial services |
| **zkp** | Zero-knowledge proof of decision properties | Verifier holds only the verification key | Intelligence, sensitive negotiations, personal AI |

In private mode, metadata (trace_id, timestamp, outcome, agent_id) remains in plaintext for indexing; reasoning steps and inputs are encrypted. In ZKP mode, the agent proves properties about its decision (e.g., "I consulted policy X and my confidence exceeded 0.8") without revealing the full reasoning chain.

Cross-border data residency: traces inherit the `jurisdiction` field from AgentFacts. Federation sync is jurisdiction-gated — an EU trace will not sync to a non-EU registry peer.

---

## Architecture Decisions

For detailed rationale on key design choices, see:

- [docs/COMPARISON.md](docs/COMPARISON.md) — Why nanda-context-graph instead of OpenTelemetry/LangSmith, why a property graph, and the structural arguments for protocol-native observability.

---

## API reference

### Ingest API (port 7200)

| Method | Endpoint | Description |
|---|---|---|
| POST | `/ingest/trace` | Ingest a DecisionTrace. Returns 202, writes to Neo4j in background. |
| POST | `/ingest/step` | Append a reasoning step to an existing trace. |
| GET | `/health` | Health check. |

### Query API (port 7201)

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/v1/trace/{trace_id}` | Full trace with all reasoning steps. |
| GET | `/api/v1/why?agent_id=X` | Most recent decision for an agent (with steps). |
| GET | `/api/v1/agent/{id}/history?limit=20&outcome=success` | Paginated decision history. |
| GET | `/api/v1/agent/{id}/trust-score?window_days=30` | Behavioral Trust Score (BTS) with sub-scores and ZTAA level. |
| GET | `/api/v1/chain/{trace_id}/causal` | Follow `PRECEDED_BY` edges to root decision. |
| POST | `/api/v1/replay/{trace_id}` | Replay a trace (stub). |
| GET | `/federation/traces?since_ms=0` | Federation: all traces since timestamp. |
| GET | `/health` | Health check. |

### DecisionTrace schema

```json
{
  "trace_id": "uuid",
  "agent_id": "rental-broker",
  "agent_handle": "@rental:broker",
  "parent_trace_id": null,
  "a2a_msg_id": "conversation-123",
  "inputs": {"user_request": "I need a car in Boston..."},
  "steps": [
    {
      "step_id": "s-abc123",
      "step_type": "evaluate",
      "thought": "User needs analysis: 4 passengers, ski trip, Gold member...",
      "tool_name": null,
      "confidence": 0.95
    },
    {
      "step_id": "s-def456",
      "step_type": "retrieve",
      "thought": "Queried car inventory database",
      "tool_name": "inventory-api",
      "tool_input": {"location": "Boston"},
      "tool_output": {"suv": {"model": "Toyota RAV4", "daily_rate": 85}},
      "confidence": 1.0
    },
    {
      "step_id": "s-ghi789",
      "step_type": "decide",
      "thought": "I recommend the Toyota RAV4 because...",
      "confidence": 0.88
    },
    {
      "step_id": "s-jkl012",
      "step_type": "delegate",
      "thought": "Delegating to @rental:pricing for rate calculation",
      "tool_name": "a2a-delegate",
      "confidence": 1.0
    }
  ],
  "output": {"recommendation": "Toyota RAV4", "delegated_to": "rental-pricing"},
  "outcome": "delegated",
  "timestamp_ms": 1775590489695,
  "duration_ms": 11140
}
```

---

## CLI

```bash
ncg emit       --agent-id ID --message TEXT    # Emit a test trace
ncg trace      TRACE_ID                        # Fetch a full trace
ncg why        --agent-id ID                   # Last decision for an agent
ncg history    --agent-id ID [--limit N]       # Decision history
ncg health                                     # Check service health
```

---

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | *(required for real demo)* | Claude API key for agent reasoning |
| `NCG_INGEST_URL` | *(unset = disabled)* | Set in adapter/NEST to enable trace emission |
| `NCG_GRAPH_API_URL` | `http://localhost:7201` | Set in nanda-index for /behavior proxy |
| `NCG_NEO4J_URI` | `bolt://localhost:7687` | Neo4j connection |
| `NCG_NEO4J_USER` | `neo4j` | Neo4j username |
| `NCG_NEO4J_PASSWORD` | `password` | Neo4j password |
| `NCG_REDIS_URL` | `redis://localhost:6379` | Redis queue |
| `NCG_PRIVACY_MODE` | `public` | public / private / zkp |
| `NCG_FEDERATION_PEERS` | *(empty)* | Comma-separated peer NCG URLs for federation |

---

## Tests

```bash
# All tests (31 total)
pytest -v

# Schema + middleware only (no external deps)
pytest tests/test_schema.py tests/test_middleware.py -v

# Integration tests (requires docker-compose up)
pytest tests/test_integration.py -v

# Store tests (requires Neo4j)
NEO4J_AVAILABLE=1 pytest tests/test_store.py -v
```

---

## Build phases

| Phase | Status | Description |
|---|---|---|
| 1 — Schema & store | Complete | Pydantic models, Neo4j adapter, CLI |
| 2 — Ingest + adapter | Complete | FastAPI ingest, MCP shim, adapter hooks |
| 3 — Query API + index | Complete | REST query API, nanda-index integration, NEST hooks |
| 4 — Docker + dashboard | Complete | docker-compose, React dashboard |
| 5A — Federation sync | Complete | Push/pull sync, LWW via MERGE, federation endpoint |
| 5B — Federation CRDT | In progress | Vector clocks on Decision nodes, jurisdiction-gated sync |
| 6 — BTS + privacy | Planned | Full BTS implementation, private/ZKP modes, VC signatures |

## License

Apache 2.0
