"""Turn a question into a structured request, deterministically.

The parser decides what a question is about before any retrieval happens. It is
rule-based on purpose: the routing decision is the most consequential step in the
pipeline, and a rule that misfires can be found, tested and fixed, while a
misrouting model cannot.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from footnote.catalog import COMPANIES, METRICS

# Words that only make sense as a question about the future, or about what to do with money.
_FORECAST = re.compile(
    r"\b(will|forecast|predict|projected?|projection|expect(ed)? to|next (fiscal )?year|outlook for 20\d\d)\b", re.I
)
_ADVICE = re.compile(
    r"\b(should i|buy|sell|invest(ing)? in|price target|undervalued|overvalued|good investment|recommend)\b", re.I
)
_INJECTION = re.compile(
    r"\b(ignore (all |any |your |the |previous |prior )*(instructions|rules|guidelines)|system prompt|pretend|you are now|jailbreak|disregard)\b",
    re.I,
)
_SQL_ATTACK = re.compile(
    r"(;\s*--|\b(drop|delete|truncate|alter|insert|update|grant)\s+(table|from|into|on|database|\w+\s+set)\b)", re.I
)
_CURRENCY = re.compile(r"\b(in (euros?|pounds?|sterling|gbp|eur|yen|jpy|yuan|cny|francs?|chf))\b", re.I)
_EVIDENCE = re.compile(r"\b(show|give|list|what (is|are)) (me )?(the )?(evidence|sources?|citations?|references?)\b", re.I)
_CHANGE = re.compile(
    r"\b(change[sd]?|grow(th|n)?|grew|increase[sd]?|decrease[sd]?|decline[sd]?|fell|rose|trend|evolve[sd]?|move[sd]?|year[- ]on[- ]year|yoy|cagr|between|from \d{4} to|go(es)? (up|down)|went (up|down)|improve[sd]?|worsen(ed)?|over the (last|past))\b",
    re.I,
)
_WHY = re.compile(r"\b(why|drivers?|driven|drove|drive|explain|reasons?|what caused|caused|due to|because|contributed)\b", re.I)
_COMPARE = re.compile(r"\b(compare|comparison|versus|vs\.?|against|relative to|higher|lower|largest|smallest|rank)\b", re.I)
_LATEST = re.compile(r"\b(latest|most recent|current|last reported|this year)\b", re.I)
_YEAR = re.compile(r"\b(?:fy\s?|fiscal\s+(?:year\s+)?)?((?:19|20)\d{2})\b", re.I)
_DOCUMENT = re.compile(
    r"\b(risks?|mention(ed|s)?|disclose[sd]?|say(s)?|said|describe[sd]?|discuss(es|ed)?|strategy|competition|competitors?|regulat\w*|supply chain|litigation|segments?|products?|customers?|business|annual report|10-k|outlook|guidance|cybersecurity|ai|artificial intelligence|tariffs?|employees? (?!number))\b",
    re.I,
)
_AMBIGUOUS_METRIC = {
    "profit": ["operating income", "net income"],
    "profits": ["operating income", "net income"],
    "earnings": ["net income", "diluted earnings per share"],
    "income": ["operating income", "net income"],
    "cash flow": ["operating cash flow", "free cash flow"],
}
# Capitalised words that look like a company name but are not one we cover.
_NOT_NAMES = {
    "what",
    "how",
    "which",
    "why",
    "show",
    "compare",
    "did",
    "does",
    "was",
    "were",
    "is",
    "are",
    "the",
    "in",
    "for",
    "and",
    "of",
    "fiscal",
    "fy",
    "give",
    "list",
    "tell",
    "eps",
    "fcf",
    "capex",
    "ebit",
    "item",
    "form",
    "sec",
    "usd",
    "between",
    "from",
    "to",
    "across",
    "over",
    "its",
    "their",
    "a",
    "an",
    "has",
    "have",
    "who",
    "when",
    "where",
    "please",
    "can",
    "could",
    "would",
    "should",
    "i",
    "me",
    "my",
    "ai",
    "us",
    "u.s.",
    "ceo",
    "cfo",
    "gaap",
}


@dataclass
class ParsedQuestion:
    raw: str
    tickers: list[str] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)
    years: list[int] = field(default_factory=list)
    latest: bool = False
    change: bool = False
    why: bool = False
    compare: bool = False
    document: bool = False
    evidence_request: bool = False
    refusal_reason: str | None = None
    ambiguity: str | None = None
    clarification_options: list[str] = field(default_factory=list)
    unknown_names: list[str] = field(default_factory=list)
    claimed_numbers: list[str] = field(default_factory=list)
    span_years: int | None = None  # "over the last five years"


def _find_companies(text: str) -> list[str]:
    low = text.lower()
    found: list[tuple[int, str]] = []
    for c in COMPANIES:
        positions = [m.start() for alias in c.aliases for m in re.finditer(r"(?<![\w&])" + re.escape(alias) + r"(?![\w&])", low)]
        # Tickers count only in capitals, so "cat" and "ko" in ordinary prose are ignored.
        positions += [m.start() for m in re.finditer(r"\b" + re.escape(c.ticker) + r"\b", text)]
        if positions:
            found.append((min(positions), c.ticker))
    found.sort()
    return [t for _, t in found]


def _find_metrics(text: str) -> list[str]:
    low = text.lower()
    hits: list[tuple[int, str]] = []
    taken: list[tuple[int, int]] = []
    # Longest synonyms first, so "operating cash flow" is not also read as "cash flow".
    pairs = sorted(((syn, m.key) for m in METRICS for syn in m.synonyms), key=lambda p: -len(p[0]))
    for syn, key in pairs:
        for m in re.finditer(r"(?<![\w-])" + re.escape(syn) + r"(?![\w-])", low):
            if any(a <= m.start() < b or a < m.end() <= b for a, b in taken):
                continue
            taken.append((m.start(), m.end()))
            hits.append((m.start(), key))
    hits.sort()
    return list(dict.fromkeys(k for _, k in hits))


def parse(question: str) -> ParsedQuestion:
    q = ParsedQuestion(raw=question)
    text = question.strip()

    if _INJECTION.search(text):
        q.refusal_reason = "The question contains instructions addressed to the system rather than a question about the filings."
        return q
    if _SQL_ATTACK.search(text):
        q.refusal_reason = "The question contains a database command. Only read-only questions about the filings are answered."
        return q

    q.tickers = _find_companies(text)
    q.metrics = _find_metrics(text)
    q.years = sorted({int(y) for y in _YEAR.findall(text)})
    span = re.search(r"\b(?:last|past)\s+(two|three|four|five|six|\d)\s+(?:fiscal\s+)?years\b", text, re.I)
    if span:
        word = span.group(1).lower()
        q.span_years = int(word) if word.isdigit() else ["two", "three", "four", "five", "six"].index(word) + 2
    q.latest = bool(_LATEST.search(text)) or not q.years
    q.change = bool(_CHANGE.search(text)) or len(q.years) >= 2
    q.why = bool(_WHY.search(text))
    q.compare = len(q.tickers) >= 2 or (bool(_COMPARE.search(text)) and len(q.tickers) != 1)
    q.document = bool(_DOCUMENT.search(text))
    q.evidence_request = bool(_EVIDENCE.search(text)) and not q.metrics and not q.tickers
    without_years = _YEAR.sub(" ", text)
    q.claimed_numbers = re.findall(r"\$?\d[\d,]*(?:\.\d+)?\s*(?:%|percent\b|billion\b|million\b|bn\b|m\b)", without_years, re.I)

    if _ADVICE.search(text):
        q.refusal_reason = "Investment recommendations are outside the scope of this system. It reports what companies filed."
        return q
    if _FORECAST.search(text):
        q.refusal_reason = "The system answers from reported results only and does not forecast future figures."
        return q
    if _CURRENCY.search(text):
        q.refusal_reason = "All figures are reported in US dollars. The system does not convert currencies, because it has no sourced exchange rate to cite."
        return q

    # A capitalised name that is not one of the fifteen companies.
    for word in re.findall(r"\b([A-Z][a-zA-Z&\.]{2,})\b", text):
        lw = word.lower().rstrip(".")
        known = any(
            lw == c.ticker.lower() or lw in re.split(r"[\s\-&.,]+", " ".join((*c.aliases, c.name.lower()))) for c in COMPANIES
        )
        if not known and lw not in _NOT_NAMES and not any(lw in s for m in METRICS for s in m.synonyms):
            q.unknown_names.append(word)

    if not q.metrics:
        low = text.lower()
        for word, options in _AMBIGUOUS_METRIC.items():
            if re.search(r"(?<![\w-])" + re.escape(word) + r"(?![\w-])", low):
                q.ambiguity = f'"{word}" could mean more than one reported figure.'
                q.clarification_options = options
                break
    return q
