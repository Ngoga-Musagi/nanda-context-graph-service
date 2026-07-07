"""Unit tests for the store, precedent ranking, and embedding helpers."""

from __future__ import annotations

from service.embeddings import Embedder, cosine_similarity
from service.precedent import (
    _lexical_scores,
    build_precedent_text,
    how_it_was_handled,
    rank_precedents,
)
from service.store import InMemoryGraphStore, ResilientStore


# ── resilient store (Aura-pause safety) ───────────────────────────


class _FlakyStore:
    """A primary that works until `.break_now()`, then raises on every call."""

    backend = "neo4j"

    def __init__(self):
        self._inner = InMemoryGraphStore()
        self._broken = False

    def break_now(self):
        self._broken = True

    def __getattr__(self, name):
        inner_attr = getattr(self._inner, name)
        if not callable(inner_attr):
            return inner_attr

        def _wrapped(*args, **kwargs):
            if self._broken:
                raise ConnectionError("simulated Aura pause: name resolution failed")
            return inner_attr(*args, **kwargs)

        return _wrapped


def _seed():
    return [
        {"trace_id": "seed-1", "agent_id": "a", "outcome": "failure",
         "timestamp_ms": 1, "inputs": {"request": "x"}, "output": {}, "steps": [],
         "precedent_text": "x", "embedding": None},
    ]


def test_resilient_store_serves_from_primary_until_it_fails():
    primary = _FlakyStore()
    store = ResilientStore(primary, _seed())
    store.write_trace(_seed()[0])
    assert store.backend == "neo4j"
    assert store.get_trace("seed-1")["agent_id"] == "a"


def test_resilient_store_degrades_to_seeded_fallback_on_primary_failure():
    primary = _FlakyStore()
    store = ResilientStore(primary, _seed())
    primary.break_now()  # Aura "pauses"
    # A read that would have 500-ed now transparently answers from the fallback,
    # which is pre-loaded with the seed decisions.
    got = store.get_trace("seed-1")
    assert got is not None
    assert got["agent_id"] == "a"
    assert store.backend == "memory(fallback)"
    # stays degraded and still serves subsequent calls
    assert store.count() == 1
    assert store.ping() is True


# ── precedent text + summary ──────────────────────────────────────


def test_build_precedent_text_includes_situation_and_steps():
    rec = {
        "inputs": {"request": "Gold member 15% discount"},
        "steps": [{"thought": "Policy: 10% ceiling", "tool_name": "policy-db"}],
        "output": {"approved": False},
        "outcome": "failure",
    }
    text = build_precedent_text(rec)
    assert "Gold member 15% discount" in text
    assert "Policy: 10% ceiling" in text
    assert "failure" in text


def test_how_it_was_handled_prefers_decide_step():
    rec = {
        "outcome": "failure",
        "steps": [
            {"step_type": "retrieve", "thought": "looked up policy"},
            {"step_type": "decide", "thought": "Rejected: exceeds ceiling"},
        ],
    }
    assert how_it_was_handled(rec) == "failure: Rejected: exceeds ceiling"


# ── lexical ranking ───────────────────────────────────────────────


def test_lexical_scores_rank_relevant_doc_first():
    query = "gold member discount ski trip"
    corpus = [
        "refund requested for a defective product",
        "gold member requests a discount for a ski trip rental",
        "vendor selection for cloud gpus",
    ]
    scores = _lexical_scores(query, corpus)
    assert scores[1] == max(scores)
    assert scores[1] > 0


def test_lexical_scores_empty_query():
    assert _lexical_scores("", ["anything"]) == [0.0]


# ── cosine ────────────────────────────────────────────────────────


def test_cosine_identity_and_orthogonal():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine_similarity([], [1.0]) == 0.0


def test_embedder_inactive_without_key():
    emb = Embedder(api_key=None)
    assert emb.active is False
    assert emb.embed(["x"]) is None
    assert emb.embed_one("x") is None


# ── rank_precedents falls back to lexical without embedder ─────────


def test_rank_precedents_lexical_without_embedder():
    candidates = [
        {"trace_id": "a", "inputs": {"r": "refund for defective item"}, "outcome": "success",
         "steps": [{"step_type": "decide", "thought": "full refund"}], "precedent_text": "refund defective full refund success"},
        {"trace_id": "b", "inputs": {"r": "gold discount ski trip"}, "outcome": "failure",
         "steps": [{"step_type": "decide", "thought": "rejected ceiling"}], "precedent_text": "gold discount ski trip rejected ceiling failure"},
    ]
    method, results = rank_precedents("gold member ski discount", candidates, Embedder(api_key=None), k=2)
    assert method == "lexical"
    assert results[0]["trace_id"] == "b"
    assert results[0]["how_it_was_handled"].startswith("failure")


# ── in-memory store ───────────────────────────────────────────────


def _rec(tid, agent, outcome="success", parent=None, ts=1):
    return {
        "trace_id": tid, "agent_id": agent, "agent_handle": None, "parent_trace_id": parent,
        "a2a_msg_id": None, "outcome": outcome, "timestamp_ms": ts, "duration_ms": None,
        "inputs": {"x": tid}, "output": {}, "steps": [{"step_type": "decide", "thought": tid}],
        "precedent_text": f"{tid} {agent}", "embedding": None,
    }


def test_store_write_get_and_why():
    s = InMemoryGraphStore()
    s.write_trace(_rec("t1", "ag", ts=1))
    s.write_trace(_rec("t2", "ag", ts=2))
    assert s.count() == 2
    assert s.get_trace("t1")["agent_id"] == "ag"
    assert s.why("ag")["decision"]["trace_id"] == "t2"  # newest


def test_store_causal_chain_follows_parents():
    s = InMemoryGraphStore()
    s.write_trace(_rec("root", "ag", parent=None))
    s.write_trace(_rec("mid", "ag", parent="root"))
    s.write_trace(_rec("leaf", "ag", parent="mid"))
    assert s.causal_chain("leaf") == ["leaf", "mid", "root"]


def test_store_history_and_outcome_filter():
    s = InMemoryGraphStore()
    s.write_trace(_rec("ok1", "ag", outcome="success", ts=1))
    s.write_trace(_rec("bad1", "ag", outcome="failure", ts=2))
    hist = s.agent_history("ag")
    assert [d["trace_id"] for d in hist] == ["bad1", "ok1"]  # newest first
    only_fail = s.agent_history("ag", outcome="failure")
    assert [d["trace_id"] for d in only_fail] == ["bad1"]


def test_store_missing_and_set_embedding():
    s = InMemoryGraphStore()
    s.write_trace(_rec("t1", "ag"))
    assert s.missing_embeddings() == [("t1", "t1 ag")]
    s.set_embedding("t1", [0.1, 0.2])
    assert s.missing_embeddings() == []
    assert s.candidates()[0]["embedding"] == [0.1, 0.2]
