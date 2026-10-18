from fastapi import APIRouter, status

from app.api.dependencies import CaseDep, ContainerDep, SessionDep
from app.api.schemas import ChatRequest, EstimateRequest, LetterEdit, LetterRequest
from app.domain import Bill, BillLine
from app.rules.terms import LabelledLine
from app.services.views import AuditView, ChatReply, LetterView, ReviewView

router = APIRouter(prefix="/api/cases/{case_id}", tags=["claims"])


@router.post("/audit")
async def audit(case: CaseDep, container: ContainerDep) -> AuditView:
    """Check the uploaded bill (or cost estimate) against the policy."""
    return await container.claims.audit(case.id)


@router.post("/estimate")
async def estimate(body: EstimateRequest, case: CaseDep, container: ContainerDep) -> AuditView:
    """What a planned admission would cost you, from expected charges you enter."""
    if not body.lines:
        return await audit(case, container)
    labelled = [
        LabelledLine(
            line=BillLine(description=line.description, amount=line.amount, quantity=line.quantity),
            category=line.category,
            reason="Entered by you",
        )
        for line in body.lines
    ]
    bill = Bill(lines=[entry.line for entry in labelled])
    return await container.claims.audit(case.id, bill=bill, labelled=labelled)


@router.post("/review")
async def review(case: CaseDep, container: ContainerDep) -> ReviewView:
    """Check whether the insurer's rejection or deductions follow the policy."""
    return await container.claims.review(case.id)


@router.get("/analyses/{analysis_id}")
async def analysis(
    analysis_id: str, case: CaseDep, container: ContainerDep, session: SessionDep
) -> AuditView | ReviewView:
    return await container.reads.analysis(session, case, analysis_id)


@router.post("/letters", status_code=status.HTTP_201_CREATED)
async def draft_letter(body: LetterRequest, case: CaseDep, container: ContainerDep) -> LetterView:
    return await container.claims.letter(case.id, body.kind)


@router.get("/letters/{letter_id}")
async def get_letter(
    letter_id: str, case: CaseDep, container: ContainerDep, session: SessionDep
) -> LetterView:
    return await container.reads.letter(session, case, letter_id)


@router.put("/letters/{letter_id}")
async def edit_letter(
    letter_id: str, body: LetterEdit, case: CaseDep, container: ContainerDep, session: SessionDep
) -> LetterView:
    return await container.reads.edit_letter(session, case, letter_id, body.subject, body.body)


@router.post("/letters/{letter_id}/approve")
async def approve_letter(
    letter_id: str, case: CaseDep, container: ContainerDep, session: SessionDep
) -> LetterView:
    """The user's sign-off; the letter is final after this."""
    return await container.reads.approve_letter(session, case, letter_id)


@router.post("/chat")
async def chat(body: ChatRequest, case: CaseDep, container: ContainerDep) -> ChatReply:
    return await container.claims.chat(case.id, body.message)
