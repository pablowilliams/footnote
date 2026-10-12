"""Storage behind the answering pipeline, in two interchangeable forms.

`PostgresBackend` is the production store: PostgreSQL with pgvector, generated SQL
run under a read-only role. `MemoryBackend` loads the same committed snapshot into
an in-memory SQLite database and a NumPy matrix of embeddings. It needs no server,
which is what lets the whole system run inside a web browser (see `browser.py`),
and the evaluation runs against both to show they give the same answers.

Both validate every generated query with sqlglot before running it.
"""

from __future__ import annotations

import json
import os
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from footnote.catalog import METRICS
from footnote.sqlguard import validate

EVIDENCE_KEYS = ("sentence", "page", "doc_accession", "doc_fiscal_year", "location")

_METRIC_COLUMNS = """c.ticker, c.name AS company, mv.metric_key, mv.fiscal_year, mv.period_end, mv.value, mv.unit,
       mv.source_kind, mv.xbrl_tag, mv.extraction_confidence, mv.found_in_document, mv.derivation, mv.evidence,
       f.accession, f.form, f.filed, f.url,
       df.accession AS doc_accession, df.form AS doc_form, df.filed AS doc_filed, df.url AS doc_url"""

PG_METRIC_SQL = f"""
SELECT {_METRIC_COLUMNS}
FROM metric_value mv
JOIN company c ON c.company_id = mv.company_id
LEFT JOIN filing f ON f.filing_id = mv.filing_id
LEFT JOIN filing df ON df.accession = mv.evidence ->> 'doc_accession'
WHERE c.ticker = ANY(:tickers)
  AND mv.metric_key = ANY(:metrics)
  AND mv.fiscal_year BETWEEN :year_from AND :year_to
ORDER BY c.ticker, mv.metric_key, mv.fiscal_year
LIMIT 500
"""

PG_COVERAGE_SQL = """
SELECT c.ticker, MIN(mv.fiscal_year) AS first_year, MAX(mv.fiscal_year) AS last_year
FROM metric_value mv
JOIN company c ON c.company_id = mv.company_id
WHERE c.ticker = ANY(:tickers) AND mv.metric_key = ANY(:metrics)
GROUP BY c.ticker
LIMIT 500
"""


def evidence_of(extra: dict[str, Any]) -> dict[str, Any] | None:
    """The part of a metric value's lineage that a citation needs."""
    return {k: extra[k] for k in EVIDENCE_KEYS if k in extra} or None


class Backend(Protocol):
    name: str

    def metric_rows(
        self, tickers: list[str], metrics: list[str], year_from: int, year_to: int
    ) -> tuple[list[dict[str, Any]], str]: ...
    def coverage(self, tickers: list[str], metrics: list[str]) -> list[dict[str, Any]]: ...
    def text_years(self, tickers: list[str]) -> dict[str, list[int]]: ...
    def corpus_rows(self) -> list[dict[str, Any]]: ...
    def dense(
        self, query: np.ndarray, tickers: list[str] | None, years: list[int] | None, items: list[str] | None, n: int
    ) -> list[tuple[int, float]]: ...
    def save_answer(
        self, answer_id: str, question: str, route: str, status: str, latency_ms: int, response_json: str
    ) -> None: ...
    def load_answer(self, answer_id: str) -> dict[str, Any] | None: ...


# ---------------------------------------------------------------------------- PostgreSQL


