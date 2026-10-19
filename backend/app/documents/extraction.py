"""Structured records from masked document text.

Bills can run to many pages, so they are split into page groups that are read in
parallel and merged. The printed total is kept so the merged lines can be
reconciled against it.
"""

import asyncio
import re
from collections.abc import Sequence

from pydantic import BaseModel, Field

from app.documents.bill_table import parse_bill_table
from app.documents.reader import Page
from app.domain import Bill, BillLine, DischargeSummary, DocumentKind, RejectionLetter
from app.llm.prompting import document_block, system_prompt
from app.llm.router import ModelRouter
from app.llm.types import Message, Task

# Characters of page text per bill extraction call; keeps answers well inside output limits.
_BILL_CHUNK_CHARS = 9_000
_SUMMARY_CHARS = 14_000

# Checked in order: a rejection letter quotes policy terms, so it must win over "policy".
_KIND_CUES: tuple[tuple[DocumentKind, re.Pattern[str]], ...] = tuple(
    (kind, re.compile("|".join(cues), re.IGNORECASE))
    for kind, cues in (
        (
            DocumentKind.REJECTION_LETTER,
            (
                r"repudiat",
                r"not admissible",
                r"claim (?:is|has been) (?:rejected|denied)",
                r"deductions? (?:details|summary)",
                r"settlement letter",
            ),
        ),
        (DocumentKind.DISCHARGE_SUMMARY, (r"discharge summary", r"course in (?:the )?hospital")),
        (DocumentKind.ESTIMATE, (r"cost estimate", r"estimated (?:cost|expenses)", r"pre-?auth")),
        (
            DocumentKind.BILL,
            (r"final bill", r"in-?patient bill", r"bill no", r"gross amount", r"net payable"),
        ),
        (
            DocumentKind.POLICY,
            (r"policy wording", r"waiting period", r"exclusions?", r"sum insured", r"definitions"),
        ),
    )
)

_BILL_ROLE = """You read Indian hospital bills and cost estimates.
Copy every charge line with its net amount in rupees. Rules:
- One line per charge row. Keep the description as printed.
- Skip subtotal, grand total, advance or deposit paid, amount due and tax summary rows.
- A discount or concession row is a line with a negative amount.
- Set `page` from the nearest [page N] marker above the row.
- Header fields (hospital, dates, room category, printed net total) are filled only when
  printed; otherwise null. room_rent_per_day only when a per-day rate is printed, never a
  multi-day room total. Dates are ISO yyyy-mm-dd.
- quantity is the number of days or units printed for the row, when shown."""

_DISCHARGE_ROLE = """You read hospital discharge summaries. Extract the final diagnosis,
procedures done, admission and discharge dates (ISO yyyy-mm-dd), conditions noted as existing
before this admission (diabetes, hypertension and so on, with duration if stated), and whether
the admission followed an accident. Leave a field null when the summary does not say."""

_REJECTION_ROLE = """You read letters from Indian health insurers or TPAs that reject a claim,
settle it partly, or raise a query. Extract the decision, amounts, claim number and each
reason the insurer gives exactly as written, with the clause it cites and the amount cut for
that reason when stated. Dates are ISO yyyy-mm-dd."""

_CLASSIFY_ROLE = """Classify an uploaded document from an Indian health insurance claim."""


class _BillChunk(Bill):
    """Same shape as `Bill`; header fields are ignored after the first chunk."""


class _Kind(BaseModel):
    kind: DocumentKind = Field(description="Which document this is.")


def _page_text(pages: Sequence[Page]) -> str:
    return "\n\n".join(f"[page {p.number}]\n{p.text}" for p in pages)


def _chunks(pages: Sequence[Page], limit: int) -> list[list[Page]]:
    groups: list[list[Page]] = [[]]
    size = 0
    for page in pages:
        if groups[-1] and size + len(page.text) > limit:
            groups.append([])
            size = 0
        groups[-1].append(page)
        size += len(page.text)
    return [g for g in groups if g]


class Extractor:
    def __init__(self, router: ModelRouter) -> None:
        self._router = router

    async def classify(self, pages: Sequence[Page], tags: dict[str, str]) -> DocumentKind:
        head = _page_text(pages[:3])[:4_000]
        for kind, cue in _KIND_CUES:
            if cue.search(head):
                return kind
        answer = await self._router.structured(
            Task.FAST,
            [
                Message(role="system", content=system_prompt(_CLASSIFY_ROLE)),
                Message(role="user", content=document_block(head, "upload")),
            ],
            _Kind,
            tags=tags,
        )
        return answer.kind

    async def bill(self, pages: Sequence[Page], tags: dict[str, str]) -> Bill:
        # A table that adds up to its printed total needs no model.
        if (parsed := parse_bill_table(pages)) is not None:
            return parsed
        groups = _chunks(pages, _BILL_CHUNK_CHARS)
        parts = await asyncio.gather(
            *(
                self._router.structured(
                    Task.FAST,
                    [
                        Message(role="system", content=system_prompt(_BILL_ROLE)),
                        Message(role="user", content=document_block(_page_text(group), "bill")),
                    ],
                    _BillChunk,
                    tags={**tags, "pages": f"{group[0].number}-{group[-1].number}"},
                )
                for group in groups
            )
        )
        header = parts[0]
        lines: list[BillLine] = []
        for group, part in zip(groups, parts, strict=True):
            valid_pages = {p.number for p in group}
            for line in part.lines:
                page = line.page if line.page in valid_pages else group[0].number
                lines.append(line.model_copy(update={"page": page}))
        printed_total = next((p.printed_total for p in reversed(parts) if p.printed_total), None)
        return Bill(
            **header.model_dump(exclude={"lines", "printed_total"}),
            printed_total=printed_total,
            lines=lines,
        )

    async def discharge_summary(
        self, pages: Sequence[Page], tags: dict[str, str]
    ) -> DischargeSummary:
        return await self._router.structured(
            Task.FAST,
            [
                Message(role="system", content=system_prompt(_DISCHARGE_ROLE)),
                Message(
                    role="user",
                    content=document_block(_page_text(pages)[:_SUMMARY_CHARS], "discharge"),
                ),
            ],
            DischargeSummary,
            tags=tags,
        )

    async def rejection_letter(
        self, pages: Sequence[Page], tags: dict[str, str]
    ) -> RejectionLetter:
        return await self._router.structured(
            Task.REASONING,
            [
                Message(role="system", content=system_prompt(_REJECTION_ROLE)),
                Message(
                    role="user",
                    content=document_block(_page_text(pages)[:_SUMMARY_CHARS], "insurer letter"),
                ),
            ],
            RejectionLetter,
            tags=tags,
        )
