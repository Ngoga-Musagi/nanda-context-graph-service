"""NCG Ingest API — accepts DecisionTrace events and writes them to Neo4j.

Run:
  uvicorn ingest.main:app --host 0.0.0.0 --port 7200 --reload
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from schema.models import DecisionTrace

logger = logging.getLogger("ncg.ingest")

# ── Neo4j connection (lazy, with graceful fallback) ──────────────

_graph = None


def _get_graph():
    """Return the Neo4jAdapter singleton, or None if unavailable."""
    global _graph
    if _graph is None:
        try:
            from store.neo4j_adapter import Neo4jAdapter

            _graph = Neo4jAdapter(
                uri=os.getenv("NCG_NEO4J_URI", "bolt://localhost:7687"),
                user=os.getenv("NCG_NEO4J_USER", "neo4j"),
                password=os.getenv("NCG_NEO4J_PASSWORD", "password"),
            )
        except Exception as exc:
            logger.warning("Neo4j driver init failed: %s", exc)
    return _graph


# ── Lifespan ─────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: verify Neo4j connection
    graph = _get_graph()
    if graph:
        try:
            graph._driver.verify_connectivity()
            logger.info("Neo4j connected at %s", os.getenv("NCG_NEO4J_URI", "bolt://localhost:7687"))
        except Exception as exc:
            logger.warning("Neo4j unreachable at startup (will retry on first write): %s", exc)
    else:
        logger.warning("Neo4j adapter not initialized — writes will be dropped until available")
    yield
    # Shutdown
    if _graph:
        _graph.close()


# ── App ──────────────────────────────────────────────────────────

app = FastAPI(title="NCG Ingest", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request models ───────────────────────────────────────────────


class StepPayload(BaseModel):
    step: dict
    parent_trace_id: str


# ── Background writers ───────────────────────────────────────────


def _bg_write_trace(trace: DecisionTrace) -> None:
    graph = _get_graph()
    if not graph:
        logger.error("Dropped trace %s — Neo4j unavailable", trace.trace_id)
        return
    try:
        graph.write_trace(trace)
    except Exception as exc:
        logger.error("Failed to write trace %s: %s", trace.trace_id, exc)


def _bg_append_step(parent_trace_id: str, step: dict) -> None:
    graph = _get_graph()
    if not graph:
        logger.error("Dropped step for %s — Neo4j unavailable", parent_trace_id)
        return
    try:
        graph.append_step(parent_trace_id, step)
    except Exception as exc:
        logger.error("Failed to append step to %s: %s", parent_trace_id, exc)


# ── Routes ───────────────────────────────────────────────────────


@app.post("/ingest/trace", status_code=202)
async def ingest_trace(trace: DecisionTrace, background_tasks: BackgroundTasks):
    """Accept a trace event. Returns 202 immediately; writes to graph in background."""
    background_tasks.add_task(_bg_write_trace, trace)
    return {"accepted": True, "trace_id": trace.trace_id}


@app.post("/ingest/step", status_code=202)
async def ingest_step(payload: StepPayload, background_tasks: BackgroundTasks):
    """Append a step to an existing Decision node (used by MCP shim)."""
    background_tasks.add_task(_bg_append_step, payload.parent_trace_id, payload.step)
    return {"accepted": True, "parent_trace_id": payload.parent_trace_id}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "nanda-context-graph-ingest"}
