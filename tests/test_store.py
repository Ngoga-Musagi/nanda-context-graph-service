"""Tests for store.neo4j_adapter — requires a running Neo4j instance.

Skipped automatically unless NEO4J_AVAILABLE=1.

Manual start:
  docker run -d --name ncg-neo4j -p 7474:7474 -p 7687:7687 \
    -e NEO4J_AUTH=neo4j/password neo4j:5
"""

import os
import uuid

import pytest

from schema.models import DecisionTrace, ReasoningStep
from store.neo4j_adapter import Neo4jAdapter

pytestmark = pytest.mark.skipif(
    os.getenv("NEO4J_AVAILABLE") != "1",
    reason="NEO4J_AVAILABLE != 1 — skip live Neo4j tests",
)

NEO4J_URI = os.getenv("NCG_NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NCG_NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NCG_NEO4J_PASSWORD", "password")


@pytest.fixture(scope="module")
def adapter():
    db = Neo4jAdapter(uri=NEO4J_URI, user=NEO4J_USER, password=NEO4J_PASSWORD)
    yield db
    db.close()


@pytest.fixture(autouse=True)
def _cleanup(adapter):
    """Wipe test data after each test to avoid cross-contamination."""
    yield
    with adapter._driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n")


class TestWriteAndGetTrace:
    def test_round_trip(self, adapter):
        trace = DecisionTrace(
            agent_id="test-agent",
            agent_handle="@test:agent",
            inputs={"message": "hello"},
            output={"response": "world"},
            outcome="success",
            duration_ms=42,
            steps=[
                ReasoningStep(
                    step_id=str(uuid.uuid4()),
                    step_type="decide",
                    thought="chose to greet back",
                ),
            ],
        )
        adapter.write_trace(trace)

        result = adapter.get_trace(trace.trace_id)
        assert result is not None
        assert result["trace_id"] == trace.trace_id
        assert result["agent_id"] == "test-agent"
        assert result["outcome"] == "success"
        assert result["duration_ms"] == 42
        assert len(result["steps"]) == 1
        assert result["steps"][0]["thought"] == "chose to greet back"

    def test_get_trace_not_found(self, adapter):
        assert adapter.get_trace("nonexistent-id") is None


class TestPrecededByEdge:
    def test_causal_chain(self, adapter):
        parent = DecisionTrace(
            agent_id="agent-a",
            inputs={"message": "start"},
            output={"response": "delegating"},
            outcome="delegated",
        )
        child = DecisionTrace(
            agent_id="agent-b",
            parent_trace_id=parent.trace_id,
            inputs={"message": "forwarded"},
            output={"response": "done"},
            outcome="success",
        )

        adapter.write_trace(parent)
        adapter.write_trace(child)

        # Verify PRECEDED_BY edge exists
        with adapter._driver.session() as session:
            result = session.run(
                """
                MATCH (c:Decision {trace_id: $child_id})-[:PRECEDED_BY]->(p:Decision {trace_id: $parent_id})
                RETURN p.trace_id AS parent_trace_id
                """,
                child_id=child.trace_id,
                parent_id=parent.trace_id,
            )
            record = result.single()
            assert record is not None
            assert record["parent_trace_id"] == parent.trace_id


class TestAgentHistory:
    def test_returns_ordered_history(self, adapter):
        for i in range(3):
            trace = DecisionTrace(
                agent_id="history-agent",
                inputs={"seq": i},
                output={"seq": i},
                outcome="success",
                timestamp_ms=1000 + i,
            )
            adapter.write_trace(trace)

        history = adapter.get_agent_history("history-agent", limit=10)
        assert len(history) == 3
        # Most recent first
        assert history[0]["timestamp_ms"] >= history[-1]["timestamp_ms"]

    def test_filter_by_outcome(self, adapter):
        for outcome in ["success", "success", "error"]:
            adapter.write_trace(
                DecisionTrace(
                    agent_id="filter-agent",
                    inputs={},
                    output={},
                    outcome=outcome,
                )
            )

        errors = adapter.get_agent_history("filter-agent", outcome="error")
        assert len(errors) == 1
        assert errors[0]["outcome"] == "error"

    def test_empty_history(self, adapter):
        assert adapter.get_agent_history("ghost-agent") == []
