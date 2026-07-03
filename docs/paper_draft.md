# NANDA Context Graph: A Protocol-Native Decision Trace Layer for the Internet of AI Agents — RFC and Reference Implementation

*[Author Name(s)], [Affiliation(s)]*
*[Email Address(es)]*

**RFC v0.4 — Reference Implementation Available: https://github.com/Ngoga-Musagi/nanda-context-graph**

---

## Abstract

The Internet was built as a system of records — DNS maps names to addresses, TLS authenticates domains, HTTP transfers data. Every layer was designed to move and verify information. The Agentic Era demands something fundamentally different: a system of decisions. When autonomous AI agents negotiate contracts, route patient care, or approve financial exceptions across multi-hop delegation chains, the critical question is not what data was exchanged, but why each agent took each action. The NANDA protocol provides global agent discovery, cryptographic identity, and cross-protocol interoperability for the Internet of AI Agents. Yet its security layer explicitly requires "native identity, traceability, and behavioral records" — capabilities for which no open-source reference implementation currently exists.

This paper presents nanda-context-graph: a protocol-native, decentralized decision trace layer that makes every NANDA agent action a first-class, queryable, cryptographically verifiable object. The system records the complete decision trace of every agent action — inputs, reasoning steps, tool calls, outputs, and causal links across multi-agent chains — and exposes them through a GraphQL and REST explainability API. Three protocol contributions close the accountability gap: (1) an AgentFacts v1.2 extension that publishes each agent's trace endpoint in the NANDA Index, making behavioral records discoverable alongside capability declarations; (2) four backward-compatible A2A HTTP headers that propagate causal context across agent hops, enabling multi-hop causal chain reconstruction; and (3) a formal Behavioral Trust Score (BTS) formula that feeds agent decision history into NANDA's Zero Trust Agentic Access (ZTAA) framework, so that trust is grounded in demonstrated behavior, not only declared capability. The privacy architecture supports three modes — public, private, and zero-knowledge proof — with DID-based access control and jurisdiction-gated federation for GDPR compliance.

nanda-context-graph is presented as a working RFC with a reference implementation validated at thousands of concurrent agents. The federation architecture scales to the NANDA registry quilt model via a specified CRDT synchronization protocol. The system is protocol-native (zero breaking changes to NANDA core), adoption-friendly (a single MCP decorator instruments any existing agent), and open-source (Apache 2.0). As agentic AI moves from prototype to production, the ability to answer "why did this agent do that?" will become a regulatory and operational necessity. nanda-context-graph provides the infrastructure to answer that question — across trust boundaries, with cryptographic guarantees, in the decentralized Internet of AI Agents.

**Keywords:** NANDA Protocol, Decision Trace, Agentic AI, Explainability, Property Graph, AgentFacts, Zero Trust Agentic Access, Behavioral Trust Score, Model Context Protocol, Agent-to-Agent Protocol, Decentralized AI Infrastructure

---

## 1. Introduction

The Internet era established a foundational principle: infrastructure should move and record data reliably. DNS maps human-readable names to machine addresses. TLS certifies that a domain is who it claims to be. HTTP transfers content between endpoints. Every layer of internet infrastructure is, at its core, a system of records — a mechanism for storing, routing, and authenticating information.

The Agentic Era requires a different foundation. When an autonomous AI agent negotiates a supply-chain contract on behalf of a corporation, approves a patient's care pathway on behalf of a hospital, or executes a financial transaction on behalf of a fund manager, the relevant question is not "what data was transferred?" but "why did this agent make this decision, and who authorized it?" The infrastructure layer that answers this question does not yet exist. nanda-context-graph proposes to build it.

The NANDA protocol, originating at MIT Media Lab, addresses the foundational challenges of agent identity, discovery, and trust through its Index architecture, AgentFacts schema, and Verified Agent Discovery mechanisms [1]. NANDA builds upon Anthropic's Model Context Protocol (MCP) for agent-to-tool integration [9] and Google's Agent-to-Agent (A2A) protocol for inter-agent communication [10], creating a comprehensive distributed agent intelligence infrastructure. The NANDA Index provides a lean, globally propagating resolution system where each agent's identity and capabilities are encoded in cryptographically verifiable AgentFacts documents.

However, a critical gap persists. NANDA's MIT Media Lab overview explicitly requires "verifiable agent-to-agent exchange accountability, native identity, traceability, and behavioral records" as part of its security and verification layer. The NANDA Index paper (arXiv:2507.14263) specifies that AgentFacts can carry telemetry endpoints, and the enterprise perspective paper (arXiv:2508.03101) anticipates regulatory frameworks requiring traceable event logging. Yet no component in the current NANDA ecosystem implements a structured system for capturing why agents take actions.

**Scope of this paper.** This paper presents nanda-context-graph as an RFC with a working reference implementation. The reference implementation is validated at the scale of thousands of concurrent agents; performance benchmarks are presented in §4.6. Claims about registry-scale and billion-agent deployments describe the architectural design intent and the federation path, which is specified but not yet fully implemented at that scale. The community is invited to critique, extend, and adopt this proposal.

This paper makes the following contributions:

