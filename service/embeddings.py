"""Hybrid embedding engine for precedent ranking.

Quality engine: OpenAI ``text-embedding-3-small`` when ``OPENAI_API_KEY`` is set.
If the key is absent or a call fails, the embedder reports itself inactive and the
precedent ranker transparently falls back to a deterministic lexical scorer
(see :mod:`service.precedent`). The service therefore never hard-depends on an
external provider being reachable — it degrades, it does not break.

The provider is intentionally thin and swappable (OpenAI today, Voyage later).
"""

from __future__ import annotations

import logging
import math
import os

logger = logging.getLogger("ncg.embeddings")

_DEFAULT_MODEL = "text-embedding-3-small"


class Embedder:
    """Embeds text via OpenAI when configured, else stays inactive.

    ``active`` is the single flag the rest of the service checks: when False the
    precedent ranker uses its lexical fallback. ``embed`` returns ``None`` on any
    failure so a transient provider error at query time degrades gracefully
    instead of surfacing a 500 to the calling agent.
    """

    def __init__(self, api_key: str | None = None, model: str = _DEFAULT_MODEL) -> None:
        self._model = model
        self._api_key = api_key if api_key is not None else os.getenv("OPENAI_API_KEY")
        self._client = None
        if self._api_key:
            try:
                from openai import OpenAI

                self._client = OpenAI(api_key=self._api_key)
                logger.info("Embedder active: OpenAI %s", model)
            except Exception as exc:  # pragma: no cover - import/init guard
                logger.warning("OpenAI client init failed, using lexical fallback: %s", exc)
                self._client = None
        else:
            logger.info("No OPENAI_API_KEY: precedent will use lexical ranking")

    @property
    def active(self) -> bool:
        """True when embeddings are available; False routes callers to lexical."""
        return self._client is not None

    def embed(self, texts: list[str]) -> list[list[float]] | None:
        """Return one embedding vector per input text, or ``None`` on any failure.

        Returning ``None`` (rather than raising) is deliberate: a rate-limit or
        outage at query time should fall back to lexical ranking, not error.
        """
        if not self._client or not texts:
            return None
        try:
            resp = self._client.embeddings.create(model=self._model, input=texts)
            return [item.embedding for item in resp.data]
        except Exception as exc:
            logger.warning("Embedding call failed, falling back to lexical: %s", exc)
            return None

    def embed_one(self, text: str) -> list[float] | None:
        """Convenience wrapper returning a single vector or ``None``."""
        vectors = self.embed([text])
        return vectors[0] if vectors else None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors; 0.0 if either is degenerate."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)
