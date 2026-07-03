# Deploying the decision-memory service

Single container, single public URL. Store = **Neo4j Aura Free**; ranking =
**OpenAI embeddings** (with a lexical fallback so it runs even without a key).

The service needs **no Anthropic key**. It needs (optionally) a Neo4j Aura
instance and (optionally) an OpenAI key — it boots and serves with neither
(in-memory store + lexical ranking), but production uses both.

---

## 1. Neo4j Aura Free (the store)

1. Go to <https://console.neo4j.io> → sign in → **Create instance** → **AuraDB Free**.
2. When it finishes provisioning it shows the **connection URI**
   (`neo4j+s://<dbid>.databases.neo4j.io`) and a generated **password** —
   *copy the password now, it's shown only once* (or download the credentials file).
3. You now have: `NCG_NEO4J_URI`, `NCG_NEO4J_USER` (=`neo4j`), `NCG_NEO4J_PASSWORD`.

> Aura Free auto-pauses after ~3 idle days. The service pings it every 4 minutes
> (`_keepwarm`) to keep it from idling, and re-seeds on every boot, so a cold
> demo always has data.

## 2. OpenAI key (the precedent quality engine)

1. <https://platform.openai.com> → **API keys** → **Create new secret key**.
2. That's `OPENAI_API_KEY`. Embeddings use `text-embedding-3-small`
   (~$0.02 / 1M tokens — a few cents covers the whole demo).

> No key? The service still works — precedent falls back to deterministic lexical
> ranking. The `/` banner shows `"precedent_ranking": "embeddings"` once a key is set.

## 3. Railway (the host)

### Option A — Railway CLI (no GitHub needed)
```bash
npm i -g @railway/cli      # or: brew install railway
railway login              # opens a browser
cd nanda-context-graph-service
railway init               # create a new project
railway up                 # builds the Dockerfile and deploys
```
Then set variables (Railway dashboard → your service → **Variables**, so secrets
stay out of your shell history):
```
NCG_NEO4J_URI=neo4j+s://<dbid>.databases.neo4j.io
NCG_NEO4J_USER=neo4j
NCG_NEO4J_PASSWORD=<aura-password>
OPENAI_API_KEY=<openai-key>
```
Then **Settings → Networking → Generate Domain** to get the public URL.

### Option B — GitHub + Railway dashboard
1. Push this directory to a GitHub repo.
2. Railway → **New Project → Deploy from GitHub repo** → pick it.
3. It auto-detects `Dockerfile` / `railway.json`.
4. Add the four variables above; generate a domain.

### Verify
```bash
BASE="https://<your-app>.up.railway.app"
curl -s "$BASE/health"
curl -s "$BASE/" | python -m json.tool   # expect store_backend=neo4j, precedent_ranking=embeddings
curl -s -X POST "$BASE/api/v1/precedent" -H "Content-Type: application/json" \
  -d '{"query":"Gold member wants 15% discount on a ski trip rental","k":3}'
```

Finally, put `$BASE` into `SKILL.md` (replace `https://REPLACE-WITH-LIVE-URL`).

---

## Alternates

### Render
- New → **Web Service** → connect repo (or **Deploy an existing image**).
- Environment: **Docker**. Render injects `$PORT` (the Dockerfile honors it).
- Add the same env vars. Free tier spins down on inactivity (~30–60s cold start) —
  fine, but note it in the demo. Health check path: `/health`.

### Fly.io
```bash
fly launch --no-deploy          # detects the Dockerfile, writes fly.toml
fly secrets set NCG_NEO4J_URI=... NCG_NEO4J_USER=neo4j NCG_NEO4J_PASSWORD=... OPENAI_API_KEY=...
fly deploy
```
Set the `fly.toml` internal port to `7200` (or rely on `$PORT`). Bump the VM to
512 MB if needed.

---

## Local run (no accounts)
```bash
uv venv --python 3.13 .venv
uv pip install --python .venv -r requirements-service.txt
./.venv/Scripts/python -m uvicorn service.app:app --port 7200
# in-memory store + lexical ranking; 31 seeded decisions
```
