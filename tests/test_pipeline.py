"""End to end over the loaded snapshot: answers, refusals, evidence follow-ups and the API."""

import pytest
from fastapi.testclient import TestClient

from footnote.graph import ask
from footnote.schemas import Route, Status


@pytest.fixture(scope="module")
def client(db):
    from footnote.api import app

    with TestClient(app) as c:
        yield c


def test_a_reported_figure_is_answered_and_cited_to_its_statement_page(db):
    a = ask("What was Apple's revenue in fiscal 2024?", persist=False)
    assert a.status == Status.verified
    assert "$391.0 billion" in a.answer
    c = a.citations[0]
    assert c.location == "Consolidated statement of operations" and c.page and c.url.startswith("https://www.sec.gov/")


def test_a_change_is_computed_in_code_and_recomputed_by_the_verifier(db):
    a = ask("How did Apple's revenue change between fiscal 2023 and 2024?", persist=False)
    assert a.route == Route.sql_calculation and "2.0%" in a.answer
    assert any(c.name == "calculations_recomputed" and c.passed for c in a.checks)


def test_free_cash_flow_is_derived_and_cites_its_inputs(db):
    a = ask("What was Apple's free cash flow in fiscal 2024?", persist=False)
    assert "$108.8 billion" in a.answer
    assert a.calculations[0].formula == "operating_cash_flow - capital_expenditure"


def test_quotations_are_verbatim(db):
    a = ask("What does Apple say about tariffs?", persist=False)
    assert a.status in (Status.verified, Status.partially_verified)
    assert any(c.name == "quotes_verbatim" and c.passed for c in a.checks)


@pytest.mark.parametrize(
    ("question", "status"),
    [
        ("What was Tesla's revenue in 2024?", Status.refused),
        ("What was Apple's revenue in 2015?", Status.insufficient_evidence),
        ("What was Apple's profit in 2024?", Status.ambiguous),
        ("What will Nvidia's revenue be in 2028?", Status.refused),
    ],
)
def test_unsupported_questions_are_declined(db, question, status):
    assert ask(question, persist=False).status == status


def test_evidence_follow_up_returns_the_earlier_sources(db):
    first = ask("What was Microsoft's revenue in fiscal 2024?")
    follow = ask("Show the evidence for that answer", previous_answer_id=first.answer_id)
    assert follow.route == Route.evidence
    assert [c.id for c in follow.citations] == [c.id for c in first.citations]


def test_api_health_ask_and_search(client):
    assert client.get("/health").json()["status"] == "ok"
    r = client.post("/ask", json={"question": "What was Adobe's net income in fiscal 2024?"})
    assert r.status_code == 200 and r.json()["status"] == "verified"
    hits = client.get("/search", params={"q": "export controls", "ticker": "NVDA", "k": 3}).json()
    assert hits and all(h["ticker"] == "NVDA" for h in hits)


def test_api_rejects_an_empty_question(client):
    assert client.post("/ask", json={"question": ""}).status_code == 422


def test_rate_limit(client):
    from footnote import api

    limiter = api.RateLimiter(2)
    assert limiter.allow("a") and limiter.allow("a") and not limiter.allow("a") and limiter.allow("b")
