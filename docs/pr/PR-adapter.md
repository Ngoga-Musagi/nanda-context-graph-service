# PR: Opt-in decision tracing hook (nanda-context-graph)

**Target repo:** `projnanda/adapter`
**Files touched:** `nanda_adapter/core/nanda.py`, `nanda_adapter/core/agent_bridge.py`
**Breaking:** No — entirely gated on the `NCG_INGEST_URL` env var (unset = pass-through)

## Summary
Adds an optional hook so an adapter agent can emit a `DecisionTrace` to the
[nanda-context-graph](https://github.com/projnanda/nanda-context-graph) ingest
service after it reasons, and propagate a causal-link header when it delegates to
another agent over A2A. When `NCG_INGEST_URL` is unset, none of this code runs and
the adapter behaves exactly as today.

## Motivation
The adapter makes agents discoverable and interoperable, but there is no record of
*why* an agent produced an action or *what caused what* across a delegation chain.
This hook lets the ecosystem reconstruct that causal chain — without changing any
agent's business logic.

## Changes
### `nanda_adapter/core/nanda.py`
- `NCG_INGEST_URL` module global — the master on/off switch (read once at import).
- `_emit_trace(trace)` — fire-and-forget `POST /ingest/trace` on a daemon thread;
  swallows all errors so it can never block or crash the agent.
- `traced_call(fn, msg, agent_id, parent_trace_id)` — wraps the agent's improver,
  builds a `DecisionTrace`, emits it. Transparent pass-through when tracing is off.
- `register_custom_improver()` registers the improver wrapped in `traced_call`
  only when tracing is enabled; otherwise registers it raw.

### `nanda_adapter/core/agent_bridge.py`
- Thread-local trace context: `get_current_trace_id()` / `set_trace_context()`
  (inert unless set).
- `send_to_agent()` adds `x-trace-id`, `x-parent-trace`, `x-reason` to the
  outgoing A2A metadata when a trace is active — old indexes/agents ignore them.
- `register_with_registry()` adds an optional `trace` sub-document to the
  registration payload when `NCG_INGEST_URL` is set.

### Correctness fixes (in the NCG-added path, surfaced by a real distributed demo)
- Ensure the package `core/` dir is on `sys.path` before import so the relative
  `agent_bridge` import succeeds and only **one** module instance (one
  thread-local) is loaded. Previously a fallback loaded a second top-level
  `agent_bridge`, so the trace context set in one was invisible in the other.
- `traced_call` uses the star-imported `set_trace_context` (same module the
  running bridge uses), not a fresh relative import.
- Do **not** clear the trace context in `traced_call`'s `finally`: a delegation
  hop can happen *after* the improver returns and needs the trace id live. It is
  overwritten at the start of the next call, so it stays request-scoped.
- `send_to_agent` sets `x-parent-trace = this agent's own trace_id` (the
  delegator), so the receiver links to its immediate caller (previously it
  forwarded the grandparent and the chain didn't link).

## Testing
- Adapter integration proof: 11/11 (`examples/adapter_integration_demo.py` in NCG).
- Real 3-process distributed demo: verified cross-process causal chain
  `[approval, pricing, broker]`.
- With `NCG_INGEST_URL` unset: no behavior change (existing flows unaffected).

## Reviewer notes
- One rule for the whole PR: **everything is gated on `NCG_INGEST_URL`.**
- Unrelated pre-existing item a reviewer may notice: `agent_bridge.py` has a
  hardcoded `SMITHERY_API_KEY` fallback (not introduced here). Flagging for
  awareness; can be addressed separately.

## Checklist
- [x] No new required fields or env vars
- [x] No behavior change when `NCG_INGEST_URL` is unset
- [x] All emission is fire-and-forget and exception-swallowing
- [x] Backward-compatible A2A metadata (additive headers only)
