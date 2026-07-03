"""NCG Query API — read-only endpoints for decision traces and causal chains.

Run:
  uvicorn api.query:app --host 0.0.0.0 --port 7201 --reload
"""

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from store.neo4j_adapter import Neo4jAdapter

logger = logging.getLogger("ncg.query")

_graph = None


def _get_graph() -> Neo4jAdapter:
    global _graph
    if _graph is None:
        _graph = Neo4jAdapter(
            uri=os.getenv("NCG_NEO4J_URI", "bolt://localhost:7687"),
            user=os.getenv("NCG_NEO4J_USER", "neo4j"),
            password=os.getenv("NCG_NEO4J_PASSWORD", "password"),
        )
    return _graph


@asynccontextmanager
async def lifespan(app: FastAPI):
    graph = _get_graph()
    try:
        graph._driver.verify_connectivity()
        logger.info("Neo4j connected")
    except Exception as exc:
        logger.warning("Neo4j unreachable at startup: %s", exc)
    yield
    if _graph:
        _graph.close()


app = FastAPI(title="NCG Query API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Endpoints ────────────────────────────────────────────────────


@app.get("/api/v1/trace/{trace_id}")
def get_trace(trace_id: str):
    """Return full DecisionTrace subgraph for a given trace_id."""
    graph = _get_graph()
    result = graph.get_trace(trace_id)
    if not result:
        raise HTTPException(status_code=404, detail="Trace not found")
    return result


@app.get("/api/v1/why")
def why(agent_id: str):
    """Return the most recent decision subgraph for an agent."""
    graph = _get_graph()
    with graph._driver.session() as session:
        result = session.run(
            """
            MATCH (a:Agent {agent_id: $agent_id})<-[:MADE_BY]-(d:Decision)
            OPTIONAL MATCH (d)-[:DECIDED_BECAUSE]->(s:Step)
            RETURN d, collect(s) AS steps
            ORDER BY d.timestamp_ms DESC LIMIT 1
            """,
            agent_id=agent_id,
        )
        record = result.single()
        if not record:
            raise HTTPException(status_code=404, detail="No traces found for agent")
        return {
            "decision": dict(record["d"]),
            "steps": [dict(s) for s in record["steps"]],
        }


@app.get("/api/v1/agent/{agent_id}/history")
def agent_history(agent_id: str, limit: int = 20, outcome: str | None = None):
    """Return paginated behavioral history for an agent."""
    graph = _get_graph()
    traces = graph.get_agent_history(agent_id, limit=limit, outcome=outcome)
    return {"agent_id": agent_id, "traces": traces}


@app.get("/api/v1/chain/{trace_id}/causal")
def causal_chain(trace_id: str, max_depth: int = 10):
    """Follow PRECEDED_BY edges back to the root decision."""
    graph = _get_graph()
    with graph._driver.session() as session:
        result = session.run(
            """
            MATCH path = (d:Decision {trace_id: $trace_id})-[:PRECEDED_BY*0..10]->(root:Decision)
            WHERE NOT (root)-[:PRECEDED_BY]->()
            RETURN [n IN nodes(path) | n.trace_id] AS chain
            LIMIT 1
            """,
            trace_id=trace_id,
        )
        record = result.single()
        if not record:
            return {"trace_id": trace_id, "chain": [trace_id]}
        return {"trace_id": trace_id, "chain": record["chain"]}


@app.get("/api/v1/agent/{agent_id}/trust-score")
def agent_trust_score(agent_id: str, window_days: int = 30):
    """Compute and return the Behavioral Trust Score (BTS) for an agent."""
    graph = _get_graph()
    result = graph.compute_behavioral_trust_score(agent_id, window_days=window_days)
    return {
        "agent_id": agent_id,
        "bts": result["bts"],
        "authorization_level": result["authorization_level"],
        "sub_scores": result["sub_scores"],
        "trace_count": result["trace_count"],
        "window_days": window_days,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/api/v1/replay/{trace_id}")
def replay(trace_id: str):
    """Replay a decision trace (stub — not yet implemented)."""
    return {"status": "not_implemented", "trace_id": trace_id}


@app.get("/federation/traces")
def federation_traces(since_ms: int = 0):
    """Return all Decision traces newer than since_ms (for federation pull)."""
    graph = _get_graph()
    with graph._driver.session() as session:
        result = session.run(
            """
            MATCH (d:Decision)-[:MADE_BY]->(a:Agent)
            WHERE d.timestamp_ms > $since_ms
            OPTIONAL MATCH (d)-[:DECIDED_BECAUSE]->(s:Step)
            RETURN d, a, collect(s) AS steps
            ORDER BY d.timestamp_ms ASC
            LIMIT 500
            """,
            since_ms=since_ms,
        )
        traces = []
        for record in result:
            d = dict(record["d"])
            traces.append({
                "trace_id": d.get("trace_id"),
                "agent_id": record["a"]["agent_id"],
                "agent_handle": record["a"].get("handle"),
                "inputs": {},
                "output": {},
                "outcome": d.get("outcome"),
                "timestamp_ms": d.get("timestamp_ms"),
                "duration_ms": d.get("duration_ms"),
                "steps": [
                    {
                        "step_id": s.get("step_id", ""),
                        "step_type": s.get("step_type", "execute"),
                        "thought": s.get("thought", ""),
                        "tool_name": s.get("tool_name"),
                    }
                    for s in record["steps"]
                ],
            })
        return traces


@app.get("/health")
async def health():
    return {"status": "ok", "service": "nanda-context-graph-query"}
