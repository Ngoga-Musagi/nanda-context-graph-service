# B4 — Clean-Agent Test Report & Deploy Runbook

**Service:** nanda-context-graph — queryable cross-agent decision memory
**Live URL:** https://nanda-context-graph-production.up.railway.app
**Store:** Neo4j Aura Free (durable) · **Ranking:** OpenAI `text-embedding-3-small` (hybrid, lexical fallback)
**Date:** 2026-07-03

---

## Part 1 — B4: does an agent succeed from SKILL.md alone?

**Test design.** A fresh agent was given **only** the text of [`SKILL.md`](../SKILL.md)
and the live Base URL — no access to this codebase, the seed data, or any prior
conversation. It had a shell with `curl`. It was handed one plain-English task and
told to complete it end-to-end with no human help, then report every real HTTP
call it made.

**Task given:**
> "A returning Gold-tier customer is asking for 15% off a 5-day ski-trip car
> rental. Decide whether to approve, consistent with how we've handled this
> before, and record your decision."

### What the agent did (the real loop)

1. **Health/status check** — `GET /health` → `{"status":"ok","store":"neo4j","decisions":31}`; `GET /` confirmed `store_backend=neo4j`, `precedent_ranking=embeddings`.
2. **Recalled precedent** — `POST /api/v1/precedent` with the situation text. Top hit:
   `seed-discount-001` (similarity **0.72**, outcome **failure**): *"Rejected: 15%
   exceeds the 10% auto-approval ceiling; routed to manager queue."* A supporting
   hit (`seed-discount-007`, a 9% **success**) confirmed the ceiling is 10%.
3. **Decided consistently** — did **not** auto-approve 15%; counter-offered the 10%
   ceiling and escalated the remainder to the manager queue, mirroring the precedent.
4. **Recorded the decision** — `POST /ingest/trace` with a unique id
   `b4-cleanagent-discount-20260703T154205Z`, four reasoning steps
   (retrieve policy → retrieve precedent → evaluate → decide), and
   `output={approved:false, counter_offer_pct:10, escalated_to:"manager-queue"}`,
   `outcome="failure"`.
5. **Confirmed persistence** — read it back via `GET /api/v1/trace/{id}` and
   `GET /api/v1/why?agent_id=discount-approval` (its trace was now the newest
   decision, i.e. available as precedent for the next agent).

### Independent verification (anti-fabrication)

The run was verified directly against Neo4j Aura (not trusting the agent's own
narration):

```
FOUND in Neo4j: trace_id=b4-cleanagent-discount-20260703T154205Z
  outcome: failure | steps: 4 | has_embedding: True
  inputs : {"request": "Returning Gold-tier customer wants 15% off a 5-day ski-trip car rental", "tier":"gold", ...}
  output : {"approved": false, "counter_offer_pct": 10, "escalated_to": "manager-queue", "reason": "15% exceeds 10% auto-approval ceiling"}
  total decisions in Aura: 32 (31 seed + 1 agent write)
```

### Result: **PASS**

A clean agent, from SKILL.md alone, recalled precedent → made a policy-consistent
decision → recorded a well-formed trace → confirmed it persisted, with **zero human
intervention**. The recorded decision became immediately available as precedent for
future agents — the core value loop of the service.

### SKILL.md improvements from the agent's feedback

The agent flagged real friction; the doc + service were iterated in response:

| Issue it raised | Fix |
|---|---|
| `/` banner said *"emit your trace first, then query"* — contradicts the correct read-first loop | Reworded to an `ordering` note: recall is available immediately (pre-seeded); only *your own* writes need to be POSTed first |
| The four `outcome` values were never defined; "failure" for a policy-correct rejection is counterintuitive | Added definitions, incl. the non-obvious point that `outcome` describes the disposition **for the requester**, not the agent's performance |
| `GET /trace/{id}` leaked the internal `embedding` array (~32 KB) and `precedent_text` | Read responses now strip internal persistence fields |
| Example showed `similarity: 0.91`; real top match was 0.72 | Documented that similarity is a **relative** ranking score (~0.6–0.8 is a strong match), trust order over the raw number |
| Unclear whether extra `inputs`/`output` keys are allowed | Documented that both are free-form JSON; extra keys are stored and returned as-is |