class PostgresBackend:
    name = "postgres"

    def _readonly(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        from sqlalchemy import text

        from footnote.db import connect

        validate(sql)
        with connect() as conn:
            conn.execute(text("SET TRANSACTION READ ONLY"))
            conn.execute(text("SET LOCAL statement_timeout = '3s'"))
            conn.execute(text("SET LOCAL ROLE footnote_reader"))
            rows = conn.execute(text(sql), params).mappings().all()
        return [dict(r) for r in rows]

    def metric_rows(
        self, tickers: list[str], metrics: list[str], year_from: int, year_to: int
    ) -> tuple[list[dict[str, Any]], str]:
        params = {"tickers": tickers, "metrics": metrics, "year_from": year_from, "year_to": year_to}
        return self._readonly(PG_METRIC_SQL, params), PG_METRIC_SQL.strip()

    def coverage(self, tickers: list[str], metrics: list[str]) -> list[dict[str, Any]]:
        return self._readonly(PG_COVERAGE_SQL, {"tickers": tickers, "metrics": metrics})

    def text_years(self, tickers: list[str]) -> dict[str, list[int]]:
        from sqlalchemy import text

        from footnote.db import connect

        with connect() as conn:
            rows = conn.execute(
                text(
                    """SELECT c.ticker, f.fiscal_year FROM filing f JOIN company c USING (company_id)
                       WHERE f.has_text AND c.ticker = ANY(:t) ORDER BY f.fiscal_year"""
                ),
                {"t": tickers},
            ).all()
        out: dict[str, list[int]] = {}
        for r in rows:
            out.setdefault(r.ticker, []).append(r.fiscal_year)
        return out

    def corpus_rows(self) -> list[dict[str, Any]]:
        from sqlalchemy import text

        from footnote.db import connect

        with connect() as conn:
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
        return [dict(r) for r in rows]

    def dense(
        self, query: np.ndarray, tickers: list[str] | None, years: list[int] | None, items: list[str] | None, n: int
    ) -> list[tuple[int, float]]:
        from sqlalchemy import text

        from footnote.db import connect

        clauses, params = ["embedding IS NOT NULL"], {"q": query, "n": n}
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
            rows = conn.execute(
                text(
                    f"""SELECT chunk_id, 1 - (embedding <=> :q) AS similarity FROM chunk
                        WHERE {" AND ".join(clauses)} ORDER BY embedding <=> :q LIMIT :n"""
                ),
                params,
            ).all()
        return [(r.chunk_id, float(r.similarity)) for r in rows]

    def save_answer(self, answer_id: str, question: str, route: str, status: str, latency_ms: int, response_json: str) -> None:
        from sqlalchemy import text

        from footnote.db import connect

        with connect() as conn:
            conn.execute(
                text(
                    """INSERT INTO answer_log (answer_id, question, route, status, latency_ms, response)
                       VALUES (:id, :q, :route, :status, :ms, CAST(:resp AS jsonb))"""
                ),
                {"id": answer_id, "q": question, "route": route, "status": status, "ms": latency_ms, "resp": response_json},
            )

    def load_answer(self, answer_id: str) -> dict[str, Any] | None:
        from sqlalchemy import text

        from footnote.db import connect

        with connect() as conn:
            row = conn.execute(text("SELECT response FROM answer_log WHERE answer_id = :id"), {"id": answer_id}).first()
        return dict(row.response) if row else None


# ---------------------------------------------------------------------------- in memory


def _in_list(name: str, values: list[Any], params: dict[str, Any]) -> str:
    """SQLite has no array parameters, so a list becomes numbered placeholders."""
    keys = []
    for i, v in enumerate(values):
        params[f"{name}{i}"] = v
        keys.append(f":{name}{i}")
    return "(" + ", ".join(keys or ["NULL"]) + ")"


class MemoryBackend:
    """The snapshot in SQLite and NumPy. Identical data, no server."""

    name = "memory"

    def __init__(self, snapshot: Path) -> None:
        import sqlite3

        from footnote.snapshot_io import read_jsonl

        companies = read_jsonl(snapshot / "companies.jsonl")
        filings = read_jsonl(snapshot / "filings.jsonl")
        values = read_jsonl(snapshot / "metric_values.jsonl")
        chunks = read_jsonl(snapshot / "chunks.jsonl.gz")

        db = sqlite3.connect(":memory:", check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.executescript(
            """
            CREATE TABLE company (company_id INTEGER PRIMARY KEY, ticker TEXT UNIQUE, cik INTEGER, name TEXT);
            CREATE TABLE filing (filing_id INTEGER PRIMARY KEY, company_id INTEGER, accession TEXT UNIQUE, form TEXT,
                                 fiscal_year INTEGER, period_end TEXT, filed TEXT, url TEXT, has_text INTEGER);
            CREATE TABLE metric (metric_key TEXT PRIMARY KEY, label TEXT, unit TEXT, kind TEXT, source TEXT);
            CREATE TABLE metric_value (metric_value_id INTEGER PRIMARY KEY, company_id INTEGER, metric_key TEXT,
                                       fiscal_year INTEGER, period_end TEXT, value REAL, unit TEXT, source_kind TEXT,
                                       filing_id INTEGER, xbrl_tag TEXT, extraction_confidence REAL,
                                       found_in_document INTEGER, derivation TEXT, evidence TEXT);
            """
        )
        company_id = {}
        for i, c in enumerate(companies, 1):
            company_id[c["ticker"]] = i
            db.execute("INSERT INTO company VALUES (?, ?, ?, ?)", (i, c["ticker"], c["cik"], c["name"]))
        filing_id = {}
        for i, f in enumerate(filings, 1):
            filing_id[f["accession"]] = i
            db.execute(
                "INSERT INTO filing VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    i,
                    company_id[f["ticker"]],
                    f["accession"],
                    f["form"],
                    f["fiscal_year"],
                    f["period_end"],
                    f["filed"],
                    f["url"],
                    int(bool(f.get("has_text"))),
                ),
            )
        db.executemany("INSERT INTO metric VALUES (?, ?, ?, ?, ?)", [(m.key, m.label, m.unit, m.kind, m.source) for m in METRICS])
        db.executemany(
            "INSERT INTO metric_value VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    company_id[v["ticker"]],
                    v["metric_key"],
                    v["fiscal_year"],
                    v["period_end"],
                    v["value"],
                    v["unit"],
                    v["source_kind"],
                    filing_id.get(v["accession"]) if v.get("accession") else None,
                    v.get("xbrl_tag"),
                    v.get("extraction_confidence", 1.0),
                    None if v["extra"].get("found_in_document") is None else int(v["extra"]["found_in_document"]),
                    json.dumps(v["derivation"]) if v.get("derivation") else None,
                    json.dumps(evidence_of(v["extra"])) if evidence_of(v["extra"]) else None,
                )
                for v in values
            ],
        )
        db.commit()
        db.execute("PRAGMA query_only = ON")  # the in-memory equivalent of the read-only role
        self.db = db

        names = {c["ticker"]: c["name"] for c in companies}
        by_acc = {f["accession"]: f for f in filings}
        self.chunks = [
            {
                "chunk_id": i,
                "text": c["text"],
                "fiscal_year": c["fiscal_year"],
                "item": c["item"],
                "page": c["page"],
                "ticker": c["ticker"],
                "name": names[c["ticker"]],
                "accession": c["accession"],
                "url": by_acc[c["accession"]]["url"],
                "filed": date.fromisoformat(by_acc[c["accession"]]["filed"]),
            }
            for i, c in enumerate(chunks, 1)
        ]
        data = np.load(snapshot / "embeddings.npz", allow_pickle=False)
        cache = dict(zip(data["hashes"].tolist(), data["vectors"], strict=True))
        self.matrix = np.stack([np.asarray(cache[c["content_hash"]], dtype=np.float32) for c in chunks])
        self._ticker = np.array([c["ticker"] for c in chunks])
        self._year = np.array([c["fiscal_year"] for c in chunks])
        self._item = np.array([c["item"] for c in chunks])
        self.answers: dict[str, dict[str, Any]] = {}

    def _query(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        validate(sql, dialect="sqlite")
        return [dict(r) for r in self.db.execute(sql, params).fetchall()]

    def metric_rows(
        self, tickers: list[str], metrics: list[str], year_from: int, year_to: int
    ) -> tuple[list[dict[str, Any]], str]:
        params: dict[str, Any] = {"year_from": year_from, "year_to": year_to}
        sql = f"""
SELECT {_METRIC_COLUMNS}
FROM metric_value mv
JOIN company c ON c.company_id = mv.company_id
LEFT JOIN filing f ON f.filing_id = mv.filing_id
LEFT JOIN filing df ON df.accession = json_extract(mv.evidence, '$.doc_accession')
WHERE c.ticker IN {_in_list("t", tickers, params)}
  AND mv.metric_key IN {_in_list("m", metrics, params)}
  AND mv.fiscal_year BETWEEN :year_from AND :year_to
ORDER BY c.ticker, mv.metric_key, mv.fiscal_year
LIMIT 500
"""
        rows = self._query(sql, params)
        for r in rows:
            r["period_end"] = date.fromisoformat(r["period_end"]) if r["period_end"] else None
            for k in ("filed", "doc_filed"):
                r[k] = date.fromisoformat(r[k]) if r[k] else None
            r["derivation"] = json.loads(r["derivation"]) if r["derivation"] else None
            r["evidence"] = json.loads(r["evidence"]) if r["evidence"] else None
            r["found_in_document"] = None if r["found_in_document"] is None else bool(r["found_in_document"])
        return rows, sql.strip()

    def coverage(self, tickers: list[str], metrics: list[str]) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        sql = f"""
SELECT c.ticker, MIN(mv.fiscal_year) AS first_year, MAX(mv.fiscal_year) AS last_year
FROM metric_value mv
JOIN company c ON c.company_id = mv.company_id
WHERE c.ticker IN {_in_list("t", tickers, params)} AND mv.metric_key IN {_in_list("m", metrics, params)}
GROUP BY c.ticker
LIMIT 500
"""
        return self._query(sql, params)

    def text_years(self, tickers: list[str]) -> dict[str, list[int]]:
        out: dict[str, list[int]] = {}
        for t in tickers:
            years = sorted({c["fiscal_year"] for c in self.chunks if c["ticker"] == t})
            if years:
                out[t] = years
        return out

    def corpus_rows(self) -> list[dict[str, Any]]:
        return self.chunks

    def dense(
        self, query: np.ndarray, tickers: list[str] | None, years: list[int] | None, items: list[str] | None, n: int
    ) -> list[tuple[int, float]]:
        mask = np.ones(len(self.chunks), dtype=bool)
        if tickers:
            mask &= np.isin(self._ticker, tickers)
        if years:
            mask &= np.isin(self._year, years)
        if items:
            mask &= np.isin(self._item, items)
        idx = np.flatnonzero(mask)
        if not len(idx):
            return []
        sims = self.matrix[idx] @ np.asarray(query, dtype=np.float32)
        top = idx[np.argsort(-sims, kind="stable")[:n]]
        order = {i: s for i, s in zip(idx, sims, strict=True)}
        return [(int(i) + 1, float(order[i])) for i in top]

    def save_answer(self, answer_id: str, question: str, route: str, status: str, latency_ms: int, response_json: str) -> None:
        self.answers[answer_id] = json.loads(response_json)

    def load_answer(self, answer_id: str) -> dict[str, Any] | None:
        return self.answers.get(answer_id)


# ---------------------------------------------------------------------------- selection

_override: Backend | None = None


def use(backend: Backend | None) -> None:
    """Install a backend explicitly (the browser build does this); None restores the default."""
    global _override
    _override = backend
    get.cache_clear()


@lru_cache(maxsize=1)
def get() -> Backend:
    if _override is not None:
        return _override
    if os.environ.get("FOOTNOTE_BACKEND", "postgres") == "memory":
        from footnote.config import settings

        return MemoryBackend(settings.snapshot_dir)
    return PostgresBackend()
