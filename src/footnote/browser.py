"""Entry point for the browser build (Pyodide). The page calls `setup` once and `ask` per question.

Everything except storage and the embedding model is the same code the server runs:
the parser, router, SQL validation, calculations, retrieval fusion, quotation
selection, verifier and narrator.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from footnote import backend
from footnote.embed import EmbeddingsNeeded, supply


def setup(snapshot_dir: str) -> str:
    # In the browser the embedding model runs in JavaScript and supplies vectors on request.
    os.environ["FOOTNOTE_EMBEDDER"] = "external"
    backend.use(backend.MemoryBackend(Path(snapshot_dir)))
    from footnote.retrieval import _corpus

    _corpus()  # build the BM25 index now, not on the first question
    return "ready"


def ask(question: str, previous_answer_id: str | None = None) -> str:
    """Return the answer as JSON, or {"need": [...texts]} when embeddings must be supplied first."""
    from footnote.graph import ask as run

    try:
        answer = run(question, previous_answer_id or None, persist=True)
    except EmbeddingsNeeded as need:
        return json.dumps({"need": need.texts})
    return answer.model_dump_json()


def add_embeddings(texts_json: str, vectors_json: str) -> None:
    supply(json.loads(texts_json), json.loads(vectors_json))
