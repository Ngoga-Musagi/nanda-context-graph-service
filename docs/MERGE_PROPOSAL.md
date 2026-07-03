# Merge Proposal — nanda-context-graph → projnanda

**To:** NANDA maintainers (Ramesh Raskar, Writing Group)
**From:** Alexis Ngoga
**Re:** Adding a causal decision-trace layer to the NANDA Internet of Agents
**Status:** Ready for review · all changes opt-in and non-breaking

---

## 1. One-line ask

Merge **nanda-context-graph (NCG)** into `projnanda` as a new repo, plus three tiny,
opt-in PRs to `adapter`, `NEST`, and `nanda-index`, so any NANDA agent can become
**causally traceable** without changing how it is built, modelled, or routed.

## 2. The gap NCG fills — "causal tracing"

The existing stack solves identity, discovery, and messaging. None of it answers
the one question that matters when an agent network misbehaves:

> **"What reasoning produced this action, and what caused what across agents?"**

| Repo | Answers |
|---|---|
| **adapter** | How do I expose my agent and route `@agent` messages? |
| **nanda-index** | Where does `@agent-id` live? (the phone book) |
| **NEST** | How do I run a reasoning agent with telemetry? |
| **NCG (new)** | **What did each agent decide, why, and what caused what?** |

Today, when agent A delegates to B and B consults C, the decision chain lives in
**scattered per-process logs that never join up**. NCG is the missing layer: it
records each agent's reasoning as a node and links cause→effect across processes
into **one queryable graph** — explainability and auditability for the Internet of
Agents.

## 3. The one rule that makes this safe to accept

> **Every NCG hook is gated on the `NCG_INGEST_URL` environment variable.
> Unset = transparent pass-through. The agent behaves exactly as before.**

Nothing is mandatory, nothing blocks the request path (all emission is
fire-and-forget on a daemon thread, all wrapped in `try/except`), and old agents
that send no trace data register and talk exactly as they do now.

## 4. What each PR changes (small, isolated, reviewable)

These are **four separate git repos**, so this is **not** one big commit — it is one
new repo + three small PRs each reviewed by its own maintainer.

| Target repo | Files | Change | Breaking? |
|---|---|---|---|
| **nanda-context-graph** | (new repo) | The ingest API, query API, Neo4j store, dashboard | n/a — new |
| **projnanda/adapter** | `core/nanda.py`, `core/agent_bridge.py` | `traced_call()` wrapper around the improver; thread-local trace context; `x-trace-id`/`x-parent-trace`/`x-reason` headers on delegation; optional `trace` sub-doc in registration | No |
| **projnanda/NEST** | `telemetry/trace_collector.py` (new), `core/agent_bridge.py` | `before_call`/`after_call` around `handle_message`, keyed on the existing `conversation_id`; reads/stamps `x-parent-trace` | No |
| **projnanda/nanda-index** | `registry.py` | `POST /register` stores optional `trace` sub-doc via `data.get('trace', {})` | No |

Per-repo `CHANGES_FOR_NCG.md` files are staged as the PR descriptions; full
file-level detail is in [`INFRASTRUCTURE_AND_CHANGES.md`](./INFRASTRUCTURE_AND_CHANGES.md).

## 5. Model-agnostic by design (bring any model — or none)

**Tracing is orthogonal to reasoning. NCG never calls an LLM and never needs an
API key.** The `ANTHROPIC_API_KEY` in the demos is used only by the *example
agent's own reasoning* — not by any tracing code.

- The `DecisionTrace` schema has **no model field**: it stores `agent_id`,
  `inputs`, `steps` (`thought`, `tool_name`, `outcome`), `output`, `timestamp` —
  plain JSON produced by *whatever* reasoned.
- A **Gemini**, **GPT**, rule-based, or human-in-the-loop agent registers and
  traces identically to a Claude agent. Swap `anthropic` for
  `google-generativeai` and **nothing in NCG changes**.
- Registration requires only `agent_id` + `agent_url`; the `trace` sub-doc is
  optional.

## 6. How a trace works (and how continuing conversations are handled)

Each time an agent handles a message it reasons once → that is **one decision** →
**one `trace_id`** (a fresh UUID per call). The graph:

```
(:Agent)◄─MADE_BY─(:Decision {trace_id, outcome, ts})─DECIDED_BECAUSE─►(:Step {thought, tool})
(:Decision)─PRECEDED_BY─►(:Decision)     ← cross-process causal chain via x-parent-trace
```

**Continuing conversations:** the conversation thread is carried as `a2a_msg_id`
(NEST's existing `conversation_id`) and stays constant across turns; each *turn*
gets its own `trace_id`. So "everything in conversation X" groups by `a2a_msg_id`,
while "what caused this decision" follows `PRECEDED_BY`. This is the correct
granularity — a conversation is a thread of many decisions, not one.

**Concurrency:** the NEST collector keys in-flight traces by `conversation_id`
using a **LIFO stack**, so nested or concurrent calls sharing one conversation
each keep their own `trace_id` and don't overwrite one another.

## 7. Proof it works — real distributed demo

Three OS processes, two frameworks, real HTTP A2A, zero business-logic changes:

```
broker (REAL adapter :6000) ─A2A─► pricing (REAL NEST :6100) ─A2A─► approval (REAL NEST :6200)
   trace#1                          trace#2                          trace#3
        └──────────── POST /ingest/trace :7200 → Neo4j ────────────┘
   query: chain(approval) = [approval, pricing, broker]   ✅ verified 3-hop causal chain
```

Run: `python run_demo.py` (Phase 6 is this distributed proof). Regression-free:
adapter integration 11/11, NEST 12/12, query API pass, distributed 3-hop pass.

## 8. What we ask the maintainers to approve

1. Transfer/fork **nanda-context-graph** into `projnanda`.
2. Merge the three opt-in PRs (adapter, NEST, nanda-index).
3. Agree the **`NCG_INGEST_URL` opt-in contract** as the standard integration seam
   for future tracing-aware components.

No existing behavior changes until an operator deliberately sets `NCG_INGEST_URL`.
