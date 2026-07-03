# PR: Opt-in trace collector for A2A lifecycle (nanda-context-graph)

**Target repo:** `projnanda/NEST`
**Files touched:** `nanda_core/telemetry/trace_collector.py` (new),
`nanda_core/telemetry/__init__.py`, `nanda_core/core/agent_bridge.py`
**Breaking:** No — entirely gated on the `NCG_INGEST_URL` env var (unset = no-op)

## Summary
Adds a small telemetry collector that emits a `DecisionTrace` to
[nanda-context-graph](https://github.com/projnanda/nanda-context-graph) around
each A2A message a NEST agent handles, reusing NEST's existing `conversation_id`
as the correlation key. When `NCG_INGEST_URL` is unset, every method is a silent
no-op.

## Motivation
NEST already has a `telemetry/` package and a `conversation_id` on every A2A
message, but nothing records the *reasoning* behind a response or links it to the
agent that called it. This collector bridges NEST's existing call lifecycle to the
decision graph with no new concepts and no business-logic change.

## Changes
### `nanda_core/telemetry/trace_collector.py` — NEW
A singleton, active only when `NCG_INGEST_URL` is set:
- `before_call(agent_id, conversation_id, message, parent_trace_id)` — opens a
  trace skeleton, returns a fresh `trace_id`.
- `current_trace_id(conversation_id)` — the innermost in-flight `trace_id`, used to
  stamp `x-parent-trace` on outgoing delegations.
- `after_call(conversation_id, response, outcome)` — finalizes and fire-and-forget
  emits the `DecisionTrace`.
- Internally keyed by `conversation_id` → **LIFO stack** of in-flight skeletons,
  so nested/concurrent calls sharing one `conversation_id` don't overwrite each
  other (each turn still gets its own `trace_id`; the conversation id is preserved
  as `a2a_msg_id`).

### `nanda_core/core/agent_bridge.py`
- Guarded `try/except` import of `trace_collector` (`None` if unavailable).
- `handle_message()` reads `x-parent-trace` from incoming metadata, calls
  `before_call` / `after_call`, records outcome (`success` / `delegated` / `error`)
  — all in `try/except`, never crashes the handler.
- `_send_to_agent()` stamps `x-parent-trace = current_trace_id(...)` on outgoing
  A2A so a delegating NEST agent extends the causal chain one more hop.

### `nanda_core/telemetry/__init__.py`
- Exports `trace_collector`.

## Testing
- NEST integration proof: 12/12 (`examples/nest_integration_demo.py` in NCG),
  including the assertion that `_pending` drains after each call.
- Standalone collector unit check: push/pop/`current_trace_id`/drain, plus the
  nested-same-`conversation_id` case (LIFO) — all pass.
- Real 3-process distributed demo: NEST agents as pricing + approval nodes,
  verified chain `[approval, pricing, broker]`.
- With `NCG_INGEST_URL` unset: collector is a no-op; existing flows unaffected.

## Reviewer notes
- One rule for the whole PR: **everything is gated on `NCG_INGEST_URL`.**
- The collector deliberately reuses `conversation_id` as `a2a_msg_id` — no new
  identifier is introduced.

## Checklist
- [x] No new required fields or env vars
- [x] No behavior change when `NCG_INGEST_URL` is unset
- [x] All emission is fire-and-forget and exception-swallowing
- [x] Backward-compatible A2A metadata (additive headers only)
