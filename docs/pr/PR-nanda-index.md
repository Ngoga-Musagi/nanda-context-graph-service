# PR: Store optional trace sub-document on register (nanda-context-graph)

**Target repo:** `projnanda/nanda-index`
**Files touched:** `registry.py`
**Breaking:** No — optional field, read via `data.get('trace', {})`

## Summary
Lets `POST /register` persist an optional `trace` sub-document so the index can
advertise *which* agents emit decision traces to
[nanda-context-graph](https://github.com/projnanda/nanda-context-graph). Agents
that send no `trace` register exactly as before.

## Motivation
Discovery already maps `@agent-id → URL`. With one optional field, the index can
also tell a consumer where an agent's trace endpoint lives, enabling
behavior/explainability lookups without any new required schema.

## Changes
### `registry.py`
- In the `POST /register` handler, store the optional sub-document:
  ```python
  registry['agent_status'][agent_id] = {
      ...,
      'trace': data.get('trace', {}),   # absent in old payloads ⇒ {}
  }
  ```
- No schema enforcement, no new required fields, no change to existing fields.

## Testing
- Existing registrations (no `trace`) store `{}` and behave identically.
- Registrations including `trace.endpointURL` round-trip through `/register` and
  are visible on the stored agent document.
- Exercised end-to-end by the NCG distributed demo, where adapter + NEST agents
  register with a `trace` sub-document against a local `TEST_MODE=1` index.

## Reviewer notes
- The `trace` sub-document is **not** propagated through switchboard/federation
  lookups (out of scope here); only stored on the local agent record.

## Checklist
- [x] No new required fields
- [x] Old payloads (no `trace`) unchanged
- [x] No schema-validation changes
