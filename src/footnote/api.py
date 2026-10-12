"""HTTP API and the bundled web interface."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from sqlalchemy import text

from footnote import __version__
from footnote.catalog import COMPANIES, METRICS, SECTIONS
from footnote.config import settings
from footnote.db import connect
from footnote.graph import ask
from footnote.logs import log
from footnote.retrieval import search
from footnote.schemas import Answer, AskRequest, SearchHit

WEB = Path(__file__).parent / "web"

QUESTIONS = Counter("footnote_questions_total", "Questions answered", ["route", "status"])
LATENCY = Histogram(
    "footnote_answer_seconds", "Time to answer a question", ["route"], buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10)
)
LIMITED = Counter("footnote_rate_limited_total", "Requests refused by the rate limiter")


class RateLimiter:
    """A sliding one-minute window per client. There is no sign-in, so this is the only bound on use."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self.hits: dict[str, deque[float]] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, client: str) -> bool:
        now = time.monotonic()
        with self.lock:
            window = self.hits[client]
            while window and now - window[0] > 60:
                window.popleft()
            if len(window) >= self.per_minute:
                return False
            window.append(now)
            return True


class AnswerCache:
    """Identical questions within ten minutes get the same answer without re-running the graph."""

    def __init__(self, size: int = 512, ttl: float = 600) -> None:
        self.size, self.ttl = size, ttl
        self.items: OrderedDict[str, tuple[float, Answer]] = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key: str) -> Answer | None:
        with self.lock:
            hit = self.items.get(key)
            if not hit or time.monotonic() - hit[0] > self.ttl:
                return None
            self.items.move_to_end(key)
            return hit[1]

    def put(self, key: str, answer: Answer) -> None:
        with self.lock:
            self.items[key] = (time.monotonic(), answer)
            while len(self.items) > self.size:
                self.items.popitem(last=False)


limiter = RateLimiter(settings.rate_limit_per_minute)
cache = AnswerCache()


@asynccontextmanager
async def lifespan(_: FastAPI):  # type: ignore[no-untyped-def]
    # Load the embedding model and build the BM25 index before the first request.
    try:
        search("warm up", k=1)
        log.info("api.ready")
    except Exception as err:  # the API still serves /health so the problem is visible
        log.error("api.warmup_failed", error=str(err))
    yield


app = FastAPI(
    title="Footnote",
    version=__version__,
    description="Answers questions about fifteen companies' SEC annual reports, with citations, "
    "recomputed figures and explicit refusals when the evidence is insufficient.",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=WEB), name="static")


def _client(request: Request) -> str:
    forwarded = request.headers.get("fly-client-ip") or request.headers.get("x-forwarded-for") or ""
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


@app.get("/health", tags=["operations"])
def health() -> dict[str, Any]:
    """Liveness and data readiness: the database answers and the corpus is loaded."""
    try:
        with connect() as conn:
            chunks = conn.execute(text("SELECT count(*) FROM chunk WHERE embedding IS NOT NULL")).scalar_one()
            values = conn.execute(text("SELECT count(*) FROM metric_value")).scalar_one()
    except Exception as err:
        raise HTTPException(status_code=503, detail=f"database unavailable: {err.__class__.__name__}") from err
    if not chunks or not values:
        raise HTTPException(status_code=503, detail="corpus not loaded")
    return {"status": "ok", "version": __version__, "chunks": chunks, "metric_values": values}


@app.get("/metrics", include_in_schema=False)
def metrics() -> PlainTextResponse:
    return PlainTextResponse(generate_latest().decode(), media_type=CONTENT_TYPE_LATEST)


@app.post("/ask", response_model=Answer, tags=["questions"])
def ask_endpoint(body: AskRequest, request: Request) -> Answer:
    """Answer a question. Pass `previous_answer_id` to ask for the evidence behind an earlier answer."""
    if not limiter.allow(_client(request)):
        LIMITED.inc()
        raise HTTPException(status_code=429, detail=f"Limit of {limiter.per_minute} questions a minute reached.")
    key = body.question.strip().lower()
    cached = None if body.previous_answer_id else cache.get(key)
    if cached:
        return cached
    started = time.perf_counter()
    answer = ask(body.question, body.previous_answer_id)
    LATENCY.labels(answer.route.value).observe(time.perf_counter() - started)
    QUESTIONS.labels(answer.route.value, answer.status.value).inc()
    if not body.previous_answer_id:
        cache.put(key, answer)
    return answer


@app.get("/search", response_model=list[SearchHit], tags=["retrieval"])
def search_endpoint(
    q: str = Query(min_length=2, max_length=300),
    ticker: list[str] | None = Query(default=None),
    year: list[int] | None = Query(default=None),
    item: list[str] | None = Query(default=None),
    k: int = Query(default=8, ge=1, le=25),
) -> list[SearchHit]:
    """Hybrid search over the annual-report passages, with optional company, year and section filters."""
    return search(q, ticker, year, item, k=k)


@app.get("/companies", tags=["corpus"])
def companies() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = (
            conn.execute(
                text(
                    """SELECT c.ticker, c.name,
                          MIN(mv.fiscal_year) AS first_year, MAX(mv.fiscal_year) AS last_year,
                          ARRAY(SELECT f.fiscal_year FROM filing f WHERE f.company_id = c.company_id AND f.has_text
                                ORDER BY f.fiscal_year) AS text_years
                   FROM company c JOIN metric_value mv USING (company_id)
                   GROUP BY c.company_id ORDER BY c.ticker"""
                )
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]


@app.get("/scope", tags=["corpus"])
def scope() -> dict[str, Any]:
    """What the system will and will not answer about."""
    return {
        "companies": [c.ticker for c in COMPANIES],
        "metrics": [{"key": m.key, "label": m.label, "unit": m.unit, "source": m.source} for m in METRICS],
        "sections": SECTIONS,
        "statuses": ["verified", "partially_verified", "ambiguous", "insufficient_evidence", "refused"],
    }


@app.get("/history", tags=["questions"])
def history(limit: int = Query(default=20, ge=1, le=100)) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = (
            conn.execute(
                text(
                    "SELECT answer_id, created_at, question, route, status, latency_ms FROM answer_log ORDER BY created_at DESC LIMIT :n"
                ),
                {"n": limit},
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]
