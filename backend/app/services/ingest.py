"""Document Reader: the upload pipeline.

    file bytes ─► read (text layer / vision OCR / local OCR) ─► mask personal data
               ─► classify ─► extract a typed record, or split and index a policy

Runs as a background task; the document row moves from `processing` to `ready`
or `failed`, and the client polls the case. Uploads to one case are processed
one at a time because they share that case's PII vault.
"""

import asyncio
import logging
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import CaseSettings, RetrievalSettings
from app.db.models import Case, Document
from app.db.session import transaction
from app.documents.extraction import Extractor
from app.documents.privacy import PiiMasker
from app.documents.reader import DocumentReader, Page
from app.domain import DocumentKind
from app.errors import AppError, DocumentError
from app.policy.chunking import split_clauses
from app.policy.index import PolicyIndex

log = logging.getLogger(__name__)

_SAFE_NAME = 200


def safe_filename(name: str | None) -> str:
    base = (name or "upload").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(ch for ch in base if ch.isalnum() or ch in "._- ()").strip()
    return (cleaned or "upload")[:_SAFE_NAME]


class IngestService:
    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        reader: DocumentReader,
        masker: PiiMasker,
        extractor: Extractor,
        index: PolicyIndex,
        retrieval: RetrievalSettings,
        limits: CaseSettings,
    ) -> None:
        self._sessions = sessions
        self._reader = reader
        self._masker = masker
        self._extractor = extractor
        self._index = index
        self._retrieval = retrieval
        self._limits = limits
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def accept(
        self, session: AsyncSession, case: Case, data: bytes, filename: str | None
    ) -> Document:
        """Validate synchronously so the client gets a clear 4xx before any processing."""
        if len(case.documents) >= self._limits.max_documents_per_case:
            raise DocumentError(
                f"a case holds at most {self._limits.max_documents_per_case} documents"
            )
        mime = self._reader.validate(data)
        document = Document(case_id=case.id, filename=safe_filename(filename), mime_type=mime)
        session.add(document)
        await session.flush()
        return document

    async def process(self, document_id: str, data: bytes, kind: DocumentKind | None) -> None:
        async with self._sessions() as session:
            document = await session.get(Document, document_id)
            if document is None:
                return
            case_id = document.case_id
        async with self._locks[case_id]:
            try:
                await self._process(case_id, document_id, data, kind)
            except AppError as exc:
                await self._fail(document_id, str(exc))
            except Exception:
                log.exception("document processing failed", extra={"document": document_id})
                await self._fail(document_id, "The document could not be processed.")

    async def _process(
        self, case_id: str, document_id: str, data: bytes, kind: DocumentKind | None
    ) -> None:
        tags = {"case": case_id, "agent": "reader"}
        read = await self._reader.read(data, tags=tags)

        async with self._sessions() as session:
            case = await session.get(Case, case_id)
            if case is None:
                return
            vault = self._masker.unseal(case.pii_vault)
        masked = [
            Page(
                page.number,
                await asyncio.to_thread(self._masker.mask, page.text, vault),
                page.source,
            )
            for page in read.pages
        ]
        kind = kind or await self._extractor.classify(masked, tags)
        extracted, clause_count = await self._extract(case_id, document_id, kind, masked, tags)

        async with transaction(self._sessions) as session:
            case = await session.get(Case, case_id)
            document = await session.get(Document, document_id)
            if case is None or document is None:
                return
            case.pii_vault = self._masker.seal(vault)
            if kind in {DocumentKind.POLICY, DocumentKind.DISCHARGE_SUMMARY}:
                case.terms_reading = None
            if kind in {DocumentKind.BILL, DocumentKind.ESTIMATE}:
                case.labelled_lines = None
            document.kind = kind.value
            document.status = "ready"
            document.sha256 = read.sha256
            document.page_count = len(masked)
            document.text_sources = sorted({p.source for p in masked})
            document.masked_text = "\n\n".join(f"[page {p.number}]\n{p.text}" for p in masked)
            document.extracted = extracted
            document.clause_count = clause_count
        log.info("document ready", extra={"document": document_id, "kind": kind.value})

    async def _extract(
        self,
        case_id: str,
        document_id: str,
        kind: DocumentKind,
        pages: list[Page],
        tags: dict[str, str],
    ) -> tuple[dict[str, Any] | None, int | None]:
        if kind in {DocumentKind.BILL, DocumentKind.ESTIMATE}:
            bill = await self._extractor.bill(pages, tags)
            if not bill.lines:
                raise DocumentError("no charge lines were found on this bill")
            return bill.model_dump(mode="json"), None
        if kind == DocumentKind.DISCHARGE_SUMMARY:
            summary = await self._extractor.discharge_summary(pages, tags)
            return summary.model_dump(mode="json"), None
        if kind == DocumentKind.REJECTION_LETTER:
            letter = await self._extractor.rejection_letter(pages, tags)
            return letter.model_dump(mode="json"), None
        if kind == DocumentKind.POLICY:
            clauses = split_clauses(
                pages,
                prefix=document_id[:6],
                max_chars=self._retrieval.chunk_max_chars,
                overlap_chars=self._retrieval.chunk_overlap_chars,
            )
            if not clauses:
                raise DocumentError("no policy clauses were found in this document")
            await self._index.add(case_id, document_id, clauses)
            return None, len(clauses)
        return None, None

    async def _fail(self, document_id: str, message: str) -> None:
        async with transaction(self._sessions) as session:
            document = await session.get(Document, document_id)
            if document is not None:
                document.status = "failed"
                document.error = message[:500]

    async def remove(self, session: AsyncSession, case: Case, document_id: str) -> None:
        document = await session.scalar(
            select(Document).where(Document.id == document_id, Document.case_id == case.id)
        )
        if document is None:
            return
        if document.kind == DocumentKind.POLICY.value:
            case.terms_reading = None
            await self._index.delete_document(case.id, document_id)
        if document.kind in {DocumentKind.BILL.value, DocumentKind.ESTIMATE.value}:
            case.labelled_lines = None
        await session.delete(document)
