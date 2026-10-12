"""Build a draft answer for each route. Drafts are verified before anyone sees them."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date

from footnote import calc
from footnote.analytics import Fact, coverage, fetch_facts
from footnote.catalog import BY_TICKER, METRIC_BY_KEY
from footnote.evidence import CitationBook, select_quotes
from footnote.question import ParsedQuestion
from footnote.retrieval import search
from footnote.schemas import Calculation, CalculationInput, Route, Segment, Status

# Below this cosine similarity the best passage is judged not to address the question.
MIN_PASSAGE_SIMILARITY = 0.38


@dataclass
class Draft:
    route: Route
    segments: list[Segment] = field(default_factory=list)
    book: CitationBook = field(default_factory=CitationBook)
    calculations: list[Calculation] = field(default_factory=list)
    table: list[dict[str, object]] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    requested: set[tuple[str, str, int]] = field(default_factory=set)
    allowed_years: set[int] = field(default_factory=set)
    best_similarity: float | None = None
    status: Status | None = None  # set only when the route itself decides the outcome
    reason: str | None = None
    clarification_options: list[str] = field(default_factory=list)
    sql: str | None = None


def short_name(ticker: str) -> str:
    name = BY_TICKER[ticker].name
    name = re.sub(r"^The ", "", name)
    name = re.sub(r",? (Inc\.|Corporation|Company|Platforms, Inc\.|Wholesale Corporation|Systems, Inc\.)$", "", name)
    return {
        "Amazon.com": "Amazon",
        "Meta Platforms": "Meta",
        "NVIDIA": "Nvidia",
        "NIKE": "Nike",
        "Procter & Gamble": "Procter & Gamble",
    }.get(name, name)


def possessive(ticker: str) -> str:
    n = short_name(ticker)
    return f"{n}'" if n.endswith("s") else f"{n}'s"


def join_names(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def markers(ids: list[int]) -> str:
    return "".join(f"[{i}]" for i in dict.fromkeys(ids))


# ---------------------------------------------------------------------------- years


def resolve_years(parsed: ParsedQuestion, metrics: list[str]) -> tuple[list[int], Draft | None]:
    """Which fiscal years the question needs, or a draft explaining why it cannot be answered."""
    cov = coverage(parsed.tickers, metrics or ["revenue"])
    if not cov:
        return [], Draft(
            route=Route.sql,
            status=Status.insufficient_evidence,
            reason="No reported figures were found for that company and metric.",
        )
    last_common = min(last for _, last in cov.values())
    first_common = max(first for first, _ in cov.values())
    if parsed.years:
        years = list(parsed.years)
        if parsed.change and len(years) == 1:
            years = [years[0] - 1, years[0]]
    elif parsed.change:
        span = parsed.span_years or 1
        years = [max(first_common, last_common - span), last_common]
    else:
        years = [last_common]
    if len(years) >= 2:
        years = list(range(min(years), max(years) + 1))
    future = [y for y in years if y > max(last for _, last in cov.values()) and y > date.today().year]
    if future:
        return [], Draft(
            route=Route.refuse,
            status=Status.refused,
            reason=f"Fiscal {future[0]} has not been reported. The system answers from reported results and does not forecast.",
        )
    missing = [y for y in years if y > last_common or y < first_common]
    if missing and all(y in missing for y in years):
        latest = ", ".join(f"{short_name(t)} fiscal {last}" for t, (_, last) in sorted(cov.items()))
        return [], Draft(
            route=Route.sql,
            status=Status.insufficient_evidence,
            reason=(
                f"No annual report in the corpus covers fiscal {join_names([str(y) for y in missing])}. "
                f"Coverage runs from fiscal {first_common}; the latest reports are {latest}."
            ),
        )
    return years, None


# ---------------------------------------------------------------------------- structured


def _year_end_month(f: Fact) -> str:
    return calendar.month_name[f.period_end.month] if f.period_end else "an unknown month"


def _calc_for_derived(f: Fact, book: CitationBook) -> Calculation | None:
    if f.source_kind != "derived" or len(f.inputs) != 2:
        return None
    a, b = f.inputs
    if f.metric_key == "operating_margin":
        op, rev = (a, b) if a.metric_key == "operating_income" else (b, a)
        return Calculation(
            description=f"{possessive(f.ticker)} operating margin, fiscal {f.fiscal_year}",
            formula="operating_income / revenue",
            inputs=[
                CalculationInput(label="Operating income", value=op.value, citation_id=book.fact(op)[0]),
                CalculationInput(label="Revenue", value=rev.value, citation_id=book.fact(rev)[0]),
            ],
            result=calc.margin(op.value, rev.value),
            unit="ratio",
        )
    ocf, capex = (a, b) if a.metric_key == "operating_cash_flow" else (b, a)
    return Calculation(
        description=f"{possessive(f.ticker)} free cash flow, fiscal {f.fiscal_year}",
        formula="operating_cash_flow - capital_expenditure",
        inputs=[
            CalculationInput(label="Operating cash flow", value=ocf.value, citation_id=book.fact(ocf)[0]),
            CalculationInput(label="Capital expenditure", value=capex.value, citation_id=book.fact(capex)[0]),
        ],
        result=calc.free_cash_flow(ocf.value, capex.value),
        unit="USD",
    )


def _value_phrase(f: Fact, draft: Draft) -> tuple[str, list[int]]:
    ids = draft.book.fact(f)
    derived = _calc_for_derived(f, draft.book)
    if derived and not any(c.description == derived.description for c in draft.calculations):
        draft.calculations.append(derived)
    return calc.fmt_value(f.value, f.unit), ids


def _claim_note(parsed: ParsedQuestion, facts: list[Fact]) -> Segment | None:
    """Say whether a figure stated in the question matches the filing, without repeating it."""
    if not parsed.claimed_numbers:
        return None
    claims = []
    for raw in parsed.claimed_numbers:
        m = re.match(r"\$?([\d,]*\.?\d+)\s*(%|percent|billion|million|bn|m)?", raw.strip(), re.I)
        if not m:
            continue
        n = float(m.group(1).replace(",", ""))
        scale = (m.group(2) or "").lower()
        claims.append(
            (n / 100, "ratio")
            if scale in ("%", "percent")
            else (n * {"billion": 1e9, "bn": 1e9, "million": 1e6, "m": 1e6}.get(scale, 1), "USD")
        )
    matched = any(
        abs(f.value - v) <= 0.015 * abs(f.value) for f in facts for v, unit in claims if (unit == "ratio") == (f.unit == "ratio")
    )
    text = (
        "The figure stated in the question is consistent with the reported number."
        if matched
        else "The figure stated in the question does not match the reported number."
    )
    return Segment(text=text, citation_ids=[], kind="computed")


def structured(parsed: ParsedQuestion, route: Route) -> Draft:
    metrics = parsed.metrics
    years, early = resolve_years(parsed, metrics)
    if early:
        return early
    facts, sql = fetch_facts(parsed.tickers, metrics, min(years), max(years))
    draft = Draft(route=route, facts=facts, sql=sql, allowed_years=set(years))
    draft.requested = {
        (t, m, y) for t in parsed.tickers for m in metrics for y in ([years[0], years[-1]] if parsed.change else years)
    }
    by = {(f.ticker, f.metric_key, f.fiscal_year): f for f in facts}

    if route == Route.comparison and len(parsed.years) > 1:
        # "Apple's 2024 revenue with Alphabet's 2023 revenue": ranking one year would
        # silently drop the other, so each company's figures are stated for every year asked.
        _levels(parsed, years, by, draft)
    elif route == Route.comparison:
        _compare(parsed, years, by, draft)
    elif parsed.change:
        _change(parsed, years, by, draft)
    else:
        _levels(parsed, years, by, draft)

    for f in facts:
        if f.metric_key in metrics and f.fiscal_year in years and f.ticker in parsed.tickers:
            draft.table.append(
                {
                    "company": short_name(f.ticker),
                    "metric": f.label,
                    "fiscal_year": f.fiscal_year,
                    "value": f.value,
                    "display": calc.fmt_value(f.value, f.unit),
                    "source": f.source_kind,
                }
            )
    note = _claim_note(parsed, [f for f in facts if f.metric_key in metrics])
    if note:
        draft.segments.append(note)
    if not draft.segments:
        draft.status = Status.insufficient_evidence
        draft.reason = "The filings in the corpus do not report that figure for the period asked about."
    return draft


def _levels(parsed: ParsedQuestion, years: list[int], by: dict[tuple[str, str, int], Fact], draft: Draft) -> None:
    for ticker in parsed.tickers:
        for metric in parsed.metrics:
            found = [by[(ticker, metric, y)] for y in years if (ticker, metric, y) in by]
            if not found:
                continue
            label = METRIC_BY_KEY[metric].label.lower()
            if metric == "headcount":
                f = found[-1]
                value, ids = _value_phrase(f, draft)
                draft.segments.append(
                    Segment(
                        text=f"{short_name(ticker)} reported approximately {value} employees at the end of fiscal {f.fiscal_year} {markers(ids)}.",
                        citation_ids=ids,
                    )
                )
                continue
            parts, ids_all = [], []
            for f in found:
                value, ids = _value_phrase(f, draft)
                parts.append(f"{value} in fiscal {f.fiscal_year} {markers(ids)}")
                ids_all += ids
            draft.segments.append(Segment(text=f"{possessive(ticker)} {label} was {join_names(parts)}.", citation_ids=ids_all))


def _change(parsed: ParsedQuestion, years: list[int], by: dict[tuple[str, str, int], Fact], draft: Draft) -> None:
    start_year, end_year = years[0], years[-1]
    first = True
    for ticker in parsed.tickers:
        for metric in parsed.metrics:
            a, b = by.get((ticker, metric, start_year)), by.get((ticker, metric, end_year))
            label = METRIC_BY_KEY[metric].label.lower()
            subject = f"{possessive(ticker)} {label}" if first or len(parsed.tickers) > 1 else f"Its {label}"
            if not a or not b:
                continue
            va, ida = _value_phrase(a, draft)
            vb, idb = _value_phrase(b, draft)
            if a.unit == "ratio":
                diff = b.value - a.value
                c = Calculation(
                    description=f"Change in {possessive(ticker)} {label}, fiscal {start_year} to {end_year}",
                    formula="end - start",
                    inputs=[
                        CalculationInput(label=f"{label}, fiscal {start_year}", value=a.value, citation_id=ida[0]),
                        CalculationInput(label=f"{label}, fiscal {end_year}", value=b.value, citation_id=idb[0]),
                    ],
                    result=diff,
                    unit="percentage_points",
                )
                draft.calculations.append(c)
                direction = "rose" if diff > 0 else "fell" if diff < 0 else "was unchanged"
                tail = f", {'up' if diff > 0 else 'down'} {calc.fmt_value(diff, 'percentage_points')}" if diff else ""
                text_ = f"{subject} {direction} from {va} in fiscal {start_year} {markers(ida)} to {vb} in fiscal {end_year} {markers(idb)}{tail}."
            else:
                g = calc.growth(b.value, a.value)
                c = Calculation(
                    description=f"Change in {possessive(ticker)} {label}, fiscal {start_year} to {end_year}",
                    formula="(end - start) / abs(start)",
                    inputs=[
                        CalculationInput(label=f"{label}, fiscal {start_year}", value=a.value, citation_id=ida[0]),
                        CalculationInput(label=f"{label}, fiscal {end_year}", value=b.value, citation_id=idb[0]),
                    ],
                    result=g,
                    unit="ratio",
                )
                draft.calculations.append(c)
                direction = "rose" if g > 0 else "fell" if g < 0 else "was unchanged"
                noun = "an increase" if g > 0 else "a decrease"
                tail = f", {noun} of {calc.fmt_change(g)}" if g else ""
                text_ = f"{subject} {direction} from {va} in fiscal {start_year} {markers(ida)} to {vb} in fiscal {end_year} {markers(idb)}{tail}."
                span = end_year - start_year
                if span >= 2 and a.value > 0 and b.value > 0 and a.unit == "USD":
                    r = calc.cagr(b.value, a.value, span)
                    draft.calculations.append(
                        Calculation(
                            description=f"Compound annual growth in {possessive(ticker)} {label}, fiscal {start_year} to {end_year}",
                            formula=f"(end / start) ** (1 / {span}) - 1",
                            inputs=c.inputs,
                            result=r,
                            unit="ratio",
                        )
                    )
                    text_ = text_[:-1] + f", or {calc.fmt_change(r)} a year compounded."
            draft.segments.append(Segment(text=text_, citation_ids=ida + idb, calc_index=draft.calculations.index(c)))
            first = False


def _compare(parsed: ParsedQuestion, years: list[int], by: dict[tuple[str, str, int], Fact], draft: Draft) -> None:
    year = years[-1]
    for metric in parsed.metrics:
        present = [by[(t, metric, year)] for t in parsed.tickers if (t, metric, year) in by]
        if not present:
            continue
        present.sort(key=lambda f: -f.value)
        label = METRIC_BY_KEY[metric].label.lower()
        parts = []
        ids_all: list[int] = []
        for f in present:
            value, ids = _value_phrase(f, draft)
            parts.append(f"{short_name(f.ticker)} ({value}) {markers(ids)}")
            ids_all += ids
        if len(parts) == 1:
            text_ = f"In fiscal {year}, {label} was reported only for {parts[0]}."
        else:
            text_ = f"In fiscal {year}, {label} was highest at {parts[0]}, followed by {join_names(parts[1:])}."
        draft.segments.append(Segment(text=text_, citation_ids=ids_all))
    months = {
        short_name(f.ticker): _year_end_month(f) for f in draft.facts if f.fiscal_year == year and f.ticker in parsed.tickers
    }
    if len(set(months.values())) > 1:
        groups: dict[str, list[str]] = {}
        for name, month in months.items():
            groups.setdefault(month, []).append(name)
        desc = "; ".join(f"{join_names(names)} in {month}" for month, names in groups.items())
        draft.segments.append(
            Segment(text=f"Fiscal years end in different months, so the periods are not identical: {desc}.", kind="context")
        )


# ---------------------------------------------------------------------------- documents


_RISK = re.compile(r"\brisks?\b", re.I)
_GENERIC_WORDS = {
    "which",
    "what",
    "risks",
    "risk",
    "mention",
    "mentioned",
    "disclose",
    "disclosed",
    "does",
    "report",
    "annual",
    "latest",
    "recent",
    "main",
    "major",
    "biggest",
    "list",
    "their",
    "describe",
    "described",
    "face",
    "faces",
    "facing",
}


def _strip_entities(question: str, parsed: ParsedQuestion) -> str:
    out = question
    for t in parsed.tickers:
        for alias in (*BY_TICKER[t].aliases, t):
            out = re.sub(r"(?i)\b" + re.escape(alias) + r"(?:'s|’s)?\b", " ", out)
    out = re.sub(r"\b(fiscal|fy)?\s*(19|20)\d{2}\b", " ", out, flags=re.I)
    out = re.sub(r"\b(latest|most recent|annual report|10-k)\b", " ", out, flags=re.I)
    # Question scaffolding carries no topic and dilutes the query embedding.
    out = re.sub(
        r"\b(what|which|who|how|does|did|do|is|are|was|were|say|says|said|about|describe[sd]?|discuss(es|ed)?|mention(s|ed)?|"
        r"identify|identifies|according to|in its|its|their|the company|the|a|an|of)\b",
        " ",
        out,
        flags=re.I,
    )
    out = re.sub(r"[?.!,]", " ", out)
    return re.sub(r"\s+", " ", out).strip()


def _text_years(tickers: list[str]) -> dict[str, list[int]]:
    from footnote import backend

    return backend.get().text_years(tickers)


def document(parsed: ParsedQuestion, question: str, lead: bool = True, draft: Draft | None = None) -> Draft:
    draft = draft or Draft(route=Route.document)
    available = _text_years(parsed.tickers)
    query = _strip_entities(question, parsed) or question
    risk = bool(_RISK.search(question))
    content = set(re.findall(r"[a-z]{4,}", query.lower())) - _GENERIC_WORDS
    if risk and not content:
        # "Which risks did X mention?" names no topic, so search for the risks the
        # company itself presents as material rather than for the word "risk".
        query = "material risks that could adversely affect the business, results of operations and financial condition"
    items = ["1A"] if risk else (["7"] if parsed.why else None)
    per_company = 4 if len(parsed.tickers) == 1 else 2
    best: float | None = None
    any_quote = False
    for ticker in parsed.tickers:
        years_available = available.get(ticker, [])
        if parsed.years:
            target = [y for y in parsed.years if y in years_available]
            if not target:
                draft.reason = (
                    f"The corpus holds the full text of {possessive(ticker)} annual reports for fiscal "
                    f"{join_names([str(y) for y in years_available])} only."
                )
                continue
            year = max(target)
        else:
            if not years_available:
                continue
            year = max(years_available)
        draft.allowed_years.add(year)
        hits = search(query, [ticker], [year], items, k=8)
        if not hits:
            continue
        top = max((h.similarity or 0.0) for h in hits)
        best = top if best is None else max(best, top)
        if top < MIN_PASSAGE_SIMILARITY:
            continue
        quotes = select_quotes(query, hits[:6], limit=per_company, prefer_headings=risk)
        quotes = [q for q in quotes if q.score >= MIN_PASSAGE_SIMILARITY - 0.05]
        if not quotes:
            continue
        any_quote = True
        if lead:
            where = (
                "Item 1A, Risk Factors"
                if risk
                else ("Item 7, Management's Discussion and Analysis" if parsed.why else "the annual report")
            )
            intro = (
                f"{possessive(ticker)} fiscal {year} annual report ({where}) describes these risks, among others:"
                if risk
                else f"{possessive(ticker)} fiscal {year} annual report addresses this in the following passages:"
            )
            if parsed.why:
                intro = (
                    f"In its fiscal {year} annual report ({where}), {short_name(ticker)} attributes the change to the following:"
                )
            draft.segments.append(Segment(text=intro, kind="context"))
        for q in quotes:
            cid = draft.book.passage(q.hit, q.sentence)
            draft.segments.append(Segment(text=q.sentence, citation_ids=[cid], kind="quoted", bullet=True))
    draft.best_similarity = best
    if not any_quote and draft.route == Route.document:
        draft.status = Status.insufficient_evidence
        draft.reason = draft.reason or "No passage in the filings addresses the question closely enough to quote."
    return draft
