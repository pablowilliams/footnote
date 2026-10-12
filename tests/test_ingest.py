"""Parsing and chunking on a small, hand-written 10-K fragment."""

from datetime import date

from footnote.catalog import Company
from footnote.ingest.chunk import chunk_section
from footnote.ingest.headcount import extract_headcount
from footnote.ingest.parse import parse_filing
from footnote.ingest.xbrl import RawFact, fiscal_year_ends, select_metric_values

PARA = " ".join(["The Company designs and sells consumer products and related services worldwide."] * 6)
HTML = f"""
<html><body>
<div>Table of Contents</div>
<p>Item 1. Business ..... 3</p><p>Item 1A. Risk Factors ..... 5</p><p>Item 7. Management's Discussion ..... 9</p>
<div style="page-break-after:always"></div>
<p><b>Item 1. Business</b></p><p>{PARA}</p>
<p>As of September 28, 2024, the Company had approximately 164,000 full-time equivalent employees.</p>
<p>Apple Inc. | 2024 Form 10-K | 1</p>
<div style="page-break-after:always"></div>
<p><b>Item 1A. Risk Factors</b></p><p>{PARA}</p><p>{PARA}</p>
<p>Apple Inc. | 2024 Form 10-K | 2</p>
<div style="page-break-after:always"></div>
<p><b>Item 7. Management's Discussion and Analysis</b></p><p>{PARA}</p>
<p>Apple Inc. | 2024 Form 10-K | 3</p>
<div style="page-break-after:always"></div>
<p><b>Item 8. Financial Statements</b></p><p>Numbers.</p>
<p>Apple Inc. | 2024 Form 10-K | 4</p>
</body></html>"""


def test_sections_skip_the_table_of_contents():
    parsed = parse_filing(HTML)
    items = {s.item: s for s in parsed.sections}
    assert set(items) == {"1", "1A", "7"}
    assert "consumer products" in items["1A"].text  # the body, not the contents line


def test_pages_follow_the_printed_footer():
    parsed = parse_filing(HTML)
    starts = {s.item: parsed.page_at(s.start) for s in parsed.sections}
    assert starts == {"1": "1", "1A": "2", "7": "3"}


def test_chunks_never_cross_a_section_and_carry_their_page():
    parsed = parse_filing(HTML)
    risk = next(s for s in parsed.sections if s.item == "1A")
    chunks = chunk_section(parsed, risk)
    assert chunks and all(c.item == "1A" and c.page == "2" for c in chunks)
    assert len({c.content_hash for c in chunks}) == len(chunks)


def test_duplicate_paragraphs_in_one_section_are_dropped():
    parsed = parse_filing(HTML)
    risk = next(s for s in parsed.sections if s.item == "1A")
    texts = [c.text for c in chunk_section(parsed, risk)]
    assert len(texts) == len(set(texts))


def test_headcount_is_read_with_its_confidence():
    m = extract_headcount("As of September 28, 2024, the Company had approximately 164,000 full-time equivalent employees.")
    assert m and m.value == 164_000 and m.confidence >= 0.9


def test_headcount_ignores_numbers_that_are_not_employee_counts():
    assert extract_headcount("We have approximately 12 employees in our garage office.") is None


def _fact(val, start, end, accn, filed, fy, tag="Revenues"):
    return RawFact(
        "AAPL",
        tag,
        "USD",
        date.fromisoformat(start),
        date.fromisoformat(end),
        val,
        accn,
        "10-K",
        date.fromisoformat(filed),
        fy,
        "FY",
        None,
    )


def test_fiscal_year_comes_from_the_filing_where_the_period_is_primary():
    # The 2024 10-K reports fiscal 2024 and 2023, both tagged fy=2024.
    facts = [
        _fact(391.0, "2023-10-01", "2024-09-28", "A24", "2024-11-01", 2024),
        _fact(383.0, "2022-09-25", "2023-09-30", "A24", "2024-11-01", 2024),
        _fact(383.0, "2022-09-25", "2023-09-30", "A23", "2023-11-03", 2023),
        _fact(394.0, "2021-09-26", "2022-09-24", "A23", "2023-11-03", 2023),
    ]
    ends = fiscal_year_ends(facts)
    assert ends[date(2024, 9, 28)] == 2024 and ends[date(2023, 9, 30)] == 2023
    values = {
        v.fiscal_year: v.value
        for v in select_metric_values(Company("AAPL", 1, "Apple Inc.", ("apple",)), facts, 2021)
        if v.metric_key == "revenue"
    }
    assert values == {2022: 394.0, 2023: 383.0, 2024: 391.0}


def test_a_restated_value_keeps_the_original_as_lineage():
    facts = [
        _fact(100.0, "2023-01-01", "2023-12-31", "A23", "2024-02-01", 2023),
        _fact(104.0, "2023-01-01", "2023-12-31", "A24", "2025-02-01", 2024),
        _fact(110.0, "2024-01-01", "2024-12-31", "A24", "2025-02-01", 2024),
    ]
    v = next(v for v in select_metric_values(Company("X", 1, "X", ("x",)), facts, 2021) if v.fiscal_year == 2023)
    assert v.value == 104.0 and v.restated_from == 100.0 and v.accession == "A24"
