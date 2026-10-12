"""The verifier, on drafts built by hand so each failure mode is isolated."""

from footnote.analytics import Fact
from footnote.answers import Draft
from footnote.schemas import Calculation, CalculationInput, Route, Segment, Status
from footnote.verify import number_tokens, run_checks, status_from


def fact(value=391_035_000_000.0, year=2024, metric="revenue", unit="USD"):
    return Fact(
        ticker="AAPL",
        company="Apple Inc.",
        metric_key=metric,
        fiscal_year=year,
        period_end=None,
        value=value,
        unit=unit,
        source_kind="xbrl",
        xbrl_tag="Revenues",
        extraction_confidence=1.0,
        found_in_document=True,
        derivation=None,
        evidence={
            "page": "29",
            "location": "Consolidated statement of operations",
            "doc_accession": "0000320193-24-000123",
            "doc_fiscal_year": year,
        },
        accession="0000320193-24-000123",
        form="10-K",
        filed="2024-11-01",
        url="https://www.sec.gov/x",
        doc_accession="0000320193-24-000123",
        doc_form="10-K",
        doc_filed="2024-11-01",
        doc_url="https://www.sec.gov/x",
    )


def draft_with(text, facts, calcs=(), allowed_years=(2024,), calc_index=None):
    d = Draft(route=Route.sql, facts=list(facts), calculations=list(calcs), allowed_years=set(allowed_years))
    ids = [i for f in facts for i in d.book.fact(f)]
    d.segments = [Segment(text=text.format(*ids) if ids else text, citation_ids=ids, calc_index=calc_index)]
    return d


def check(d, name):
    return next(c for c in run_checks(d) if c.name == name)


def test_a_correct_answer_passes_every_check():
    d = draft_with("Apple's revenue was $391.0 billion in fiscal 2024 [{}].", [fact()])
    checks = run_checks(d)
    assert all(c.passed for c in checks), checks
    assert status_from(checks) == Status.verified


def test_an_invented_figure_is_caught():
    d = draft_with("Apple's revenue was $500.0 billion in fiscal 2024 [{}].", [fact()])
    assert not check(d, "numbers_sourced").passed
    assert status_from(run_checks(d)) == Status.insufficient_evidence


def test_a_rounded_restatement_of_a_real_figure_is_still_caught():
    # "$391 billion" is not how the system writes $391,035 million, so it is not traceable.
    d = draft_with("Apple's revenue was $391 billion in fiscal 2024 [{}].", [fact()])
    assert not check(d, "numbers_sourced").passed


def test_a_figure_without_a_citation_is_caught():
    d = draft_with("Apple's revenue was $391.0 billion in fiscal 2024.", [fact()])
    d.segments[0].citation_ids = []
    assert not check(d, "citations_resolve").passed


def test_a_citation_marker_that_points_nowhere_is_caught():
    d = draft_with("Apple's revenue was $391.0 billion in fiscal 2024 [{}][9].", [fact()])
    assert not check(d, "citations_resolve").passed


def test_a_figure_from_the_wrong_year_is_caught():
    d = draft_with("Apple's revenue was $391.0 billion [{}].", [fact(year=2023)], allowed_years=(2024,))
    assert not check(d, "periods_match").passed


def test_a_calculation_that_does_not_reproduce_is_caught():
    c = Calculation(
        description="growth",
        formula="(end - start) / abs(start)",
        inputs=[CalculationInput(label="a", value=100, citation_id=1), CalculationInput(label="b", value=110, citation_id=1)],
        result=0.2,
        unit="ratio",
    )
    d = draft_with("Revenue rose [{}].", [fact()], calcs=[c])
    assert not check(d, "calculations_recomputed").passed


def test_a_description_that_contradicts_the_sign_is_caught():
    c = Calculation(
        description="growth",
        formula="(end - start) / abs(start)",
        inputs=[CalculationInput(label="a", value=100, citation_id=1), CalculationInput(label="b", value=90, citation_id=1)],
        result=-0.1,
        unit="ratio",
    )
    d = draft_with("Revenue rose by 10.0% [{}].", [fact()], calcs=[c], calc_index=0)
    assert not check(d, "direction_consistent").passed


def test_a_misquotation_is_caught():
    d = Draft(route=Route.document)
    from footnote.schemas import SearchHit

    hit = SearchHit(
        chunk_id=1,
        ticker="AAPL",
        company="Apple Inc.",
        fiscal_year=2025,
        item="1A",
        section="Risk Factors",
        page="6",
        score=1,
        vector_rank=0,
        bm25_rank=0,
        text="Tariffs can materially adversely affect the business.",
        accession="x",
        url="u",
    )
    cid = d.book.passage(hit, "")
    d.segments = [Segment(text="Tariffs will certainly ruin the business.", citation_ids=[cid], kind="quoted", bullet=True)]
    assert not check(d, "quotes_verbatim").passed
    d.segments[0].text = "Tariffs can materially adversely affect the business."
    assert check(d, "quotes_verbatim").passed


def test_a_missing_requested_figure_is_advisory_not_blocking():
    d = draft_with("Apple's revenue was $391.0 billion in fiscal 2024 [{}].", [fact()])
    d.requested = {("AAPL", "revenue", 2024), ("AAPL", "revenue", 2023)}
    assert status_from(run_checks(d)) == Status.partially_verified


def test_number_tokens_ignore_citation_markers_and_years():
    assert number_tokens("In fiscal 2024 [12], revenue was $391.0 billion, up 2.0%.") == ["$391.0 billion", "2.0%"]
