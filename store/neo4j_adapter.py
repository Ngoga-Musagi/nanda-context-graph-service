"""Neo4j graph store for decision traces.

Follows SKILL-09 from SKILLS.md. All writes use explicit transactions.
Node/edge types match CLAUDE.md section 5 (Graph Node/Edge Types).

Manual Neo4j start (do NOT auto-start):
  docker run -d --name ncg-neo4j -p 7474:7474 -p 7687:7687 \
    -e NEO4J_AUTH=neo4j/password neo4j:5
"""

import math

from neo4j import GraphDatabase

from schema.models import DecisionTrace


# ── Behavioral Trust Score (BTS) — ZTAA integration ────────────

BTS_WEIGHTS = {"success": 0.35, "policy": 0.30, "anomaly": 0.25, "depth": 0.10}
BTS_MIN_SAMPLES = 10
BTS_MAX_DEPTH = 5.0
BTS_NSA_DEFAULT = 0.40  # "Newly Seen Agent" default from arXiv:2508.03101


def get_ztaa_authorization_level(bts: float) -> str:
    """Map a BTS value to a ZTAA authorization level."""
    if bts >= 0.85:
        return "full"
    if bts >= 0.70:
        return "monitored"
    if bts >= 0.50:
        return "restricted_hitl"
    if bts >= 0.30:
        return "restricted"
    return "blocked"


