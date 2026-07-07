"""nanda-context-graph — hosted decision-memory service (slim, single process).

One FastAPI app that an agent uses to:

* **emit** a decision trace        — ``POST /ingest/trace``
* **ask why** a decision was made  — ``GET /api/v1/why`` / ``GET /api/v1/trace/{id}``
* **query precedent**              — ``POST /api/v1/precedent`` (recall similar past decisions)

Reuses the original :mod:`schema.models` ``DecisionTrace`` for wire compatibility,
persists the full trace (so precedent recall has the situation + resolution), and
ranks precedent with a hybrid embeddings/lexical scorer. Backed by Neo4j Aura in
production, with an in-memory fallback so the service never hard-fails.

Run::

    uvicorn service.app:app --host 0.0.0.0 --port 7200
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from schema.models import DecisionTrace

from service.embeddings import Embedder
from service.precedent import rank_precedents
from service.seed import seed_records, trace_to_record
from service.store import GraphStore, ResilientStore, create_store

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ncg.app")

_KEEPWARM_SECONDS = 240  # ping the store every 4 min so Aura Free does not idle-pause


class _State:
    store: GraphStore
    embedder: Embedder


state = _State()


def _embed_and_write(record: dict) -> None:
    if state.embedder.active and not record.get("embedding"):
        record["embedding"] = state.embedder.embed_one(record.get("precedent_text") or "")
    state.store.write_trace(record)


def _build_seed_records() -> list[dict]:
    """Seed records with embeddings attached (when an embedding key is active)."""
    records = seed_records()
    if state.embedder.active:
        vectors = state.embedder.embed([r["precedent_text"] for r in records])
        if vectors:
            for record, vector in zip(records, vectors, strict=False):
                record["embedding"] = vector
    return records


def _backfill_embeddings() -> None:
    if not state.embedder.active:
        return
    missing = state.store.missing_embeddings()
    if not missing:
        return
    vectors = state.embedder.embed([text for _, text in missing])
    if vectors:
        for (trace_id, _), vector in zip(missing, vectors, strict=False):
            state.store.set_embedding(trace_id, vector)
        logger.info("Backfilled embeddings for %d decisions", len(missing))


async def _keepwarm() -> None:
    while True:
        await asyncio.sleep(_KEEPWARM_SECONDS)
        try:
            state.store.ping()
        except Exception as exc:  # pragma: no cover - best-effort
            logger.warning("keep-warm ping failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    state.embedder = Embedder()
    primary = create_store(
        uri=os.getenv("NCG_NEO4J_URI"),
        user=os.getenv("NCG_NEO4J_USER", "neo4j"),
        password=os.getenv("NCG_NEO4J_PASSWORD", "password"),
    )
    records = _build_seed_records()
    # Wrap a live Neo4j primary so a *later* Aura pause degrades to in-memory
    # instead of 500-ing every request. An in-memory primary can't fail, so it
    # is used as-is.
    state.store = ResilientStore(primary, records) if primary.backend == "neo4j" else primary
    for record in records:
        state.store.write_trace(record)
    logger.info("Seeded %d decisions (store now holds %d)", len(records), state.store.count())
    _backfill_embeddings()
    warm_task = asyncio.create_task(_keepwarm())
    yield
    warm_task.cancel()
    state.store.close()


app = FastAPI(
    title="nanda-context-graph — decision memory",
    version="1.0.0",
    summary="Queryable cross-agent decision memory: emit traces, ask why, recall precedent.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


# ── Request models ────────────────────────────────────────────────


class PrecedentQuery(BaseModel):
    """A precedent lookup: a situation plus optional filters."""

    query: str = Field(..., description="Natural-language description of the situation")
    agent_id: str | None = Field(None, description="Restrict to one agent's history")
    outcome: str | None = Field(
        None, description="Restrict to an outcome: success | failure | delegated | error"
    )
    k: int = Field(5, ge=1, le=25, description="How many precedents to return")


# ── Meta ─────────────────────────────────────────────────────────


@app.get("/")
def root():
    """Service banner with live status (used by the SKILL.md self-check)."""
    return {
        "service": "nanda-context-graph",
        "what": "queryable cross-agent decision memory — emit traces, ask why, recall precedent",
        "endpoints": {
            "emit_trace": "POST /ingest/trace",
            "ask_why": "GET /api/v1/why?agent_id=...",
            "get_trace": "GET /api/v1/trace/{trace_id}",
            "query_precedent": "POST /api/v1/precedent",
            "causal_chain": "GET /api/v1/chain/{trace_id}/causal",
            "history": "GET /api/v1/agent/{agent_id}/history",
        },
        "store_backend": state.store.backend,
        "decisions_stored": state.store.count(),
        "precedent_ranking": "embeddings" if state.embedder.active else "lexical",
        "ordering": (
            "The store is pre-seeded, so recall precedent immediately. Your OWN "
            "decisions become recallable only after you POST them to /ingest/trace."
        ),
    }


@app.get("/health")
def health():
    return {"status": "ok", "store": state.store.backend, "decisions": state.store.count()}


# ── Write ────────────────────────────────────────────────────────


@app.post("/ingest/trace", status_code=202)
def ingest_trace(trace: DecisionTrace):
    """Record a decision trace (inputs, reasoning steps, output, outcome, causal link)."""
    record = trace_to_record(trace)
    _embed_and_write(record)
    return {"accepted": True, "trace_id": trace.trace_id}


# ── Read ─────────────────────────────────────────────────────────


@app.post("/api/v1/precedent")
def precedent(q: PrecedentQuery):
    """Recall the most similar prior decisions and how each was handled."""
    candidates = state.store.candidates(agent_id=q.agent_id, outcome=q.outcome)
    method, results = rank_precedents(q.query, candidates, state.embedder, k=q.k)
    return {
        "query": q.query,
        "ranking": method,
        "count": len(results),
        "precedents": results,
    }


@app.get("/api/v1/why")
def why(agent_id: str):
    """Return an agent's most recent decision with its reasoning steps."""
    result = state.store.why(agent_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"No decisions found for agent {agent_id!r}")
    return result


# Internal persistence fields that callers never need to see in read responses.
_INTERNAL_FIELDS = ("embedding", "precedent_text")


def _public_trace(record: dict) -> dict:
    """Strip internal persistence fields (the raw embedding, precedent_text)."""
    return {k: v for k, v in record.items() if k not in _INTERNAL_FIELDS}


@app.get("/api/v1/trace/{trace_id}")
def get_trace(trace_id: str):
    """Return a full decision trace by id."""
    result = state.store.get_trace(trace_id)
    if not result:
        raise HTTPException(status_code=404, detail="Trace not found")
    return _public_trace(result)


@app.get("/api/v1/agent/{agent_id}/history")
def agent_history(agent_id: str, limit: int = 20, outcome: str | None = None):
    """Return an agent's recent decisions (newest first), optionally filtered by outcome."""
    return {"agent_id": agent_id, "decisions": state.store.agent_history(agent_id, limit, outcome)}


@app.get("/api/v1/chain/{trace_id}/causal")
def causal_chain(trace_id: str, max_depth: int = 20):
    """Follow parent links back to the root decision (delegation/causal chain)."""
    return {"trace_id": trace_id, "chain": state.store.causal_chain(trace_id, max_depth)}
