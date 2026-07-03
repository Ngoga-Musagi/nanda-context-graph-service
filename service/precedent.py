"""Precedent ranking: recall how similar past decisions were handled.

This is the headline capability the hosted service adds over the original
nanda-context-graph: given a situation, return the most similar prior decisions
with *how they were resolved*, so an agent can act on precedent.

Ranking is hybrid (see :mod:`service.embeddings`):

* **embeddings** — cosine similarity over OpenAI vectors when a key is configured;
* **lexical** — a dependency-free TF-IDF cosine fallback that is always available.

The lexical path is fully deterministic, so the endpoint returns sensible
precedent even with no API key and even if the embedding provider is down.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def build_precedent_text(record: dict[str, Any]) -> str:
    """Flatten a decision into the searchable text used for ranking.

    Combines the situation (inputs), every reasoning-step thought, the outcome,
    and the output so both semantic and lexical matching have signal. Stored on
    the record at write time so ranking never has to re-derive it.
    """
    parts: list[str] = []
    inputs = record.get("inputs") or {}
    for value in inputs.values():
        parts.append(str(value))
    for step in record.get("steps") or []:
        thought = step.get("thought")
        if thought:
            parts.append(str(thought))
        tool = step.get("tool_name")
        if tool:
            parts.append(str(tool))
    output = record.get("output") or {}
    for value in output.values():
        parts.append(str(value))
    if record.get("outcome"):
        parts.append(str(record["outcome"]))
    return " ".join(parts).strip()


def how_it_was_handled(record: dict[str, Any]) -> str:
    """One-line, deterministic summary of how a prior decision resolved.

    Prefers the final ``decide`` step's thought; otherwise the last step's
    thought; always prefixed with the outcome so an agent sees the verdict first.
    """
    outcome = str(record.get("outcome", "unknown"))
    steps = record.get("steps") or []
    decide_steps = [s for s in steps if s.get("step_type") == "decide" and s.get("thought")]
    chosen = decide_steps[-1] if decide_steps else (steps[-1] if steps else None)
    thought = str(chosen.get("thought", "")).strip() if chosen else ""
    if thought:
        return f"{outcome}: {thought}"
    output = record.get("output") or {}
    if output:
        summary = "; ".join(f"{k}={v}" for k, v in output.items())
        return f"{outcome}: {summary}"
    return outcome


def _lexical_scores(query: str, corpus: list[str]) -> list[float]:
    """TF-IDF cosine of ``query`` against each document in ``corpus``.

    Pure-Python, deterministic. IDF is computed over the candidate corpus so
    common words (e.g. "agent", "request") are down-weighted automatically.
    """
    query_tokens = _tokenize(query)
    if not query_tokens or not corpus:
        return [0.0] * len(corpus)

    doc_tokens = [_tokenize(doc) for doc in corpus]
    n_docs = len(doc_tokens)

    # Document frequency over the candidate set (+ the query as one more doc).
    df: Counter[str] = Counter()
    for tokens in [query_tokens, *doc_tokens]:
        for term in set(tokens):
            df[term] += 1
    total_docs = n_docs + 1

    def idf(term: str) -> float:
        return math.log((total_docs + 1) / (df.get(term, 0) + 1)) + 1.0

    def tfidf_vec(tokens: list[str]) -> dict[str, float]:
        counts = Counter(tokens)
        length = len(tokens) or 1
        return {term: (count / length) * idf(term) for term, count in counts.items()}

    def cosine(v1: dict[str, float], v2: dict[str, float]) -> float:
        if not v1 or not v2:
            return 0.0
        shared = set(v1) & set(v2)
        dot = sum(v1[t] * v2[t] for t in shared)
        n1 = math.sqrt(sum(w * w for w in v1.values()))
        n2 = math.sqrt(sum(w * w for w in v2.values()))
        if n1 == 0.0 or n2 == 0.0:
            return 0.0
        return dot / (n1 * n2)

    qvec = tfidf_vec(query_tokens)
    return [cosine(qvec, tfidf_vec(tokens)) for tokens in doc_tokens]


def rank_precedents(
    query: str,
    candidates: list[dict[str, Any]],
    embedder: Any,
    k: int = 5,
) -> tuple[str, list[dict[str, Any]]]:
    """Rank ``candidates`` by similarity to ``query``; return (method, results).

    Uses embedding cosine when the embedder is active *and* every candidate has a
    stored embedding; otherwise the deterministic lexical scorer. Results are
    sorted by descending similarity and truncated to ``k``.
    """
    from service.embeddings import cosine_similarity

    if not candidates:
        return ("lexical", [])

    texts = [c.get("precedent_text") or build_precedent_text(c) for c in candidates]
    method = "lexical"
    scores: list[float]

    have_all_embeddings = embedder is not None and embedder.active and all(
        c.get("embedding") for c in candidates
    )
    query_vec = embedder.embed_one(query) if have_all_embeddings else None
    if have_all_embeddings and query_vec is not None:
        method = "embeddings"
        scores = [cosine_similarity(query_vec, c["embedding"]) for c in candidates]
    else:
        scores = _lexical_scores(query, texts)

    ranked = sorted(
        zip(candidates, scores, strict=False),
        key=lambda pair: pair[1],
        reverse=True,
    )

    results: list[dict[str, Any]] = []
    for record, score in ranked[: max(1, k)]:
        results.append(
            {
                "trace_id": record.get("trace_id"),
                "agent_id": record.get("agent_id"),
                "agent_handle": record.get("agent_handle"),
                "similarity": round(float(score), 4),
                "situation": record.get("inputs") or {},
                "outcome": record.get("outcome"),
                "how_it_was_handled": how_it_was_handled(record),
                "key_steps": [
                    {
                        "step_type": s.get("step_type"),
                        "thought": s.get("thought"),
                        "tool_name": s.get("tool_name"),
                    }
                    for s in (record.get("steps") or [])
                ],
                "timestamp_ms": record.get("timestamp_ms"),
            }
        )
    return (method, results)