class Neo4jAdapter:
    def __init__(self, uri: str, user: str, password: str):
        self._driver = GraphDatabase.driver(
            uri, auth=(user, password), max_transaction_retry_time=5.0
        )
        try:
            self.ensure_schema()
        except Exception:
            # Neo4j may not be reachable at construction time; the ingest service
            # retries writes anyway. Constraints are also re-asserted on demand.
            pass

    def ensure_schema(self) -> None:
        """Create uniqueness constraints (idempotent).

        These are REQUIRED for correctness, not just performance: every write
        MERGEs Agent/Decision/Step by their business key. Without a uniqueness
        constraint, two MERGEs that race (or a child trace that MERGEs a parent
        stub before the parent's own write arrives) can create DUPLICATE nodes
        with the same key. The causal chain then splits across the copies and
        traversal silently stops short. The constraint makes MERGE lock on the
        key, guaranteeing a single node and true idempotency.
        """
        constraints = [
            "CREATE CONSTRAINT decision_trace_id IF NOT EXISTS "
            "FOR (d:Decision) REQUIRE d.trace_id IS UNIQUE",
            "CREATE CONSTRAINT agent_agent_id IF NOT EXISTS "
            "FOR (a:Agent) REQUIRE a.agent_id IS UNIQUE",
            "CREATE CONSTRAINT step_step_id IF NOT EXISTS "
            "FOR (s:Step) REQUIRE s.step_id IS UNIQUE",
        ]
        with self._driver.session() as session:
            for c in constraints:
                session.run(c)

    # ── writes ───────────────────────────────────────────────────────

    def write_trace(self, trace: DecisionTrace) -> None:
        """Write a complete DecisionTrace as a subgraph to Neo4j."""
        with self._driver.session() as session:
            session.execute_write(self._create_trace_subgraph, trace)

    @staticmethod
    def _create_trace_subgraph(tx, trace: DecisionTrace) -> None:
        # Upsert Agent node
        tx.run(
            """
            MERGE (a:Agent {agent_id: $agent_id})
            ON CREATE SET a.handle = $handle, a.first_seen_ms = $ts
            ON MATCH  SET a.last_seen_ms = $ts
            """,
            agent_id=trace.agent_id,
            handle=trace.agent_handle or "",
            ts=trace.timestamp_ms,
        )

        # Upsert Decision node (MERGE for federation idempotency)
        tx.run(
            """
            MERGE (d:Decision {trace_id: $trace_id})
            ON CREATE SET d.outcome = $outcome, d.timestamp_ms = $ts, d.duration_ms = $dur
            ON MATCH  SET d.outcome = $outcome, d.duration_ms = $dur
            """,
            trace_id=trace.trace_id,
            outcome=trace.outcome,
            ts=trace.timestamp_ms,
            dur=trace.duration_ms or 0,
        )

        # MADE_BY: Decision → Agent
        tx.run(
            """
            MATCH (a:Agent {agent_id: $agent_id})
            MATCH (d:Decision {trace_id: $trace_id})
            MERGE (d)-[:MADE_BY]->(a)
            """,
            agent_id=trace.agent_id,
            trace_id=trace.trace_id,
        )

        # PRECEDED_BY: Decision → parent Decision (causal chain).
        # MERGE (not MATCH) the parent so the edge is created even when a child
        # trace arrives BEFORE its parent — which happens whenever delegation is
        # synchronous (the callee finishes and emits first) or simply out of
        # order on the wire. The parent is created as a stub keyed on trace_id;
        # when the parent's own trace arrives later, the Decision MERGE above
        # matches that same node and fills in its properties (idempotent).
        if trace.parent_trace_id:
            tx.run(
                """
                MATCH (d:Decision {trace_id: $trace_id})
                MERGE (p:Decision {trace_id: $parent_id})
                MERGE (d)-[:PRECEDED_BY]->(p)
                """,
                trace_id=trace.trace_id,
                parent_id=trace.parent_trace_id,
            )

        # Create Step nodes + DECIDED_BECAUSE edges
        for step in trace.steps:
            tx.run(
                """
                CREATE (s:Step {
                    step_id:   $step_id,
                    step_type: $type,
                    thought:   $thought,
                    tool_name: $tool
                })
                """,
                step_id=step.step_id,
                type=step.step_type,
                thought=step.thought,
                tool=step.tool_name or "",
            )

            tx.run(
                """
                MATCH (d:Decision {trace_id: $trace_id})
                MATCH (s:Step {step_id: $step_id})
                MERGE (d)-[:DECIDED_BECAUSE]->(s)
                """,
                trace_id=trace.trace_id,
                step_id=step.step_id,
            )

    def append_step(self, parent_trace_id: str, step: dict) -> bool:
        """Append a Step node to an existing Decision. Returns True if Decision was found."""
        with self._driver.session() as session:
            result = session.execute_write(
                self._create_and_link_step, parent_trace_id, step
            )
            return result

    @staticmethod
    def _create_and_link_step(tx, parent_trace_id: str, step: dict) -> bool:
        # Check Decision exists
        check = tx.run(
            "MATCH (d:Decision {trace_id: $tid}) RETURN d",
            tid=parent_trace_id,
        )
        if not check.single():
            return False

        tx.run(
            """
            CREATE (s:Step {
                step_id:   $step_id,
                step_type: $type,
                thought:   $thought,
                tool_name: $tool
            })
            """,
            step_id=step.get("step_id", ""),
            type=step.get("step_type", "execute"),
            thought=step.get("thought", ""),
            tool=step.get("tool_name", ""),
        )
        tx.run(
            """
            MATCH (d:Decision {trace_id: $trace_id})
            MATCH (s:Step {step_id: $step_id})
            MERGE (d)-[:DECIDED_BECAUSE]->(s)
            """,
            trace_id=parent_trace_id,
            step_id=step.get("step_id", ""),
        )
        return True

    # ── reads ────────────────────────────────────────────────────────

    def get_trace(self, trace_id: str) -> dict | None:
        """Return full trace subgraph as a dict, or None if not found."""
        with self._driver.session() as session:
            result = session.run(
                """
                MATCH (d:Decision {trace_id: $trace_id})-[:MADE_BY]->(a:Agent)
                OPTIONAL MATCH (d)-[:DECIDED_BECAUSE]->(s:Step)
                RETURN d, a, collect(s) AS steps
                """,
                trace_id=trace_id,
            )
            record = result.single()
            if not record:
                return None
            return {
                "trace_id": record["d"]["trace_id"],
                "agent_id": record["a"]["agent_id"],
                "outcome": record["d"]["outcome"],
                "timestamp_ms": record["d"]["timestamp_ms"],
                "duration_ms": record["d"]["duration_ms"],
                "steps": [dict(s) for s in record["steps"]],
            }

    def get_agent_history(
        self, agent_id: str, limit: int = 20, outcome: str | None = None
    ) -> list[dict]:
        """Return paginated decision history for an agent."""
        with self._driver.session() as session:
            if outcome:
                result = session.run(
                    """
                    MATCH (a:Agent {agent_id: $agent_id})<-[:MADE_BY]-(d:Decision)
                    WHERE d.outcome = $outcome
                    RETURN d ORDER BY d.timestamp_ms DESC LIMIT $limit
                    """,
                    agent_id=agent_id,
                    outcome=outcome,
                    limit=limit,
                )
            else:
                result = session.run(
                    """
                    MATCH (a:Agent {agent_id: $agent_id})<-[:MADE_BY]-(d:Decision)
                    RETURN d ORDER BY d.timestamp_ms DESC LIMIT $limit
                    """,
                    agent_id=agent_id,
                    limit=limit,
                )
            return [dict(r["d"]) for r in result]

    # ── Behavioral Trust Score ──────────────────────────────────────

    def compute_behavioral_trust_score(
        self, agent_id: str, window_days: int = 30
    ) -> dict:
        """Compute the BTS for an agent over a lookback window.

        Returns a dict with keys: bts, sub_scores, trace_count, authorization_level.
        If the agent has no traces, returns BTS_NSA_DEFAULT (0.40).
        """
        with self._driver.session() as session:
            result = session.run(
                """
                WITH $window_ms AS cutoff
                OPTIONAL MATCH (a:Agent {agent_id: $agent_id})<-[:MADE_BY]-(d:Decision)
                WHERE d.timestamp_ms >= cutoff
                WITH collect(d) AS decisions
                WITH decisions, size(decisions) AS total

                // S_success: success rate
                WITH decisions, total,
                     size([d IN decisions WHERE d.outcome = 'success']) AS successes

                // S_policy: count traces with policy_violation property
                WITH decisions, total, successes,
                     size([d IN decisions WHERE d.policy_refs IS NOT NULL]) AS with_policy,
                     size([d IN decisions WHERE d.policy_violation = true]) AS violations

                // S_depth: mean delegation depth (count PRECEDED_BY chain length)
                UNWIND CASE WHEN size(decisions) > 0 THEN decisions ELSE [null] END AS d
                OPTIONAL MATCH path = (d)-[:PRECEDED_BY*0..10]->()
                WITH total, successes, with_policy, violations,
                     d, CASE WHEN d IS NOT NULL THEN length(path) ELSE 0 END AS depth
                WITH total, successes, with_policy, violations,
                     collect({trace_id: d.trace_id, depth: depth}) AS trace_depths

                // Feature stats for anomaly: gather per-decision stats
                RETURN total, successes, with_policy, violations, trace_depths
                """,
                agent_id=agent_id,
                window_ms=int(
                    (__import__("time").time() - window_days * 86400) * 1000
                ),
            )
            record = result.single()

        total = record["total"] if record else 0
        if total == 0:
            return {
                "bts": BTS_NSA_DEFAULT,
                "sub_scores": {
                    "success": 0.5,
                    "policy": 0.5,
                    "anomaly": 0.5,
                    "depth": 1.0,
                },
                "trace_count": 0,
                "authorization_level": get_ztaa_authorization_level(BTS_NSA_DEFAULT),
            }

        successes = record["successes"]
        with_policy = record["with_policy"]
        violations = record["violations"]
        trace_depths = record["trace_depths"]

        # S_success
        if total < BTS_MIN_SAMPLES:
            s_success = 0.5
        else:
            s_success = successes / total

        # S_policy
        if with_policy == 0 or total < BTS_MIN_SAMPLES:
            s_policy = 0.5
        else:
            s_policy = 1.0 - (violations / with_policy)

        # S_depth: max(0, 1 - mean_depth / MAX_DEPTH)
        depths = [
            td["depth"]
            for td in trace_depths
            if td["trace_id"] is not None
        ]
        mean_depth = sum(depths) / len(depths) if depths else 0.0
        s_depth = max(0.0, 1.0 - (mean_depth / BTS_MAX_DEPTH))

        # S_anomaly: simplified — use confidence deviation from agent's own mean
        # For the reference implementation, compute anomaly as 0.5 (neutral) when
        # fewer than MIN_SAMPLES traces; otherwise use a simple z-score sigmoid
        if total < BTS_MIN_SAMPLES:
            s_anomaly = 0.5
        else:
            s_anomaly = self._compute_anomaly_score(agent_id, window_days)

        bts = (
            BTS_WEIGHTS["success"] * s_success
            + BTS_WEIGHTS["policy"] * s_policy
            + BTS_WEIGHTS["anomaly"] * (1.0 - s_anomaly)
            + BTS_WEIGHTS["depth"] * s_depth
        )
        bts = round(max(0.0, min(1.0, bts)), 4)

        return {
            "bts": bts,
            "sub_scores": {
                "success": round(s_success, 4),
                "policy": round(s_policy, 4),
                "anomaly": round(s_anomaly, 4),
                "depth": round(s_depth, 4),
            },
            "trace_count": total,
            "authorization_level": get_ztaa_authorization_level(bts),
        }

    def _compute_anomaly_score(self, agent_id: str, window_days: int = 30) -> float:
        """Compute anomaly sub-score via z-score sigmoid over trace features.

        Features: [mean_confidence, delegation_depth, tool_call_count, duration_ms].
        Z-score is computed against the agent's own historical distribution.
        Returns sigmoid(norm(z_scores)) in [0, 1].
        """
        with self._driver.session() as session:
            result = session.run(
                """
                WITH $window_ms AS cutoff
                MATCH (a:Agent {agent_id: $agent_id})<-[:MADE_BY]-(d:Decision)
                WHERE d.timestamp_ms >= cutoff
                OPTIONAL MATCH (d)-[:DECIDED_BECAUSE]->(s:Step)
                WITH d, collect(s) AS steps
                OPTIONAL MATCH path = (d)-[:PRECEDED_BY*0..10]->()
                WITH d, steps,
                     CASE WHEN path IS NOT NULL THEN length(path) ELSE 0 END AS depth
                WITH d.duration_ms AS dur,
                     CASE WHEN size(steps) > 0
                          THEN reduce(acc = 0.0, s IN steps | acc + coalesce(s.confidence, 1.0)) / size(steps)
                          ELSE 1.0 END AS mean_conf,
                     size([s IN steps WHERE s.tool_name IS NOT NULL AND s.tool_name <> '']) AS tool_count,
                     depth
                RETURN collect({dur: dur, mean_conf: mean_conf, tool_count: tool_count, depth: depth}) AS features
                """,
                agent_id=agent_id,
                window_ms=int(
                    (__import__("time").time() - window_days * 86400) * 1000
                ),
            )
            record = result.single()

        features = record["features"] if record else []
        if len(features) < BTS_MIN_SAMPLES:
            return 0.5

        # Extract feature vectors
        confs = [f["mean_conf"] for f in features]
        depths = [f["depth"] for f in features]
        tools = [f["tool_count"] for f in features]
        durs = [f["dur"] or 0 for f in features]

        def _z_score_norm(values: list[float]) -> float:
            """Return the z-score of the last value vs the population."""
            n = len(values)
            if n < 2:
                return 0.0
            mean = sum(values) / n
            std = math.sqrt(sum((v - mean) ** 2 for v in values) / n)
            if std == 0:
                return 0.0
            return abs((values[-1] - mean) / std)

        z_conf = _z_score_norm(confs)
        z_depth = _z_score_norm(depths)
        z_tools = _z_score_norm(tools)
        z_dur = _z_score_norm(durs)

        # Norm of z-scores
        z_norm = math.sqrt(z_conf**2 + z_depth**2 + z_tools**2 + z_dur**2)

        # Sigmoid
        return 1.0 / (1.0 + math.exp(-z_norm))

    # ── lifecycle ────────────────────────────────────────────────────

    def close(self) -> None:
        self._driver.close()
