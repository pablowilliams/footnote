"""Check a draft answer against its own evidence before it is released.

Checks are either blocking or advisory. A blocking failure means the answer may
state something the evidence does not support, so it is withheld. An advisory
failure means the answer is correct as far as it goes but incomplete or rests on
a weaker source, so it is released as partially verified.
"""

from __future__ import annotations

import re

from footnote import calc
from footnote.answers import MIN_PASSAGE_SIMILARITY, Draft
from footnote.schemas import Check, Segment, Status

_NUMBER = re.compile(
    r"-?\$\d[\d,]*(?:\.\d+)?(?: (?:trillion|billion|million))?"
    r"|\d[\d,]*(?:\.\d+)? percentage points"
    r"|\d[\d,]*(?:\.\d+)?%"
    r"|\b\d{1,3}(?:,\d{3})+\b"
)
_MARKER = re.compile(r"\[(\d+)\]")
_UP = re.compile(r"\b(rose|increased|grew|up|higher|an increase)\b")
_DOWN = re.compile(r"\b(fell|decreased|declined|down|lower|a decrease)\b")

BLOCKING = {
    "citations_resolve",
    "numbers_sourced",
    "quotes_verbatim",
    "calculations_recomputed",
    "units_consistent",
    "periods_match",
    "direction_consistent",
}


def number_tokens(text: str) -> list[str]:
    return [m.group(0) for m in _NUMBER.finditer(_MARKER.sub(" ", text))]


def allowed_numbers(draft: Draft) -> set[str]:
    allowed: set[str] = set()
    for f in draft.facts:
        allowed.add(calc.fmt_value(f.value, f.unit))
    for c in draft.calculations:
        allowed.add(calc.fmt_value(c.result, c.unit))
        if c.unit == "ratio":
            allowed.add(calc.fmt_change(c.result))
    return allowed


