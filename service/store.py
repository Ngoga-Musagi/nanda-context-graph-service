"""Decision-trace store with two interchangeable backends.

* :class:`Neo4jGraphStore` — the production backend (Neo4j Aura Free). Unlike the
  original ``store/neo4j_adapter.py``, it persists the FULL trace — ``inputs``,
  ``output``, per-step ``thought``/``confidence``, the precedent text, and the
  embedding — because precedent recall needs the situation and how it resolved,
  not just the outcome.
* :class:`InMemoryGraphStore` — a faithful dict-backed backend used for local
  testing and as a graceful fallback when Aura is unreachable, so the service
  answers queries even if the managed DB is paused or briefly down.

A "record" is the common dict shape both backends speak::

    {
      "trace_id", "agent_id", "agent_handle", "parent_trace_id", "a2a_msg_id",
      "outcome", "timestamp_ms", "duration_ms",
      "inputs": {...}, "output": {...},
      "steps": [{"step_id","step_type","thought","tool_name","confidence"}, ...],
      "precedent_text": str, "embedding": list[float] | None,
    }
"""

from __future__ import annotations

import json
import logging
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger("ncg.store")


@runtime_checkable
class GraphStore(Protocol):
    """Common read/write surface for both backends."""

    backend: str

    def write_trace(self, record: dict[str, Any]) -> None: ...
    def get_trace(self, trace_id: str) -> dict[str, Any] | None: ...
    def why(self, agent_id: str) -> dict[str, Any] | None: ...
    def agent_history(
        self, agent_id: str, limit: int = 20, outcome: str | None = None
    ) -> list[dict[str, Any]]: ...
    def causal_chain(self, trace_id: str, max_depth: int = 20) -> list[str]: ...
    def candidates(
        self, agent_id: str | None = None, outcome: str | None = None
    ) -> list[dict[str, Any]]: ...
    def missing_embeddings(self) -> list[tuple[str, str]]: ...
    def set_embedding(self, trace_id: str, embedding: list[float]) -> None: ...
    def count(self) -> int: ...
    def ping(self) -> bool: ...
    def close(self) -> None: ...


def _decision_summary(record: dict[str, Any]) -> dict[str, Any]:
    """Project a record to a compact decision summary (no steps)."""
    return {
        "trace_id": record.get("trace_id"),
        "agent_id": record.get("agent_id"),
        "agent_handle": record.get("agent_handle"),
        "parent_trace_id": record.get("parent_trace_id"),
        "outcome": record.get("outcome"),
        "timestamp_ms": record.get("timestamp_ms"),
        "duration_ms": record.get("duration_ms"),
        "inputs": record.get("inputs") or {},
        "output": record.get("output") or {},
    }


# ---------------------------------------------------------------------------
# In-memory backend
# ---------------------------------------------------------------------------


class InMemoryGraphStore:
    """Dict-backed store: full fidelity, no persistence. Used for tests/fallback."""

    backend = "memory"

    def __init__(self) -> None:
        self._traces: dict[str, dict[str, Any]] = {}

    def write_trace(self, record: dict[str, Any]) -> None:
        self._traces[record["trace_id"]] = json.loads(json.dumps(record))  # deep copy

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        rec = self._traces.get(trace_id)
        return json.loads(json.dumps(rec)) if rec else None

    def why(self, agent_id: str) -> dict[str, Any] | None:
        recs = [r for r in self._traces.values() if r.get("agent_id") == agent_id]
        if not recs:
            return None
        latest = max(recs, key=lambda r: r.get("timestamp_ms") or 0)
        return {"decision": _decision_summary(latest), "steps": latest.get("steps") or []}

    def agent_history(
        self, agent_id: str, limit: int = 20, outcome: str | None = None
    ) -> list[dict[str, Any]]:
        recs = [r for r in self._traces.values() if r.get("agent_id") == agent_id]
        if outcome:
            recs = [r for r in recs if r.get("outcome") == outcome]
        recs.sort(key=lambda r: r.get("timestamp_ms") or 0, reverse=True)
        return [_decision_summary(r) for r in recs[:limit]]

    def causal_chain(self, trace_id: str, max_depth: int = 20) -> list[str]:
        chain = [trace_id]
        seen = {trace_id}
        current = self._traces.get(trace_id)
        depth = 0
        while current and current.get("parent_trace_id") and depth < max_depth:
            parent = current["parent_trace_id"]
            if parent in seen:
                break
            chain.append(parent)
            seen.add(parent)
            current = self._traces.get(parent)
            depth += 1
        return chain

    def candidates(
        self, agent_id: str | None = None, outcome: str | None = None
    ) -> list[dict[str, Any]]:
        recs = list(self._traces.values())
        if agent_id:
            recs = [r for r in recs if r.get("agent_id") == agent_id]
        if outcome:
            recs = [r for r in recs if r.get("outcome") == outcome]
        return [json.loads(json.dumps(r)) for r in recs]

    def missing_embeddings(self) -> list[tuple[str, str]]:
        return [
            (r["trace_id"], r.get("precedent_text") or "")
            for r in self._traces.values()
            if not r.get("embedding")
        ]

    def set_embedding(self, trace_id: str, embedding: list[float]) -> None:
        if trace_id in self._traces:
            self._traces[trace_id]["embedding"] = embedding

    def count(self) -> int:
        return len(self._traces)

    def ping(self) -> bool:
        return True

    def close(self) -> None:
        return None


