"""Local sentence embeddings, so the corpus and the evaluation are reproducible offline."""

from __future__ import annotations

from functools import lru_cache

import numpy as np

from footnote.config import settings


@lru_cache(maxsize=1)
def _model():  # type: ignore[no-untyped-def]
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(settings.embedding_model, device="cpu")


def embed_texts(texts: list[str]) -> list[np.ndarray]:
    vectors = _model().encode(texts, batch_size=64, normalize_embeddings=True, show_progress_bar=False)
    return [np.asarray(v, dtype=np.float32) for v in vectors]


@lru_cache(maxsize=2048)
def embed_query(query: str) -> np.ndarray:
    return embed_texts([query])[0]
