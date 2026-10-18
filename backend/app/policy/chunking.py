"""Splitting a policy wording into citable clauses.

Indian policy wordings follow the IRDAI standard layout: numbered sections
("4.2", "C. Exclusions"), coded exclusions ("Code- Excl03") and definitions
("Def. 12"). A clause runs from one heading to the next and remembers its pages,
so every answer can point at "4.2 Room Rent, page 7". Long clauses are split
with overlap and keep their heading.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.documents.reader import Page

_SECTION = re.compile(r"^(?:section|part|chapter)\s+[a-z0-9]{1,4}\b", re.IGNORECASE)
_NUMBERED = re.compile(r"^(?:[A-Z]|\d{1,2})(?:\.\d{1,2}){0,3}[.)]?\s+[A-Z(]")
_ROMAN = re.compile(r"^[ivx]{1,5}[.)]\s+[A-Z]")
_CODED = re.compile(
    r"\b(?:code\s{0,2}[-:]?\s{0,2}excl\s{0,2}\d{1,3}|def\.?\s{0,2}\d{1,3})\b", re.IGNORECASE
)
# Lines that look like headings but are amounts or table rows.
_NOT_HEADING = re.compile(r"rs\.?|inr|₹|%|\||\d{3,}", re.IGNORECASE)
_HEADING_CHARS = (4, 120)
# Shorter fragments are page furniture such as lone page numbers.
_MIN_CLAUSE_CHARS = 20


@dataclass(frozen=True, slots=True)
class Clause:
    clause_id: str
    section: str
    page_start: int
    page_end: int
    text: str

    @property
    def citation(self) -> str:
        pages = (
            f"p. {self.page_start}"
            if self.page_start == self.page_end
            else f"pp. {self.page_start}-{self.page_end}"
        )
        return f"{self.section} ({pages})"


@dataclass(slots=True)
class _Section:
    heading: str
    page_start: int
    page_end: int
    lines: list[str]

    def has_text(self) -> bool:
        return any(line.strip() for line in self.lines)


def _is_heading(line: str) -> bool:
    stripped = line.strip()
    shortest, longest = _HEADING_CHARS
    if not shortest <= len(stripped) <= longest:
        return False
    if _CODED.search(stripped):
        return True
    if not (_SECTION.match(stripped) or _NUMBERED.match(stripped) or _ROMAN.match(stripped)):
        return False
    return not _NOT_HEADING.search(stripped) and not stripped.endswith((".", ",", ";"))


def _split_long(text: str, limit: int, overlap: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + limit)
        if end < len(text):
            boundary = text.rfind(". ", start + limit // 2, end)
            end = boundary + 1 if boundary != -1 else end
        parts.append(text[start:end].strip())
        if end == len(text):
            break
        start = max(end - overlap, start + 1)
    return parts


def _sections(pages: Sequence[Page]) -> list[_Section]:
    first = pages[0].number if pages else 1
    current = _Section("Preamble", first, first, [])
    sections: list[_Section] = []
    for page in pages:
        for line in page.text.splitlines():
            if _is_heading(line):
                if current.has_text():
                    sections.append(current)
                current = _Section(" ".join(line.split()), page.number, page.number, [])
            current.lines.append(line)
            current.page_end = page.number
    if current.has_text():
        sections.append(current)
    return sections


def split_clauses(
    pages: Sequence[Page], *, prefix: str, max_chars: int, overlap_chars: int
) -> list[Clause]:
    clauses: list[Clause] = []
    for section in _sections(pages):
        text = re.sub(r"[ \t]+", " ", "\n".join(section.lines)).strip()
        for piece in _split_long(text, max_chars, overlap_chars):
            if len(piece) < _MIN_CLAUSE_CHARS:
                continue
            heading = section.heading
            body = piece if piece.startswith(heading[:20]) else f"{heading}\n{piece}"
            clauses.append(
                Clause(
                    clause_id=f"{prefix}-{len(clauses) + 1:03d}",
                    section=heading,
                    page_start=section.page_start,
                    page_end=section.page_end,
                    text=body,
                )
            )
    return clauses
