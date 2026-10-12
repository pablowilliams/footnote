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
from sqlalchemy import text

from footnote.catalog import SECTIONS
from footnote.db import connect
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
    with _lock, connect() as conn:
        rows = (
            conn.execute(
                text(
                    """SELECT ch.chunk_id, ch.text, ch.fiscal_year, ch.item, ch.page, c.ticker, c.name, f.accession, f.url, f.filed
                   FROM chunk ch JOIN company c USING (company_id) JOIN filing f USING (filing_id)
                   ORDER BY ch.chunk_id"""
                )
            )
            .mappings()
            .all()
        )
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

    # Dense: exact cosine search inside the filter. The HNSW index serves unfiltered
    # queries; with a company and year filter the candidate set is small enough that
    # an exact scan is both faster and free of the recall loss of a filtered ANN search.
    clauses, params = ["embedding IS NOT NULL"], {"q": embed_query(query), "n": candidates}
    if tickers:
        clauses.append("company_id IN (SELECT company_id FROM company WHERE ticker = ANY(:tickers))")
        params["tickers"] = tickers
    if years:
        clauses.append("fiscal_year = ANY(:years)")
        params["years"] = years
    if items:
        clauses.append("item = ANY(:items)")
        params["items"] = items
    with connect() as conn:
        dense = conn.execute(
            text(
                f"""SELECT chunk_id, 1 - (embedding <=> :q) AS similarity FROM chunk
                    WHERE {" AND ".join(clauses)} ORDER BY embedding <=> :q LIMIT :n"""
            ),
            params,
        ).all()
    if mode == "bm25":
        dense = []
    dense_rank = {r.chunk_id: i for i, r in enumerate(dense)}
    similarity = {r.chunk_id: float(r.similarity) for r in dense}

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
