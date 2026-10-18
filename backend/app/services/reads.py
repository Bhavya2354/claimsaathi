"""Queries behind the case page: the case, stored analyses and letters, unmasked for the owner."""

from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Analysis, Case, ChatMessage, Document, Letter, utcnow
from app.documents.privacy import PiiMasker
from app.domain import DocumentKind
from app.errors import NotFoundError
from app.services.claims import effective_facts, letter_view
from app.services.views import (
    AuditView,
    CaseView,
    ClauseView,
    DocumentView,
    LetterView,
    MessageView,
    ReviewView,
)


def _summary(document: Document) -> dict[str, Any] | None:
    data = document.extracted
    if document.kind == DocumentKind.POLICY.value:
        return {"clauses": document.clause_count}
    if not data:
        return None
    if document.kind in {DocumentKind.BILL.value, DocumentKind.ESTIMATE.value}:
        lines = data.get("lines", [])
        return {
            "hospital": data.get("hospital_name"),
            "lines": len(lines),
            "lines_total": round(sum(float(line["amount"]) for line in lines), 2),
            "printed_total": data.get("printed_total"),
        }
    if document.kind == DocumentKind.DISCHARGE_SUMMARY.value:
        return {"diagnosis": data.get("diagnosis"), "procedures": data.get("procedures")}
    if document.kind == DocumentKind.REJECTION_LETTER.value:
        return {
            "decision": data.get("decision"),
            "amount_claimed": data.get("amount_claimed"),
            "amount_approved": data.get("amount_approved"),
            "reasons": len(data.get("reasons", [])),
        }
    return None


def document_view(document: Document) -> DocumentView:
    status: Literal["processing", "ready", "failed"] = (
        "ready"
        if document.status == "ready"
        else "processing"
        if document.status == "processing"
        else "failed"
    )
    return DocumentView(
        id=document.id,
        filename=document.filename,
        kind=document.kind,
        status=status,
        error=document.error,
        page_count=document.page_count,
        text_sources=document.text_sources,
        clause_count=document.clause_count,
        summary=_summary(document),
        created_at=document.created_at,
    )


class CaseReads:
    def __init__(self, masker: PiiMasker) -> None:
        self._masker = masker

    def _unmask[V: BaseModel](self, case: Case, view: V) -> V:
        vault = self._masker.unseal(case.pii_vault)
        return type(view).model_validate(
            self._masker.unmask_tree(view.model_dump(mode="json"), vault)
        )

    async def case(self, session: AsyncSession, case: Case) -> CaseView:
        letters = list(
            await session.scalars(
                select(Letter).where(Letter.case_id == case.id).order_by(Letter.created_at)
            )
        )
        messages = list(
            await session.scalars(
                select(ChatMessage)
                .where(ChatMessage.case_id == case.id)
                .order_by(ChatMessage.created_at)
            )
        )
        latest: dict[str, str] = {}
        for intent, analysis_id in await session.execute(
            select(Analysis.intent, Analysis.id)
            .where(Analysis.case_id == case.id)
            .order_by(Analysis.created_at)
        ):
            latest[intent] = analysis_id
        view = CaseView(
            id=case.id,
            expires_at=case.expires_at,
            facts=case.facts or {},
            effective_facts=effective_facts(case),
            documents=[document_view(d) for d in case.documents],
            letters=[letter_view(letter, []) for letter in letters],
            messages=[
                MessageView(
                    id=m.id,
                    role="assistant" if m.role == "assistant" else "user",
                    content=m.content,
                    verified=m.verified,
                    created_at=m.created_at,
                )
                for m in messages
            ],
            latest_audit_id=latest.get("audit"),
            latest_review_id=latest.get("review"),
        )
        return self._unmask(case, view)

    async def analysis(
        self, session: AsyncSession, case: Case, analysis_id: str
    ) -> AuditView | ReviewView:
        row = await session.scalar(
            select(Analysis).where(Analysis.id == analysis_id, Analysis.case_id == case.id)
        )
        if row is None:
            raise NotFoundError("analysis not found")
        payload = {
            "analysis_id": row.id,
            **row.result,
            "verified": row.verified,
            "issues": row.issues,
            "steps": row.steps,
        }
        view: AuditView | ReviewView = (
            AuditView.model_validate(payload)
            if row.intent == "audit"
            else ReviewView.model_validate(payload)
        )
        return self._unmask(case, view)

    async def _letter(self, session: AsyncSession, case: Case, letter_id: str) -> Letter:
        row = await session.scalar(
            select(Letter).where(Letter.id == letter_id, Letter.case_id == case.id)
        )
        if row is None:
            raise NotFoundError("letter not found")
        return row

    async def letter(
        self, session: AsyncSession, case: Case, letter_id: str, clauses: list[ClauseView]
    ) -> LetterView:
        row = await self._letter(session, case, letter_id)
        return self._unmask(case, letter_view(row, clauses))

    async def edit_letter(
        self, session: AsyncSession, case: Case, letter_id: str, subject: str, body: str
    ) -> LetterView:
        row = await self._letter(session, case, letter_id)
        if row.status == "approved":
            raise NotFoundError("an approved letter cannot be edited; draft a new one")
        vault = self._masker.unseal(case.pii_vault)
        row.subject = self._masker.remask(subject, vault)[:300]
        row.body = self._masker.remask(body, vault)
        return self._unmask(case, letter_view(row, []))

    async def approve_letter(self, session: AsyncSession, case: Case, letter_id: str) -> LetterView:
        row = await self._letter(session, case, letter_id)
        row.status = "approved"
        row.approved_at = row.approved_at or utcnow()
        return self._unmask(case, letter_view(row, []))
