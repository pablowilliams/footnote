"""Database access: engine, numbered SQL migrations, and loading a snapshot."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pgvector.psycopg import register_vector
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.engine import Connection

from footnote.catalog import METRICS
from footnote.config import ROOT, settings
from footnote.ingest.pipeline import read_jsonl
from footnote.logs import log

MIGRATIONS = ROOT / "migrations"
_engines: dict[str, Engine] = {}


def engine(url: str | None = None) -> Engine:
    url = url or settings.database_url
    if url not in _engines:
        eng = create_engine(url, pool_pre_ping=True, pool_size=5)

        @event.listens_for(eng, "connect")
        def _register(dbapi_conn: Any, _: Any) -> None:
            # The vector type does not exist until the first migration has run.
            with contextlib.suppress(Exception):
                register_vector(dbapi_conn)

        _engines[url] = eng
    return _engines[url]


@contextmanager
def connect(url: str | None = None) -> Iterator[Connection]:
    with engine(url).begin() as conn:
        yield conn


def migrate(url: str | None = None) -> list[str]:
    applied: list[str] = []
    with connect(url) as conn:
        conn.execute(
            text("CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, applied_at timestamptz DEFAULT now())")
        )
        done = {r[0] for r in conn.execute(text("SELECT version FROM schema_migrations"))}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.stem in done:
                continue
            conn.exec_driver_sql(path.read_text())
            conn.execute(text("INSERT INTO schema_migrations (version) VALUES (:v)"), {"v": path.stem})
            applied.append(path.stem)
    # Connections opened before the extension existed could not register the type.
    engine(url).dispose()
    return applied


def load_snapshot(snapshot: Path | None = None, url: str | None = None, embed: bool = True) -> dict[str, int]:
    snapshot = snapshot or settings.snapshot_dir
    companies = read_jsonl(snapshot / "companies.jsonl")
    filings = read_jsonl(snapshot / "filings.jsonl")
    sections = read_jsonl(snapshot / "sections.jsonl")
    chunks = read_jsonl(snapshot / "chunks.jsonl.gz")
    facts = read_jsonl(snapshot / "xbrl_facts.jsonl.gz")
    values = read_jsonl(snapshot / "metric_values.jsonl")

    with connect(url) as conn:
        conn.execute(text("TRUNCATE company, filing, section, chunk, metric, xbrl_fact, metric_value RESTART IDENTITY CASCADE"))
        conn.execute(
            text("INSERT INTO metric (metric_key, label, unit, kind, source) VALUES (:key, :label, :unit, :kind, :source)"),
            [{"key": m.key, "label": m.label, "unit": m.unit, "kind": m.kind, "source": m.source} for m in METRICS],
        )
        company_id = {}
        for c in companies:
            company_id[c["ticker"]] = conn.execute(
                text("INSERT INTO company (ticker, cik, name) VALUES (:ticker, :cik, :name) RETURNING company_id"), c
            ).scalar_one()
        filing_id = {}
        for f in filings:
            filing_id[f["accession"]] = conn.execute(
                text(
                    """INSERT INTO filing (company_id, accession, form, fiscal_year, period_end, filed,
                       primary_document, url, has_text, page_count)
                       VALUES (:cid, :accession, :form, :fiscal_year, :period_end, :filed,
                       :primary_document, :url, :has_text, :page_count) RETURNING filing_id"""
                ),
                {**f, "cid": company_id[f["ticker"]], "page_count": f.get("page_count")},
            ).scalar_one()
        section_id = {}
        for s in sections:
            section_id[(s["accession"], s["item"])] = conn.execute(
                text(
                    """INSERT INTO section (filing_id, item, title, start_page, end_page, word_count)
                       VALUES (:fid, :item, :title, :start_page, :end_page, :word_count) RETURNING section_id"""
                ),
                {**s, "fid": filing_id[s["accession"]]},
            ).scalar_one()
        conn.execute(
            text(
                """INSERT INTO chunk (section_id, filing_id, company_id, fiscal_year, item, ordinal, page, text, content_hash)
                   VALUES (:sid, :fid, :cid, :fiscal_year, :item, :ordinal, :page, :text, :content_hash)"""
            ),
            [
                {
                    **c,
                    "sid": section_id[(c["accession"], c["item"])],
                    "fid": filing_id[c["accession"]],
                    "cid": company_id[c["ticker"]],
                }
                for c in chunks
            ],
        )
        conn.execute(
            text(
                """INSERT INTO xbrl_fact (company_id, tag, unit, period_start, period_end, value, accession, form, filed, fy, fp, frame)
                   VALUES (:cid, :tag, :unit, :period_start, :period_end, :value, :accession, :form, :filed, :fy, :fp, :frame)"""
            ),
            [{**f, "cid": company_id[f["ticker"]]} for f in facts],
        )
        conn.execute(
            text(
                """INSERT INTO metric_value (company_id, metric_key, fiscal_year, period_start, period_end, value, unit,
                   source_kind, filing_id, xbrl_tag, extraction_confidence, found_in_document, restated_from,
                   derivation, evidence)
                   VALUES (:cid, :metric_key, :fiscal_year, :period_start, :period_end, :value, :unit, :source_kind,
                   :fid, :xbrl_tag, :extraction_confidence, :found, :restated_from,
                   CAST(:derivation AS jsonb), CAST(:evidence AS jsonb))"""
            ),
            [
                {
                    **v,
                    "cid": company_id[v["ticker"]],
                    "fid": filing_id.get(v["accession"]) if v.get("accession") else None,
                    "found": v["extra"].get("found_in_document"),
                    "derivation": json.dumps(v["derivation"]) if v.get("derivation") else None,
                    "evidence": json.dumps(
                        {
                            k: v["extra"][k]
                            for k in ("sentence", "page", "doc_accession", "doc_fiscal_year", "location")
                            if k in v["extra"]
                        }
                        or None
                    ),
                }
                for v in values
            ],
        )
    counts = {
        "companies": len(companies),
        "filings": len(filings),
        "sections": len(sections),
        "chunks": len(chunks),
        "xbrl_facts": len(facts),
        "metric_values": len(values),
    }
    log.info("db.loaded", **counts)
    if embed:
        counts["embedded"] = embed_chunks(url)
    return counts


def _embedding_cache_path() -> Path:
    return settings.snapshot_dir / "embeddings.npz"


def embed_chunks(url: str | None = None, batch: int = 256) -> int:
    """Embed every chunk, reusing vectors cached by content hash.

    The cache is committed with the snapshot, so a fresh database is searchable in
    seconds and the vectors are identical on every machine. Vectors are stored at
    half precision; the loss is far below what changes a nearest-neighbour ranking.
    """
    import numpy as np

    from footnote.embed import embed_texts

    cache: dict[str, np.ndarray] = {}
    path = _embedding_cache_path()
    if path.exists():
        data = np.load(path, allow_pickle=False)
        if str(data["model"]) == settings.embedding_model:
            cache = dict(zip(data["hashes"].tolist(), data["vectors"], strict=True))
    with connect(url) as conn:
        rows = conn.execute(
            text("SELECT chunk_id, text, content_hash FROM chunk WHERE embedding IS NULL ORDER BY chunk_id")
        ).all()
    todo = [r for r in rows if r.content_hash not in cache]
    for start in range(0, len(todo), batch):
        part = todo[start : start + batch]
        for r, vec in zip(part, embed_texts([r.text for r in part]), strict=True):
            cache[r.content_hash] = vec.astype(np.float16)
        log.info("db.embedded", done=start + len(part), total=len(todo))
    with connect(url) as conn:
        conn.execute(
            text("UPDATE chunk SET embedding = :e WHERE chunk_id = :id"),
            [{"e": np.asarray(cache[r.content_hash], dtype=np.float32), "id": r.chunk_id} for r in rows],
        )
    if todo:
        hashes = sorted(cache)
        np.savez_compressed(
            path, model=np.array(settings.embedding_model), hashes=np.array(hashes), vectors=np.stack([cache[h] for h in hashes])
        )
    return len(rows)
