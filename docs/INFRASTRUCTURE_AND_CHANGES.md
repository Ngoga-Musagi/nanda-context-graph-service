# Existing Infrastructure vs. NANDA Context Graph Changes

This document is the honest, file-level answer to two questions:

1. **What already existed** in the NANDA ecosystem (adapter, nanda-index, NEST).
2. **Exactly what nanda-context-graph (NCG) changed** in those repos to make
   decision tracing work — and why every change is **opt-in and non-breaking**.

> **The one rule:** every NCG hook is gated on the `NCG_INGEST_URL` environment
> variable. If it is unset, all the code below is a transparent pass-through and
> the agents behave exactly as they did before. Nothing is mandatory.

---

## 1. The existing infrastructure (unchanged in spirit)

| Component | Repo | Role | Key entry points (pre-existing) |
|---|---|---|---|
| **Adapter** | `projnanda/adapter` | Wraps any agent as an A2A bridge | `NANDA(improvement_fn)`, `bridge.improve_message_direct()`, `send_to_agent()`, `run_server()` on port 6000 |
| **nanda-index** | `projnanda/nanda-index` | Discovery "phone book" `@id → URL` | Flask `POST /register`, `GET /lookup/<id>`, port 6900, `TEST_MODE=1` for in-memory |
| **NEST** | `projnanda/NEST` | A2A agent framework w/ telemetry | `SimpleAgentBridge.handle_message()`, `AgentInterface.process_message()`, `telemetry/` |
| **NCG** (new) | `nanda-context-graph` | Decision-trace graph | `POST /ingest/trace` (7200), query API (7201), Neo4j |

**How they already talk to each other (no NCG involved):**

```
user/terminal ──"@agent-b msg"──► adapter bridge A ──lookup──► nanda-index
                                         │
                                         └──A2A POST /a2a──► agent bridge B
```

The adapter already injected/forwarded A2A messages, already looked agents up in
the index, and NEST already had a `telemetry/` package with a metrics/health
system. **NCG did not introduce A2A, discovery, or telemetry — it rides on them.**

---

## 2. What NCG added to the **adapter**

### 2.1 `nanda_adapter/core/nanda.py`

| Addition | What it does | Breaking? |
|---|---|---|
| `NCG_INGEST_URL` module global | Read once at import; the master on/off switch | No — `None` ⇒ disabled |
| `_emit_trace(trace)` | Fire-and-forget `POST /ingest/trace` on a daemon thread; swallows all errors | No — never blocks or crashes the agent |
| `traced_call(fn, msg, agent_id, parent_trace_id)` | Wraps the agent's improver, builds a `DecisionTrace`, emits it. Transparent pass-through when `NCG_INGEST_URL` is unset | No |
| `register_custom_improver()` wrap | When tracing is on, registers the improver wrapped in `traced_call`; otherwise registers it raw | No |

**Three correctness fixes made while building the distributed demo** (all still
opt-in, all in the NCG-added code path):

- **`sys.path` includes the package `core/` dir before import.** `agent_bridge.py`
  does `from mcp_utils import ...` (absolute). Under a package import that raised
  `ImportError`, sending `nanda.py` into a fallback that loaded a **second,
  top-level `agent_bridge` module**. Two module instances meant two separate
  thread-locals, so the trace context set in one was invisible in the other.
  Forcing the relative import to succeed collapses it to **one** module instance.
- **`traced_call` uses the star-imported `set_trace_context`** (the same module
  the running bridge uses) instead of a fresh relative import.
- **The trace context is no longer cleared in `traced_call`'s `finally`.** The
  bridge may `send_to_agent` *after* the improver returns; that delegation hop
  needs `get_current_trace_id()` to still be live so it can stamp the outgoing
  A2A headers. It is overwritten at the start of the next call, so it stays
  scoped to the request.

### 2.2 `nanda_adapter/core/agent_bridge.py`

| Addition | What it does | Breaking? |
|---|---|---|
| `_trace_ctx` (thread-local) + `get_current_trace_id()` / `set_trace_context()` | Carries the active trace id across the request so delegation can reference it | No — inert unless set |
| Header injection in `send_to_agent()` | When a trace is active, adds `x-trace-id`, `x-parent-trace`, `x-reason` to the outgoing A2A metadata | No — only adds fields; old indexes/agents ignore them |
| `trace` sub-document in `register_with_registry()` | Adds `trace.endpointURL` to the registration payload when `NCG_INGEST_URL` is set | No — index stores it via `data.get('trace', {})`; old index ignores it |

**Fix made for real cross-process chaining:** `send_to_agent` now sets
`x-parent-trace = this agent's own trace_id` (the delegator). Previously it
forwarded *this* agent's parent (effectively the grandparent), so the downstream
agent's trace did not link to the immediate caller. With the fix, the receiver's
trace links back to the exact decision that delegated to it.

---

## 3. What NCG added to **NEST**

### 3.1 `nanda_core/telemetry/trace_collector.py`  — **NEW FILE**