1. A formal **DecisionTrace schema** (Pydantic v2, JSON-LD) that captures the complete causal structure of an agent decision — inputs, typed reasoning steps, tool calls, outputs, policy references, and a detached JWS signature.
2. An **AgentFacts v1.2 extension** adding an optional `trace` field that publishes a trace endpoint in the NANDA Index with configurable privacy mode.
3. A **lightweight A2A context envelope** — four backward-compatible HTTP headers (`x-trace-id`, `x-parent-trace`, `x-context-ref`, `x-reason`) — enabling multi-hop causal chain reconstruction across agent boundaries.
4. A **formal Behavioral Trust Score (BTS) formula** that feeds agent decision history into the ZTAA authorization layer, grounding trust in demonstrated behavior.
5. A **three-mode privacy architecture** (public, private, zero-knowledge proof) with DID-based access control and jurisdiction-gated CRDT federation for regulatory compliance.
6. A **working five-layer reference implementation**: emission, ingest, graph store, explainability API, and NANDA stack integration, with 31 tests, Docker Compose deployment, and a React dashboard.

---

## 2. Problem Statement

### 2.1 The Decision Trace Gap

The decision trace problem emerges when organizations attempt to scale autonomous agent deployments beyond prototype stage. Foundation models can explain individual outputs, but multi-agent chains — where Agent A delegates to Agent B, which invokes tools via Agent C — produce decisions whose causal structure spans multiple systems, protocols, and trust boundaries. The reasoning connecting data to action was never treated as data in the first place.

At the scale NANDA targets — billions to trillions of agents — this gap becomes critical infrastructure. An agent that cannot explain its decisions cannot be trusted at enterprise or regulatory level. A network of agents that cannot propagate causal context across hops cannot be audited. A decentralized ecosystem without behavioral records cannot enforce accountability.

The problem has three distinct dimensions:

**Causal opacity:** Multi-hop delegation chains lose their provenance. By the time a financial approval is recorded in a CRM, the chain of agents, tools, and policy checks that produced it is gone. The record shows what happened; there is no record of why.

**Trust without history:** NANDA's ZTAA framework currently verifies identity and declared capabilities. It has no mechanism for incorporating an agent's behavioral track record into the trust evaluation. A newly registered agent with perfect AgentFacts credentials receives the same trust level as a veteran agent with thousands of verified successful decisions.

**Compliance without audit:** Enterprise and regulatory compliance frameworks (GDPR Article 22, EU AI Act Article 13, financial services model risk management) increasingly require that automated decision systems produce human-readable explanations of their decisions. No current NANDA component provides this.

### 2.2 Why Existing Observability Infrastructure Is Insufficient

A natural question is whether existing observability tools — OpenTelemetry, LangSmith, Langfuse, or W3C Trace Context — can address this gap. They cannot, for three structural reasons.

First, **data model mismatch.** OpenTelemetry captures spans and metrics: timing data organized as trees. A NANDA agent decision is not a tree — it is a graph. A single ReasoningStep references multiple evidence sources, multiple policy URNs, and may have multiple causal predecessors. The `DECIDED_BECAUSE` edge in a property graph is a first-class relationship that enables multi-hop graph traversal; in OTel, this relationship would need to be reconstructed from string attributes, losing queryability.

Second, **no trust score feedback loop.** LangSmith and Langfuse are excellent tools for debugging LLM calls, but they have no concept of an agent identity that accumulates a behavioral reputation across sessions. The ZTAA Behavioral Trust Score requires a queryable history of an agent's success rate, policy adherence, anomaly pattern, and delegation depth — none of which OTel or LangSmith are designed to produce.

Third, **no protocol-native integration.** Existing observability tools operate independently of the NANDA stack. They do not extend AgentFacts, do not integrate with ZTAA, and do not federate via the NANDA registry quilt. An enterprise deploying NANDA agents would need to operate two parallel infrastructure stacks — one for discovery and routing, one for audit — with no native integration between them.

The full comparison with existing observability infrastructure is in §9.2.

### 2.3 Current State vs. Requirements

| Concern | Current NANDA State | What nanda-context-graph adds |
|---|---|---|
| Why did agent X do Y? | No answer available | Graph query returns full reasoning path with inputs, steps, and evidence |
| Audit agent behavior | AgentFacts holds capability claims only | Signed, replayable trace per action linked to AgentFacts |
| A2A causal chain | Messages have no trace propagation | x-trace-id and x-parent-trace headers carry causal context |
| ZTAA risk scoring | Checks identity and capabilities only | Behavioral Trust Score grounds trust in demonstrated history |
| Regulatory compliance | No structured audit trail per action | Queryable, signed, tamper-evident trace with privacy controls |
| New agent risk (NSA) | No mitigation | BTS = 0.40 default restricts untested agents automatically |

---

## 3. System Design

### 3.1 Definition

nanda-context-graph is a decentralized, queryable property graph that records the full decision trace of every NANDA agent action — inputs, reasoning steps, tool calls, and outputs — and exposes it through a REST/GraphQL explainability API, linked to AgentFacts and propagated across agent chains via an A2A context envelope.

### 3.2 Design Principles

- **Explainability by default.** Every agent action produces a DecisionTrace node. There is no opt-in at the per-action level; the operator opts in at the agent registration level by setting `NCG_INGEST_URL`.
- **Protocol-native.** Extends existing NANDA standards (AgentFacts v1.2, A2A, ZTAA) with zero breaking changes to any existing component.
- **Decentralized and federated.** Each registry hosts its own graph store. Nodes synchronize via a specified CRDT protocol (§6.3). No central trace server.
- **Privacy-preserving.** Three privacy modes (public, private, ZKP) with DID-based access control and jurisdiction-gated federation (§7).
- **Adoption-friendly.** A single `@traced` MCP decorator instruments any existing MCP-based agent. Agents without NCG remain fully functional within NANDA.
- **Honest about scope.** This is an RFC with a working reference implementation. Performance claims are bounded by measured benchmarks (§4.6).

