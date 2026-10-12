"""Turn SEC company-facts JSON into annual metric values with full lineage.

Two traps shape this module. First, the `fy` field on a fact is the fiscal year of
the filing that reported it, not of the period it describes: a 10-K for 2024 also
reports 2023 and 2022, all tagged fy=2024. The period's own fiscal year is
therefore taken from the filing in which that period is the primary one. Second,
the same period is reported again in later filings and is sometimes restated; the
most recently filed value wins, and every earlier value is kept as raw lineage.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from footnote.catalog import METRICS, Company

ANNUAL_FORMS = {"10-K", "10-K/A"}


@dataclass
class RawFact:
    ticker: str
    tag: str
    unit: str
    period_start: date | None
    period_end: date
    value: float
    accession: str
    form: str
    filed: date
    fy: int | None
    fp: str | None
    frame: str | None


@dataclass
class MetricValue:
    ticker: str
    metric_key: str
    fiscal_year: int
    period_start: date | None
    period_end: date
    value: float
    unit: str
    source_kind: str  # xbrl, derived or text
    accession: str | None
    xbrl_tag: str | None
    filed: date | None
    extraction_confidence: float = 1.0
    derivation: dict[str, Any] | None = None
    restated_from: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def as_row(self) -> dict[str, Any]:
        return asdict(self)


def _d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def raw_facts(company: Company, facts: dict[str, Any]) -> list[RawFact]:
    """All annual-form facts for the tags the catalogue uses."""
    wanted = {tag for m in METRICS for tag in m.tags}
    out: list[RawFact] = []
    for tag, body in facts.get("facts", {}).get("us-gaap", {}).items():
        if tag not in wanted:
            continue
        for unit, rows in body.get("units", {}).items():
            for r in rows:
                if r.get("form") not in ANNUAL_FORMS:
                    continue
                out.append(
                    RawFact(
                        ticker=company.ticker,
                        tag=tag,
                        unit=unit,
                        period_start=_d(r.get("start")),
                        period_end=date.fromisoformat(r["end"]),
                        value=float(r["val"]),
                        accession=r["accn"],
                        form=r["form"],
                        filed=date.fromisoformat(r["filed"]),
                        fy=r.get("fy"),
                        fp=r.get("fp"),
                        frame=r.get("frame"),
                    )
                )
    return out


def fiscal_year_ends(facts: list[RawFact]) -> dict[date, int]:
    """Map each fiscal year-end date to the fiscal year the company itself gives it.

    Within one 10-K, the latest period end among its annual duration facts is that
    filing's own year, and its `fy` field names it.
    """
    by_accession: dict[str, list[RawFact]] = {}
    for f in facts:
        if f.period_start and 350 <= (f.period_end - f.period_start).days <= 380 and f.form == "10-K":
            by_accession.setdefault(f.accession, []).append(f)
    ends: dict[date, int] = {}
    for rows in by_accession.values():
        primary_end = max(r.period_end for r in rows)
        fy = next((r.fy for r in rows if r.period_end == primary_end and r.fy), None)
        if fy:
            ends[primary_end] = fy
    return ends


def fiscal_year_for(end: date, ends: dict[date, int]) -> int:
    if end in ends:
        return ends[end]
    # A period end within a week of a known year end belongs to that fiscal year
    # (52/53-week calendars drift by a few days).
    for known, fy in ends.items():
        if abs((known - end).days) <= 7:
            return fy
    return end.year


def select_metric_values(company: Company, facts: list[RawFact], first_year: int) -> list[MetricValue]:
    ends = fiscal_year_ends(facts)
    out: list[MetricValue] = []
    for metric in METRICS:
        if metric.source != "xbrl":
            continue
        chosen: dict[int, MetricValue] = {}
        for priority, tag in enumerate(metric.tags):
            candidates = [f for f in facts if f.tag == tag]
            by_year: dict[int, list[RawFact]] = {}
            for f in candidates:
                if metric.kind == "duration":
                    if not f.period_start or not 350 <= (f.period_end - f.period_start).days <= 380:
                        continue
                elif f.period_start is not None or not any(abs((f.period_end - e).days) <= 7 for e in ends):
                    continue
                fy = fiscal_year_for(f.period_end, ends)
                if fy >= first_year:
                    by_year.setdefault(fy, []).append(f)
            for fy, rows in by_year.items():
                if fy in chosen:
                    continue  # a higher-priority tag already supplied this year
                rows.sort(key=lambda r: (r.filed, r.accession))
                latest = rows[-1]
                original = rows[0].value
                chosen[fy] = MetricValue(
                    ticker=company.ticker,
                    metric_key=metric.key,
                    fiscal_year=fy,
                    period_start=latest.period_start,
                    period_end=latest.period_end,
                    value=latest.value,
                    unit=metric.unit,
                    source_kind="xbrl",
                    accession=latest.accession,
                    xbrl_tag=latest.tag,
                    filed=latest.filed,
                    restated_from=original if abs(original - latest.value) > 0.5 else None,
                    extra={"tag_priority": priority, "times_reported": len(rows)},
                )
        out.extend(chosen.values())
    out.extend(derive(out))
    return out


def derive(values: list[MetricValue]) -> list[MetricValue]:
    """Free cash flow and operating margin, computed rather than read.

    Neither is a GAAP line item, so neither is taken from a filing. Each derived
    value carries the formula and the exact inputs it was computed from.
    """
    idx = {(v.ticker, v.metric_key, v.fiscal_year): v for v in values}
    out: list[MetricValue] = []
    for (ticker, key, fy), ocf in list(idx.items()):
        if key != "operating_cash_flow":
            continue
        capex = idx.get((ticker, "capital_expenditure", fy))
        if capex:
            out.append(
                MetricValue(
                    ticker=ticker,
                    metric_key="free_cash_flow",
                    fiscal_year=fy,
                    period_start=ocf.period_start,
                    period_end=ocf.period_end,
                    value=ocf.value - capex.value,
                    unit="USD",
                    source_kind="derived",
                    accession=None,
                    xbrl_tag=None,
                    filed=None,
                    derivation={
                        "formula": "operating_cash_flow - capital_expenditure",
                        "inputs": ["operating_cash_flow", "capital_expenditure"],
                    },
                )
            )
    for (ticker, key, fy), rev in list(idx.items()):
        if key != "revenue" or not rev.value:
            continue
        op = idx.get((ticker, "operating_income", fy))
        if op:
            out.append(
                MetricValue(
                    ticker=ticker,
                    metric_key="operating_margin",
                    fiscal_year=fy,
                    period_start=rev.period_start,
                    period_end=rev.period_end,
                    value=op.value / rev.value,
                    unit="ratio",
                    source_kind="derived",
                    accession=None,
                    xbrl_tag=None,
                    filed=None,
                    derivation={"formula": "operating_income / revenue", "inputs": ["operating_income", "revenue"]},
                )
            )
    return out
