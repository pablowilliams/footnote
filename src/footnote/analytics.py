"""The structured path: a query plan becomes validated SQL, run read-only."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from footnote import backend
from footnote.backend import PG_COVERAGE_SQL as _COVERAGE_SQL
from footnote.backend import PG_METRIC_SQL as _METRIC_SQL
from footnote.catalog import METRIC_BY_KEY


@dataclass
class Fact:
    ticker: str
    company: str
    metric_key: str
    fiscal_year: int
    period_end: date | None
    value: float
    unit: str
    source_kind: str
    xbrl_tag: str | None
    extraction_confidence: float
    found_in_document: bool | None
    derivation: dict[str, Any] | None
    evidence: dict[str, Any] | None
    accession: str | None
    form: str | None
    filed: str | None
    url: str | None
    doc_accession: str | None
    doc_form: str | None
    doc_filed: str | None
    doc_url: str | None
    inputs: list[Fact] = field(default_factory=list)

    @property
    def label(self) -> str:
        return METRIC_BY_KEY[self.metric_key].label


def expand_metrics(metric_keys: list[str]) -> list[str]:
    """Derived metrics are answered from their inputs, so fetch those too."""
    keys = list(metric_keys)
    for key in metric_keys:
        if key == "free_cash_flow":
            keys += ["operating_cash_flow", "capital_expenditure"]
        if key == "operating_margin":
            keys += ["operating_income", "revenue"]
    return list(dict.fromkeys(keys))


def fetch_facts(tickers: list[str], metric_keys: list[str], year_from: int, year_to: int) -> tuple[list[Fact], str]:
    rows, sql = backend.get().metric_rows(tickers, expand_metrics(metric_keys), year_from, year_to)
    facts = [
        Fact(
            ticker=r["ticker"],
            company=r["company"],
            metric_key=r["metric_key"],
            fiscal_year=r["fiscal_year"],
            period_end=r["period_end"],
            value=float(r["value"]),
            unit=r["unit"],
            source_kind=r["source_kind"],
            xbrl_tag=r["xbrl_tag"],
            extraction_confidence=float(r["extraction_confidence"]),
            found_in_document=r["found_in_document"],
            derivation=r["derivation"],
            evidence=r["evidence"],
            accession=r["accession"],
            form=r["form"],
            filed=r["filed"].isoformat() if r["filed"] else None,
            url=r["url"],
            doc_accession=r["doc_accession"],
            doc_form=r["doc_form"],
            doc_filed=r["doc_filed"].isoformat() if r["doc_filed"] else None,
            doc_url=r["doc_url"],
        )
        for r in rows
    ]
    index = {(f.ticker, f.metric_key, f.fiscal_year): f for f in facts}
    for f in facts:
        if f.derivation:
            f.inputs = [
                index[(f.ticker, k, f.fiscal_year)] for k in f.derivation["inputs"] if (f.ticker, k, f.fiscal_year) in index
            ]
    return facts, sql


def coverage(tickers: list[str], metric_keys: list[str]) -> dict[str, tuple[int, int]]:
    rows = backend.get().coverage(tickers, metric_keys)
    return {r["ticker"]: (r["first_year"], r["last_year"]) for r in rows}


__all__ = ["_COVERAGE_SQL", "_METRIC_SQL", "Fact", "coverage", "expand_metrics", "fetch_facts"]
