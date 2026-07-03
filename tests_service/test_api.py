"""End-to-end API tests (in-memory store, lexical ranking — no Neo4j or key needed)."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    os.environ.pop("NCG_NEO4J_URI", None)
    os.environ.pop("OPENAI_API_KEY", None)
    from service.app import app

    with TestClient(app) as c:
        yield c


def test_root_and_health(client):
    root = client.get("/").json()
    assert root["service"] == "nanda-context-graph"
    assert root["store_backend"] == "memory"
    assert root["decisions_stored"] >= 30
    assert "ordering" in root
    health = client.get("/health").json()
    assert health["status"] == "ok"


def test_precedent_finds_relevant_discount(client):
    r = client.post(
        "/api/v1/precedent",
        json={"query": "Gold member wants a 15% discount on a ski trip car rental", "k": 3},
    )
    body = r.json()
    assert body["ranking"] == "lexical"
    assert body["count"] == 3
    top_ids = [p["trace_id"] for p in body["precedents"]]
    # The exact Gold/15%/ceiling precedent should surface in the top results.
    # (Lexical ranking gets it into the top-k; embeddings would rank it #1 —
    # which is precisely the quality gap the hybrid engine closes.)
    assert "seed-discount-001" in top_ids
    discount = next(p for p in body["precedents"] if p["trace_id"] == "seed-discount-001")
    assert discount["outcome"] == "failure"
    handled = discount["how_it_was_handled"].lower()
    assert "10%" in handled or "ceiling" in handled


def test_precedent_outcome_filter(client):
    r = client.post(
        "/api/v1/precedent",
        json={"query": "discount request", "outcome": "success", "k": 5},
    )
    body = r.json()
    assert body["count"] >= 1
    assert all(p["outcome"] == "success" for p in body["precedents"])


def test_precedent_agent_filter(client):
    r = client.post(
        "/api/v1/precedent",
        json={"query": "refund request", "agent_id": "refund-agent", "k": 5},
    )
    body = r.json()
    assert body["count"] >= 1
    assert all(p["agent_id"] == "refund-agent" for p in body["precedents"])


def test_write_before_read_roundtrip(client):
    trace = {
        "trace_id": "test-live-001",
        "agent_id": "discount-approval",
        "agent_handle": "@billing:discount-approval",
        "inputs": {"request": "Bronze member requests 5% discount"},
        "steps": [
            {"step_id": "x1", "step_type": "retrieve", "thought": "Policy: up to 10% auto-approve"},
            {"step_id": "x2", "step_type": "decide", "thought": "Approved: 5% within ceiling"},
        ],
        "output": {"approved": True, "discount_pct": 5},
        "outcome": "success",
        "timestamp_ms": 1775000999000,
    }
    w = client.post("/ingest/trace", json=trace)
    assert w.status_code == 202
    assert w.json()["trace_id"] == "test-live-001"

    got = client.get("/api/v1/trace/test-live-001").json()
    assert got["inputs"]["request"].startswith("Bronze")
    assert got["outcome"] == "success"
    assert len(got["steps"]) == 2
    # internal persistence fields are not leaked to callers
    assert "embedding" not in got
    assert "precedent_text" not in got

    why = client.get("/api/v1/why", params={"agent_id": "discount-approval"}).json()
    assert why["decision"]["trace_id"] == "test-live-001"  # newest decision wins


def test_causal_chain(client):
    chain = client.get("/api/v1/chain/seed-chain-approval/causal").json()["chain"]
    assert chain == ["seed-chain-approval", "seed-chain-pricing", "seed-chain-broker"]


def test_agent_history(client):
    hist = client.get(
        "/api/v1/agent/refund-agent/history", params={"limit": 10}
    ).json()["decisions"]
    assert len(hist) >= 5
    assert all(d["agent_id"] == "refund-agent" for d in hist)


def test_trace_not_found(client):
    assert client.get("/api/v1/trace/does-not-exist").status_code == 404
    assert client.get("/api/v1/why", params={"agent_id": "ghost"}).status_code == 404
