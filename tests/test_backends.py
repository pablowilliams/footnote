"""The in-memory backend (used by the browser build) must answer exactly as PostgreSQL does."""

import json

import pytest

from footnote import backend
from footnote.config import settings
from footnote.graph import ask

QUESTIONS = [
    "What was Apple's revenue in fiscal 2024?",
    "How did Meta's revenue change from 2023 to 2024?",
    "Compare free cash flow across Apple, Microsoft and Alphabet in 2024.",
    "Why did Intel's operating margin decline in 2024?",
    "What does Apple say about tariffs?",
    "How many employees did Apple have at the end of fiscal 2024?",
    "What was Apple's revenue in 2015?",
]


@pytest.fixture(scope="module")
def memory():
    mem = backend.MemoryBackend(settings.snapshot_dir)
    yield mem
    backend.use(None)  # type: ignore[arg-type]
    backend.get.cache_clear()


def _answers(b):
    from footnote.retrieval import reset_cache

    backend.use(b)
    reset_cache()
    out = [json.loads(ask(q, persist=False).model_dump_json(exclude={"answer_id", "latency_ms", "sql"})) for q in QUESTIONS]
    reset_cache()
    return out


def _same(a, b):
    """Equal, allowing for the last digits of a float: PostgreSQL stores derived ratios as numeric."""
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, float) or isinstance(b, float):
        return a == pytest.approx(b, rel=1e-12, abs=1e-15)
    return a == b


def test_memory_backend_matches_postgres(db, memory):
    mem, pg = _answers(memory), _answers(backend.PostgresBackend())
    assert [a["answer"] for a in mem] == [a["answer"] for a in pg]
    assert _same(mem, pg)


def test_memory_backend_validates_sqlite_queries(memory):
    rows, sql = memory.metric_rows(["AAPL"], ["revenue"], 2024, 2024)
    assert rows[0]["value"] == 391_035_000_000 and "json_extract" in sql


def test_memory_backend_is_read_only(memory):
    import sqlite3

    with pytest.raises(sqlite3.OperationalError):
        memory.db.execute("DELETE FROM metric_value")


def test_browser_entry_point_asks_for_embeddings_then_answers(memory, monkeypatch):
    from footnote import browser, embed

    monkeypatch.setenv("FOOTNOTE_EMBEDDER", "external")
    backend.use(memory)
    out = json.loads(browser.ask("What does Apple say about tariffs?"))
    assert out.get("need")
    for _ in range(4):
        embed.supply(out["need"], embed._model().encode(out["need"], normalize_embeddings=True))
        out = json.loads(browser.ask("What does Apple say about tariffs?"))
        if "need" not in out:
            break
    assert out["status"] == "verified" and "tariff" in out["answer"].lower()
