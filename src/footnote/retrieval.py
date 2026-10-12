"""Hybrid retrieval: pgvector similarity and BM25, filtered by metadata and fused by rank.

Dense vectors find passages that mean the same thing in different words; BM25
finds the exact terms a filing uses ("tariffs", "Hopper", "GLP-1"). Neither is
reliable alone on 10-K prose, so both run and their rankings are combined with
reciprocal rank fusion, which needs no score calibration between the two.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from functools import lru_cache

from rank_bm25 import BM25Okapi

from footnote import backend
from footnote.catalog import SECTIONS
from footnote.embed import embed_query
from footnote.schemas import SearchHit

RRF_K = 60
_TOKEN = re.compile(r"[a-z0-9][a-z0-9\-\.]*[a-z0-9]|[a-z0-9]")
_STOP = set(
    [
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "by",
        "with",
        "as",
        "at",
        "from",
        "that",
        "this",
        "these",
        "those",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "it",
        "its",
        "our",
        "we",
        "us",
        "they",
        "their",
        "which",
        "what",
        "how",
        "did",
        "does",
        "do",
        "has",
        "have",
        "had",
        "not",
        "no",
        "may",
        "can",
        "could",
        "would",
        "should",
        "will",
    ]
)


def tokenize(text_: str) -> list[str]:
    return [t for t in _TOKEN.findall(text_.lower()) if t not in _STOP]


@dataclass
class _Corpus:
    ids: list[int]
    meta: dict[int, dict[str, object]]
    bm25: BM25Okapi


_lock = threading.Lock()


@lru_cache(maxsize=1)
def _corpus() -> _Corpus:
    with _lock:
        rows = backend.get().corpus_rows()
    ids = [r["chunk_id"] for r in rows]
    meta = {r["chunk_id"]: dict(r) for r in rows}
    return _Corpus(ids=ids, meta=meta, bm25=BM25Okapi([tokenize(r["text"]) for r in rows]))


def reset_cache() -> None:
    _corpus.cache_clear()


def search(
    query: str,
    tickers: list[str] | None = None,
    years: list[int] | None = None,
    items: list[str] | None = None,
    k: int = 8,
    candidates: int = 50,
    mode: str = "hybrid",
) -> list[SearchHit]:
    """Search passages. `mode` exists so the evaluation can compare each retriever alone."""
    corpus = _corpus()

    def allowed(m: dict[str, object]) -> bool:
        return (
            (not tickers or m["ticker"] in tickers)
            and (not years or m["fiscal_year"] in years)
            and (not items or m["item"] in items)
        )

    # Dense: cosine similarity inside the metadata filter.
    dense = backend.get().dense(embed_query(query), tickers, years, items, candidates)
    if mode == "bm25":
        dense = []
    dense_rank = {cid: i for i, (cid, _) in enumerate(dense)}
    similarity = dict(dense)

    scores = corpus.bm25.get_scores(tokenize(query))
    ranked = sorted(
        (i for i, cid in enumerate(corpus.ids) if allowed(corpus.meta[cid]) and scores[i] > 0),
        key=lambda i: -scores[i],
    )[:candidates]
    bm25_rank = {corpus.ids[i]: r for r, i in enumerate(ranked)} if mode != "dense" else {}

    fused: dict[int, float] = {}
    for cid, r in dense_rank.items():
        fused[cid] = fused.get(cid, 0.0) + 1 / (RRF_K + r + 1)
    for cid, r in bm25_rank.items():
        fused[cid] = fused.get(cid, 0.0) + 1 / (RRF_K + r + 1)

    hits = []
    for cid in sorted(fused, key=lambda c: -fused[c])[:k]:
        m = corpus.meta[cid]
        hits.append(
            SearchHit(
                chunk_id=cid,
                ticker=str(m["ticker"]),
                company=str(m["name"]),
                fiscal_year=int(m["fiscal_year"]),  # type: ignore[call-overload]
                item=str(m["item"]),
                section=SECTIONS.get(str(m["item"]), str(m["item"])),
                page=str(m["page"]) if m["page"] else None,
                score=round(fused[cid], 5),
                vector_rank=dense_rank.get(cid),
                bm25_rank=bm25_rank.get(cid),
                text=str(m["text"]),
                accession=str(m["accession"]),
                url=str(m["url"]),
                similarity=similarity.get(cid),
                filed=str(m["filed"]) if m.get("filed") else None,
            )
        )
    return hits
