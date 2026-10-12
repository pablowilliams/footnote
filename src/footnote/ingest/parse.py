"""Convert a 10-K's HTML into clean text, page markers and the four sections we keep.

EDGAR filings have no page objects, but almost all of them mark page breaks with
CSS (`page-break-before` or `page-break-after`). Those markers are turned into
form feeds, and the printed page number is read from the short numeric line that
usually sits just before each break. Citations can then say "page 23" and mean
the page a reader would find in the PDF version of the same report.
"""

from __future__ import annotations

import re
import warnings
from collections import Counter
from dataclasses import dataclass

from bs4 import BeautifulSoup, NavigableString, Tag, XMLParsedAsHTMLWarning

from footnote.catalog import SECTIONS

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

PAGE_BREAK = "\x0c"
_BLOCK_TAGS = ["p", "div", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "br"]
_PAGE_STYLE = re.compile(r"page-break-(before|after)\s*:\s*always|break-(before|after)\s*:\s*page", re.I)
_ITEM_LINE = re.compile(
    r"^\s*item\s*(1a|1b|1c|1|2|3|4|5|6|7a|7|8|9a|9b|9c|9|10|11|12|13|14|15|16)\b\s*[\.\:\-–—]?\s*(.{0,220})$",
    re.I,
)
_ORDER = ["1", "1A", "1B", "1C", "2", "3", "4", "5", "6", "7", "7A", "8", "9", "9A", "9B", "9C", "10"]
_PAGE_NUMBER = re.compile(r"^(?:page\s*)?(\d{1,3})$|^.{0,80}\|\s*(\d{1,3})$", re.I)


@dataclass
class Section:
    item: str
    title: str
    text: str  # still contains form feeds
    start: int  # offset in the full document text


@dataclass
class ParsedFiling:
    text: str
    sections: list[Section]
    page_labels: list[str]  # printed page label for each physical page, index = page number - 1

    def page_at(self, offset: int) -> str:
        page_index = self.text.count(PAGE_BREAK, 0, offset)
        if page_index < len(self.page_labels):
            return self.page_labels[page_index]
        return str(page_index + 1)


def html_to_text(html: bytes | str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for hidden in soup.find_all(["script", "style", "ix:header"]):
        hidden.decompose()
    for el in soup.find_all(style=re.compile(r"display\s*:\s*none", re.I)):
        el.decompose()
    for el in soup.find_all(style=_PAGE_STYLE):
        if not isinstance(el, Tag):
            continue
        style = el.get("style", "")
        if re.search(r"before", str(style), re.I):
            el.insert_before(NavigableString(PAGE_BREAK))
        else:
            el.insert_after(NavigableString(PAGE_BREAK))
    for td in soup.find_all(["td", "th"]):
        td.append(NavigableString(" | "))
    for el in soup.find_all(_BLOCK_TAGS):
        el.append(NavigableString("\n"))
    raw = soup.get_text()
    raw = raw.replace("\xa0", " ").replace("​", "")
    lines = []
    for line in raw.split("\n"):
        parts = line.split(PAGE_BREAK)
        for i, part in enumerate(parts):
            cleaned = re.sub(r"[ \t]+", " ", part).strip()
            cleaned = re.sub(r"(\s*\|\s*)+$", "", cleaned)
            cleaned = re.sub(r"^(\|\s*)+", "", cleaned)
            if cleaned:
                lines.append(cleaned)
            if i < len(parts) - 1:
                lines.append(PAGE_BREAK)
    text = "\n".join(lines)
    text = re.sub(r"(\x0c\n?){2,}", PAGE_BREAK + "\n", text)
    return text


def _page_labels(text: str) -> list[str]:
    """Printed page numbers, aligned to physical pages.

    Footers are found on most pages, but a table's last cell can look like a page
    number too. The printed number is therefore trusted only through the offset
    between printed and physical page that most pages agree on.
    """
    pages = text.split(PAGE_BREAK)
    offsets: Counter[int] = Counter()
    for index, page in enumerate(pages):
        for ln in reversed([ln.strip() for ln in page.strip().split("\n")[-3:]]):
            m = _PAGE_NUMBER.match(ln)
            if m:
                offsets[int(m.group(1) or m.group(2)) - index] += 1
                break
    if offsets:
        offset, votes = offsets.most_common(1)[0]
        if votes >= max(3, len(pages) // 3):
            return [str(i + offset) if i + offset >= 1 else "cover" for i in range(len(pages))]
    return [str(i + 1) for i in range(len(pages))]


def find_sections(text: str) -> list[Section]:
    candidates: list[tuple[int, str, str]] = []
    offset = 0
    for line in text.split("\n"):
        m = _ITEM_LINE.match(line)
        if m and len(line) < 260:
            item = m.group(1).upper()
            candidates.append((offset, item, m.group(2).strip(" .|")))
        offset += len(line) + 1
    sections: list[Section] = []
    for item, default_title in SECTIONS.items():
        best: tuple[int, int, str] | None = None
        next_items = _ORDER[_ORDER.index(item) + 1 :]
        for pos, cand_item, title in candidates:
            if cand_item != item:
                continue
            # A section ends at the next heading for a later item, or for an earlier one:
            # an "Item 7" on the contents page is followed by the body's "Item 1".
            earlier = _ORDER[: _ORDER.index(item)]
            end = next(
                (p for p, it, _ in candidates if p > pos and (it in next_items or it in earlier)),
                len(text),
            )
            span = end - pos
            if best is None or span > best[1]:
                best = (pos, span, title)
        if best and best[1] > 400:
            pos, span, title = best
            body = text[pos : pos + span]
            sections.append(Section(item=item, title=default_title, text=body, start=pos))
    return sections


# Titles used when a filing does not number its sections as "Item N" (Intel's 10-K
# is organised around its own headings and lists the item numbers only in an index).
_TITLE_LINES = {
    "1": ("fundamentals of our business", "business"),
    "1A": ("risk factors",),
    "7": ("management's discussion and analysis", "management’s discussion and analysis"),
}
_MAJOR_TITLES = (
    "risk factors",
    "management's discussion and analysis",
    "management’s discussion and analysis",
    "financial statements and supplementary details",
    "financial statements and supplementary data",
    "financial statements",
    "other key information",
    "fundamentals of our business",
    "quantitative and qualitative disclosures about market risk",
)


def _find_by_title(text: str, item: str) -> Section | None:
    lines: list[tuple[int, str]] = []
    offset = 0
    for line in text.split("\n"):
        lines.append((offset, line.strip().lower().rstrip(":").replace(PAGE_BREAK, "")))
        offset += len(line) + 1
    titles = _TITLE_LINES.get(item, ())
    best: tuple[int, int] | None = None
    for i, (pos, low) in enumerate(lines):
        if low not in titles:
            continue
        end = next((p for p, other in lines[i + 1 :] if other in _MAJOR_TITLES and other not in titles), len(text))
        if best is None or end - pos > best[1]:
            best = (pos, end - pos)
    if not best or best[1] < 2000:
        return None
    return Section(item=item, title=SECTIONS[item], text=text[best[0] : best[0] + best[1]], start=best[0])


def parse_filing(html: bytes | str) -> ParsedFiling:
    text = html_to_text(html)
    sections = find_sections(text)
    for item in _TITLE_LINES:
        current = next((s for s in sections if s.item == item), None)
        if current is None or len(current.text.split()) < 1000:
            fallback = _find_by_title(text, item)
            if fallback and (current is None or len(fallback.text) > len(current.text)):
                sections = [s for s in sections if s.item != item] + [fallback]
    return ParsedFiling(text=text, sections=sections, page_labels=_page_labels(text))
