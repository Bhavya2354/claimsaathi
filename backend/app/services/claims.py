"""Running the agents for a case and saving what they produce.

A run loads a snapshot in one short transaction, runs the graph with no database
session held open (model calls can take a minute), then saves the results in a
second transaction. Expensive readings (policy terms, bill labels) are cached on
the case and dropped when a document they depend on changes.
"""

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.agents.advocate import LetterKind
from app.agents.graph import CaseSnapshot, ClaimGraph, GraphState, Intent
from app.agents.policy_expert import TermsReading
from app.db.models import Analysis, Case, ChatMessage, Document, Letter, new_id, utcnow
from app.db.session import transaction
from app.documents.privacy import PiiMasker
from app.domain import Bill, BillLine, DischargeSummary, DocumentKind, RejectionLetter
from app.errors import AppError, NotFoundError
from app.llm.types import Message
from app.policy.chunking import Clause
from app.rules.terms import CaseFacts, LabelledLine
from app.services.views import (
    AuditView,
    ChatReply,
    ClauseView,
    LetterView,
    RejectionSummary,
    ReviewView,
)


class MissingDocumentsError(AppError):
    """The request needs documents the case does not have yet."""


def _ready(case: Case, *kinds: DocumentKind) -> list[Document]:
    wanted = {k.value for k in kinds}
    return [d for d in case.documents if d.status == "ready" and d.kind in wanted]


def _merge_bills(documents: Sequence[Document]) -> Bill | None:
    bills = [Bill.model_validate(d.extracted) for d in documents if d.extracted]
    if not bills:
        return None
    first = bills[0]
    totals = [b.printed_total for b in bills if b.printed_total is not None]
    return Bill(
        hospital_name=first.hospital_name,
        bill_number=first.bill_number,
        admission_date=next((b.admission_date for b in bills if b.admission_date), None),
        discharge_date=next((b.discharge_date for b in bills if b.discharge_date), None),
        room_category=next((b.room_category for b in bills if b.room_category), None),
        room_rent_per_day=next((b.room_rent_per_day for b in bills if b.room_rent_per_day), None),
        printed_total=sum(totals, Decimal(0)) if len(totals) == len(bills) else None,
        lines=[line for bill in bills for line in bill.lines],
    )


def effective_facts(case: Case) -> CaseFacts:
    """The user's form wins; gaps are filled from the discharge summary and the bill."""
    given = CaseFacts.model_validate(case.facts or {})
    fallback: dict[str, Any] = {}
    summaries = _ready(case, DocumentKind.DISCHARGE_SUMMARY)
    if summaries and summaries[-1].extracted:
        summary = DischargeSummary.model_validate(summaries[-1].extracted)
        fallback |= {
            "diagnosis": summary.diagnosis,
            "procedures": summary.procedures or None,
            "admission_date": summary.admission_date,
            "discharge_date": summary.discharge_date,
            "pre_existing_conditions": summary.past_history or None,
            "is_accident": summary.is_accident,
        }
    bill = _merge_bills(_ready(case, DocumentKind.BILL) or _ready(case, DocumentKind.ESTIMATE))
    if bill is not None:
        fallback.setdefault("admission_date", bill.admission_date)
        fallback.setdefault("discharge_date", bill.discharge_date)
        fallback |= {"room_rent_per_day": bill.room_rent_per_day}
    updates = {
        key: value
        for key, value in fallback.items()
        if value is not None and key not in given.model_fields_set
    }
    return given.model_copy(update=updates)


def _parties(vault: dict[str, str]) -> str:
    def first(prefix: str) -> str | None:
        tokens = sorted(
            (t for t in vault if t.startswith(f"[{prefix}_")),
            key=lambda t: int(t.rsplit("_", 1)[1].rstrip("]")),
        )
        return tokens[0] if tokens else None

    holder = first("PERSON") or "[your name]"
    number = first("POLICY_NUMBER") or "[your policy number]"
    return f"Policyholder: {holder}. Policy number: {number}."


def _clause_views(clauses: Sequence[Clause], cited: set[str] | None = None) -> list[ClauseView]:
    seen: dict[str, ClauseView] = {}
    for clause in clauses:
        if (cited is None or clause.clause_id in cited) and clause.clause_id not in seen:
            seen[clause.clause_id] = ClauseView.of(clause)
    return list(seen.values())


def _cited_in_terms(reading: TermsReading) -> set[str]:
    terms = reading.terms
    ids = {*terms.clause_ids, *terms.proportionate_clause_ids}
    for term in (terms.room_rent, terms.icu_rent, terms.copay, terms.deductible):
        if term is not None:
            ids.update(term.clause_ids)
    for item in [*terms.waiting_periods, *terms.sub_limits]:
        ids.update(item.clause_ids)
    return ids