A small singleton, activated only when `NCG_INGEST_URL` is set:

- `before_call(agent_id, conversation_id, message, parent_trace_id)` — opens a
  trace skeleton keyed on NEST's existing `conversation_id`, returns a `trace_id`.
- `after_call(conversation_id, response, outcome)` — finalizes and fire-and-forget
  emits the `DecisionTrace`.
- When `NCG_INGEST_URL` is unset, **every method is a silent no-op.**

This is the bridge between NEST's A2A lifecycle and NCG. It deliberately reuses
NEST's own `conversation_id` as the trace correlation key (`a2a_msg_id`).

### 3.2 `nanda_core/core/agent_bridge.py`

| Addition | What it does | Breaking? |
|---|---|---|
| Import `trace_collector` (guarded) | `try/except` import; `None` if unavailable | No |
| `handle_message()` wraps `before_call` / `after_call` | Reads `x-parent-trace` from incoming metadata, traces the call, records outcome (`success` / `delegated` / `error`) | No — all in `try/except`, never crashes |
| `_ncg_after_call()` helper | Centralized, exception-swallowing finalizer | No |
| Header injection in `_send_to_agent()` | When tracing is active, stamps `x-parent-trace = current trace_id` on outgoing A2A so a delegating NEST agent also chains | No — additive metadata |

**The NEST receive path already reasons** (`process_message`) and now traces on
receive — which is what makes it the natural "specialist" in the distributed
demo: it links its trace back to whatever called it via `x-parent-trace`.

---

## 4. What NCG added to **nanda-index**

`registry.py` `POST /register` stores an optional `trace` sub-document:

```python
registry['agent_status'][agent_id] = {
    ...,
    'trace': data.get('trace', {}),   # ← NCG; absent in old payloads ⇒ {}
}
```

No schema enforcement, no new required fields. Old agents that don't send `trace`
register exactly as before. (A `GET /agents/<id>/behavior` proxy endpoint is
specified in the RFC but is additive and not required by the demos.)

---

## 5. The end-to-end picture (what the distributed demo proves)

```
 nanda-index :6900 (TEST_MODE)  ── discovery, unchanged API ──┐
        ▲ register / lookup                                   │
 broker (REAL adapter :6000) ──A2A──► specialist (REAL NEST :6100) ──A2A──► approver (REAL NEST :6200)
   traced_call → trace#1                process_message → trace#2             process_message → trace#3
   x-parent-trace = #1                  x-parent-trace = #2                   parent_trace_id = #2
        └──────────────── POST /ingest/trace :7200 → NCG / Neo4j ◄───────────────┘
                          chain(#3) = [#3, #2, #1]   ✅ 3-hop chain
```

- **Three OS processes**, **two frameworks** (adapter + NEST), **real HTTP A2A**.
- The pricing specialist consults the approver *as part of its own reasoning* —
  a real synchronous sub-delegation, not a scripted relay.
- No agent's business logic was modified — only `NCG_INGEST_URL` was set.
- The traces form ONE verified causal chain across two network hops.

Run it: `python run_distributed_demo.py` (NCG stack must be up first), or
`python run_demo.py` which finishes on this as **Phase 6**.

### 5.1 Two robustness fixes inside NCG (the store), surfaced by going distributed

Real distribution exposed two latent bugs in `store/neo4j_adapter.py` that the
earlier in-process demos never hit. Both are now fixed:

- **Out-of-order parent (MERGE, not MATCH).** The `PRECEDED_BY` edge used
  `MATCH (p:Decision {trace_id:$parent})`, which silently dropped the edge when a
  **child trace arrived before its parent** — exactly what happens with
  synchronous nested delegation (the callee finishes and emits first) or any
  out-of-order arrival on the wire. Now it `MERGE`s the parent stub, so the edge
  is always created; the parent's real data fills in when its own trace lands.
- **Uniqueness constraints (correctness, not just speed).** There were **no**
  constraints on `Decision.trace_id` / `Agent.agent_id` / `Step.step_id`. Without
  them, two `MERGE`s that race (or the stub-then-real sequence above) created
  **duplicate nodes** with the same key, splitting the causal chain across copies
  so traversal stopped short. `Neo4jAdapter.ensure_schema()` now creates these
  constraints on startup, making `MERGE` lock on the key — true idempotency, the
  property the federation design already assumed.

---

## 6. Bring-your-own-agent — the integration contract

To put **any** agent into this graph, a developer touches **none** of the code
above. They only:

1. Set `NCG_INGEST_URL=http://<ncg-host>:7200` and a unique `AGENT_ID`.
2. Wrap their logic in `NANDA(fn)` (adapter) **or** implement
   `AgentInterface.process_message` (NEST).
3. Register in the index (automatic for the adapter when `PUBLIC_URL` is set;
   a one-line `POST /register` for NEST).
4. Provide an `ANTHROPIC_API_KEY` **only if their agent reasons with Claude** —
   tracing itself never needs a key.

Everything else (trace emission, header propagation, causal linking, graph
storage) happens automatically.
