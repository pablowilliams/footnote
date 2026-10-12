"""Typed request and response models shared by the graph, the API and the evaluation."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Status(StrEnum):
    verified = "verified"
    partially_verified = "partially_verified"
    ambiguous = "ambiguous"
    insufficient_evidence = "insufficient_evidence"
    refused = "refused"


class Route(StrEnum):
    sql = "sql"
    sql_calculation = "sql_calculation"
    sql_and_document = "sql_and_document"
    comparison = "comparison"
    document = "document"
    evidence = "evidence"
    clarify = "clarify"
    refuse = "refuse"


class Citation(BaseModel):
    id: int
    kind: str  # financial_statement, document or extracted
    company: str
    ticker: str
    fiscal_year: int
    form: str | None = None
    filed: str | None = None
    accession: str | None = None
    url: str | None = None
    location: str  # "Consolidated statement of operations" or "Item 1A Risk Factors"
    page: str | None = None
    xbrl_tag: str | None = None
    snippet: str | None = None

    @property
    def label(self) -> str:
        parts = [f"{self.company}, Form {self.form or '10-K'} for fiscal {self.fiscal_year}"]
        if self.filed:
            parts[0] += f" (filed {self.filed})"
        parts.append(self.location)
        if self.page:
            parts.append(f"page {self.page}")
        return ", ".join(parts)


class CalculationInput(BaseModel):
    label: str
    value: float
    citation_id: int


class Calculation(BaseModel):
    description: str
    formula: str
    inputs: list[CalculationInput]
    result: float
    unit: str  # ratio, percentage_points, USD, USD/share, people


class Check(BaseModel):
    name: str
    passed: bool
    detail: str = ""


class Segment(BaseModel):
    """One sentence or bullet of the answer and the evidence behind it."""

    text: str
    citation_ids: list[int] = Field(default_factory=list)
    kind: str = "computed"  # computed (built from figures) or quoted (verbatim from a filing)
    bullet: bool = False
    calc_index: int | None = None  # the calculation whose direction this sentence describes


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    previous_answer_id: str | None = None


class SearchHit(BaseModel):
    chunk_id: int
    ticker: str
    company: str
    fiscal_year: int
    item: str
    section: str
    page: str | None
    score: float
    vector_rank: int | None
    bm25_rank: int | None
    similarity: float | None = None
    text: str
    accession: str
    url: str
    filed: str | None = None


class Answer(BaseModel):
    answer_id: str
    question: str
    status: Status
    route: Route
    answer: str
    segments: list[Segment] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    calculations: list[Calculation] = Field(default_factory=list)
    checks: list[Check] = Field(default_factory=list)
    table: list[dict[str, object]] = Field(default_factory=list)
    clarification_options: list[str] = Field(default_factory=list)
    reason: str | None = None
    narrator: str = "template"
    latency_ms: int = 0
    sql: str | None = None