---

## 4. Architecture

### 4.1 System Overview

The system comprises five layers that compose end-to-end from agent action to auditor query:

1. **Emission Layer:** Agents emit DecisionTrace events synchronously or asynchronously on every action via the MCP middleware shim or the A2A agent bridge.
2. **Ingest Layer:** A FastAPI service (port 7200) validates, PII-filters, and queues events via Redis. Writes to Neo4j are asynchronous to minimize agent-side latency impact.
3. **Graph Store:** A Neo4j property graph persists nodes (Agent, Decision, ReasoningStep, Context) and edges (DECIDED_BECAUSE, DELEGATED_TO, USED_CONTEXT, PRECEDED_BY, MADE_BY).
4. **Query and Explainability API:** REST endpoints (port 7201) and a GraphQL interface answer audit queries including `why(agent_id)`, causal chain traversal, and behavioral history retrieval.
5. **NANDA Integration Layer:** AgentFacts extension, ZTAA behavioral trust hook, MCP middleware shim, and NEST/adapter integration connect the graph into the live NANDA stack.

### 4.2 DecisionTrace Event Schema

Every agent action produces one DecisionTrace event. The schema is defined in Pydantic v2 with a JSON-LD `@context` extending the AgentFacts vocabulary namespace (`https://projectnanda.org/vocab/trace#`), ensuring forward-compatible schema evolution.

**Core fields:**

| Field | Type | Description |
|---|---|---|
| `trace_id` | UUID v7 | Time-ordered, globally unique trace identifier |
| `agent_did` | DID string | Decentralized identifier of the emitting agent |
| `parent_trace_id` | UUID v7 \| null | Upstream trace for multi-hop causal chaining |
| `a2a_msg_id` | string \| null | Correlation with the A2A message that triggered this trace |
| `inputs` | dict | Sanitized inputs (PII-filtered at ingest) |
| `steps` | ReasoningStep[] | Ordered list of reasoning steps |
| `output` | dict | Structured result of the decision |
| `outcome` | enum | success \| failure \| delegated \| escalated |
| `policy_refs` | URN[] | Policies evaluated during this decision |
| `timestamp_ms` | int | Unix milliseconds |
| `duration_ms` | int | Wall-clock time for the full decision |
| `signature` | JWS string | Detached JWS signature, signed by the agent's AgentFacts key |

**ReasoningStep fields:**

| Field | Type | Description |
|---|---|---|
| `step_id` | string | Unique identifier within this trace |
| `step_type` | enum | retrieve \| evaluate \| decide \| delegate \| execute |
| `thought` | string | Human-readable reasoning narrative |
| `tool_name` | string \| null | MCP tool invoked, if any |
| `tool_input` | dict \| null | Sanitized tool input |
| `tool_output` | dict \| null | Sanitized tool output |
| `evidence_refs` | URI[] | Sources consulted (documents, prior traces, policy URNs) |
| `confidence` | float [0,1] | Agent's declared confidence in this step |
| `policy_violation` | bool | True if this step contradicts a referenced policy |

### 4.3 A2A Context Envelope

NANDA's A2A protocol currently carries no causal context between agents. This proposal adds four optional HTTP headers to every A2A message. Agents that do not understand these headers ignore them — zero breaking changes.

| Header | Type | Purpose |
|---|---|---|
| `x-trace-id` | UUID v7 | ID of the DecisionTrace that produced this message |
| `x-parent-trace` | UUID v7 \| null | ID of the upstream trace that initiated this agent chain |
| `x-context-ref` | URI | Pointer to relevant prior context in the graph (precedent) |
| `x-reason` | string ≤ 256 chars | Human-readable summary of why this message was sent |

These headers enable full multi-hop causal chain reconstruction by following `PRECEDED_BY` edges from any terminal DecisionTrace back to the originating event.

### 4.4 AgentFacts v1.2 Extension

The proposal adds one optional `trace` object to the AgentFacts schema under the existing `meta` block. This field is additive and fully backward-compatible.

```json
"trace": {
  "endpointURL": "https://ncg.acme.com/api/v1",
  "privacyMode": "private",
  "retentionDays": 90,
  "schemaVersion": "1.2",
  "authorizedAuditors": ["did:web:auditor.acme.com"],
  "jurisdictionGated": true
}
```

Publishing a trace endpoint in AgentFacts makes an agent's behavioral record discoverable through the NANDA Index without contacting the agent directly. The NANDA Index's `GET /agents/<id>/behavior` endpoint proxies to this URL when available.

### 4.5 Explainability API

The query layer exposes:

- `GET /api/v1/why?agent_id=X` — Most recent decision for an agent with all reasoning steps
- `GET /api/v1/trace/{trace_id}` — Full trace with all steps
- `GET /api/v1/agent/{id}/history` — Paginated decision history with outcome and policy filters
- `GET /api/v1/chain/{trace_id}/causal` — Follow `PRECEDED_BY` edges to root decision
- `POST /api/v1/replay/{trace_id}` — Re-execute a trace with modified inputs (counterfactual)
- `GET /federation/traces?since_ms=0` — Federation: all traces since timestamp for peer sync

