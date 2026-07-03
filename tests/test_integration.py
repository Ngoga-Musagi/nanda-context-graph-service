"""Full pipeline integration test: ingest → Neo4j → query API.

Requires a running Neo4j instance. Skipped unless NEO4J_AVAILABLE=1.

Manual start:
  docker run -d --name ncg-neo4j -p 7474:7474 -p 7687:7687 \
    -e NEO4J_AUTH=neo4j/password neo4j:5
"""

import os
import time

import pytest
from fastapi.testclient import TestClient

from ingest.main import app as ingest_app
from api.query import app as query_app
from store.neo4j_adapter import Neo4jAdapter

pytestmark = pytest.mark.skipif(
    os.getenv("NEO4J_AVAILABLE") != "1",
    reason="NEO4J_AVAILABLE != 1 — skip live integration tests",
)

AGENT_ID = "integ-agent-001"


@pytest.fixture(scope="module")
def adapter():
    db = Neo4jAdapter(
        uri=os.getenv("NCG_NEO4J_URI", "bolt://localhost:7687"),
        user=os.getenv("NCG_NEO4J_USER", "neo4j"),
        password=os.getenv("NCG_NEO4J_PASSWORD", "password"),
    )
    yield db
    # Clean up all test data
    with db._driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n")
    db.close()


@pytest.fixture(scope="module")
def ingest_client():
    return TestClient(ingest_app)


@pytest.fixture(scope="module")
def query_client():
    return TestClient(query_app)


@pytest.fixture(scope="module")
def traces(ingest_client, adapter):
    """Emit 3 traces and wait for background writes to complete."""
    now = int(time.time() * 1000)

    parent = {
        "trace_id": "integ-parent-001",
        "agent_id": AGENT_ID,
        "inputs": {"message": "start workflow"},
        "steps": [],
        "output": {"response": "delegating"},
        "outcome": "success",
        "timestamp_ms": now,
    }
    failure = {
        "trace_id": "integ-failure-002",
        "agent_id": AGENT_ID,
        "inputs": {"message": "bad request"},
        "steps": [],
        "output": {"response": "crashed"},
        "outcome": "failure",
        "timestamp_ms": now + 100,
    }
    child = {
        "trace_id": "integ-child-003",
        "agent_id": AGENT_ID,
        "parent_trace_id": "integ-parent-001",
        "inputs": {"message": "delegated task"},
        "steps": [],
        "output": {"response": "done"},
        "outcome": "success",
        "timestamp_ms": now + 200,
    }

    for trace in [parent, failure, child]:
        resp = ingest_client.post("/ingest/trace", json=trace)
        assert resp.status_code == 202
        assert resp.json()["accepted"] is True

    # Wait for background tasks to write to Neo4j
    time.sleep(2)

    return {"parent": parent, "failure": failure, "child": child}


class TestIngestPipeline:
    def test_all_traces_accepted(self, traces):
        # traces fixture already asserted 202 for each
        assert len(traces) == 3

    def test_traces_in_neo4j(self, traces, adapter):
        for key in ("parent", "failure", "child"):
            result = adapter.get_trace(traces[key]["trace_id"])
            assert result is not None, f"Trace {key} not found in Neo4j"
            assert result["outcome"] == traces[key]["outcome"]


class TestQueryAPI:
    def test_get_trace(self, traces, query_client):
        resp = query_client.get("/api/v1/trace/integ-parent-001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["trace_id"] == "integ-parent-001"
        assert data["agent_id"] == AGENT_ID
        assert data["outcome"] == "success"

    def test_get_trace_404(self, traces, query_client):
        resp = query_client.get("/api/v1/trace/nonexistent")
        assert resp.status_code == 404

    def test_why_returns_latest(self, traces, query_client):
        resp = query_client.get("/api/v1/why", params={"agent_id": AGENT_ID})
        assert resp.status_code == 200
        data = resp.json()
        # Latest by timestamp_ms is the child (now + 200)
        assert data["decision"]["trace_id"] == "integ-child-003"

    def test_agent_history_returns_all(self, traces, query_client):
        resp = query_client.get(f"/api/v1/agent/{AGENT_ID}/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_id"] == AGENT_ID
        trace_ids = {t["trace_id"] for t in data["traces"]}
        assert trace_ids == {"integ-parent-001", "integ-failure-002", "integ-child-003"}

    def test_agent_history_filter_outcome(self, traces, query_client):
        resp = query_client.get(
            f"/api/v1/agent/{AGENT_ID}/history", params={"outcome": "failure"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["traces"]) == 1
        assert data["traces"][0]["outcome"] == "failure"

    def test_causal_chain(self, traces, query_client):
        resp = query_client.get("/api/v1/chain/integ-child-003/causal")
        assert resp.status_code == 200
        data = resp.json()
        assert data["chain"] == ["integ-child-003", "integ-parent-001"]

    def test_causal_chain_no_parent(self, traces, query_client):
        resp = query_client.get("/api/v1/chain/integ-failure-002/causal")
        assert resp.status_code == 200
        data = resp.json()
        assert data["chain"] == ["integ-failure-002"]

    def test_replay_stub(self, traces, query_client):
        resp = query_client.post("/api/v1/replay/integ-parent-001")
        assert resp.status_code == 200
        assert resp.json()["status"] == "not_implemented"

    def test_trust_score_with_traces(self, traces, query_client):
        resp = query_client.get(f"/api/v1/agent/{AGENT_ID}/trust-score")
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_id"] == AGENT_ID
        assert "bts" in data
        assert 0.0 <= data["bts"] <= 1.0
        assert "authorization_level" in data
        assert "sub_scores" in data
        assert data["trace_count"] == 3
        assert "computed_at" in data

    def test_trust_score_no_traces(self, traces, query_client):
        resp = query_client.get("/api/v1/agent/nonexistent-agent/trust-score")
        assert resp.status_code == 200
        data = resp.json()
        assert data["bts"] == 0.40
        assert data["authorization_level"] == "restricted"
        assert data["trace_count"] == 0

    def test_health(self, query_client):
        resp = query_client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
