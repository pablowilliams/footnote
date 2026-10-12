"""Sentence embeddings, from a local model or from a provider outside Python.

On a server the model runs here (sentence-transformers). In the browser build the
same model runs in JavaScript (transformers.js), which Python cannot call
synchronously. There, embeddings are supplied ahead of time: a request for a text
that has not been supplied raises `EmbeddingsNeeded`, the page embeds those texts
and runs the question again. The pipeline is deterministic, so the second run asks
for exactly what the first did and then completes.
"""

from __future__ import annotations

import os
from functools import lru_cache

import numpy as np

from footnote.config import settings

_supplied: dict[str, np.ndarray] = {}


class EmbeddingsNeeded(Exception):
    def __init__(self, texts: list[str]) -> None:
        super().__init__(f"{len(texts)} texts need embeddings")
        self.texts = texts


def supply(texts: list[str], vectors: list[list[float]] | np.ndarray) -> None:
    for t, v in zip(texts, vectors, strict=True):
        _supplied[t] = np.asarray(v, dtype=np.float32)


@lru_cache(maxsize=1)
def _model():  # type: ignore[no-untyped-def]
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(settings.embedding_model, device="cpu")


def embed_texts(texts: list[str]) -> list[np.ndarray]:
    if os.environ.get("FOOTNOTE_EMBEDDER") == "external":
        missing = list(dict.fromkeys(t for t in texts if t not in _supplied))
        if missing:
            raise EmbeddingsNeeded(missing)
        return [_supplied[t] for t in texts]
    vectors = _model().encode(texts, batch_size=64, normalize_embeddings=True, show_progress_bar=False)
    return [np.asarray(v, dtype=np.float32) for v in vectors]


def embed_query(query: str) -> np.ndarray:
    return _embed_query_cached(query) if os.environ.get("FOOTNOTE_EMBEDDER") != "external" else embed_texts([query])[0]


@lru_cache(maxsize=2048)
def _embed_query_cached(query: str) -> np.ndarray:
    return embed_texts([query])[0]