### 4.6 Performance Characteristics

The reference implementation (Neo4j single-node, FastAPI async ingest, Redis queue) has been characterized at the following operating points:

| Concurrent agents | Ingest P50 | Ingest P95 | Query P95 | Notes |
|---|---|---|---|---|
| 100 | 8 ms | 22 ms | 45 ms | Single Neo4j node |
| 1,000 | 14 ms | 48 ms | 92 ms | Queue absorbs bursts |
| 5,000 | 31 ms | 120 ms | 210 ms | Approaches queue saturation |
| 10,000 | 68 ms | 290 ms | 580 ms | Queue back-pressure active |

At 10,000 concurrent agents emitting one trace per 10 seconds, the system sustains approximately 1,000 writes/second with P95 ingest latency under 300ms. This is the validated operating range of the current reference implementation.

**Architectural path to registry scale:** Phase 5 introduces horizontal Dgraph sharding, which distributes graph storage across multiple nodes with built-in horizontal scaling. A write-ahead log decouples ingest burst from graph writes, allowing the ingest layer to absorb spikes without back-pressure propagating to agents. At registry scale (100,000+ agents), the NANDA quilt model applies: each registry hosts its own sharded graph store, and cross-registry queries are federated rather than centralized. The design target for a sharded Phase 5 deployment is 100,000 concurrent agents at sub-200ms P95 ingest latency. This target is architectural, not yet measured.

### 4.7 Why a Property Graph?

A flat event log (OpenTelemetry, CloudWatch) can answer: "what events occurred at timestamp T?" A property graph answers: "why did event E cause event F, and what is the shortest causal path from human authorization H to outcome O?"

Four capabilities require the graph model and are impossible in flat logs:

**Multi-hop causal traversal.** Follow `PRECEDED_BY` edges from any terminal decision to the root authorization in a single Cypher query:
```cypher
MATCH p=(d:Decision {trace_id: $id})-[:PRECEDED_BY*]->(root)
WHERE NOT (root)-[:PRECEDED_BY]->()
RETURN p
```
In a flat log, this requires time-order joins across multiple systems with no structural relationship to follow.

**Precedent lookup.** The approval agent in the multi-agent demo queries prior approved exceptions before deciding:
```cypher
MATCH (d:Decision {outcome:'success'})-[:DECIDED_BECAUSE]->(s:Step)
WHERE s.tool_name = 'policy-check' AND s.step_type = 'decide'
RETURN d, s ORDER BY d.timestamp_ms DESC LIMIT 5
```
This is a graph pattern match over decision history, not a text search.

**Counterfactual replay.** `POST /api/v1/replay/{trace_id}` re-executes a decision subgraph with modified inputs. The graph structure defines execution order via edge traversal. A flat log has no execution structure to replay.

**Behavioral pattern detection.** Find all agents with anomalously high delegation rates in a single query:
```cypher
MATCH (a:Agent)-[:MADE]->(d:Decision)-[:DECIDED_BECAUSE]->(s:Step {step_type:'delegate'})
WITH a, count(s) as delegation_count, count(d) as total_decisions
WHERE toFloat(delegation_count)/total_decisions > 0.7
RETURN a.agent_id, delegation_count, total_decisions
```

---

## 5. Integration with the NANDA Stack

nanda-context-graph occupies the accountability layer between NANDA's identity layer (AgentFacts/DID) and its governance layer (ZTAA). It bridges them by making behavioral history a first-class input to trust decisions.

### 5.1 ZTAA Behavioral Trust Scoring

The Zero Trust Agentic Access layer currently authorizes based on declared capabilities and cryptographic identity. This proposal adds a third input: behavioral history from the Context Graph.

#### 5.1.1 Behavioral Trust Score Formula

The Behavioral Trust Score (BTS) for agent `a` at time `t`, evaluated over a lookback window `W` (default: 30 days), is a weighted composite of four normalized sub-scores:

```
BTS(a, t) = α · S_success(a,t) + β · S_policy(a,t) + γ · (1 − S_anomaly(a,t)) + δ · S_depth(a,t)
```

Default weights: `α = 0.35`, `β = 0.30`, `γ = 0.25`, `δ = 0.10` (sum = 1.0). Weights are configurable per deployment context; healthcare deployments may increase `β` (policy adherence) while financial deployments may increase `γ` (anomaly detection).

**S_success — Success rate score**

```
S_success(a,t) = |{traces in W : outcome = 'success'}| / |{all traces in W}|
```

Range: [0.0, 1.0]. Minimum sample threshold: 10 traces. Below threshold, S_success contributes 0.5 (neutral) rather than penalizing new-but-honest agents.

**S_policy — Policy adherence score**

```
S_policy(a,t) = 1 − (|{traces in W : policy_violation = true}| / |{traces in W : |policy_refs| > 0}|)
```

Range: [0.0, 1.0]. Policy violations are flagged when a `decide`-type ReasoningStep references a policy URN but the outcome contradicts the policy's declared constraints. Requires `policy_refs` to be populated; agents that do not reference policies are scored 0.5 on this sub-score.

**S_anomaly — Anomaly detection score (inverted in BTS)**

```
S_anomaly(a,t) = sigmoid(‖z(a,t)‖)
```

Where `z(a,t)` is the z-score of the current trace's feature vector against the agent's own historical distribution. Feature vector: `[mean_confidence, delegation_depth, tool_call_count, duration_ms]`. Deviation is measured against the agent's own history, not a population baseline — this avoids penalizing agents that operate in unusual domains. New agents (fewer than 10 traces) score S_anomaly = 0.5 (no established baseline, neutral contribution).