# ---------------------------------------------------------------------------
# Neo4j backend (Aura)
# ---------------------------------------------------------------------------


class Neo4jGraphStore:
    """Neo4j-backed store persisting the full trace as a queryable subgraph."""

    backend = "neo4j"

    def __init__(self, uri: str, user: str, password: str) -> None:
        from neo4j import GraphDatabase

        self._driver = GraphDatabase.driver(
            uri, auth=(user, password), max_transaction_retry_time=8.0
        )
        self.ensure_schema()

    def ensure_schema(self) -> None:
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

    def write_trace(self, record: dict[str, Any]) -> None:
        with self._driver.session() as session:
            session.execute_write(self._write_tx, record)

    @staticmethod
    def _write_tx(tx, record: dict[str, Any]) -> None:
        tx.run(
            """
            MERGE (a:Agent {agent_id: $agent_id})
            ON CREATE SET a.handle = $handle, a.first_seen_ms = $ts
            ON MATCH  SET a.last_seen_ms = $ts
            """,
            agent_id=record["agent_id"],
            handle=record.get("agent_handle") or "",
            ts=record.get("timestamp_ms") or 0,
        )
        tx.run(
            """
            MERGE (d:Decision {trace_id: $trace_id})
            SET d.agent_id = $agent_id,
                d.agent_handle = $handle,
                d.parent_trace_id = $parent,
                d.a2a_msg_id = $a2a,
                d.outcome = $outcome,
                d.timestamp_ms = $ts,
                d.duration_ms = $dur,
                d.inputs_json = $inputs_json,
                d.output_json = $output_json,
                d.precedent_text = $ptext,
                d.embedding = $embedding
            """,
            trace_id=record["trace_id"],
            agent_id=record["agent_id"],
            handle=record.get("agent_handle") or "",
            parent=record.get("parent_trace_id"),
            a2a=record.get("a2a_msg_id"),
            outcome=record.get("outcome"),
            ts=record.get("timestamp_ms") or 0,
            dur=record.get("duration_ms") or 0,
            inputs_json=json.dumps(record.get("inputs") or {}),
            output_json=json.dumps(record.get("output") or {}),
            ptext=record.get("precedent_text") or "",
            embedding=record.get("embedding"),
        )
        tx.run(
            """
            MATCH (a:Agent {agent_id: $agent_id})
            MATCH (d:Decision {trace_id: $trace_id})
            MERGE (d)-[:MADE_BY]->(a)
            """,
            agent_id=record["agent_id"],
            trace_id=record["trace_id"],
        )
        if record.get("parent_trace_id"):
            tx.run(
                """
                MATCH (d:Decision {trace_id: $trace_id})
                MERGE (p:Decision {trace_id: $parent_id})
                MERGE (d)-[:PRECEDED_BY]->(p)
                """,
                trace_id=record["trace_id"],
                parent_id=record["parent_trace_id"],
            )
        for idx, step in enumerate(record.get("steps") or []):
            tx.run(
                """
                MERGE (s:Step {step_id: $step_id})
                SET s.step_type = $type, s.thought = $thought,
                    s.tool_name = $tool, s.confidence = $conf, s.idx = $idx
                WITH s
                MATCH (d:Decision {trace_id: $trace_id})
                MERGE (d)-[:DECIDED_BECAUSE]->(s)
                """,
                step_id=step.get("step_id") or f"{record['trace_id']}-s{idx}",
                type=step.get("step_type") or "execute",
                thought=step.get("thought") or "",
                tool=step.get("tool_name") or "",
                conf=step.get("confidence", 1.0),
                idx=idx,
                trace_id=record["trace_id"],
            )

    def _record_from_node(self, d: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "trace_id": d.get("trace_id"),
            "agent_id": d.get("agent_id"),
            "agent_handle": d.get("agent_handle"),
            "parent_trace_id": d.get("parent_trace_id"),
            "a2a_msg_id": d.get("a2a_msg_id"),
            "outcome": d.get("outcome"),
            "timestamp_ms": d.get("timestamp_ms"),
            "duration_ms": d.get("duration_ms"),
            "inputs": json.loads(d.get("inputs_json") or "{}"),
            "output": json.loads(d.get("output_json") or "{}"),
            "precedent_text": d.get("precedent_text") or "",
            "embedding": d.get("embedding"),
            "steps": [
                {
                    "step_id": s.get("step_id"),
                    "step_type": s.get("step_type"),
                    "thought": s.get("thought"),
                    "tool_name": s.get("tool_name") or None,
                    "confidence": s.get("confidence", 1.0),
                }
                for s in sorted(steps, key=lambda s: s.get("idx", 0))
            ],
        }

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        with self._driver.session() as session:
            result = session.run(
                """
                MATCH (d:Decision {trace_id: $trace_id})
                OPTIONAL MATCH (d)-[:DECIDED_BECAUSE]->(s:Step)
                RETURN d, collect(s) AS steps
                """,
                trace_id=trace_id,
            )
            record = result.single()
            if not record or record["d"].get("agent_id") is None:
                return None
            return self._record_from_node(dict(record["d"]), [dict(s) for s in record["steps"]])

    def why(self, agent_id: str) -> dict[str, Any] | None:
        with self._driver.session() as session:
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
                return None
            full = self._record_from_node(dict(record["d"]), [dict(s) for s in record["steps"]])
            return {"decision": _decision_summary(full), "steps": full["steps"]}

    def agent_history(
        self, agent_id: str, limit: int = 20, outcome: str | None = None
    ) -> list[dict[str, Any]]:
        cypher = (
            "MATCH (a:Agent {agent_id: $agent_id})<-[:MADE_BY]-(d:Decision) "
            + ("WHERE d.outcome = $outcome " if outcome else "")
            + "RETURN d ORDER BY d.timestamp_ms DESC LIMIT $limit"
        )
        with self._driver.session() as session:
            result = session.run(cypher, agent_id=agent_id, outcome=outcome, limit=limit)
            return [_decision_summary(self._record_from_node(dict(r["d"]), [])) for r in result]

    def causal_chain(self, trace_id: str, max_depth: int = 20) -> list[str]:
        with self._driver.session() as session:
            result = session.run(
                """
                MATCH path = (d:Decision {trace_id: $trace_id})-[:PRECEDED_BY*0..25]->(root:Decision)
                WHERE NOT (root)-[:PRECEDED_BY]->()
                RETURN [n IN nodes(path) | n.trace_id] AS chain
                ORDER BY length(path) DESC LIMIT 1
                """,
                trace_id=trace_id,
            )
            record = result.single()
            if not record or not record["chain"]:
                return [trace_id]
            return record["chain"]

    def candidates(
        self, agent_id: str | None = None, outcome: str | None = None
    ) -> list[dict[str, Any]]:
        clauses = ["d.agent_id IS NOT NULL"]
        if agent_id:
            clauses.append("d.agent_id = $agent_id")
        if outcome:
            clauses.append("d.outcome = $outcome")
        where = " AND ".join(clauses)
        with self._driver.session() as session:
            result = session.run(
                f"""
                MATCH (d:Decision) WHERE {where}
                OPTIONAL MATCH (d)-[:DECIDED_BECAUSE]->(s:Step)
                RETURN d, collect(s) AS steps
                """,
                agent_id=agent_id,
                outcome=outcome,
            )
            return [
                self._record_from_node(dict(r["d"]), [dict(s) for s in r["steps"]]) for r in result
            ]

    def missing_embeddings(self) -> list[tuple[str, str]]:
        with self._driver.session() as session:
            result = session.run(
                """
                MATCH (d:Decision)
                WHERE d.agent_id IS NOT NULL AND d.embedding IS NULL
                RETURN d.trace_id AS tid, d.precedent_text AS ptext
                """
            )
            return [(r["tid"], r["ptext"] or "") for r in result]

    def set_embedding(self, trace_id: str, embedding: list[float]) -> None:
        with self._driver.session() as session:
            session.run(
                "MATCH (d:Decision {trace_id: $tid}) SET d.embedding = $emb",
                tid=trace_id,
                emb=embedding,
            )

    def count(self) -> int:
        with self._driver.session() as session:
            result = session.run(
                "MATCH (d:Decision) WHERE d.agent_id IS NOT NULL RETURN count(d) AS n"
            )
            record = result.single()
            return record["n"] if record else 0

    def ping(self) -> bool:
        try:
            self._driver.verify_connectivity()
            return True
        except Exception:
            return False

    def close(self) -> None:
        self._driver.close()


def create_store(uri: str | None, user: str, password: str) -> GraphStore:
    """Return a Neo4j store when a URI is set and reachable, else in-memory.

    Graceful by design: a misconfigured or paused Aura instance falls back to the
    in-memory backend with a warning rather than crashing the service at boot.
    """
    if uri:
        try:
            store = Neo4jGraphStore(uri, user, password)
            if store.ping():
                logger.info("Store backend: Neo4j at %s", uri)
                return store
            logger.warning("Neo4j unreachable at %s; using in-memory store", uri)
            store.close()
        except Exception as exc:
            logger.warning("Neo4j init failed (%s); using in-memory store", exc)
    else:
        logger.info("No NCG_NEO4J_URI set; using in-memory store")
    return InMemoryGraphStore()
