"""Read the employee count out of Item 1, with a confidence that says how it was found.

Headcount is rarely tagged in XBRL, so it is extracted from the human-capital
paragraph. The confidence is not a probability; it records which pattern matched,
so a reviewer can see at a glance which values deserve a second look.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_NUMBER = r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(thousand|million)?"
_PATTERNS = [
    # "As of September 28, 2024, the Company had approximately 164,000 full-time equivalent employees."
    (
        re.compile(
            r"as of [^.]{3,40}?,?\s+(?:we|the company|it|[A-Z][\w&.]+)[^.]{0,40}?(?:had|employed)\s+(?:approximately|about|over|more than)?\s*"
            + _NUMBER
            + r"\s+(?:full[- ]time(?: and part[- ]time)?(?: equivalent)?\s+)?(?:employees|people|team members|associates)",
            re.I,
        ),
        0.95,
    ),
    (
        re.compile(
            r"(?:we|the company|it)\s+(?:had|employed|employ|have)\s+(?:approximately|about|over|more than)?\s*"
            + _NUMBER
            + r"\s+(?:full[- ]time(?: and part[- ]time)?(?: equivalent)?\s+)?(?:employees|people|team members|associates)",
            re.I,
        ),
        0.85,
    ),
    (
        re.compile(
            r"(?:approximately|about|over|more than)\s+"
            + _NUMBER
            + r"\s+(?:full[- ]time(?: and part[- ]time)?(?: equivalent)?\s+)?(?:employees|people|team members|associates)",
            re.I,
        ),
        0.7,
    ),
    (re.compile(r"workforce of\s+(?:approximately|about|over|more than)?\s*" + _NUMBER, re.I), 0.6),
]


@dataclass
class HeadcountMatch:
    value: int
    confidence: float
    sentence: str
    offset: int


def extract_headcount(section_text: str) -> HeadcountMatch | None:
    best: HeadcountMatch | None = None
    for pattern, confidence in _PATTERNS:
        for m in pattern.finditer(section_text):
            number = float(m.group(1).replace(",", ""))
            scale = {"thousand": 1_000, "million": 1_000_000}.get((m.group(2) or "").lower(), 1)
            value = round(number * scale)
            if not 500 <= value <= 3_000_000:
                continue
            start = section_text.rfind(".", 0, m.start()) + 1
            end = section_text.find(".", m.end())
            sentence = " ".join(section_text[start : end + 1 if end > 0 else None].split())
            # "7,600 employees located in the United States" is a subset, not the total.
            # The qualifier must follow the count directly; "of which 50% were in the United
            # States" describes a total, while "located in the United States" limits it.
            subset = re.match(
                r"\s*(?:(?:located|based|employed)\s+)?in\s+the\s+(?:united\s+states|u\.s\.)|\s*,?\s*excluding",
                section_text[m.end() : m.end() + 60],
                re.I,
            )
            match = HeadcountMatch(
                value=value, confidence=confidence - 0.45 if subset else confidence, sentence=sentence, offset=m.start()
            )
            if best is None or match.confidence > best.confidence:
                best = match
            if not subset:
                break
        if best and best.confidence >= 0.6:
            return best
    # Only a subset was found (for example, US employees only): report nothing rather than a wrong total.
    return best if best and best.confidence >= 0.6 else None
