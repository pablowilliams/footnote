"""A polite, cached client for SEC EDGAR.

Every response is written to data/raw, so a second ingestion run makes no network
calls and the corpus is exactly reproducible from the cache.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from footnote.config import settings
from footnote.logs import log

_MIN_INTERVAL = 0.15  # the SEC's published ceiling is ten requests a second
_lock = threading.Lock()
_last_call = 0.0


class SecClient:
    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir or settings.raw_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._http = httpx.Client(
            headers={"User-Agent": settings.sec_user_agent, "Accept-Encoding": "gzip, deflate"},
            timeout=60,
            follow_redirects=True,
        )

    def _cache_path(self, url: str, suffix: str) -> Path:
        digest = hashlib.sha1(url.encode()).hexdigest()[:16]
        return self.cache_dir / f"{digest}{suffix}"

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError,)),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _fetch(self, url: str) -> bytes:
        global _last_call
        with _lock:
            wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
            if wait > 0:
                time.sleep(wait)
            _last_call = time.monotonic()
        response = self._http.get(url)
        response.raise_for_status()
        log.info("sec.fetch", url=url, bytes=len(response.content))
        return response.content

    def get_bytes(self, url: str, suffix: str = ".bin") -> bytes:
        path = self._cache_path(url, suffix)
        if path.exists():
            return path.read_bytes()
        body = self._fetch(url)
        path.write_bytes(body)
        return body

    def get_json(self, url: str) -> Any:
        return json.loads(self.get_bytes(url, ".json"))

    def company_facts(self, cik: int) -> Any:
        return self.get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")

    def submissions(self, cik: int) -> Any:
        return self.get_json(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")

    def filing_document(self, cik: int, accession: str, document: str) -> bytes:
        url = filing_url(cik, accession, document)
        return self.get_bytes(url, ".htm")


def filing_url(cik: int, accession: str, document: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{document}"