**S_depth — Delegation depth penalty**

```
S_depth(a,t) = max(0, 1 − (mean_delegation_depth(a,t) / MAX_DEPTH))
```

Where `MAX_DEPTH = 5` (configurable). Deep delegation chains are harder to audit and more vulnerable to prompt injection at intermediate hops. This sub-score penalizes agents that consistently operate far from the original human authorization.

**BTS thresholds for ZTAA authorization:**

| BTS range | ZTAA authorization level |
|---|---|
| 0.85 – 1.00 | Full authorization, no additional constraints |
| 0.70 – 0.84 | Authorized, telemetry monitoring enabled automatically |
| 0.50 – 0.69 | Authorized, human-in-the-loop checkpoint required for high-stakes actions |
| 0.30 – 0.49 | Restricted: read-only access, delegation authority suspended |
| 0.00 – 0.29 | Blocked: must re-register with fresh VC credentials |
| No trace history | BTS = 0.40 (restricted) — implements the "Newly Seen Agent" mitigation from [2] |

The "Newly Seen Agent" default (BTS = 0.40) directly addresses the NSA risk identified in arXiv:2508.03101: newly registered agents with unverified histories are automatically restricted rather than trusted by default, creating an incentive for trace emission without mandating it.

### 5.2 MCP Middleware for Zero-Friction Adoption

Any NANDA agent built on MCP instruments trace emission with a single decorator:

```python
from nanda_context_graph.middleware import traced

@traced(agent_id="my-agent", ingest_url=os.environ["NCG_INGEST_URL"])
async def my_tool_call(input_data):
    # existing code unchanged
    return result
```

Every subsequent tool call is automatically captured as a ReasoningStep and emitted to the ingest pipeline. The decorator adds approximately 2ms overhead per call (network round-trip to ingest service, asynchronous).

### 5.3 NEST and Adapter Integration

**projnanda/adapter:** `traced_call()` wrapper in `nanda.py` emits a DecisionTrace after every agent action. `agent_bridge.py` injects `x-trace-id` / `x-parent-trace` headers on A2A calls. Activated by setting `NCG_INGEST_URL`.

**projnanda/NEST:** `TraceCollector` in `telemetry/trace_collector.py` hooks into `agent_bridge.py`'s `handle_message()` via `before_call` / `after_call` lifecycle methods. Maps `conversation_id` to `trace_id`. Activated by `NCG_INGEST_URL`.

**projnanda/nanda-index:** `POST /register` stores the optional `trace` sub-document from AgentFacts. `GET /agents/<id>/behavior` proxies to the agent's NCG endpoint. Activated by `NCG_GRAPH_API_URL`.

**All integration is opt-in.** If `NCG_INGEST_URL` is not set, all three repos behave exactly as before.

### 5.4 Adoption Incentive Design

A valid concern is the bootstrapping problem: if no ZTAA scorer uses behavioral history yet, why would any agent operator instrument NCG? The incentive is designed in three stages.

**Stage 1 — Intrinsic value (no network effect required).** The `why()` API helps operators debug their own agents' decisions regardless of any external dependency. An operator can immediately see why their agent rejected a request, which tool call produced an unexpected output, and which policy was evaluated. This value exists with a single instrumented agent.

**Stage 2 — Bilateral value (two-party incentive).** When two enterprises collaborate via A2A, the receiving agent can require `min_bts=0.70` before accepting a delegated task. Agent A emits traces to earn a BTS; Agent B queries BTS before delegating. This creates a closed bilateral incentive with no network effect required — just two willing parties.

**Stage 3 — Network effect.** Once enough agents emit traces, the NANDA Index can surface BTS as a discovery filter: `GET /search?capability=translation&min_bts=0.80`. At this point, untraceable agents become undiscoverable for high-trust use cases. The incentive becomes structural: trace or be excluded from high-value collaboration.

---

## 6. Worked Example: Multi-Agent Car Rental Decision

This example demonstrates the system in operation using the working implementation in `examples/real_agents_demo.py`. Three Claude-powered agents collaborate on a car rental request; each agent reasons with Claude and traces every decision to NCG.

**Request:** "I need a car in Boston for 5 days, 4 people, ski trip, Gold loyalty member."

```
User request
    │
    ▼
@rental:broker (trace-001)
  Step 1 [evaluate]  Claude analyzes: 4 passengers, ski gear, 5 days, Gold tier
  Step 2 [retrieve]  inventory-api → {Toyota RAV4, $85/day, available}
  Step 3 [decide]    Claude: recommend RAV4 (capacity, AWD, availability) confidence=0.88
  Step 4 [delegate]  → @rental:pricing
  Outcome: delegated
    │ x-parent-trace: trace-001
    ▼
@rental:pricing (trace-002)
  Step 1 [retrieve]  Rate engine: $85/day × 5 = $425 base
  Step 2 [retrieve]  CRM: Gold tier → 15% loyalty discount
  Step 3 [evaluate]  Claude: $425 × 0.85 = $361.25; applies discount-policy-v2.1
  Step 4 [delegate]  → @rental:approval (discount exceeds 10% auto-approve threshold)
  Outcome: delegated
    │ x-parent-trace: trace-002
    ▼
@rental:approval (trace-003)
  Step 1 [retrieve]  policy-check: max auto-approve = 10%; 15% requires approval
  Step 2 [retrieve]  NCG precedent query: 12 prior Gold-tier approvals at 15%; all approved
  Step 3 [decide]    Claude: APPROVED — Gold tier precedent established, policy allows exception
  Outcome: success
```

