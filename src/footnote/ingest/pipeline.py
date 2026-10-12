"""SEC EDGAR to a versioned snapshot on disk.

The snapshot is the contract between ingestion and everything else. It is plain
JSON Lines, committed to the repository, so the database, the evaluation and CI
can all be rebuilt without touching the SEC again.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any

from footnote.catalog import COMPANIES, Company
from footnote.config import settings
from footnote.ingest.chunk import chunk_section
from footnote.ingest.headcount import extract_headcount
from footnote.ingest.parse import ParsedFiling, parse_filing
from footnote.ingest.xbrl import MetricValue, fiscal_year_ends, fiscal_year_for, raw_facts, select_metric_values
from footnote.logs import log
from footnote.sec import SecClient, filing_url
from footnote.snapshot_io import write_jsonl


def _annual_filings(company: Company, submissions: dict[str, Any], client: SecClient) -> list[dict[str, Any]]:
    # "recent" holds the last thousand filings. A company that files many insider
    # forms (Meta, for one) pushes older 10-Ks into the paged archive files.
    pages = [submissions["filings"]["recent"]]
    for extra in submissions["filings"].get("files", []):
        if extra.get("filingTo", "") >= f"{settings.first_fiscal_year}-01-01":
            pages.append(client.get_json(f"https://data.sec.gov/submissions/{extra['name']}"))
    rows = []
    for recent in pages:
        rows.extend(_annual_rows(company, recent))
    return rows


def _annual_rows(company: Company, recent: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for i, form in enumerate(recent["form"]):
        if form != "10-K":
            continue
        rows.append(
            {
                "ticker": company.ticker,
                "accession": recent["accessionNumber"][i],
                "form": form,
                "filed": recent["filingDate"][i],
                "period_end": recent["reportDate"][i],
                "primary_document": recent["primaryDocument"][i],
                "url": filing_url(company.cik, recent["accessionNumber"][i], recent["primaryDocument"][i]),
            }
        )
    return rows


def _number_forms(value: float, unit: str) -> list[str]:
    v = abs(value)
    if unit == "USD/share":
        return [f"{v:.2f}"]
    forms = []
    for scale in (1_000_000, 1_000):
        scaled = v / scale
        if scaled >= 1:
            forms.append(f"{round(scaled):,}")
    return forms


# Statement headings, matched only as whole short lines so that a sentence in the
# notes that mentions "the consolidated statements of operations" is not taken for one.
_STATEMENTS = {
    "income": (
        re.compile(
            r"(?im)^\s*(consolidated\s+)?(statements?\s+of\s+(consolidated\s+)?(operations|income|earnings)|income\s+statements?|results\s+of\s+operations\s+for\s+the)\b.{0,40}$"
        ),
        "Consolidated statement of operations",
    ),
    "cash": (
        re.compile(
            r"(?im)^\s*(consolidated\s+)?(statements?\s+of\s+(consolidated\s+)?cash\s+flows?|cash\s+flows?\s+statements?)\b.{0,40}$"
        ),
        "Consolidated statement of cash flows",
    ),
    "balance": (
        re.compile(
            r"(?im)^\s*(consolidated\s+)?(balance\s+sheets?|statements?\s+of\s+(consolidated\s+)?financial\s+position)\b.{0,40}$"
        ),
        "Consolidated balance sheet",
    ),
}
_STATEMENT_FOR = {
    "revenue": "income",
    "operating_income": "income",
    "net_income": "income",
    "eps_diluted": "income",
    "operating_cash_flow": "cash",
    "capital_expenditure": "cash",
    "long_term_debt": "balance",
}


def locate_value(v: MetricValue, candidates: list[tuple[dict[str, Any], ParsedFiling]]) -> dict[str, Any] | None:
    """Find the printed figure in a filing, preferring its primary financial statement."""
    pattern_label = _STATEMENTS[_STATEMENT_FOR[v.metric_key]]
    forms = _number_forms(v.value, v.unit)
    fallback: dict[str, Any] | None = None
    for filing, parsed in candidates:
        text = parsed.text
        for heading in pattern_label[0].finditer(text):
            window = text[heading.start() : heading.start() + 12000]
            for form in forms:
                hit = re.search(r"(?<![\d.,])" + re.escape(form) + r"(?![\d,]|\.\d)", window)
                if hit:
                    return {
                        "doc_accession": filing["accession"],
                        "doc_fiscal_year": filing["fiscal_year"],
                        "page": parsed.page_at(heading.start() + hit.start()),
                        "location": pattern_label[1],
                    }
        if fallback is None:
            for form in forms:
                hit = re.search(r"(?<![\d.,])" + re.escape(form) + r"(?![\d,]|\.\d)", text)
                if hit:
                    fallback = {
                        "doc_accession": filing["accession"],
                        "doc_fiscal_year": filing["fiscal_year"],
                        "page": parsed.page_at(hit.start()),
                        "location": "Annual report text",
                    }
                    break
    return fallback


def ingest(client: SecClient | None = None, out_dir: Path | None = None) -> dict[str, Any]:
    client = client or SecClient()
    out_dir = out_dir or settings.snapshot_dir
    companies: list[dict[str, Any]] = []
    filings: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    chunks: list[dict[str, Any]] = []
    facts_out: list[dict[str, Any]] = []
    values_out: list[dict[str, Any]] = []
    quality: dict[str, Any] = {"generated": date.today().isoformat(), "companies": {}}

    for company in COMPANIES:
        facts_json = client.company_facts(company.cik)
        facts = raw_facts(company, facts_json)
        ends = fiscal_year_ends(facts)
        values: list[MetricValue] = select_metric_values(company, facts, settings.first_fiscal_year)
        companies.append({"ticker": company.ticker, "cik": company.cik, "name": company.name})
        facts_out.extend(asdict(f) for f in facts)

        submissions = client.submissions(company.cik)
        annual = []
        for f in _annual_filings(company, submissions, client):
            fy = fiscal_year_for(date.fromisoformat(f["period_end"]), ends)
            if fy >= settings.first_fiscal_year:
                annual.append({**f, "fiscal_year": fy})
        annual.sort(key=lambda f: f["period_end"], reverse=True)
        text_filings = annual[: settings.text_filings_per_company]

        docs: dict[int, tuple[dict[str, Any], ParsedFiling]] = {}
        for f in annual:
            f["has_text"] = f in text_filings
            filings.append(f)
        for f in text_filings:
            html = client.filing_document(company.cik, f["accession"], f["primary_document"])
            parsed = parse_filing(html)
            docs[f["fiscal_year"]] = (f, parsed)
            f["page_count"] = parsed.text.count("\x0c") + 1
            found = {s.item for s in parsed.sections}
            for s in parsed.sections:
                section_chunks = chunk_section(parsed, s)
                sections.append(
                    {
                        "accession": f["accession"],
                        "item": s.item,
                        "title": s.title,
                        "start_page": parsed.page_at(s.start),
                        "end_page": parsed.page_at(s.start + len(s.text) - 1),
                        "word_count": len(s.text.split()),
                        "chunk_count": len(section_chunks),
                    }
                )
                for c in section_chunks:
                    chunks.append(
                        {
                            "ticker": company.ticker,
                            "accession": f["accession"],
                            "fiscal_year": f["fiscal_year"],
                            "item": c.item,
                            "ordinal": c.ordinal,
                            "page": c.page,
                            "text": c.text,
                            "content_hash": c.content_hash,
                        }
                    )
                if s.item == "1":
                    match = extract_headcount(s.text)
                    if match:
                        values.append(
                            MetricValue(
                                ticker=company.ticker,
                                metric_key="headcount",
                                fiscal_year=f["fiscal_year"],
                                period_start=None,
                                period_end=date.fromisoformat(f["period_end"]),
                                value=float(match.value),
                                unit="people",
                                source_kind="text",
                                accession=f["accession"],
                                xbrl_tag=None,
                                filed=date.fromisoformat(f["filed"]),
                                extraction_confidence=match.confidence,
                                extra={"sentence": match.sentence, "page": parsed.page_at(s.start + match.offset)},
                            )
                        )
            missing = sorted(set(["1", "1A", "7"]) - found)
            if missing:
                log.warning("ingest.sections_missing", ticker=company.ticker, accession=f["accession"], missing=missing)

        # Lineage check: does each XBRL figure also appear, as printed, in a parsed report?
        # The page where it appears becomes the page a citation points to.
        checked = matched = 0
        for v in values:
            if v.source_kind != "xbrl":
                continue
            candidates = [docs[y] for y in (v.fiscal_year, v.fiscal_year + 1, v.fiscal_year + 2) if y in docs]
            if not candidates:
                v.extra["found_in_document"] = None
                continue
            checked += 1
            location = locate_value(v, candidates)
            v.extra["found_in_document"] = location is not None
            if location:
                v.extra.update(location)
                matched += 1
        values_out.extend(v.as_row() for v in values)
        quality["companies"][company.ticker] = {
            "metric_values": len(values),
            "text_filings": [f["fiscal_year"] for f in text_filings],
            "xbrl_values_checked_against_text": checked,
            "xbrl_values_found_in_text": matched,
        }
        log.info("ingest.company", ticker=company.ticker, values=len(values), filings=len(text_filings))

    write_jsonl(out_dir / "companies.jsonl", companies)
    write_jsonl(out_dir / "filings.jsonl", filings)
    write_jsonl(out_dir / "sections.jsonl", sections)
    write_jsonl(out_dir / "chunks.jsonl.gz", chunks)
    write_jsonl(out_dir / "xbrl_facts.jsonl.gz", facts_out)
    write_jsonl(out_dir / "metric_values.jsonl", values_out)
    quality["totals"] = {
        "companies": len(companies),
        "filings": len(filings),
        "filings_with_text": sum(1 for f in filings if f.get("has_text")),
        "sections": len(sections),
        "chunks": len(chunks),
        "xbrl_facts": len(facts_out),
        "metric_values": len(values_out),
    }
    (out_dir / "quality.json").write_text(json.dumps(quality, indent=2))
    return quality