class ClaimService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        graph: ClaimGraph,
        masker: PiiMasker,
        history_turns: int,
    ) -> None:
        self._sessions = sessions
        self._graph = graph
        self._masker = masker
        self._history_turns = history_turns

    async def _load(self, case_id: str) -> Case:
        async with self._sessions() as session:
            case = await session.scalar(
                select(Case).where(Case.id == case_id).options(selectinload(Case.documents))
            )
        if case is None:
            raise NotFoundError("case not found")
        return case

    def snapshot(
        self,
        case: Case,
        *,
        bill: Bill | None = None,
        labelled: list[LabelledLine] | None = None,
    ) -> tuple[CaseSnapshot, str]:
        """Returns the snapshot and where its bill came from."""
        source = "form" if bill is not None else "bill"
        if bill is None:
            bill = _merge_bills(_ready(case, DocumentKind.BILL))
            if bill is None:
                bill = _merge_bills(_ready(case, DocumentKind.ESTIMATE))
                source = "estimate"
        letters = _ready(case, DocumentKind.REJECTION_LETTER)
        rejection = (
            RejectionLetter.model_validate(letters[-1].extracted)
            if letters and letters[-1].extracted
            else None
        )
        cached_labels = (
            [LabelledLine.model_validate(x) for x in case.labelled_lines]
            if case.labelled_lines is not None and source != "form"
            else None
        )
        vault = self._masker.unseal(case.pii_vault)
        return (
            CaseSnapshot(
                case_id=case.id,
                facts=effective_facts(case),
                has_policy=bool(_ready(case, DocumentKind.POLICY)),
                bill=bill,
                rejection=rejection,
                reading=(
                    TermsReading.model_validate(case.terms_reading) if case.terms_reading else None
                ),
                labelled=labelled if labelled is not None else cached_labels,
                parties=_parties(vault),
            ),
            source,
        )

    async def run(
        self,
        case_id: str,
        intent: Intent,
        *,
        letter_kind: LetterKind | None = None,
        bill: Bill | None = None,
        labelled: list[LabelledLine] | None = None,
    ) -> AuditView | ReviewView | LetterView:
        case = await self._load(case_id)
        snapshot, source = self.snapshot(case, bill=bill, labelled=labelled)
        state = await self._graph.run(snapshot, intent, letter_kind=letter_kind)
        if "message" in state:
            raise MissingDocumentsError(state["message"])
        async with transaction(self._sessions) as session:
            stored = await session.get(Case, case_id)
            if stored is None:
                raise NotFoundError("case not found")
            self._cache(stored, snapshot, state, source)
            view = self._persist(session, stored, state, source)
            vault = self._masker.unseal(stored.pii_vault)
        return type(view).model_validate(
            self._masker.unmask_tree(view.model_dump(mode="json"), vault)
        )

    async def chat(self, case_id: str, text: str) -> ChatReply:
        case = await self._load(case_id)
        snapshot, source = self.snapshot(case)
        async with self._sessions() as session:
            earlier = list(
                await session.scalars(
                    select(ChatMessage)
                    .where(ChatMessage.case_id == case_id)
                    .order_by(ChatMessage.created_at.desc())
                    .limit(self._history_turns * 2)
                )
            )
        history = [
            Message(role="assistant" if m.role == "assistant" else "user", content=m.content)
            for m in reversed(earlier)
        ]
        vault = self._masker.unseal(case.pii_vault)
        masked_question = self._masker.remask(text, vault)
        state = await self._graph.run(snapshot, None, question=masked_question, history=history)

        async with transaction(self._sessions) as session:
            stored = await session.get(Case, case_id)
            if stored is None:
                raise NotFoundError("case not found")
            self._cache(stored, snapshot, state, source)
            reply = self._reply(session, stored, state, source)
            session.add(ChatMessage(case_id=case_id, role="user", content=masked_question))
            session.add(
                ChatMessage(
                    case_id=case_id,
                    role="assistant",
                    content=reply.text,
                    clause_ids=[c.clause_id for c in reply.clauses],
                    verified=reply.verified,
                )
            )
        return ChatReply.model_validate(
            self._masker.unmask_tree(reply.model_dump(mode="json"), vault)
        )

    @staticmethod
    def _cache(case: Case, snapshot: CaseSnapshot, state: GraphState, source: str) -> None:
        reading = state.get("reading")
        report = state.get("report")
        terms_ok = report is None or report.passed or state.get("retry") != "terms"
        if reading is not None and snapshot.reading is None and terms_ok:
            case.terms_reading = reading.model_dump(mode="json")
        audit = state.get("audit")
        if audit is not None and snapshot.labelled is None and source != "form":
            case.labelled_lines = [line.model_dump(mode="json") for line in audit.lines]

    def _persist(
        self, session: AsyncSession, case: Case, state: GraphState, source: str
    ) -> AuditView | ReviewView | LetterView:
        intent = state["intent"]
        if intent == Intent.AUDIT:
            return self._save_audit(session, case, state, source)
        if intent == Intent.REVIEW:
            return self._save_review(session, case, state)
        return self._save_letter(session, case, state)

    @staticmethod
    def _verification(state: GraphState) -> dict[str, Any]:
        report = state.get("report")
        return {
            "verified": bool(report and report.passed),
            "issues": report.issues if report and not report.passed else [],
            "steps": state.get("steps", []),
        }

    def _save_audit(
        self, session: AsyncSession, case: Case, state: GraphState, source: str
    ) -> AuditView:
        reading = state["reading"]
        audit = state["audit"]
        relation = state.get("relation")
        cited = _cited_in_terms(reading)
        result = {
            "source": source,
            "coverage": audit.coverage.model_dump(mode="json"),
            "terms": reading.terms.model_dump(mode="json"),
            "facts": state["facts"].model_dump(mode="json"),
            "clauses": [c.model_dump() for c in _clause_views(reading.clauses, cited)],
            "warnings": [*audit.coverage.warnings, *audit.warnings],
            "reasoning": relation.reasoning if relation else None,
        }
        verification = self._verification(state)
        row = Analysis(
            id=new_id(), case_id=case.id, intent=Intent.AUDIT.value, result=result, **verification
        )
        session.add(row)
        return AuditView.model_validate({"analysis_id": row.id, **result, **verification})

    def _save_review(self, session: AsyncSession, case: Case, state: GraphState) -> ReviewView:
        snapshot = state["snapshot"]
        rejection = snapshot.rejection
        if rejection is None:
            raise MissingDocumentsError("Please upload the insurer's letter first.")
        review = state["review"]
        audit = state.get("audit")
        cited = {cid for r in review.reasons for cid in r.clause_ids}
        clauses = [*state.get("review_clauses", []), *state["reading"].clauses]
        result = {
            "review": review.model_dump(mode="json"),
            "rejection": RejectionSummary(
                insurer_name=rejection.insurer_name,
                claim_number=rejection.claim_number,
                decision=rejection.decision,
                amount_claimed=float(rejection.amount_claimed)
                if rejection.amount_claimed is not None
                else None,
                amount_approved=float(rejection.amount_approved)
                if rejection.amount_approved is not None
                else None,
            ).model_dump(mode="json"),
            "coverage": audit.coverage.model_dump(mode="json") if audit else None,
            "clauses": [c.model_dump() for c in _clause_views(clauses, cited)],
        }
        verification = self._verification(state)
        row = Analysis(
            id=new_id(), case_id=case.id, intent=Intent.REVIEW.value, result=result, **verification
        )
        session.add(row)
        return ReviewView.model_validate({"analysis_id": row.id, **result, **verification})

    def _save_letter(self, session: AsyncSession, case: Case, state: GraphState) -> LetterView:
        letter = state["letter"]
        clauses = [*state.get("review_clauses", []), *state["reading"].clauses]
        verification = self._verification(state)
        row = Letter(
            id=new_id(),
            case_id=case.id,
            kind=state.get("letter_kind", LetterKind.GRIEVANCE).value,
            subject=letter.subject[:300],
            body=letter.body,
            clause_ids=letter.clause_ids,
            verified=verification["verified"],
            issues=verification["issues"],
            created_at=utcnow(),
        )
        session.add(row)
        return letter_view(row, _clause_views(clauses, set(letter.clause_ids)), state)

    def _reply(
        self, session: AsyncSession, case: Case, state: GraphState, source: str
    ) -> ChatReply:
        intent = state["intent"]
        verification = self._verification(state)
        if "message" in state:
            return ChatReply(intent=intent, text=state["message"], verified=True, steps=[])
        if intent == Intent.ASK:
            answer = state["answer"]
            clauses = _clause_views(state["answer_clauses"], set(answer.clause_ids))
            return ChatReply(intent=intent, text=answer.answer, clauses=clauses, **verification)
        if intent == Intent.CHECKLIST:
            checklist = state["checklist"]
            return ChatReply(
                intent=intent,
                text="Here are the documents your claim needs.",
                checklist=checklist,
                verified=True,
                steps=state.get("steps", []),
            )
        view = self._persist(session, case, state, source)
        if isinstance(view, AuditView):
            coverage = view.coverage
            text = (
                f"I checked {len(coverage.lines)} bill lines. The policy should pay "
                f"Rs {coverage.payable_total:,.2f} of Rs {coverage.billed_total:,.2f}; "
                f"you would pay Rs {coverage.patient_pays:,.2f}."
            )
            return ChatReply(intent=intent, text=text, audit=view, **verification)
        if isinstance(view, ReviewView):
            return ChatReply(intent=intent, text=view.review.summary, review=view, **verification)
        return ChatReply(
            intent=intent,
            text="I drafted the letter. Review and edit it, then approve it.",
            letter=view,
            **verification,
        )


def letter_view(
    row: Letter, clauses: list[ClauseView], state: GraphState | None = None
) -> LetterView:
    return LetterView(
        id=row.id,
        kind=row.kind,
        subject=row.subject,
        body=row.body,
        status="approved" if row.status == "approved" else "draft",
        clauses=clauses,
        created_at=row.created_at,
        approved_at=row.approved_at,
        verified=row.verified,
        issues=list(row.issues or []),
        steps=state.get("steps", []) if state else [],
    )


def bill_from_form(lines: Sequence[tuple[str, Decimal, Decimal | None]]) -> Bill:
    return Bill(
        lines=[
            BillLine(description=description, amount=amount, quantity=quantity)
            for description, amount, quantity in lines
        ]
    )