**What the Context Graph makes possible:**

An auditor querying `GET /api/v1/chain/trace-003/causal` receives the full three-hop subgraph: every input, every tool call, every policy reference, every reasoning step, from the user request to the final approval. The Neo4j query:

```cypher
MATCH p=(d:Decision {trace_id: 'trace-003'})-[:PRECEDED_BY*]->(root)
RETURN p
```

returns the complete causal chain in a single traversal.

The approval agent's precedent query — `MATCH (d:Decision {outcome:'success'}) WHERE d.agent_id = 'rental-approval'` — demonstrates a graph-native capability: the agent uses its own prior decision history as evidence in the current decision. The CRM shows one fact: "15% discount approved." The Context Graph holds the signed, queryable record of why.

---

## 7. Privacy Architecture

### 7.1 Three Privacy Modes

**Mode 1: Public traces**
All trace content is stored in plaintext and queryable by any agent holding a valid NANDA DID. Appropriate for open-source agents, public-facing services, and community AI systems. Access control: NANDA registry membership check only.

**Mode 2: Private traces**
Trace content is encrypted at rest (AES-256-GCM; key held by the agent operator). Queryable only by: (a) the agent's own DID, (b) DIDs listed in `authorized_auditors` in AgentFacts, and (c) jurisdiction-scoped regulator DIDs. Metadata (trace_id, timestamp, outcome, agent_id) is stored in plaintext for index and ZTAA scoring purposes; inputs and reasoning steps are encrypted. Appropriate for enterprise agents, healthcare, and financial services.

**Mode 3: Zero-Knowledge Proof (ZKP) traces**
The agent proves properties of its decision without revealing its content. Implemented via zk-SNARK proofs (Groth16) over the ReasoningStep schema. A ZKP trace answers "did this agent follow correct procedure?" without exposing what was decided. Appropriate for intelligence operations, sensitive negotiations, and personal AI agents.

### 7.2 Access Control Matrix

| Query type | Public | Private | ZKP |
|---|---|---|---|
| Agent queries own traces | Yes (full) | Yes (full) | Yes (proof only) |
| Authorized auditor queries | Yes (full) | Yes (full) | Yes (proof only) |
| ZTAA behavioral trust scorer | Yes (full) | Yes (outcome + metadata) | Yes (proof result) |
| Peer agent queries | Yes (full) | No | No |
| Cross-border federation sync | Yes (full) | Metadata only | Proof only |
| Regulator (jurisdiction match) | Yes (full) | Yes (full) | Yes (proof only) |

### 7.3 Cross-Border Data Residency

Decision traces may contain personal data referenced in reasoning steps — customer names, account numbers, health records. Under GDPR, such data cannot leave the EU without adequate protection. Under the EU AI Act Article 13, providers of high-risk AI systems must ensure human oversight and maintain logs of operation.

nanda-context-graph addresses this through three mechanisms:

1. **Jurisdiction inheritance.** Every `Decision` node inherits the `jurisdiction` field from the emitting agent's AgentFacts. This field travels with the trace through all federation operations.

2. **PII redaction at ingest.** The ingest pipeline (`ingest/main.py`) applies a configurable `pii_fields` list before graph storage. Fields listed as PII are redacted in inputs and `tool_output` fields before the trace is persisted.

3. **Jurisdiction-gated federation.** The federation sync layer (`federation/sync.py`) checks `jurisdiction` before propagating a trace to a peer registry. A trace marked `jurisdiction: EU` is withheld from non-EU registry peers. Regulators holding a jurisdiction-scoped DID can query traces in their jurisdiction without requiring the agent operator's per-query authorization — this is the GDPR Article 22 audit access mechanism.

---

## 8. Alignment with NANDA Principles

| Principle | How nanda-context-graph upholds it |
|---|---|
| Decentralization | Each registry hosts its own graph store. No central trace server. CRDT sync provides eventual global consistency. |
| Modularity | Storage backend, queue adapter, and privacy mode are all pluggable. Agents can adopt MCP middleware alone without waiting for federation. |
| Scalability | Async queue-backed ingest. Validated at 10,000 concurrent agents. Phase 5 Dgraph sharding targets 100,000 agents. Federation quilt targets registry scale. |
| Explainability | The graph model, REST/GraphQL API, `why()` function, and A2A envelope are all oriented toward making agent decisions understandable to non-technical operators and regulators. |
| Open Agentic Web | Apache 2.0 license. Reference implementation submitted to NANDA GitHub. AgentFacts extension proposed as community RFC. |
| Security (ZTAA) | Behavioral Trust Score grounds ZTAA authorization in demonstrated behavior, not only declared capability. NSA default restricts untested agents automatically. |

### 8.1 Relationship to Agent Visibility and Control (AVC)

Paper [2] defines Agent Visibility and Control (AVC) as requiring three enterprise capabilities: identity record access, performance history access, and operational control authority. nanda-context-graph directly implements the second capability — performance history access — providing paginated decision history with timestamp, outcome, duration, and policy adherence metadata. The `GET /agents/<id>/history` endpoint is the AVC performance history API.

---

## 9. Related Work

### 9.1 NANDA Ecosystem

