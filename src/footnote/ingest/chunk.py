"""Split a section into retrievable chunks that never cross a section boundary."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from footnote.ingest.parse import PAGE_BREAK, ParsedFiling, Section

TARGET_WORDS = 200
MAX_WORDS = 320
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")


@dataclass
class Chunk:
    item: str
    ordinal: int
    text: str
    page: str
    char_start: int
    content_hash: str


def _paragraphs(section: Section) -> list[tuple[int, str]]:
    out = []
    offset = section.start
    for line in section.text.split("\n"):
        stripped = line.replace(PAGE_BREAK, "").strip()
        if stripped:
            out.append((offset, stripped))
        offset += len(line) + 1
    return out


def _split_long(paragraph: str) -> list[str]:
    words = paragraph.split()
    if len(words) <= MAX_WORDS:
        return [paragraph]
    pieces: list[str] = []
    current: list[str] = []
    for sentence in _SENTENCE.split(paragraph):
        if current and len(" ".join(current).split()) + len(sentence.split()) > TARGET_WORDS:
            pieces.append(" ".join(current))
            current = []
        current.append(sentence)
    if current:
        pieces.append(" ".join(current))
    return pieces


def normalise(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def chunk_section(parsed: ParsedFiling, section: Section) -> list[Chunk]:
    chunks: list[Chunk] = []
    buffer: list[str] = []
    buffer_start = section.start
    seen: set[str] = set()

    def flush() -> None:
        nonlocal buffer
        if not buffer:
            return
        text = "\n".join(buffer).strip()
        buffer = []
        if len(text.split()) < 25:
            return  # headings and table fragments on their own carry no retrievable meaning
        digest = hashlib.sha1(normalise(text).encode()).hexdigest()
        if digest in seen:
            return
        seen.add(digest)
        chunks.append(
            Chunk(
                item=section.item,
                ordinal=len(chunks),
                text=text,
                page=parsed.page_at(buffer_start),
                char_start=buffer_start,
                content_hash=digest,
            )
        )

    for offset, paragraph in _paragraphs(section):
        for piece in _split_long(paragraph):
            if not buffer:
                buffer_start = offset
            buffer.append(piece)
            if sum(len(p.split()) for p in buffer) >= TARGET_WORDS:
                flush()
    flush()
    return chunks
