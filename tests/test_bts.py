"""Tests for Behavioral Trust Score (BTS) computation.

Pure-logic tests (TestGetZtaaAuthorizationLevel) run without Neo4j.
Neo4j-dependent tests require NEO4J_AVAILABLE=1.
"""

import os
import time
import uuid

import pytest

from schema.models import DecisionTrace, ReasoningStep
from store.neo4j_adapter import (
    Neo4jAdapter,
    get_ztaa_authorization_level,
    BTS_NSA_DEFAULT,
)

_skip_neo4j = pytest.mark.skipif(
    os.getenv("NEO4J_AVAILABLE") != "1",
    reason="NEO4J_AVAILABLE != 1 — skip live Neo4j tests",
)

NEO4J_URI = os.getenv("NCG_NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NCG_NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NCG_NEO4J_PASSWORD", "password")


@pytest.fixture(scope="module")
def adapter():
    if os.getenv("NEO4J_AVAILABLE") != "1":
        pytest.skip("NEO4J_AVAILABLE != 1")
    db = Neo4jAdapter(uri=NEO4J_URI, user=NEO4J_USER, password=NEO4J_PASSWORD)
    # Wait for Neo4j to be ready (may still be starting from docker-compose)
    import time as _time
    for attempt in range(30):
        try:
            db._driver.verify_connectivity()
            break
        except Exception:
            if attempt == 29:
                pytest.skip("Neo4j not reachable after 30 retries")
            _time.sleep(1)
    yield db
    db.close()


@pytest.fixture(autouse=False)
def _cleanup(adapter):
    yield
    try:
        with adapter._driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")
    except Exception:
        pass  # teardown best-effort; don't fail the test


def _emit_traces(adapter, agent_id, count, outcome="success", **kwargs):
    """Helper: emit N traces for an agent."""
    now = int(time.time() * 1000)
    for i in range(count):
        trace = DecisionTrace(
            agent_id=agent_id,
            inputs={"seq": i},
            output={"seq": i},
            outcome=outcome,
            timestamp_ms=now + i,
            duration_ms=100,
            steps=[
                ReasoningStep(
                    step_id=str(uuid.uuid4()),
                    step_type="decide",
                    thought=f"decision {i}",
                    confidence=0.9,
                )
            ],
            **kwargs,
        )
        adapter.write_trace(trace)


class TestGetZtaaAuthorizationLevel:
    def test_full(self):
        assert get_ztaa_authorization_level(0.90) == "full"
        assert get_ztaa_authorization_level(0.85) == "full"

    def test_monitored(self):
        assert get_ztaa_authorization_level(0.75) == "monitored"
        assert get_ztaa_authorization_level(0.70) == "monitored"

    def test_restricted_hitl(self):
        assert get_ztaa_authorization_level(0.60) == "restricted_hitl"
        assert get_ztaa_authorization_level(0.50) == "restricted_hitl"

    def test_restricted(self):
        assert get_ztaa_authorization_level(0.40) == "restricted"
        assert get_ztaa_authorization_level(0.30) == "restricted"

    def test_blocked(self):
        assert get_ztaa_authorization_level(0.20) == "blocked"
        assert get_ztaa_authorization_level(0.0) == "blocked"


@_skip_neo4j
@pytest.mark.usefixtures("_cleanup")
class TestBTSNoTraces:
    def test_no_trace_agent_returns_nsa_default(self, adapter):
        result = adapter.compute_behavioral_trust_score("ghost-agent")
        assert result["bts"] == BTS_NSA_DEFAULT
        assert result["trace_count"] == 0
        assert result["authorization_level"] == "restricted"


@_skip_neo4j
@pytest.mark.usefixtures("_cleanup")
class TestBTSAllSuccess:
    def test_all_success_high_bts(self, adapter):
        _emit_traces(adapter, "success-agent", 15, outcome="success")
        result = adapter.compute_behavioral_trust_score("success-agent")
        assert result["trace_count"] == 15
        assert result["sub_scores"]["success"] == 1.0
        # BTS should be high (>= 0.70) for a fully successful agent
        assert result["bts"] >= 0.70
        assert result["authorization_level"] in ("full", "monitored")


@_skip_neo4j
@pytest.mark.usefixtures("_cleanup")
class TestBTSPolicyViolation:
    def test_policy_violations_lower_bts(self, adapter):
        """Agent with policy violations should score lower on S_policy."""
        now = int(time.time() * 1000)
        for i in range(15):
            trace = DecisionTrace(
                agent_id="policy-agent",
                inputs={"seq": i},
                output={"seq": i},
                outcome="success",
                timestamp_ms=now + i,
                duration_ms=100,
                steps=[
                    ReasoningStep(
                        step_id=str(uuid.uuid4()),
                        step_type="decide",
                        thought=f"decision {i}",
                    )
                ],
            )
            adapter.write_trace(trace)

            # Mark some traces as having policy violations
            if i < 5:
                with adapter._driver.session() as session:
                    session.run(
                        """
                        MATCH (d:Decision {trace_id: $tid})
                        SET d.policy_refs = 'policy-v1', d.policy_violation = true
                        """,
                        tid=trace.trace_id,
                    )
            elif i < 10:
                with adapter._driver.session() as session:
                    session.run(
                        """
                        MATCH (d:Decision {trace_id: $tid})
                        SET d.policy_refs = 'policy-v1'
                        """,
                        tid=trace.trace_id,
                    )

        result = adapter.compute_behavioral_trust_score("policy-agent")
        # 5 violations out of 10 with policy refs → S_policy = 0.5
        assert result["sub_scores"]["policy"] == 0.5


@_skip_neo4j
@pytest.mark.usefixtures("_cleanup")
class TestBTSMixedAgent:
    def test_mixed_outcomes_moderate_bts(self, adapter):
        """Agent with mixed success/failure should have moderate BTS."""
        now = int(time.time() * 1000)
        for i in range(20):
            outcome = "success" if i % 2 == 0 else "failure"
            trace = DecisionTrace(
                agent_id="mixed-agent",
                inputs={"seq": i},
                output={"seq": i},
                outcome=outcome,
                timestamp_ms=now + i,
                duration_ms=100,
                steps=[
                    ReasoningStep(
                        step_id=str(uuid.uuid4()),
                        step_type="decide",
                        thought=f"decision {i}",
                        confidence=0.9,
                    )
                ],
            )
            adapter.write_trace(trace)

        result = adapter.compute_behavioral_trust_score("mixed-agent")
        assert result["trace_count"] == 20
        # 50% success rate → S_success = 0.5
        assert result["sub_scores"]["success"] == 0.5
        # BTS should be moderate
        assert 0.30 <= result["bts"] <= 0.70