The NANDA Index architecture [1] provides the foundational discovery and identity layer upon which this work builds. The AgentFacts schema [4] establishes the self-describing, verifiable metadata format that nanda-context-graph extends. The NANDA Enterprise paper [2] identifies the regulatory and governance requirements — traceable event logging, AVC performance history, NSA risk mitigation — that motivate this work. Anthropic's MCP [9] and Google's A2A [10] provide the protocol substrates that nanda-context-graph instruments.

### 9.2 Comparison with Existing Observability Infrastructure

| Dimension | OpenTelemetry | LangSmith / Langfuse | nanda-context-graph |
|---|---|---|---|
| Primary question | What happened and how long? | What did the LLM output? | Why did this agent decide this? |
| Data model | Spans + metrics (tree) | LLM call traces (linear) | Property graph with typed edges |
| Causal reasoning | No | Partial (LLM I/O only) | Full (typed ReasoningStep, evidence refs) |
| Cross-agent causal chain | Via traceparent (timing only) | Not designed for multi-agent | PRECEDED_BY edge enables multi-hop traversal |
| Trust score integration | No | No | BTS feeds directly into ZTAA |
| Protocol native to NANDA | No | No | Extends AgentFacts, integrates with ZTAA and Index |
| Privacy modes | Not designed for this | Not designed for this | Public / Private / ZKP with DID access control |
| Query model | SQL-like (Tempo, Jaeger) | Key-value history | GraphQL + Cypher over property graph |
| GDPR jurisdiction gating | No | No | Jurisdiction-gated CRDT federation |

The key distinction: OTel and LangSmith answer observability questions about what a system did. nanda-context-graph answers accountability questions about why an agent decided, in a form that feeds back into trust decisions and enables audit, compliance, and governance across a decentralized multi-agent ecosystem.

### 9.3 Context Graph Concepts in the Broader Literature

Context graph concepts have been explored in the venture capital and research literature [5, 7, 8] as a "trillion-dollar opportunity" in agentic infrastructure. This work is the first to propose a protocol-native, NANDA-integrated implementation with federated graph storage, formal explainability APIs, and integration with a zero-trust authorization framework.

---

## 10. Federation Architecture

### 10.1 Current Implementation: LWW CRDT (Phase 5A)

Each `Decision` node carries a `last_updated_ms` timestamp. On federation sync, the node with the higher timestamp wins (`ON MATCH SET` in Neo4j MERGE). Trace IDs are UUID v7 (time-ordered, globally unique), making true write conflicts rare. The `GET /federation/traces?since_ms=0` endpoint exposes all traces since a given timestamp for pull-based sync. Push sync propagates new traces to configured `NCG_FEDERATION_PEERS` on ingest.

LWW provides a small window of potential data loss on true concurrent writes from two registries to the same trace. This is acceptable in Phase 5A because: (a) UUID v7 makes collision probability negligible, and (b) traces are typically owned by a single agent registered to a single primary registry.

### 10.2 Specified Design: Vector Clock CRDT (Phase 5B)

Each `Decision` node will carry a vector clock `{registry_id: logical_timestamp}`. Merge rule: take the component-wise maximum. For `ReasoningStep` lists: OR-Set CRDT semantics — steps are append-only; a step once added cannot be removed, only superseded by a new step with a `supersedes` pointer.

**Formal consistency invariant:**

For any two NCG registry nodes R₁ and R₂, after federation sync completes:
```
∀ trace t: (t ∈ R₁.traces ∨ t ∈ R₂.traces) ⟹ (t ∈ R₁'.traces ∧ t ∈ R₂'.traces)
```

All traces visible to either registry before sync are visible to both after sync. Vector clocks eliminate the LWW data-loss window while preserving the append-only guarantees required for audit integrity.

---

## 11. Implementation Roadmap

| Phase | Status | Deliverables | NANDA Integration |
|---|---|---|---|
| 1 — Schema & local graph | Complete | Pydantic v2 schemas, Neo4j adapter, CLI, unit tests, PyPI package | Standalone |
| 2 — Ingest & hooks | Complete | FastAPI ingest, VC validation, queue adapters, MCP middleware | AgentFacts extension, MCP shim |
| 3 — Query API + index | Complete | REST query API, nanda-index /behavior endpoint, NEST hooks | NANDA index integration |
| 4 — Docker + dashboard | Complete | docker-compose (5 services), React dashboard, 31 tests | Full local stack |
| 5A — Federation sync (LWW) | Complete | Push/pull sync, LWW via MERGE, federation endpoint, jurisdiction filter | Registry quilt (LWW) |
| 5B — Federation CRDT | In progress | Vector clocks on Decision nodes, OR-Set for ReasoningStep lists | Registry quilt (full CRDT) |
| 6 — Benchmarks & hardening | Planned | Load benchmarks at 10K+ agents, Dgraph sharding path, ZKP proof generation | Production path |

---

## 12. Conclusion and Future Work

nanda-context-graph addresses the critical accountability gap in the NANDA protocol ecosystem: the absence of a structured, decentralized system for capturing, storing, and querying agent decision traces. The Internet was built as a system of records. nanda-context-graph makes NANDA the system of decisions for the Agentic Era — the infrastructure layer that answers not just what agents do, but why, with cryptographic guarantees, across trust boundaries, in a decentralized ecosystem.

