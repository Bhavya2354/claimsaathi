from typing import Literal

from fastapi import APIRouter, Query, status

from app.api.dependencies import CaseDep, ContainerDep, SessionDep
from app.api.schemas import FactsUpdate, FeedbackRequest
from app.db.models import Case, Feedback
from app.errors import NotFoundError
from app.rules.checklist import Checklist, build_checklist
from app.services.claims import effective_facts
from app.services.reads import CaseReads
from app.services.views import CaseView, ClauseView, CreatedCase

router = APIRouter(prefix="/api/cases", tags=["cases"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_case(container: ContainerDep) -> CreatedCase:
    case, token = await container.cases.create()
    return CreatedCase(id=case.id, token=token, expires_at=case.expires_at)


@router.get("/{case_id}")
async def get_case(case: CaseDep, container: ContainerDep, session: SessionDep) -> CaseView:
    return await CaseReads(container.masker).case(session, case)


@router.patch("/{case_id}/facts")
async def update_facts(
    body: FactsUpdate, case: CaseDep, container: ContainerDep, session: SessionDep
) -> CaseView:
    stored = await session.get(Case, case.id)
    if stored is None:
        raise NotFoundError("case not found")
    stored.facts = {**(stored.facts or {}), **body.model_dump(mode="json", exclude_unset=True)}
    # Co-payment and waiting-period readings depend on these details.
    stored.terms_reading = None
    await session.flush()
    await session.refresh(stored, ["documents"])
    return await CaseReads(container.masker).case(session, stored)


@router.delete("/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_case(case: CaseDep, container: ContainerDep) -> None:
    await container.cases.delete(case.id)


@router.get("/{case_id}/checklist")
async def checklist(
    case: CaseDep,
    claim_type: Literal["cashless", "reimbursement"] | None = Query(default=None),
) -> Checklist:
    facts = effective_facts(case)
    return build_checklist(
        claim_type=claim_type or facts.claim_type or "reimbursement",
        accident=facts.is_accident,
        surgery=bool(facts.procedures),
    )


@router.get("/{case_id}/clauses/{clause_id}")
async def clause(clause_id: str, case: CaseDep, container: ContainerDep) -> ClauseView:
    found = await container.index.clauses(case.id, {clause_id})
    if not found:
        raise NotFoundError("clause not found")
    return ClauseView.of(found[0])


@router.post("/{case_id}/feedback", status_code=status.HTTP_204_NO_CONTENT)
async def feedback(body: FeedbackRequest, case: CaseDep, session: SessionDep) -> None:
    session.add(Feedback(case_id=case.id, **body.model_dump()))
