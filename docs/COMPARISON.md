# Comparison with Existing Observability Infrastructure

This document explains why nanda-context-graph exists as a standalone system rather than an extension of OpenTelemetry, LangSmith, or other existing observability tools.

---

## Comparison Table

| Dimension | OpenTelemetry | LangSmith / Langfuse | nanda-context-graph |
|---|---|---|---|
| **Data model** | Spans + metrics (tree, not graph) | LLM call traces (linear chain) | Property graph with typed edges |
| **Causal reasoning** | No — OTel captures timing, not why | Partial — captures LLM I/O | Yes — ReasoningStep captures thought, confidence, evidence |
| **Cross-agent causal chain** | Via traceparent header (timing only) | Not designed for multi-agent | x-trace-id + PRECEDED_BY edge enables multi-hop reconstruction |
| **Trust score integration** | No | No | ZTAA Behavioral Trust Score (BTS) fed from trace history |
| **Protocol native** | No NANDA integration | No NANDA integration | Extends AgentFacts, integrates with ZTAA, federated via NANDA registry quilt |
| **Privacy modes** | Not designed for this | Not designed for this | Public / private / ZKP with DID-based access control |
| **Query model** | SQL-like (Tempo, Jaeger) | Key-value history | REST/GraphQL over property graph — answers `why(agent, action)` |
| **Identity model** | Service name (string) | API key scoped | NANDA DID — cryptographic identity that persists across sessions |
| **Federation** | Collector → backend (centralized) | SaaS (centralized) | CRDT-based peer-to-peer sync between registry graph stores |

---

## Why a Property Graph?

A flat event log (OpenTelemetry, CloudWatch, Splunk) can answer: "what happened at timestamp T?"

A property graph can answer: **"why did event E cause event F, and what is the shortest causal path from human authorization H to outcome O?"**

### 1. Multi-hop causal traversal

Follow `PRECEDED_BY` edges from any terminal decision back to the root authorization. Flat logs require time-order joins across multiple systems; the graph makes this a single query.

```cypher
MATCH p = (d:Decision {trace_id: $trace_id})-[:PRECEDED_BY*]->(root)
WHERE NOT (root)-[:PRECEDED_BY]->()
RETURN [n IN nodes(p) | n.trace_id] AS causal_chain
```

### 2. Precedent lookup

Find prior approved exceptions matching a pattern. This is a graph pattern match, not a text search.

```cypher
MATCH (d:Decision {outcome: 'success'})-[:DECIDED_BECAUSE]->(s:Step {tool_name: 'policy-check'})
WHERE s.thought CONTAINS 'exception'
RETURN d.trace_id, s.thought, d.timestamp_ms
ORDER BY d.timestamp_ms DESC
LIMIT 10
```

### 3. Counterfactual replay

Re-execute a decision subgraph with modified inputs. The graph structure defines the execution order via edge traversal; a flat log has no such structure.

```cypher
MATCH (d:Decision {trace_id: $trace_id})-[:DECIDED_BECAUSE]->(s:Step)
RETURN d, collect(s) AS steps
// Client re-executes with modified inputs and compares outcomes
```

### 4. Behavioral pattern detection

Find all agents with high delegation rates — a graph pattern that has no flat-log equivalent.

```cypher
MATCH (a:Agent)-[:MADE_BY]-(d:Decision)-[:DECIDED_BECAUSE]->(s:Step {step_type: 'delegate'})
WITH a, count(d) AS total_decisions, count(s) AS delegations
WHERE delegations > total_decisions * 0.5
RETURN a.agent_id, total_decisions, delegations,
       round(toFloat(delegations) / total_decisions * 100, 1) AS delegation_pct
ORDER BY delegation_pct DESC
```

---

## Why Not Extend OpenTelemetry?

Three structural reasons make extending OTel insufficient for the NANDA decision trace use case:

### 1. Data model mismatch

OpenTelemetry captures spans and metrics: timing data organized as trees. A NANDA agent decision is not a tree — it is a **graph**. A single `ReasoningStep` references multiple evidence sources, multiple policy URNs, and may have multiple causal predecessors. The `DECIDED_BECAUSE` edge in a property graph is a first-class relationship that enables multi-hop graph traversal. In OTel, this relationship would need to be reconstructed from string attributes, losing queryability.

### 2. No trust score feedback loop

LangSmith and Langfuse are excellent tools for debugging LLM calls, but they have no concept of an **agent identity that accumulates a behavioral reputation across sessions**. The ZTAA Behavioral Trust Score requires a queryable history of an agent's success rate, policy adherence, anomaly patterns, and delegation depth — none of which OTel or LangSmith are designed to produce. The BTS formula (`BTS = 0.35*S_success + 0.30*S_policy + 0.25*(1-S_anomaly) + 0.10*S_depth`) is structurally impossible to compute from OTel data because OTel has no concept of agent-scoped behavioral aggregation.

### 3. No protocol-native integration

Existing observability tools operate independently of the NANDA stack. They do not extend AgentFacts, do not integrate with ZTAA, and do not federate via the NANDA registry quilt. An enterprise deploying NANDA agents would need to operate two parallel infrastructure stacks — one for discovery and routing, one for audit — with no native integration between them. nanda-context-graph closes this gap by:

- Publishing trace endpoints in the NANDA Index via AgentFacts v1.2
- Feeding BTS into the ZTAA authorization framework
- Federating trace data via the same registry quilt model NANDA uses for agent discovery

---

## The Key Argument

OTel and LangSmith answer: **"what happened and how long did it take?"**

nanda-context-graph answers: **"why did this agent make this decision, and can I trust the next decision it makes?"**

The graph model is not an aesthetic choice — it is required because causal reasoning is inherently relational. A `DECIDED_BECAUSE` edge is not a span attribute; it is a first-class relationship that enables graph traversal, precedent lookup, and counterfactual replay. The ZTAA trust score feedback loop is structurally impossible in OTel because OTel has no concept of an agent identity that accumulates behavioral history across sessions.