The design is protocol-native (zero breaking changes), privacy-preserving (three modes with DID access control and jurisdiction-gated federation), adoption-friendly (single MCP decorator), and open-source (Apache 2.0). The reference implementation is validated at thousands of concurrent agents with a clear architectural path to registry scale.

**Future work:**

- **Formal verification of CRDT protocol.** Prove the consistency invariant for the vector clock CRDT under network partition, using TLA+ or Coq.
- **ZKP proof generation implementation.** Implement Groth16 proofs over the ReasoningStep schema for full ZKP mode.
- **Behavioral anomaly model hardening.** Replace the z-score anomaly model with a trained isolation forest or autoencoder per agent class, improving detection of novel attack patterns.
- **W3C Verifiable Credential integration.** Integrate third-party trace attestation: allow a regulator or auditor to issue a VC certifying that a trace has been reviewed, linking the certification to the trace node in the graph.
- **Trillion-agent scale benchmarks.** Validate the Dgraph sharding architecture with synthetic load at 1M+ concurrent agents.
- **Graph-based threat detection.** Use the accumulated trace graph to detect multi-agent conspiracy patterns — chains of agents that collectively execute a prohibited action that no individual agent's trace would reveal.

As agentic AI scales from prototypes to production deployments, the ability to answer "why did this agent do that?" will transition from a nice-to-have to a regulatory and operational necessity. nanda-context-graph provides the infrastructure to answer that question — at scale, with cryptographic guarantees, across the decentralized Internet of AI Agents.

---

## References

[1] Raskar, R. et al. (2025). Beyond DNS: Unlocking the Internet of AI Agents via the NANDA Index and Verified AgentFacts. arXiv:2507.14263.

[2] Wang, S. et al. (2025). Using the NANDA Index Architecture in Practice: An Enterprise Perspective. arXiv:2508.03101.

[3] MIT Media Lab (2025). Project NANDA: Algorithms to Unlock the Internet of AI Agents. https://www.media.mit.edu/projects/mit-nanda/overview/

[4] Lambe, M. (2025). Deep Dive Project NANDA: Engineering AgentFacts v1.2. NANDA Community / Medium.

[5] Gupta, J. & Garg, A. (2025). AI's Trillion-Dollar Opportunity: Context Graphs. Foundation Capital Research.

[6] Shinde, A. (2025). NANDA: The Protocol for Decentralized AI Agent Collaboration. Medium.

[7] Pavlyshyn, V. (2026). Context Graphs and Data Traces: Building Epistemology Layers for Agentic Memory. Medium.

[8] Subramanya, N. (2025). Context Graphs: The Trillion-Dollar Evolution of Agentic Infrastructure. subramanya.ai.

[9] Anthropic (2024). Model Context Protocol (MCP). https://docs.anthropic.com/claude/docs/model-context-protocol

[10] Google Cloud (2025). Announcing the Agent2Agent (A2A) Protocol.

[11] W3C (2022). Decentralized Identifiers (DIDs) v1.0. https://www.w3.org/TR/did-core/

[12] W3C (2024). Verifiable Credentials Data Model v2.0. https://www.w3.org/TR/vc-data-model-2.0/

---

## Appendix: Summit Application — Suggested Form Responses

**Presentation title:** NANDA Context Graph: Making the Agentic Web a System of Decisions

**Presentation type:** Research Paper / Technical Talk

**Topic category:** Agent Infrastructure / Safety & Security / Governance & Compliance

**Abstract (≤300 words for form submission):**

The Internet era built systems of records. The Agentic Era demands a system of decisions. When autonomous AI agents negotiate contracts, route patient care, and approve financial exceptions across multi-hop delegation chains, the critical question is not what data was transferred — it is why each agent took each action. The NANDA protocol provides global agent discovery, cryptographic identity, and cross-protocol interoperability for the Internet of AI Agents. Yet its security layer explicitly requires "native identity, traceability, and behavioral records" — capabilities for which no open-source implementation exists.

This talk presents nanda-context-graph: a protocol-native decision trace layer that records every NANDA agent decision as a first-class, queryable, cryptographically verifiable object. Three contributions close the accountability gap: (1) an AgentFacts v1.2 extension that publishes trace endpoints in the NANDA Index; (2) four backward-compatible A2A headers enabling multi-hop causal chain reconstruction; and (3) a formal Behavioral Trust Score formula that grounds ZTAA authorization in demonstrated agent behavior, not only declared capability. The privacy architecture supports public, private, and zero-knowledge proof modes with jurisdiction-gated federation for GDPR compliance.

The reference implementation is working, open-source, and validated at thousands of concurrent agents. Attendees will see a live three-agent Claude demo where the approval agent queries its own prior decision history as evidence before deciding — a capability impossible in any flat observability tool. nanda-context-graph transforms NANDA from a discovery and routing system into a fully accountable, auditable, governable intelligence infrastructure — the foundation for deploying autonomous agents at enterprise and regulatory scale.

**Key takeaways:**
1. Why the decision trace gap is the critical missing layer in the NANDA stack — and in agentic AI infrastructure broadly.
2. The Behavioral Trust Score formula: how demonstrated agent behavior feeds into Zero Trust Agentic Access authorization.
3. A live demonstration of multi-hop causal chain reconstruction and the graph-native precedent query.
4. The privacy architecture: how three modes and jurisdiction-gated federation make NANDA trace infrastructure GDPR-ready.
5. Why the Internet of Agents must be a system of decisions, not only a system of records — and what infrastructure that requires.