Service test suite remained green (19/19) after these changes.

---

## Part 2 — Deploy runbook (Railway + Aura + OpenAI)

Reproduce the live deployment from scratch. The service is a single container;
`schema/` + `service/` are the only runtime code (see `.dockerignore`).

### 0. Prerequisites
- **Railway CLI ≥ 5.x** — `npm i -g @railway/cli@latest`.
  ⚠️ CLI 4.x fails to create a project in the Personal workspace
  (*"please upgrade your CLI or pick another workspace"*). Upgrade first.
- A **Neo4j Aura Free** instance (URI + username + password).
  ⚠️ On Aura the **username is the instance id** (e.g. `0316f2ed`), **not** `neo4j`.
- An **OpenAI API key with billing/credits** (embeddings). Without credits the key
  returns `429 insufficient_quota`; the service still runs, falling back to lexical.

### 1. Create project & service
```bash
cd nanda-context-graph-service
railway login
railway init --name nanda-context-graph
railway service nanda-context-graph        # link the service
```

### 2. Set variables
Set the store + port via CLI (secrets can also go in the dashboard):
```bash
railway variables --set "NCG_NEO4J_URI=neo4j+s://<id>.databases.neo4j.io" \
                  --set "NCG_NEO4J_USER=<id>" \
                  --set "NCG_NEO4J_PASSWORD=<password>" \
                  --set "PORT=7200" --skip-deploys
```
Add `OPENAI_API_KEY` in the **Railway dashboard → service → Variables** (keeps the
secret out of shell history). Leaving it unset is fine — ranking falls back to lexical.

### 3. Deploy
```bash
railway up --service nanda-context-graph --ci
```
> The CLI log stream sometimes drops with `reqwest error: operation timed out`.
> That is only the stream — the build continues server-side. Check with
> `railway status` / `railway logs`.

### 4. Public domain
```bash
railway domain            # generates https://<service>-production.up.railway.app
```

### 5. Verify
```bash
BASE="https://<service>-production.up.railway.app"
curl -s "$BASE/health"                      # {"status":"ok","store":"neo4j",...}
curl -s "$BASE/" | python -m json.tool      # store_backend=neo4j, precedent_ranking=embeddings
curl -s -X POST "$BASE/api/v1/precedent" -H "Content-Type: application/json" \
  -d '{"query":"Gold member wants 15% discount on a ski trip rental","k":3}'
```
Finally, put `$BASE` into `SKILL.md` (it ships with the live URL already).

### Gotchas hit during this deployment (and fixes)

| Symptom | Cause | Fix |
|---|---|---|
| `init` → *"Unable to create project in Personal workspace"* | Old CLI (4.6.3) | Upgrade to CLI 5.x |
| Healthcheck fails; logs show `Invalid value for '--port': '$PORT' is not a valid integer` | `railway.json` `startCommand` is run **without a shell**, so `$PORT` never expands | Remove `startCommand`; let the Dockerfile `CMD ["sh","-c","… ${PORT:-7200}"]` expand it, and set `PORT=7200` |
| Banner shows `store_backend: memory` despite Aura vars set | Aura Free **auto-paused** after ~3 idle days (hostname stops resolving in DNS) | Resume the instance at console.neo4j.io until *Running*, then `railway redeploy`; the service's 4-min keep-warm ping holds it while up |
| Precedent stuck on `lexical`; logs show `429 insufficient_quota` | OpenAI key has no credits/billing | Add billing to the key (or use a funded one); redeploy — ranking flips to `embeddings` |

### Design note: graceful degradation
The service **never hard-fails**: if Aura is unreachable it uses an in-memory store
(re-seeded on boot), and if the embedding key is missing/over-quota it uses a
deterministic lexical ranker. Both fallbacks were observed live before the full
Aura + embeddings configuration came up green — the endpoints stayed correct
throughout.
