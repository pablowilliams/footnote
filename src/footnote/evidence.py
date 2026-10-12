"""Citations, and the selection of quotable sentences from retrieved passages."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from footnote.analytics import Fact
from footnote.catalog import SECTIONS
from footnote.embed import embed_query, embed_texts
from footnote.schemas import Citation, SearchHit

_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“(])")
_ITEM_LABEL = {item: f"Item {item}, {title}" for item, title in SECTIONS.items()}


@dataclass
class CitationBook:
    citations: list[Citation] = field(default_factory=list)
    _keys: dict[tuple[object, ...], int] = field(default_factory=dict)
    chunk_text: dict[int, str] = field(default_factory=dict)  # citation id -> full passage, for the verifier

    def _add(self, key: tuple[object, ...], make: object) -> int:
        if key in self._keys:
            return self._keys[key]
        cid = len(self.citations) + 1
        citation = make(cid)  # type: ignore[operator]
        self.citations.append(citation)
        self._keys[key] = cid
        return cid

    def fact(self, f: Fact) -> list[int]:
        """Citation ids for a figure. A derived figure is cited through its inputs."""
        if f.source_kind == "derived":
            return [i for inp in f.inputs for i in self.fact(inp)]
        ev = f.evidence or {}

        def make(cid: int) -> Citation:
            if f.source_kind == "text":
                return Citation(
                    id=cid,
                    kind="extracted",
                    company=f.company,
                    ticker=f.ticker,
                    fiscal_year=f.fiscal_year,
                    form=f.form,
                    filed=f.filed,
                    accession=f.accession,
                    url=f.url,
                    location="Item 1, Business (human capital)",
                    page=str(ev.get("page")) if ev.get("page") else None,
                    snippet=ev.get("sentence"),
                )
            if f.doc_accession:
                return Citation(
                    id=cid,
                    kind="financial_statement",
                    company=f.company,
                    ticker=f.ticker,
                    fiscal_year=int(ev.get("doc_fiscal_year", f.fiscal_year)),
                    form=f.doc_form,
                    filed=f.doc_filed,
                    accession=f.doc_accession,
                    url=f.doc_url,
                    location=str(ev.get("location", "Annual report")),
                    page=str(ev.get("page")) if ev.get("page") else None,
                    xbrl_tag=f"us-gaap:{f.xbrl_tag}" if f.xbrl_tag else None,
                )
            return Citation(
                id=cid,
                kind="financial_statement",
                company=f.company,
                ticker=f.ticker,
                fiscal_year=f.fiscal_year,
                form=f.form,
                filed=f.filed,
                accession=f.accession,
                url=f.url,
                location=f"XBRL financial data (us-gaap:{f.xbrl_tag})",
                xbrl_tag=f"us-gaap:{f.xbrl_tag}" if f.xbrl_tag else None,
            )

        # Figures printed on the same page of the same report share one citation.
        if f.source_kind == "xbrl" and f.doc_accession:
            key: tuple[object, ...] = ("page", f.doc_accession, ev.get("location"), ev.get("page"))
        else:
            key = ("fact", f.ticker, f.metric_key, f.fiscal_year)
        cid = self._add(key, make)
        if f.xbrl_tag:
            # A page-level citation can support several figures; list every tag it covers.
            citation = self.citations[cid - 1]
            tags = set(filter(None, (citation.xbrl_tag or "").split(", "))) | {f"us-gaap:{f.xbrl_tag}"}
            citation.xbrl_tag = ", ".join(sorted(tags))
        return [cid]

    def passage(self, hit: SearchHit, snippet: str) -> int:
        def make(cid: int) -> Citation:
            return Citation(
                id=cid,
                kind="document",
                company=hit.company,
                ticker=hit.ticker,
                fiscal_year=hit.fiscal_year,
                form="10-K",
                filed=hit.filed,
                accession=hit.accession,
                url=hit.url,
                location=_ITEM_LABEL.get(hit.item, f"Item {hit.item}"),
                page=hit.page,
                snippet=snippet,
            )

        cid = self._add(("chunk", hit.chunk_id), make)
        self.chunk_text[cid] = hit.text
        return cid


_NOT_QUOTABLE = re.compile(
    r"^(risks? related to|risks? relating to|\d+\s|\(\d+\)|see |refer to|table of contents)|:$|refer to \"?note"
    r"|not (presently|currently) known|currently (deem|believe)[a-z ]* (to be )?immaterial|forward-looking statements|^the following risks",
    re.I,
)


def sentences(text: str) -> list[str]:
    """Sentences fit to quote: complete, not a heading, a footnote or a cross-reference."""
    out = []
    for line in text.split("\n"):
        for s in _SENTENCE.split(line.strip()):
            s = s.strip().lstrip("\u2022\u00b7\u25aa\u25cf- ").strip()
            if not s or not (s[0].isupper() or s[0].isdigit() or s[0] in '"\u201c') or re.match(r"^\S*[\u201d\")\]]", s):
                continue  # a fragment left by a split inside a quotation or parenthesis
            words = len(s.split())
            if 8 <= words <= 70 and "|" not in s and not _NOT_QUOTABLE.search(s) and s[-1:] in '.!?"”':
                out.append(s)
    return out


@dataclass
class Quote:
    hit: SearchHit
    sentence: str
    score: float
    vector_index: int = -1


def select_quotes(
    query: str, hits: list[SearchHit], limit: int = 4, prefer_headings: bool = False, per_hit: int = 1
) -> list[Quote]:
    """The most relevant sentence from each of the best passages.

    Risk factors in a 10-K open with a one-sentence summary in bold, so for risk
    questions the first sentence of a passage gets a small preference: it is usually
    the company's own statement of the risk.
    """
    candidates: list[tuple[SearchHit, str, bool]] = []
    for hit in hits:
        for i, s in enumerate(sentences(hit.text)):
            candidates.append((hit, s, i == 0))
    if not candidates:
        return []
    q = embed_query(query)
    vectors = np.vstack(embed_texts([c[1] for c in candidates]))
    sims = vectors @ q
    terms = {t for t in re.findall(r"[a-z]{4,}", query.lower())}
    scored = []
    for i, ((hit, s, first), sim) in enumerate(zip(candidates, sims, strict=True)):
        overlap = len(terms & set(re.findall(r"[a-z]{4,}", s.lower())))
        score = float(sim) + 0.03 * overlap + (0.08 if prefer_headings and first else 0.0)
        scored.append(Quote(hit=hit, sentence=s, score=score, vector_index=i))
    scored.sort(key=lambda x: -x.score)
    chosen: list[Quote] = []
    used: dict[int, int] = {}
    chosen_vecs: list[np.ndarray] = []
    for quote in scored:
        if used.get(quote.hit.chunk_id, 0) >= per_hit:
            continue
        v = vectors[quote.vector_index]
        if any(float(v @ w) > 0.85 for w in chosen_vecs):
            continue  # near-duplicate of a sentence already chosen, often the same risk restated
        chosen.append(quote)
        chosen_vecs.append(v)
        used[quote.hit.chunk_id] = used.get(quote.hit.chunk_id, 0) + 1
        if len(chosen) >= limit:
            break
    return chosen