def _recompute(formula: str, values: list[float]) -> float:
    if formula == "operating_income / revenue":
        return calc.margin(values[0], values[1])
    if formula == "operating_cash_flow - capital_expenditure":
        return calc.free_cash_flow(values[0], values[1])
    if formula == "end - start":
        return values[1] - values[0]
    if formula == "(end - start) / abs(start)":
        return calc.growth(values[1], values[0])
    m = re.fullmatch(r"\(end / start\) \*\* \(1 / (\d+)\) - 1", formula)
    if m:
        return calc.cagr(values[1], values[0], int(m.group(1)))
    raise ValueError(f"unknown formula {formula}")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def run_checks(draft: Draft, text_segments: list[Segment] | None = None) -> list[Check]:
    segments = text_segments if text_segments is not None else draft.segments
    checks: list[Check] = []
    ids = {c.id for c in draft.book.citations}

    # Every marker points at a real citation, and every sentence with a figure carries one.
    bad = sorted({int(m) for s in segments for m in _MARKER.findall(s.text)} - ids)
    uncited = [s.text for s in segments if s.kind == "computed" and number_tokens(s.text) and not _MARKER.search(s.text)]
    uncited += [s.text for s in segments if s.kind == "quoted" and not s.citation_ids]
    checks.append(
        Check(
            name="citations_resolve",
            passed=not bad and not uncited,
            detail="every figure and quotation is cited"
            if not bad and not uncited
            else f"unresolved {bad}; uncited {uncited[:2]}",
        )
    )

    # Every number in a computed sentence is a source value or a calculation result, as formatted.
    allowed = allowed_numbers(draft)
    stray = [n for s in segments if s.kind == "computed" for n in number_tokens(s.text) if n not in allowed]
    n_figures = sum(len(number_tokens(s.text)) for s in segments if s.kind == "computed")
    checks.append(
        Check(
            name="numbers_sourced",
            passed=not stray,
            detail=f"{n_figures} figure{'' if n_figures == 1 else 's'} traced to source or calculation"
            if not stray
            else f"figures with no source: {', '.join(stray[:5])}",
        )
    )

    # Quotations must appear word for word in the passage they cite.
    misquoted = [
        s.text
        for s in segments
        if s.kind == "quoted"
        for cid in s.citation_ids[:1]
        if _norm(s.text) not in _norm(draft.book.chunk_text.get(cid, ""))
    ]
    if any(s.kind == "quoted" for s in segments):
        checks.append(
            Check(
                name="quotes_verbatim",
                passed=not misquoted,
                detail="quotations match the cited passages" if not misquoted else f"not found in source: {misquoted[0][:80]}",
            )
        )

    # Re-run every calculation from its cited inputs.
    if draft.calculations:
        wrong = []
        for c in draft.calculations:
            try:
                if not calc.close(_recompute(c.formula, [i.value for i in c.inputs]), c.result, rel=1e-9):
                    wrong.append(c.description)
            except (ValueError, ZeroDivisionError) as err:
                wrong.append(f"{c.description}: {err}")
        checks.append(
            Check(
                name="calculations_recomputed",
                passed=not wrong,
                detail=f"{len(draft.calculations)} calculation{'' if len(draft.calculations) == 1 else 's'} reproduced from their inputs"
                if not wrong
                else f"did not reproduce: {wrong[0]}",
            )
        )

    # Units: every figure is in US dollars (or per share, or people), and a calculation never mixes units.
    if draft.facts:
        units = {f.unit for f in draft.facts}
        mixed = [
            c.description
            for c in draft.calculations
            if c.formula in ("end - start", "(end - start) / abs(start)")
            and len({f.unit for f in draft.facts if any(calc.close(f.value, i.value) for i in c.inputs)}) > 1
        ]
        ok = units <= {"USD", "USD/share", "ratio", "people"} and not mixed
        checks.append(
            Check(
                name="units_consistent",
                passed=ok,
                detail=f"units: {', '.join(sorted(units))}" if ok else f"mixed units in {mixed or units}",
            )
        )

    # Periods: every cited figure belongs to a fiscal year the question asked about.
    if draft.allowed_years and draft.facts:
        cited_years = {f.fiscal_year for f in draft.facts}
        stray_years = sorted(cited_years - draft.allowed_years)
        checks.append(
            Check(
                name="periods_match",
                passed=not stray_years,
                detail=f"fiscal {', '.join(map(str, sorted(cited_years)))}"
                if not stray_years
                else f"unrequested years {stray_years}",
            )
        )

    # The words that describe a change agree with the sign of the computed change.
    contradictions = []
    for s in segments:
        if s.calc_index is None:
            continue
        result = draft.calculations[s.calc_index].result
        says_up, says_down = bool(_UP.search(s.text)), bool(_DOWN.search(s.text))
        if (says_up and result < 0) or (says_down and result > 0):
            contradictions.append(s.text)
    if any(s.calc_index is not None for s in segments):
        checks.append(
            Check(
                name="direction_consistent",
                passed=not contradictions,
                detail="described direction matches the calculation" if not contradictions else contradictions[0][:100],
            )
        )

    # Advisory: completeness, evidence strength and extraction confidence.
    if draft.requested:
        present = {(f.ticker, f.metric_key, f.fiscal_year) for f in draft.facts}
        missing = sorted(draft.requested - present)
        checks.append(
            Check(
                name="complete",
                passed=not missing,
                detail="all requested figures found"
                if not missing
                else "not reported: " + ", ".join(f"{t} {m} {y}" for t, m, y in missing[:4]),
            )
        )
    if draft.best_similarity is not None:
        checks.append(
            Check(
                name="evidence_strength",
                passed=draft.best_similarity >= MIN_PASSAGE_SIMILARITY,
                detail=f"best passage similarity {draft.best_similarity:.2f} (threshold {MIN_PASSAGE_SIMILARITY})",
            )
        )
    weak = [f for f in draft.facts if f.extraction_confidence < 0.8]
    if any(f.source_kind == "text" for f in draft.facts):
        checks.append(
            Check(
                name="extraction_confidence",
                passed=not weak,
                detail="figures read from text matched a high-confidence pattern"
                if not weak
                else "a figure was read from text with a low-confidence pattern",
            )
        )
    return checks


def status_from(checks: list[Check]) -> Status:
    if any(not c.passed and c.name in BLOCKING for c in checks):
        return Status.insufficient_evidence
    if any(not c.passed for c in checks):
        return Status.partially_verified
    return Status.verified
